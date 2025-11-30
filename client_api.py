# client_api.py (Версия с уведомлениями Владельцу)

import os
import asyncio
import telegram
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, joinedload
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional
from datetime import date

# Импортируем модели
from models import Client, Order, Location, OrderHistory, Company, Employee, Role

# --- Pydantic модель ---
class OrderCreatePayload(BaseModel):
    track_code: str
    comment: Optional[str] = None

# --- НАСТРОЙКА ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Client Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: Уведомление Владельцу ---
async def send_telegram_async(token, chat_id, message):
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='HTML')
    except Exception as e:
        print(f"[ClientAPI Error] Не удалось отправить в Telegram: {e}")

def notify_owners_from_client_api(company_id: int, message_text: str):
    """
    Находит владельцев компании и отправляет им сообщение.
    (Копия логики из main.py, адаптированная для client_api)
    """
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company or not company.telegram_bot_token:
            return

        # Ищем сотрудников-владельцев
        owner_employees = db.query(Employee).join(Role).filter(
            Employee.company_id == company_id,
            Role.name == "Владелец",
            Employee.is_active == True
        ).all()
        
        owner_names = [e.full_name for e in owner_employees]

        # Ищем клиентов (профили владельцев), у которых есть Telegram ID
        owners_clients = db.query(Client).filter(
            Client.company_id == company_id,
            Client.full_name.in_(owner_names),
            Client.telegram_chat_id.isnot(None)
        ).all()

        # Запускаем асинхронную отправку
        for client in owners_clients:
            asyncio.run(send_telegram_async(company.telegram_bot_token, client.telegram_chat_id, message_text))

    except Exception as e:
        print(f"[ClientAPI Notify Error] {e}")
    finally:
        db.close()

# --- ЭНДПОИНТЫ ---

@app.get("/api/client/data")
def get_client_data(token: str, db: Session = Depends(get_db)):
    try:
        parts = token.split('-')
        client_id = int(parts[1])
    except (IndexError, ValueError):
        raise HTTPException(status_code=400, detail="Неверный формат токена.")

    client = db.query(Client).options(joinedload(Client.orders)).filter(Client.id == client_id).first()

    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
        
    return {"full_name": client.full_name, "orders": client.orders}

@app.post("/api/client/orders")
def client_add_order(
    token: str, 
    payload: OrderCreatePayload, 
    background_tasks: BackgroundTasks, # <-- Добавили фоновые задачи
    db: Session = Depends(get_db)
):
    # 1. Парсим токен
    try:
        parts = token.split('-')
        client_id = int(parts[1])
    except (IndexError, ValueError):
        raise HTTPException(status_code=400, detail="Неверный формат токена.")

    # 2. Ищем клиента
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")

    # 3. Определяем филиал (Берем первый попавшийся, так как клиент не выбирает)
    default_location = db.query(Location).filter(Location.company_id == client.company_id).first()
    if not default_location:
        raise HTTPException(status_code=500, detail="Ошибка: У компании нет филиалов.")

    # 4. Создаем заказ
    new_order = Order(
        track_code=payload.track_code,
        comment=payload.comment,
        client_id=client.id,
        company_id=client.company_id,
        location_id=default_location.id,
        purchase_type="Доставка",
        status="В обработке",
        party_date=date.today()
    )
    
    try:
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        # История
        history = OrderHistory(
            order_id=new_order.id,
            status="В обработке",
            employee_id=None
        )
        db.add(history)
        db.commit()

        # 5. УВЕДОМЛЕНИЕ ВЛАДЕЛЬЦУ
        comment_str = f"\n<i>Комментарий: {new_order.comment}</i>" if new_order.comment else ""
        notify_msg = (
            f"🔔 <b>Новый заказ (из ЛК)</b>\n\n"
            f"👤 Клиент: <b>{client.full_name}</b>\n"
            f"📦 Трек: <code>{new_order.track_code}</code>{comment_str}\n\n"
            f"💻 <b>Источник: Личный Кабинет</b>"
        )
        
        # Добавляем задачу в фон
        background_tasks.add_task(notify_owners_from_client_api, client.company_id, notify_msg)
        
        return {"status": "ok", "message": "Ваш заказ успешно добавлен!"}
        
    except Exception as e:
        db.rollback()
        print(f"Error creating order: {e}")
        raise HTTPException(status_code=500, detail="Не удалось сохранить заказ.")
