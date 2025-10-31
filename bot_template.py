#!/usr/bin/env python
# -*- coding: utf-8 -*-
# bot_template.py (Версия для multi-location архитектуры)

import os
import httpx  # Используется для API запросов (регистрация)
import re     # Используется для очистки номера телефона
from typing import Optional
from sqlalchemy.orm import Session
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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

# --- ИЗМЕНЕНИЕ 1: Импортируем Location и Setting ---
# Теперь мы импортируем все модели, которые нам нужны
from models import Client, Order, Location, Setting

# --- 1. НАСТРОЙКА ---
# Загружаем переменные окружения из .env файла
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# URL твоего главного API (main.py)
ADMIN_API_URL = os.getenv('ADMIN_API_URL')

# Проверка, что все переменные окружения заданы
if not TELEGRAM_BOT_TOKEN or not DATABASE_URL or not ADMIN_API_URL:
    print("="*50)
    print("КРИТИЧЕСКАЯ ОШИБКА: bot_template.py")
    print("Не найдены переменные окружения: TELEGRAM_BOT_TOKEN, DATABASE_URL или ADMIN_API_URL.")
    print("Убедитесь, что .env файл существует и содержит эти переменные.")
    print("="*50)
    exit()

# Настройка подключения к базе данных
engine = create_engine(DATABASE_URL, pool_recycle=1800, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- 2. Клавиатуры (Меню) ---
main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Мои заказы"],
    ["➕ Добавить заказ", "🇨🇳 Адреса складов"],
    ["🇰🇬 Наши контакты"]
]
main_menu_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True)

# --- 3. Состояния для диалогов (ConversationHandler) ---
# Определяем шаги для разных диалогов
(
    # Диалог добавления заказа
    ADD_ORDER_TRACK_CODE,
    ADD_ORDER_COMMENT,
    ADD_ORDER_LOCATION, # Новый шаг для выбора филиала

    # Диалог регистрации
    REGISTER_GET_NAME
) = range(4) # Теперь 4 состояния

# --- 4. Функции-помощники ---

def get_db() -> Session:
    """Создает сессию базы данных."""
    return SessionLocal()

def normalize_phone_number(phone_str: str) -> str:
    """Очищает номер телефона, оставляя только цифры в формате '996...'."""
    if not phone_str:
        return ""
    # Удаляем все, кроме цифр
    digits = re.sub(r'\D', '', phone_str)
    
    # Приводим к стандартному 9-значному формату (без 996 или 0)
    if len(digits) == 12 and digits.startswith("996"): # 996555123456
        return digits[3:] # 555123456
    if len(digits) == 10 and digits.startswith("0"): # 0555123456
        return digits[1:] # 555123456
    if len(digits) == 9: # 555123456
        return digits
    
    # Если формат неизвестен, возвращаем как есть (или пустую строку)
    return digits

async def get_client_from_user_id(user_id: int, db: Session) -> Optional[Client]:
    """
    Быстро находит клиента в БД по его Telegram ID.
    Возвращает объект Client или None, если не найден.
    """
    return db.query(Client).filter(Client.telegram_chat_id == str(user_id)).first()

# --- 5. Основные функции бота (обработчики команд и кнопок) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Обработчик команды /start."""
    user = update.effective_user
    db = get_db()
    try:
        # 1. Ищем клиента по Telegram ID
        client = await get_client_from_user_id(user.id, db)
        
        if client:
            # 2. Если клиент найден, приветствуем
            await update.message.reply_html(
                f"👋 Здравствуйте, <b>{client.full_name}</b>!\n\n"
                "Рад вас снова видеть! Используйте меню ниже для навигации.",
                reply_markup=main_menu_markup
            )
            return ConversationHandler.END # Завершаем диалог
        else:
            # 3. Если не найден, просим номер телефона
            await update.message.reply_text(
                "Здравствуйте! 🌟\n\n"
                "Чтобы я мог вас узнать, пожалуйста, отправьте мне ваш номер телефона "
                "(тот, который вы указывали при регистрации).",
                reply_markup=ReplyKeyboardRemove() # Убираем клавиатуру
            )
            # Не завершаем диалог, ждем ответа (текста или имени)
            return None 
    finally:
        db.close()

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    """Показывает информацию о профиле клиента."""
    
    # Пытаемся сгенерировать ссылку на ЛК через API
    lk_url = None
    try:
        # Используем httpx для асинхронного запроса к API
        async with httpx.AsyncClient() as http_client:
            # Используем POST, как указано в main.py
            response = await http_client.post(f"{ADMIN_API_URL}/api/clients/{client.id}/generate_lk_link")
            if response.status_code == 200:
                lk_url = response.json().get("link")
            else:
                print(f"Ошибка API (generate_lk_link): {response.text}")
    except Exception as e:
        print(f"Ошибка при генерации ссылки на ЛК для клиента {client.id}: {e}")

    # Формируем текст профиля
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>✨ ФИО:</b> {client.full_name}\n"
        f"<b>📞 Телефон:</b> {client.phone}\n"
        f"<b>⭐️ Ваш код:</b> {client.client_code_prefix or ''}{client.client_code_num or 'НЕ УКАЗАН'}\n\n"
        f"<i>Пожалуйста, всегда указывайте этот код при оформлении заказов на наш склад.</i>"
    )

    # Создаем кнопку, только если ссылка успешно получена
    keyboard = []
    if lk_url:
        keyboard.append([InlineKeyboardButton("Перейти в Личный Кабинет (ЛК)", url=lk_url)])

    await update.message.reply_html(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else main_menu_markup
    )

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    """Показывает список активных (не "Выдан") заказов клиента."""
    db = get_db()
    try:
        # Загружаем клиента и все его заказы
        client_with_orders = db.query(Client).options(
            joinedload(Client.orders)
        ).filter(Client.id == client.id).one()
        
        # Фильтруем только активные заказы
        active_orders = [order for order in client_with_orders.orders if order.status != "Выдан"]
        
        if not active_orders:
            await update.message.reply_text("У вас пока нет активных заказов. 🚚", reply_markup=main_menu_markup)
            return

        message = "📦 <b>Ваши текущие заказы:</b>\n\n"
        # Сортируем (например, по дате создания, новые вверху)
        for order in sorted(active_orders, key=lambda o: o.created_at, reverse=True):
            message += f"<b>Трек:</b> <code>{order.track_code}</code>\n"
            message += f"<b>Статус:</b> {order.status}\n"
            if order.comment:
                message += f"<b>Примечание:</b> {order.comment}\n"
            message += "──────────────\n"
            
        await update.message.reply_html(message, reply_markup=main_menu_markup)
    finally:
        db.close()

# --- ИЗМЕНЕНИЕ: Функция "Адреса складов" ---
# Теперь она берет данные из БД (таблица settings)
async def china_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client, db: Session) -> None:
    """Показывает адрес склада, подставляя код клиента."""
    try:
        # 1. Формируем код клиента
        client_code = f"{client.client_code_prefix or ''}{client.client_code_num or 'ВАШ_КОД'}"
        if client_code == 'ВАШ_КОД':
             print(f"Внимание: У клиента {client.full_name} (ID: {client.id}) не настроен код клиента (prefix/num).")

        # 2. Ищем настройку адреса в БД (для компании этого клиента)
        address_setting = db.query(Setting).filter(
            Setting.company_id == client.company_id,
            Setting.key == 'china_warehouse_address' # Ключ, который ты вводишь в админке
        ).first()

        if not address_setting or not address_setting.value:
            # Если настройка не найдена или пустая
            raise Exception("Адрес склада не настроен в админ-панели (Настройки -> Адрес склада в Китае).")

        # 3. Подставляем код клиента в шаблон адреса
        # {{client_code}} будет заменен на реальный код
        final_address_text = address_setting.value.replace("{{client_code}}", client_code)

        # 4. Ищем PDF-инструкцию (необязательно)
        pdf_setting = db.query(Setting).filter(
            Setting.company_id == client.company_id,
            Setting.key == 'instruction_pdf_link' # Ключ из админки
        ).first()

        # 5. Формируем сообщение
        text = (
            f"🇨🇳 <b>Адрес нашего склада в Китае</b>\n\n"
            f"Используйте этот адрес для всех ваших покупок.\n"
            f"<i>Обязательно скопируйте его полностью, вместе с вашим уникальным кодом <b>{client_code}</b>!</i>\n\n"
            f"👇 Просто нажмите на адрес ниже, чтобы скопировать:\n\n"
            f"<code>{final_address_text}</code>"
        )
        
        keyboard = []
        if pdf_setting and pdf_setting.value:
            keyboard.append([InlineKeyboardButton("📄 Скачать инструкцию (PDF)", url=pdf_setting.value)])
        
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else main_menu_markup)

    except Exception as e:
        print(f"Ошибка в china_addresses: {e}")
        await update.message.reply_text(
            f"Произошла ошибка при получении адреса склада.\n"
            f"Пожалуйста, убедитесь, что ваш профиль клиента (Префикс/Номер Кода) и настройки компании (Адрес склада) заполнены в админ-панели.",
            reply_markup=main_menu_markup
        )

# --- ИЗМЕНЕНИЕ: Функция "Наши контакты" ---
# Теперь она показывает выбор филиалов
async def bishkek_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client, db: Session) -> None:
    """
    Показывает контакты. 
    Если филиал один - показывает сразу. 
    Если филиалов несколько - предлагает выбор.
    """
    try:
        # 1. Запрашиваем филиалы этой компании из БД
        locations = db.query(Location).filter(
            Location.company_id == client.company_id,
            # (Можно добавить фильтр, если не все филиалы надо показывать)
        ).order_by(Location.name).all() # Сортируем по имени

        if not locations:
            await update.message.reply_text("Ошибка: Не удалось найти контакты филиалов. Обратитесь к администратору.", reply_markup=main_menu_markup)
            return

        # 2. Если у компании только ОДИН филиал
        if len(locations) == 1:
            loc = locations[0]
            text = f"🇰🇬 <b>{loc.name}</b>\n\n"
            if loc.address:
                text += f"📍 <b>Адрес:</b> {loc.address}\n"
            if loc.phone:
                text += f"📞 <b>Телефон:</b> <code>{loc.phone}</code>\n"
            
            keyboard = []
            if loc.whatsapp_link:
                keyboard.append([InlineKeyboardButton("💬 Написать в WhatsApp", url=loc.whatsapp_link)])
            if loc.instagram_link:
                keyboard.append([InlineKeyboardButton("📸 Наш Instagram", url=loc.instagram_link)])
            if loc.map_link:
                keyboard.append([InlineKeyboardButton("🗺️ Показать на карте", url=loc.map_link)])
            
            await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else main_menu_markup)

        # 3. Если у компании НЕСКОЛЬКО филиалов
        else:
            keyboard = []
            for loc in locations:
                # Создаем кнопку с callback_data вида "loc_contact_{ID_ФИЛИАЛА}"
                keyboard.append([InlineKeyboardButton(f"📍 {loc.name}", callback_data=f"loc_contact_{loc.id}")])
            
            await update.message.reply_text(
                "🇰🇬 Пожалуйста, выберите филиал, чьи контакты вы хотите посмотреть:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as e:
        print(f"Ошибка в bishkek_contacts: {e}")
        await update.message.reply_text("Произошла ошибка при загрузке контактов.", reply_markup=main_menu_markup)

# --- 6. Функции-Обработчики (Callbacks) для Инлайн-Кнопок ---

# --- НОВАЯ ФУНКЦИЯ: Обработчик нажатия на кнопку филиала ---
async def location_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Срабатывает, когда пользователь нажимает на кнопку выбора филиала (loc_contact_ID).
    """
    query = update.callback_query
    await query.answer() # Подтверждаем нажатие кнопки

    try:
        # Извлекаем ID филиала из callback_data (например, "loc_contact_123")
        location_id = int(query.data.split('_')[-1])
    except (ValueError, IndexError):
        await query.edit_message_text("Ошибка: Неверный ID филиала.")
        return

    db = get_db()
    try:
        # Получаем клиента, чтобы проверить, что он из той же компании
        client = await get_client_from_user_id(query.from_user.id, db)
        if not client:
            await query.edit_message_text("Ошибка: Ваш профиль не найден. Нажмите /start")
            return
        
        # Находим филиал в БД
        loc = db.query(Location).filter(
            Location.id == location_id,
            Location.company_id == client.company_id
        ).first()

        if not loc:
            await query.edit_message_text("Ошибка: Филиал не найден.")
            return
        
        # Формируем сообщение (такое же, как для одного филиала)
        text = f"🇰🇬 <b>{loc.name}</b>\n\n"
        if loc.address:
            text += f"📍 <b>Адрес:</b> {loc.address}\n"
        if loc.phone:
            text += f"📞 <b>Телефон:</b> <code>{loc.phone}</code>\n"
        
        keyboard = []
        if loc.whatsapp_link:
            keyboard.append([InlineKeyboardButton("💬 Написать в WhatsApp", url=loc.whatsapp_link)])
        if loc.instagram_link:
            keyboard.append([InlineKeyboardButton("📸 Наш Instagram", url=loc.instagram_link)])
        if loc.map_link:
            keyboard.append([InlineKeyboardButton("🗺️ Показать на карте", url=loc.map_link)])
        
        # Добавляем кнопку "Назад"
        keyboard.append([InlineKeyboardButton("⬅️ Назад к выбору филиала", callback_data="loc_contact_back")])

        # Редактируем сообщение, показывая контакты
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    except Exception as e:
        print(f"Ошибка в location_contact_callback: {e}")
        # Если не можем отредактировать, можем отправить новое сообщение
        try:
            await context.bot.send_message(query.from_user.id, "Произошла ошибка при показе контактов.")
        except Exception:
            pass
    finally:
        db.close()

# --- НОВАЯ ФУНКЦИЯ: Обработчик кнопки "Назад" ---
async def location_contact_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Срабатывает, когда пользователь нажимает "Назад" (loc_contact_back).
    Просто заново показывает список филиалов.
    """
    query = update.callback_query
    await query.answer()
    
    db = get_db()
    try:
        client = await get_client_from_user_id(query.from_user.id, db)
        if not client:
            await query.edit_message_text("Ошибка: Ваш профиль не найден. Нажмите /start")
            return
        
        # Заново получаем список филиалов
        locations = db.query(Location).filter(Location.company_id == client.company_id).order_by(Location.name).all()
        if not locations or len(locations) <= 1:
             # Если филиалов вдруг не осталось или остался один, просто пишем
             await query.edit_message_text("Ошибка: Не удалось найти список филиалов.", reply_markup=main_menu_markup)
             return

        keyboard = []
        for loc in locations:
            keyboard.append([InlineKeyboardButton(f"📍 {loc.name}", callback_data=f"loc_contact_{loc.id}")])
        
        # Редактируем сообщение, снова показывая список
        await query.edit_message_text(
            "🇰🇬 Пожалуйста, выберите филиал, чьи контакты вы хотите посмотреть:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        print(f"Ошибка в location_contact_back_callback: {e}")
    finally:
        db.close()


# --- 7. Диалог добавления заказа (ConversationHandler) ---

# Шаг 1: Нажата кнопка "Добавить заказ"
async def add_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления заказа, спрашивает филиал."""
    db = get_db()
    try:
        client = await get_client_from_user_id(update.effective_user.id, db)
        if not client:
             await update.message.reply_text("Ошибка: Ваш профиль не найден. Нажмите /start", reply_markup=main_menu_markup)
             return ConversationHandler.END

        # Запрашиваем филиалы компании клиента
        locations = db.query(Location).filter(Location.company_id == client.company_id).order_by(Location.name).all()

        if not locations:
            await update.message.reply_text("Ошибка: В вашей компании не настроено ни одного филиала. Добавление заказа невозможно.", reply_markup=main_menu_markup)
            return ConversationHandler.END
        
        if len(locations) == 1:
            # Если филиал один, не спрашиваем, а сразу сохраняем
            context.user_data['location_id'] = locations[0].id
            await update.message.reply_text(
                f"📦 Ваш заказ будет добавлен в филиал: {locations[0].name}.\n\n"
                "Пожалуйста, введите <b>трек-код</b> вашего нового заказа.",
                reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
                parse_mode='HTML'
            )
            return ADD_ORDER_TRACK_CODE # Переходим к вводу трек-кода
        
        else:
            # Если филиалов несколько, предлагаем выбор
            keyboard = [[KeyboardButton(loc.name)] for loc in locations]
            keyboard.append([KeyboardButton("Отмена")])
            
            await update.message.reply_text(
                "📦 Пожалуйста, выберите филиал, в который придет ваш заказ:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            )
            # Сохраняем список филиалов в user_data для проверки
            context.user_data['locations_map'] = {loc.name: loc.id for loc in locations}
            return ADD_ORDER_LOCATION # Переходим к выбору филиала

    finally:
        db.close()

# Шаг 2 (Если филиалов > 1): Получен филиал
async def add_order_received_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает название филиала, проверяет и сохраняет ID."""
    location_name = update.message.text
    locations_map = context.user_data.get('locations_map')

    if not locations_map or location_name not in locations_map:
        await update.message.reply_text(
            "Пожалуйста, выберите филиал из предложенных кнопок.",
            # (Можно отправить клавиатуру еще раз, если нужно)
        )
        return ADD_ORDER_LOCATION # Остаемся на этом же шаге

    # Сохраняем ID филиала и удаляем карту из user_data
    context.user_data['location_id'] = locations_map[location_name]
    del context.user_data['locations_map']

    await update.message.reply_text(
        f"Отлично! Заказ будет добавлен в: {location_name}.\n\n"
        "Теперь введите <b>трек-код</b>.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        parse_mode='HTML'
    )
    return ADD_ORDER_TRACK_CODE # Переходим к следующему шагу

# Шаг 3: Получен трек-код
async def add_order_received_track_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает трек-код и спрашивает комментарий."""
    track_code = update.message.text
    if not track_code or len(track_code) < 3:
        await update.message.reply_text(
            "Это не похоже на трек-код. Пожалуйста, введите корректный трек-код.",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_ORDER_TRACK_CODE # Остаемся на этом шаге
        
    context.user_data['track_code'] = track_code
    
    keyboard = [
        [KeyboardButton("⏩ Пропустить")],
        [KeyboardButton("Отмена")]
    ]
    await update.message.reply_text(
        "Отлично! Теперь введите примечание (например, 'красные кроссовки') или нажмите 'Пропустить'.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return ADD_ORDER_COMMENT # Переходим к следующему шагу

# Шаг 4 (Финальный): Получен комментарий
async def add_order_received_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает комментарий и сохраняет заказ."""
    context.user_data['comment'] = update.message.text
    await save_order_from_bot(update, context) # Вызываем функцию сохранения
    return ConversationHandler.END # Завершаем диалог

# Шаг 4 (Альтернативный): Комментарий пропущен
async def add_order_skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропускает комментарий и сохраняет заказ."""
    context.user_data['comment'] = None
    await save_order_from_bot(update, context) # Вызываем функцию сохранения
    return ConversationHandler.END # Завершаем диалог

# Функция сохранения заказа (вызывается из add_order_received_comment и add_order_skip_comment)
async def save_order_from_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Финальная функция, сохраняющая заказ в БД."""
    db = get_db()
    try:
        client = await get_client_from_user_id(update.effective_user.id, db)
        if not client:
            await update.message.reply_text("Произошла ошибка, ваш профиль не найден. Попробуйте /start", reply_markup=main_menu_markup)
            return

        # Извлекаем все данные из user_data
        track_code = context.user_data.get('track_code')
        comment = context.user_data.get('comment')
        location_id = context.user_data.get('location_id')
        company_id = client.company_id
        
        if not track_code or not location_id or not company_id:
             await update.message.reply_text("Произошла внутренняя ошибка (не найден трек-код или филиал). Попробуйте снова.", reply_markup=main_menu_markup)
             return

        # --- Проверка на дубликат трек-кода в компании ---
        existing_order = db.query(Order).filter(
            Order.company_id == company_id,
            Order.track_code == track_code
        ).first()
        
        if existing_order:
            await update.message.reply_html(
                f"❗️ <b>Ошибка!</b>\n\n"
                f"Заказ с трек-кодом <code>{track_code}</code> уже существует в системе.",
                reply_markup=main_menu_markup
            )
            return
        
        # Создаем заказ
        new_order = Order(
            track_code=track_code,
            comment=comment,
            client_id=client.id,
            company_id=company_id,
            location_id=location_id,
            purchase_type="Доставка", # Заказы из бота - всегда "Доставка"
            status="В обработке"
        )
        db.add(new_order)
        db.commit()
        
        await update.message.reply_html(
            f"✅ Готово! Ваш заказ с трек-кодом <code>{track_code}</code> успешно добавлен.",
            reply_markup=main_menu_markup
        )
    except Exception as e:
        print(f"Ошибка в save_order_from_bot: {e}")
        await update.message.reply_text("Произошла ошибка при сохранении заказа.", reply_markup=main_menu_markup)
    finally:
        context.user_data.clear() # Очищаем user_data
        db.close()

# Отмена диалога
async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет любой диалог и возвращает в главное меню."""
    await update.message.reply_text("Действие отменено.", reply_markup=main_menu_markup)
    context.user_data.clear()
    return ConversationHandler.END


# --- 8. Диалог Регистрации ---

# Шаг 2 (Регистрация): Получено имя
async def register_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает ФИО и регистрирует нового клиента."""
    full_name = update.message.text
    phone = context.user_data.get('phone_to_register')
    user = update.effective_user

    if not phone or not full_name:
        await update.message.reply_text(
            "Произошла ошибка (не найден телефон или имя). Попробуйте снова отправить /start и ваш номер телефона.",
            reply_markup=main_menu_markup
        )
        return ConversationHandler.END

    db = get_db()
    try:
        # --- ЗАПРОС К API ДЛЯ РЕГИСТРАЦИИ ---
        # Мы НЕ создаем клиента в боте, мы просим API (main.py) его создать.
        # Это гарантирует, что код клиента (client_code_num) будет сгенерирован правильно.
        
        payload = {
            "full_name": full_name,
            "phone": phone,
            # (API сам назначит префикс по умолчанию, если нужно)
        }
        
        new_client_data = None
        async with httpx.AsyncClient() as http_client:
            # Используем эндпоинт /api/clients (POST)
            response = await http_client.post(f"{ADMIN_API_URL}/api/clients", json=payload)
            
            if response.status_code == 200:
                new_client_data = response.json()
            elif response.status_code == 400: # Дубликат телефона
                error_data = response.json()
                raise Exception(error_data.get("detail", "Клиент с таким телефоном уже существует."))
            else: # Другие ошибки API
                error_data = response.json()
                raise Exception(error_data.get("detail", f"Неизвестная ошибка API (Статус {response.status_code})"))

        if not new_client_data or 'id' not in new_client_data:
            raise Exception("API не вернул данные нового клиента.")

        # 2. Находим только что созданного клиента в БД, чтобы привязать Telegram ID
        client_to_update = db.query(Client).filter(Client.id == new_client_data['id']).first()
        if client_to_update:
            client_to_update.telegram_chat_id = str(user.id)
            db.commit()
            
            await update.message.reply_html(
                f"✅ Регистрация прошла успешно, <b>{full_name}</b>!\n\n"
                f"Ваш уникальный код клиента: <b>{client_to_update.client_code_prefix}{client_to_update.client_code_num}</b>\n\n"
                "Теперь вы можете пользоваться всеми функциями бота.",
                reply_markup=main_menu_markup
            )
        else:
             raise Exception("Не удалось найти созданного клиента в БД для привязки Telegram.")
            
    except Exception as e:
        print(f"Ошибка в register_get_name: {e}")
        await update.message.reply_text(
            f"Произошла ошибка при регистрации: {e}\n\n"
            "Пожалуйста, попробуйте еще раз или свяжитесь с администратором.",
            reply_markup=main_menu_markup
        )
    finally:
        context.user_data.clear()
        db.close()

    return ConversationHandler.END


# --- 9. Главный обработчик текстовых сообщений ---

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Обрабатывает все текстовые сообщения, которые не являются командами или частью диалога."""
    user = update.effective_user
    text = update.message.text
    db = get_db()

    try:
        # 1. Проверяем, привязан ли уже пользователь
        client_already_linked = await get_client_from_user_id(user.id, db)
        
        if client_already_linked:
            # --- Если пользователь привязан, обрабатываем кнопки меню ---
            if text == "👤 Мой профиль":
                await profile(update, context, client_already_linked)
            elif text == "📦 Мои заказы":
                await my_orders(update, context, client_already_linked)
            elif text == "🇨🇳 Адреса складов":
                # --- ИЗМЕНЕНИЕ ---
                await china_addresses(update, context, client_already_linked, db)
            elif text == "🇰🇬 Наши контакты":
                # --- ИЗМЕНЕНИЕ ---
                await bishkek_contacts(update, context, client_already_linked, db)
            else:
                # Ответ на неизвестный текст
                await update.message.reply_text(
                    "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
                    reply_markup=main_menu_markup
                )
            return ConversationHandler.END # Завершаем диалог (на всякий случай)

        # --- Если пользователь НЕ привязан, пытаемся его найти или зарегистрировать ---
        
        # 2. Пытаемся распознать текст как номер телефона
        normalized_phone = normalize_phone_number(text)
        
        if not normalized_phone or len(normalized_phone) < 9:
            # Текст не похож на телефон
            await update.message.reply_text(
                "Неверный формат номера. Попробуйте еще раз (например, 0555123456 или 996555123456)."
            )
            return None # Остаемся в ожидании

        # 3. Ищем клиента по очищенному номеру
        client_found = db.query(Client).filter(Client.phone == normalized_phone).first()
        
        if client_found:
            # --- Клиент НАЙДЕН ---
            if client_found.telegram_chat_id:
                # ... но уже привязан к другому Telegram
                await update.message.reply_text(
                    "Этот номер телефона уже привязан к другому Telegram-аккаунту. "
                    "Если это ошибка, обратитесь к администратору."
                )
                return ConversationHandler.END
            
            # Привязываем Telegram ID к найденному клиенту
            client_found.telegram_chat_id = str(user.id)
            db.commit()
            
            await update.message.reply_html(
                f"🎉 Отлично, <b>{client_found.full_name}</b>! Ваш аккаунт успешно привязан.\n\n"
                "Теперь вы можете пользоваться всеми функциями. Используйте меню ниже 👇",
                reply_markup=main_menu_markup
            )
            return ConversationHandler.END
        
        else:
            # --- Клиент НЕ НАЙДЕН ---
            # Начинаем регистрацию
            context.user_data['phone_to_register'] = normalized_phone
            await update.message.reply_text(
                f"Клиент с номером {text} не найден. Хотите зарегистрироваться?\n\n"
                "Пожалуйста, отправьте ваше полное имя (ФИО), чтобы мы могли вас записать."
            )
            return REGISTER_GET_NAME # Переходим в состояние ожидания имени

    except Exception as e:
        print(f"Ошибка в handle_text_message: {e}")
        await update.message.reply_text("Произошла неизвестная ошибка. Попробуйте /start", reply_markup=main_menu_markup)
        return ConversationHandler.END
    finally:
        db.close()


# --- 10. Запуск Бота ---

def main() -> None:
    """Главная функция запуска бота."""
    
    print("Запуск бота...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Диалог добавления заказа ---
    add_order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^➕ Добавить заказ$'), add_order_start)],
        states={
            ADD_ORDER_LOCATION: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_order_received_location)
            ],
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
        fallbacks=[CommandHandler('cancel', cancel_dialog), MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)],
    )
    
    # --- Диалог регистрации / обработки телефона ---
    registration_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
        ],
        states={
            REGISTER_GET_NAME: [
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_get_name)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_dialog),
            MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)
        ],
    )

# Регистрируем СНАЧАЛА диалоги (более конкретные, как "Добавить заказ")
    application.add_handler(add_order_conv)
    
    # Регистрируем ПОТОМ /start и общий текст (менее конкретные)
    application.add_handler(registration_conv)

    # Регистрируем обработчики инлайн-кнопок (они не конфликтуют)
    application.add_handler(CallbackQueryHandler(location_contact_callback, pattern=r'^loc_contact_\d+$'))
    application.add_handler(CallbackQueryHandler(location_contact_back_callback, pattern=r'^loc_contact_back$'))

    print(f"Бот {ADMIN_API_URL} запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()
