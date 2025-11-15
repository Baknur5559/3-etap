# ai_tools.py

# ... (ИМПОРТЫ ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ)
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

# --- 1. ИНСТРУКЦИЯ ДЛЯ ИИ (СИСТЕМНЫЙ ПРОМПТ) ---
TOOLS_SYSTEM_PROMPT = """
⚡️ **РЕЖИМ АДМИНИСТРАТОРА**
Ты имеешь ПОЛНЫЙ доступ к управлению CRM. Твоя цель — помогать Владельцу управлять бизнесом быстро.

🧠 **КАК ПОНИМАТЬ КОМАНДЫ:**
Понимай с полуслова. Контекст — твой друг.
- "Удали его" -> (смотри в истории, о каком заказе/клиенте шла речь).
- "Запиши расход 200 такси" -> (Инструмент: add_expense).
- "Сделай рассылку, что груз пришел" -> (Сначала предложи текст, потом вызови инструмент broadcast).
- "Смени код Салтанат на 500" -> (Инструмент: change_client_code).

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
# --- КОНЕЦ СИСТЕМНОГО ПРОМПТА ---

# --- 2. ФУНКЦИИ-ОБРАБОТЧИКИ (ПОЛНАЯ ПЕРЕПИСЬ) ---

async def execute_ai_tool(tool_command: dict, api_request_func, company_id: int, employee_id: int) -> str:
    """
    Выполняет "мысли" ИИ, превращая их в действия API или кнопки подтверждения.
    (ВЕРСИЯ 2.1 - Добавлен инструмент get_settings)
    """
    tool = tool_command.get("tool")
    
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
            
        # === БЛОК 6: КОНФИГУРАЦИЯ (НОВЫЙ БЛОК) ===
        elif tool == "get_settings":
            # Используем API для Владельца, чтобы получить все настройки
            api_response = await api_request_func("GET", "/api/settings", employee_id=employee_id)
            
            if not api_response: 
                 return "❌ Ошибка загрузки настроек."
            
            # Преобразуем список [{key: k, value: v}, ...] в словарь для удобства
            settings_dict = {s.get('key'): s.get('value') for s in api_response}
            
            settings_text = "⚙️ **Текущие Настройки Системы:**\n"
            
            # Поля для отображения
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
                    # Скрываем пароли
                    display_value = '*** (Установлен)' if key.startswith('password') else value
                    settings_text += f"- **{display_name}**: {display_value}\n"
                elif key not in settings_dict:
                     # Если настройки вообще нет в БД
                    settings_text += f"- **{display_name}**: ⚠️ Не настроено\n"
            
            # Отдельно выводим статус AI-рубильника
            ai_status = settings_dict.get('ai_enabled')
            ai_status_text = "✅ ВКЛЮЧЕН" if ai_status == 'True' else "❌ ВЫКЛЮЧЕН"
            settings_text += f"\n🤖 **AI Ассистент (Рубильник)**: {ai_status_text}"
            
            return settings_text
            
        else:
            return f"⚠️ Инструмент '{tool}' не поддерживается."

    except Exception as e:
        logger.error(f"AI Tool Error: {e}")
        return "❌ Ошибка выполнения команды."
