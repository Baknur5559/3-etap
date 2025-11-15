# ai_tools.py (Полностью переписанная версия 3.0 - с инструментами для клиента и Админа)

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

async def get_user_orders_json(api_request_func, client_id: int, company_id: int) -> str:
    """
    Получает список всех невыданных заказов клиента для предоставления информации о статусе.
    :param api_request_func: Асинхронная функция для выполнения API запросов.
    :param client_id: ID текущего клиента.
    :param company_id: ID текущей компании.
    :return: JSON-строка со списком заказов.
    """
    active_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
    
    orders = await api_request_func(
        "GET",
        "/api/orders",
        params={
            "client_id": client_id,
            "company_id": company_id,
            "statuses": active_statuses,
            "limit": 10 # Ограничиваем для краткости контекста
        }
    )

    if not orders or "error" in orders:
        return json.dumps({"error": "Не удалось загрузить заказы или их нет."}, ensure_ascii=False)

    formatted_orders = [
        {
            "трек": o.get('track_code'),
            "статус": o.get('status'),
            "комментарий": o.get('comment'),
            "расчет_вес_кг": o.get('calculated_weight_kg'),
            "расчет_сумма_сом": o.get('calculated_final_cost_som'),
        } for o in orders
    ]
    
    return json.dumps({"active_orders": formatted_orders}, ensure_ascii=False)


async def add_client_order_request(api_request_func, client_id: int, company_id: int, request_text: str) -> str:
    """
    (ИСПРАВЛЕНО) Используйте этот инструмент, когда клиент просит создать заказ, добавить товар или передает список товаров для оформления.
    :param api_request_func: Асинхронная функция для выполнения API запросов.
    :param client_id: ID текущего клиента.
    :param company_id: ID текущей компании.
    :param request_text: Полный текст запроса клиента на создание заказа.
    :return: JSON-ответ от API.
    """
    try:
        if not client_id or not company_id:
            return json.dumps({"status": "error", "message": "Ошибка: ID клиента или компании не определен."}, ensure_ascii=False)
            
        response = await api_request_func(
            "POST",
            "/api/bot/order_request", # Используем эндпоинт, который парсит текст
            json={
                "client_id": client_id,
                "company_id": company_id,
                "request_text": request_text
            }
        )
        if "error" in response:
            logger.error(f"[AI Tool Error] /api/bot/order_request: {response.get('error')}")
            # Если ошибка парсинга (например, не найдено треков), просим клиента уточнить
            if "не смог найти" in response.get("error", ""):
                 return json.dumps({"status": "error", "message": "Я не смог распознать трек-коды в вашем сообщении. Пожалуйста, отправьте их в формате: ТРЕК-КОД Комментарий."}, ensure_ascii=False)
            
            return json.dumps({"status": "error", "message": response.get("error")}, ensure_ascii=False)
        
        # Собираем красивый ответ
        created = response.get("created", 0)
        assigned = response.get("assigned", 0)
        skipped = response.get("skipped", 0)
        
        response_text = "Готово! 🚀\n"
        if created > 0: response_text += f"✔️ Новых заказов добавлено: {created}\n"
        if assigned > 0: response_text += f"✨ Найдено и присвоено вам: {assigned}\n"
        if skipped > 0: response_text += f"⚠️ Пропущено (дубликаты): {skipped}\n"

        return json.dumps({"status": "success", "message": response_text, "data": response}, ensure_ascii=False)
    
    except Exception as e:
        logger.error(f"!!! [AI Tool Error] add_client_order_request: {e}", exc_info=True)
        return json.dumps({"status": "error", "message": f"Ошибка связи с сервером: {e}"}, ensure_ascii=False)


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

# =================================================================
# --- 1. ИНСТРУКЦИЯ ДЛЯ ИИ (СИСТЕМНЫЙ ПРОМПТ) ---
# =================================================================

TOOLS_SYSTEM_PROMPT = """
⚡️ **РЕЖИМ АДМИНИСТРАТОРА**
Ты имеешь ПОЛНЫЙ доступ к управлению CRM. Твоя цель — помогать Владельцу управлять бизнесом быстро.

🧠 **КАК ПОНИМАТЬ КОМАНДЫ:**
Понимай с полуслова. Контекст — твой друг.

🛠 **СПИСОК ИНСТРУМЕНТОВ (Возвращай JSON):**

1. **УПРАВЛЕНИЕ ЗАКАЗАМИ:**
   - Поиск: `{"tool": "search_order", "query": "..."}`
   - Смена статуса: `{"tool": "update_order_status", "track_code": "...", "new_status": "..."}`
   - Присвоение (Магия): `{"tool": "assign_client", "track_code": "...", "client_search": "..."}`
   - ❌ Удаление: `{"tool": "delete_order", "track_code": "..."}`

2. **УПРАВЛЕНИЕ КЛИЕНТАМИ:**
   - Поиск: `{"tool": "search_client", "query": "..."}` (Найти телефон, код)
   - Смена кода: `{"tool": "change_client_code", "client_search": "...", "new_code_num": 123}`
   - ❌ Удаление: `{"tool": "delete_client", "client_search": "..."}`

3. **ФИНАНСЫ И КАССА:**
   - Добавить расход: `{"tool": "add_expense", "amount": 100, "reason": "..."}`
   - Отчет: `{"tool": "get_report", "period_start": "YYYY-MM-DD", "period_end": "..."}`

4. **ПАРТИИ И МАССОВЫЕ ДЕЙСТВИЯ:**
   - Список партий: `{"tool": "get_active_parties"}`
   - Массовая смена статуса: `{"tool": "bulk_update_party", "party_date": "...", "new_status": "..."}`

5. **📢 РАССЫЛКА (ОБЪЯВЛЕНИЯ):**
   - Если просят *написать* объявление -> Сначала просто сгенерируй красивый текст с эмодзи в чат.
   - Если просят *отправить* текст -> `{"tool": "broadcast", "text": "..."}`

6. **⚙️ КОНФИГУРАЦИЯ:** - Получить настройки компании: `{"tool": "get_settings"}`

⚠️ **ВАЖНО:** Для любых действий, меняющих данные (удаление, смена, расход), ты должен вернуть JSON. Бот сам спросит подтверждение у Владельца.
"""
# --- КОНЕЦ СИСТЕМНОГО ПРОМПТА ДЛЯ АДМИНА ---


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
        return await get_user_orders_json(api_request_func, client_id, company_id)

    elif tool == "add_client_order_request":
        if not client_id: return "❌ Ошибка: ID клиента не определен для инструмента."
        request_text = tool_command.get("request_text")
        if not request_text: return "❌ Ошибка: Не передан текст запроса на оформление заказа."
        return await add_client_order_request(api_request_func, client_id, company_id, request_text)
    
    elif tool == "get_company_locations":
        return await get_company_locations(api_request_func, company_id)

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
