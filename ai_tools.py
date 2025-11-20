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

async def get_user_orders_json(api_request_func, client_id: int, company_id: int, status_filter: Optional[List[str]] = None, uncalculated_only: bool = False) -> str:
    """
    Умный инструмент: Возвращает список заказов И (НОВОЕ) считает ИТОГО внизу.
    """
    
    if not status_filter:
        statuses_to_fetch = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
    else:
        statuses_to_fetch = status_filter
    
    params = {
        "client_id": client_id,
        "company_id": company_id,
        "statuses": statuses_to_fetch,
        "limit": 100
    }
    
    # --- [NEW] ДОБАВЛЯЕМ ФИЛЬТР ---
    if uncalculated_only:
        params["uncalculated_only"] = True
    # ------------------------------

    orders = await api_request_func("GET", "/api/orders", params=params)

    if not orders or (isinstance(orders, dict) and "error" in orders):
        return json.dumps({"message": "📭 У этого клиента сейчас нет таких заказов."}, ensure_ascii=False)
    
    if not isinstance(orders, list):
        return json.dumps({"error": "Ошибка формата данных."}, ensure_ascii=False)

    # --- РЕЖИМ 1: ДЕТАЛЬНЫЙ СПИСОК + ИТОГИ ---
    if status_filter:
        formatted_orders = []
        
        # Переменные для подсчета итогов
        total_weight = 0.0
        total_cost = 0.0
        count_calculated = 0

        for o in orders:
            # Собираем историю
            history_entries = []
            if o.get('history_entries'):
                for entry in o['history_entries']:
                    history_entries.append({
                        "status": entry.get('status'),
                        "date": entry.get('created_at')
                    })
            
            # Собираем данные для вывода
            order_data = {
                "трек": o.get('track_code'),
                "статус": o.get('status'),
                "комментарий": o.get('comment'),
                "партия": o.get('party_date'), 
                "history_entries": history_entries 
            }

            # Логика подсчета
            w = o.get('calculated_weight_kg') or o.get('weight_kg')
            c = o.get('calculated_final_cost_som') or o.get('final_cost_som')
            
            if w is not None and c is not None:
                order_data["расчет"] = f"{w} кг / {c} сом"
                total_weight += float(w)
                total_cost += float(c)
                count_calculated += 1
            else:
                order_data["расчет"] = None

            formatted_orders.append(order_data)
        
        # Формируем итоговое сообщение
        response_json = {"active_orders": formatted_orders}
        
        # Если есть посчитанные заказы, добавляем блок итогов
        if count_calculated > 0:
            summary_text = (
                f"\n💰 <b>ИТОГО ПО СПИСКУ ({count_calculated} шт):</b>\n"
                f"⚖️ Общий вес: <b>{total_weight:.2f} кг</b>\n"
                f"💵 Общая сумма: <b>{total_cost:.2f} сом</b>"
            )
            response_json["summary_footer"] = summary_text # Бот добавит это в конец сообщения

        return json.dumps(response_json, ensure_ascii=False)

    # --- РЕЖИМ 2: СВОДКА + ОБЩИЙ СЧЕТЧИК ---
    else:
        # Запрашиваем ВСЕ заказы (включая Выданные), чтобы дать точную цифру
        # Для этого временно убираем фильтр статусов или добавляем "Выдан"
        all_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]
        
        all_orders = await api_request_func(
            "GET",
            "/api/orders",
            params={
                "client_id": client_id,
                "company_id": company_id,
                "statuses": all_statuses,
                "limit": 200 # Берем больше для статистики
            }
        )
        
        if not all_orders: return json.dumps({"message": "📭 История заказов пуста."}, ensure_ascii=False)
        
        stats = {}
        parties = set()
        transit_statuses = ["В пути", "На складе в Китае", "На складе в КР"]
        total_count = len(all_orders)

        for o in all_orders:
            status = o.get('status', 'Неизвестно')
            stats[status] = stats.get(status, 0) + 1
            if status in transit_statuses and o.get('party_date'):
                parties.add(o.get('party_date'))

        summary_msg = f"📊 **Сводка по клиенту (Всего: {total_count} шт):**\n\n"
        priority_order = ["Готов к выдаче", "На складе в КР", "В пути", "На складе в Китае", "Выкуплен", "Ожидает выкупа", "В обработке", "Выдан"]
        
        for st in priority_order:
            if stats.get(st, 0) > 0:
                icon = "✅" if st == "Готов к выдаче" else "🚚" if st == "В пути" else "🏁" if st == "Выдан" else "📦"
                summary_msg += f"{icon} <b>{st}:</b> {stats[st]} шт.\n"

        if parties:
            summary_msg += f"\n📅 <b>Партии в пути:</b> {', '.join(sorted(list(parties), reverse=True))}"

        return json.dumps({"message": summary_msg}, ensure_ascii=False)

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
    (ИСПРАВЛЕНО) Используется ТОЛЬКО для получения актуальной информации о филиалах компании: адресах, телефонах и графике работы.
    :param api_request_func: Асинхронная функция для выполнения API запросов.
    :param company_id: ID текущей компании.
    :return: JSON-строка со списком филиалов.
    """
    try:
        # --- (ИСПРАВЛЕНИЕ) ---
        # Убираем f-строку из URL и передаем company_id через 'params',
        # как того ожидает 'api_request_func' в bot_template.py.
        response = await api_request_func(
            "GET", 
            "/api/bot/locations", 
            params={"company_id": company_id}
        )
        # --- (КОНЕЦ ИСПРАВЛЕНИЯ) ---
        
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
    Получает актуальную цену ($/кг) И КУРС.
    """
    try:
        # 1. Запрос к API
        response = await api_request_func(
            "GET", 
            "/api/bot/price", 
            params={"company_id": company_id}
        )
        
        # 2. Проверка ответа (API всегда должен возвращать JSON)
        if not response or "price_usd" not in response:
            logger.error(f"[AI Tool] get_shipping_price: Странный ответ API: {response}")
            return json.dumps({"message": "Не удалось получить тарифы. Попробуйте позже."}, ensure_ascii=False)

        price_usd = response.get("price_usd", 0.0)
        exchange_rate = response.get("exchange_rate", 0.0)

        # 3. Проверка, что цены установлены (не 0)
        if price_usd > 0 and exchange_rate > 0:
            price_som = price_usd * exchange_rate
            
            message = (
                f"Актуальный тариф:\n"
                f"<b>{price_usd}$</b> за кг.\n"
                f"По текущему курсу ({exchange_rate} сом) это примерно <b>{price_som:.0f} сом</b> за кг."
            )
            return json.dumps({
                "price_usd": price_usd,
                "exchange_rate": exchange_rate,
                "price_som": round(price_som, 2),
                "message": message
            }, ensure_ascii=False)
            
        else:
            # Если цена 0 (смен не было)
            return json.dumps({"message": "Тариф пока не установлен (нет активных или закрытых смен). Уточните у менеджера."}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"!!! [AI Tool Exception] get_shipping_price: {e}", exc_info=True)
        # Возвращаем мягкое сообщение, а не JSON-ошибку
        return json.dumps({"message": "Временно не могу узнать цену. Напишите менеджеру."}, ensure_ascii=False)
    
async def create_delivery_request(api_request_func, client_id: int, company_id: int, address: str, method: str, delivery_time: str) -> str:
    """
    Создает заявку на доставку и уведомляет владельца.
    """
    try:
        if not client_id: return json.dumps({"error": "Вы не авторизованы."}, ensure_ascii=False)

        response = await api_request_func(
            "POST",
            "/api/bot/notify_delivery",
            json={
                "client_id": client_id,
                "company_id": company_id,
                "address": address,
                "delivery_method": method,
                "delivery_time": delivery_time, # <-- Передаем время
                "comment": "Заявка через AI"
            }
        )
        return json.dumps({"status": "success", "message": "✅ Заявка принята! Менеджер получил данные и скоро свяжется."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

# =================================================================
# --- (СТАРЫЙ ПРОМПТ 'TOOLS_SYSTEM_PROMPT' ПОЛНОСТЬЮ УДАЛЕН) ---
# =================================================================


# =================================================================
# --- 2. ФУНКЦИИ-ОБРАБОТЧИКИ (ПОЛНАЯ ПЕРЕПИСЬ) ---
# =================================================================

async def update_orders_by_tracks(api_request_func, employee_id, company_id, track_codes, new_status):
    """
    Инструмент: Ищет заказы по трек-кодам и готовит кнопку для смены статуса.
    НЕ МЕНЯЕТ статус сам, только находит ID.
    """
    try:
        # 1. Нормализуем треки
        clean_tracks = [t.strip() for t in track_codes if t.strip()]
        if not clean_tracks: return "❌ Нет трек-кодов."

        found_ids = []
        clients_found = set()
        client_names = []
        found_tracks_str = []
        
        # 2. Ищем каждый заказ
        for track in clean_tracks:
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            if orders:
                order = orders[0]
                found_ids.append(order['id'])
                found_tracks_str.append(order['track_code'])
                
                if order.get('client'):
                    c = order['client']
                    clients_found.add(c['id'])
                    client_info = f"{c['full_name']} (ID {c['id']})"
                    if client_info not in client_names:
                        client_names.append(client_info)
                else:
                    clients_found.add("unclaimed")
                    if "Невостребованный" not in client_names:
                        client_names.append("Невостребованный")

        if not found_ids:
            return f"❌ Ни один из трек-кодов {clean_tracks} не найден в базе."

        # 3. Проверка на "кашу" (разные клиенты)
        if len(clients_found) > 1:
             names_str = ", ".join(client_names)
             return json.dumps({
                 "status": "error",
                 "message": f"⚠️ Внимание! Эти трек-коды принадлежат РАЗНЫМ клиентам ({names_str}).\nЯ не могу массово менять статус для разных людей одновременно во избежание ошибок."
             }, ensure_ascii=False)

        # 4. Возвращаем JSON для кнопки (БЕЗ ВЫПОЛНЕНИЯ ДЕЙСТВИЯ)
        owner_str = client_names[0] if client_names else "Неизвестно"
        count = len(found_ids)
        
        return json.dumps({
            "confirm_action": "bulk_status_manual", # Тип действия для бота
            "ids": found_ids,       # <-- ВАЖНО: Передаем ID, чтобы кнопка сработала
            "new_status": new_status,
            "count": count,
            "message": (
                f"🔄 <b>СМЕНА СТАТУСА</b>\n"
                f"📦 Заказов: {count}\n"
                f"👤 Владелец: <b>{owner_str}</b>\n"
                f"📝 Статус: <b>'{new_status}'</b>\n\n"
                f"❓ Подтверждаете изменение?"
            )
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка поиска: {e}"

async def execute_ai_tool(tool_command: dict, api_request_func, company_id: int, employee_id: Optional[int], client_id: Optional[int] = None) -> str:
    """
    Выполняет "мысли" ИИ, превращая их в действия API или кнопки подтверждения.
    (ВЕРСИЯ 3.0 - Добавлены клиентские инструменты)
    """
    tool = tool_command.get("tool")

    # ===========================================================
    # 🛡 ЗАЩИТА ОТ ВЛОЖЕННОСТИ (ЕСЛИ БОТ ПРОПУСТИЛ)
    # ===========================================================
    # Если параметры спрятаны внутри 'parameters', 'arguments' или 'args' — вытаскиваем их!
    for key in ['parameters', 'arguments', 'args', 'params']:
        if key in tool_command:
            nested = tool_command[key]
            if isinstance(nested, dict):
                tool_command.update(nested) # Сливаем параметры в основной словарь
                logger.info(f"[AI Tool] Unpacked nested '{key}': {nested}")
    # ===========================================================
    
    # --- БЛОК КЛИЕНТСКИХ ИНСТРУМЕНТОВ ---
    
    if tool == "get_user_orders_json":
        if not client_id: return "❌ Ошибка: ID клиента не определен для инструмента."
        status_list_filter = tool_command.get("statuses") 
        uncalculated_only = tool_command.get("uncalculated_only") # <-- Извлекаем параметр
        
        return await get_user_orders_json(
            api_request_func, 
            client_id, 
            company_id, 
            status_filter=status_list_filter,
            uncalculated_only=uncalculated_only # <-- Передаем
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

    elif tool == "create_delivery_request":
        if not client_id: return "Ошибка: Вы не зарегистрированы."
        
        address = str(tool_command.get("address", "")).strip()
        method = str(tool_command.get("method", "")).strip()
        delivery_time = str(tool_command.get("delivery_time", "Как можно скорее")).strip()
        
        # 1. СПИСОК СТОП-СЛОВ (Явные галлюцинации)
        stop_words = ["не указан", "не знаю", "нет", "unknown", "адрес", "null", "none", ""]
        if address.lower() in stop_words:
             return "Пожалуйста, напишите точный адрес доставки. ✍️"

        # 2. ПРАВИЛО ЦИФРЫ (Главная защита)
        # Если в адресе нет ни одной цифры — это не адрес, а улица или район.
        has_digit = any(char.isdigit() for char in address)
        
        # Слова-маркеры неточных адресов
        vague_words = ["возле", "рядом", "напротив", "пересечение", "угла", "район", "пер.", "перекресток"]
        is_vague = any(word in address.lower() for word in vague_words)
        
        if not has_digit:
            if is_vague:
                return f"Вы написали ориентир: '{address}'. Курьеру нужен точный адрес. Пожалуйста, напишите **номер дома** или здания."
            else:
                return f"Уточните, пожалуйста: '{address}' — это улица или район? Напишите **номер дома**, чтобы я мог оформить доставку."

        # 3. ПРОВЕРКА МЕТОДА
        if not method or len(method) < 2 or method.lower() in stop_words:
             # Здесь ИИ должен посмотреть в правила компании, но если он тупит, подскажем:
             return "Уточните, пожалуйста, какой службой отправить? (Например: Яндекс, СДЭК) 🚚"
        
        # Если адрес содержит цифру и метод указан — создаем
        return await create_delivery_request(
            api_request_func, 
            client_id, 
            company_id, 
            address, 
            method,
            delivery_time # <-- Передаем время
        )
    
    elif tool == "submit_complaint":
        text = tool_command.get("text")
        if not text: return "Ошибка: Пустой текст жалобы."
        
        return await submit_complaint(
            api_request_func, 
            client_id, 
            company_id, 
            text
        )

    elif tool == "alert_order_submission":
        tracks = tool_command.get("track_codes")
        if not tracks or len(tracks) < 2: 
             return "❌ Ошибка логики: Инструмент вызван с недостаточным количеством трек-кодов."
        return await alert_order_submission(tracks)
    
    # === НОВЫЕ ИНСТРУМЕНТЫ (ШАГ 1) ===
        
    elif tool == "get_orders_by_date":
            target_date = tool_command.get("target_date")
            return await get_orders_by_date(api_request_func, employee_id, company_id, target_date)

    elif tool == "calculate_orders":
            client_id = tool_command.get("target_client_id")
            client_search = tool_command.get("client_search")
            
            # --- ЗАЩИТА ОТ ЗАПЯТЫХ ---
            raw_weight = tool_command.get("total_weight", 0)
            if isinstance(raw_weight, str):
                raw_weight = raw_weight.replace(',', '.').strip()
            try:
                total_weight = float(raw_weight)
            except ValueError:
                return "❌ Ошибка: Некорректный формат веса."
            # -------------------------
            
            party_date = tool_command.get("party_date")
            uncalculated_only = tool_command.get("uncalculated_only")
            
            # --- [NEW] ИЗВЛЕКАЕМ СПИСОК ТРЕКОВ ---
            track_codes = tool_command.get("track_codes") # Ожидаем список строк
            if isinstance(track_codes, str): # Если вдруг ИИ прислал строку "A, B", превращаем в список
                track_codes = [t.strip() for t in track_codes.split(',')]
            # -------------------------------------
            
            return await prepare_calculation(
                api_request_func, 
                employee_id, 
                company_id, 
                client_id=client_id, 
                client_search=client_search, 
                total_weight=total_weight,
                party_date=party_date,
                uncalculated_only=uncalculated_only,
                track_codes=track_codes # <-- ПЕРЕДАЕМ СЮДА
            )

    elif tool == "update_client_data":
            client_search = tool_command.get("client_search")
            new_phone = tool_command.get("new_phone")
            new_code = tool_command.get("new_code")
            # Преобразуем код в число, если передан
            if new_code and str(new_code).isdigit(): new_code = int(new_code)
            return await prepare_client_update(api_request_func, employee_id, company_id, client_search, new_phone, new_code)
            
        # =================================
    elif tool == "bulk_update_client_orders":
        client_id = tool_command.get("target_client_id")
        old_status = tool_command.get("old_status")
        new_status = tool_command.get("new_status")
        if not client_id: return "❌ Нет ID клиента."
        return await bulk_update_client_orders(api_request_func, employee_id, company_id, int(client_id), old_status, new_status)
    
    elif tool == "get_all_party_dates":
        return await get_all_party_dates(api_request_func, company_id)
    
    elif tool == "undo_last_operation":
        op_id = tool_command.get("operation_id")
        if not op_id: return "❌ Нет ID операции."
        return await undo_last_operation(api_request_func, int(op_id))
    
    
    elif tool == "update_orders_by_tracks":
        tracks = tool_command.get("track_codes")
        new_status = tool_command.get("new_status")
        
        # Обработка строки вместо списка
        if isinstance(tracks, str): tracks = [t.strip() for t in tracks.split(',')]
        
        return await update_orders_by_tracks(api_request_func, employee_id, company_id, tracks, new_status)

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
            if not track: return "❌ Ошибка: Не указан трек-код."

            # Сначала ищем заказ, чтобы узнать его ID
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            
            if not orders: return f"❌ Заказ `{track}` не найден."
            
            order = orders[0]
            
            # Возвращаем кнопку подтверждения
            return json.dumps({
                "confirm_action": "delete_order", 
                "order_id": order['id'], 
                "track": track,
                "message": f"🗑 **УДАЛЕНИЕ ЗАКАЗА**\nТрек: `{track}`\nКлиент: {order.get('client', {}).get('full_name', 'Неизвестно')}\n\nВы уверены? Это необратимо."
            }, ensure_ascii=False)

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
            query = tool_command.get("query") or tool_command.get("client_search") or tool_command.get("name")
            if not query: return "❌ Ошибка: Пустой запрос поиска."

            # ИСПРАВЛЕНИЕ: используем api_request_func вместо api_request
            clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": query, "company_id": company_id})

            if not clients: return f"❌ Клиенты по запросу '{query}' не найдены."

            # --- ЛОВУШКА ДЛЯ ДУБЛИКАТОВ ---
            if len(clients) > 1:
                # Возвращаем JSON, чтобы ИИ понял: "СТОП, НУЖНО УТОЧНЕНИЕ"
                options = []
                for c in clients:
                    code = f"{c.get('client_code_prefix')}{c.get('client_code_num')}"
                    options.append(f"ID {c['id']}: {c['full_name']} ({code})")
                
                return json.dumps({
                    "status": "multiple_results",
                    "message": f"⚠️ Найдено {len(clients)} клиентов. Уточните, кто именно?",
                    "options": options
                }, ensure_ascii=False)
            # ------------------------------

            # Если найден один - показываем как обычно
            c = clients[0]
            code = f"{c.get('client_code_prefix')}{c.get('client_code_num')}"
            return (
                f"✅ **Клиент найден:**\n"
                f"👤 {c['full_name']}\n"
                f"🆔 ID: {c['id']}\n"
                f"🔢 Код: {code}\n"
                f"📞 {c['phone']}\n\n"
                f"👉 *Работаем с ним?*"
            )
        
        elif tool == "admin_get_client_orders":
            target_id = tool_command.get("target_client_id")
            if not target_id: return "❌ Ошибка: Не передан ID клиента."
            
            # --- ЗАЩИТА ОТ МУСОРА (FIX 'KBNone') ---
            try:
                client_id_int = int(target_id)
            except (ValueError, TypeError):
                return f"❌ Ошибка ИИ: ID клиента должен быть числом (например, 3031). Вы передали: '{target_id}'. Сначала выполните поиск клиента (search_client)."
            # ---------------------------------------

            status_filter = tool_command.get("statuses") 
            uncalculated_only = tool_command.get("uncalculated_only")
            
            return await get_user_orders_json(
                api_request_func, 
                client_id_int, # Используем проверенное число
                company_id, 
                status_filter=status_filter,
                uncalculated_only=uncalculated_only
            )

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
    
async def submit_complaint(api_request_func, client_id: int, company_id: int, text: str) -> str:
    """
    Отправляет жалобу клиента руководству.
    """
    try:
        if not client_id: return json.dumps({"error": "Вы не авторизованы."}, ensure_ascii=False)
        
        response = await api_request_func(
            "POST",
            "/api/bot/notify_complaint",
            json={
                "client_id": client_id,
                "company_id": company_id,
                "complaint_text": text
            }
        )
        # Возвращаем ИИ инструкцию, что сказать клиенту (ИИ перефразирует это тепло)
        return json.dumps({"status": "success", "message": "✅ Ваша жалоба официально зарегистрирована и передана руководству. Мы разберемся в ближайшее время."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    
async def get_orders_by_date(api_request_func, employee_id: int, company_id: int, target_date: str) -> str:
    """
    Инструмент: Поиск заказов по конкретной дате партии.
    (Safe Version)
    """
    # --- [FIX] ЗАЩИТА ОТ ПУСТОЙ ДАТЫ ---
    if not target_date or len(target_date) < 5:
        return "❌ Ошибка: Не указана корректная дата (формат YYYY-MM-DD)."
    # -----------------------------------

    try:
        orders = await api_request_func(
            "GET", 
            "/api/orders", 
            employee_id=employee_id, 
            params={"party_dates": target_date, "company_id": company_id}
        )
        
        # --- ЗАЩИТА ОТ КРАША (FIX 'str' object has no attribute 'get') ---
        # 1. Если вернулся словарь с ошибкой
        if isinstance(orders, dict) and "error" in orders:
            return f"⚠️ Ошибка API: {orders.get('error')}"
        
        # 2. Если вернулось что-то, что не список
        if not isinstance(orders, list):
             return f"⚠️ Некорректный ответ сервера (ожидался список, получено: {type(orders)})."
        # ---------------------------------------------------------------
        
        if not orders: 
            return f"📅 Заказов за дату **{target_date}** не найдено."
            
        text = f"📅 **Заказы партии от {target_date} ({len(orders)} шт):**\n\n"
        
        status_counts = {}
        for o in orders:
            s = o.get('status', 'Неизвестно')
            status_counts[s] = status_counts.get(s, 0) + 1
            
        for s, count in status_counts.items():
            text += f"• {s}: {count}\n"
            
        text += "\n👇 *Примеры (последние 5):*\n"
        # Берем последние 5 для примера
        for o in orders[:5]:
            client_info = o.get('client', {}) or {} # Защита от None
            client_name = client_info.get('full_name', 'Без клиента')
            track = o.get('track_code', 'Нет трека')
            status = o.get('status', 'Нет статуса')
            text += f"- `{track}` ({client_name}) -> {status}\n"
            
        return text

    except Exception as e:
        logger.error(f"Date Search Error: {e}", exc_info=True)
        return f"❌ Ошибка поиска по дате: {str(e)}"

async def prepare_calculation(api_request_func, employee_id: int, company_id: int, client_id: Optional[int], client_search: Optional[str], total_weight: float, party_date: Optional[str] = None, uncalculated_only: Optional[bool] = None, track_codes: Optional[List[str]] = None) -> str:
    """
    Инструмент: Подготовка расчета.
    Версия 2.0: Красивое форматирование чека.
    """
    try:
        client = None
        
        # 1. Находим клиента
        if client_id:
            client_data = await api_request_func("GET", f"/api/clients/{client_id}", params={"company_id": company_id})
            if client_data and "id" in client_data:
                client = client_data
        
        if not client and client_search:
             clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
             if clients:
                 if len(clients) > 1:
                     return json.dumps({"status": "multiple_results", "message": f"Найдено {len(clients)} клиентов. Уточните ID."}, ensure_ascii=False)
                 client = clients[0]

        if not client:
             if not client_id and not client_search:
                  return "❌ Ошибка: ИИ не передал ни ID клиента, ни имя для поиска."
             return f"❌ Клиент не найден (ID: {client_id}, Поиск: '{client_search}')."

        # 2. Ищем заказы
        calc_statuses = ["В обработке", "В пути", "На складе в Китае", "На складе в КР", "Ожидает выкупа", "Готов к выдаче"]
        
        params = {
            "client_id": client['id'], 
            "statuses": calc_statuses, 
            "company_id": company_id,
            "limit": 200
        }
        if party_date: params["party_dates"] = party_date

        orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params=params)
        
        if not orders: 
            return f"❌ У клиента **{client['full_name']}** нет подходящих заказов."

        # --- ФИЛЬТР ПО ТРЕК-КОДАМ ---
        if track_codes and len(track_codes) > 0:
            target_tracks = {t.strip() for t in track_codes}
            filtered_orders = []
            for o in orders:
                if o.get('track_code', '').strip() in target_tracks:
                    filtered_orders.append(o)
            
            if not filtered_orders:
                return f"❌ Ни один из указанных треков не найден у клиента."
            orders = filtered_orders

        # --- ПРЕДОХРАНИТЕЛЬ (SAFETY LOGIC) ---
        uncalculated_orders = []
        calculated_orders = []
        
        for o in orders:
            cost = o.get('calculated_final_cost_som')
            if cost is None or cost == 0:
                uncalculated_orders.append(o)
            else:
                calculated_orders.append(o)
        
        target_orders = []
        
        if uncalculated_only:
            target_orders = uncalculated_orders
            if not target_orders: return "✅ Все заказы уже посчитаны."
        elif len(uncalculated_orders) > 0:
            target_orders = uncalculated_orders
        else:
            target_orders = orders

        if not target_orders:
            return "❌ Нет заказов для обработки."

        # 3. Получаем тарифы
        price_data = await api_request_func("GET", "/api/bot/price", params={"company_id": company_id})
        price = price_data.get("price_usd", 5.5)
        rate = price_data.get("exchange_rate", 89.5)
        
        # 4. Считаем
        count = len(target_orders)
        cost_som = total_weight * price * rate
        
        # --- КРАСИВОЕ ФОРМАТИРОВАНИЕ СПИСКА ---
        tracks_preview = ""
        # Используем enumerate для нумерации (1., 2., 3.)
        for i, o in enumerate(target_orders[:15], 1): 
            # Обработка комментария: если есть, добавляем курсивом через тире
            comment = o.get('comment')
            comment_str = f" — <i>{comment}</i>" if comment else ""
            
            tracks_preview += f"{i}. <code>{o.get('track_code')}</code>{comment_str}\n"
        
        if count > 15:
            tracks_preview += f"<i>... и еще {count - 15} шт.</i>\n"
        
        # Форматирование денег (с пробелами: 4 288 вместо 4288)
        formatted_cost = "{:,.0f}".format(cost_som).replace(",", " ")
        
        # 5. Возвращаем JSON
        return json.dumps({
            "confirm_action": "confirm_calc",
            "client_id": client['id'],
            "client_name": client['full_name'],
            "order_ids": [o['id'] for o in target_orders],
            "count": count,
            "weight": total_weight,
            "price": price,
            "rate": rate,
            "total_sum": round(cost_som, 0),
            "message": (
                f"🧮 <b>РАСЧЕТ И ПРИЕМКА</b>\n"
                f"👤 Клиент: <b>{client['full_name']}</b>\n\n"
                f"👇 <b>Список заказов ({count} шт):</b>\n"
                f"{tracks_preview}"
                f"──────────────\n"
                f"⚖️ Вес: <b>{total_weight} кг</b>\n"
                f"💵 Тариф: {price}$ (курс {rate})\n"
                f"💰 <b>К ОПЛАТЕ: {formatted_cost} сом</b>\n"
                f"──────────────\n\n"
                f"❓ <b>Подтверждаете?</b>\n"
                f"<i>(Статус изменится на 'Готов к выдаче')</i>"
            )
        }, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Calc Error: {e}", exc_info=True)
        return f"❌ Ошибка расчета: {e}"

async def prepare_client_update(api_request_func, employee_id: int, company_id: int, client_search: str, new_phone: str = None, new_code: str = None) -> str:
    """
    Инструмент: Подготовка изменения данных клиента.
    """
    try:
        # 1. Ищем клиента
        clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
        if not clients: return f"❌ Клиент '{client_search}' не найден."
        client = clients[0]
        
        changes_text = ""
        if new_phone: changes_text += f"📱 Телефон: {client.get('phone')} ➡️ **{new_phone}**\n"
        if new_code: changes_text += f"🔢 Код номера: {client.get('client_code_num')} ➡️ **{new_code}**\n"
        
        if not changes_text: return "⚠️ Вы не указали, что менять (телефон или код)."
        
        return json.dumps({
            "confirm_action": "confirm_client_edit",
            "client_id": client['id'],
            "new_phone": new_phone,
            "new_code": new_code,
            "message": (
                f"📝 **РЕДАКТИРОВАНИЕ КЛИЕНТА**\n"
                f"👤 {client['full_name']}\n\n"
                f"{changes_text}\n"
                f"❓ **Сохранить изменения?**"
            )
        }, ensure_ascii=False)
    except Exception as e:
        return f"❌ Ошибка подготовки обновления: {e}"
    
async def bulk_update_client_orders(api_request_func, employee_id, company_id, client_id, old_status, new_status):
    """
    Инструмент: Массовая смена статуса.
    ЕСЛИ old_status не указан (None) -> Берет ВСЕ активные заказы.
    """
    try:
        # Формируем параметры запроса
        params = {
            "client_id": client_id, 
            "company_id": company_id,
            "limit": 1000
        }

        # --- УМНАЯ ФИЛЬТРАЦИЯ ---
        status_label = old_status
        
        # Если статус не указан или явно сказано "Все" -> берем все активные
        if not old_status or str(old_status).lower() in ['none', 'null', 'все', 'all', 'any']:
            # Берем все статусы, кроме "Выдан" (их обычно не трогают массово)
            params["statuses"] = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
            status_label = "ЛЮБОЙ АКТИВНЫЙ"
        else:
            # Если статус указан конкретно
            params["statuses"] = [old_status]

        # Запрашиваем заказы
        orders = await api_request_func("GET", "/api/orders", params=params)
        
        if not orders or (isinstance(orders, dict) and "error" in orders):
            return f"❌ У этого клиента нет заказов подходящих под критерий: '{status_label}'."
            
        order_ids = [o['id'] for o in orders]
        count = len(order_ids)
        
        if count == 0:
            return f"❌ Нет заказов для обновления."

        # 2. Возвращаем JSON для кнопки подтверждения
        return json.dumps({
            "confirm_action": "bulk_status_manual", 
            "ids": order_ids,
            "new_status": new_status,
            "count": count,
            "message": f"❓ Перевести **{count} заказов** клиента (Статус: {status_label}) в **'{new_status}'**?"
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка: {e}"
    
async def get_all_party_dates(api_request_func, company_id: int) -> str:
    """
    Инструмент: Получает список всех активных партий (дат).
    """
    try:
        parties = await api_request_func("GET", "/api/orders/parties", params={"company_id": company_id})
        
        if not parties or not isinstance(parties, list):
            return "📅 Активных партий не найдено."
            
        text = "📅 **Список всех партий:**\n"
        for p in parties:
            text += f"- {p}\n"
        return text
    except Exception as e:
        return f"❌ Ошибка получения партий: {e}"
    
async def undo_last_operation(api_request_func, operation_id: int) -> str:
    """Инструмент: Отмена массовой операции по ID."""
    response = await api_request_func("POST", f"/api/orders/undo/{operation_id}")
    
    if isinstance(response, dict) and "error" in response:
        return f"❌ Ошибка отмены: {response['error']}"
    
    return f"✅ Готово! {response.get('message')}"
