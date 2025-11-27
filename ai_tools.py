# ai_tools.py (Версия 4.0 - Старый промпт удален)

import json
import logging
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# =================================================================
# --- НОВЫЕ АСИНХРОННЫЕ ФУНКЦИИ-ИНСТРУМЕНТЫ ДЛЯ ИИ ---
# Все функции принимают api_request_func (асинхронный клиент) и данные сессии
# =================================================================

async def get_user_orders_json(api_request_func, client_id: int, company_id: int, status_filter: Optional[List[str]] = None, uncalculated_only: bool = False) -> str:
    """
    Умный инструмент: Возвращает список заказов + ИМЯ КЛИЕНТА для контекста.
    """
    # 1. Получаем имя клиента (чтобы ИИ помнил, с кем работает)
    client_info_str = f"ID {client_id}"
    try:
        client_data = await api_request_func("GET", f"/api/clients/{client_id}", params={"company_id": company_id})
        if client_data and "full_name" in client_data:
            code = f"{client_data.get('client_code_prefix', '')}{client_data.get('client_code_num', '')}"
            client_info_str = f"{client_data['full_name']} ({code}, ID {client_id})"
    except:
        pass

    # 2. Параметры поиска
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
    if uncalculated_only:
        params["uncalculated_only"] = True

    # 3. Запрос заказов
    orders = await api_request_func("GET", "/api/orders", params=params)

    if not orders or (isinstance(orders, dict) and "error" in orders):
        return json.dumps({"message": f"📭 У клиента {client_info_str} нет таких заказов."}, ensure_ascii=False)
    
    if not isinstance(orders, list):
        return json.dumps({"error": "Ошибка формата данных."}, ensure_ascii=False)

    # --- РЕЖИМ 1: ДЕТАЛЬНЫЙ СПИСОК ---
    if status_filter or uncalculated_only:
        formatted_orders = []
        total_weight = 0.0
        total_cost = 0.0
        count_calculated = 0

        for o in orders:
            history_entries = []
            if o.get('history_entries'):
                # --- НОВАЯ ЛОГИКА: Оставляем только ПОСЛЕДНЮЮ дату для каждого статуса ---
                # Используем словарь, где ключ = статус. Новые записи перезапишут старые.
                # Важно: предполагаем, что history_entries приходят отсортированными по дате (от старых к новым)
                # Если нет, можно отсортировать: sorted(o['history_entries'], key=lambda x: x['created_at'])
                
                status_map = {}
                raw_history = o['history_entries']
                # Сортируем по дате (на всякий случай)
                raw_history.sort(key=lambda x: x.get('created_at', '')) 
                
                for entry in raw_history:
                    st = entry.get('status')
                    # Записываем/Перезаписываем, чтобы осталась самая свежая дата для этого статуса
                    status_map[st] = entry.get('created_at')
                
                # Превращаем обратно в список, сортируя по дате
                # (чтобы хронология была правильной: Обработка -> Китай -> Путь -> КР)
                unique_history = []
                for st, dt in status_map.items():
                    unique_history.append({"status": st, "date": dt})
                
                # Сортируем итоговый список по дате
                unique_history.sort(key=lambda x: x['date'])
                
                history_entries = unique_history
                # --- КОНЕЦ ФИЛЬТРАЦИИ ---
            
            order_data = {
                "трек": o.get('track_code'),
                "статус": o.get('status'),
                "комментарий": o.get('comment'),
                "партия": o.get('party_date'), 
                "history_entries": history_entries 
            }

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
        
        response_json = {
            "client_info": client_info_str, # <--- ВАЖНО: Передаем имя
            "active_orders": formatted_orders
        }
        
        if count_calculated > 0:
            summary_text = (
                f"\n💰 <b>ИТОГО ПО СПИСКУ ({count_calculated} шт):</b>\n"
                f"⚖️ Общий вес: <b>{total_weight:.2f} кг</b>\n"
                f"💵 Общая сумма: <b>{total_cost:.2f} сом</b>"
            )
            response_json["summary_footer"] = summary_text

        return json.dumps(response_json, ensure_ascii=False)

    # --- РЕЖИМ 2: СВОДКА ---
    else:
        all_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]
        all_orders = await api_request_func("GET", "/api/orders", params={"client_id": client_id, "company_id": company_id, "statuses": all_statuses, "limit": 200})
        
        if not all_orders: return json.dumps({"message": f"📭 История заказов клиента {client_info_str} пуста."}, ensure_ascii=False)
        
        stats = {}
        parties = set()
        transit_statuses = ["В пути", "На складе в Китае", "На складе в КР"]
        total_count = len(all_orders)

        for o in all_orders:
            status = o.get('status', 'Неизвестно')
            stats[status] = stats.get(status, 0) + 1
            if status in transit_statuses and o.get('party_date'):
                parties.add(o.get('party_date'))

        summary_msg = f"📊 **Сводка по клиенту {client_info_str} (Всего: {total_count} шт):**\n\n"
        priority_order = ["Готов к выдаче", "На складе в КР", "В пути", "На складе в Китае", "Выкуплен", "Ожидает выкупа", "В обработке", "Выдан"]
        
        for st in priority_order:
            if stats.get(st, 0) > 0:
                icon = "✅" if st == "Готов к выдаче" else "🚚" if st == "В пути" else "📦"
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

async def search_deletion_history(api_request_func, company_id: int, employee_id: int, query: str = None, date_from: str = None, date_to: str = None) -> str:
    """
    ДЕТЕКТИВ 2.0: Ищет по слову И/ИЛИ по дате.
    """
    try:
        if not employee_id:
            return "❌ Ошибка: Нет прав Владельца."

        params = {"company_id": company_id}
        if query and query.lower() not in ['сегодня', 'вчера', 'завтра']: # Игнорируем слова-паразиты в поиске
            params["q"] = query
        if date_from: params["start_date"] = date_from
        if date_to: params["end_date"] = date_to

        # Запрос к API
        logs = await api_request_func(
            "GET", 
            "/api/audit/search", 
            params=params,
            employee_id=employee_id
        )
        
        # Обработка ошибок и пустоты
        if not logs or (isinstance(logs, dict) and "error" in logs):
            if not logs: return "🕵️‍♂️ По вашему запросу записей не найдено."
            return f"⚠️ Ошибка поиска: {logs.get('error')}"

        # Формирование ответа
        period_info = ""
        if date_from and date_to: period_info = f" (📅 {date_from} — {date_to})"
        elif date_from: period_info = f" (📅 c {date_from})"
        
        text = f"🕵️‍♂️ **ОТЧЕТ ДЕТЕКТИВА{period_info}**\nНайдено записей: {len(logs)}\n\n"
        
        for log in logs:
            try:
                # Конвертация UTC -> Бишкек (+6)
                raw_date = log.get('created_at', '')
                if raw_date:
                    dt_utc = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                    bishkek_tz = timezone(timedelta(hours=6))
                    dt_bishkek = dt_utc.astimezone(bishkek_tz)
                    date_str = dt_bishkek.strftime('%Y-%m-%d %H:%M')
                else:
                    date_str = "??"
            except:
                date_str = "??"
            
            text += (
                f"📅 <b>{date_str}</b>\n"
                f"👤 Кто: <b>{log.get('who_did_it')}</b>\n"
                f"📝 Что:\n{log.get('description')}\n"
                f"----------------\n"
            )
            
        return text

    except Exception as e:
        return f"❌ Ошибка расследования: {e}"

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
    (ВЕРСИЯ 2.0 - Разрешены массовые действия для РАЗНЫХ клиентов)
    """
    try:
        # 1. Нормализуем треки
        if isinstance(track_codes, str): 
            track_codes = [t.strip() for t in track_codes.split(',')]
            
        clean_tracks = [t.strip() for t in track_codes if t.strip()]
        if not clean_tracks: return "❌ Нет трек-кодов."

        found_ids = []
        clients_found = set()
        client_names = []
        found_tracks_str = []
        
        # 2. Ищем каждый заказ
        for track in clean_tracks:
            # Ищем заказ (limit=1, так как трек уникален в рамках компании)
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": track, "company_id": company_id, "limit": 1})
            if orders:
                order = orders[0]
                found_ids.append(order['id'])
                found_tracks_str.append(order['track_code'])
                
                if order.get('client'):
                    c = order['client']
                    c_id = c['id']
                    if c_id not in clients_found:
                        clients_found.add(c_id)
                        # Формируем краткое имя для списка
                        client_names.append(f"{c['full_name']}")
                else:
                    if "unclaimed" not in clients_found:
                        clients_found.add("unclaimed")
                        client_names.append("Невостребованный")

        if not found_ids:
            return f"❌ Ни один из трек-кодов {clean_tracks} не найден в базе."

        # 3. Формирование описания владельцев (Убрана блокировка "каши")
        count = len(found_ids)
        unique_clients_count = len(clients_found)

        if unique_clients_count > 1:
            # Если клиентов много, показываем сводку
            # Пример: "Алимбек, Мажид и др."
            names_display = ", ".join(client_names[:2])
            if len(client_names) > 2:
                names_display += f" и еще {len(client_names) - 2}"
            
            owner_str = f"⚠️ <b>РАЗНЫЕ КЛИЕНТЫ ({unique_clients_count}):</b>\n({names_display})"
        else:
            # Если клиент один
            owner_str = f"👤 Владелец: <b>{client_names[0] if client_names else 'Неизвестно'}</b>"

        # 4. Возвращаем JSON для кнопки подтверждения
        return json.dumps({
            "confirm_action": "bulk_status_manual", # Тип действия для бота
            "ids": found_ids,       # Передаем ВСЕ найденные ID скопом
            "new_status": new_status,
            "count": count,
            "message": (
                f"🔄 <b>МАССОВАЯ СМЕНА СТАТУСА</b>\n"
                f"📦 Заказов найдено: <b>{count}</b>\n"
                f"{owner_str}\n"
                f"📝 Новый статус: <b>'{new_status}'</b>\n\n"
                f"❓ Подтверждаете изменение для ВСЕХ этих заказов?"
            )
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка поиска: {e}"
    
async def get_client_debt_report(api_request_func, employee_id: int, company_id: int, client_search: str) -> str:
    """
    Инструмент: Детальный отчет по долгу КОНКРЕТНОГО клиента.
    """
    try:
        if not client_search: return "⚠️ Укажите имя клиента."

        # 1. Ищем клиента
        clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
        
        if not clients: return f"❌ Клиент '{client_search}' не найден."
        
        # Ловушка дубликатов
        if len(clients) > 1:
             options = [f"{c['full_name']} ({c['phone']})" for c in clients]
             return json.dumps({
                 "status": "multiple_results",
                 "message": f"⚠️ Найдено {len(clients)} клиентов. Уточните, кто именно?",
                 "options": options
             }, ensure_ascii=False)

        client = clients[0]
        client_id = client['id']

        # 2. Получаем транзакции для расчета баланса и поиска последнего платежа
        trx = await api_request_func("GET", f"/api/clients/{client_id}/transactions", employee_id=employee_id, params={"company_id": company_id})
        
        if not trx or not isinstance(trx, list):
            return f"✅ У клиента <b>{client['full_name']}</b> нет истории операций (Баланс: 0 с.)"

        # Считаем баланс вручную (сумма всех amount)
        balance = sum(t.get('amount', 0) for t in trx)
        
        # Ищем последний платеж
        payment = next((t for t in trx if t.get('transaction_type') == 'payment'), None)
        last_payment_info = "Нет платежей"
        
        if payment:
            raw_date = payment.get('created_at')
            amt = payment.get('amount', 0)
            if raw_date:
                dt = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                bishkek_tz = timezone(timedelta(hours=6))
                date_str = dt.astimezone(bishkek_tz).strftime('%d.%m.%Y')
                last_payment_info = f"{amt:.0f} с. ({date_str})"

        # Формируем ответ
        icon = "🔴" if balance < -1 else "🟢"
        status = "Должен нам" if balance < -1 else "В расчете / Переплата" if balance >= 0 else "Баланс ок"
        
        text = (
            f"👤 <b>Отчет по клиенту: {client['full_name']}</b>\n"
            f"📞 {client['phone']}\n"
            f"──────────────────\n"
            f"{icon} <b>Баланс: {balance:.0f} с.</b> ({status})\n\n"
            f"🗓 Последний платеж: <b>{last_payment_info}</b>\n"
            f"📊 Всего операций: {len(trx)}"
        )
        
        return text

    except Exception as e:
        return f"❌ Ошибка: {e}"

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
    
    elif tool == "bulk_assign_by_tracks":
            tracks = tool_command.get("track_codes")
            client_search = tool_command.get("client_search")
            status = tool_command.get("status", "В пути") 
            
            # Вес
            raw_weight = tool_command.get("weight", 0)
            if isinstance(raw_weight, str): raw_weight = raw_weight.replace(',', '.')
            try: weight = float(raw_weight)
            except: weight = 0.0
            
            # --- НОВОЕ: Читаем цену и курс от ИИ ---
            raw_price = tool_command.get("price", 0)
            if isinstance(raw_price, str): raw_price = raw_price.replace(',', '.')
            try: custom_price = float(raw_price)
            except: custom_price = 0.0
            
            raw_rate = tool_command.get("rate", 0)
            if isinstance(raw_rate, str): raw_rate = raw_rate.replace(',', '.')
            try: custom_rate = float(raw_rate)
            except: custom_rate = 0.0
            # ---------------------------------------

            if isinstance(tracks, str): tracks = [t.strip() for t in tracks.split(',')]
            
            return await bulk_assign_by_tracks(
                api_request_func, employee_id, company_id, tracks, client_search, 
                status, weight, custom_price, custom_rate
            )
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
            
            party_date = tool_command.get("party_date")
            uncalculated_only = tool_command.get("uncalculated_only")
            track_codes = tool_command.get("track_codes")
            if isinstance(track_codes, str):
                track_codes = [t.strip() for t in track_codes.split(',')]

            # --- НОВОЕ ПОЛЕ ---
            target_status = tool_command.get("target_status")
            
            return await prepare_calculation(
                api_request_func, 
                employee_id, 
                company_id, 
                client_id=client_id, 
                client_search=client_search, 
                total_weight=total_weight,
                party_date=party_date,
                uncalculated_only=uncalculated_only,
                track_codes=track_codes,
                target_status=target_status # <-- Передаем в функцию
            )

    elif tool == "update_client_data":
            client_search = tool_command.get("client_search")
            new_phone = tool_command.get("new_phone")
            new_code = tool_command.get("new_code")
            new_name = tool_command.get("new_name")     # <-- Добавлено
            new_prefix = tool_command.get("new_prefix") # <-- Добавлено
            
            # Преобразуем код в число, если передан
            if new_code and str(new_code).isdigit(): new_code = int(new_code)
            
            return await prepare_client_update(
                api_request_func, 
                employee_id, 
                company_id, 
                client_search, 
                new_phone, 
                new_code,
                new_name,   # <-- Передаем
                new_prefix  # <-- Передаем
            )
    
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

        elif tool == "prepare_add_expense":
            amount = tool_command.get("amount")
            reason = tool_command.get("reason")
            cat = tool_command.get("category")
            src = tool_command.get("source", "shift")
            loc_search = tool_command.get("location_search") # <-- Достаем параметр
        
            return await prepare_add_expense(
                api_request_func, 
                employee_id, 
                company_id, 
                float(amount), 
                reason, 
                cat, 
                src,
                loc_search # <-- Передаем
            )
        
        elif tool == "get_shift_summary":
            loc_id = tool_command.get("location_id") # Опционально
            return await get_shift_summary(api_request_func, employee_id, company_id, loc_id)

        elif tool == "request_broadcast_photo":
           text = tool_command.get("text")
           if not text: return "❌ Ошибка: Текст рассылки пустой."
           return await request_broadcast_photo(api_request_func, text)

        elif tool == "get_summary_by_date":
            start = tool_command.get("start_date")
            end = tool_command.get("end_date")
            loc_search = tool_command.get("location_search")
        
            if not start or not end:
                return "❌ Ошибка ИИ: Не указаны даты начала или конца периода."
            
            return await get_summary_report_by_range(api_request_func, employee_id, company_id, start, end, loc_search)
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
        
        elif tool == "search_deletion_history":
            q = tool_command.get("query")
            d_from = tool_command.get("date_from")
            d_to = tool_command.get("date_to")
            
            # Разрешаем поиск, если есть ХОТЯ БЫ ОДИН параметр
            if not q and not d_from: 
                 return "❌ Ошибка ИИ: Не заданы критерии поиска (ни текста, ни даты)."
            
            return await search_deletion_history(api_request_func, company_id, employee_id, query=q, date_from=d_from, date_to=d_to)
            
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
        
        # === БЛОК 6: ДОЛГИ ===
        elif tool == "get_debtors_list":
            return await get_debtors_list(api_request_func, employee_id, company_id)
        
        # --- ДОБАВИТЬ ЭТОТ БЛОК ---
        elif tool == "get_client_debt_report":
            search = tool_command.get("client_search")
            return await get_client_debt_report(api_request_func, employee_id, company_id, search)
        # --------------------------

        elif tool == "prepare_repay_debt":
            client_search = tool_command.get("client_search")
            amount = tool_command.get("amount")
            link_to_shift = tool_command.get("link_to_shift", True) # По умолчанию True
            
            if not amount: return "❌ Ошибка: Не указана сумма."
            
            # Защита от строк в сумме
            try: amount = float(amount)
            except: return "❌ Ошибка: Сумма должна быть числом."

            return await prepare_repay_debt(api_request_func, employee_id, company_id, client_search, amount, link_to_shift)
            
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

async def prepare_calculation(api_request_func, employee_id: int, company_id: int, client_id: Optional[int], client_search: Optional[str], total_weight: float, party_date: Optional[str] = None, uncalculated_only: Optional[bool] = None, track_codes: Optional[List[str]] = None, target_status: str = None) -> str:
    """
    Инструмент: Подготовка расчета.
    ВЕРСИЯ 3.0: Поддержка фильтрации по текущему статусу (target_status).
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
        # --- НОВАЯ ЛОГИКА: Фильтр по статусу ---
        if target_status:
            # Если ИИ сказал "те что в пути", ищем только их
            calc_statuses = [target_status]
        else:
            # Иначе берем стандартный набор (исключая уже выданные)
            calc_statuses = ["В обработке", "В пути", "На складе в Китае", "На складе в КР", "Ожидает выкупа"]
        # ---------------------------------------
        
        params = {
            "client_id": client['id'], 
            "statuses": calc_statuses, 
            "company_id": company_id,
            "limit": 200
        }
        if party_date: params["party_dates"] = party_date

        orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params=params)
        
        if not orders: 
            status_msg = f"со статусом '{target_status}'" if target_status else "для расчета"
            return f"❌ У клиента **{client['full_name']}** нет заказов {status_msg}."

        # --- ФИЛЬТР ПО ТРЕК-КОДАМ (если переданы явно) ---
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
        
        for o in orders:
            cost = o.get('calculated_final_cost_som')
            if cost is None or cost == 0:
                uncalculated_orders.append(o)
        
        target_orders = []
        
        if uncalculated_only:
            target_orders = uncalculated_orders
            if not target_orders: return "✅ Все подходящие заказы уже посчитаны."
        elif len(uncalculated_orders) > 0:
            # Если есть непосчитанные, берем только их (по умолчанию)
            target_orders = uncalculated_orders
        else:
            # Если все посчитаны, но мы все равно вызвали функцию — берем все (пересчет)
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
        for i, o in enumerate(target_orders[:15], 1): 
            comment = o.get('comment')
            comment_str = f" — <i>{comment}</i>" if comment else ""
            status_icon = "🚚" if o.get('status') == "В пути" else "📦"
            tracks_preview += f"{i}. {status_icon} <code>{o.get('track_code')}</code>{comment_str}\n"
        
        if count > 15:
            tracks_preview += f"<i>... и еще {count - 15} шт.</i>\n"
        
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
        return f"❌ Ошибка расчета: {e}"

async def prepare_client_update(api_request_func, employee_id: int, company_id: int, client_search: str, new_phone: str = None, new_code: str = None, new_name: str = None, new_prefix: str = None) -> str:
    """
    Инструмент: Подготовка изменения данных клиента (ФИО, Телефон, Код, Префикс).
    """
    try:
        # --- ЗАЩИТА ОТ ПУСТОГО ПОИСКА ---
        if not client_search or not str(client_search).strip():
            return "⚠️ Ошибка: Вы не указали, кого редактировать (имя или телефон). Пожалуйста, повторите команду, указав имя клиента."
        # -------------------------------

        # 1. Ищем клиента
        clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
        
        # Проверка на ошибку от сервера
        if isinstance(clients, dict) and "error" in clients:
            return f"❌ Ошибка поиска: {clients.get('error')}"
            
        if not isinstance(clients, list):
             return f"❌ Ошибка: Сервер вернул некорректные данные (не список)."

        if not clients: 
            return f"❌ Клиент по запросу '{client_search}' не найден. Уточните ФИО или телефон."
        
        if len(clients) > 1:
             # Если нашли несколько, возвращаем список для уточнения
             options = []
             for c in clients:
                 code = f"{c.get('client_code_prefix')}{c.get('client_code_num')}"
                 options.append(f"ID {c['id']}: {c['full_name']} ({code})")
             return json.dumps({
                 "status": "multiple_results",
                 "message": f"⚠️ Найдено {len(clients)} клиентов. Уточните ID или точное имя:",
                 "options": options
             }, ensure_ascii=False)

        client = clients[0]
        
        # 2. Формируем список изменений
        changes_text = ""
        payload_data = {}

        # Смена ФИО
        if new_name and new_name != client.get('full_name'):
            changes_text += f"👤 ФИО: {client.get('full_name')} ➡️ <b>{new_name}</b>\n"
            payload_data['new_name'] = new_name

        # Смена Телефона
        if new_phone and new_phone != client.get('phone'):
            changes_text += f"📱 Телефон: {client.get('phone')} ➡️ <b>{new_phone}</b>\n"
            payload_data['new_phone'] = new_phone

        # Смена Кода (Цифры)
        if new_code:
            old_code_num = str(client.get('client_code_num')) if client.get('client_code_num') else ""
            if str(new_code) != old_code_num:
                changes_text += f"🔢 Номер кода: {old_code_num} ➡️ <b>{new_code}</b>\n"
                payload_data['new_code'] = int(new_code)

        # Смена Префикса
        if new_prefix:
            new_prefix = new_prefix.upper()
            if new_prefix != client.get('client_code_prefix'):
                changes_text += f"🔤 Префикс: {client.get('client_code_prefix')} ➡️ <b>{new_prefix}</b>\n"
                payload_data['new_prefix'] = new_prefix
        
        if not changes_text: 
            return "⚠️ Вы не указали никаких новых данных, отличающихся от текущих."
        
        # 3. Возвращаем JSON для кнопки подтверждения
        payload_data['client_id'] = client['id']
        
        return json.dumps({
            "confirm_action": "confirm_client_edit",
            "payload": payload_data,
            "message": (
                f"📝 **РЕДАКТИРОВАНИЕ КЛИЕНТА**\n"
                f"Клиент: {client['full_name']} (ID {client['id']})\n"
                f"--------------------------\n"
                f"{changes_text}\n"
                f"❓ **Подтверждаете изменения?**"
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

async def get_expense_types_list(api_request_func, company_id: int, employee_id: int) -> dict:
    """Вспомогательная функция: Получает словарь {имя_типа: id_типа}."""
    # ВАЖНО: Передаем employee_id, так как эндпоинт защищен
    types = await api_request_func("GET", "/api/expense_types", employee_id=employee_id, params={"company_id": company_id})
    
    if not types or not isinstance(types, list):
        return {}
    # Создаем словарь для нечеткого поиска: {"хознужды": 1, "питание": 2, ...}
    return {t['name'].lower(): t['id'] for t in types}

async def prepare_add_expense(api_request_func, employee_id: int, company_id: int, amount: float, reason: str, category_name: str = None, source: str = "shift", location_search: str = None) -> str:
    """
    Инструмент: Подготовка расхода. 
    Умный поиск категории (нечеткое совпадение) и филиала.
    """
    try:
        # 1. Получаем список категорий из БД
        types_map = await get_expense_types_list(api_request_func, company_id, employee_id) # { "имя": id }
        
        if not types_map:
             return "❌ Ошибка: В системе нет ни одной категории расходов. Создайте их в Админ-панели."

        expense_type_id = None
        expense_type_name = None
        
        # А. Если ИИ передал категорию (например "Хоз нужды")
        if category_name:
            cat_lower = category_name.lower().strip()
            
            # 1. Точное совпадение
            if cat_lower in types_map:
                expense_type_id = types_map[cat_lower]
                expense_type_name = category_name.capitalize()
            else:
                # 2. Частичное совпадение (если "Хоз нужды" содержится в "хоз. нужды")
                for db_name, db_id in types_map.items():
                    # Очищаем от точек и пробелов для сравнения
                    clean_db = db_name.replace('.', '').replace(' ', '')
                    clean_cat = cat_lower.replace('.', '').replace(' ', '')
                    
                    if clean_cat in clean_db or clean_db in clean_cat:
                        expense_type_id = db_id
                        expense_type_name = db_name.capitalize() # Берем правильное имя из БД
                        break

        # Б. Если по имени не нашли, ищем ключевые слова в ПРИЧИНЕ
        if not expense_type_id:
            reason_lower = reason.lower()
            for db_name, db_id in types_map.items():
                # Убираем точки для лучшего поиска ("хоз. нужды" -> "хоз нужды")
                clean_db_name = db_name.replace('.', ' ')
                # Разбиваем на слова (чтобы "нужды" нашлось в "хоз. нужды")
                db_words = clean_db_name.split()
                
                # Если любое значимое слово из категории есть в причине
                for word in db_words:
                    if len(word) > 2 and word in reason_lower:
                        expense_type_id = db_id
                        expense_type_name = db_name.capitalize()
                        break
                if expense_type_id: break
        
        # В. ФИНАЛЬНЫЙ ФОЛЛБЭК
        if not expense_type_id:
            # Ищем "Прочее" или "Хоз" как дефолт (для лампочек это логичнее чем Аванс)
            priority_keys = ["хоз", "прочее", "разное", "расходы"]
            for key in priority_keys:
                for db_name, db_id in types_map.items():
                    if key in db_name:
                        expense_type_id = db_id
                        expense_type_name = db_name.capitalize()
                        break
                if expense_type_id: break
            
            # Если совсем ничего не нашли, берем первую
            if not expense_type_id and types_map:
                first_key = list(types_map.keys())[0]
                expense_type_id = types_map[first_key]
                expense_type_name = first_key.capitalize()

        # Логика Филиалов (без изменений)
        target_location_id = None
        target_location_name = "Текущий"
        locations = await api_request_func("GET", "/api/bot/locations", params={"company_id": company_id})
        
        if location_search:
            search_lower = location_search.lower()
            found_loc = None
            for loc in locations:
                if search_lower in loc['name'].lower():
                    found_loc = loc
                    break
            if found_loc:
                target_location_id = found_loc['id']
                target_location_name = found_loc['name']
        elif len(locations) > 1 and source == 'shift':
             return json.dumps({
                 "status": "multiple_results",
                 "message": f"🏢 У вас {len(locations)} филиалов. Уточните филиал?",
                 "options": [l['name'] for l in locations]
             }, ensure_ascii=False)
        elif len(locations) == 1:
            target_location_id = locations[0]['id']

        source_text = f"💵 Из КАССЫ ({target_location_name})" if source == 'shift' else "💼 ЛИЧНЫЕ / Сейф"
        
        return json.dumps({
            "confirm_action": "add_expense",
            "amount": amount,
            "reason": reason,
            "expense_type_id": expense_type_id,
            "expense_type_name": expense_type_name, # Важно вернуть имя из БД
            "source": source,
            "location_id": target_location_id,
            "message": (
                f"💸 **ЗАПИСЬ РАСХОДА**\n"
                f"💰 Сумма: **{amount} сом**\n"
                f"📂 Категория: **{expense_type_name}**\n"
                f"📝 Причина: {reason}\n"
                f"📍 Источник: {source_text}\n\n"
                f"❓ Подтверждаете?"
            )
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка подготовки расхода: {e}"

async def get_shift_summary(api_request_func, employee_id: int, company_id: int, location_id: int = None) -> str:
    """
    Инструмент: Получает ДЕТАЛЬНУЮ сводку по текущей смене.
    """
    try:
        url = "/api/reports/shift/current"
        if location_id:
             url = f"/api/reports/shift/location/{location_id}"
             
        report = await api_request_func("GET", url, employee_id=employee_id, params={"company_id": company_id})
        
        if not report or "error" in report:
            err = report.get('error', '') if isinstance(report, dict) else ''
            if "не найдена" in str(err) or "not found" in str(err):
                return "📴 **Смена сейчас закрыта.**\nВ кассе 0.00 сом."
            return f"❌ Ошибка получения отчета: {err}"

        # Формируем детализированный текст
        text = (
            f"📊 **СВОДКА ПО СМЕНЕ**\n"
            f"📍 Филиал: {report.get('location_name')}\n"
            f"👤 Сотрудник: {report.get('employee_name')}\n"
            f"🕐 Открыта: {report.get('shift_start_time', '')[:16].replace('T', ' ')}\n"
            f"──────────────────\n"
            f"💰 **В КАССЕ: {report.get('calculated_cash', 0):.2f} сом**\n"
            f"──────────────────\n"
            f"📥 **ОБЩИЙ ПРИХОД:**\n"
            f"   💵 Нал: {report.get('cash_income', 0):.2f} с.\n"
            f"      ├ 📦 Заказы: {report.get('cash_from_orders', 0):.2f}\n"
            f"      └ 💸 Долги: {report.get('cash_from_debts', 0):.2f}\n"
            f"   💳 Карта: {report.get('card_income', 0):.2f} с.\n"
            f"      ├ 📦 Заказы: {report.get('card_from_orders', 0):.2f}\n"
            f"      └ 💸 Долги: {report.get('card_from_debts', 0):.2f}\n"
            f"──────────────────\n"
            f"📤 **РАСХОДЫ:**\n"
            f"   🔻 Операционные: -{report.get('total_expenses', 0):.2f}\n"
            f"   ↩️ Возвраты: -{report.get('total_returns', 0):.2f}\n"
        )
        return text

    except Exception as e:
        return f"❌ Ошибка сводки: {e}"
    
async def get_summary_report_by_range(api_request_func, employee_id: int, company_id: int, start_date: str, end_date: str, location_search: str = None) -> str:
    """
    Инструмент: Получает сводный отчет (Приход, Расход, Прибыль) за указанный период.
    """
    try:
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "company_id": company_id
        }

        # Логика поиска филиала (если указан)
        if location_search:
            locations = await api_request_func("GET", "/api/bot/locations", params={"company_id": company_id})
            if locations:
                search_lower = location_search.lower()
                for loc in locations:
                    if search_lower in loc['name'].lower():
                        params["location_id"] = loc['id']
                        break
        
        # Запрос к API
        report = await api_request_func("GET", "/api/reports/summary", employee_id=employee_id, params=params)
        
        if not report or "error" in report:
            err = report.get('error', '') if isinstance(report, dict) else ''
            return f"❌ Не удалось получить отчет: {err}"
        
        s = report.get('summary', {})
        
        # Формирование красивого ответа
        loc_text = f" (Филиал: {s.get('location_id_filter')})" if s.get('location_id_filter') else ""
        
        text = (
            f"📊 **ОТЧЕТ ЗА ПЕРИОД**\n"
            f"📅 {start_date} — {end_date}{loc_text}\n"
            f"──────────────────\n"
            f"💰 **ВЫРУЧКА: {s.get('total_income', 0):,.2f} сом**\n"
            f"   ├ 💵 Нал: {s.get('total_cash_income', 0):,.2f}\n"
            f"   └ 💳 Карта: {s.get('total_card_income', 0):,.2f}\n"
            f"──────────────────\n"
            f"📉 **РАСХОДЫ: {s.get('total_expenses', 0):,.2f} сом**\n"
        )
        
        # Детализация расходов
        if s.get('expenses_by_type'):
            for type_name, amount in s['expenses_by_type'].items():
                 text += f"   • {type_name}: {amount:,.2f}\n"
        
        text += f"──────────────────\n"
        profit = s.get('net_profit', 0)
        profit_icon = "📈" if profit >= 0 else "📉"
        text += f"{profit_icon} **ЧИСТАЯ ПРИБЫЛЬ: {profit:,.2f} сом**"
        
        return text

    except Exception as e:
        return f"❌ Ошибка формирования отчета: {e}"
    
async def request_broadcast_photo(api_request_func, text: str) -> str:
    """
    Инструмент: Сохраняет черновик и просит фото/подтверждение.
    """
    return json.dumps({
        "status": "waiting_for_broadcast_photo",
        "draft_text": text,
        "message": (
            f"📝 **Черновик объявления:**\n\n"
            f"{text}\n\n"
            f"➖➖➖➖➖➖➖\n"
            f"📸 **Если текст устраивает** — отправьте фото (или напишите 'без фото').\n"
            f"✏️ **Если нужно исправить** — просто напишите, что поменять."
        )
    }, ensure_ascii=False)

async def get_debtors_list(api_request_func, employee_id: int, company_id: int) -> str:
    """
    Инструмент: Получает список должников в новом формате.
    """
    try:
        # 1. Запрашиваем список должников
        debtors = await api_request_func("GET", "/api/debtors", employee_id=employee_id, params={"company_id": company_id})
        
        if not debtors or (isinstance(debtors, dict) and "error" in debtors):
            return "✅ Должников нет! Все чисто."
            
        if not isinstance(debtors, list):
            return "❌ Ошибка формата данных."

        # 2. Сортировка (Старые даты сверху)
        def get_sort_key(d):
            date_str = d.get('last_transaction_date')
            if not date_str: return "0000-00-00"
            return date_str
            
        debtors.sort(key=get_sort_key)
        
        # Берем топ-10, чтобы не спамить (и не делать 100 запросов к API)
        top_debtors = debtors[:10]

        text = "📊 **Отчёт: задолженности клиентов**\n"
        text += "(сортировка: по длительности отсутствия активности)\n\n"

        for i, d in enumerate(top_debtors, 1):
            client = d.get('client', {})
            client_id = client.get('id')
            name = client.get('full_name', 'Неизвестный')
            phone = client.get('phone', 'Не указан')
            current_debt = d.get('balance', 0)
            
            # 3. ДОПОЛНИТЕЛЬНЫЙ ЗАПРОС: Ищем последний ПЛАТЕЖ для каждого должника
            last_payment_date = "Нет платежей"
            last_payment_amount = "0"
            
            try:
                # Получаем транзакции клиента
                trx = await api_request_func("GET", f"/api/clients/{client_id}/transactions", employee_id=employee_id, params={"company_id": company_id})
                if trx and isinstance(trx, list):
                    # Ищем первую транзакцию типа 'payment' (они уже отсортированы по дате desc)
                    payment = next((t for t in trx if t.get('transaction_type') == 'payment'), None)
                    if payment:
                        # Форматируем дату
                        raw_date = payment.get('created_at')
                        if raw_date:
                            dt = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                            # Добавляем 6 часов для Бишкека
                            bishkek_tz = timezone(timedelta(hours=6))
                            last_payment_date = dt.astimezone(bishkek_tz).strftime('%d.%m.%Y')
                        
                        last_payment_amount = f"{payment.get('amount', 0):.0f}"
            except:
                pass # Если ошибка получения транзакций, оставляем "Нет платежей"

            # 4. Формируем красивый блок
            text += (
                f"{i}️⃣ <b>{name}</b>\n\n"
                f"• 💰 Текущий долг: <b>{current_debt:.0f} с.</b>\n"
                f"• 📞 Контакт: <code>{phone}</code>\n"
                f"• 🗓 Дата последнего платежа: {last_payment_date}\n"
                f"• 💵 Сумма последнего платежа: {last_payment_amount} с.\n"
                f"──────────────────\n"
            )
        
        if len(debtors) > 10:
            text += f"\n... и еще {len(debtors) - 10} в полном списке."

        return text
    except Exception as e:
        return f"❌ Ошибка формирования отчета: {e}"

async def prepare_repay_debt(api_request_func, employee_id: int, company_id: int, client_search: str, amount: float, link_to_shift: bool = True) -> str:
    """
    Инструмент: Подготовка погашения долга.
    Ищет клиента и готовит кнопку подтверждения.
    """
    try:
        if not client_search: return "⚠️ Укажите имя клиента или телефон."
        
        # 1. Ищем клиента
        clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
        
        if not clients: return f"❌ Клиент '{client_search}' не найден."
        if len(clients) > 1:
             options = [f"{c['full_name']} ({c['phone']})" for c in clients]
             return json.dumps({
                 "status": "multiple_results",
                 "message": f"⚠️ Найдено несколько клиентов. Уточните:",
                 "options": options
             }, ensure_ascii=False)
        
        client = clients[0]
        
        # 2. Формируем текст источника
        source_text = "✅ <b>В КАССУ СМЕНЫ</b>" if link_to_shift else "🤝 <b>ЛИЧНО / МИМО КАССЫ</b>"
        
        return json.dumps({
            "confirm_action": "repay_debt",
            "client_id": client['id'],
            "amount": amount,
            "link_to_shift": link_to_shift,
            "message": (
                f"💰 **ПРИЕМ ОПЛАТЫ ДОЛГА**\n"
                f"👤 Клиент: <b>{client['full_name']}</b>\n"
                f"💵 Сумма: <b>{amount} сом</b>\n"
                f"📥 Куда: {source_text}\n\n"
                f"❓ Всё верно? Записываем?"
            )
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка: {e}"
    
async def bulk_assign_by_tracks(
    api_request_func, 
    employee_id: int, 
    company_id: int, 
    track_codes: List[str], 
    client_search: str, 
    status: str = "В пути", 
    weight: float = 0,
    custom_price: float = 0,  # <-- НОВОЕ: Спец цена
    custom_rate: float = 0    # <-- НОВОЕ: Спец курс
) -> str:
    """
    Инструмент: Массовое присвоение + Статус + Расчет (с выбором цены).
    """
    try:
        if not track_codes or not client_search:
            return "❌ Ошибка: Не указаны трек-коды или клиент."

        # 1. Ищем клиента
        clients = await api_request_func("GET", "/api/clients/search", employee_id=employee_id, params={"q": client_search, "company_id": company_id})
        if not clients: return f"❌ Клиент '{client_search}' не найден."
        
        if len(clients) > 1:
             options = [f"ID {c['id']}: {c['full_name']} ({c.get('client_code_prefix')}{c.get('client_code_num')})" for c in clients]
             return json.dumps({
                 "status": "multiple_results",
                 "message": f"⚠️ Найдено {len(clients)} клиентов. Уточните, кто именно?",
                 "options": options
             }, ensure_ascii=False)
        
        client = clients[0]

        # 2. Ищем ID заказов
        found_ids = []
        not_found = []
        for track in track_codes:
            clean_track = track.strip()
            if not clean_track: continue
            orders = await api_request_func("GET", "/api/orders", employee_id=employee_id, params={"q": clean_track, "company_id": company_id, "limit": 1})
            if orders: found_ids.append(orders[0]['id'])
            else: not_found.append(clean_track)

        if not found_ids: return f"❌ Ни один трек-код не найден."

        # 3. ОПРЕДЕЛЯЕМ ЦЕНУ И КУРС
        # Если ИИ не передал свои цифры, берем из Активной Смены (API)
        final_price = custom_price
        final_rate = custom_rate
        
        # Если хотя бы одного параметра нет, запрашиваем дефолтные
        if final_price <= 0 or final_rate <= 0:
            try:
                p_data = await api_request_func("GET", "/api/bot/price", params={"company_id": company_id})
                # Если ИИ не дал цену, берем из API. Если дал - оставляем ИИшную.
                if final_price <= 0: final_price = p_data.get("price_usd", 0)
                if final_rate <= 0: final_rate = p_data.get("exchange_rate", 0)
            except: pass

        # 4. Расчет для отображения
        calc_info = ""
        if weight > 0 and final_price > 0 and final_rate > 0:
            total_sum = weight * final_price * final_rate
            calc_info = (
                f"\n💰 <b>РАСЧЕТ:</b>\n"
                f"⚖️ Вес: {weight} кг\n"
                f"💵 Тариф: <b>${final_price}</b> (курс {final_rate})\n"
                f"🏷 Итог: <b>{total_sum:.0f} сом</b>"
            )
        elif weight > 0:
            calc_info = "\n⚠️ Вес есть, но <b>нет цены/курса</b>. Расчет будет равен 0."

        missing_msg = f"\n⚠️ Не найдены: {', '.join(not_found)}" if not_found else ""
        
        return json.dumps({
            "confirm_action": "bulk_assign_manual",
            "order_ids": found_ids,
            "client_id": client['id'],
            "client_name": client['full_name'],
            "count": len(found_ids),
            "new_status": status,
            "total_weight": weight,
            
            # --- ВАЖНО: Передаем ФИНАЛЬНЫЕ цифры в кнопку ---
            "price": final_price,
            "rate": final_rate,
            # ------------------------------------------------
            
            "message": (
                f"👤 **ПРИСВОЕНИЕ ЗАКАЗОВ**\n"
                f"Клиент: <b>{client['full_name']}</b>\n"
                f"📦 Заказов: <b>{len(found_ids)}</b>\n"
                f"{missing_msg}\n"
                f"⚙️ Статус: <b>'{status}'</b>"
                f"{calc_info}\n\n"
                f"❓ Подтверждаете данные?"
            )
        }, ensure_ascii=False)

    except Exception as e:
        return f"❌ Ошибка: {e}"
