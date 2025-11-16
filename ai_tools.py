# ai_tools.py (Версия 4.0 - Старый промпт удален)

import json
import logging
import re
from datetime import date
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# =================================================================
# --- НОВЫЕ АСИНХРОННЫЕ ФУНКЦИИ-ИНСТРУМЕНТЫ ДЛЯ ИИ ---
# Все функции принимают api_request_func (асинхронный клиент) и данные сессии
# =================================================================

async def get_user_orders_json(api_request_func, client_id: int, company_id: int, status_filter: Optional[List[str]] = None) -> str:
    """
    Получает список заказов клиента, опционально фильтруя по статусу.
    (ВЕРСИЯ 3.0 - Поддержка status_filter)
    :param api_request_func: Асинхронная функция для выполнения API запросов.
    :param client_id: ID текущего клиента.
    :param company_id: ID текущей компании.
    :param status_filter: (НОВОЕ) Список статусов для фильтрации.
    :return: JSON-строка со списком заказов и их историей.
    """
    
    # --- (ИСПРАВЛЕНО) ---
    # Если фильтр не передан, используем стандартный набор "активных"
    if not status_filter:
        statuses_to_fetch = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
    else:
        # Если фильтр передан (например, ["Готов к выдаче"]), используем его
        statuses_to_fetch = status_filter
    # --- (КОНЕЦ ИСПРАВЛЕНИЯ) ---
    
    orders = await api_request_func(
        "GET",
        "/api/orders",
        params={
            "client_id": client_id,
            "company_id": company_id,
            "statuses": statuses_to_fetch, # <-- Используем наш динамический список
            "limit": 50 # (ИСПРАВЛЕНО) Синхронизировано с 'my_orders'
        }
    )

    if not orders or "error" in orders:
        # (ИСПРАВЛЕНО) Уточняем сообщение об ошибке
        if not orders:
            return json.dumps({"active_orders": [], "message": "По этому фильтру заказов не найдено."}, ensure_ascii=False)
        return json.dumps({"error": "Не удалось загрузить заказы или их нет."}, ensure_ascii=False)

    formatted_orders = []
    for o in orders:
        # Форматируем историю
        history_entries = []
        if o.get('history_entries'):
            for entry in o['history_entries']:
                history_entries.append({
                    "status": entry.get('status'),
                    "date": entry.get('created_at') # (ИСПРАВЛЕНО) Просто передаем строку
                })
        
        formatted_orders.append({
            "трек": o.get('track_code'),
            "статус": o.get('status'),
            "комментарий": o.get('comment'),
            "расчет_вес_кг": o.get('calculated_weight_kg'),
            "расчет_сумма_сом": o.get('calculated_final_cost_som'),
            "history_entries": history_entries 
        })
    
    return json.dumps({"active_orders": formatted_orders}, ensure_ascii=False)

async def notify_buyout_request(api_request_func, client_id: int, company_id: int, amount_yuan: float = 0, amount_som: float = 0) -> str:
    """
    Вызывает Владельца, когда клиент согласен на выкуп и хочет оплатить.
    """
    try:
        response = await api_request_func(
            "POST",
            "/api/bot/notify_buyout",
            json={
                "client_id": client_id,
                "company_id": company_id,
                "amount_yuan": amount_yuan,
                "amount_som": amount_som,
                "comment": "Запрос из чата с ИИ"
            }
        )
        return json.dumps({"status": "success", "message": "✅ Заявка принята! Я передал информацию Владельцу, он скоро напишет вам реквизиты."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

async def add_client_order_request(api_request_func, client_id: int, company_id: int, request_text: str) -> str:
    """
    Инструмент для добавления заказов.
    """
    try:
        if not client_id or not company_id:
            return json.dumps({"status": "error", "message": "Ошибка: ID клиента или компании не определен."}, ensure_ascii=False)
            
        # Выполняем запрос к нашему "Единому Двигателю"
        response = await api_request_func(
            "POST",
            "/api/bot/order_request", 
            json={
                "client_id": client_id,
                "company_id": company_id,
                "request_text": request_text
            }
        )
        
        # Если сервер вернул ошибку в JSON (например, 400 или 500, обработанные в api_request_func)
        if "error" in response:
            error_msg = response.get("error", "Неизвестная ошибка сервера")
            logger.error(f"[AI Tool] API Error: {error_msg}")
            # Возвращаем ошибку как есть, чтобы ИИ её прочитал
            return json.dumps({"status": "error", "message": f"Сервер вернул ошибку: {error_msg}"}, ensure_ascii=False)
        
        # Если успех
        created = response.get("created", 0)
        assigned = response.get("assigned", 0)
        skipped = response.get("skipped", 0)
        
        result_msg = "Результат операции:\n"
        if created > 0: result_msg += f"✅ Успешно создано новых: {created}.\n"
        if assigned > 0: result_msg += f"🎉 Найдено на складе и присвоено (Магия): {assigned}.\n"
        if skipped > 0: result_msg += f"⚠️ Пропущено (уже были в базе): {skipped}.\n"
        
        if created == 0 and assigned == 0 and skipped == 0:
             result_msg += "❓ Сервер не нашел трек-кодов или ничего не сделал."

        return json.dumps({"status": "success", "message": result_msg, "data": response}, ensure_ascii=False)
    
    except Exception as e:
        logger.error(f"!!! [AI Tool Exception] add_client_order_request: {e}", exc_info=True)
        return json.dumps({"status": "error", "message": f"Критический сбой инструмента: {str(e)}"}, ensure_ascii=False)


async def get_company_locations(api_request_func, company_id: int) -> str:
    """
    Используется ТОЛЬКО для получения актуальной информации о филиалах компании: адресах, телефонах и графике работы.
    :param api_request_func: Асинхронная функция для выполнения API запросов.
    :param company_id: ID текущей компании.
    :return: JSON-строка со списком филиалов.
    """
    try:
        # Используем эндпоинт, который ты добавишь
        response = await api_request_func("GET", f"/api/bot/locations?company_id={company_id}") 
        
        if not response or "error" in response:
             return json.dumps({"status": "error", "message": "Не удалось загрузить данные о филиалах."}, ensure_ascii=False)
        
        # Форматирование для лучшей читаемости моделью
        locations_info = []
        for loc in response:
            locations_info.append({
                "Филиал": loc.get('name'),
                "Адрес": loc.get('address', 'Не указан'),
                "Телефон": loc.get('phone', 'Не указан'),
                "График_работы": loc.get('schedule', 'Не указан')
            })
        
        return json.dumps(locations_info, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Ошибка связи с сервером: {e}"}, ensure_ascii=False)


async def alert_order_submission(track_codes: List[str]) -> str:
    """
    Используется, если видишь 2+ трек-кода в одном сообщении, но клиент не нажал кнопку 'Добавить заказ'.
    :param track_codes: Список найденных трек-кодов.
    :return: Проактивный ответ для клиента.
    """
    count = len(track_codes)
    return f"🎉 Я обнаружил {count} трек-код(ов) в вашем сообщении. Чтобы добавить их, пожалуйста, выберите '➕ Добавить заказ' в меню. Я смогу обработать весь ваш текст сразу!"

async def get_shipping_price(api_request_func, company_id: int) -> str:
    """
    Получает актуальную цену за доставку ($/кг).
    Используй, если клиент спрашивает "Сколько стоит?", "Цена за кг", "Тарифы".
    """
    try:
        response = await api_request_func("GET", f"/api/bot/price?company_id={company_id}")
        if response and "price" in response:
            price = response["price"]
            if price > 0:
                return json.dumps({"price_usd": price, "message": f"Актуальная цена: {price}$ за кг."}, ensure_ascii=False)
            else:
                return json.dumps({"message": "Цена пока не установлена (смен не было)."}, ensure_ascii=False)
        return json.dumps({"error": "Не удалось получить цену."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

# =================================================================
# --- (СТАРЫЙ ПРОМПТ 'TOOLS_SYSTEM_PROMPT' ПОЛНОСТЬЮ УДАЛЕН) ---
# =================================================================


# =================================================================
# --- 2. ФУНКЦИИ-ОБРАБОТЧИКИ (ПОЛНАЯ ПЕРЕПИСЬ) ---
# =================================================================

async def execute_ai_tool(tool_command: dict, api_request_func, company_id: int, employee_id: Optional[int], client_id: Optional[int] = None) -> str:
    """
    Выполняет "мысли" ИИ, превращая их в действия API или кнопки подтверждения.
    (ВЕРСИЯ 3.0 - Добавлены клиентские инструменты)
    """
    tool = tool_command.get("tool")
    
    # --- БЛОК КЛИЕНТСКИХ ИНСТРУМЕНТОВ ---
    
    if tool == "get_user_orders_json":
        if not client_id: return "❌ Ошибка: ID клиента не определен для инструмента."
        # --- (НОВОЕ) ---
        # Ищем опциональный фильтр статусов в команде ИИ
        status_list_filter = tool_command.get("statuses") 
        # --- (КОНЕЦ НОВОГО) ---
        return await get_user_orders_json(
            api_request_func, 
            client_id, 
            company_id, 
            status_filter=status_list_filter # <-- Передаем фильтр
        )

    elif tool == "add_client_order_request":
        if not client_id: return "❌ Ошибка: ID клиента не определен для инструмента."
        request_text = tool_command.get("request_text")
        if not request_text: return "❌ Ошибка: Не передан текст запроса на оформление заказа."
        return await add_client_order_request(api_request_func, client_id, company_id, request_text)
    
    elif tool == "get_company_locations":
        return await get_company_locations(api_request_func, company_id)
    
    elif tool == "get_shipping_price":
        return await get_shipping_price(api_request_func, company_id)
    
    # ... (после get_shipping_price) ...
    elif tool == "notify_buyout_request":
        if not client_id: return "Ошибка: Вы не зарегистрированы."
        return await notify_buyout_request(
            api_request_func, 
            client_id, 
            company_id, 
            tool_command.get("amount_yuan", 0), 
            tool_command.get("amount_som", 0)
        )
    # ...

    elif tool == "alert_order_submission":
        tracks = tool_command.get("track_codes")
        if not tracks or len(tracks) < 2: 
             return "❌ Ошибка логики: Инструмент вызван с недостаточным количеством трек-кодов."
        return await alert_order_submission(tracks)

    # --- БЛОК АДМИНСКИХ ИНСТРУМЕНТОВ (требует employee_id) ---
    
    if not employee_id:
        return "❌ Ошибка: Вы не авторизованы как сотрудник (Владелец) для использования административных инструментов."

    try:
        # === БЛОК 1: ЗАКАЗЫ ===
        
        if tool == "search_order":
            query = tool_command.get("query")
            response = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": query, "company_id": company_id, "limit": 5})
            if not response: return "❌ Заказы не найдены."
            text = f"🔍 **Поиск заказа '{query}':**\n"
            for o in response:
                client = f"{o.get('client', {}).get('full_name')} ({o.get('client', {}).get('client_code_prefix')}{o.get('client', {}).get('client_code_num')})" if o.get('client') else "🔴 Неизвестный"
                text += f"- `{o['track_code']}`: {o['status']}\n  👤 {client}\n  📅 {o['party_date']}\n"
            return text

        elif tool == "update_order_status":
            track = tool_command.get("track_code")
            status = tool_command.get("new_status")
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            if not orders: return f"❌ Заказ `{track}` не найден."
            return json.dumps({
                "confirm_action": "update_single", "order_id": orders[0]['id'], "track": track, "new_status": status,
                "message": f"❓ Изменить статус заказа `{track}` на **{status}**?"
            })

        elif tool == "delete_order":
            track = tool_command.get("track_code")
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            if not orders: return f"❌ Заказ `{track}` не найден."
            return json.dumps({
                "confirm_action": "delete_order", "order_id": orders[0]['id'], "track": track,
                "message": f"🗑 **УДАЛЕНИЕ ЗАКАЗА**\nВы уверены, что хотите удалить заказ `{track}`? Это необратимо."
            })

        elif tool == "assign_client":
            track = tool_command.get("track_code")
            c_query = tool_command.get("client_search")
            clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": c_query, "company_id": company_id})
            if not clients: return f"❌ Клиент '{c_query}' не найден."
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            if not orders: return f"❌ Заказ `{track}` не найден."
            return json.dumps({
                "confirm_action": "assign_client", "order_id": orders[0]['id'], "track": track, "client_id": clients[0]['id'], "client_name": clients[0]['full_name'],
                "message": f"❓ Присвоить заказ `{track}` клиенту **{clients[0]['full_name']}**?"
            })

        # === БЛОК 2: КЛИЕНТЫ ===

        elif tool == "search_client":
            query = tool_command.get("query")
            clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": query, "company_id": company_id})
            if not clients: return "❌ Клиенты не найдены."
            text = f"🔍 **Поиск клиента '{query}':**\n"
            for c in clients:
                code = f"{c.get('client_code_prefix')}{c.get('client_code_num')}"
                text += f"- **{c['full_name']}** (Код: {code})\n  📞 {c['phone']}\n"
            return text

        elif tool == "change_client_code":
            search = tool_command.get("client_search")
            new_code = tool_command.get("new_code_num")
            clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": search, "company_id": company_id})
            if not clients: return f"❌ Клиент '{search}' не найден."
            client = clients[0]
            return json.dumps({
                "confirm_action": "change_client_code", "client_id": client['id'], "client_name": client['full_name'], "new_code": new_code,
                "message": f"❓ Сменить код клиента **{client['full_name']}** на номер **{new_code}**?"
            })

        elif tool == "delete_client":
            search = tool_command.get("client_search")
            clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": search, "company_id": company_id})
            if not clients: return f"❌ Клиент '{search}' не найден."
            client = clients[0]
            return json.dumps({
                "confirm_action": "delete_client", "client_id": client['id'], "client_name": client['full_name'],
                "message": f"🗑 **УДАЛЕНИЕ КЛИЕНТА**\nВы точно хотите удалить **{client['full_name']}**? Его заказы могут потеряться."
            })

        # === БЛОК 3: ФИНАНСЫ И РАССЫЛКА ===

        elif tool == "add_expense":
            amount = tool_command.get("amount")
            reason = tool_command.get("reason")
            return json.dumps({
                "confirm_action": "add_expense", "amount": amount, "reason": reason,
                "message": f"💸 Записать расход **{amount} сом**?\nПричина: *{reason}*"
            })

        elif tool == "broadcast":
            text = tool_command.get("text")
            return json.dumps({
                "confirm_action": "broadcast", "text": text,
                "message": f"📢 **ОТПРАВИТЬ РАССЫЛКУ ВСЕМ?**\n\nТекст:\n{text}"
            })

        elif tool == "get_report":
            start = tool_command.get("period_start")
            end = tool_command.get("period_end")
            report = await api_request_func("GET", "/api/reports/summary", employee_id=employee_id, params={"start_date": start, "end_date": end, "company_id": company_id})
            if not report or "summary" not in report: return "❌ Ошибка отчета."
            s = report['summary']
            return f"📊 **Отчет ({start} - {end}):**\n💰 Выручка: {s['total_income']}\n📉 Расходы: {s['total_expenses']}\n💵 Чистая: {s['net_profit']}"

        # === БЛОК 4: ПАРТИИ ===
        
        elif tool == "get_active_parties":
            parties = await api_request_func("GET", "/api/orders/parties", employee_id=employee_id, params={"company_id": company_id})
            return f"📅 **Партии:**\n" + "\n".join([f"- {d}" for d in parties]) if parties else "Нет партий."

        elif tool == "bulk_update_party":
            date_str = tool_command.get("party_date")
            status = tool_command.get("new_status")
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"party_dates": date_str, "company_id": company_id})
            count = len(orders) if orders else 0
            if count == 0: return f"❌ Нет заказов за {date_str}."
            return json.dumps({
                "confirm_action": "bulk_status", "party_date": date_str, "new_status": status, "count": count,
                "message": f"❓ Перевести партию от **{date_str}** ({count} шт) в статус **{status}**?"
            })
            
        # === БЛОК 5: КОНФИГУРАЦИЯ ===
        elif tool == "get_settings":
            # Используем API для Владельца, чтобы получить все настройки
            api_response = await api_request_func("GET", "/api/settings", employee_id=employee_id)
            
            if not api_response: 
                 return "❌ Ошибка загрузки настроек."
            
            settings_dict = {s.get('key'): s.get('value') for s in api_response}
            
            settings_text = "⚙️ **Текущие Настройки Системы:**\n"
            
            key_map = {
                'china_warehouse_address': 'Адрес склада (Китай)',
                'instruction_pdf_link': 'Ссылка на PDF-инструкцию',
                'client_code_start': 'Начальный код клиента',
                'office_schedule': 'График работы офиса',
                'password_revert_order': 'Пароль на отмену выдачи',
                'password_delete_order': 'Пароль на удаление заказа',
                'password_delete_client': 'Пароль на удаление клиента',
            }

            for key, display_name in key_map.items():
                value = settings_dict.get(key)
                if value:
                    display_value = '*** (Установлен)' if key.startswith('password') else value
                    settings_text += f"- **{display_name}**: {display_value}\n"
                elif key not in settings_dict:
                     settings_text += f"- **{display_name}**: ⚠️ Не настроено\n"
            
            ai_status = settings_dict.get('ai_enabled')
            ai_status_text = "✅ ВКЛЮЧЕН" if ai_status == 'True' else "❌ ВЫКЛЮЧЕН"
            settings_text += f"\n🤖 **AI Ассистент (Рубильник)**: {ai_status_text}"
            
            return settings_text
            
        else:
            return f"⚠️ Инструмент '{tool}' не поддерживается."

    except Exception as e:
        logger.error(f"AI Tool Error: {e}")
        return "❌ Ошибка выполнения команды."
