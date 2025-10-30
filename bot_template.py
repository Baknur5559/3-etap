# bot_template.py (Полная ИСПРАВЛЕННАЯ версия 2 для Варианта 1)

import os
import httpx # Для HTTP-запросов к API
import asyncio
import logging
import re # Для нормализации телефона
import sys # Для аргументов командной строки
import argparse # Для парсинга аргументов

from typing import Optional, Dict, Any, List # Добавлен List для типизации
from dotenv import load_dotenv # Необязательно, если параметры передаются через аргументы

# Используем библиотеку python-telegram-bot
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from telegram.constants import ParseMode # Для HTML в сообщениях

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
# Настраиваем формат вывода логов и уровень (INFO - показывать информационные сообщения)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Уменьшаем количество логов от библиотеки httpx (показываем только предупреждения и ошибки)
logging.getLogger("httpx").setLevel(logging.WARNING)
# Получаем логгер для нашего бота
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ (будут установлены при запуске из аргументов) ---
BOT_TOKEN: str = ""        # Токен Telegram бота для этой компании
COMPANY_ID: int = 0        # ID компании, к которой привязан этот бот
ADMIN_API_URL: str = ""    # URL основного бэкенда (FastAPI)

# --- ДОБАВЛЕНО: Статусы заказов (нужны для фильтрации) ---
ORDER_STATUSES = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]

# --- Клавиатуры (Меню) ---
# Клавиатура для ОБЫЧНОГО КЛИЕНТА
client_main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Мои заказы"],
    ["➕ Добавить заказ", "🇨🇳 Адреса складов"],
    ["🇰🇬 Наши контакты"]
]
client_main_menu_markup = ReplyKeyboardMarkup(client_main_menu_keyboard, resize_keyboard=True)

# Клавиатура для ВЛАДЕЛЬЦА
owner_main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Все Заказы"],
    ["👥 Клиенты", "🏢 Филиалы"],
    ["➕ Добавить заказ", "📢 Объявление"],
    ["🇨🇳 Адреса складов", "🇰🇬 Наши контакты"]
]
owner_main_menu_markup = ReplyKeyboardMarkup(owner_main_menu_keyboard, resize_keyboard=True)

# --- Состояния для ConversationHandler'ов ---
# Определяем числовые константы для каждого шага в диалогах
# Диалог регистрации:
ASK_PHONE, GET_NAME = range(2)
# Диалог добавления заказа:
CHOOSE_LOCATION, TRACK_CODE, COMMENT = range(2, 5) # Начинаем нумерацию с 2, чтобы не пересекаться

# --- Функции-помощники ---

def normalize_phone_number(phone_str: str) -> str:
    """Очищает номер телефона от лишних символов и приводит к формату 996XXXXXXXXX."""
    if not phone_str: return "" # Возвращаем пустую строку, если на входе пусто
    # Удаляем все символы, кроме цифр
    digits = "".join(filter(str.isdigit, phone_str))
    # Логика нормализации для номеров Кыргызстана
    if len(digits) == 12 and digits.startswith("996"):
        return digits # Уже в нужном формате (996555123456)
    if len(digits) == 10 and digits.startswith("0"):
        return "996" + digits[1:] # Преобразуем 0555123456 -> 996555123456
    if len(digits) == 9:
        return "996" + digits # Преобразуем 555123456 -> 996555123456
    # Если формат не распознан, логируем предупреждение и возвращаем пустую строку
    logger.warning(f"Не удалось нормализовать номер: {phone_str} -> {digits}")
    return ""

# --- Функция для работы с API ---
async def api_request(method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Универсальная асинхронная функция для отправки запросов к API бэкенда.
    Args:
        method (str): HTTP метод ('GET', 'POST', 'PATCH', 'DELETE').
        endpoint (str): Путь к API эндпоинту (начинается с '/api/...').
        **kwargs: Дополнительные аргументы для httpx (json, params, data, headers).
    Returns:
        Optional[Dict[str, Any]]: Распарсенный JSON-ответ от API или None/словарь с ошибкой.
    """
    global ADMIN_API_URL, COMPANY_ID # Используем глобальные переменные для URL и ID компании
    # Проверяем, установлен ли URL API
    if not ADMIN_API_URL:
        logger.error("ADMIN_API_URL не установлен! Невозможно выполнить API запрос.")
        return {"error": "URL API не настроен.", "status_code": 500}
    # Формируем полный URL
    url = f"{ADMIN_API_URL}{endpoint}"

    # --- Идентификация бота/компании на стороне API ---
    # Добавляем company_id в параметры GET или тело POST/PATCH/PUT
    if method.upper() == 'GET':
        params = kwargs.get('params', {})
        # Проверяем, что company_id еще не был добавлен вручную
        if 'company_id' not in params:
            params['company_id'] = COMPANY_ID
        kwargs['params'] = params
    elif method.upper() in ['POST', 'PATCH', 'PUT']:
        json_data = kwargs.get('json') # Не используем .get() с default={}, чтобы можно было передать None
        if json_data is not None: # Добавляем company_id, только если тело JSON передается
            # Проверяем, что company_id еще не был добавлен вручную
            if 'company_id' not in json_data:
                json_data['company_id'] = COMPANY_ID
            kwargs['json'] = json_data
        # Если передается 'data' вместо 'json', предполагаем, что company_id там уже есть или не нужен
    # --------------------------------------------------------------------

    headers = kwargs.pop('headers', {'Content-Type': 'application/json'}) # Заголовки по умолчанию

    try:
        # Используем httpx.AsyncClient для асинхронных запросов
        async with httpx.AsyncClient(timeout=15.0) as client: # Увеличен таймаут до 15 сек
            # Логируем исходящий запрос
            logger.debug(f"API Request: {method} {url} | Headers: {headers} | Data/Params: {kwargs}")
            # Выполняем запрос
            response = await client.request(method, url, headers=headers, **kwargs)
            # Логируем ответ
            logger.debug(f"API Response: {response.status_code} for {method} {url}")

            # Проверяем статус ответа, если >400, вызывается исключение HTTPStatusError
            response.raise_for_status()

            # Обработка успешного ответа
            if response.status_code == 204: # No Content (например, для DELETE)
                return {"status": "ok"} # Возвращаем условный успех

            # Пытаемся декодировать JSON, если есть тело ответа
            if response.content:
                try:
                    return response.json()
                except Exception as json_err:
                    logger.error(f"API Error: Failed to decode JSON response from {url}. Status: {response.status_code}. Content: {response.text[:200]}...", exc_info=True)
                    return {"error": "Ошибка чтения ответа от сервера.", "status_code": 500}
            else:
                 # Успешный ответ без тела
                 return {"status": "ok"}

    except httpx.HTTPStatusError as e:
        # Ошибка от API (4xx, 5xx) - бэкенд вернул ошибку
        error_detail = f"Ошибка API ({e.response.status_code})"
        try:
            # Пытаемся извлечь сообщение 'detail' из JSON-ответа ошибки
            error_data = e.response.json()
            error_detail = error_data.get("detail", str(error_data))
        except Exception:
            # Если ответ не JSON, берем текстовое содержимое или стандартное сообщение
            error_detail = e.response.text or str(e)
        logger.error(f"API Error ({e.response.status_code}) for {method} {url}: {error_detail}")
        # Возвращаем словарь с ошибкой
        return {"error": error_detail, "status_code": e.response.status_code}
    except httpx.RequestError as e:
        # Ошибка сети (недоступен сервер, таймаут DNS и т.д.)
        logger.error(f"Network Error for {method} {url}: {e}")
        return {"error": "Ошибка сети при обращении к серверу. Попробуйте позже.", "status_code": 503} # Service Unavailable
    except Exception as e:
        # Любые другие неожиданные ошибки
        logger.error(f"Unexpected Error during API request to {url}: {e}", exc_info=True) # Логируем полный traceback
        return {"error": "Внутренняя ошибка бота при запросе к серверу.", "status_code": 500}

# bot_template.py (ЗАМЕНИТЬ ПОЛНОСТЬЮ функцию start)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик команды /start.
    Проверяет, привязан ли Telegram пользователя к клиенту в ЭТОЙ компании через API.
    Если да - показывает главное меню (клиента или владельца).
    Если нет - запрашивает номер телефона.
    Возвращает следующее состояние для ConversationHandler.
    """
    user = update.effective_user
    chat_id = str(user.id) # ID чата пользователя
    logger.info(f"Команда /start от пользователя {user.full_name} (ID: {chat_id}) для компании {COMPANY_ID}")

    # Делаем запрос к API для идентификации пользователя по Chat ID
    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", # Эндпоинт, который мы создали в main.py
        json={"telegram_chat_id": chat_id, "company_id": COMPANY_ID} # Отправляем ID чата и ID компании
    )

    # Анализируем ответ API
    if api_response and "error" not in api_response:
        # Успех: API нашел клиента и вернул его данные
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False) # Получаем флаг Владельца

        # Проверка, что данные клиента получены
        if not client_data or not client_data.get("id"):
             logger.error(f"Ошибка API /api/bot/identify_user: Не получены данные клиента в успешном ответе. Ответ: {api_response}")
             await update.message.reply_text("Произошла ошибка при получении данных вашего профиля. Попробуйте /start позже.", reply_markup=ReplyKeyboardRemove())
             return ConversationHandler.END # Завершаем диалог при ошибке

        # Сохраняем важные данные в user_data для использования в других функциях
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        logger.info(f"Пользователь {chat_id} идентифицирован как ClientID: {client_data.get('id')}, IsOwner: {is_owner}")

        # Выбираем правильную клавиатуру
        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        # Добавляем пометку для Владельца
        role_text = " (Владелец)" if is_owner else ""
        # Отправляем приветственное сообщение
        await update.message.reply_html(
            f"👋 Здравствуйте, <b>{client_data.get('full_name')}</b>{role_text}!\n\nРад вас снова видеть! Используйте меню.",
            reply_markup=markup
        )
        # Завершаем диалог, так как пользователь идентифицирован
        return ConversationHandler.END
    elif api_response and api_response.get("status_code") == 404:
        # Ошибка 404 от API: Пользователь с таким Chat ID не найден в этой компании
        logger.info(f"Пользователь {chat_id} не найден по Chat ID для компании {COMPANY_ID}. Запрашиваем телефон.")
        # Просим пользователя отправить номер телефона
        await update.message.reply_text(
            "Здравствуйте! 🌟\n\nПохоже, мы еще не знакомы или ваш Telegram не привязан."
            "\nПожалуйста, отправьте ваш номер телефона (тот, что вы использовали при регистрации в карго).",
            # Добавляем кнопку "Отправить мой номер", которая запросит контакт у пользователя
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]], # request_contact=True - магия Telegram
                resize_keyboard=True, one_time_keyboard=True # Клавиатура под размер, исчезает после нажатия
            )
        )
        # Переходим в состояние ASK_PHONE, ожидая получения контакта
        return ASK_PHONE
    else:
        # Любая другая ошибка API (500, 400 и т.д.) или ошибка сети
        error_msg = api_response.get("error", "Неизвестная ошибка сервера.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове /api/bot/identify_user (Chat ID): {error_msg}")
        # Сообщаем пользователю об ошибке
        await update.message.reply_text(
            f"Произошла ошибка при проверке ваших данных: {error_msg}\nПожалуйста, попробуйте позже, нажав /start.",
            reply_markup=ReplyKeyboardRemove() # Убираем клавиатуру
            )
        # Завершаем диалог при ошибке
        return ConversationHandler.END

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик получения контакта (номера телефона) от пользователя.
    Вызывается, когда пользователь нажимает кнопку "📱 Отправить мой номер".
    Пытается найти клиента по номеру через API.
    Если найден - привязывает Telegram и показывает меню.
    Если не найден - предлагает зарегистрироваться, переходя в состояние GET_NAME.
    Возвращает следующее состояние для ConversationHandler.
    """
    user = update.effective_user
    chat_id = str(user.id)
    contact = update.message.contact # Получаем объект контакта
    # Проверяем, что контакт действительно получен
    if not contact:
        logger.warning(f"Сообщение от {chat_id} не содержит контакта, хотя ожидался.")
        await update.message.reply_text("Не удалось получить ваш номер. Попробуйте еще раз.", reply_markup=ReplyKeyboardRemove())
        return ASK_PHONE # Остаемся в состоянии ожидания телефона

    phone_number = contact.phone_number
    # Нормализуем номер к формату 996...
    normalized_phone = normalize_phone_number(phone_number)
    # Проверяем результат нормализации
    if not normalized_phone:
         await update.message.reply_text(f"Не удалось распознать номер телефона: {phone_number}. Попробуйте отправить его текстом (начиная с 0 или 996).", reply_markup=ReplyKeyboardRemove())
         return ASK_PHONE # Остаемся в состоянии ожидания телефона

    logger.info(f"Получен контакт от {user.full_name} (ID: {chat_id}): {phone_number} -> {normalized_phone}")

    # Пытаемся найти или зарегистрировать клиента по НОМЕРУ ТЕЛЕФОНА через API
    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", # Используем тот же эндпоинт
        json={"telegram_chat_id": chat_id, "phone_number": normalized_phone, "company_id": COMPANY_ID}
    )

    # Анализируем ответ API
    if api_response and "error" not in api_response:
        # Успех: Клиент найден по номеру, API привязал Chat ID
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False)
        # Проверка данных клиента
        if not client_data or not client_data.get("id"):
             logger.error(f"Ошибка API /api/bot/identify_user (Phone): Не получены данные клиента. Ответ: {api_response}")
             await update.message.reply_text("Ошибка при получении данных профиля после привязки. Попробуйте /start.", reply_markup=ReplyKeyboardRemove())
             return ConversationHandler.END

        # Сохраняем данные в user_data
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        logger.info(f"Пользователь {chat_id} успешно привязан к ClientID: {client_data.get('id')}, IsOwner: {is_owner}")

        # Выбираем и показываем меню
        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        role_text = " (Владелец)" if is_owner else ""
        await update.message.reply_html(
            f"🎉 Отлично, <b>{client_data.get('full_name')}</b>{role_text}! Ваш аккаунт успешно привязан.\n\nИспользуйте меню.",
            reply_markup=markup
        )
        # Успешно, завершаем диалог
        return ConversationHandler.END
    elif api_response and api_response.get("status_code") == 404:
        # Ошибка 404 от API: Клиент с таким номером НЕ НАЙДЕН в этой компании
        logger.info(f"Клиент с номером {normalized_phone} не найден для компании {COMPANY_ID}. Предлагаем регистрацию.")
        # Сохраняем номер телефона для следующего шага регистрации
        context.user_data['phone_to_register'] = normalized_phone
        # Просим ввести имя
        await update.message.reply_html( # Используем HTML для форматирования
            f"Клиент с номером <code>{normalized_phone}</code> не найден. Хотите зарегистрироваться?\n\n"
            "Отправьте ваше <b>полное имя (ФИО)</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove() # Убираем кнопки
        )
        # Переходим в состояние GET_NAME, ожидая ввода имени
        return GET_NAME
    else:
        # Любая другая ошибка API или сети
        error_msg = api_response.get("error", "Неизвестная ошибка сервера.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове /api/bot/identify_user (Phone): {error_msg}")
        await update.message.reply_text(
            f"Произошла ошибка при проверке номера: {error_msg}\nПожалуйста, попробуйте позже, нажав /start.",
            reply_markup=ReplyKeyboardRemove()
            )
        # Завершаем диалог при ошибке
        return ConversationHandler.END

async def register_via_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик получения имени пользователя для регистрации.
    Вызывается ConversationHandler'ом, когда бот находится в состоянии GET_NAME.
    Отправляет запрос на создание клиента через API.
    """
    user = update.effective_user
    chat_id = str(user.id)
    full_name = update.message.text # Получаем введенное имя

    # Получаем номер телефона, сохраненный на предыдущем шаге
    phone_to_register = context.user_data.get('phone_to_register')
    # Проверяем, есть ли номер (на всякий случай)
    if not phone_to_register:
        logger.error(f"Ошибка регистрации для {chat_id}: Не найден phone_to_register в user_data.")
        await update.message.reply_text("Произошла внутренняя ошибка. Пожалуйста, попробуйте начать сначала с /start.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # Простая валидация имени (не пустое)
    if not full_name or len(full_name) < 2:
         await update.message.reply_text("Пожалуйста, введите корректное полное имя (ФИО).")
         return GET_NAME # Остаемся в состоянии ожидания имени

    logger.info(f"Попытка регистрации нового клиента: Имя='{full_name}', Телефон='{phone_to_register}', Компания={COMPANY_ID}, ChatID={chat_id}")

    # --- Вызываем API для СОЗДАНИЯ клиента ---
    # Бэкенд сам сгенерирует код клиента и проверит дубликат телефона еще раз
    api_response = await api_request(
        "POST",
        "/api/clients", # Используем стандартный эндпоинт создания клиента
        json={
            "full_name": full_name,
            "phone": phone_to_register, # Передаем нормализованный номер
            "company_id": COMPANY_ID,   # Передаем ID компании
            "telegram_chat_id": chat_id # Сразу привязываем Telegram
            # client_code_prefix и client_code_num не передаем, бэкенд сгенерирует
        }
    )

    # Анализируем ответ API
    if api_response and "error" not in api_response and "id" in api_response:
        # Успех: Клиент создан
        client_data = api_response # Ответ POST /api/clients - это созданный клиент
        # Проверка полученных данных
        if not client_data.get("id"):
             logger.error(f"Ошибка API POST /api/clients: Не получен ID нового клиента. Ответ: {api_response}")
             await update.message.reply_text("Ошибка сервера при регистрации. Попробуйте /start позже.", reply_markup=ReplyKeyboardRemove())
             return ConversationHandler.END

        # Сохраняем данные нового клиента в user_data
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = False # Новый клиент точно не Владелец
        context.user_data['full_name'] = client_data.get("full_name")
        # Очищаем временные данные регистрации
        context.user_data.pop('phone_to_register', None)
        logger.info(f"Новый клиент успешно зарегистрирован: ID={client_data.get('id')}")

        # Отправляем сообщение об успехе
        await update.message.reply_html(
            f"✅ Регистрация успешна, <b>{full_name}</b>!\n\n"
            f"Ваш код: <b>{client_data.get('client_code_prefix', '')}{client_data.get('client_code_num', '')}</b>\n\n"
            "Теперь используйте меню.",
            reply_markup=client_main_menu_markup # Показываем меню клиента
        )
        # Завершаем диалог
        return ConversationHandler.END
    else:
        # Ошибка при создании клиента на бэкенде
        error_msg = api_response.get("error", "Неизвестная ошибка регистрации.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове POST /api/clients для регистрации: {error_msg}")
        await update.message.reply_text(
            f"К сожалению, произошла ошибка при регистрации: {error_msg}\n"
            "Возможно, клиент с таким телефоном уже существует. Попробуйте /start снова.",
            reply_markup=ReplyKeyboardRemove()
            )
        # Завершаем диалог при ошибке
        return ConversationHandler.END

# --- ОБРАБОТЧИКИ ДИАЛОГА ДОБАВЛЕНИЯ ЗАКАЗА ---

async def add_order_start_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога добавления заказа: выбор филиала."""
    client_id = context.user_data.get('client_id')
    # Проверка, идентифицирован ли пользователь
    if not client_id:
        await update.message.reply_text("Ошибка: Сначала нужно идентифицироваться. Нажмите /start.")
        return ConversationHandler.END # Завершаем, если клиент не найден

    logger.info(f"Пользователь {client_id} начинает добавление заказа для компании {COMPANY_ID}.")

    # Получаем список филиалов для текущей компании
    api_response = await api_request("GET", "/api/locations", params={'company_id': COMPANY_ID})

    # Проверяем ответ API
    if not api_response or "error" in api_response or not isinstance(api_response, list) or not api_response:
        error_msg = api_response.get("error", "Не удалось загрузить филиалы.") if api_response else "Нет ответа от сервера."
        logger.error(f"Ошибка загрузки филиалов для company_id={COMPANY_ID}: {error_msg}")
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return ConversationHandler.END # Завершаем при ошибке

    locations = api_response # Список филиалов
    # Сохраняем ID и имена филиалов в user_data для последующей проверки
    context.user_data['available_locations'] = {loc['id']: loc['name'] for loc in locations}

    # Создаем инлайн-клавиатуру с кнопками филиалов
    keyboard = [
        # Создаем по 2 кнопки в ряд для компактности
        [InlineKeyboardButton(loc['name'], callback_data=f"loc_{loc['id']}") for loc in locations[i:i+2]]
        for i in range(0, len(locations), 2)
    ]
    # Добавляем кнопку отмены в отдельный ряд
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel_add_order")])

    # Отправляем сообщение с запросом выбора филиала
    await update.message.reply_text(
        "Шаг 1/3: Выберите филиал, к которому относится заказ:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    # Переходим в состояние CHOOSE_LOCATION, ожидая нажатия инлайн-кнопки
    return CHOOSE_LOCATION

async def location_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора филиала (нажатие Inline кнопки)."""
    query = update.callback_query # Получаем объект CallbackQuery
    await query.answer() # Отвечаем на callback, чтобы убрать часики у кнопки

    # Извлекаем ID филиала из callback_data (например, 'loc_7' -> '7')
    location_id_str = query.data.split('_')[1]

    try:
        chosen_location_id = int(location_id_str) # Преобразуем ID в число
        # Проверяем, есть ли такой ID в списке доступных филиалов, сохраненном ранее
        available_locations = context.user_data.get('available_locations', {})
        if chosen_location_id not in available_locations:
            logger.warning(f"Пользователь {update.effective_user.id} выбрал неверный location_id: {chosen_location_id}")
            await query.edit_message_text(text="Ошибка: Выбран неверный филиал.")
            return ConversationHandler.END # Завершаем при ошибке

        # Сохраняем выбранный ID филиала в user_data
        context.user_data['chosen_location_id'] = chosen_location_id
        # Получаем имя филиала для отображения
        location_name = available_locations.get(chosen_location_id, f"ID {chosen_location_id}")

        logger.info(f"Пользователь {update.effective_user.id} выбрал филиал {location_name} (ID: {chosen_location_id})")

        # Редактируем исходное сообщение, убирая кнопки выбора филиалов
        await query.edit_message_text(text=f"Филиал '{location_name}' выбран.")
        # Отправляем НОВОЕ сообщение с запросом трек-кода и обычной клавиатурой "Отмена"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Шаг 2/3: Теперь введите трек-код заказа:",
            # Клавиатура только с кнопкой "Отмена"
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True)
        )
        # Переходим в состояние TRACK_CODE, ожидая ввода трек-кода
        return TRACK_CODE
    except (ValueError, IndexError, KeyError) as e: # Ловим ошибки парсинга или доступа к данным
        logger.error(f"Ошибка обработки выбора филиала: {e}. Callback data: {query.data}", exc_info=True)
        await query.edit_message_text(text="Произошла ошибка при выборе филиала. Попробуйте снова.")
        return ConversationHandler.END # Завершаем при ошибке

async def received_track_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен трек-код от пользователя."""
    track_code = update.message.text.strip() # Получаем текст и убираем пробелы
    # Простая валидация (не пустой и минимальная длина)
    if not track_code or len(track_code) < 3: # Пример минимальной длины - 3 символа
        await update.message.reply_text("Трек-код кажется некорректным или слишком коротким. Попробуйте ввести еще раз:")
        return TRACK_CODE # Остаемся в том же состоянии, ожидая повторного ввода

    # Сохраняем трек-код в user_data
    context.user_data['track_code'] = track_code
    logger.info(f"Пользователь {update.effective_user.id} ввел трек-код: {track_code}")

    # Создаем клавиатуру с опциями для комментария ("Пропустить", "Отмена")
    keyboard = [
        ["⏩ Пропустить"], # Кнопка Пропустить
        ["Отмена"]       # Кнопка Отмена
    ]
    # Отправляем сообщение с запросом комментария
    await update.message.reply_text(
        "Шаг 3/3: Введите примечание (например, 'красные кроссовки') или нажмите 'Пропустить'.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    # Переходим в состояние COMMENT, ожидая комментария или нажатия кнопки
    return COMMENT

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь нажал 'Пропустить' на шаге ввода комментария."""
    context.user_data['comment'] = None # Устанавливаем комментарий в None
    logger.info(f"Пользователь {update.effective_user.id} пропустил ввод комментария.")
    # Сразу вызываем функцию сохранения заказа
    # save_order_from_bot сама завершит диалог (вернет ConversationHandler.END)
    return await save_order_from_bot(update, context)

async def received_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен комментарий от пользователя."""
    comment = update.message.text # Получаем текст комментария
    context.user_data['comment'] = comment # Сохраняем комментарий
    logger.info(f"Пользователь {update.effective_user.id} ввел комментарий: {comment}")
    # Сразу вызываем функцию сохранения заказа
    # save_order_from_bot сама завершит диалог (вернет ConversationHandler.END)
    return await save_order_from_bot(update, context)

async def save_order_from_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет введенные данные заказа через API."""
    # Получаем все необходимые данные из user_data
    client_id = context.user_data.get('client_id')
    location_id = context.user_data.get('chosen_location_id')
    track_code = context.user_data.get('track_code')
    comment = context.user_data.get('comment') # Может быть None
    is_owner = context.user_data.get('is_owner', False)
    # Определяем правильную клавиатуру для ответа
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    # Проверка на случай, если что-то пошло не так в диалоге и данных не хватает
    if not all([client_id, location_id, track_code]):
         await update.message.reply_text("Ошибка: Не хватает данных для сохранения заказа. Попробуйте добавить заказ снова.", reply_markup=markup)
         logger.error(f"Ошибка сохранения заказа: Не хватает данных. client={client_id}, loc={location_id}, track={track_code}")
         # Очищаем временные данные перед выходом
         context.user_data.pop('chosen_location_id', None)
         context.user_data.pop('track_code', None)
         context.user_data.pop('comment', None)
         context.user_data.pop('available_locations', None)
         return ConversationHandler.END # Завершаем диалог с ошибкой

    # Формируем payload для отправки на эндпоинт POST /api/orders
    payload = {
        "client_id": client_id,
        "location_id": location_id, # ID выбранного филиала
        "track_code": track_code,
        "comment": comment, # Комментарий (может быть null)
        "purchase_type": "Доставка", # Заказы из бота - всегда Доставка
        "company_id": COMPANY_ID # Передаем ID компании из глобальной переменной
        # party_date бэкенд установит сам
    }
    logger.info(f"Отправка запроса на создание заказа: {payload}")
    # Выполняем запрос к API
    api_response = await api_request("POST", "/api/orders", json=payload)

    # Анализируем ответ API
    if api_response and "error" not in api_response and "id" in api_response:
        # Успех: Заказ создан
        order_data = api_response
        logger.info(f"Заказ ID {order_data.get('id')} успешно создан для клиента {client_id}")
        await update.message.reply_html(
            f"✅ Готово! Ваш заказ с трек-кодом <code>{track_code}</code> успешно добавлен.",
            reply_markup=markup # Показываем основное меню
        )
    else:
        # Ошибка при создании заказа
        error_msg = api_response.get("error", "Не удалось сохранить заказ.") if api_response else "Нет ответа от сервера."
        logger.error(f"Ошибка сохранения заказа для клиента {client_id}: {error_msg}")
        await update.message.reply_text(f"Ошибка сохранения заказа: {error_msg}", reply_markup=markup)

    # Очистка временных данных диалога добавления заказа
    context.user_data.pop('chosen_location_id', None)
    context.user_data.pop('track_code', None)
    context.user_data.pop('comment', None)
    context.user_data.pop('available_locations', None)
    # Завершаем диалог
    return ConversationHandler.END

# --- ОБРАБОТЧИК КОМАНД МЕНЮ (вне диалогов) ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений (команды меню). Запускается, если сообщение не было перехвачено диалогом."""
    user = update.effective_user
    text = update.message.text
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    chat_id = update.effective_chat.id

    # --- Проверка идентификации ---
    if not client_id:
        logger.warning(f"Сообщение '{text}' от неидентифицированного пользователя {chat_id}.")
        await update.message.reply_text("Пожалуйста, сначала представьтесь. Нажмите /start.", reply_markup=ReplyKeyboardRemove())
        return

    logger.info(f"Обработка команды меню от {user.full_name} (ClientID: {client_id}, IsOwner: {is_owner}): '{text}'")
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    # --- Обработка общих команд меню ---
    if text == "👤 Мой профиль":
        logger.info(f"Вызов функции profile для client_id={client_id}")
        await profile(update, context)
    elif text == "📦 Мои заказы" and not is_owner: # "Мои заказы" только для клиента
        logger.info(f"Вызов функции my_orders для client_id={client_id}")
        await my_orders(update, context)
    # Команда "➕ Добавить заказ" обрабатывается ConversationHandler'ом, поэтому здесь ее нет
    elif text == "🇨🇳 Адреса складов":
        logger.info(f"Вызов функции china_addresses для client_id={client_id}")
        await china_addresses(update, context)
    elif text == "🇰🇬 Наши контакты":
        logger.info(f"Вызов функции bishkek_contacts для company_id={COMPANY_ID}")
        await bishkek_contacts(update, context)

    # --- Обработка команд меню Владельца ---
    elif is_owner:
        if text == "📦 Все Заказы":
             logger.info(f"Вызов функции owner_all_orders для company_id={COMPANY_ID}")
             await owner_all_orders(update, context)
        elif text == "👥 Клиенты":
             logger.info(f"Вызов функции owner_clients для company_id={COMPANY_ID}")
             await owner_clients(update, context)
        elif text == "🏢 Филиалы":
             logger.info(f"Вызов функции owner_locations для company_id={COMPANY_ID}")
             await owner_locations(update, context)
        elif text == "📢 Объявление":
             logger.info(f"Вызов функции owner_broadcast_start для company_id={COMPANY_ID}")
             await owner_broadcast_start(update, context)
        # Если команда Владельца не распознана
        else:
             logger.warning(f"Неизвестная команда Владельца: '{text}' от {client_id}")
             await update.message.reply_text("Неизвестная команда.", reply_markup=markup)

    # --- Обработка неизвестных команд для клиента ---
    else:
        logger.warning(f"Неизвестная команда Клиента: '{text}' от {client_id}")
        await update.message.reply_text("Неизвестная команда. Пожалуйста, используйте кнопки меню.", reply_markup=markup)

# --- РЕАЛИЗАЦИЯ ФУНКЦИЙ-ЗАГЛУШЕК (с await) ---
# bot_template.py (ЗАМЕНИТЬ ПОЛНОСТЬЮ функцию profile)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает профиль клиента (или владельца), запрашивая данные через API."""
    user = update.effective_user
    chat_id = str(user.id)
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    if not client_id:
        logger.warning(f"Попытка вызова profile без client_id для chat_id={chat_id}")
        await update.message.reply_text("Ошибка: Не удалось определить ваш профиль. Попробуйте /start.", reply_markup=markup)
        return

    logger.info(f"Запрос профиля для клиента {client_id}")
    await update.message.reply_text("Загрузка данных профиля...", reply_markup=markup) # Сообщение о загрузке

    # --- Шаг 1: Запрос данных клиента ---
    api_response_client = await api_request("GET", f"/api/clients/{client_id}", params={'company_id': COMPANY_ID})

    if not api_response_client or "error" in api_response_client:
        error_msg = api_response_client.get("error", "Не удалось загрузить профиль.") if api_response_client else "Нет ответа от сервера."
        logger.error(f"Ошибка API при запросе профиля клиента {client_id}: {error_msg}")
        # --- ИЗМЕНЕНИЕ: Отправляем ошибку и выходим ---
        await update.message.reply_text(f"Ошибка загрузки профиля: {error_msg}")
        return # Выходим при ошибке

    client = api_response_client # Данные клиента получены

    # --- Шаг 2: Формирование и отправка текста профиля ---
    role_text = " (Владелец)" if is_owner else ""
    text = (
        f"👤 <b>Ваш профиль</b>{role_text}\n\n"
        f"<b>✨ ФИО:</b> {client.get('full_name', '?')}\n"
        f"<b>📞 Телефон:</b> {client.get('phone', '?')}\n"
        f"<b>⭐️ Ваш код:</b> {client.get('client_code_prefix', '')}{client.get('client_code_num', 'Нет кода')}\n"
        f"<b>📊 Статус:</b> {client.get('status', 'Розница')}\n"
        f"\n<i>Используйте код при оформлении заказов на склад.</i>"
    )
    # Отправляем основной текст профиля БЕЗ инлайн-кнопки пока
    await update.message.reply_html(text, reply_markup=markup) # Отправляем с основной клавиатурой
    logger.info(f"Профиль для клиента {client_id} отправлен.")

    # --- Шаг 3: ОТДЕЛЬНЫЙ Запрос ссылки на ЛК ---
    logger.info(f"Запрос ссылки ЛК для клиента {client_id}")
    # Передаем company_id в теле POST, так как GET не сработал (?)
    api_response_link = await api_request("POST", f"/api/clients/{client_id}/generate_lk_link", json={'company_id': COMPANY_ID})
    lk_url = None
    if api_response_link and "error" not in api_response_link:
        lk_url = api_response_link.get("link")
        logger.info(f"Ссылка ЛК для клиента {client_id} получена: {lk_url}")
    else:
        error_msg_link = api_response_link.get("error", "Нет ответа") if api_response_link else "Нет ответа"
        logger.warning(f"Не удалось сгенерировать ссылку на ЛК для клиента {client_id}: {error_msg_link} (Статус: {api_response_link.get('status_code') if api_response_link else 'N/A'})")
        # Сообщать об ошибке генерации ЛК пользователю необязательно

    # --- Шаг 4: Отправка инлайн-кнопки ЛК (если ссылка есть) ОТДЕЛЬНЫМ сообщением ---
    if lk_url:
        keyboard = [[InlineKeyboardButton("Перейти в Личный Кабинет", url=lk_url)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        # Отправляем сообщение только с кнопкой
        await update.message.reply_text("Ссылка на ваш Личный Кабинет:", reply_markup=reply_markup_inline)
        logger.info(f"Кнопка ЛК отправлена клиенту {client_id}")

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает активные заказы ОБЫЧНОГО КЛИЕНТА, запрашивая данные через API."""
    user = update.effective_user
    chat_id = str(user.id)
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    # Эта функция только для обычных клиентов
    if is_owner:
        logger.warning(f"Владелец {client_id} попытался вызвать 'Мои заказы' вместо 'Все заказы'.")
        await update.message.reply_text("Для просмотра всех заказов компании используйте кнопку '📦 Все Заказы'.", reply_markup=markup)
        return
    if not client_id:
        logger.warning(f"Попытка вызова my_orders без client_id для chat_id={chat_id}")
        await update.message.reply_text("Ошибка: Не удалось определить ваш профиль. Попробуйте /start.", reply_markup=markup)
        return

    logger.info(f"Запрос 'Мои заказы' для клиента {client_id}")
    await update.message.reply_text("Загрузка ваших активных заказов...", reply_markup=markup)

    # --- Запрос активных заказов клиента ---
    # Формируем параметры: ID клиента и все статусы, кроме "Выдан"
    params = {'client_id': client_id}
    # Передаем список статусов в параметрах URL
    statuses_param = [s for s in ORDER_STATUSES if s != "Выдан"]
    # Добавляем company_id
    params['company_id'] = COMPANY_ID

    # Выполняем GET запрос к /api/orders
    # Передаем статусы как отдельные параметры ?statuses=Статус1&statuses=Статус2...
    api_response = await api_request("GET", "/api/orders", params={'client_id': client_id, 'statuses': statuses_param, 'company_id': COMPANY_ID})

    # Проверяем ответ API
    if not api_response or "error" in api_response or not isinstance(api_response, list):
        error_msg = api_response.get("error", "Не удалось загрузить заказы.") if api_response else "Нет ответа от сервера."
        logger.error(f"Ошибка API при запросе заказов клиента {client_id}: {error_msg}")
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return

    active_orders = api_response # Ответ - это список заказов

    if not active_orders:
        await update.message.reply_text("У вас пока нет активных заказов. 🚚", reply_markup=markup)
        return

    # --- Формирование сообщения со списком заказов ---
    message = "📦 <b>Ваши текущие заказы:</b>\n\n"
    # Сортируем по ID (или дате создания), новые вверху
    for order in sorted(active_orders, key=lambda o: o.get('id', 0), reverse=True):
        message += f"<b>Трек:</b> <code>{order.get('track_code', '?')}</code>\n"
        message += f"<b>Статус:</b> {order.get('status', '?')}\n"
        comment = order.get('comment')
        if comment:
            # Экранируем HTML-символы в комментарии на всякий случай
            import html
            message += f"<b>Примечание:</b> {html.escape(comment)}\n"

        # Добавим вес и стоимость, если они рассчитаны
        calc_weight = order.get('calculated_weight_kg')
        calc_cost = order.get('calculated_final_cost_som')
        if calc_weight is not None and calc_cost is not None:
             message += f"<b>Расчет:</b> {calc_weight:.3f} кг / {calc_cost:.0f} сом\n"

        message += "──────────────\n"

    # Ограничение длины сообщения Telegram (4096 символов)
    if len(message) > 4000:
         message = message[:4000] + "\n... (список слишком длинный)"

    # Отправляем сообщение
    await update.message.reply_html(message, reply_markup=markup)
    logger.info(f"Список активных заказов ({len(active_orders)}) отправлен клиенту {client_id}")

# bot_template.py (ЗАМЕНИТЬ ПОЛНОСТЬЮ china_addresses)

async def china_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает адрес склада в Китае, подставляя код клиента и добавляя кнопку инструкции."""
    user = update.effective_user
    chat_id = str(user.id)
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    if not client_id:
        await update.message.reply_text("Ошибка: Не удалось определить профиль.", reply_markup=markup)
        return

    logger.info(f"Запрос адреса склада Китая для клиента {client_id}")
    processing_message = await update.message.reply_text("Загрузка адреса склада...", reply_markup=markup)

    client_unique_code = "ВАШ_КОД" # Значение по умолчанию
    address_text_template = "Адрес склада не настроен в системе."
    instruction_link = None # Ссылка на PDF

    try:
        # --- Запрос 1: Данные клиента (для кода) ---
        api_response_client = await api_request("GET", f"/api/clients/{client_id}", params={'company_id': COMPANY_ID})
        if api_response_client and "error" not in api_response_client:
            client = api_response_client
            client_code_num = client.get('client_code_num')
            client_code_prefix = client.get('client_code_prefix', 'PREFIX')
            if client_code_num:
                client_unique_code = f"{client_code_prefix}-{client_code_num}" # Используем дефис для читаемости
            logger.info(f"Код клиента {client_id} получен: {client_unique_code}")
        else:
            logger.warning(f"Не удалось получить данные клиента {client_id} для кода.")

        # --- Запрос 2: Адрес склада И ССЫЛКА НА ИНСТРУКЦИЮ из настроек ---
        keys_to_fetch = ['china_warehouse_address', 'address_instruction_pdf_link'] # Запрашиваем оба ключа
        api_response_settings = await api_request("GET", "/api/settings", params={'company_id': COMPANY_ID, 'keys': keys_to_fetch})

        if api_response_settings and "error" not in api_response_settings and isinstance(api_response_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_response_settings}
            logger.info(f"Настройки адреса/инструкции для компании {COMPANY_ID} получены: {settings_dict}")

            # Извлекаем адрес
            address_value = settings_dict.get('china_warehouse_address')
            if address_value:
                address_text_template = address_value
            else:
                logger.warning(f"Настройка 'china_warehouse_address' не найдена или пуста для компании {COMPANY_ID}.")

            # Извлекаем ссылку на инструкцию
            instruction_link = settings_dict.get('address_instruction_pdf_link')
            if instruction_link:
                 logger.info(f"Ссылка на инструкцию найдена: {instruction_link}")
            else:
                 logger.info(f"Ссылка на инструкцию ('address_instruction_pdf_link') не найдена для компании {COMPANY_ID}.")

        else:
            error_msg = api_response_settings.get("error", "Нет ответа") if isinstance(api_response_settings, dict) else "Неверный формат ответа"
            logger.warning(f"Не удалось получить адрес/инструкцию для компании {COMPANY_ID} из API: {error_msg}")

        # --- Формирование и отправка сообщения ---
        # Подставляем код клиента в шаблон адреса
        address_text = address_text_template.replace("{{client_code}}", client_unique_code).replace("{client_code}", client_unique_code)

        # Формируем текст сообщения
        text = (
            f"🇨🇳 <b>Адрес склада в Китае</b>\n\n"
            f"Используйте этот адрес для ваших покупок.\n\n"
            # --- УЛУЧШЕННЫЙ ТЕКСТ ---
            f"❗️ Ваш уникальный код: <pre>{client_unique_code}</pre>\n" # Выделяем код
            f"<i>Обязательно указывайте его ПОЛНОСТЬЮ при оформлении заказа!</i>\n\n"
            f"👇 Нажмите на адрес ниже, чтобы скопировать:\n\n"
            f"<code>{address_text}</code>" # Адрес для копирования
        )

        # --- Создание инлайн-кнопки для инструкции ---
        inline_keyboard = []
        if instruction_link:
            # Добавляем кнопку, если ссылка есть
            inline_keyboard.append([InlineKeyboardButton("📄 Инструкция по заполнению адреса", url=instruction_link)])

        reply_markup_inline = InlineKeyboardMarkup(inline_keyboard) if inline_keyboard else None

        # Удаляем сообщение "Загрузка..."
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
        except Exception as del_err:
             logger.warning(f"Не удалось удалить сообщение 'Загрузка...': {del_err}")

        # Отправляем итоговое сообщение с адресом и кнопкой (если есть)
        await update.message.reply_html(text, reply_markup=reply_markup_inline)

        # Отправляем основное меню, если была инлайн-кнопка
        if reply_markup_inline:
            await update.message.reply_text("Используйте основное меню:", reply_markup=markup)

    except Exception as e:
        logger.error(f"Неожиданная ошибка в china_addresses для клиента {client_id}: {e}", exc_info=True)
        try: await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
        except: pass
        await update.message.reply_text("Произошла ошибка при получении адреса склада.", reply_markup=markup)

# bot_template.py (ЗАМЕНИТЬ ПОЛНОСТЬЮ bishkek_contacts)

async def bishkek_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает контакты офиса в Бишкеке, запрашивая данные через API."""
    user = update.effective_user
    chat_id = str(user.id)
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    logger.info(f"Запрос контактов Бишкека для компании {COMPANY_ID}")
    processing_message = await update.message.reply_text("Загрузка контактов...", reply_markup=markup)

    # Значения по умолчанию
    address = "Адрес не указан"
    phone = "Телефон не указан"
    whatsapp_link = None
    instagram_link = None
    map_link = None

    try:
        # --- Запрос контактов из настроек API ---
        keys_to_fetch = ['bishkek_office_address', 'contact_phone', 'whatsapp_link', 'instagram_link', 'map_link']
        api_response_settings = await api_request("GET", "/api/settings", params={'company_id': COMPANY_ID, 'keys': keys_to_fetch})

        if api_response_settings and "error" not in api_response_settings and isinstance(api_response_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_response_settings}
            logger.info(f"Настройки контактов для компании {COMPANY_ID} получены: {settings_dict}")
            address = settings_dict.get('bishkek_office_address') or address
            phone = settings_dict.get('contact_phone') or phone
            whatsapp_link = settings_dict.get('whatsapp_link')
            instagram_link = settings_dict.get('instagram_link')
            map_link = settings_dict.get('map_link')
        else:
            error_msg = api_response_settings.get("error", "Нет ответа") if isinstance(api_response_settings, dict) else "Неверный формат ответа"
            logger.warning(f"Не удалось получить контакты для компании {COMPANY_ID} из API: {error_msg}")

        # --- Формирование текста ---
        text = (
            "🇰🇬 <b>Наши контакты в Бишкеке</b>\n\n"
            f"📍 <b>Адрес:</b>\n{address}\n\n"
            f"📞 <b>Телефон:</b>\n<code>{phone}</code> (нажмите для копирования)"
        )

        # --- Создание инлайн-кнопок ---
        keyboard = []
        if whatsapp_link: keyboard.append([InlineKeyboardButton("💬 WhatsApp", url=whatsapp_link)])
        if instagram_link: keyboard.append([InlineKeyboardButton("📸 Instagram", url=instagram_link)])
        if map_link: keyboard.append([InlineKeyboardButton("🗺️ Карта", url=map_link)])

        # --- Отправка сообщения ---
        reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None
        try: await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
        except Exception as del_err: logger.warning(f"Не удалось удалить сообщение 'Загрузка...': {del_err}")
        await update.message.reply_html(text, reply_markup=reply_markup_inline)
        if reply_markup_inline: await update.message.reply_text("Используйте основное меню:", reply_markup=markup)
        logger.info(f"Контакты Бишкека отправлены пользователю {chat_id}")

    except Exception as e:
        logger.error(f"Неожиданная ошибка в bishkek_contacts для компании {COMPANY_ID}: {e}", exc_info=True)
        try: await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
        except: pass
        await update.message.reply_text("Произошла ошибка при получении контактов.", reply_markup=markup)

# --- Функции-заглушки для Владельца (с await) ---
async def owner_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     logger.info(f"Запрос Владельца 'Все Заказы' для компании {COMPANY_ID}")
     # TODO: Запрос к API GET /api/orders (без client_id, но с company_id)
     await update.message.reply_text("Функция 'Все Заказы' (Владелец) в разработке.") # Используем await

async def owner_clients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     logger.info(f"Запрос Владельца 'Клиенты' для компании {COMPANY_ID}")
     # TODO: Запрос к API GET /api/clients (с company_id)
     await update.message.reply_text("Функция 'Клиенты' (Владелец) в разработке.") # Используем await

async def owner_locations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     logger.info(f"Запрос Владельца 'Филиалы' для компании {COMPANY_ID}")
     # TODO: Запрос к API GET /api/locations (с company_id)
     await update.message.reply_text("Функция 'Филиалы' (Владелец) в разработке.") # Используем await

async def owner_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     logger.info(f"Запрос Владельца 'Сделать объявление' для компании {COMPANY_ID}")
     # TODO: Запустить диалог для создания объявления
     await update.message.reply_text("Функция 'Сделать объявление' (Владелец) в разработке.") # Используем await

# --- ОТМЕНА ДИАЛОГА ---
async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена любого диалога ConversationHandler."""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} отменил диалог.")
    # Определяем правильную клавиатуру для возврата в главное меню
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    message_text = "Действие отменено."

    # Если отмена пришла из инлайн-кнопки (CallbackQuery)
    if update.callback_query:
        await update.callback_query.answer() # Отвечаем на callback
        try:
            # Пытаемся отредактировать сообщение, убрав инлайн-клавиатуру
            await update.callback_query.edit_message_text(message_text, reply_markup=None)
        except Exception as e:
            # Если не получилось (сообщение старое), просто логируем
            logger.warning(f"Не удалось отредактировать сообщение при отмене callback'а: {e}")
        # В любом случае отправляем новое сообщение с основной клавиатурой
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Возврат в главное меню.", reply_markup=markup)
    else: # Если отмена пришла как текстовое сообщение "Отмена" или команда /cancel
        await update.message.reply_text(message_text, reply_markup=markup)

    # Очищаем ВСЕ временные данные диалогов из user_data, чтобы избежать проблем
    keys_to_clear = ['chosen_location_id', 'track_code', 'comment', 'phone_to_register', 'available_locations']
    for key in keys_to_clear:
        context.user_data.pop(key, None)
    # Можно добавить очистку других ключей, если они используются в будущих диалогах
    logger.debug(f"User data очищен после отмены для пользователя {user.id}")

    # Завершаем диалог
    return ConversationHandler.END

# --- Основная функция запуска бота ---
def main() -> None:
    """Запуск бота с параметрами из командной строки."""
    global BOT_TOKEN, COMPANY_ID, ADMIN_API_URL # Объявляем, что будем устанавливать глобальные переменные

    # --- Парсинг аргументов командной строки ---
    parser = argparse.ArgumentParser(description=f"Telegram Bot for Cargo CRM Company")
    parser.add_argument("--token", required=True, help="Telegram Bot Token")
    parser.add_argument("--company-id", required=True, type=int, help="Company ID this bot belongs to")
    parser.add_argument("--api-url", required=True, help="URL of the main Cargo CRM API (e.g., http://127.0.0.1:8000)")
    try:
        args = parser.parse_args()
        BOT_TOKEN = args.token
        COMPANY_ID = args.company_id
        ADMIN_API_URL = args.api_url.rstrip('/') # Убираем слэш в конце URL
    except SystemExit:
         logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Не переданы аргументы --token, --company-id, --api-url.")
         sys.exit(1) # Завершаем работу
    # --- Конец парсинга ---

    logger.info(f"--- Запуск бота для Компании ID: {COMPANY_ID} ---")
    logger.info(f"API URL: {ADMIN_API_URL}")

    # Создание экземпляра приложения Telegram
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Определение ConversationHandler'ов ---

    # Диалог Регистрации/Идентификации
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)], # Начинается с команды /start
        states={
            ASK_PHONE: [MessageHandler(filters.CONTACT, handle_contact)], # Ожидаем контакт
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_via_name)], # Ожидаем имя
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
        per_user=True, per_chat=True, name="registration", persistent=False,
    )

    # Диалог Добавления Заказа
    add_order_conv = ConversationHandler(
        # Точка входа: текстовое сообщение "➕ Добавить заказ"
        entry_points=[MessageHandler(filters.Regex('^➕ Добавить заказ$'), add_order_start_conv)],
        states={
            CHOOSE_LOCATION: [CallbackQueryHandler(location_chosen, pattern='^loc_')], # Выбор филиала
            TRACK_CODE: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog), # Отмена
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_track_code) # Ввод трек-кода
                ],
            COMMENT: [
                MessageHandler(filters.Regex('^⏩ Пропустить$'), skip_comment), # Пропуск комментария
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),       # Отмена
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_comment) # Ввод комментария
            ],
        },
        fallbacks=[
             CommandHandler('cancel', cancel_dialog), # Команда /cancel
             MessageHandler(filters.Regex('^Отмена$'), cancel_dialog), # Текст "Отмена"
             CallbackQueryHandler(cancel_dialog, pattern='^cancel_add_order$') # Инлайн-кнопка отмены
        ],
        per_user=True, per_chat=True, name="add_order", persistent=False,
    )

    # --- Добавление обработчиков в приложение ---
    # Сначала добавляем обработчики диалогов, они имеют приоритет
    application.add_handler(registration_conv)
    application.add_handler(add_order_conv)

    # Затем добавляем обработчик для ВСЕХ ОСТАЛЬНЫХ текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Обработчик команды /cancel вне диалогов
    application.add_handler(CommandHandler('cancel', cancel_dialog))

    # Запуск бота
    logger.info("Бот готов к работе и запускает опрос...")
    application.run_polling()
    logger.info("Бот остановлен.")

# Точка входа при запуске скрипта: python bot_template.py ...
if __name__ == "__main__":
    main()
