#!/usr/bin/env python
# -*- coding: utf-8 -*-
# bot_template.py (Версия 6.0 - Полный переход на API + Функции Владельца)

import os
import httpx # Используется для API запросов
import re    # <-- ДОБАВЛЕНО (для "Экстрасенса")
import re    # Используется для очистки номера телефона
import sys  # Для sys.exit()
import logging
import asyncio
import html # Для форматирования ответов
import asyncio
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta, date
import json # <-- Добавляем json
from ai_brain import get_ai_response, AI_CLIENT_PROMPT, AI_OWNER_PROMPT # <-- Импортируем оба промпта
from ai_tools import execute_ai_tool # <-- Убрали старый промпт
import openpyxl

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

async def keep_typing(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача: отправляет статус 'печатает...' каждые 4 сек."""
    chat_id = context.job.chat_id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

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
    ["📊 Статистика", "🇰🇬 Наши контакты"], # <-- ИЗМЕНЕНО
    ["🇨🇳 Адреса складов"] # <-- Перенесено
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

    # Диалоги Владельца
    OWNER_ASK_ORDER_SEARCH,
    OWNER_ASK_CLIENT_SEARCH,
    OWNER_ASK_BROADCAST_PHOTO,
    OWNER_ASK_BROADCAST_TEXT,
    OWNER_REASK_BROADCAST_TEXT,
    OWNER_CONFIRM_BROADCAST,
    
    # Импорт Excel
    OWNER_WAIT_IMPORT_DATE # <-- НОВОЕ (12-е состояние)

) = range(12) # Теперь 11 состояний

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

# --- НОВАЯ ФУНКЦИЯ: Проверка AI-Рубильника ---
async def is_ai_enabled() -> bool:
    """
    Проверяет статус AI-Рубильника (ai_enabled) для текущей компании.
    """
    global COMPANY_ID_FOR_BOT
    
    # Запрашиваем только AI-Рубильник
    keys_to_fetch = ['ai_enabled'] 
    
    # Используем публичный эндпоинт для бота
    api_settings = await api_request(
        "GET", 
        "/api/bot/settings", 
        params={'company_id': COMPANY_ID_FOR_BOT, 'keys': keys_to_fetch}
    )
    
    if api_settings and "error" not in api_settings and isinstance(api_settings, list):
        settings_dict = {s.get('key'): s.get('value') for s in api_settings}
        # AI включен, если значение 'ai_enabled' равно строке 'True' или 'true'
        return settings_dict.get('ai_enabled') in ['True', 'true']
    
    logger.error("Не удалось получить статус AI-Рубильника. Предполагаем, что AI отключен.")
    return False

# --- НОВАЯ ФУНКЦИЯ (ЗАГЛУШКА): Уведомление Владельца о Жалобе ---
async def notify_owner_of_complaint(context: ContextTypes.DEFAULT_TYPE, complaint_text: str):
    """
    Заглушка: Отправляет уведомление в Telegram Владельцу компании.
    """
    logger.info(f"НОТИФИКАЦИЯ ЖАЛОБЫ (ЗАГЛУШКА): Текст: {complaint_text}")
    # TODO: Реализовать получение telegram_chat_id Владельца и отправку сообщения
    pass
# --- КОНЕЦ ЗАГЛУШКИ ---

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

async def check_restart_or_get_client_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    (CRITICAL) Проверяет, был ли бот перезапущен (потеря context.user_data).
    Если да, просит пользователя нажать /start.
    Если нет, возвращает client_id.
    """
    client_id = context.user_data.get('client_id')
    
    if client_id is None:
        # (Проверяем, что это не /start или /register, хотя сюда они и так не попадут)
        text = update.message.text if update.message else ""
        if text not in ['/start', '/register']:
            logger.warning(f"[Restart Check] client_id отсутствует для Chat ID {update.effective_user.id}. Просим нажать /start.")
            await update.message.reply_html(
                "<b>Бот был обновлен!</b> 🚀\n\n"
                "Пожалуйста, нажмите /start, чтобы обновить ваше меню и продолжить работу.",
                reply_markup=ReplyKeyboardMarkup([["/start"]], resize_keyboard=True, one_time_keyboard=True)
            )
        return None # Возвращаем None, сигнализируя о сбое
    
    return client_id # Возвращаем ID, если все в порядке


# --- 5. Диалог Регистрации (ПОЛНОСТЬЮ ПЕРЕПИСАН) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /start - Просто вход.
    Если клиент есть -> Приветствуем.
    Если гость -> Приветствуем и включаем ИИ (НЕ требуем телефон сразу).
    """
    user = update.effective_user
    chat_id = str(user.id)
    logger.info(f"Команда /start от {user.full_name} (ID: {chat_id})")

    # Проверяем юзера (тихо)
    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", 
        json={"telegram_chat_id": chat_id, "company_id": COMPANY_ID_FOR_BOT} 
    )

    if api_response and "error" not in api_response:
        # --- КЛИЕНТ НАЙДЕН ---
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False)
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data['employee_id'] = api_response.get("employee_id")

        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        role_text = " (Владелец)" if is_owner else ""
        await update.message.reply_html(
            f"👋 Здравствуйте, <b>{client_data.get('full_name')}</b>{role_text}!\n\nРад вас видеть! Используйте меню или напишите что вы хотите наш ИИ менеджер ответит на все ваши вопросы.",
            reply_markup=markup
        )
    else:
        # --- ГОСТЬ (НЕ НАЙДЕН) ---
        # ВАЖНО: Мы НЕ требуем телефон, а просто здороваемся и разрешаем общаться с ИИ
        context.user_data['client_id'] = None
        await update.message.reply_html(
            "👋 Здравствуйте! Я — ИИ-помощник Карго.\n\n"
            "Вы пока не зарегистрированы, но можете задавать мне вопросы!\n"
            "📦 Если захотите добавить заказы — нажмите /register.",
            reply_markup=ReplyKeyboardRemove()
        )

    return ConversationHandler.END # <-- САМОЕ ГЛАВНОЕ: Не захватываем пользователя!

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /register - Принудительная регистрация.
    Вот тут мы уже требуем телефон.
    """
    await update.message.reply_text(
        "📝 Регистрация нового клиента.\n\n"
        "Пожалуйста, введите ваш номер телефона (начиная с 0 или 996).",
        reply_markup=ReplyKeyboardRemove()
    )
    return ASK_PHONE # <-- Вот тут захватываем пользователя

async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Умная проверка номера.
    Если не найден -> Мягко предлагаем варианты, а не посылаем.
    """
    user = update.effective_user
    chat_id = str(user.id)
    phone_number_text = update.message.text 
    normalized_phone = normalize_phone_number(phone_number_text)
    
    if not normalized_phone:
         await update.message.reply_text(f"Не удалось распознать номер. Попробуйте цифрами (например: 0555123456).", reply_markup=ReplyKeyboardRemove())
         return ASK_PHONE 

    logger.info(f"Проверка номера {normalized_phone} для {user.full_name}")

    api_response = await api_request(
        "POST",
        "/api/bot/identify_user", 
        json={"telegram_chat_id": chat_id, "phone_number": normalized_phone, "company_id": COMPANY_ID_FOR_BOT}
    )

    if api_response and "error" not in api_response:
        # --- УСПЕХ ---
        client_data = api_response.get("client")
        is_owner = api_response.get("is_owner", False)
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = is_owner
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data['employee_id'] = api_response.get("employee_id")

        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
        role_text = " (Владелец)" if is_owner else ""
        await update.message.reply_html(
            f"🎉 Отлично, <b>{client_data.get('full_name')}</b>{role_text}! Аккаунт найден.\nТеперь вам доступны все функции!",
            reply_markup=markup
        )
        return ConversationHandler.END

    elif api_response and api_response.get("status_code") == 404:
        # --- 404: УМНЫЙ ОТВЕТ ---
        context.user_data['phone_to_register'] = normalized_phone
        
        await update.message.reply_html(
            f"😕 Хм, номер <code>{normalized_phone}</code> в базе не найден.\n\n"
            f"☝️ <b>Если вы уже работали с нами:</b>\n"
            f"Возможно, вы сдавали груз под <b>другим номером</b>? Попробуйте вспомнить и отправить тот номер.\n\n"
            f"🆕 <b>Если вы у нас впервые:</b>\n"
            f"Давайте создадим вам новый аккаунт! Просто отправьте ваше <b>Имя (ФИО)</b> в ответ.",
            reply_markup=ReplyKeyboardRemove()
        )
        # Мы все равно переходим к GET_NAME, но текст подсказывает пользователю, что делать
        return GET_NAME 

    else:
        await update.message.reply_text("Ошибка соединения с сервером. Попробуйте позже /start.")
        return ConversationHandler.END

async def register_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Получает ФИО и регистрирует клиента.
    ИСПРАВЛЕНО: Теперь понимает слова отмены.
    """
    full_name = update.message.text.strip()
    phone_to_register = context.user_data.get('phone_to_register')
    user = update.effective_user
    chat_id = str(user.id)

    # --- 1. ЗАЩИТА ОТ "ОТМЕНА ПОТОМ НАПИШУ" (РУС + KG) ---
    stop_words = [
        # Русский
        'отмена', 'стоп', 'позже', 'потом', 'нет', 'не хочу', 'cancel', 'назад', 
        'подожди', 'минутку', 'стой', 'передумал', 'не надо', 'выход',
        # Кыргызский
        'жок', 'кийин', 'токто', 'керек эмес', 'азыр эмес', 'күтө тур', 'кой', 'болду', 'чыгуу'
    ]
    
    # Проверка: если хотя бы одно слово найдено в тексте
    if any(word in full_name.lower() for word in stop_words):
        await update.message.reply_text(
            "Хорошо, регистрация отменена. 👌 / Макул, токтоттук.\n\n"
            "Вы остаетесь в гостевом режиме. Когда будете готовы — нажмите /register.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.pop('phone_to_register', None)
        return ConversationHandler.END
    # -------------------------------------------

    if not phone_to_register:
        await update.message.reply_text("Произошла ошибка. Начните с /register.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if len(full_name) < 2:
         await update.message.reply_text("Имя слишком короткое. Пожалуйста, введите полное Имя.")
         return GET_NAME 

    # Далее стандартная логика регистрации...
    logger.info(f"Попытка регистрации: Имя='{full_name}', Телефон='{phone_to_register}'")
    
    payload = {
        "full_name": full_name,
        "phone": phone_to_register,
        "company_id": COMPANY_ID_FOR_BOT,
        "telegram_chat_id": chat_id,
        "client_code_prefix": "TG" # Или логика префикса из прошлых шагов
    }
    
    api_response = await api_request("POST", "/api/bot/register_client", json=payload)

    if api_response and "error" not in api_response and "id" in api_response:
        client_data = api_response 
        context.user_data['client_id'] = client_data.get("id")
        context.user_data['is_owner'] = False
        context.user_data['full_name'] = client_data.get("full_name")
        context.user_data.pop('phone_to_register', None)

        client_code = f"{client_data.get('client_code_prefix', 'TG')}{client_data.get('client_code_num', '?')}"
        
        await update.message.reply_html(
            f"✅ <b>Регистрация успешна, {html.escape(full_name)}!</b>\n\n"
            f"Ваш код: <b>{client_code}</b>\n\n"
            "Теперь вам доступны все функции бота!",
            reply_markup=client_main_menu_markup
        )
        return ConversationHandler.END
    else:
        error_msg = api_response.get("error", "Неизвестная ошибка.") if api_response else "Сервер недоступен."
        await update.message.reply_text(f"Ошибка при регистрации: {error_msg}")
        return ConversationHandler.END

# --- 6. Диалог добавления заказа (ПЕРЕПИСАН НА API) ---

async def add_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления заказа, спрашивает филиал (через API)."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return ConversationHandler.END
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

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
    """
    (ВЕРСИЯ 6.2 - УДАЛЕНА СТРОГАЯ ПРОВЕРКА)
    1. Парсит трек-коды и привязанные к ним комментарии.
    2. Если кодов > 1: отправляет массово.
    3. Если код == 1: работает по старой логике (магия -> запрос комментария).
    """
    global COMPANY_ID_FOR_BOT
    text_input = update.message.text.strip()
    client_id = context.user_data.get('client_id')
    location_id = context.user_data.get('location_id')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    if not client_id or not location_id:
         await update.message.reply_text("Ошибка: Потеряны данные сессии. Начните сначала с /start.", reply_markup=markup)
         return ConversationHandler.END

    # --- ЛОГИКА "ЭКСТРАСЕНС" ---

    # 1. Находим все "слова", похожие на трек-код (8-25 букв/цифр)
    track_codes_found = re.findall(r'(\b[a-zA-Z0-9]{8,25}\b)', text_input)

    # Если кодов не найдено, возвращаемся к ожиданию ввода
    if not track_codes_found:
        logger.warning(f"Клиент {client_id} ввел 'мусор', трек-коды не найдены. Текст: {text_input[:100]}")
        await update.message.reply_html(
            "❗️ <b>Ошибка:</b> Я не смог найти в вашем тексте ничего, похожего на трек-код (8-25 букв/цифр).\n\n"
            "Пожалуйста, введите **один** трек-код или **список** трек-кодов."
        )
        return ADD_ORDER_TRACK_CODE # Остаемся ждать

    # 2. Парсим комментарии, используя найденные треки как разделители
    parts_with_tracks = re.split(r'(\b[a-zA-Z0-9]{8,25}\b)', text_input)

    items_to_add = {} # {track_code: comment}
    last_track = None

    for part in parts_with_tracks:
        if part in track_codes_found:
            last_track = part
            if last_track not in items_to_add:
                 items_to_add[last_track] = "" 
        elif last_track is not None:
            items_to_add[last_track] += part
            
    # 3. Финальная очистка и дедупликация
    final_items = {} 
    for track, comment in items_to_add.items():
        clean_comment = comment.strip().rstrip('.,;:')
        if track not in final_items:
             final_items[track] = clean_comment or None 
    
    items_list = [{"track_code": code, "comment": comment} for code, comment in final_items.items()]
    
    
    # --- Сценарий 1: Массовая загрузка (найдено > 1 кода) ---
    if len(items_list) > 1:
        logger.info(f"Клиент {client_id} запустил МАССОВУЮ загрузку. Найдено {len(items_list)} трек-кодов с комментариями.")

        await update.message.reply_text(f"✅ Понял. Нашел {len(items_list)} трек-кодов. Обрабатываю... Ожидайте.")

        payload = {
            "client_id": client_id,
            "location_id": location_id,
            "company_id": COMPANY_ID_FOR_BOT,
            "items": items_list
        }

        api_response = await api_request("POST", "/api/bot/bulk_add_orders", json=payload)

        if not api_response or "error" in api_response:
            error_msg = api_response.get("error", "Неизвестная ошибка") if api_response else "Нет ответа"
            logger.error(f"Ошибка API /api/bot/bulk_add_orders: {error_msg}")
            await update.message.reply_text(f"❌ Произошла ошибка при массовом добавлении: {error_msg}", reply_markup=markup)
        else:
            created = api_response.get("created", 0)
            assigned = api_response.get("assigned", 0)
            skipped = api_response.get("skipped", 0)

            response_text = f"✅ <b>Готово!</b>\n\n"
            if created > 0:
                response_text += f"✔️ Новых заказов добавлено: <b>{created}</b>\n"
            if assigned > 0:
                response_text += f"✨ Найдено и присвоено вам (невостребованных): <b>{assigned}</b>\n"
            if skipped > 0:
                response_text += f"⚠️ Пропущено (дубликаты): <b>{skipped}</b>\n"

            await update.message.reply_html(response_text, reply_markup=markup)

        context.user_data.pop('location_id', None)
        context.user_data.pop('available_locations', None)
        return ConversationHandler.END

    # --- Сценарий 2: Одиночный заказ (найден == 1 код) ---
    elif len(items_list) == 1:
        item = items_list[0]
        track_code = item['track_code']
        comment_from_text = item['comment']

        logger.info(f"Клиент {client_id} ввел ОДИНОЧНЫЙ трек-код. Текст: {comment_from_text}")

        # 3. "Магия" (поиск невостребованных) - ПЕРВЫЙ ПРИОРИТЕТ
        claim_payload = {
            "track_code": track_code,
            "client_id": client_id,
            "company_id": COMPANY_ID_FOR_BOT
        }
        api_response = await api_request(
            "POST",
            "/api/bot/claim_order",
            json=claim_payload
        )

        if api_response and "error" not in api_response and "id" in api_response:
            # 1. УСПЕХ! Заказ найден и присвоен (Магия сработала)
            logger.info(f"МАГИЯ: Невостребованный заказ (ID: {api_response.get('id')}) назначен клиенту {client_id}")
            await update.message.reply_html(
                f"🎉 <b>Отличные новости!</b>\n\nМы нашли этот заказ (<code>{track_code}</code>) в нашей базе невостребованных посылок и <b>сразу присвоили его вам!</b> Теперь он отображается в вашем списке заказов.",
                reply_markup=markup
            )
            context.user_data.pop('location_id', None)
            context.user_data.pop('available_locations', None)
            return ConversationHandler.END

        else:
            # 2. Не найден (или ошибка "магии") -> Проверка на ДУБЛИКАТ, чтобы избежать создания нового.
            
            # НОВЫЙ ШАГ: Проверка, существует ли этот трек-код в системе вообще
            search_response = await api_request(
                 "GET",
                 "/api/orders", # Используем общий эндпоинт поиска
                 params={"q": track_code, "company_id": COMPANY_ID_FOR_BOT, "limit": 1}
            )

            if search_response and isinstance(search_response, list) and len(search_response) > 0:
                 # Найден дубликат (принадлежит кому-то или "Неизвестному", но магия не сработала)
                 order_status = search_response[0].get("status", "Неизвестен")
                 
                 await update.message.reply_html(
                      f"⚠️ <b>Внимание:</b> Заказ с трек-кодом <code>{track_code}</code> уже зарегистрирован в нашей системе. "
                      f"Его текущий статус: <b>{order_status}</b>. "
                      f"Повторное добавление невозможно. Если вы считаете, что это ваш заказ, обратитесь к менеджеру для ручного присвоения."
                 )
                 # Сбрасываем разговор, чтобы не создавать дубликат
                 context.user_data.pop('location_id', None)
                 context.user_data.pop('available_locations', None)
                 return ConversationHandler.END 
            
            # Если не найден (проверка на дубликат прошла успешно) -> Спрашиваем комментарий для создания нового.
            logger.info(f"Заказ '{track_code}' не найден. Продолжаем создание нового.")
            context.user_data['track_code'] = track_code
            
            # Если в тексте уже был комментарий, сразу его используем и пропускаем шаг
            if comment_from_text:
                context.user_data['comment'] = comment_from_text
                return await save_order_from_bot(update, context)
            
            # Если комментария не было, спрашиваем его
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
    """
    (ИСПРАВЛЕНО 16.11) Финальный шаг: сохраняет новый заказ в базе через API,
    вызывая СТАНДАРТНЫЙ эндпоинт /api/orders.
    """
    global COMPANY_ID_FOR_BOT
    
    client_id = context.user_data.get('client_id')
    track_code = context.user_data.get('track_code')
    location_id = context.user_data.get('location_id')
    comment = context.user_data.get('comment')
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    # Очистка комментария
    if comment == "⏩ Пропустить":
         final_comment = None
    else:
         final_comment = comment

    if not track_code or not client_id or not location_id:
        await update.message.reply_text("❌ Ошибка: Потеряны данные для сохранения заказа (нет ID клиента, трека или филиала). Попробуйте /start", reply_markup=markup)
        logger.error(f"Ошибка сохранения заказа: Не хватает данных. client={client_id}, loc={location_id}, track={track_code}")
        # Очистка
        for key in ['location_id', 'track_code', 'comment', 'available_locations']:
            context.user_data.pop(key, None)
        return ConversationHandler.END

    # 2. Формируем и отправляем Payload
    payload = {
        "track_code": track_code,
        "client_id": client_id,
        "company_id": COMPANY_ID_FOR_BOT,
        "location_id": location_id,
        "comment": final_comment,
        "purchase_type": "Доставка", # Всегда доставка из бота
        "party_date": date.today().isoformat() # Сегодняшняя дата
    }
    
    api_response = await api_request(
        "POST", 
        "/api/orders",  # <-- ИСПОЛЬЗУЕМ СТАНДАРТНЫЙ ЭНДПОИНТ
        json=payload
    )

    # 3. Обработка ответа
    if api_response and "error" not in api_response and "id" in api_response:
        # УСПЕХ: Заказ создан
        await update.message.reply_html(
            f"✅ <b>Заказ добавлен!</b>\n\nТрек-код: <code>{track_code}</code>\n"
            f"Теперь вы можете отслеживать его в разделе 'Мои заказы'.",
            reply_markup=markup
        )
    else:
        # ОШИБКА: Сервер вернул ошибку
        error_msg = api_response.get("error", "Неизвестная ошибка сервера.") if api_response else "Нет ответа от API."
        logger.error(f"Ошибка сохранения заказа для клиента {client_id}: {error_msg}")
        await update.message.reply_html(
            f"❌ <b>Не удалось добавить заказ!</b>\n"
            f"Ошибка: {error_msg}",
            reply_markup=markup
        )
        
    # Сброс данных сессии
    for key in ['track_code', 'comment', 'location_id', 'available_locations']:
        context.user_data.pop(key, None)
        
    return ConversationHandler.END


# --- ОБРАБОТЧИКИ ТЕКСТА И ГОЛОСА ---

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обертка для голоса (Google Speech Free)."""
    voice = await update.message.voice.get_file()
    # Имя файла
    path = f"voice_{update.message.id}_{update.effective_user.id}.ogg"
    
    try:
        # 1. Показываем, что бот "слушает" (загружает файл)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_voice")
        msg = await update.message.reply_text("👂 Слушаю...")
        
        # Скачиваем файл
        await voice.download_to_drive(path)
        
        # Импортируем НАШУ НОВУЮ функцию Google
        from ai_brain import transcribe_audio_google
        
        # Распознаем
        text = await transcribe_audio_google(path)
        
        # Удаляем сообщение "Слушаю..."
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
        except: pass
        
        if not text: 
            await update.message.reply_text("🤷‍♂️ Не удалось разобрать слова. Попробуйте четче.")
            return
            
        # 2. Показываем, что услышали
        await update.message.reply_text(f"🗣 <b>Вы сказали:</b>\n<i>«{text}»</i>", parse_mode=ParseMode.HTML)
        
        # 3. САМОЕ ГЛАВНОЕ: Показываем статус "Печатает...", пока ИИ думает
        # Это даст пользователю понять, что процесс идет
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # 4. Отправляем текст в главную логику
        await process_text_logic(update, context, text)
        
    except Exception as e:
        # --- (ИСПРАВЛЕНО) УЛУЧШЕННОЕ ЛОГИРОВАНИЕ ---
        import traceback
        logger.error(f"!!! [Voice Error] Произошла критическая ошибка при обработке голоса:")
        logger.error(traceback.format_exc()) # <-- ЭТО ПОКАЖЕТ НАМ ПРИЧИНУ
        # --- (КОНЕЦ ИСПРАВЛЕНИЯ) ---
        await update.message.reply_text("Ошибка обработки голосового.")
    finally: 
        # Удаляем скачанный файл
        if os.path.exists(path): 
            try: os.remove(path)
            except: pass

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ВОССТАНОВЛЕНО) Обертка для текстовых сообщений.
    Извлекает текст и передает его в process_text_logic.
    """
    user_text = update.message.text
    if not user_text: 
        return
    # Передаем текст в главную логику
    await process_text_logic(update, context, user_text.strip())

async def notify_progress(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """
    Фоновая задача: отправляет успокаивающие сообщения, если ИИ думает долго.
    """
    try:
        # > 3 секунды
        await asyncio.sleep(3)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await context.bot.send_message(chat_id=chat_id, text="Секундочку, печатаю... ✍️")

        # > 10 секунд (3 + 7)
        await asyncio.sleep(7)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await context.bot.send_message(chat_id=chat_id, text="Собираю информацию, чтобы дать подробный ответ. Подождите немного... 🧐")

        # > 25 секунд (10 + 15)
        await asyncio.sleep(15)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await context.bot.send_message(chat_id=chat_id, text="Вопрос сложный, но я работаю над ним! Скоро вернусь к вам. ⏳")

    except asyncio.CancelledError:
        # Задача отменена (значит, ИИ успел ответить), ничего не делаем
        pass

# --- 7. Обработчик текстовых сообщений (МАРШРУТИЗАТОР) ---

import ast # Добавь этот импорт в начало файла, если его нет!

async def process_text_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """
    (ФИНАЛ v8.0 - ГИБРИДНЫЙ ИНТЕЛЛЕКТ)
    Обрабатывает текст от пользователя или Агрегатора.
    1. Проверяет трек-коды (с защитой от слов типа 'WhatsApp').
    2. Формирует контекст (Сыворотка Правды).
    3. Получает ответ ИИ.
    4. Если ИИ вернул Текст + Инструмент -> Сначала отправляет текст, потом выполняет инструмент.
    """
    from ai_brain import AI_CLIENT_PROMPT, AI_OWNER_PROMPT # <-- Импортируем ОБА промпта
    import ast
    import json
    import html

    if not text:
        logger.warning("process_text_logic получила пустой текст. Игнорируем.")
        return

    user = update.effective_user
    client_id = context.user_data.get('client_id')
    employee_id = context.user_data.get('employee_id')
    is_owner = context.user_data.get('is_owner', False)
    chat_id = update.effective_chat.id
    
    # === 1. ПРОВЕРКА ПЕРЕЗАПУСКА ===
    if client_id is None and text.strip() not in ['/start', '/register']:
        logger.warning(f"[Restart Check] client_id отсутствует для Chat ID {user.id}. Вероятен перезапуск.")
        await update.message.reply_html(
            "<b>Бот был обновлен!</b> 🚀\n\n"
            "Пожалуйста, нажмите /start, чтобы обновить ваше меню и продолжить работу.",
            reply_markup=ReplyKeyboardMarkup([["/start"]], resize_keyboard=True, one_time_keyboard=True)
        )
        return 

    # 2. ИНДИКАТОР РЕАКЦИИ
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    if client_id:
        markup = owner_main_menu_markup if is_owner else client_main_menu_markup
    else:
        markup = ReplyKeyboardRemove()

    # 3. ПРОВЕРКА РУБИЛЬНИКА (AI Toggle)
    if not (await is_ai_enabled()):
        if not client_id:
             await update.message.reply_text("Здравствуйте! Для начала работы нажмите /register.", reply_markup=ReplyKeyboardRemove())
        else:
             await update.message.reply_text("Неизвестная команда. Используйте кнопки.", reply_markup=markup)
        return

    # 4. АВТО-ПЕРЕХВАТ ТРЕК-КОДОВ
    potential_tracks = re.findall(r'\b[a-zA-Z0-9]{8,25}\b', text)
    valid_tracks = [t for t in potential_tracks if any(char.isdigit() for char in t)]

    # --- УМНЫЙ ФИЛЬТР (ИСПРАВЛЕНИЕ) ---
    # Если пишет Владелец и код ВСЕГО ОДИН, мы НЕ перехватываем его (чтобы ИИ мог ответить на вопрос "Чей это заказ?").
    # Если кодов много -> это точно массовое добавление.
    # --- УМНЫЙ ФИЛЬТР v2 (ЗАЩИТА ОТ ТУПОСТИ) ---
    should_intercept = True
    
    # 1. Если пишет Владелец и код ВСЕГО ОДИН -> это поиск, не перехватываем.
    if is_owner and len(valid_tracks) == 1:
         should_intercept = False

    # 2. (НОВОЕ) Если в тексте есть СЛОВА-КОМАНДЫ -> это действие, не перехватываем.
    stop_words = [
        # --- Арифметика ---
        "посчитать", "расчет", "вес", "кг", "сумма", "сколько", 
        # --- Действия ---
        "удалить", "убрать", "отмена", 
        "статус", "перенести", "изменить", "сменить", "вернуть", "поставить", "отметить", "сделай",
        # --- Поиск ---
        "чей", "кто", "где", "инфо", "покажи", "список",
        # --- Английский ---
        "calculate", "weight", "delete", "status",
        
        # --- [NEW] СТАТУСЫ (Самое важное!) ---
        "в пути", "на складе", "выдан", "готов", "обработке", "выкуп", "прибыли", "уехали", "пришли","выехали", 
        
        # --- [NEW] РАЗГОВОРНЫЕ МАРКЕРЫ ---
        "эти", "вот", "оказ", "они", "их" 
    ]
    
    text_lower = text.lower()
    # Проверка: Если есть хоть одно стоп-слово -> Отключаем перехватчик добавления
    if any(word in text_lower for word in stop_words):
        should_intercept = False
        logger.info(f"Intercept Cancelled: найдены командные слова или статусы. Передаю ИИ.")

    # ----------------------------------

    # Если фильтр пропустил (True) -> Пытаемся добавить заказы
    if valid_tracks and len(valid_tracks) >= 1 and should_intercept:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            # ... (дальше идет старый код api_request "check_only") ...
            # 1. ДЕЛАЕМ ПРОВЕРКУ (Check Only)
            api_response = await api_request("POST", "/api/bot/order_request", json={
                "client_id": client_id, 
                "company_id": COMPANY_ID_FOR_BOT, 
                "request_text": text,
                "check_only": True 
            })
            
            if not api_response or "error" in api_response:
                return # Пропускаем к ИИ при ошибке

            if api_response.get("status") == "check_result":
                stats = api_response.get("stats", {})
                groups = api_response.get("groups", {})
                
                # --- ФОРМИРУЕМ КРАСИВЫЙ ОТЧЕТ ПО БЛОКАМ ---
                msg = f"🔎 <b>Я нашел {stats.get('total')} трек-кодов:</b>\n"

                # Блок 1: НОВЫЕ
                if groups.get("new"):
                    msg += f"\n🆕 <b>Новых: {stats.get('new')}</b>\n"
                    msg += "   └ <i>Новый трек, создадим заказ.</i>\n"
                    msg += "\n".join(groups["new"]) + "\n"

                # Блок 2: ПРИСВОЕНИЕ (МАГИЯ)
                if groups.get("assigned"):
                    msg += f"\n✨ <b>Присвоим (Магия): {stats.get('assigned')}</b>\n"
                    msg += "   └ <i>Найден на складе (невостребованный). Присвоим вам!</i>\n"
                    msg += "\n".join(groups["assigned"]) + "\n"

                # Блок 3: ДУБЛИКАТЫ
                if groups.get("duplicates"):
                    msg += f"\n⚠️ <b>Дубликатов (пропустим): {stats.get('duplicates')}</b>\n"
                    msg += "   └ <i>Эти заказы уже есть в базе. <b>Проверьте разницу в описании!</b></i>\n"
                    msg += "\n".join(groups["duplicates"]) + "\n"

                msg += "\n<b>Добавить эти заказы?</b>"
                
                # Сохраняем текст для подтверждения
                context.user_data['pending_order_text'] = text
                
                # Кнопки
                keyboard = [
                    [InlineKeyboardButton("✅ Да, добавить всё", callback_data="confirm_add_orders")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel_add_orders")]
                ]
                
                # Отправляем БЕЗ обрезки (Telegram вмещает 4096 символов, этого хватит на ~100 треков)
                # Если треков ОЧЕНЬ много (>100), Telegram сам разобьет сообщение, но мы пока отправим одним.
                await update.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(keyboard))
                return 

        except Exception as e:
            logger.error(f"Auto-Add Check Error: {e}")
            pass

    # 5. ПОДГОТОВКА КОНТЕКСТА ДЛЯ ИИ
    history = context.user_data.get('dialog_history', [])
    history.append({"role": "user", "content": text})
    if len(history) > 10: history = history[-10:] # Храним последние 10 сообщений

    # --- СЫВОРОТКА ПРАВДЫ (Сбор данных о компании) ---
    company_info_text = ""
    try:
        # 1. Филиалы
        loc_data = await api_request("GET", "/api/bot/locations", params={"company_id": COMPANY_ID_FOR_BOT})
        if loc_data:
            company_info_text += "\n🏢 **НАШИ АДРЕСА:**\n"
            for loc in loc_data:
                company_info_text += (
                    f"📍 {loc.get('name')}\n"
                    f"   🏠 {loc.get('address', 'Уточняется')}\n"
                    f"   ⏰ {loc.get('schedule', 'Не указан')}\n"
                    f"   📞 {loc.get('phone', 'Не указан')}\n\n"
                )
        else:
             company_info_text += "Адреса филиалов пока не настроены.\n"

        # 2. Правила (Settings)
        rule_keys = ['rule_buyout', 'rule_delivery', 'rule_general']
        rules_response = await api_request("GET", "/api/bot/settings", params={'company_id': COMPANY_ID_FOR_BOT, 'keys': rule_keys})
        
        if rules_response and isinstance(rules_response, list):
            rules_dict = {r['key']: r['value'] for r in rules_response}
            
            if rules_dict.get('rule_buyout'): 
                company_info_text += f"\n🛒 **ВЫКУП:**\n{rules_dict['rule_buyout']}\n"
            
            if rules_dict.get('rule_delivery'):
                # Цензурируем цены в правилах доставки, чтобы ИИ использовал инструмент
                rule_delivery_text = rules_dict['rule_delivery']
                rule_delivery_text = re.sub(r'(\d+(\.\d+)?\s*(\$|usd|сом|kgs|kgs|cом))|(\d+(\.\d+)?\s*(доллар|сом))|(цена|тариф)', 
                                            '[...цена...]', 
                                            rule_delivery_text, 
                                            flags=re.IGNORECASE)
                company_info_text += f"\n🚚 **ПРАВИЛА ДОСТАВКИ:**\n{rule_delivery_text}\n"

            if rules_dict.get('rule_general'): 
                company_info_text += f"\nℹ️ **ИНФО:**\n{rules_dict['rule_general']}\n"

    except Exception:
        pass
    
    # Время (Бишкек)
    bishkek_tz = timezone(timedelta(hours=6))
    current_date = datetime.now(tz=bishkek_tz).strftime("%Y-%m-%d %H:%M")
    
    # Профиль клиента и счетчик заказов
    client_profile_str = "..."
    orders_str = "..."
    try:
        # Профиль
        c_data = await api_request("GET", f"/api/clients/{client_id}", params={"company_id": COMPANY_ID_FOR_BOT})
        if c_data:
             code = f"{c_data.get('client_code_prefix','')}{c_data.get('client_code_num','')}"
             client_profile_str = f"ФИО: {c_data.get('full_name')}\nКод: {code}\nТел: {c_data.get('phone')}"
        
        # Заказы (только активные статусы)
        active_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
        o_data = await api_request("GET", "/api/orders", params={
            "client_id": client_id, 
            "company_id": COMPANY_ID_FOR_BOT, 
            "statuses": active_statuses, 
            "limit": 50
        })
        if o_data and isinstance(o_data, list):
             orders_str = f"Активных заказов: {len(o_data)}."
        else:
             orders_str = "Активных заказов: 0."
    except: pass

    # Формируем системный промпт
    # --- УМНЫЙ ВЫБОР МОЗГА (ШАГ 3) ---
    if is_owner:
        base_prompt = AI_OWNER_PROMPT
        # logger.info(f"Режим Владельца для {client_id}")
    else:
        base_prompt = AI_CLIENT_PROMPT
        # logger.info(f"Режим Клиента для {client_id}")

    # Формируем системный промпт
    system_role = base_prompt.format(company_name=COMPANY_NAME_FOR_BOT)
    
    # Добавляем контекст (дату, профиль)
    system_role += (
        f"\n\n--- КОНТЕКСТ ДИАЛОГА ---\n"
        f"СЕГОДНЯ: {current_date}.\n"
        f"КЛИЕНТ:\n{client_profile_str}\n"
        f"ЗАКАЗЫ: {orders_str}\n"
        f"{company_info_text}\n"
        f"--- КОНЕЦ КОНТЕКСТА ---"
    )
    # ---------------------------------

    # 6. ЗАПРОС ИИ
    wait_task = asyncio.create_task(notify_progress(context, chat_id))
    
    try:
        # 1. Получаем ответ от ИИ
        ai_answer = await asyncio.wait_for(get_ai_response(history, system_role), timeout=60.0)
        wait_task.cancel()

        # 2. Исправляем форматирование (Markdown -> HTML)
        if "**" in ai_answer:
            ai_answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', ai_answer)
            
        # Сохраняем в историю
        history.append({"role": "assistant", "content": ai_answer})
        context.user_data['dialog_history'] = history

        # ====================================================================================
        # --- УНИВЕРСАЛЬНЫЙ ПАРСЕР КОМАНД v4.0 (Nested Support) ---
        # ====================================================================================
        command = None
        clean_ans = ai_answer.strip()
        
        # ЛОГИКА "СКОБКИ": Ищем первую { и последнюю } во всем тексте.
        # --- УМНЫЙ ПАРСЕР JSON (FIX 6.0) ---
        json_start = clean_ans.find('{')
        command = None
        
        if json_start != -1:
            # Пытаемся найти валидный JSON, начиная с первой скобки
            # Простой метод: идем от конца, пока не распарсится
            # Это спасет, если ИИ написал: '{...} какой-то текст' или '{...} {второй json}'
            
            for i in range(len(clean_ans), json_start, -1):
                try:
                    potential_json = clean_ans[json_start:i]
                    command = json.loads(potential_json)
                    logger.info(f"[JSON Parser] Smart match found: {potential_json[:50]}...")
                    break # Нашли первый валидный JSON - выходим
                except:
                    continue
            
            # Если json.loads не сработал, пробуем ast (для одинарных кавычек)
            if not command:
                 try:
                     # Тут берем просто по последней скобке, как раньше, как фоллбэк
                     json_end = clean_ans.rfind('}') + 1
                     if json_end > json_start:
                        command = ast.literal_eval(clean_ans[json_start:json_end])
                 except: 
                     pass

        # ЛОГИКА "АДАПТЕР": Нормализуем любые форматы (Action, Function, Params)
        if command and isinstance(command, dict):
            # Приводим ключи к нижнему регистру
            command = {k.lower(): v for k, v in command.items()}
            
            # 1. Action/Function -> Tool
            if "tool" not in command:
                if "function" in command: command["tool"] = command.pop("function")
                elif "action" in command: command["tool"] = command.pop("action")
            
            # 2. Arguments/Parameters -> Плоская структура
            # Ищем вложенные словари и вытаскиваем их наверх
            for key in ["arguments", "parameters", "params", "args"]:
                if key in command:
                    nested = command.pop(key)
                    if isinstance(nested, str):
                        try: nested = json.loads(nested)
                        except: pass
                    if isinstance(nested, dict):
                        command.update(nested)
            
            logger.info(f"[Smart Adapter] FINAL COMMAND: {command}")

        # ЛОГИКА "СПАСАТЕЛЬНЫЙ КРУГ" (Если JSON вообще не найден, ищем func())
        if not command:
            func_match = re.search(r'([a-zA-Z_]+)\((.*)\)', clean_ans)
            if func_match:
                tool_name = func_match.group(1)
                if tool_name in ["search_client", "search_order", "calculate_orders", "update_client_data", "get_orders_by_date", "bulk_update_party", "add_expense", "get_settings", "get_shipping_price", "get_company_locations", "get_user_orders_json", "add_client_order_request", "admin_get_client_orders"]:
                    command = {"tool": tool_name}
                    # Парсим параметры грубой силой
                    args_str = func_match.group(2)
                    for match in re.finditer(r'(\w+)=["\'](.*?)["\']', args_str):
                        command[match.group(1)] = match.group(2)
                    for match in re.finditer(r'(\w+)=(\d+(\.\d+)?)', args_str):
                        if match.group(1) not in command: command[match.group(1)] = float(match.group(2)) if '.' in match.group(2) else int(match.group(2))
                    logger.info(f"[Text Parser] Parsed text command: {command}")
        # ====================================================================================

        # 7. ВЫПОЛНЕНИЕ КОМАНД
        if command and isinstance(command, dict) and "tool" in command:
             # ... (код выполнения остается без изменений)
            try:
                # --- (УЛУЧШЕНО) ОЧИСТКА ОТ MARKDOWN ---
                # Сначала убираем маркеры кода, чтобы они не попали в text_before
                clean_raw_answer = ai_answer.replace("```json", "").replace("```", "").strip()
                
                json_start = clean_raw_answer.find('{')
                
                # Если перед JSON есть текст (извинения, комментарии) — отправляем его
                if json_start > 0:
                    text_before = clean_raw_answer[:json_start].strip()
                    if text_before:
                        # Отправляем текст, предварительно убедившись, что там нет мусора
                        await update.message.reply_html(text_before)
                # --------------------------------------------------

                clean_ans = ai_answer.replace("```json", "").replace("```", "").strip()
                command = None
                
                # Парсинг JSON
                json_start_clean = clean_ans.find('{')
                json_end_clean = clean_ans.rfind('}') + 1
                
                if json_start_clean != -1 and json_end_clean > json_start_clean:
                    json_str = clean_ans[json_start_clean:json_end_clean]
                    try: command = json.loads(json_str)
                    except:
                        try: command = ast.literal_eval(json_str)
                        except: pass

                # --- ПАТЧ ДЛЯ НЕСЛУХА (Адаптер JSON) ---
                if command and isinstance(command, dict):
                    # Если ИИ решил выпендриться и написал "function" вместо "tool"
                    if "function" in command and "tool" not in command:
                        command["tool"] = command.pop("function") # Переименовываем в tool
                        
                        # Если параметры спрятаны внутри "arguments"
                        if "arguments" in command:
                            args = command.pop("arguments")
                            # Иногда аргументы приходят как строка JSON, иногда как словарь
                            if isinstance(args, str):
                                try: args = json.loads(args)
                                except: pass
                            if isinstance(args, dict):
                                command.update(args) # Вытаскиваем параметры наверх
                # ---------------------------------------

                if command and isinstance(command, dict) and "tool" in command:
                    if command['tool'] != 'get_user_orders_json':
                         await context.bot.send_chat_action(chat_id=chat_id, action="typing")

                    # ВЫПОЛНЯЕМ ИНСТРУМЕНТ (Здесь сработают наши новые функции доставки/жалоб)
                    tool_result = await execute_ai_tool(
                        tool_command=command, 
                        api_request_func=api_request, 
                        company_id=COMPANY_ID_FOR_BOT, 
                        employee_id=employee_id, 
                        client_id=client_id
                    )
                    
                    # Логика подтверждений для Владельца (остается)
                    try:
                        if is_owner and isinstance(tool_result, str) and tool_result.strip().startswith("{") and "confirm_action" in tool_result:
                            confirm_data = json.loads(tool_result)
                            keyboard = [
                                [InlineKeyboardButton("✅ Подтвердить", callback_data=f"ai_confirm_{confirm_data['confirm_action']}")],
                                [InlineKeyboardButton("❌ Отмена", callback_data="ai_cancel")]
                            ]
                            context.user_data['ai_pending_action'] = confirm_data
                            # ИСПРАВЛЕНИЕ: Включаем HTML
                            await update.message.reply_text(confirm_data['message'], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
                            return
                    except: pass
                    
                    # Обработка результата инструмента
                    final_text = str(tool_result)
                    try:
                        # Пробуем распарсить JSON, если ответ похож на него
                        if final_text.strip().startswith(("{", "[")):
                            res_json = json.loads(final_text)
                            
                            # --- (ИСПРАВЛЕНО) ПРИОРИТЕТ 1: РАССЫЛКА (Ждем фото) ---
                            if isinstance(res_json, dict) and res_json.get("status") == "waiting_for_broadcast_photo":
                                # 1. Сохраняем текст в память
                                draft = res_json.get('draft_text', '')
                                context.user_data['ai_broadcast_text'] = draft
                                context.user_data['ai_broadcast_photo'] = None
                                logger.info(f"[Broadcast Setup] DRAFT SAVED via Priority 1. Text len: {len(draft)}")
                                # 2. Формируем ответ
                                final_text = res_json.get('message', 'Пришлите фото.')

                            # ПРИОРИТЕТ 2: Дубликаты (Multiple Results)
                            elif isinstance(res_json, dict) and res_json.get("status") == "multiple_results":
                                msg = res_json.get("message", "")
                                options = res_json.get("options", [])
                                if options:
                                    msg += "\n\n" + "\n".join(options)
                                final_text = msg

                            # ПРИОРИТЕТ 3: Список заказов (active_orders)
                            elif isinstance(res_json, dict) and "active_orders" in res_json:
                                orders = res_json.get("active_orders", [])
                                client_info = res_json.get("client_info", "Клиент")
                                
                                if not orders:
                                    final_text = f"📭 У клиента {client_info} нет активных заказов."
                                else:
                                    # Группировка
                                    active_statuses = ["Готов к выдаче", "На складе в КР", "В пути", "На складе в Китае", "Выкуплен", "Ожидает выкупа", "В обработке"]
                                    grouped_orders = {}
                                    for status in active_statuses:
                                        grouped_orders[status] = []
                                    
                                    for order in orders:
                                        status = order.get('статус', 'В обработке')
                                        if status in grouped_orders:
                                            grouped_orders[status].append(order)

                                    formatted_text = f"📦 <b>Заказы клиента: {client_info}</b>\n" 
                                    has_orders_in_message = False
                                    bishkek_tz = timezone(timedelta(hours=6)) 

                                    for status, status_orders in grouped_orders.items():
                                        if not status_orders: continue
                                        has_orders_in_message = True
                                        formatted_text += f"\n\n═════ <b>{status.upper()}</b> ({len(status_orders)} шт) ═════\n\n"

                                        for o in status_orders:
                                            formatted_text += f"Трек: <code>{o.get('трек', '?')}</code>\n"
                                            comment = o.get('комментарий')
                                            if comment: formatted_text += f"Примечание: {html.escape(comment)}\n"
                                            
                                            calc_string = o.get('расчет')
                                            if calc_string:
                                                formatted_text += f"Расчет: {calc_string}\n"
                                            
                                            history = o.get('history_entries', [])
                                            if history:
                                                formatted_text += "История:\n"
                                                try:
                                                    for entry in history[-3:]:
                                                        date_str = entry.get('date', '').replace('Z', '+00:00')
                                                        dt = datetime.fromisoformat(date_str).astimezone(bishkek_tz)
                                                        formatted_text += f"  - {dt.strftime('%d.%m %H:%M')}: {entry.get('status')}\n"
                                                except: pass
                                            formatted_text += "──────────────\n"
                                    
                                    if not has_orders_in_message: formatted_text = f"У клиента {client_info} нет заказов по этому фильтру."
                                    
                                    if "summary_footer" in res_json:
                                        formatted_text += "\n──────────────\n" + res_json["summary_footer"]
                                        
                                    final_text = formatted_text

                            # ПРИОРИТЕТ 4: Список филиалов
                            elif isinstance(res_json, list): 
                                formatted_text = ""
                                for l in res_json:
                                    nm = l.get("Филиал") or l.get("name") or "Филиал"
                                    ad = l.get("Адрес") or l.get("address") or ""
                                    ph = l.get("Телефон") or l.get("phone") or ""
                                    sch = l.get("График_работы") or l.get("schedule") or ""
                                    formatted_text += f"📍 <b>{nm}</b>\n🏠 {ad}\n"
                                    if sch: formatted_text += f"⏰ {sch}\n"
                                    if ph: formatted_text += f"📞 {ph}\n"
                                    formatted_text += "\n"
                                if formatted_text: final_text = formatted_text

                            # ПРИОРИТЕТ 5 (САМЫЙ НИЗКИЙ): Просто сообщение
                            # (Ставим в конец, чтобы оно не перехватывало другие JSON)
                            elif isinstance(res_json, dict) and "message" in res_json:
                                final_text = res_json["message"]
                            
                            # Fallback
                            else:
                                final_text = str(tool_result)

                    except Exception as e_json:
                        logger.warning(f"Tool result was not JSON, using raw text: {e_json}")
                        final_text = str(tool_result)
                    
                    # --- [ULTRA MEMORY FIX v2] ---
                    # Мы не пишем в память технические ответы (JSON), только человеческий текст.
                    
                    history_content = final_text
                    
                    # Если ответ был JSON-командой (которую мы выполнили), не пишем её в память как текст.
                    # Вместо этого пишем системное сообщение о том, что действие выполнено.
                    if final_text.strip().startswith("{") and "confirm_action" in final_text:
                         try:
                             action_data = json.loads(final_text)
                             action_name = action_data.get("confirm_action", "действие")
                             history_content = f"[Системно: Я предложил пользователю выполнить '{action_name}'. Жду нажатия кнопки.]"
                         except:
                             history_content = "[Системно: Я предложил действие.]"
                    
                    elif final_text.strip().startswith("{") and "active_orders" in final_text:
                         # Исправление Памяти: Записываем не просто "список", а "список КЛИЕНТА"
                         try:
                             data = json.loads(final_text)
                             count = len(data.get("active_orders", []))
                             # Достаем имя клиента из JSON
                             c_info = data.get("client_info", "клиента")
                             # Пишем в память ИМЯ и ID
                             history_content = f"[Системно: Я вывел список из {count} заказов для {c_info}.]"
                         except:
                             history_content = "[Системно: Я показал список заказов.]"
                    
                    # Если это просто очень длинный текст
                    elif len(final_text) > 400:
                        history_content = final_text[:200] + "... [Текст сокращен]"

                    history.append({"role": "assistant", "content": history_content})
                    context.user_data['dialog_history'] = history[-15:] # Храним только последние 15 сообщений
                    # -----------------------------

                    # --- SMART PAGINATION: Разбивка длинных сообщений ---
                    if len(final_text) < 4000:
                        # Короткое сообщение - отправляем сразу
                        try:
                            await update.message.reply_text(final_text, parse_mode=ParseMode.HTML)
                        except Exception:
                            await update.message.reply_text(final_text, parse_mode=None)
                    else:
                        # Длинное сообщение - разбиваем по заказам (разделитель)
                        parts = final_text.split('──────────────\n')
                        current_chunk = ""
                        
                        for part in parts:
                            if not part.strip(): continue
                            part_to_add = part + '──────────────\n'
                            
                            if len(current_chunk) + len(part_to_add) > 4000:
                                try:
                                    await update.message.reply_text(current_chunk, parse_mode=ParseMode.HTML)
                                except Exception:
                                    await update.message.reply_text(current_chunk, parse_mode=None)
                                current_chunk = part_to_add 
                            else:
                                current_chunk += part_to_add
                        
                        if current_chunk:
                             try:
                                await update.message.reply_text(current_chunk, parse_mode=ParseMode.HTML)
                             except Exception:
                                await update.message.reply_text(current_chunk, parse_mode=None)
                    return

            except Exception as e_tool:
                logger.error(f"!!! [Tool Error]: {e_tool}", exc_info=True)
                await update.message.reply_html(f"⚠️ Ошибка выполнения: {html.escape(str(e_tool))}")
                return 

        # Если инструментов нет - просто отправляем ответ ИИ
        # Если JSON не найден или это просто текст
        try:
            await update.message.reply_html(ai_answer, reply_markup=markup)
        except Exception:
            await update.message.reply_text(ai_answer, reply_markup=markup) # Fallback

    except asyncio.TimeoutError:
        wait_task.cancel()
        logger.error("AI Response Timeout (60s)")
        await update.message.reply_text("⚠️ ИИ долго не отвечает. Пожалуйста, попробуйте позже или используйте меню.", reply_markup=markup)

    except Exception as e:
        wait_task.cancel()
        logger.error(f"AI Error: {e}")
        await update.message.reply_html("<b>Произошла ошибка.</b> Попробуйте еще раз.", reply_markup=markup)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает профиль клиента (или владельца), запрашивая данные через API."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

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
    
    # --- ИЗМЕНЕНИЕ: /generate_lk_link - это POST ---
    api_response_link = await api_request("POST", f"/api/clients/{client_id}/generate_lk_link", params={'company_id': COMPANY_ID_FOR_BOT})
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
    """
    (ВЕРСИЯ 2.1 Fixed) Показывает активные заказы КЛИЕНТА.
    """
    # 1. Проверка сессии (если бот перезапускался)
    client_id = context.user_data.get('client_id')
    if client_id is None:
        # Если сессии нет, пробуем восстановить её тихо через API
        user = update.effective_user
        chat_id = str(user.id)
        try:
            api_response = await api_request("POST", "/api/bot/identify_user", 
                                             json={"telegram_chat_id": chat_id, "company_id": COMPANY_ID_FOR_BOT})
            if api_response and "client" in api_response:
                client_data = api_response["client"]
                context.user_data['client_id'] = client_data.get("id")
                context.user_data['is_owner'] = api_response.get("is_owner", False)
                context.user_data['full_name'] = client_data.get("full_name")
                context.user_data['employee_id'] = api_response.get("employee_id")
                client_id = client_data.get("id")
            else:
                await update.message.reply_text("⚠️ Ваша сессия истекла. Нажмите /start, чтобы обновить меню.")
                return
        except Exception as e:
            logger.error(f"Ошибка восстановления сессии в my_orders: {e}")
            await update.message.reply_text("⚠️ Ошибка доступа. Нажмите /start.")
            return

    markup = client_main_menu_markup 
    logger.info(f"Запрос 'Мои заказы' для клиента {client_id}")
    
    # Статусы, которые считаются "активными"
    active_statuses = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче"]
    
    params = {
        'client_id': client_id,
        'statuses': active_statuses,
        'company_id': COMPANY_ID_FOR_BOT,
        'limit': 50
    }
    
    try:
        api_response = await api_request("GET", "/api/orders", params=params)

        if not api_response or "error" in api_response or not isinstance(api_response, list):
            error_msg = api_response.get("error", "Сбой загрузки") if isinstance(api_response, dict) else "Нет ответа"
            await update.message.reply_text(f"❌ Не удалось загрузить заказы: {error_msg}")
            return

        active_orders = api_response 
        if not active_orders:
            await update.message.reply_text("📭 У вас пока нет активных заказов.", reply_markup=markup)
            return

        # Группировка по статусу
        grouped_orders = {}
        for status in active_statuses:
            grouped_orders[status] = []

        for order in active_orders:
            status = order.get('status', 'В обработке')
            if status in grouped_orders:
                grouped_orders[status].append(order)
            else: # Если статус нестандартный, добавляем его в словарь
                if status not in grouped_orders: grouped_orders[status] = []
                grouped_orders[status].append(order)

        message = "📦 <b>Ваши текущие заказы:</b>\n"
        has_orders = False
        bishkek_tz = timezone(timedelta(hours=6)) 

        for status, orders in grouped_orders.items():
            if not orders: continue
            has_orders = True
            
            message += f"\n═════ <b>{status.upper()}</b> ({len(orders)}) ═════\n"
            
            for order in sorted(orders, key=lambda o: o.get('id', 0), reverse=True):
                track = order.get('track_code', '???')
                comment = order.get('comment')
                
                message += f"🔹 <code>{track}</code>"
                if comment:
                    message += f" — <i>{html.escape(comment)}</i>"
                message += "\n"
                
                # Расчет
                w = order.get('calculated_weight_kg')
                c = order.get('calculated_final_cost_som')
                if w and c:
                    message += f"   💰 <b>{w} кг / {c:.0f} с.</b>\n"

                # История (Полная хронология без дублей)
                history = order.get('history_entries', [])
                if history:
                    try:
                        # 1. Фильтруем дубликаты (оставляем последнюю дату для статуса)
                        status_map = {}
                        # Сортируем исходные данные
                        history.sort(key=lambda x: x.get('created_at', ''))
                        
                        for entry in history:
                            status_map[entry.get('status')] = entry
                        
                        # Получаем уникальные записи и сортируем их по времени
                        unique_entries = sorted(status_map.values(), key=lambda x: x.get('created_at', ''))
                        
                        # 2. Выводим последние 3 записи (чтобы не спамить, если история длинная)
                        # Если нужно ВСЕ - убери [-3:]
                        for entry in unique_entries[-3:]:
                            date_str = entry.get('created_at', '').replace('Z', '+00:00')
                            dt = datetime.fromisoformat(date_str).astimezone(bishkek_tz)
                            message += f"   🕒 <i>{dt.strftime('%d.%m %H:%M')}: {entry.get('status')}</i>\n"
                    except Exception as e: 
                        pass

        if not has_orders:
            await update.message.reply_text("📭 Активных заказов нет.", reply_markup=markup)
            return

        if len(message) > 4000:
             message = message[:4000] + "\n...(список очень длинный)..."

        await update.message.reply_html(message, reply_markup=markup)

    except Exception as e:
        logger.error(f"CRITICAL ERROR in my_orders: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при обработке списка.")


async def china_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает адрес склада в Китае, (через API)."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

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
        keys_to_fetch = ['china_warehouse_address', 'instruction_pdf_link']
        api_settings = await api_request("GET", "/api/bot/settings", params={'keys': keys_to_fetch})


        if api_settings and "error" not in api_settings and isinstance(api_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_settings}
           
        # Ищем адрес склада
        address_value = settings_dict.get('china_warehouse_address')
        if address_value:
            address_text_template = address_value

        # Ищем ссылку на PDF (НЕЗАВИСИМО от адреса)
        instruction_link = settings_dict.get('instruction_pdf_link')
       
        # 3. Формируем ответ
        final_address = address_text_template.replace("{{client_code}}", client_unique_code).replace("{client_code}", client_unique_code)


        text = (
            f"🇨🇳 <b>Адрес склада в Китае</b> 🇨🇳\n\n"
            f"❗️ Ваш уникальный код: <b>{client_unique_code}</b> ❗️\n"
            f"<i>Обязательно указывайте его ПОЛНОСТЬЮ при оформлении заказов!</i>\n\n"
            f"👇 Адрес для копирования (нажмите на него):\n\n"
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
    """
    Показывает список филиалов и общие ссылки (обновлено: График убран на уровень филиала).
    """
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    if await check_restart_or_get_client_id(update, context) is None:
        return
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    is_owner = context.user_data.get('is_owner', False)
    markup = owner_main_menu_markup if is_owner else client_main_menu_markup

    logger.info(f"Запрос контактов (выбор филиала) для компании {COMPANY_ID_FOR_BOT}")

    try:
        # 1. Получаем список филиалов (Locations)
        # Location теперь содержит все поля (address, phone, schedule и т.д.)
        api_locations = await api_request("GET", "/api/locations", params={})
        if not api_locations or "error" in api_locations or not isinstance(api_locations, list) or not api_locations:
             error_msg = api_locations.get("error", "Филиалы не найдены") if isinstance(api_locations, dict) else "Филиалы не найдены"
             await update.message.reply_text(f"Ошибка: Не удалось загрузить список филиалов. {error_msg}")
             return

        locations = api_locations

        # 2. Формирование кнопок
        keyboard = []
        
        # --- Если филиал один, показываем его сразу (как раньше) ---
        if len(locations) == 1:
            loc = locations[0]
            # Вызываем callback-функцию напрямую, чтобы сразу показать контакты
            await location_contact_callback(update, context, loc_id_override=loc.get('id'), is_start_of_dialog=True)
            return

        # --- Если филиалов несколько, показываем список ---
        
        # Кнопки для каждого филиала
        for loc in locations:
            keyboard.append([InlineKeyboardButton(f"📍 {loc.get('name', 'Филиал')}", callback_data=f"contact_loc_{loc.get('id')}")])

        # Общие кнопки (можно добавить, если они хранятся где-то еще, но пока удалены)
        # Мы полагаемся на то, что нужные ссылки (WhatsApp, Instagram, Map) хранятся в Location

        reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        text = "🇰🇬 Выберите филиал, контакты которого вы хотите посмотреть:"
        
        await update.message.reply_html(
            text, 
            reply_markup=reply_markup_inline
        )
        # Если есть хотя бы одна кнопка, отправляем меню
        if reply_markup_inline:
             await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="Используйте основное меню:", 
                reply_markup=markup
            )
             
    except Exception as e:
        logger.error(f"Неожиданная ошибка в bishkek_contacts: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при получении контактов.", reply_markup=markup)

# --- 9. Обработчики Инлайн-кнопок (ПЕРЕПИСАНЫ НА API) ---
async def location_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, loc_id_override: Optional[int] = None, is_start_of_dialog: bool = False) -> None:
    """
    (ОБНОВЛЕНО) Показывает адрес, ГРАФИК и ИНЛАЙН-КНОПКИ выбранного филиала.
    """
    query = update.callback_query
    
    # Определяем chat_id, откуда пришло сообщение
    chat_id = update.effective_chat.id if update.effective_chat else query.from_user.id

    # 1. Ответ на callback
    if query:
        await query.answer()

    # 2. Определяем ID филиала
    location_id = loc_id_override # Используем переданный ID, если есть
    if not location_id and query:
        try:
            # Извлекаем ID из callback_data: 'contact_loc_1' -> '1'
            location_id_str = query.data.split('_')[-1]
            location_id = int(location_id_str)
        except (ValueError, IndexError):
            logger.error(f"Ошибка парсинга location_id из callback: {query.data}")
            if query:
                await query.edit_message_text(text="Ошибка: Неверный ID филиала.")
            return

    if not location_id:
        return # Нечего делать, если ID филиала не определен

    logger.info(f"Пользователь {chat_id} запросил контакты филиала ID: {location_id}")

    # 3. Запрашиваем данные ТОЛЬКО ЭТОГО филиала
    # Используем публичный эндпоинт, который принимает company_id
    api_response = await api_request("GET", f"/api/locations/{location_id}", params={'company_id': COMPANY_ID_FOR_BOT})

    if not api_response or "error" in api_response or not api_response.get('id'):
        error_msg = api_response.get("error", "Филиал не найден.") if api_response else "Нет ответа"
        logger.error(f"Ошибка API при запросе филиала {location_id}: {error_msg}")
        
        # Если это не начало диалога, пробуем отредактировать сообщение
        if query and not is_start_of_dialog:
            await query.edit_message_text(f"Ошибка: {error_msg}")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"Ошибка: {error_msg}")
        return

    location = api_response

    # 4. ФОРМИРУЕМ ТЕКСТ (включая новое поле schedule)
    text = f"📍 <b>{location.get('name', 'Филиал')}</b>\n\n"
    if location.get('schedule'):
         text += f"⏰ <b>График работы:</b>\n{location.get('schedule')}\n\n" # <-- НОВОЕ ПОЛЕ
    if location.get('address'):
         text += f"🗺️ <b>Наш адрес:</b>\n{location.get('address')}\n"
    if location.get('phone'):
         text += f"📞 <b>Телефон:</b> <code>{location.get('phone')}</code>\n"

    # 5. Генерируем кнопки
    keyboard = []
    if location.get('whatsapp_link'):
        keyboard.append([InlineKeyboardButton("💬 Написать в WhatsApp", url=location.get('whatsapp_link'))])
    if location.get('instagram_link'):
        keyboard.append([InlineKeyboardButton("📸 Наш Instagram", url=location.get('instagram_link'))])
    if location.get('map_link'):
        keyboard.append([InlineKeyboardButton("🗺️ Показать на карте", url=location.get('map_link'))])

    # Если есть больше одного филиала, добавляем кнопку "Назад"
    # (Мы не знаем, сколько всего филиалов, но добавим кнопку "Назад" на всякий случай,
    # если не было переопределения ID)
    if not loc_id_override:
         keyboard.append([InlineKeyboardButton("⬅️ Назад к выбору", callback_data="contact_list_back")])

    reply_markup_inline = InlineKeyboardMarkup(keyboard) if keyboard else None

    # 6. Отправляем или редактируем сообщение
    if query and not is_start_of_dialog:
        # Редактируем сообщение (для callback'а)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup_inline
        )
    else:
        # Отправляем новое сообщение (для start_of_dialog или ошибки)
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup_inline
        )

# (Функция location_contact_back_callback удалена, т.к. мы используем API v5.0, где она не нужна)

async def location_contact_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Возвращает пользователя к списку выбора филиалов (как в bishkek_contacts) с Графиком работы.
    """
    query = update.callback_query
    await query.answer()
    
    logger.info(f"Пользователь {query.from_user.id} нажал 'Назад' к списку контактов")
    
    try:
        # 1. Получаем список филиалов (Locations)
        api_locations = await api_request("GET", "/api/locations", params={})
        if not api_locations or "error" in api_locations or not isinstance(api_locations, list) or not api_locations:
             await query.edit_message_text("Ошибка: Не удалось загрузить список филиалов.")
             return

        locations = api_locations

        # 2. Получаем ОБЩИЕ контакты И ГРАФИК РАБОТЫ (Используем /api/bot/settings)
        keys_to_fetch = ['whatsapp_link', 'instagram_link', 'map_link', 'office_schedule'] # <-- ДОБАВЛЕНО
        api_settings = await api_request("GET", "/api/bot/settings", params={'company_id': COMPANY_ID_FOR_BOT, 'keys': keys_to_fetch})
        
        settings_dict = {}
        if api_settings and "error" not in api_settings and isinstance(api_settings, list):
            settings_dict = {s.get('key'): s.get('value') for s in api_settings}
        
        # --- НОВОЕ: Извлекаем график работы ---
        schedule = settings_dict.get('office_schedule', 'График не указан')
        # --- КОНЕЦ НОВОГО ---

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
        
        # 4. Редактируем сообщение (Добавляем график работы в текст)
        text = (
            "🇰🇬 Выберите филиал для просмотра контактов или воспользуйтесь общими ссылками:\n\n"
            f"⏰ <b>График работы:</b> {schedule}" # <-- ДОБАВЛЕНО
        )
        
        await query.edit_message_text(
            text, 
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup_inline
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка в location_contact_back_callback: {e}", exc_info=True)
        await query.edit_message_text("Произошла ошибка.")

# --- НОВЫЙ ОБРАБОТЧИК РЕАКЦИЙ ---
async def handle_reaction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ИСПРАВЛЕНО) Ловит нажатия на кнопки реакций (callback_data='react_BROADCASTID_TYPE')
    """
    query = update.callback_query
    
    try:
        # 1. ПРОВЕРЯЕМ АВТОРИЗАЦИЮ КЛИЕНТА В ПЕРВУЮ ОЧЕРЕДЬ
        client_id = context.user_data.get('client_id')
        if not client_id:
            logger.warning(f"[Reaction Callback] Неавторизованный пользователь (ChatID: {query.from_user.id}) нажал на реакцию.")
            # Отправляем ВСПЛЫВАЮЩЕЕ окно с ошибкой
            await query.answer(
                text="Ошибка: Вы не авторизованы.\n\nПожалуйста, нажмите /start, чтобы войти в систему и голосовать.", 
                show_alert=True
            )
            return
        
        # 2. Парсим callback_data
        # 'react_123_like' -> ['react', '123', 'like']
        parts = query.data.split('_')
        broadcast_id = int(parts[1])
        reaction_type = parts[2]
        
        # Отправляем быстрый ответ, что голос учтен
        await query.answer(text="Ваш голос учтен!") 
        
        logger.info(f"[Reaction Callback] Клиент {client_id} нажал '{reaction_type}' для рассылки {broadcast_id}")

        # 3. Отправляем реакцию в API
        payload = {
            "client_id": client_id,
            "broadcast_id": broadcast_id,
            "reaction_type": reaction_type,
            "company_id": COMPANY_ID_FOR_BOT
        }
        api_response = await api_request("POST", "/api/bot/react", json=payload)

        if not api_response or "error" in api_response:
            error_msg = api_response.get("error", "Неизвестная ошибка") if api_response else "Нет ответа"
            logger.error(f"[Reaction Callback] Ошибка API при сохранении реакции: {error_msg}")
            # Не обновляем кнопки, если была ошибка
            return

        # 4. Обновляем кнопки в сообщении
        new_counts = api_response.get("new_counts", {})
        like_count = new_counts.get("like", 0)
        dislike_count = new_counts.get("dislike", 0)
        
        # (Если добавляли 'fire', добавьте его сюда)
        # fire_count = new_counts.get("fire", 0)

        # Формируем текст для кнопок
        like_text = f"👍 {like_count}" if like_count > 0 else "👍"
        dislike_text = f"👎 {dislike_count}" if dislike_count > 0 else "👎"
        # fire_text = f"🔥 {fire_count}" if fire_count > 0 else "🔥"

        # Создаем новую клавиатуру
        new_keyboard = [
            [
                InlineKeyboardButton(like_text, callback_data=f"react_{broadcast_id}_like"),
                InlineKeyboardButton(dislike_text, callback_data=f"react_{broadcast_id}_dislike"),
                # InlineKeyboardButton(fire_text, callback_data=f"react_{broadcast_id}_fire"),
            ]
        ]
        
        # Редактируем исходное сообщение, заменяя клавиатуру
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        logger.info(f"[Reaction Callback] Кнопки для рассылки {broadcast_id} обновлены.")

    except (IndexError, ValueError, TypeError):
        logger.error(f"[Reaction Callback] Ошибка парсинга callback_data: {query.data}", exc_info=True)
    except Exception as e:
         logger.error(f"[Reaction Callback] Неожиданная ошибка: {e}", exc_info=True)
         # Пытаемся убрать кнопки, если что-то пошло не так
         try:
             await query.edit_message_reply_markup(reply_markup=None)
         except:
             pass
         
async def handle_ai_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает подтверждения действий от AI-Администратора.
    """
    query = update.callback_query
    await query.answer()
    
    if not context.user_data.get('is_owner'):
        await query.edit_message_text("❌ Нет прав.")
        return

    data = query.data
    if data == "ai_cancel":
        await query.edit_message_text("❌ Отменено.")
        context.user_data.pop('ai_pending_action', None)
        return

    action_data = context.user_data.get('ai_pending_action')
    if not action_data:
        await query.edit_message_text("⚠️ Данные устарели.")
        return

    employee_id = context.user_data['employee_id']
    
    try:
        # --- 1. ЗАКАЗЫ ---
        if data == "ai_confirm_update_single":
            await api_request("PATCH", f"/api/orders/{action_data['order_id']}", employee_id=employee_id, json={"status": action_data['new_status'], "company_id": COMPANY_ID_FOR_BOT})
            await query.edit_message_text(f"✅ Статус изменен на '{action_data['new_status']}'.")

        elif data == "ai_confirm_delete_order":
            # Передаем пароль владельца (берем из employee_id -> API само проверит, если мы допишем логику, 
            # но пока просто удаляем, так как Владелец уже авторизован в боте)
            
            # ВАЖНО: В main.py delete_order требует пароль. 
            # Чтобы это работало из бота БЕЗ ввода пароля каждый раз, 
            # мы должны либо передать пароль в запросе (если храним его в сессии - но мы не храним),
            # либо сделать спец. эндпоинт для бота.
            
            # ВРЕМЕННОЕ РЕШЕНИЕ: Мы будем использовать bulk_action с action='delete', 
            # так как там можно передать ID заказа. Но там тоже нужен пароль.
            
            # ДАВАЙТЕ СДЕЛАЕМ ТАК: Бот попросит ввести пароль текстом.
            await query.edit_message_text("🔒 Введите ваш пароль Владельца для подтверждения удаления:")
            # Сохраняем контекст, что мы ждем пароль для удаления заказа ID
            context.user_data['awaiting_password_for_action'] = {
                'action': 'delete_order',
                'order_id': action_data['order_id']
            }
            return

        elif data == "ai_confirm_assign_client":
            payload = {"action": "assign_client", "order_ids": [action_data['order_id']], "client_id": action_data['client_id'], "new_status": "В пути"}
            await api_request("POST", "/api/orders/bulk_action", employee_id=employee_id, json=payload)
            await query.edit_message_text(f"✅ Заказ присвоен {action_data['client_name']}.")

        # --- 2. КЛИЕНТЫ ---
        elif data == "ai_confirm_change_client_code":
            # Используем PATCH
            await api_request("PATCH", f"/api/clients/{action_data['client_id']}", employee_id=employee_id, json={"client_code_num": action_data['new_code'], "company_id": COMPANY_ID_FOR_BOT})
            await query.edit_message_text(f"✅ Код клиента изменен на {action_data['new_code']}.")

        elif data == "ai_confirm_delete_client":
             await api_request("DELETE", f"/api/clients/{action_data['client_id']}", employee_id=employee_id, params={"company_id": COMPANY_ID_FOR_BOT})
             await query.edit_message_text(f"✅ Клиент {action_data['client_name']} удален.")

        # --- 3. ФИНАНСЫ (ОБНОВЛЕНО v2 - С ФИЛИАЛАМИ) ---
        elif data == "ai_confirm_add_expense":
            shift_id = None
            source = action_data.get('source', 'shift')
            location_id = action_data.get('location_id') # ID выбранного филиала
            
            if source == 'shift':
                # Если филиал выбран (для Владельца), ищем активную смену ИМЕННО ТАМ
                if location_id:
                    # Используем эндпоинт для поиска всех смен и фильтруем
                    active_shifts = await api_request("GET", "/api/shifts/all_active", employee_id=employee_id)
                    target_shift = next((s for s in active_shifts if s['location_id'] == location_id), None)
                    
                    if target_shift:
                        shift_id = target_shift['id']
                    else:
                        await query.edit_message_text(f"❌ Ошибка: В выбранном филиале сейчас нет открытой смены.")
                        return
                else:
                    # Стандартный поиск (для сотрудника или если филиал один)
                    active_shift = await api_request("GET", "/api/shifts/active", employee_id=employee_id, params={"company_id": COMPANY_ID_FOR_BOT})
                    if active_shift and active_shift.get('id'):
                        shift_id = active_shift['id']
                    else:
                        await query.edit_message_text("❌ Ошибка: Активная смена не найдена.")
                        return
            else:
                shift_id = None # Личные

            payload = {
                "amount": action_data['amount'],
                "notes": action_data['reason'],
                "expense_type_id": action_data['expense_type_id'],
                "company_id": COMPANY_ID_FOR_BOT,
                "shift_id": shift_id 
            }
            
            resp = await api_request("POST", "/api/expenses", employee_id=employee_id, json=payload)
            
            if resp and "id" in resp:
                src_text = "из кассы" if shift_id else "из личных"
                await query.edit_message_text(f"✅ Расход {action_data['amount']} сом ({src_text}) добавлен.")
            else:
                err = resp.get('detail', 'Ошибка') if resp else 'Сбой'
                await query.edit_message_text(f"❌ Не удалось добавить расход: {err}")

        # --- 4. РАССЫЛКА (ОБНОВЛЕНО С ФОТО) ---
        elif data == "ai_confirm_broadcast":
            # Достаем данные (они могут быть в action_data или в user_data['ai_pending_action'])
            text = action_data.get('text')
            photo = action_data.get('photo') or context.user_data.get('ai_broadcast_photo')
            
            payload = {
                "text": text, 
                "photo_file_id": photo, # Передаем ID фото
                "company_id": COMPANY_ID_FOR_BOT
            }
            
            resp = await api_request("POST", "/api/bot/broadcast", employee_id=employee_id, json=payload)
            count = resp.get('sent_to_clients', 0) if resp else 0
            
            # Очищаем память
            context.user_data.pop('ai_broadcast_text', None)
            context.user_data.pop('ai_broadcast_photo', None)
            
            await query.edit_message_text(f"✅ Рассылка успешно отправлена {count} клиентам.")

        # --- 5. МАССОВОЕ (ПО ID) ---
        elif data == "ai_confirm_bulk_status_manual":
            ids = action_data['ids']
            if ids:
                # Выполняем действие
                resp = await api_request("POST", "/api/orders/bulk_action", employee_id=employee_id, json={"action": "update_status", "order_ids": ids, "new_status": action_data['new_status']})
                
                msg = f"✅ Статус обновлен для {len(ids)} заказов."
                
                # Если API вернуло ID операции, добавляем кнопку отмены
                op_id = resp.get("operation_id")
                if op_id:
                     msg += "\n\n🔄 **Ошиблись?** У вас есть 3 часа, чтобы отменить."
                     # Создаем клавиатуру с кнопкой отмены
                     undo_kb = [[InlineKeyboardButton("🔙 ОТМЕНИТЬ ИЗМЕНЕНИЯ", callback_data=f"ai_undo_{op_id}")]]
                     await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(undo_kb), parse_mode=ParseMode.MARKDOWN)
                else:
                     await query.edit_message_text(msg)
        # --- 6. РАСЧЕТ ЗАКАЗОВ (НОВОЕ) ---
        # Callback будет выглядеть как ai_confirm_confirm_calc (из-за префикса ai_confirm_)
        elif data == "ai_confirm_confirm_calc":
            # 1. Формируем список заказов с весом (распределяем общий вес поровну)
            weight_per_item = action_data['weight'] / action_data['count']
            orders_payload = [{"order_id": oid, "weight_kg": weight_per_item} for oid in action_data['order_ids']]
            
            payload = {
                "orders": orders_payload,
                "price_per_kg_usd": action_data['price'],
                "exchange_rate_usd": action_data['rate'],
                "new_status": "Готов к выдаче" # Сразу меняем статус
            }
            
            # 2. Вызываем API расчета
            await api_request("POST", "/api/orders/calculate", employee_id=employee_id, json=payload)
            
            # 3. Отчет
            await query.edit_message_text(
                f"✅ <b>Расчет выполнен!</b>\n"
                f"📦 Заказов: {action_data['count']}\n"
                f"⚖️ Вес: {action_data['weight']} кг\n"
                f"💰 Итог: <b>{action_data['total_sum']} сом</b>\n"
                f"📍 Статус изменен на 'Готов к выдаче'. Клиент уведомлен.",
                parse_mode=ParseMode.HTML
            )

        # --- 7. РЕДАКТИРОВАНИЕ КЛИЕНТА (ОБНОВЛЕНО) ---
        elif data == "ai_confirm_confirm_client_edit":
            # В новой версии ai_tools данные лежат внутри ключа 'payload'
            # Но для совместимости проверяем структуру
            payload_src = action_data.get('payload', action_data)
            
            client_id = payload_src['client_id']
            api_payload = {}
            
            # Собираем только то, что изменилось
            if payload_src.get('new_phone'): api_payload['phone'] = payload_src['new_phone']
            if payload_src.get('new_code'): api_payload['client_code_num'] = payload_src['new_code']
            if payload_src.get('new_name'): api_payload['full_name'] = payload_src['new_name']       # <-- Новое
            if payload_src.get('new_prefix'): api_payload['client_code_prefix'] = payload_src['new_prefix'] # <-- Новое
            
            # Добавляем company_id, так как API требует его для проверок уникальности
            api_payload['company_id'] = COMPANY_ID_FOR_BOT

            # Вызываем API обновления
            await api_request("PATCH", f"/api/clients/{client_id}", employee_id=employee_id, json=api_payload)
            
            await query.edit_message_text(f"✅ Данные клиента успешно обновлены!", parse_mode=ParseMode.HTML)

        # --- 8. ОПЛАТА ДОЛГА ---
        elif data == "ai_confirm_repay_debt":
            payload = {
                "client_id": action_data['client_id'],
                "amount": action_data['amount'],
                "description": "Погашение через ИИ-бота",
                "payment_method": "cash", 
                "link_to_shift": action_data['link_to_shift']
            }
            await api_request("POST", "/api/debtors/repay", employee_id=employee_id, json=payload)
            dest_str = "в кассу смены" if action_data['link_to_shift'] else "на баланс (мимо кассы)"
            await query.edit_message_text(f"✅ Оплата {action_data['amount']} с. принята {dest_str}!")

        # --- 9. МАССОВОЕ ПРИСВОЕНИЕ ПО ТРЕКАМ (КОМБАЙН) ---
        elif data == "ai_confirm_bulk_assign_manual":
            payload = {
                "action": "assign_client",
                "order_ids": action_data['order_ids'],
                "client_id": action_data['client_id'],
                "new_status": action_data.get('new_status', "В пути"),
                "total_weight": action_data.get('total_weight', 0),
                
                # --- ВАЖНО: Передаем зафиксированные цены ---
                "price_per_kg": action_data.get('price'), 
                "exchange_rate": action_data.get('rate')
                # --------------------------------------------
            }
            
            await api_request("POST", "/api/orders/bulk_action", employee_id=employee_id, json=payload)
            
            status_text = payload['new_status']
            calc_text = ""
            if payload['total_weight'] > 0:
                # Показываем в отчете, по какой цене посчитали
                p = payload.get('price_per_kg', 0)
                r = payload.get('exchange_rate', 0)
                sum_val = payload['total_weight'] * p * r
                calc_text = f"\n💰 Расчет: <b>{sum_val:.0f} с.</b> ({p}$ / {r} с.)"
            
            await query.edit_message_text(
                f"✅ <b>Успешно!</b>\n"
                f"Заказы ({action_data['count']} шт) присвоены клиенту.\n"
                f"Статус: {status_text}{calc_text}",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Action Error: {e}")
        await query.edit_message_text(f"❌ Ошибка API: {e}")
    
    context.user_data.pop('ai_pending_action', None)

# --- НОВЫЙ ОБРАБОТЧИК ОТМЕНЫ (UNDO) ---
async def handle_undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопки отмены массового действия."""
    query = update.callback_query
    # data выглядит как "ai_undo_123", нам нужен ID (123)
    op_id = query.data.split("_")[2] 
    
    # Получаем ID сотрудника для авторизации запроса
    employee_id = context.user_data.get('employee_id')

    # Вызываем инструмент отмены через API
    # Обратите внимание: api_request импортируется или определен в этом файле
    resp = await api_request("POST", f"/api/orders/undo/{op_id}", employee_id=employee_id)
    
    if resp and "message" in resp:
        await query.edit_message_text(f"✅ {resp['message']}")
    else:
        err = resp.get("error", "Ошибка") if resp else "Сбой связи"
        await query.edit_message_text(f"❌ Не удалось отменить: {err}")

# --- НОВЫЙ ОБРАБОТчик ДЛЯ ВЛАДЕЛЬЦА (КТО РЕАГИРОВАЛ) ---
async def handle_show_reactions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ПОЛНАЯ ПЕРЕПИСЬ) Ловит нажатие Владельца на 'Показать, кто отреагировал'
    (callback_data='show_reacts_BROADCASTID')
    """
    query = update.callback_query
    
    # --- 1. Проверка Авторизации (Владелец) ---
    employee_id = context.user_data.get('employee_id')
    if not employee_id:
        try:
            await query.answer("Ошибка: Вы не авторизованы как Владелец. Нажмите /start.", show_alert=True)
        except Exception:
            pass # Если не удалось ответить, ничего страшного
        return

    # --- 2. Быстрый ответ "Загружаю..." ---
    # (Это ЕДИНСТВЕННЫЙ query.answer(), который мы вызовем)
    try:
        await query.answer(text="Загружаю список...")
    except Exception as e:
        logger.error(f"[Show Reactions] Не удалось отправить query.answer: {e}")
        # Если не можем ответить, нет смысла продолжать
        return

    # --- 3. Получение данных из API ---
    try:
        parts = query.data.split('_') # 'show_reacts_123'
        broadcast_id = int(parts[2])
        
        logger.info(f"[Show Reactions] Владелец (EID: {employee_id}) запросил список для {broadcast_id}")

        api_response = await api_request(
            "GET",
            f"/api/reports/broadcast/{broadcast_id}/reactions",
            employee_id=employee_id
        )

        # --- 4. Обработка ответа API ---
        if not api_response or "error" in api_response or "reactions" not in api_response:
            error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
            logger.error(f"[Show Reactions] Ошибка API: {error_msg}")
            # Отправляем сообщение об ошибке
            await context.bot.send_message(
                chat_id=query.from_user.id, 
                text=f"❌ Ошибка загрузки данных: {error_msg}"
            )
            return

        # --- 5. Формирование ответа ---
        reactions = api_response.get("reactions", [])
        if not reactions:
            logger.info(f"[Show Reactions] Реакций для {broadcast_id} не найдено.")
            await context.bot.send_message(
                chat_id=query.from_user.id, 
                text=f"📊 На рассылку #{broadcast_id} пока никто не отреагировал."
            )
            return
        
        # Группируем по типу реакции
        likes = []
        dislikes = []
        
        for r in reactions:
            client_data = r.get('client', {}) 
            client_info = f"<b>{client_data.get('full_name', '?')}</b> (<code>{client_data.get('phone', '?')}</code>)"
            
            if r.get('reaction_type') == 'like':
                likes.append(client_info)
            elif r.get('reaction_type') == 'dislike':
                dislikes.append(client_info)
            # (Можно добавить другие)

        # Собираем сообщение
        text = f"📊 <b>Реакции на рассылку #{broadcast_id}:</b>\n\n"
        if likes:
            text += f"👍 Понравилось ({len(likes)}):\n" + "\n".join(likes) + "\n\n"
        if dislikes:
            text += f"👎 Не понравилось ({len(dislikes)}):\n" + "\n".join(dislikes) + "\n\n"
        if not likes and not dislikes:
             text += "Нет данных о реакциях." # На всякий случай

        # --- 6. Отправка сообщения ---
        await context.bot.send_message(
            chat_id=query.from_user.id, 
            text=text, 
            parse_mode=ParseMode.HTML
        )

    except (IndexError, ValueError, TypeError):
        logger.error(f"[Show Reactions] Ошибка парсинга callback_data: {query.data}", exc_info=True)
        await context.bot.send_message(chat_id=query.from_user.id, text="❌ Ошибка: неверный формат запроса.")
    except Exception as e:
        logger.error(f"[Show Reactions] Неожиданная ошибка: {e}", exc_info=True)
        await context.bot.send_message(chat_id=query.from_user.id, text=f"❌ Произошла неизвестная ошибка: {e}")

# --- 10. НОВЫЕ Функции Владельца ---

async def owner_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог поиска 'Все Заказы'."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return ConversationHandler.END
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    logger.info(f"Владелец {client_id} начинает поиск по всем заказам.")
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
        params={'q': search_term, 'company_id': COMPANY_ID_FOR_BOT, 'limit': 1000}
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
        
        # --- ДОБАВЛЕНО: Вывод истории статусов (Задача 3-В) ---
        history = order.get('history_entries', [])
        if history:
            text += "<b>История статусов:</b>\n"
            bishkek_tz = timezone(timedelta(hours=6)) # Часовой пояс Бишкека
            
            for entry in history:
                try:
                    # Конвертируем UTC в Бишкек
                    utc_date = datetime.fromisoformat(entry.get('created_at'))
                    bishkek_date = utc_date.astimezone(bishkek_tz)
                    hist_date = bishkek_date.strftime('%d.%m %H:%M')
                    text += f"  <i>- {hist_date}: {entry.get('status')}</i>\n"
                except Exception as e_hist:
                    logger.warning(f"Ошибка парсинга даты истории: {e_hist}")
                    text += f"  <i>- (ошибка даты): {entry.get('status')}</i>\n"
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---
            
        text += "──────────────\n"
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (слишком много результатов)"

    await update.message.reply_html(text, reply_markup=markup)
    return ConversationHandler.END

async def owner_clients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог поиска 'Клиенты'."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return ConversationHandler.END
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    logger.info(f"Владелец {client_id} начинает поиск по клиентам.")
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
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
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

async def owner_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Владелец) Показывает статистику по реакциям на рассылки."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    employee_id = context.user_data.get('employee_id')
    markup = owner_main_menu_markup

    if not employee_id:
         await update.message.reply_text("Ошибка аутентификации Владельца. Попробуйте /start", reply_markup=markup)
         return

    logger.info(f"Владелец (EID: {employee_id}) запросил статистику рассылок.")
    await update.message.reply_text("Загружаю статистику по последним 10 рассылкам...", reply_markup=markup)

    # Вызываем новый API
    api_response = await api_request(
        "GET", 
        "/api/reports/broadcasts",
        employee_id=employee_id # Аутентификация
    )

    if not api_response or "error" in api_response or "report" not in api_response:
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API (Владелец /api/reports/broadcasts): {error_msg}")
        await update.message.reply_text(f"Ошибка загрузки статистики: {error_msg}")
        return

    report_items = api_response.get("report", [])
    if not report_items:
        await update.message.reply_text("📊 Статистика пуста. Рассылок еще не было.", reply_markup=markup)
        return

    # --- ИЗМЕНЕНИЕ: Отправляем каждую статистику ОТДЕЛЬНЫМ сообщением ---
    await update.message.reply_html("📊 <b>Статистика по последним 10 рассылкам:</b>\n\n", reply_markup=markup)

    for item in report_items:
        # Определяем часовой пояс Бишкека (UTC+6)
        bishkek_tz = timezone(timedelta(hours=6))
        # Получаем дату из ISO (она будет в UTC)
        utc_date = datetime.fromisoformat(item.get('sent_at'))
        # Конвертируем в Бишкек
        bishkek_date = utc_date.astimezone(bishkek_tz)
        # Форматируем
        sent_date = bishkek_date.strftime('%d.%m.%Y %H:%M')

        # Укорачиваем текст рассылки для превью
        plain_text = re.sub(r'<[^>]+>', '', item.get('text', '')) # Убираем HTML
        preview_text = (plain_text[:70] + '...') if len(plain_text) > 70 else plain_text
        
        photo_icon = "🖼️" if item.get('photo_file_id') else "📄"

        item_text = f"<b>{photo_icon} Рассылка от {sent_date}</b>\n"
        item_text += f"<i>«{html.escape(preview_text)}»</i>\n"
        item_text += f"👍 <b>{item.get('like_count', 0)}</b> | 👎 <b>{item.get('dislike_count', 0)}</b>\n"
        
        # Создаем кнопку "Кто отреагировал?"
        # Кнопка будет, только если есть хотя бы 1 реакция
        reply_markup_inline = None
        if item.get('like_count', 0) > 0 or item.get('dislike_count', 0) > 0:
            keyboard = [[
                InlineKeyboardButton(
                    "Показать, кто отреагировал", 
                    callback_data=f"show_reacts_{item.get('id')}"
                )
            ]]
            reply_markup_inline = InlineKeyboardMarkup(keyboard)
        
        # Отправляем сообщение
        await update.message.reply_html(item_text, reply_markup=reply_markup_inline)

async def owner_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Начинает диалог 'Объявление' (Рассылка), спрашивает про фото."""
    # --- ИЗМЕНЕНИЕ: Добавлена проверка перезапуска ---
    client_id = await check_restart_or_get_client_id(update, context)
    if client_id is None:
        return ConversationHandler.END
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    logger.info(f"Владелец {client_id} начинает рассылку.")
    context.user_data['broadcast_photo'] = None # Сбрасываем фото
    context.user_data['broadcast_text'] = None # Сбрасываем текст

    keyboard = [["Да, добавить фото"], ["Нет, только текст"], ["Отмена"]]
    await update.message.reply_text(
        "📢 Начинаем рассылку.\n\nХотите прикрепить <b>одно фото</b> к вашему объявлению?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.HTML
    )
    return OWNER_ASK_BROADCAST_PHOTO # <-- Переходим в новое состояние

async def handle_broadcast_photo_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Обрабатывает выбор 'Да' или 'Нет' для фото."""
    answer = update.message.text
    
    if answer == "Да, добавить фото":
        await update.message.reply_text(
            "Пожалуйста, <b>пришлите 1 фото</b> (не как файл, а как сжатое изображение).",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return OWNER_ASK_BROADCAST_TEXT # <-- Все равно переходим к ASK_TEXT, но будем ждать фото

    elif answer == "Нет, только текст":
        context.user_data['broadcast_photo'] = None
        await update.message.reply_text(
            "Хорошо. Теперь введите <b>текст</b> объявления (можно использовать HTML).",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return OWNER_REASK_BROADCAST_TEXT # <-- Переходим в состояние ожидания ТЕКСТА

    else: # Если пользователь что-то другое нажал (не должно случиться с one_time_keyboard)
        await update.message.reply_text("Пожалуйста, выберите 'Да' или 'Нет'.")
        return OWNER_ASK_BROADCAST_PHOTO

async def handle_broadcast_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Получил фото. Теперь просит текст."""
    if not update.message.photo:
        await update.message.reply_text("Это не фото. Пожалуйста, пришлите <b>сжатое изображение</b>, не файл.")
        return OWNER_ASK_BROADCAST_TEXT # Остаемся в том же состоянии

    # Берем фото лучшего качества (последнее в списке)
    photo_file = update.message.photo[-1]
    context.user_data['broadcast_photo'] = photo_file.file_id
    logger.info(f"Владелец {update.effective_user.id} добавил фото, file_id: {photo_file.file_id}")
    
    await update.message.reply_text(
        "✅ Фото получено.\n\nТеперь введите <b>текст</b> объявления (он будет подписью к фото).",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.HTML
    )
    return OWNER_REASK_BROADCAST_TEXT # <-- Переходим в состояние ожидания ТЕКСТА

async def handle_broadcast_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Получил фото. Теперь просит текст."""
    if not update.message.photo:
        await update.message.reply_text("Это не фото. Пожалуйста, пришлите <b>сжатое изображение</b>, не файл.")
        return OWNER_ASK_BROADCAST_TEXT # Остаемся в том же состоянии

    # Берем фото лучшего качества (последнее в списке)
    photo_file = update.message.photo[-1]
    context.user_data['broadcast_photo'] = photo_file.file_id
    logger.info(f"Владелец {update.effective_user.id} добавил фото, file_id: {photo_file.file_id}")
    
    await update.message.reply_text(
        "✅ Фото получено.\n\nТеперь введите <b>текст</b> объявления (он будет подписью к фото).",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.HTML
    )
    return OWNER_REASK_BROADCAST_TEXT # <-- Переходим в состояние ожидания ТЕКСТА

async def handle_broadcast_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Получил текст рассылки, показывает превью и просит подтверждения."""
    broadcast_text_html = update.message.text_html # Сохраняем с HTML
    broadcast_text_plain = update.message.text # Для превью
    context.user_data['broadcast_text'] = broadcast_text_html

    photo_file_id = context.user_data.get('broadcast_photo')

    # Формируем превью
    preview_message = "<b>Пожалуйста, проверьте ваше объявление:</b>\n"
    preview_message += "-----------------------------------\n"
    if photo_file_id:
        preview_message += "[ ФОТО ]\n"
    preview_message += f"{broadcast_text_plain}\n" # Показываем как простой текст
    preview_message += "-----------------------------------\n\n"
    preview_message += "<b>Отправляем это сообщение всем клиентам?</b>"

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
    photo_file_id = context.user_data.get('broadcast_photo') # <-- Получаем фото

    if not broadcast_text_html:
        await update.message.reply_text("Ошибка: текст рассылки потерян. Попробуйте снова.", reply_markup=markup)
        return ConversationHandler.END

    await update.message.reply_text("⏳ Запускаю рассылку... Это может занять несколько минут.", reply_markup=markup)
    
    # Формируем payload
    payload = {
        'text': broadcast_text_html,
        'photo_file_id': photo_file_id, # <-- Добавляем ID фото (будет None, если фото нет)
        'company_id': COMPANY_ID_FOR_BOT
    }

    api_response = await api_request(
        "POST", 
        "/api/bot/broadcast",
        employee_id=employee_id, # <--- Аутентификация
        json=payload # <-- Отправляем полный payload
    )
    
    context.user_data.pop('broadcast_text', None)
    context.user_data.pop('broadcast_photo', None) # <-- Очищаем фото

    if not api_response or "error" in api_response:
        error_msg = api_response.get("error", "Нет ответа") if api_response else "Нет ответа"
        logger.error(f"Ошибка API (Владелец /api/bot/broadcast): {error_msg}")
        await update.message.reply_text(f"❌ Ошибка при запуске рассылки: {error_msg}")
    else:
        sent_count = api_response.get('sent_to_clients', 0)
        logger.info(f"Рассылка Владельца (EID: {employee_id}) завершена. Отправлено: {sent_count}")
        await update.message.reply_text(f"✅ Рассылка успешно отправлена {sent_count} клиентам.")
        
    return ConversationHandler.END


# --- МОДУЛЬ ИМПОРТА EXCEL (ВЛАДЕЛЕЦ) ---

async def owner_handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Принимает Excel-файл и спрашивает дату."""
    # Проверка прав
    if not context.user_data.get('is_owner'):
        await update.message.reply_text("Извините, я не умею работать с файлами.")
        return ConversationHandler.END

    doc = update.message.document
    file_ext = doc.file_name.split('.')[-1].lower()
    
    if file_ext not in ['xlsx', 'xls']:
        await update.message.reply_text("❌ Я понимаю только Excel файлы (.xlsx).")
        return ConversationHandler.END

    # Скачиваем файл
    file = await doc.get_file()
    file_path = f"/tmp/{doc.file_name}" # Сохраняем во временную папку
    await file.download_to_drive(file_path)
    
    context.user_data['import_file_path'] = file_path
    
    await update.message.reply_html(
        f"📂 Получил файл: <b>{doc.file_name}</b>\n\n"
        "📅 <b>Какой датой записать эту партию?</b>\n"
        "Напишите дату (например: <code>2023-11-18</code>) или слова <i>'сегодня'</i>, <i>'вчера'</i>.",
        reply_markup=ReplyKeyboardMarkup([["Сегодня"], ["Отмена"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return OWNER_WAIT_IMPORT_DATE

async def owner_handle_import_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """(Владелец) Получает дату, парсит Excel и отправляет в API."""
    date_text = update.message.text.strip().lower()
    file_path = context.user_data.get('import_file_path')
    client_id = context.user_data.get('client_id') # ID Владельца
    
    if not file_path or not os.path.exists(file_path):
        await update.message.reply_text("❌ Ошибка: Файл потерян. Попробуйте отправить снова.")
        return ConversationHandler.END

    # 1. Определяем дату
    target_date = date.today().isoformat() # По умолчанию сегодня
    
    if date_text in ['сегодня', 'today']:
        target_date = date.today().isoformat()
    elif date_text in ['вчера', 'yesterday']:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    else:
        # Пытаемся найти дату в тексте (YYYY-MM-DD)
        import re
        match = re.search(r'(\d{4}-\d{2}-\d{2})', date_text)
        if match:
            target_date = match.group(1)
        else:
            # Если формат сложный, оставим "сегодня" но предупредим? 
            # Для простоты пока так. Можно расширить логику.
            pass

    await update.message.reply_text(f"⏳ Обрабатываю файл... Партия от: {target_date}")

    # 2. Парсим Excel (openpyxl)
    try:
        wb = openpyxl.load_workbook(file_path)
        sheet = wb.active
        
        orders_data = []
        # Ищем колонки (простая логика: ищем 'track' или берем 1-ю колонку)
        # Предполагаем: 1 колонка - Трек, 2 - Код клиента (опц), 3 - Тел (опц), 4 - Коммент
        
        for row in sheet.iter_rows(min_row=2, values_only=True): # Пропускаем заголовок
            if not row or not row[0]: continue
            
            track = str(row[0]).strip()
            client_code = str(row[1]).strip() if len(row) > 1 and row[1] else None
            phone = str(row[2]).strip() if len(row) > 2 and row[2] else None
            comment = str(row[3]).strip() if len(row) > 3 and row[3] else None
            
            orders_data.append({
                "track_code": track,
                "client_code": client_code,
                "phone": phone,
                "comment": comment
            })
        
        if not orders_data:
            await update.message.reply_text("❌ Файл пуст или не содержит трек-кодов.")
            return ConversationHandler.END

        # 3. Отправляем в API
        payload = {
            "orders_data": orders_data,
            "party_date": target_date,
            # location_id возьмется из профиля владельца в API
        }
        
        # Используем employee_id Владельца для авторизации запроса
        employee_id = context.user_data.get('employee_id')
        
        api_response = await api_request(
            "POST", 
            "/api/orders/bulk_import", 
            employee_id=employee_id,
            json=payload
        )
        
        if not api_response or "error" in api_response:
            err = api_response.get("error", "Сбой API") if api_response else "Нет ответа"
            await update.message.reply_text(f"❌ Ошибка импорта: {err}")
        else:
            msg = api_response.get("message", "Импорт завершен")
            await update.message.reply_html(
                f"✅ <b>Успешно!</b>\n\n{msg}\n"
                f"📅 Дата партии: <b>{target_date}</b>",
                reply_markup=owner_main_menu_markup
            )

    except Exception as e:
        logger.error(f"Excel Import Error: {e}")
        await update.message.reply_text("❌ Ошибка обработки файла. Убедитесь, что это правильный Excel.")
    
    finally:
        # Удаляем файл
        if os.path.exists(file_path): os.remove(file_path)
        context.user_data.pop('import_file_path', None)

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
        'phone_to_register', 'broadcast_text', 'broadcast_photo' # <-- ДОБАВЛЕНО
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
    # --- ИЗМЕНЕНИЕ: Используем проверку, а не просто .get() ---
    client_id = await check_restart_or_get_client_id(update, context)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    if not client_id:
        logger.info(f"Пользователь {chat_id} уже вышел (/logout) или сессия потеряна.")
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

async def handle_ai_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ловит фото для рассылки.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # 1. Пытаемся получить черновик
    draft_text = context.user_data.get('ai_broadcast_text')
    
    # Логируем состояние для отладки
    logger.info(f"[Handle AI Photo] User: {user.id}. Draft exists: {bool(draft_text)}. Is Owner: {context.user_data.get('is_owner')}")

    # --- ИСПРАВЛЕНИЕ: Если текст потерян (перезагрузка), говорим об этом ---
    if not draft_text:
        # Реагируем только если это похоже на диалог с Владельцем
        if context.user_data.get('is_owner'):
             await update.message.reply_text(
                 "⚠️ **Сбой режима рассылки.**\n\n"
                 "Бот не видит черновик текста (возможно, был перезапуск сервера).\n"
                 "Пожалуйста, попросите ИИ создать объявление заново.",
                 parse_mode=ParseMode.HTML
             )
        else:
             # Если это обычный клиент шлет фото — игнорируем или даем стандартный ответ
             pass
        return 
    # -----------------------------------------------------------------------

    photo_file_id = None
    
    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
    elif update.message.document:
         doc = update.message.document
         if doc.mime_type and doc.mime_type.startswith('image/'):
            photo_file_id = doc.file_id
    
    if not photo_file_id:
        await update.message.reply_text("⚠️ Пожалуйста, отправьте изображение (сжатое фото).")
        return

    context.user_data['ai_broadcast_photo'] = photo_file_id
    
    keyboard = [
        [InlineKeyboardButton("✅ Отправить всем", callback_data="ai_confirm_broadcast")],
        [InlineKeyboardButton("❌ Отмена", callback_data="ai_cancel")]
    ]
    
    # Сохраняем для кнопки подтверждения
    context.user_data['ai_pending_action'] = {
        "text": draft_text,
        "photo": photo_file_id
    }
    
    # Показываем превью
    caption = draft_text[:1000] + "..." if len(draft_text) > 1000 else draft_text
    
    try:
        await update.message.reply_photo(
            photo=photo_file_id,
            caption=f"📸 **ФОТО ДОБАВЛЕНО!**\n\nВот как это будет выглядеть:\n\n{caption}\n\n<b>Готовы запускать рассылку?</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки превью фото: {e}")
        await update.message.reply_text(
            "📸 Фото принято, но я не смог показать превью.\nГотовы отправлять?", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- 12. Запуск Бота ---

def main() -> None:
    """Главная функция запуска бота."""
    
    # --- Идентифицируем бота ПЕРЕД запуском ---
    identify_bot_company()
    # (Если ошибка, sys.exit(1) уже остановил программу)

    logger.info(f"Запуск бота для компании '{COMPANY_NAME_FOR_BOT}' (ID: {COMPANY_ID_FOR_BOT})...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Диалог Регистрации (Теперь по команде /register) ---
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("register", start_registration)], # <-- ИЗМЕНЕНО
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_input)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_get_name)],
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog)],
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
            OWNER_ASK_BROADCAST_PHOTO: [
                MessageHandler(filters.Regex('^Да, добавить фото$'), handle_broadcast_photo_choice),
                MessageHandler(filters.Regex('^Нет, только текст$'), handle_broadcast_photo_choice),
            ],
            OWNER_ASK_BROADCAST_TEXT: [
                MessageHandler(filters.PHOTO, handle_broadcast_photo_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text_received), # Если фото не прислали, а прислали текст
            ],
            OWNER_REASK_BROADCAST_TEXT: [ # Состояние, когда мы ТОЧНО ждем текст
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text_received),
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
    # Обработчик /start теперь стоит ОТДЕЛЬНО (чтобы не блокировать ИИ)
    application.add_handler(CommandHandler("start", start))
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

    # НОВЫЙ Обработчик реакций (ловит все, что начинается с 'react_')
    application.add_handler(CallbackQueryHandler(handle_reaction_callback, pattern=r'^react_'))

    # НОВЫЙ Обработчик для Владельца (ловит 'show_reacts_')
    application.add_handler(CallbackQueryHandler(handle_show_reactions_callback, pattern=r'^show_reacts_'))

    # НОВЫЙ Обработчик подтверждений ИИ (ловит 'ai_confirm_' и 'ai_cancel')
    application.add_handler(CallbackQueryHandler(handle_ai_confirmation, pattern=r'^ai_'))

    # --- ВОТ ЭТУ СТРОКУ НУЖНО ДОБАВИТЬ: ---
    application.add_handler(CallbackQueryHandler(handle_undo_callback, pattern=r'^ai_undo_'))

    # Команда /cancel вне диалогов
    application.add_handler(CommandHandler('cancel', cancel_dialog))
    application.add_handler(CallbackQueryHandler(handle_confirm_add_orders, pattern=r'^confirm_add_orders$'))
    application.add_handler(CallbackQueryHandler(cancel_dialog, pattern=r'^cancel_add_orders$'))

    # --- НОВЫЕ: Обработчики кнопок меню (для скорости и надежности) ---
    # (Они должны стоять ПЕРЕД 'process_text_logic')
    
    # Общие кнопки (доступны всем авторизованным)
    application.add_handler(MessageHandler(filters.Regex(r'^👤 Мой профиль$'), profile))
    application.add_handler(MessageHandler(filters.Regex(r'^🇨🇳 Адреса складов$'), china_addresses))
    application.add_handler(MessageHandler(filters.Regex(r'^🇰🇬 Наши контакты$'), bishkek_contacts))
    
    # Только для клиента (фильтр is_owner сработает внутри функции)
    application.add_handler(MessageHandler(filters.Regex(r'^📦 Мои заказы$'), my_orders))

    # Только для Владельца (фильтр is_owner сработает внутри функции)
    application.add_handler(MessageHandler(filters.Regex(r'^🏢 Филиалы$'), owner_locations))
    application.add_handler(MessageHandler(filters.Regex(r'^📊 Статистика$'), owner_statistics))

    # Обработчик ВСЕХ ОСТАЛЬНЫХ текстовых сообщений (ИИ, авто-перехват треков)
    # (Используем process_text_logic напрямую, т.к. handle_text_message удален)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Обработка Голоса (НОВОЕ)
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    # Обработчик фото И документов-картинок для AI-рассылок
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_ai_photo))

    logger.info(f"Бот (ID: {COMPANY_ID_FOR_BOT}) запущен и готов к работе...")
    # --- Диалог Импорта Excel (Владелец) ---
    owner_import_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.FileExtension("xlsx"), owner_handle_document)],
        states={
            OWNER_WAIT_IMPORT_DATE: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, owner_handle_import_date)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_dialog)],
        per_user=True, per_chat=True, name="owner_import"
    )
    application.add_handler(owner_import_conv)
    application.run_polling()
    
# --- НОВАЯ ФУНКЦИЯ (ЗАГЛУШКА): Уведомление Владельца о Жалобе ---
async def notify_owner_of_complaint(context: ContextTypes.DEFAULT_TYPE, complaint_text: str):
    """
    Заглушка: Отправляет уведомление в Telegram Владельцу компании.
    Требует получения Chat ID Владельца из БД (это сложная логика, пока просто лог).
    """
    logger.info(f"НОТИФИКАЦИЯ ЖАЛОБЫ (ЗАГЛУШКА): Текст: {complaint_text}")
    # TODO: Реализовать получение telegram_chat_id Владельца и отправку сообщения
    pass
async def handle_confirm_add_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка кнопки 'Да, добавить всё'."""
    query = update.callback_query
    await query.answer()
    
    text = context.user_data.get('pending_order_text')
    client_id = context.user_data.get('client_id')
    
    if not text or not client_id:
        await query.edit_message_text("⚠️ Данные устарели. Отправьте список заново.")
        return

    await query.edit_message_text("⏳ Сохраняю...")
    
    try:
        # Реальное сохранение (check_only=False)
        api_response = await api_request("POST", "/api/bot/order_request", json={
            "client_id": client_id, 
            "company_id": COMPANY_ID_FOR_BOT, 
            "request_text": text,
            "check_only": False 
        })
        
        created = api_response.get("created", 0)
        assigned = api_response.get("assigned", 0)
        skipped = api_response.get("skipped", 0)
        
        msg = f"✅ <b>Готово!</b>\n\n🆕 Добавлено: {created}\n✨ Присвоено: {assigned}\n⚠️ Пропущено: {skipped}"
        
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.HTML)
        
        # Очистка
        context.user_data.pop('pending_order_text', None)

    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Ошибка при сохранении: {e}")

if __name__ == "__main__":
    main()

