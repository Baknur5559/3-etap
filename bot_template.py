#!/usr/bin/env python
# -*- coding: utf-8 -*-
# bot_template.py (Версия 6.0 - Полный переход на API + Функции Владельца)

import os
import httpx # Используется для API запросов
import re    # Используется для очистки номера телефона
import sys  # Для sys.exit()
import logging
import asyncio
import html # Для форматирования ответов
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from telegram.constants import ParseMode # Для HTML в сообщениях

# --- ИЗМЕНЕНИЕ: Модели и БД больше не нужны боту ---
# from models import Client, Order, Location, Setting
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker, joinedload

# --- НАСТРОЙКА ЛОГИРОВАНИЯ (РЕКОМЕНДУЕТСЯ) ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- 1. НАСТРОЙКА ---
# Загружаем переменные окружения из .env файла
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# DATABASE_URL = os.getenv("DATABASE_URL") # <-- Больше не нужен
ADMIN_API_URL = os.getenv('ADMIN_API_URL')

# --- Глобальные переменные для ID компании ---
# Они будут установлены при запуске функцией identify_bot_company()
COMPANY_ID_FOR_BOT: int = 0
COMPANY_NAME_FOR_BOT: str = "Неизвестная компания"

# Проверка, что все переменные окружения заданы
if not TELEGRAM_BOT_TOKEN or not ADMIN_API_URL: # <-- Убрали DATABASE_URL
    logger.critical("="*50)
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: bot_template.py")
    logger.critical("Не найдены переменные окружения: TELEGRAM_BOT_TOKEN или ADMIN_API_URL.")
    logger.critical("="*50)
    sys.exit(1)

# --- Настройка подключения к базе данных ---
# engine = create_engine(DATABASE_URL, pool_recycle=1800, pool_pre_ping=True) # <-- Больше не нужен
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) # <-- Больше не нужен

# --- 2. Клавиатуры (Меню) ---
client_main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Мои заказы"],
    ["➕ Добавить заказ", "🇨🇳 Адреса складов"],
    ["🇰🇬 Наши контакты"]
]
client_main_menu_markup = ReplyKeyboardMarkup(client_main_menu_keyboard, resize_keyboard=True)

# --- НОВАЯ КЛАВИАТУРА ВЛАДЕЛЬЦА ---
owner_main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Все Заказы"], # <
    ["👥 Клиенты", "🏢 Филиалы"], # <
    ["➕ Добавить заказ", "📢 Объявление"], # <
    ["🇨🇳 Адреса складов", "🇰🇬 Наши контакты"]
]
owner_main_menu_markup = ReplyKeyboardMarkup(owner_main_menu_keyboard, resize_keyboard=True)
# --- КОНЕЦ НОВОЙ КЛАВИАТУРЫ ---

# --- 3. Состояния для диалогов (ConversationHandler) ---
# Определяем шаги для разных диалогов
(
    # Диалог Регистрации
    ASK_PHONE, GET_NAME,

    # Диалог добавления заказа
    ADD_ORDER_LOCATION,
    ADD_ORDER_TRACK_CODE,
    ADD_ORDER_COMMENT,

    # --- НОВЫЕ ДИАЛОГИ ВЛАДЕЛЬЦА ---
    OWNER_ASK_ORDER_SEARCH,
    OWNER_ASK_CLIENT_SEARCH,
    OWNER_ASK_BROADCAST_TEXT,
    OWNER_CONFIRM_BROADCAST

) = range(9) # Теперь 9 состояний

# --- 4. Функции-помощники ---

# def get_db() -> Session: # <-- Больше не нужен
#     """Создает сессию базы данных."""
#     return SessionLocal()

def normalize_phone_number(phone_str: str) -> str:
    """Очищает номер телефона от лишних символов и приводит к формату 996XXXXXXXXX."""
    # (Эта функция взята из v5.0, она более надежна)
    if not phone_str: return "" 
    digits = "".join(filter(str.isdigit, phone_str))
    
    # 996555123456 (12 цифр)
    if len(digits) == 12 and digits.startswith("996"):
        return digits 
    # 0555123456 (10 цифр)
    if len(digits) == 10 and digits.startswith("0"):
        return "996" + digits[1:] 
    # 555123456 (9 цифр)
    if len(digits) == 9:
        return "996" + digits 
        
    logger.warning(f"Не удалось нормализовать номер: {phone_str} -> {digits}")
    return "" # Возвращаем пустую строку, если формат не распознан

# async def get_client_from_user_id(user_id: int, db: Session) -> Optional[Client]: # <-- Больше не нужен
#     """..."""
#     return db.query(Client).filter(Client.telegram_chat_id == str(user_id)).first()

# --- НОВАЯ ФУНКЦИЯ API REQUEST (Из v5.0) ---
async def api_request(
    method: str, 
    endpoint: str, 
    employee_id: Optional[int] = None, 
    **kwargs
) -> Optional[Dict[str, Any]]:
    """
    Универсальная асинхронная функция для отправки запросов к API бэкенда.
    (ВЕРСИЯ 6.0 - с поддержкой X-Employee-ID и COMPANY_ID_FOR_BOT)
    """
    global ADMIN_API_URL, COMPANY_ID_FOR_BOT
    if not ADMIN_API_URL:
        logger.error("ADMIN_API_URL не установлен! Невозможно выполнить API запрос.")
        return {"error": "URL API не настроен.", "status_code": 500}
    
    url = f"{ADMIN_API_URL}{endpoint}"
    
    params_dict = kwargs.pop('params', {}) 
    headers = kwargs.pop('headers', {'Content-Type': 'application/json'})

    # Добавляем аутентификацию Владельца, если передан ID
    if employee_id:
        headers['X-Employee-ID'] = str(employee_id)

    # --- ИЗМЕНЕНИЕ: Используем COMPANY_ID_FOR_BOT ---
    if method.upper() == 'GET':
        if 'company_id' not in params_dict:
            params_dict['company_id'] = COMPANY_ID_FOR_BOT
        kwargs['params'] = params_dict

    elif method.upper() in ['POST', 'PATCH', 'PUT']:
        json_data = kwargs.get('json') 
        if json_data is not None: 
            if 'company_id' not in json_data:
                json_data['company_id'] = COMPANY_ID_FOR_BOT
            kwargs['json'] = json_data
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client: 
            logger.debug(f"API Request: {method} {url} | Headers: {headers} | Data/Params: {kwargs}")
            response = await client.request(method, url, headers=headers, **kwargs)
            logger.debug(f"API Response: {response.status_code} for {method} {url}")
            response.raise_for_status()

            if response.status_code == 204:
                return {"status": "ok"} 

            if response.content:
                try:
                    return response.json()
                except Exception as json_err:
                    logger.error(f"API Error: Failed to decode JSON from {url}. Status: {response.status_code}. Content: {response.text[:200]}...", exc_info=True)
                    return {"error": "Ошибка чтения ответа от сервера.", "status_code": 500}
            else:
                return {"status": "ok"}

    except httpx.HTTPStatusError as e:
        error_detail = f"Ошибка API ({e.response.status_code})"
        try:
            error_data = e.response.json()
            error_detail = error_data.get("detail", str(error_data))
        except Exception:
            error_detail = e.response.text or str(e)
        logger.error(f"API Error ({e.response.status_code}) for {method} {url}: {error_detail}")
        return {"error": error_detail, "status_code": e.response.status_code}
    except httpx.RequestError as e:
        logger.error(f"Network Error for {method} {url}: {e}")
        return {"error": "Ошибка сети при обращении к серверу. Попробуйте позже.", "status_code": 503}
    except Exception as e:
        logger.error(f"Unexpected Error during API request to {url}: {e}", exc_info=True) 
        return {"error": "Внутренняя ошибка бота при запросе к серверу.", "status_code": 500}
# --- КОНЕЦ API REQUEST ---


# --- Функция идентификации бота (ОСТАЕТСЯ) ---
def identify_bot_company() -> None:
    """
    Синхронная функция, вызываемая при запуске.
    Обращается к API, чтобы узнать, к какой компании относится этот бот.
    Устанавливает глобальные переменные COMPANY_ID_FOR_BOT и COMPANY_NAME_FOR_BOT.
    """
    global COMPANY_ID_FOR_BOT, COMPANY_NAME_FOR_BOT
    
    print("[Startup] Идентификация компании бота через API...")
    payload = {"token": TELEGRAM_BOT_TOKEN}
    
    try:
        # Используем СИНХРОННЫЙ клиент httpx, так как main() - не async
        with httpx.Client(timeout=10.0) as client:
            response = client.post(f"{ADMIN_API_URL}/api/bot/identify_company", json=payload)
            response.raise_for_status() 
            
            data = response.json()
            COMPANY_ID_FOR_BOT = data.get("company_id")
            COMPANY_NAME_FOR_BOT = data.get("company_name", "Ошибка имени")

            if not COMPANY_ID_FOR_BOT:
                raise Exception("API вернул пустой ID компании.")
                
            print(f"[Startup] УСПЕХ: Бот идентифицирован как '{COMPANY_NAME_FOR_BOT}' (ID: {COMPANY_ID_FOR_BOT})")

    except httpx.HTTPStatusError as e:
        print("="*50)
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось идентифицировать бота (Статус: {e.response.status_code}).")
        try:
            print(f"Ответ API: {e.response.json().get('detail')}")
        except Exception:
            print(f"Ответ API (raw): {e.response.text}")
        print("Убедитесь, что токен этого бота (TELEGRAM_BOT_TOKEN) правильно указан в Админ-панели (main.py) для нужной компании.")
        print("="*50)
        sys.exit(1)
    
    except httpx.RequestError as e:
        print("="*50)
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к API по адресу {ADMIN_API_URL}.")
        print(f"Ошибка сети: {e}")
        print("Убедитесь, что API-сервер (main.py) запущен и доступен.")
        print("="*50)
        sys.exit(1)
    
    except Exception as e:
        print("="*50)
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Неизвестная ошибка при идентификации бота.")
        print(f"Ошибка: {e}")
        print("="*50)
        sys.exit(1)


# --- 5. Диалог Регистрации (ПОЛНОСТЬЮ ПЕРЕПИСАН) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик /start. Проверяет пользователя по Chat ID.
    Если найден - входит.
    Если не найден - спрашивает телефон.
    """
    user = update.effective_user
    chat_id = str(user.id) 
    logger.info(f"Команда /start от {user.full_name} (ID: {chat_id}) для компании {COMPANY_ID_FOR_BOT}")

    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", 
        json={"telegram_chat_id": chat_id, "company_id": COMPANY_ID_FOR_BOT} 
    )

    if api_response and "error" not in api_response:
        # --- УСПЕХ: Пользователь найден по Chat ID ---
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False) 

        if not client_data or not client_data.get("id"):
             logger.error(f"Ошибка API /identify_user: Не получены данные клиента. Ответ: {api_response}")
             await update.message.reply_text("Произошла ошибка при получении данных профиля.", reply_markup=ReplyKeyboardRemove())
             return ConversationHandler.END 

        # --- Сохраняем ВСЕ данные в user_data ---
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data['employee_id'] = api_response.get("employee_id") # <-- ВАЖНО ДЛЯ ВЛАДЕЛЬЦА
        logger.info(f"Пользователь {chat_id} идентифицирован как ClientID: {client_data.get('id')}, IsOwner: {is_owner}, EID: {api_response.get('employee_id')}")

        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        role_text = " (Владелец)" if is_owner else ""
        await update.message.reply_html(
            f"👋 Здравствуйте, <b>{client_data.get('full_name')}</b>{role_text}!\n\nРад вас снова видеть! Используйте меню.",
            reply_markup=markup
        )
        return ConversationHandler.END

    elif api_response and api_response.get("status_code") == 404:
        # --- ОШИБКА 404: Пользователь не найден по Chat ID ---
        logger.info(f"Пользователь {chat_id} не найден. Запрашиваем телефон.")
        await update.message.reply_text(
            "Здравствуйте! 🌟\n\nПохоже, мы еще не знакомы или ваш Telegram не привязан."
            "\nПожалуйста, введите ваш номер телефона (тот, что вы использовали при регистрации в карго), начиная с 0 или 996.",
            reply_markup=ReplyKeyboardRemove() 
        )
        return ASK_PHONE # <-- Переходим в состояние ожидания телефона

    else:
        # --- ДРУГАЯ ОШИБКА API ---
        error_msg = api_response.get("error", "Неизвестная ошибка.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове /api/bot/identify_user (Chat ID): {error_msg}")
        await update.message.reply_text(
            f"Произошла ошибка при проверке данных: {error_msg}\nПожалуйста, попробуйте позже, нажав /start.",
            reply_markup=ReplyKeyboardRemove() 
        )
        return ConversationHandler.END

async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик получения номера телефона, ВВЕДЕННОГО ТЕКСТОМ.
    Проверяет по API.
    Если найден - входит.
    Если не найден - спрашивает ФИО для регистрации.
    """
    user = update.effective_user
    chat_id = str(user.id)
    phone_number_text = update.message.text 
    normalized_phone = normalize_phone_number(phone_number_text)
    
    if not normalized_phone:
         await update.message.reply_text(f"Не удалось распознать номер: {phone_number_text}. Попробуйте отправить его текстом (начиная с 0 или 996).", reply_markup=ReplyKeyboardRemove())
         return ASK_PHONE 

    logger.info(f"Получен номер текстом от {user.full_name} (ID: {chat_id}): {phone_number_text} -> {normalized_phone}")

    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", 
        json={"telegram_chat_id": chat_id, "phone_number": normalized_phone, "company_id": COMPANY_ID_FOR_BOT}
    )

    if api_response and "error" not in api_response:
        # --- УСПЕХ: Пользователь найден по Телефону ---
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False)
        
        if not client_data or not client_data.get("id"):
             logger.error(f"Ошибка API /identify_user (Phone): Не получены данные клиента. Ответ: {api_response}")
             await update.message.reply_text("Ошибка при получении данных профиля. Попробуйте /start.", reply_markup=ReplyKeyboardRemove())
             return ConversationHandler.END

        # --- Сохраняем ВСЕ данные в user_data ---
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data['employee_id'] = api_response.get("employee_id") # <-- ВАЖНО ДЛЯ ВЛАДЕЛЬЦА
        logger.info(f"Пользователь {chat_id} успешно привязан к ClientID: {client_data.get('id')}, IsOwner: {is_owner}, EID: {api_response.get('employee_id')}")

        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        role_text = " (Владелец)" if is_owner else ""
        await update.message.reply_html(
            f"🎉 Отлично, <b>{client_data.get('full_name')}</b>{role_text}! Ваш аккаунт успешно привязан.\n\nИспользуйте меню.",
            reply_markup=markup
        )
        return ConversationHandler.END

    elif api_response and api_response.get("status_code") == 404:
        # --- ОШИБКА 404: Пользователь не найден по Телефону ---
        logger.info(f"Клиент с номером {normalized_phone} не найден. Предлагаем регистрацию.")
        context.user_data['phone_to_register'] = normalized_phone
        
        await update.message.reply_html( 
            f"Клиент с номером <code>{normalized_phone}</code> не найден. Хотите зарегистрироваться?\n\n"
            "Отправьте ваше <b>полное имя (ФИО)</b>.",
            reply_markup=ReplyKeyboardRemove() 
        )
        return GET_NAME # <-- Переходим в состояние ожидания имени

    else:
        # --- ДРУГАЯ ОШИБКА API ---
        error_msg = api_response.get("error", "Неизвестная ошибка.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове /api/bot/identify_user (Phone): {error_msg}")
        await update.message.reply_text(
            f"Произошла ошибка при проверке номера: {error_msg}\nПожалуйста, попробуйте позже, нажав /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

async def register_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    (Переименовано из register_via_name)
    Получает ФИО и регистрирует нового клиента через ПУБЛИЧНЫЙ API эндпоинт.
    """
    full_name = update.message.text
    phone_to_register = context.user_data.get('phone_to_register')
    user = update.effective_user
    chat_id = str(user.id)

    if not phone_to_register:
        logger.error(f"Ошибка регистрации для {chat_id}: Не найден phone_to_register в user_data.")
        await update.message.reply_text("Произошла внутренняя ошибка. Пожалуйста, попробуйте начать сначала с /start.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if not full_name or len(full_name) < 2:
         await update.message.reply_text("Пожалуйста, введите корректное полное имя (ФИО).")
         return GET_NAME 

    logger.info(f"Попытка регистрации: Имя='{full_name}', Телефон='{phone_to_register}', Компания={COMPANY_ID_FOR_BOT}, ChatID={chat_id}")
    
    payload = {
        "full_name": full_name,
        "phone": phone_to_register,
        "company_id": COMPANY_ID_FOR_BOT, # <-- Используем ID, полученный при запуске
        "telegram_chat_id": chat_id   # <-- Сразу привязываем Telegram
    }
    
    # --- Вызов API для регистрации ---
    api_response = await api_request("POST", "/api/bot/register_client", json=payload)

    if api_response and "error" not in api_response and "id" in api_response:
        # --- УСПЕХ: Клиент создан ---
        client_data = api_response 
        
        # --- Сразу сохраняем данные в user_data ---
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = False # Новые клиенты не могут быть Владельцами
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data['employee_id'] = None
        context.user_data.pop('phone_to_register', None)
        logger.info(f"Новый клиент успешно зарегистрирован: ID={client_data.get('id')}")

        client_code = f"{client_data.get('client_code_prefix', 'TG')}{client_data.get('client_code_num', '?')}"
        
        await update.message.reply_html(
            f"✅ Регистрация успешна, <b>{full_name}</b>!\n\n"
            f"Ваш код: <b>{client_code}</b>\n\n"
            "Теперь используйте меню.",
            reply_markup=client_main_menu_markup # Новые клиенты всегда получают меню клиента
        )
        return ConversationHandler.END
    else:
        # --- ОШИБКА РЕГИСТРАЦИИ ---
        error_msg = api_response.get("error", "Неизвестная ошибка.") if api_response else "Сервер недоступен."
        logger.error(f"Ошибка при вызове POST /api/bot/register_client: {error_msg}")
        await update.message.reply_text(
            f"К сожалению, произошла ошибка при регистрации: {error_msg}\n"
            "Попробуйте /start снова.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

# --- 6. Диалог добавления заказа (ПЕРЕПИСАН НА API) ---

async def add_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления заказа, спрашивает филиал (через API)."""
    client_id = context.user_data.get('client_id')
    if not client_id:
        await update.message.reply_text("Ошибка: Сначала нужно идентифицироваться. Нажмите /start.")
        return ConversationHandler.END 

    logger.info(f"Пользователь {client_id} начинает добавление заказа для компании {COMPANY_ID_FOR_BOT}.")

    # --- Запрос к API ---
    api_response = await api_request("GET", "/api/locations", params={'company_id': COMPANY_ID_FOR_BOT})

    if not api_response or "error" in api_response or not isinstance(api_response, list) or not api_response:
        error_msg = api_response.get("error", "Филиалы не найдены.") if api_response else "Нет ответа."
        logger.error(f"Ошибка загрузки филиалов для company_id={COMPANY_ID_FOR_BOT}: {error_msg}")
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return ConversationHandler.END 

    locations = api_response 
    context.user_data['available_locations'] = {loc['id']: loc['name'] for loc in locations}

    if len(locations) == 1:
        # --- Если филиал один ---
        loc = locations[0]
        context.user_data['location_id'] = loc['id']
        logger.info(f"Найден 1 филиал, выбран автоматически: {loc['name']}")
        await update.message.reply_text(
            f"📦 Ваш заказ будет добавлен в филиал: {loc['name']}.\n\n"
            "Пожалуйста, введите <b>трек-код</b> вашего нового заказа.",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return ADD_ORDER_TRACK_CODE
    else:
        # --- Если филиалов несколько ---
        keyboard = [
            [InlineKeyboardButton(loc['name'], callback_data=f"loc_{loc['id']}") for loc in locations[i:i+2]]
            for i in range(0, len(locations), 2)
        ]
        keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel_add_order")])
        
        await update.message.reply_text(
            "Шаг 1/3: Выберите филиал, к которому относится заказ:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_ORDER_LOCATION

async def add_order_received_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора филиала (нажатие Inline кнопки)."""
    query = update.callback_query 
    await query.answer() 
    location_id_str = query.data.split('_')[1]

    try:
        chosen_location_id = int(location_id_str) 
        available_locations = context.user_data.get('available_locations', {})
        if chosen_location_id not in available_locations:
             logger.warning(f"Пользователь {update.effective_user.id} выбрал неверный location_id: {chosen_location_id}")
             await query.edit_message_text(text="Ошибка: Выбран неверный филиал.")
             return ConversationHandler.END 

        context.user_data['location_id'] = chosen_location_id
        location_name = available_locations.get(chosen_location_id, f"ID {chosen_location_id}")

        logger.info(f"Пользователь {update.effective_user.id} выбрал филиал {location_name} (ID: {chosen_location_id})")

        await query.edit_message_text(text=f"Филиал '{location_name}' выбран.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Шаг 2/3: Теперь введите трек-код заказа:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True)
        )
        return ADD_ORDER_TRACK_CODE
    except (ValueError, IndexError, KeyError) as e: 
        logger.error(f"Ошибка обработки выбора филиала: {e}. Callback data: {query.data}", exc_info=True)
        await query.edit_message_text(text="Произошла ошибка при выборе филиала. Попробуйте снова.")
        return ConversationHandler.END 

async def add_order_received_track_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен трек-код от пользователя."""
    track_code = update.message.text.strip() 
    if not track_code or len(track_code) < 3: 
        await update.message.reply_text("Трек-код кажется некорректным. Попробуйте ввести еще раз:")
        return ADD_ORDER_TRACK_CODE 

    context.user_data['track_code'] = track_code
    logger.info(f"Пользователь {update.effective_user.id} ввел трек-код: {track_code}")

    keyboard = [["⏩ Пропустить"], ["Отмена"]]
    await update.message.reply_text(
        "Шаг 3/3: Введите примечание (например, 'красные кроссовки') или нажмите 'Пропустить'.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return ADD_ORDER_COMMENT

async def add_order_received_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен комментарий от пользователя."""
    comment = update.message.text 
    context.user_data['comment'] = comment 
    logger.info(f"Пользователь {update.effective_user.id} ввел комментарий: {comment}")
    return await save_order_from_bot(update, context)

async def add_order_skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь нажал 'Пропустить' на шаге ввода комментария."""
    context.user_data['comment'] = None 
    logger.info(f"Пользователь {update.effective_user.id} пропустил ввод комментария.")
    return await save_order_from_bot(update, context)

async def save_order_from_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет введенные данные заказа через API."""
    client_id = context.user_data.get('client_id')
    location_id = context.user_data.get('location_id')
    track_code = context.user_data.get('track_code')
    comment = context.user_data.get('comment') 
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    if not all([client_id, location_id, track_code]):
         await update.message.reply_text("Ошибка: Не хватает данных. Попробуйте добавить заказ снова.", reply_markup=markup)
         logger.error(f"Ошибка сохранения заказа: Не хватает данных. client={client_id}, loc={location_id}, track={track_code}")
         # Очистка
         context.user_data.pop('location_id', None)
         context.user_data.pop('track_code', None)
         context.user_data.pop('comment', None)
         context.user_data.pop('available_locations', None)
         return ConversationHandler.END 

    payload = {
        "client_id": client_id,
        "location_id": location_id, 
        "track_code": track_code,
        "comment": comment, 
        "purchase_type": "Доставка", 
        "company_id": COMPANY_ID_FOR_BOT # <--- Используем глобальный ID
    }
    logger.info(f"Отправка запроса на создание заказа: {payload}")
    
    # --- Вызов API ---
    api_response = await api_request("POST", "/api/orders", json=payload)

    if api_response and "error" not in api_response and "id" in api_response:
        logger.info(f"Заказ ID {api_response.get('id')} успешно создан для клиента {client_id}")
        await update.message.reply_html(
            f"✅ Готово! Ваш заказ с трек-кодом <code>{track_code}</code> успешно добавлен.",
            reply_markup=markup 
        )
    else:
        error_msg = api_response.get("error", "Не удалось сохранить заказ.") if api_response else "Нет ответа."
        logger.error(f"Ошибка сохранения заказа для клиента {client_id}: {error_msg}")
        await update.message.reply_text(f"Ошибка сохранения заказа: {error_msg}", reply_markup=markup)

    # Очистка
    context.user_data.pop('location_id', None)
    context.user_data.pop('track_code', None)
    context.user_data.pop('comment', None)
    context.user_data.pop('available_locations', None)
    return ConversationHandler.END


# --- 7. Обработчик текстовых сообщений (МАРШРУТИЗАТОР) ---

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработка текстовых сообщений (команды меню).
    Вызывается, ТОЛЬКО если пользователь уже идентифицирован (прошел /start).
    """
    user = update.effective_user
    text = update.message.text
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    chat_id = update.effective_chat.id

    if not client_id:
        logger.warning(f"Сообщение '{text}' от неидентифицированного пользователя {chat_id}.")
        await update.message.reply_text("Пожалуйста, сначала представьтесь. Нажмите /start.", reply_markup=ReplyKeyboardRemove())
        return

    logger.info(f"Обработка команды меню от {user.full_name} (ClientID: {client_id}, IsOwner: {is_owner}): '{text}'")
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    # --- Общие команды ---
    if text == "👤 Мой профиль":
        await profile(update, context)
    elif text == "🇨🇳 Адреса складов":
        await china_addresses(update, context)
    elif text == "🇰🇬 Наши контакты":
        await bishkek_contacts(update, context)
    
    # --- Команды Клиента ---
    elif text == "📦 Мои заказы" and not is_owner: 
        await my_orders(update, context)

    # --- Команды Владельца ---
    elif is_owner:
        # (Кнопки "Добавить заказ" обрабатываются диалогом)
        if text == "📦 Все Заказы":
            await owner_all_orders(update, context)
        elif text == "👥 Клиенты":
            await owner_clients(update, context)
        elif text == "🏢 Филиалы":
            await owner_locations(update, context)
        elif text == "📢 Объявление":
            await owner_broadcast_start(update, context)
        else:
             logger.warning(f"Неизвестная команда Владельца: '{text}' от {client_id}")
             await update.message.reply_text("Неизвестная команда.", reply_markup=markup)
    else:
        logger.warning(f"Неизвестная команда Клиента: '{text}' от {client_id}")
        await update.message.reply_text("Неизвестная команда.", reply_markup=markup)


# --- 8. Функции меню (ПЕРЕПИСАНЫ НА API) ---

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает профиль клиента (или владельца), запрашивая данные через API."""
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    if not client_id:
         await update.message.reply_text("Ошибка: Не удалось определить профиль. Попробуйте /start.", reply_markup=markup)
         return

    logger.info(f"Запрос профиля для клиента {client_id}")
    api_response_client = await api_request("GET", f"/api/clients/{client_id}", params={'company_id': COMPANY_ID_FOR_BOT})

    if not api_response_client or "error" in api_response_client:
        error_msg = api_response_client.get("error", "Не удалось загрузить профиль.") if api_response_client else "Нет ответа."
        await update.message.reply_text(f"Ошибка загрузки профиля: {error_msg}")
        return 

    client = api_response_client 
    role_text = " (Владелец)" if is_owner else ""
    text = (
        f"👤 <b>Ваш профиль</b>{role_text}\n\n"
        f"<b>✨ ФИО:</b> {client.get('full_name', '?')}\n"
        f"<b>📞 Телефон:</b> {client.get('phone', '?')}\n"
        f"<b>⭐️ Ваш код:</b> {client.get('client_code_prefix', '')}{client.get('client_code_num', 'Нет кода')}\n"
        f"<b>📊 Статус:</b> {client.get('status', 'Розница')}\n"
    )
    await update.message.reply_html(text, reply_markup=markup) 

    logger.info(f"Запрос ссылки ЛК для клиента {client_id}")
    # (В main.py /generate_lk_link требует аутентификации Владельца, 
    # это нужно будет исправить в main.py, чтобы бот мог ее вызывать,
    # или сделать для нее отдельный эндпоинт /api/bot/generate_lk)
    #
    # ПОКА МЫ ИСПОЛЬЗУЕМ API v5.0, где /generate_lk_link ПУБЛИЧНЫЙ
    # и использует client_id.
    
    # --- ИЗМЕНЕНИЕ: /generate_lk_link - это POST ---
    api_response_link = await api_request("POST", f"/api/clients/{client_id}/generate_lk_link", json={'company_id': COMPANY_ID_FOR_BOT})
    lk_url = None
    if api_response_link and "error" not in api_response_link:
        lk_url = api_response_link.get("link")
    else:
        error_msg_link = api_response_link.get("error", "Нет ответа") if api_response_link else "Нет ответа"
        logger.warning(f"Не удалось сгенерировать ссылку на ЛК для {client_id}: {error_msg_link}")

    if lk_url:
        keyboard = [[InlineKeyboardButton("Перейти в Личный Кабинет", url=lk_url)]]
        await update.message.reply_text("Ссылка на ваш Личный Кабинет:", reply_markup=InlineKeyboardMarkup(keyboard))


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает активные заказы ОБЫЧНОГО КЛИЕНТА через API."""
    client_id = context.user_data.get('client_id')
    markup = client_main_menu_markup # Эта функция только для клиентов

    logger.info(f"Запрос 'Мои заказы' для клиента {client_id}")
    
    # Статусы, которые считаются "активными"
    active_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
    
    params = {
        'client_id': client_id,
        'statuses': active_statuses,
        'company_id': COMPANY_ID_FOR_BOT
    }
    api_response = await api_request("GET", "/api/orders", params=params)

    if not api_response or "error" in api_response or not isinstance(api_response, list):
        error_msg = api_response.get("error", "Не удалось загрузить заказы.") if api_response else "Нет ответа."
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return

    active_orders = api_response 
    if not active_orders:
        await update.message.reply_text("У вас пока нет активных заказов. 🚚", reply_markup=markup)
        return

    message = "📦 <b>Ваши текущие заказы:</b>\n\n"
    for order in sorted(active_orders, key=lambda o: o.get('id', 0), reverse=True):
        message += f"<b>Трек:</b> <code>{order.get('track_code', '?')}</code>\n"
        message += f"<b>Статус:</b> {order.get('status', '?')}\n"
        comment = order.get('comment')
        if comment:
            message += f"<b>Примечание:</b> {html.escape(comment)}\n"
        
        # Показ расчета, если он есть
        calc_weight = order.get('calculated_weight_kg')
        calc_cost = order.get('calculated_final_cost_som')
        if calc_weight is not None and calc_cost is not None:
            message += f"<b>Расчет:</b> {calc_weight:.3f} кг / {calc_cost:.0f} сом\n"
            
        message += "──────────────\n"

    if len(message) > 4000:
         message = message[:4000] + "\n... (список слишком длинный)"

    await update.message.reply_html(message, reply_markup=markup)


async def china_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает адрес склада в Китае, (через API)."""
    client_id = context.user_data.get('client_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    logger.info(f"Запрос адреса склада Китая для клиента {client_id}")
    
    client_unique_code = "ВАШ_КОД"
    address_text_template = "Адрес склада не настроен в системе."
    instruction_link = None 

    try:
        # 1. Получаем код клиента
        api_client = await api_request("GET", f"/api/clients/{client_id}", params={})
        if api_client and "error" not in api_client:
            client_code_num = api_client.get('client_code_num')
            client_code_prefix = api_client.get('client_code_prefix', 'PREFIX')
            if client_code_num:
                client_unique_code = f"{client_code_prefix}-{client_code_num}"
        else:
             logger.warning(f"Не удалось получить данные клиента {client_id} для кода.")

        # 2. Получаем настройки адреса и инструкции
        keys_to_fetch = ['china_warehouse_address', 'address_instruction_pdf_link'] 
        api_settings = await api_request("GET", "/api/settings", params={'keys': keys_to_fetch})

        if api_settings and "error" not in api_settings and isinstance(api_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_settings}
            
            address_value = settings_dict.get('china_warehouse_address')
            if address_value:
                address_text_template = address_value
            instruction_link = settings_dict.get('address_instruction_pdf_link')
        
        # 3. Формируем ответ
        final_address = address_text_template.replace("{{client_code}}", client_unique_code).replace("{client_code}", client_unique_code)

        text = (
            f"🇨🇳 <b>Адрес склада в Китае</b>\n\n"
            f"❗️ Ваш уникальный код: <pre>{client_unique_code}</pre>\n" 
            f"<i>Обязательно указывайте его ПОЛНОСТЬЮ!</i>\n\n"
            f"👇 Нажмите на адрес ниже, чтобы скопировать:\n\n"
            f"<code>{final_address}</code>" 
        )

        inline_keyboard = []
        if instruction_link:
            inline_keyboard.append([InlineKeyboardButton("📄 Инструкция по заполнению", url=instruction_link)])
        
        reply_markup_inline = InlineKeyboardMarkup(inline_keyboard) if inline_keyboard else None
        
        await update.message.reply_html(text, reply_markup=reply_markup_inline)
        if reply_markup_inline:
            await update.message.reply_text("Используйте основное меню:", reply_markup=markup)

    except Exception as e:
        logger.error(f"Ошибка в china_addresses (API): {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при получении адреса склада.", reply_markup=markup)


async def bishkek_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает контакты офиса, запрашивая филиалы (через API)."""
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    logger.info(f"Запрос контактов (выбор филиала) для компании {COMPANY_ID_FOR_BOT}")

    try:
        # 1. Получаем список филиалов (Locations)
        api_locations = await api_request("GET", "/api/locations", params={})
        if not api_locations or "error" in api_locations or not isinstance(api_locations, list) or not api_locations:
             error_msg = api_locations.get("error", "Филиалы не найдены") if isinstance(api_locations, dict) else "Филиалы не найдены"
             await update.message.reply_text(f"Ошибка: Не удалось загрузить список филиалов. {error_msg}")
             return

        locations = api_locations

        # 2. Получаем ОБЩИЕ контакты (WhatsApp/Instagram)
        keys_to_fetch = ['whatsapp_link', 'instagram_link', 'map_link']
        api_settings = await api_request("GET", "/api/settings", params={'keys': keys_to_fetch})
        
        settings_dict = {}
        if api_settings and "error" not in api_settings and isinstance(api_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_settings}

        # 3. Формирование кнопок
        keyboard = []
        # Кнопки для каждого филиала
        for loc in locations:
            keyboard.append([InlineKeyboardButton(f"📍 {loc.get('name', 'Филиал')}", callback_data=f"contact_loc_{loc.get('id')}")])

        # Общие кнопки
        if settings_dict.get('whatsapp_link'): 
            keyboard.append([InlineKeyboardButton("💬 WhatsApp", url=settings_dict.get('whatsapp_link'))])
        if settings_dict.get('instagram_link'): 
            keyboard.append([InlineKeyboardButton("📸 Instagram", url=settings_dict.get('instagram_link'))])
        if settings_dict.get('map_link'): 
            keyboard.append([InlineKeyboardButton("🗺️ Общая Карта", url=settings_dict.get('map_link'))])

        reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        await update.message.reply_text(
            "🇰🇬 Выберите филиал для просмотра контактов или воспользуйтесь общими ссылками:", 
            reply_markup=reply_markup_inline
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка в bishkek_contacts: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при получении контактов.", reply_markup=markup)

# --- 9. Обработчики Инлайн-кнопок (ПЕРЕПИСАНЫ НА API) ---
async def location_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ИСПРАВЛЕНО) Показывает адрес и ИНЛАЙН-КНОПКИ выбранного филиала.
    """
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    try:
        location_id_str = query.data.split('_')[-1] # 'contact_loc_1' -> '1'
        location_id = int(location_id_str)
        logger.info(f"Пользователь {chat_id} запросил контакты филиала ID: {location_id}")

        # Запрашиваем данные ТОЛЬКО ЭТОГО филиала
        api_response = await api_request("GET", f"/api/locations/{location_id}", params={})

        if not api_response or "error" in api_response or not api_response.get('id'):
            error_msg = api_response.get("error", "Филиал не найден.") if api_response else "Нет ответа"
            logger.error(f"Ошибка API при запросе филиала {location_id}: {error_msg}")
            await query.edit_message_text(f"Ошибка: {error_msg}")
            # Отправляем меню, так как инлайн-сообщение сломано
            await context.bot.send_message(chat_id=chat_id, text="Используйте основное меню:", reply_markup=markup)
            return

        location = api_response
        
        # --- ФОРМИРУЕМ ТЕКСТ ---
        text = (
            f"📍 <b>{location.get('name', 'Филиал')}</b>\n\n"
        )
        if location.get('address'):
             text += f"🗺️ <b>Адрес:</b>\n{location.get('address')}\n\n"
        if location.get('phone'):
             text += f"📞 <b>Телефон:</b> <code>{location.get('phone')}</code>\n"

        # --- ИСПРАВЛЕНИЕ: Добавляем кнопки ---
        keyboard = []
        if location.get('whatsapp_link'):
            keyboard.append([InlineKeyboardButton("💬 WhatsApp", url=location.get('whatsapp_link'))])
        if location.get('instagram_link'):
            keyboard.append([InlineKeyboardButton("📸 Instagram", url=location.get('instagram_link'))])
        if location.get('map_link'):
            keyboard.append([InlineKeyboardButton("🗺️ Показать на карте", url=location.get('map_link'))])
        
        # Добавляем кнопку "Назад", если филиалов было несколько
        # (Простая проверка: если у пользователя есть client_id, у него есть и user_data)
        if context.user_data.get('client_id'):
             keyboard.append([InlineKeyboardButton("⬅️ Назад к выбору", callback_data="contact_list_back")])

        reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        # Редактируем сообщение, показывая адрес И КНОПКИ
        await query.edit_message_text(
            text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=reply_markup_inline # <-- ИСПОЛЬЗУЕМ КНОПКИ
        )
        
        # (Больше не нужно отправлять "Используйте основное меню" отдельным сообщением)

    except (ValueError, IndexError, KeyError, TypeError) as e:
        logger.error(f"Ошибка обработки callback'а контакта: {e}. Callback data: {query.data}", exc_info=True)
        try:
            await query.edit_message_text(text="Произошла ошибка. Попробуйте снова нажать '🇰🇬 Наши контакты'.")
        except:
            pass # Если не удалось отредактировать
        await context.bot.send_message(chat_id=chat_id, text="Используйте основное меню:", reply_markup=markup)

# (Функция location_contact_back_callback удалена, т.к. мы используем API v5.0, где она не нужна)

async def location_contact_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (НОВАЯ) Возвращает пользователя к списку выбора филиалов (как в bishkek_contacts).
    """
    query = update.callback_query
    await query.answer()
    
    # Эта функция по сути заново вызывает bishkek_contacts,
    # но нам нужно отредактировать сообщение, а не отправлять новое.
    
    logger.info(f"Пользователь {query.from_user.id} нажал 'Назад' к списку контактов")
    
    try:
        # 1. Получаем список филиалов (Locations)
        api_locations = await api_request("GET", "/api/locations", params={})
        if not api_locations or "error" in api_locations or not isinstance(api_locations, list) or not api_locations:
             await query.edit_message_text("Ошибка: Не удалось загрузить список филиалов.")
             return

        locations = api_locations

        # 2. Получаем ОБЩИЕ контакты (WhatsApp/Instagram)
        keys_to_fetch = ['whatsapp_link', 'instagram_link', 'map_link']
        api_settings = await api_request("GET", "/api/settings", params={'keys': keys_to_fetch})
        
        settings_dict = {}
        if api_settings and "error" not in api_settings and isinstance(api_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_settings}

        # 3. Формирование кнопок (такое же, как в bishkek_contacts)
        keyboard = []
        for loc in locations:
            keyboard.append([InlineKeyboardButton(f"📍 {loc.get('name', 'Филиал')}", callback_data=f"contact_loc_{loc.get('id')}")])

        if settings_dict.get('whatsapp_link'): 
            keyboard.append([InlineKeyboardButton("💬 WhatsApp", url=settings_dict.get('whatsapp_link'))])
        if settings_dict.get('instagram_link'): 
            keyboard.append([InlineKeyboardButton("📸 Instagram", url=settings_dict.get('instagram_link'))])
        if settings_dict.get('map_link'): 
            keyboard.append([InlineKeyboardButton("🗺️ Общая Карта", url=settings_dict.get('map_link'))])

        reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # 4. Редактируем сообщение
        await query.edit_message_text(
            "🇰🇬 Выберите филиал для просмотра контактов или воспользуйтесь общими ссылками:", 
            reply_markup=reply_markup_inline
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка в location_contact_back_callback: {e}", exc_info=True)
        await query.edit_message_text("Произошла ошибка.")

# --- 10. НОВЫЕ Функции Владельца ---

async def owner_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог поиска 'Все Заказы'."""
    logger.info(f"Владелец {context.user_data.get('client_id')} начинает поиск по всем заказам.")
    await update.message.reply_text(
        "🔍 Введите трек-код, ФИО клиента или номер телефона для поиска заказа:",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return OWNER_ASK_ORDER_SEARCH # Переходим в состояние ожидания текста

async def handle_owner_order_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Обрабатывает поисковый запрос по заказам."""
    search_term = update.message.text
    employee_id = context.user_data.get('employee_id')
    markup = owner_main_menu_markup

    if not employee_id:
        logger.error(f"Ошибка поиска заказа: не найден employee_id для Владельца {context.user_data.get('client_id')}")
        await update.message.reply_text("Ошибка аутентификации Владельца. Попробуйте /start", reply_markup=markup)
        return ConversationHandler.END

    logger.info(f"Владелец (EID: {employee_id}) ищет заказы: '{search_term}'")
    await update.message.reply_text(f"Ищу заказы по запросу: '{search_term}'...", reply_markup=markup)

    # Вызываем API с аутентификацией Владельца
    api_response = await api_request(
        "GET", 
        "/api/orders",
        employee_id=employee_id, # <--- Аутентификация
        params={'q': search_term, 'company_id': COMPANY_ID_FOR_BOT, 'limit': 20}
    )

    if not api_response or "error" in api_response or not isinstance(api_response, list):
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API (Владелец /api/orders?q=...): {error_msg}")
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return ConversationHandler.END

    if not api_response:
        await update.message.reply_text(f"По запросу '{search_term}' заказы не найдены.", reply_markup=markup)
        return ConversationHandler.END

    # Форматируем ответ
    text = f"📦 <b>Найдено заказов ({len(api_response)} шт.):</b>\n\n"
    for order in api_response:
        client_info = order.get('client', {})
        client_name = client_info.get('full_name', 'Клиент ?')
        client_code = f"{client_info.get('client_code_prefix', '')}{client_info.get('client_code_num', '')}"
        
        text += f"<b>Трек:</b> <code>{order.get('track_code', '?')}</code>\n"
        text += f"<b>Клиент:</b> {html.escape(client_name)} ({client_code})\n"
        text += f"<b>Статус:</b> {order.get('status', '?')}\n"
        
        location = order.get('location') 
        if location:
            text += f"<b>Филиал:</b> {location.get('name', '?')}\n"

        calc_weight = order.get('calculated_weight_kg')
        calc_cost = order.get('calculated_final_cost_som')
        if calc_weight is not None and calc_cost is not None:
            text += f"<b>Расчет:</b> {calc_weight:.3f} кг / {calc_cost:.0f} сом\n"
        
        text += "──────────────\n"
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (слишком много результатов)"

    await update.message.reply_html(text, reply_markup=markup)
    return ConversationHandler.END

async def owner_clients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог поиска 'Клиенты'."""
    logger.info(f"Владелец {context.user_data.get('client_id')} начинает поиск по клиентам.")
    await update.message.reply_text(
        "🔍 Введите ФИО, код клиента или номер телефона для поиска:",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return OWNER_ASK_CLIENT_SEARCH

async def handle_owner_client_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Обрабатывает поисковый запрос по клиентам."""
    search_term = update.message.text
    employee_id = context.user_data.get('employee_id')
    markup = owner_main_menu_markup

    if not employee_id:
        logger.error(f"Ошибка поиска клиента: не найден employee_id для Владельца {context.user_data.get('client_id')}")
        await update.message.reply_text("Ошибка аутентификации Владельца. Попробуйте /start", reply_markup=markup)
        return ConversationHandler.END
        
    logger.info(f"Владелец (EID: {employee_id}) ищет клиентов: '{search_term}'")
    await update.message.reply_text(f"Ищу клиентов по запросу: '{search_term}'...", reply_markup=markup)

    api_response = await api_request(
        "GET", 
        "/api/clients/search", 
        employee_id=employee_id, 
        params={'q': search_term, 'company_id': COMPANY_ID_FOR_BOT}
    )
    
    if not api_response or "error" in api_response or not isinstance(api_response, list):
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API (Владелец /api/clients/search?q=...): {error_msg}")
        await update.message.reply_text(f"Ошибка: {error_msg}")
        return ConversationHandler.END

    if not api_response:
        await update.message.reply_text(f"По запросу '{search_term}' клиенты не найдены.", reply_markup=markup)
        return ConversationHandler.END

    text = f"👥 <b>Найдено клиентов ({len(api_response)} шт.):</b>\n\n"
    for client in api_response:
        client_name = client.get('full_name', 'Клиент ?')
        client_code = f"{client.get('client_code_prefix', '')}{client.get('client_code_num', '')}"
        tg_status = "Привязан" if client.get('telegram_chat_id') else "Нет"
        
        text += f"<b>ФИО:</b> {html.escape(client_name)}\n"
        text += f"<b>Код:</b> {client_code}\n"
        text += f"<b>Телефон:</b> <code>{client.get('phone', '?')}</code>\n"
        text += f"<b>Статус:</b> {client.get('status', 'Розница')}\n"
        text += f"<b>Telegram:</b> {tg_status}\n"
        text += "──────────────\n"

    await update.message.reply_html(text, reply_markup=markup)
    return ConversationHandler.END

async def owner_locations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Владелец) Показывает список его филиалов."""
    client_id = context.user_data.get('client_id')
    employee_id = context.user_data.get('employee_id')
    markup = owner_main_menu_markup

    # Для этого запроса Владельцу нужен employee_id для аутентификации
    if not employee_id:
         await update.message.reply_text("Ошибка аутентификации Владельца. Попробуйте /start", reply_markup=markup)
         return

    api_response = await api_request("GET", "/api/locations", employee_id=employee_id, params={'company_id': COMPANY_ID_FOR_BOT})

    if not api_response or "error" in api_response or not isinstance(api_response, list):
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка загрузки филиалов для Владельца {client_id}: {error_msg}")
        await update.message.reply_text(f"Ошибка загрузки филиалов: {error_msg}")
        return

    if not api_response:
        await update.message.reply_text("🏢 У вас пока не настроено ни одного филиала.")
        return

    text = "🏢 <b>Ваши филиалы:</b>\n\n"
    for i, loc in enumerate(api_response, 1):
        text += f"<b>{i}. {loc.get('name', 'Без имени')}</b>\n"
        if loc.get('address'):
            text += f"   <b>Адрес:</b> {loc.get('address')}\n"
        if loc.get('phone'):
            text += f"   <b>Телефон:</b> <code>{loc.get('phone')}</code>\n"
        text += "──────────\n"
    
    await update.message.reply_html(text, reply_markup=markup)

async def owner_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог 'Объявление' (Рассылка)."""
    logger.info(f"Владелец {context.user_data.get('client_id')} начинает рассылку.")
    await update.message.reply_text(
        "📢 Введите текст объявления для рассылки всем клиентам.\n"
        "Вы можете использовать <b>HTML</b>-теги (<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>).\n\n"
        "Для отмены нажмите 'Отмена'.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.HTML
    )
    return OWNER_ASK_BROADCAST_TEXT

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Получил текст рассылки, показывает превью и просит подтверждения."""
    broadcast_text = update.message.text
    # (Примечание: если нужен HTML, нужно отправлять update.message.text_html)
    context.user_data['broadcast_text'] = update.message.text_html # Сохраняем с HTML

    preview_message = (
        "Пожалуйста, проверьте ваше объявление:\n"
        "-----------------------------------\n"
        f"{broadcast_text}\n" # Показываем как простой текст в превью
        "-----------------------------------\n\n"
        "<b>Отправляем это сообщение (с форматированием) всем клиентам?</b>"
    )
    
    keyboard = [["Да, отправить"], ["Нет, отменить"]]
    await update.message.reply_html(
        preview_message,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return OWNER_CONFIRM_BROADCAST

async def handle_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Обрабатывает подтверждение рассылки."""
    answer = update.message.text
    employee_id = context.user_data.get('employee_id')
    markup = owner_main_menu_markup
    
    if answer != "Да, отправить":
        await update.message.reply_text("Рассылка отменена.", reply_markup=markup)
        context.user_data.pop('broadcast_text', None)
        return ConversationHandler.END

    if not employee_id:
        logger.error(f"Ошибка рассылки: не найден employee_id для Владельца {context.user_data.get('client_id')}")
        await update.message.reply_text("Ошибка аутентификации Владельца. Попробуйте /start", reply_markup=markup)
        return ConversationHandler.END

    broadcast_text_html = context.user_data.get('broadcast_text')
    if not broadcast_text_html:
        await update.message.reply_text("Ошибка: текст рассылки потерян. Попробуйте снова.", reply_markup=markup)
        return ConversationHandler.END

    await update.message.reply_text("⏳ Запускаю рассылку... Это может занять несколько минут.", reply_markup=markup)
    
    api_response = await api_request(
        "POST", 
        "/api/bot/broadcast",
        employee_id=employee_id, # <--- Аутентификация
        json={'text': broadcast_text_html, 'company_id': COMPANY_ID_FOR_BOT}
    )
    
    context.user_data.pop('broadcast_text', None)

    if not api_response or "error" in api_response:
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API (Владелец /api/bot/broadcast): {error_msg}")
        await update.message.reply_text(f"❌ Ошибка при запуске рассылки: {error_msg}")
    else:
        sent_count = api_response.get('sent_to_clients', 0)
        logger.info(f"Рассылка Владельца (EID: {employee_id}) завершена. Отправлено: {sent_count}")
        await update.message.reply_text(f"✅ Рассылка успешно отправлена {sent_count} клиентам.")
        
    return ConversationHandler.END


# --- 11. Отмена диалога ---

async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена любого диалога ConversationHandler."""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} отменил диалог.")
    
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup
    message_text = "Действие отменено."

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(message_text, reply_markup=None)
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение при отмене callback'а: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Возврат в главное меню.", reply_markup=markup)
    else:
        await update.message.reply_text(message_text, reply_markup=markup)

    # Очистка ВСЕХ временных данных
    keys_to_clear = [
        'location_id', 'track_code', 'comment', 'available_locations', 
        'phone_to_register', 'broadcast_text'
    ]
    for key in keys_to_clear:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END


# bot_template.py

# --- НОВАЯ ФУНКЦИЯ ВЫХОДА ---
async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обрабатывает команду /logout.
    Отвязывает Telegram ID от клиента через API и очищает user_data.
    """
    user = update.effective_user
    chat_id = str(user.id)
    client_id = context.user_data.get('client_id')

    if not client_id:
        logger.info(f"Пользователь {chat_id} уже вышел (/logout)")
        await update.message.reply_text(
            "Вы уже вышли из системы.\nНажмите /start, чтобы войти.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    logger.info(f"Пользователь {chat_id} (ClientID: {client_id}) выходит из системы...")

    # 1. Вызываем API, чтобы отвязать аккаунт
    api_response = await api_request(
        "POST",
        "/api/bot/unlink",
        json={"telegram_chat_id": chat_id, "company_id": COMPANY_ID_FOR_BOT}
    )

    if not api_response or "error" in api_response:
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API при вызове /api/bot/unlink: {error_msg}")
        # (Даже если API ответил ошибкой, мы все равно очистим сессию бота)
    
    # 2. Очищаем локальную сессию бота
    context.user_data.clear()
    
    await update.message.reply_text(
        "✅ Вы успешно вышли из системы.\n\n"
        "Чтобы войти снова, нажмите /start и введите ваш номер телефона.",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Завершаем все диалоги, если вдруг были в них
    return ConversationHandler.END
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---


# --- 12. Запуск Бота ---

def main() -> None:
    """Главная функция запуска бота."""
    
    # --- Идентифицируем бота ПЕРЕД запуском ---
    identify_bot_company()
    # (Если ошибка, sys.exit(1) уже остановил программу)

    logger.info(f"Запуск бота для компании '{COMPANY_NAME_FOR_BOT}' (ID: {COMPANY_ID_FOR_BOT})...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Диалог Регистрации/Идентификации ---
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)], 
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_input)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_get_name)],
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
        per_user=True, per_chat=True, name="registration",
    )
    
    # --- Диалог добавления заказа ---
    add_order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^➕ Добавить заказ$'), add_order_start)],
        states={
            ADD_ORDER_LOCATION: [CallbackQueryHandler(add_order_received_location, pattern=r'^loc_')],
            ADD_ORDER_TRACK_CODE: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_order_received_track_code)
            ],
            ADD_ORDER_COMMENT: [
                MessageHandler(filters.Regex('^⏩ Пропустить$'), add_order_skip_comment),
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_order_received_comment)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_dialog), 
            MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
            CallbackQueryHandler(cancel_dialog, pattern='^cancel_add_order$')
        ],
        per_user=True, per_chat=True, name="add_order",
    )
    
    # --- НОВЫЕ ДИАЛОГИ ВЛАДЕЛЬЦА ---
    owner_all_orders_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📦 Все Заказы$'), owner_all_orders)],
        states={
            OWNER_ASK_ORDER_SEARCH: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_owner_order_search)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
        per_user=True, per_chat=True, name="owner_search_orders",
    )

    owner_clients_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^👥 Клиенты$'), owner_clients)],
        states={
            OWNER_ASK_CLIENT_SEARCH: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_owner_client_search)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
        per_user=True, per_chat=True, name="owner_search_clients",
    )

    owner_broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📢 Объявление$'), owner_broadcast_start)],
        states={
            OWNER_ASK_BROADCAST_TEXT: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text)
            ],
            OWNER_CONFIRM_BROADCAST: [
                MessageHandler(filters.Regex('^Нет, отменить$'), cancel_dialog),
                MessageHandler(filters.Regex('^Да, отправить$'), handle_broadcast_confirm)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
        per_user=True, per_chat=True, name="owner_broadcast",
    )
    
    # --- Регистрация обработчиков ---
    
    # Сначала диалоги (они имеют приоритет)
    application.add_handler(registration_conv)
    application.add_handler(add_order_conv)
    application.add_handler(owner_all_orders_conv)
    application.add_handler(owner_clients_conv)
    application.add_handler(owner_broadcast_conv)

    # Инлайн-кнопки контактов
    application.add_handler(CallbackQueryHandler(location_contact_callback, pattern=r'^contact_loc_'))
    application.add_handler(CallbackQueryHandler(location_contact_back_callback, pattern=r'^contact_list_back$'))
    application.add_handler(CommandHandler('logout', logout))
    # (Убрали back_callback, т.к. в этой версии он не нужен)

    # Команда /cancel вне диалогов
    application.add_handler(CommandHandler('cancel', cancel_dialog))

    # Обработчик ВСЕХ ОСТАЛЬНЫХ текстовых сообщений (меню)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info(f"Бот (ID: {COMPANY_ID_FOR_BOT}) запущен и готов к работе...")
    application.run_polling()
    

if __name__ == "__main__":
    main()

