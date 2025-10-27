# main.py (ИСПРАВЛЕННАЯ ВЕРСЯ 3.0)

import os
from datetime import date, datetime, time, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, func, or_, String, cast, Date as SQLDate
from sqlalchemy.orm import sessionmaker, Session, joinedload
from pydantic import BaseModel, Field
from typing import List, Optional
import asyncio
import telegram
import re

# === НАЧАЛО ИЗМЕНЕНИЯ ===
# Определяем статусы ЗДЕСЬ, в глобальной области видимости, ПОСЛЕ импортов
ORDER_STATUSES = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]
# === КОНЕЦ ИЗМЕНЕНИЯ ===

# --- Импортируем ВСЕ наши НОВЫE модели ---
from models import (
    Base, Company, Location, Client, Order, Role, Permission, Employee,
    ExpenseType, Shift, Expense, Setting
)

# --- 1. НАСТРОЙКА ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Не найден ключ DATABASE_URL в файле .env")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Cargo CRM API - Multi-Tenant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем всем
    allow_credentials=True,
    allow_methods=["*"], # Разрешаем все методы
    allow_headers=["*"], # Разрешаем все заголовки (включая наш X-Employee-ID)
)

# --- 2. DEPENDENCIES (Аутентификация) ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# НАША ГЛАВНАЯ DEPENDENCY ДЛЯ БЕЗОПАСНОСТИ
def get_current_active_employee(
    x_employee_id: Optional[str] = Header(None),  
    db: Session = Depends(get_db)
) -> Employee:
    """
    Проверяет заголовок X-Employee-ID, находит сотрудника в БД.
    Это - наша "сессия" пользователя.
    """
    if not x_employee_id:
        raise HTTPException(status_code=401, detail="Отсутствует заголовок X-Employee-ID (Не авторизован)")
    
    try:
        employee_id = int(x_employee_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Неверный формат X-Employee-ID")

    employee = db.query(Employee).options(
        joinedload(Employee.role).joinedload(Role.permissions)
    ).filter(Employee.id == employee_id).first()
    
    db.refresh(employee)
    print("----- DEBUG: Employee Attributes after refresh -----")
    print(dir(employee)) # Эта строка покажет все атрибуты объекта
    print("----- END DEBUG -----") # Принудительно обновить атрибуты объекта из БД

    if not employee:
        raise HTTPException(status_code=401, detail="Сотрудник не найден (Не авторизован)")
    
    if not employee.is_active:
        raise HTTPException(status_code=403, detail="Сотрудник неактивен")

    return employee

# Dependency для проверки прав СУПЕР-АДМИНА
def get_super_admin(employee: Employee = Depends(get_current_active_employee)):
    if employee.company_id is not None or employee.role.name != "Super-Admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен. Требуются права Super-Admin.")
    return employee

# Dependency для проверки прав ВЛАДЕЛЬЦА КОМПАНИИ
def get_company_owner(employee: Employee = Depends(get_current_active_employee)):
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Это действие только для сотрудников компании.")
    
    # Проверяем, есть ли у него нужные права
    permissions = {p.codename for p in employee.role.permissions}
    if 'manage_employees' not in permissions and 'manage_roles' not in permissions and 'manage_locations' not in permissions:
         raise HTTPException(status_code=403, detail="У вас нет прав на управление персоналом или филиалами.")
        
    return employee

# --- НОВАЯ ЗАВИСИМОСТЬ: Для обычных сотрудников ---
def get_current_company_employee(employee: Employee = Depends(get_current_active_employee)):
    """
    Проверяет, что сотрудник (не SuperAdmin) принадлежит компании.
    Используется для эндпоинтов, доступных всем сотрудникам (например, просмотр клиентов, заказов).
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Это действие доступно только сотрудникам компании.")
    return employee


# --- 3. Pydantic МОДЕЛИ ---

# Модель для создания Супер-Админа
class SuperAdminSetupPayload(BaseModel):
    full_name: str
    password: str

# --- Модели для Компаний (Super-Admin) ---
class CompanyBase(BaseModel):
    name: str
    company_code: str = Field(..., pattern=r'^[A-Z0-9_]{3,15}$')
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
class CompanyCreate(CompanyBase):
    subscription_paid_until: date
    owner_full_name: str
    owner_password: str
class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    is_active: Optional[bool] = None
    subscription_paid_until: Optional[date] = None
class CompanyOut(CompanyBase):
    id: int
    is_active: bool
    subscription_paid_until: Optional[date]
    created_at: datetime
    class Config:
        orm_mode = True

# --- Модели для Логина ---
class LoginPayload(BaseModel):
    password: str
    company_code: Optional[str] = None
class LoginResponse(BaseModel):
    status: str
    employee: dict  # {id, full_name, role, permissions, is_super_admin, location_id}
    company: Optional[dict] # {id, name, company_code}

# --- Модели для Управления Персоналом (Владелец Компании) ---
class LocationCreate(BaseModel):
    name: str
    address: Optional[str] = None
class LocationUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
class LocationOut(LocationCreate):
    id: int
    company_id: int
    class Config:
        orm_mode = True

class RoleBase(BaseModel):
    name: str
# ИСПРАВЛЕНИЕ: Добавлены поля для ORM Mode
class RoleOut(RoleBase):
    id: int
    class Config:
        orm_mode = True

class RolePermissionsUpdate(BaseModel):
    permission_ids: List[int]

class PermissionOut(BaseModel):
    id: int
    codename: str
    description: str
    class Config:
        orm_mode = True

class EmployeeBase(BaseModel):
    full_name: str
    location_id: int
    role_id: int
class EmployeeCreate(EmployeeBase):
    password: str
class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    location_id: Optional[int] = None
    role_id: Optional[int] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
class EmployeeOut(EmployeeBase):
    id: int
    is_active: bool
    # ИСПРАВЛЕНИЕ: RoleOut должна быть здесь для ответа
    role: RoleOut  
    class Config:
        orm_mode = True

# === НАЧАЛО НОВЫХ МОДЕЛЕЙ (СМЕНЫ И РАСХОДЫ) ===

# --- Модели для Смен ---
class ShiftBase(BaseModel):
    starting_cash: float
    exchange_rate_usd: float
    price_per_kg_usd: float

class ShiftOpenPayload(ShiftBase):
    # При открытии смены сотрудник выбирает себя (или Владелец выбирает сотрудника)
    employee_id: int # ID сотрудника, который открывает смену
    location_id: int # ID филиала, где открывается смена

class ShiftClosePayload(BaseModel):
    closing_cash: float

class ShiftOut(ShiftBase):
    id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    closing_cash: Optional[float] = None
    employee_id: int
    location_id: int
    company_id: int
    # Можно добавить данные сотрудника и филиала при необходимости
    # employee: EmployeeOut
    # location: LocationOut
    class Config:
        orm_mode = True

# --- Модели для Отчетов по смене ---
class ShiftReport(BaseModel):
    shift_id: Optional[int] = None # Для отчета по ID
    shift_start_time: datetime
    shift_end_time: Optional[datetime] = None
    employee_name: str
    location_name: str # Добавляем название филиала
    starting_cash: float
    cash_income: float = 0
    card_income: float = 0
    total_expenses: float = 0 # Расходы БЕЗ зарплат/авансов
    total_returns: float = 0
    calculated_cash: float # Расчетный остаток на конец
    actual_closing_cash: Optional[float] = None # Фактический остаток (если смена закрыта)
    discrepancy: Optional[float] = None # Расхождение (если смена закрыта)

# --- Модели для Типов Расходов ---
class ExpenseTypeBase(BaseModel):
    name: str

class ExpenseTypeCreate(ExpenseTypeBase):
    pass

class ExpenseTypeUpdate(ExpenseTypeBase):
    pass

class ExpenseTypeOut(ExpenseTypeBase):
    id: int
    company_id: int
    class Config:
        orm_mode = True

# === КОНЕЦ НОВЫХ МОДЕЛЕЙ ===

# main.py (Добавление новой модели)

class ShiftForceClosePayload(BaseModel):
    closing_cash: float
    password: str # Требуем пароль Владельца

# main.py (Добавление нового эндпоинта)
@app.post("/api/shifts/{shift_id}/force_close", tags=["Смены"])
def force_close_shift(
    shift_id: int,
    payload: ShiftForceClosePayload,
    employee: Employee = Depends(get_company_owner), # Только Владелец может
    db: Session = Depends(get_db)
):
    """Принудительное закрытие смены (доступно только Владельцу)."""
    # 1. Проверка пароля Владельца
    if employee.password != payload.password:
        raise HTTPException(status_code=403, detail="Неверный пароль Владельца.")

    # 2. Находим смену
    shift = db.query(Shift).filter(
        Shift.id == shift_id,
        Shift.company_id == employee.company_id # Принадлежит этой компании
    ).first()

    if not shift:
        raise HTTPException(status_code=404, detail="Смена не найдена в вашей компании.")
    
    if shift.end_time is not None:
        raise HTTPException(status_code=400, detail="Смена уже закрыта.")

    # 3. Закрываем смену
    shift.end_time = datetime.now()
    shift.closing_cash = payload.closing_cash
    db.commit()
    db.refresh(shift)
    
    return {"status": "ok", "message": f"Смена #{shift_id} принудительно закрыта Владельцем."}

# === НАЧАЛО НОВЫХ МОДЕЛЕЙ (РАСХОДЫ) ===

# --- Модели для Расходов ---
# Вспомогательная модель для сотрудника в ShiftInfoOut
class EmployeeSmallOut(BaseModel):
    id: int
    full_name: str
    class Config:
        orm_mode = True

# Вспомогательная модель для информации о смене в ExpenseOut
class ShiftInfoOut(BaseModel):
    employee: EmployeeSmallOut
    class Config:
        orm_mode = True

# --- Модели для Расходов ---
class ExpenseBase(BaseModel):
    amount: float = Field(..., gt=0) # Сумма должна быть больше 0
    notes: Optional[str] = None
    expense_type_id: int

class ExpenseCreate(ExpenseBase):
    shift_id: Optional[int] = None
    pass # Все поля уже в ExpenseBase

class ExpenseUpdate(BaseModel):
    amount: Optional[float] = Field(None, gt=0) # Сумма опциональна, но если есть, > 0
    notes: Optional[str] = None
    expense_type_id: Optional[int] = None

# Модель для вывода расхода с доп. информацией
class ExpenseOut(ExpenseBase):
    id: int
    created_at: datetime
    shift_id: Optional[int] = None
    company_id: int
    # Включаем данные о типе расхода
    expense_type: ExpenseTypeOut
    # Включаем данные о сотруднике смены через ShiftInfoOut
    shift: Optional[ShiftInfoOut] = None

    class Config:
        orm_mode = True

# === КОНЕЦ НОВЫХ МОДЕЛЕЙ (РАСХОДЫ) ===

# --- МОДЕЛИ ДЛЯ ОТЧЕТОВ ---
# (Перенесены с конца файла для исправления NameError)

class SummaryReportItem(BaseModel):
    total_income: float
    total_cash_income: float
    total_card_income: float
    total_expenses: float
    net_profit: float
    expenses_by_type: dict[str, float] = {}
    shifts: List[ShiftOut] = [] # ShiftOut должен быть определен ВЫШЕ

    class Config:
        orm_mode = True 
        # (Если используете Pydantic V2, замените на: from_attributes = True)

class SummaryReportResponse(BaseModel):
    status: str
    summary: SummaryReportItem

    class Config:
        orm_mode = True
        # (Если используете Pydantic V2, замените на: from_attributes = True)
# --- КОНЕЦ МОДЕЛЕЙ ДЛЯ ОТЧЕТОВ ---

# --- 4. ЭНДПОИНТЫ АУТЕНТИФИКАЦИИ ---

ALL_PERMISSIONS = {
    'manage_companies': 'Управлять Компаниями (создавать, блокировать, продлевать)',
    'impersonate_company': 'Входить "от имени" компании (для техподдержки)',
    'manage_employees': 'Управлять сотрудниками (добавлять, увольнять)',  
    'manage_roles': 'Управлять должностями и доступами',
    'manage_locations': 'Управлять филиалами (точками)',
    'manage_expense_types': 'Управлять типами расходов',  
    'view_full_reports': 'Видеть полные финансовые отчеты',
    'view_shift_report': 'Видеть отчет по текущей смене',
    'add_expense': 'Добавлять расходы',  
    'open_close_shift': 'Открывать и закрывать смены',  
    'issue_orders': 'Выдавать заказы',
    'manage_clients': 'Управлять клиентами',  
    'manage_orders': 'Управлять заказами',
    'wipe_database': 'Полностью очищать базу данных (опасная зона)'
}

@app.post("/api/superadmin/setup", tags=["Super-Admin"])
def setup_super_admin(payload: SuperAdminSetupPayload, db: Session = Depends(get_db)):
    if db.query(Employee).count() > 0:
        raise HTTPException(status_code=403, detail="Система уже настроена.")

    # 1. Создаем все ГЛОБАЛЬНЫЕ разрешения
    existing_permissions = {p.codename for p in db.query(Permission).all()}
    for codename, description in ALL_PERMISSIONS.items():
        if codename not in existing_permissions:
            db.add(Permission(codename=codename, description=description))
    db.commit()

    # 2. Создаем Роль "Super-Admin" (без company_id)
    all_permissions_in_db = db.query(Permission).all()
    super_admin_role = Role(name="Super-Admin", company_id=None, permissions=all_permissions_in_db)
    db.add(super_admin_role)
    db.commit()

    # 3. Создаем Сотрудника "Super-Admin" (вас)
    super_admin_employee = Employee(
        full_name=payload.full_name,
        password=payload.password,  
        is_active=True,
        role_id=super_admin_role.id,
        company_id=None,
        location_id=None
    )
    db.add(super_admin_employee)
    db.commit()

    return {"status": "ok", "message": f"Пользователь Super-Admin '{payload.full_name}' успешно создан."}


@app.post("/api/login", tags=["Аутентификация"], response_model=LoginResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    """
    Аутентификация сотрудника (ВЕРСИЯ С НАДЕЖНОЙ ЗАГРУЗКОЙ ПРАВ)
    """
    employee = None
    company = None
    
    # Приводим код компании к верхнему регистру для надежности
    company_code_upper = payload.company_code.upper() if payload.company_code else None

    if company_code_upper == 'SUPER':
        # --- Вход для СУПЕР-АДМИНА ---
        employee = db.query(Employee).options(
            joinedload(Employee.role) # Загружаем только роль
        ).filter(
            Employee.password == payload.password,
            Employee.company_id == None 
        ).first()
        if not employee:
            raise HTTPException(status_code=401, detail="Неверный пароль Super-Admin.")
    
    else:
        # --- Вход для СОТРУДНИКА КОМПАНИИ ---
        if not company_code_upper:
            raise HTTPException(status_code=400, detail="Не указан Код Компании.")
        
        company = db.query(Company).filter(Company.company_code == company_code_upper).first()
        if not company:
            raise HTTPException(status_code=404, detail=f"Компания с кодом '{company_code_upper}' не найдена.")
        
        if not company.is_active or (company.subscription_paid_until and company.subscription_paid_until < date.today()):
            raise HTTPException(status_code=403, detail="Доступ для компании заблокирован или подписка истекла.")

        # 1. Находим сотрудника (загружаем только его роль)
        employee = db.query(Employee).options(
            joinedload(Employee.role) 
        ).filter(
            Employee.password == payload.password,
            Employee.company_id == company.id, 
            Employee.is_active == True
        ).first()

        if not employee:
            raise HTTPException(status_code=401, detail="Неверный пароль или сотрудник неактивен.")

    # --- КРИТИЧЕСКАЯ ЧАСТЬ: НАДЕЖНАЯ ЗАГРУЗКА ПРАВ ---
    # Мы нашли сотрудника и его ID роли (employee.role_id).
    # Теперь мы отдельно и принудительно загружаем ОБЪЕКТ РОЛИ и ЕГО ПРАВА
    
    permissions = []
    if employee.role_id:
        # 2. Находим объект роли, принудительно загружая связанные с ним permissions
        role_with_permissions = db.query(Role).options(
            joinedload(Role.permissions)
        ).filter(Role.id == employee.role_id).first()
        
        if role_with_permissions and role_with_permissions.permissions:
            permissions = [p.codename for p in role_with_permissions.permissions]
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    print(f"[Login] Сотрудник {employee.full_name} (Роль: {employee.role.name}) вошел. Найдено прав: {len(permissions)}")
    
    if 'open_close_shift' in permissions:
        print(f"[Login DEBUG] Право 'open_close_shift' НАЙДЕНО для {employee.full_name}")
    else:
        print(f"[Login DEBUG] Право 'open_close_shift' НЕ НАЙДЕНО для {employee.full_name}")


    return {
        "status": "ok", 
        "employee": {
            "id": employee.id, 
            "full_name": employee.full_name, 
            "role": employee.role.name, 
            "permissions": permissions, # <-- Отправляем свежие права на фронтенд
            "is_super_admin": (employee.company_id is None),
            "location_id": employee.location_id 
        },
        "company": {"id": company.id, "name": company.name, "company_code": company.company_code} if company else None
    }

# --- 5. ЭНДПОИНТЫ: SUPER-ADMIN ---

@app.get("/api/superadmin/companies", tags=["Super-Admin"], response_model=List[CompanyOut])
def get_all_companies(
    employee: Employee = Depends(get_super_admin),  
    db: Session = Depends(get_db)
):
    companies = db.query(Company).order_by(Company.name).all()
    return companies

@app.post("/api/superadmin/companies", tags=["Super-Admin"], response_model=CompanyOut)
def create_company(
    payload: CompanyCreate,  
    employee: Employee = Depends(get_super_admin),  
    db: Session = Depends(get_db)
):
    if not re.match(r'^[A-Z0-9_]{3,15}$', payload.company_code):
         raise HTTPException(status_code=400, detail="Код компании некорректен. 3-15 знаков, только A-Z, 0-9, _")
    if db.query(Company).filter(Company.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Компания с таким названием уже существует.")
    if db.query(Company).filter(Company.company_code == payload.company_code).first():
        raise HTTPException(status_code=400, detail="Компания с таким кодом уже существует.")

    db.begin_nested() # Начинаем транзакцию
    try:
        new_company = Company(
            name=payload.name, company_code=payload.company_code,
            contact_person=payload.contact_person, contact_phone=payload.contact_phone,
            subscription_paid_until=payload.subscription_paid_until, is_active=True
        )
        db.add(new_company)
        db.flush() # Получаем ID

        main_location = Location(name="Главный филиал", address="Не указан", company_id=new_company.id)
        db.add(main_location)
        db.flush() # Получаем ID

        owner_permissions = db.query(Permission).filter(
            Permission.codename.notin_(['manage_companies', 'impersonate_company'])
        ).all()
        owner_role = Role(name="Владелец", company_id=new_company.id, permissions=owner_permissions)
        db.add(owner_role)
        db.flush() # Получаем ID

        owner_employee = Employee(
            full_name=payload.owner_full_name, password=payload.owner_password,
            is_active=True, role_id=owner_role.id,
            company_id=new_company.id, location_id=main_location.id
        )
        db.add(owner_employee)
        
        default_expense_types = ["Хоз. нужды", "Зарплата", "Аванс", "Аренда", "Прочие расходы"]
        for exp_type_name in default_expense_types:
            db.add(ExpenseType(name=exp_type_name, company_id=new_company.id))
        
        db.commit() # Применяем все изменения
        db.refresh(new_company)
        return new_company
        
    except Exception as e:
        db.rollback() # Откатываем все, если была ошибка
        raise HTTPException(status_code=500, detail=f"Ошибка при создании компании: {e}")


@app.patch("/api/superadmin/companies/{company_id}", tags=["Super-Admin"], response_model=CompanyOut)
def update_company(
    company_id: int,  
    payload: CompanyUpdate,  
    employee: Employee = Depends(get_super_admin),  
    db: Session = Depends(get_db)
):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена.")
    update_data = payload.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(company, key, value)
    db.commit()
    db.refresh(company)
    return company

@app.delete("/api/superadmin/companies/{company_id}", tags=["Super-Admin"], status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: int,
    employee: Employee = Depends(get_super_admin),
    db: Session = Depends(get_db)
):
    """Удаляет компанию и все связанные с ней данные."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена.")

    # ПРОВЕРКА: Нельзя удалить компанию, если у нее есть сотрудники
    # (кроме автоматически созданного Владельца)
    employee_count = db.query(Employee).filter(Employee.company_id == company_id).count()
    if employee_count > 1: # Больше, чем 1 (владелец)
         raise HTTPException(status_code=400, detail=f"Нельзя удалить компанию '{company.name}', т.к. в ней {employee_count} сотрудников. Сначала удалите их.")
    elif employee_count == 1:
        # Удаляем единственного сотрудника (владельца)
        owner = db.query(Employee).filter(Employee.company_id == company_id).first()
        if owner:
            db.delete(owner)

    # ДОБАВИТЬ: Удаление связанных данных (филиалы, роли, клиенты, заказы и т.д.)
    # Это важно, чтобы не оставлять "мусор" в базе.
    # Мы сделаем это каскадным удалением в models.py позже, сейчас просто удалим саму компанию.

    # Находим и удаляем роли этой компании
    roles_to_delete = db.query(Role).filter(Role.company_id == company_id).all()
    for role in roles_to_delete:
        db.delete(role)

    # Находим и удаляем филиалы этой компании
    locations_to_delete = db.query(Location).filter(Location.company_id == company_id).all()
    for loc in locations_to_delete:
        db.delete(loc)

    # Находим и удаляем настройки этой компании
    settings_to_delete = db.query(Setting).filter(Setting.company_id == company_id).all()
    for setting in settings_to_delete:
        db.delete(setting)

    # Находим и удаляем типы расходов
    exp_types_to_delete = db.query(ExpenseType).filter(ExpenseType.company_id == company_id).all()
    for exp_type in exp_types_to_delete:
        db.delete(exp_type)

    # TODO: Добавить удаление Клиентов, Заказов, Смен, Расходов этой компании

    db.delete(company)
    db.commit()
    # Возвращаем 204 No Content, так как компания удалена
    return None

# --- 6. ЭНДПОИНТЫ: ВЛАДЕЛЕЦ КОМПАНИИ (Управление персоналом) ---
# (Мы "включаем" их обратно, но с проверкой прав)

@app.get("/api/locations", tags=["Персонал (Владелец)"], response_model=List[LocationOut])
def get_locations(
    mployee: Employee = Depends(get_current_company_employee),
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕ филиалы ТЕКУЩЕЙ компании."""
    # Этот код правильный. Проблема ВНЕ этой функции.
    locations = db.query(Location).filter(Location.company_id == employee.company_id).all()
    return locations

@app.post("/api/locations", tags=["Персонал (Владелец)"], response_model=LocationOut)
def create_location(
    payload: LocationCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новый филиал для ТЕКУЩЕЙ компании."""
    new_location = Location(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_location)
    db.commit()
    db.refresh(new_location)
    return new_location

@app.get("/api/employees", tags=["Персонал (Владелец)"], response_model=List[EmployeeOut])
def get_employees(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕХ сотрудников ТЕКУЩЕЙ компании."""
    employees = db.query(Employee).options(
        joinedload(Employee.role)
    ).filter(
        Employee.company_id == employee.company_id
    ).order_by(Employee.full_name).all()
    return employees

@app.post("/api/employees", tags=["Персонал (Владелец)"], response_model=EmployeeOut)
def create_employee(
    payload: EmployeeCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает нового сотрудника для ТЕКУЩЕЙ компании."""
    # Проверяем, что location_id и role_id принадлежат этой же компании
    loc = db.query(Location).filter(Location.id == payload.location_id, Location.company_id == employee.company_id).first()
    rol = db.query(Role).filter(Role.id == payload.role_id, Role.company_id == employee.company_id).first()
    if not loc or not rol:
        raise HTTPException(status_code=404, detail="Филиал или Должность не найдены в вашей компании.")

    new_employee = Employee(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    # Загружаем роль, чтобы она была в ответе
    new_employee = db.query(Employee).options(joinedload(Employee.role)).get(new_employee.id)
    return new_employee

# main.py (Вставляем после @app.post("/employees", ...)
# ... (Код функции create_employee)

# --- ДОБАВЛЕН /api/employees/{id} ---
@app.patch("/api/employees/{employee_id}", tags=["Персонал (Владелец)"], response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет данные сотрудника ТЕКУЩЕЙ компании."""
    target_employee = db.query(Employee).options(joinedload(Employee.role)).filter(
        Employee.id == employee_id,
        Employee.company_id == employee.company_id
    ).first()

    if not target_employee:
        raise HTTPException(status_code=404, detail="Сотрудник не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True)

    # 1. Проверка на увольнение единственного активного Владельца
    if 'is_active' in update_data and update_data['is_active'] is False:
        if target_employee.role.name == 'Владелец':
            active_owners_count = db.query(Employee).filter(
                Employee.company_id == employee.company_id,
                Employee.is_active == True,
                Employee.role.has(name='Владелец')
            ).count()
            if active_owners_count <= 1:
                raise HTTPException(status_code=400, detail="Нельзя уволить единственного активного Владельца компании.")

    # 2. Проверка, что location_id и role_id принадлежат этой же компании
    if 'location_id' in update_data:
        loc = db.query(Location).filter(Location.id == update_data['location_id'], Location.company_id == employee.company_id).first()
        if not loc:
            raise HTTPException(status_code=404, detail="Указанный филиал не найден в вашей компании.")
        
    if 'role_id' in update_data:
        rol = db.query(Role).filter(Role.id == update_data['role_id'], Role.company_id == employee.company_id).first()
        if not rol:
            raise HTTPException(status_code=404, detail="Указанная должность не найдена в вашей компании.")
        # Запрет смены роли Владельца, если он не меняет ее на другую роль Владельца (проверка для безопасности)
        if target_employee.role.name == 'Владелец' and rol.name != 'Владелец':
             raise HTTPException(status_code=400, detail="Нельзя изменить роль 'Владелец' на другую роль.")


    # 3. Применяем обновления
    for key, value in update_data.items():
        setattr(target_employee, key, value)
    
    db.commit()
    db.refresh(target_employee)

    # Загружаем роль, чтобы она была в ответе
    target_employee = db.query(Employee).options(joinedload(Employee.role)).get(target_employee.id)
    return target_employee


@app.get("/api/roles", tags=["Персонал (Владелец)"], response_model=List[RoleOut])
def get_roles(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕ роли ТЕКУЩЕЙ компании."""
    roles = db.query(Role).filter(
        Role.company_id == employee.company_id
    ).order_by(Role.name).all()
    return roles

@app.get("/api/permissions", tags=["Персонал (Владелец)"], response_model=List[PermissionOut])
def get_permissions(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕ доступные (глобальные) права в системе."""
    # Владелец не может выдавать права Super-Admin
    permissions = db.query(Permission).filter(
        Permission.codename.notin_(['manage_companies', 'impersonate_company'])
    ).all()
    return permissions

    # === НАЧАЛО НОВОГО КОДА ===

@app.post("/api/roles", tags=["Персонал (Владелец)"], response_model=RoleOut)
def create_role(
    payload: RoleBase, # Используем базовую модель для имени
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новую роль для ТЕКУЩЕЙ компании."""
    if db.query(Role).filter(Role.name == payload.name, Role.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Должность с таким названием уже существует в вашей компании.")
    
    # Права по умолчанию - пока пустые
    new_role = Role(
        name=payload.name,
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role

@app.delete("/api/roles/{role_id}", tags=["Персонал (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Удаляет роль в ТЕКУЩЕЙ компании."""
    role_to_delete = db.query(Role).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id # Убеждаемся, что роль принадлежит этой компании
    ).first()

    if not role_to_delete:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    if role_to_delete.name == "Владелец":
        raise HTTPException(status_code=400, detail="Нельзя удалить стандартную роль 'Владелец'.")
    
    # Проверка, есть ли сотрудники с этой ролью
    assigned_employees = db.query(Employee).filter(Employee.role_id == role_id).count()
    if assigned_employees > 0:
        raise HTTPException(status_code=400, detail=f"Нельзя удалить должность '{role_to_delete.name}', так как к ней привязано {assigned_employees} сотрудников.")

    db.delete(role_to_delete)
    db.commit()
    return None # Возвращаем 204 No Content

@app.get("/api/roles/{role_id}/permissions", tags=["Персонал (Владелец)"], response_model=List[int])
def get_role_permissions(
    role_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ID прав для указанной роли ТЕКУЩЕЙ компании."""
    role = db.query(Role).options(joinedload(Role.permissions)).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id
    ).first()
    if not role:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    
    return [p.id for p in role.permissions]


@app.put("/api/roles/{role_id}/permissions", tags=["Персонал (Владелец)"])
def update_role_permissions(
    role_id: int,
    payload: RolePermissionsUpdate, # Ожидаем список ID прав
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет права для указанной роли ТЕКУЩЕЙ компании."""
    role = db.query(Role).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id
    ).first()
    if not role:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    if role.name == "Владелец":
         raise HTTPException(status_code=400, detail="Нельзя изменять права для роли 'Владелец'.")

    # Находим объекты Permission по ID из payload, НО только те, которые доступны Владельцу
    allowed_permission_codenames = set(ALL_PERMISSIONS.keys()) - {'manage_companies', 'impersonate_company'}
    
    new_permissions = db.query(Permission).filter(
        Permission.id.in_(payload.permission_ids),
        Permission.codename.in_(allowed_permission_codenames) # Доп. проверка безопасности
    ).all()

    # Проверяем, все ли запрошенные ID были найдены и разрешены
    if len(new_permissions) != len(set(payload.permission_ids)):
         print(f"Запрошено: {payload.permission_ids}, Найдено разрешенных: {[p.id for p in new_permissions]}")
         # Не прерываем, просто назначаем то, что разрешено

    role.permissions = new_permissions # SQLAlchemy сам разберется с many-to-many связью
    db.commit()
    
    return {"status": "ok", "message": f"Доступы для должности '{role.name}' обновлены."}

# === КОНЕЦ НОВОГО КОДА ===

# === НАЧАЛО НОВОГО КОДА (КЛИЕНТЫ) ===

# --- Pydantic Модели для Клиентов ---
class ClientBase(BaseModel):
    full_name: str
    phone: str
    client_code_prefix: Optional[str] = None
    client_code_num: Optional[int] = None # Теперь можно редактировать
    status: Optional[str] = "Розница"

class ClientCreate(ClientBase):
    pass # Все поля уже в ClientBase

class ClientUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    client_code_prefix: Optional[str] = None
    client_code_num: Optional[int] = None
    status: Optional[str] = None
    telegram_chat_id: Optional[str] = None # Добавим возможность отвязки (редко нужно)

class ClientOut(ClientBase):
    id: int
    company_id: int
    telegram_chat_id: Optional[str] # Чтобы видеть, привязан ли телеграм
    created_at: datetime
    class Config:
        orm_mode = True

class BulkClientItem(BaseModel):
    full_name: str
    phone: str
    client_code: Optional[str] = None # Оставляем как строку для гибкости импорта

class GenerateLKLinkResponse(BaseModel):
    link: str

# --- Эндпоинты для Клиентов ---

@app.get("/api/clients", tags=["Клиенты (Владелец)"], response_model=List[ClientOut])
def get_clients(
    employee: Employee = Depends(get_current_company_employee), # Проверка прав владельца
    db: Session = Depends(get_db)
):
    """Получает ВСЕХ клиентов ТЕКУЩЕЙ компании."""
    clients = db.query(Client).filter(
        Client.company_id == employee.company_id
    ).order_by(Client.full_name).all()
    return clients

# main.py (Для админ-панели, которая использует get_company_owner)

@app.post("/api/clients", tags=["Клиенты (Владелец)"], response_model=ClientOut)
def create_client(
    payload: ClientCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    # Проверка на дубликат телефона ВНУТРИ компании
    if db.query(Client).filter(Client.phone == payload.phone, Client.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует в вашей компании.")

    # Проверка на дубликат кода клиента ВНУТРИ компании (если указан)
    if payload.client_code_num and db.query(Client).filter(Client.client_code_num == payload.client_code_num, Client.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail=f"Клиентский код {payload.client_code_num} уже занят в вашей компании.")

    # === ИСПРАВЛЕННАЯ ЛОГИКА АВТО-ГЕНЕРАЦИИ КОДА (с фильтром по компании) ===
    if payload.client_code_num is None:
        # 1. Находим максимальный существующий код ДЛЯ ЭТОЙ КОМПАНИИ
        max_code_result = db.query(
            func.max(Client.client_code_num)
        ).filter(
            Client.company_id == employee.company_id
        ).scalar()
        
        # 2. Устанавливаем следующий код: +1 к максимуму, или 1001, если клиентов нет
        payload.client_code_num = (max_code_result + 1) if max_code_result else 1001 
    # === КОНЕЦ ИСПРАВЛЕНИЯ ===

    if payload.client_code_prefix is None:
         payload.client_code_prefix = "KB" # Префикс по умолчанию

    new_client = Client(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return new_client

# main.py (Для админ-панели, которая использует get_company_owner)

@app.post("/api/clients", tags=["Клиенты (Владелец)"], response_model=ClientOut)
def create_client(
    payload: ClientCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    # Проверка на дубликат телефона ВНУТРИ компании
    if db.query(Client).filter(Client.phone == payload.phone, Client.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует в вашей компании.")

    # Проверка на дубликат кода клиента ВНУТРИ компании (если указан)
    if payload.client_code_num and db.query(Client).filter(Client.client_code_num == payload.client_code_num, Client.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail=f"Клиентский код {payload.client_code_num} уже занят в вашей компании.")

    # === ИСПРАВЛЕННАЯ ЛОГИКА АВТО-ГЕНЕРАЦИИ КОДА (с фильтром по компании) ===
    if payload.client_code_num is None:
        # 1. Находим максимальный существующий код ДЛЯ ЭТОЙ КОМПАНИИ
        max_code_result = db.query(
            func.max(Client.client_code_num)
        ).filter(
            Client.company_id == employee.company_id
        ).scalar()
        
        # 2. Устанавливаем следующий код: +1 к максимуму, или 1001, если клиентов нет
        payload.client_code_num = (max_code_result + 1) if max_code_result else 1001 
    # === КОНЕЦ ИСПРАВЛЕНИЯ ===

    if payload.client_code_prefix is None:
         payload.client_code_prefix = "KB" # Префикс по умолчанию

    new_client = Client(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return new_client

@app.patch("/api/clients/{client_id}", tags=["Клиенты (Владелец)"], response_model=ClientOut)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет данные клиента ТЕКУЩЕЙ компании."""
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == employee.company_id # Убеждаемся, что клиент из той же компании
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True)

    # Проверка на уникальность телефона при изменении
    if 'phone' in update_data and update_data['phone'] != client.phone:
        if db.query(Client).filter(Client.phone == update_data['phone'], Client.company_id == employee.company_id).first():
            raise HTTPException(status_code=400, detail="Другой клиент с таким телефоном уже существует в вашей компании.")

    # Проверка на уникальность кода клиента при изменении
    if 'client_code_num' in update_data and update_data['client_code_num'] != client.client_code_num:
         if update_data['client_code_num'] and db.query(Client).filter(Client.client_code_num == update_data['client_code_num'], Client.company_id == employee.company_id).first():
             raise HTTPException(status_code=400, detail=f"Клиентский код {update_data['client_code_num']} уже занят в вашей компании.")

    for key, value in update_data.items():
        setattr(client, key, value)

    db.commit()
    db.refresh(client)
    return client

@app.delete("/api/clients/{client_id}", tags=["Клиенты (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Удаляет клиента ТЕКУЩЕЙ компании."""
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == employee.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    # ПРОВЕРКА: Есть ли у клиента активные (не выданные) заказы?
    active_orders_count = db.query(Order).filter(
        Order.client_id == client_id,
        Order.status != "Выдан" # Используем статус "Выдан" как признак завершенного
    ).count()

    if active_orders_count > 0:
        raise HTTPException(status_code=400, detail=f"Невозможно удалить клиента, так как у него есть {active_orders_count} незавершенных заказов.")

    # TODO: Подумать, нужно ли удалять ИСТОРИЮ заказов клиента или только самого клиента.
    # Пока удаляем только клиента, заказы останутся без привязки (или можно настроить каскадное удаление).
    # Рекомендуется НЕ удалять заказы, чтобы сохранялась история.

    db.delete(client)
    db.commit()
    return None

@app.get("/api/clients/search", tags=["Клиенты (Владелец)"], response_model=List[ClientOut])
def search_clients(
    q: str = Query(..., min_length=1), # Запрос должен быть не пустым
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Ищет клиентов по имени, телефону или коду ВНУТРИ ТЕКУЩЕЙ компании."""
    search_term = f"%{q.lower()}%" # Поиск без учета регистра
    
    # Ищем совпадения в имени, телефоне ИЛИ коде (префикс + номер)
    clients = db.query(Client).filter(
        Client.company_id == employee.company_id, # Только в текущей компании
        or_(
            func.lower(Client.full_name).ilike(search_term),
            Client.phone.ilike(search_term),
            (func.lower(Client.client_code_prefix) + func.cast(Client.client_code_num, String)).ilike(search_term)
        )
    ).limit(15).all() # Ограничиваем количество результатов
    
    return clients

@app.post("/api/clients/{client_id}/generate_lk_link", tags=["Клиенты (Владелец)"], response_model=GenerateLKLinkResponse)
def generate_lk_link_for_client(
    client_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Генерирует ссылку на личный кабинет для клиента ТЕКУЩЕЙ компании."""
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == employee.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    # Формируем токен (можно усложнить для большей безопасности)
    # Формат: CLIENT-<client_id>-COMPANY-<company_id>-SECRET
    secret_token = f"CLIENT-{client.id}-COMPANY-{employee.company_id}-SECRET"  
    
    # Получаем базовый URL клиентского портала (если он задан в .env, иначе используем заглушку)
    # Важно: Этот URL должен быть доступен КЛИЕНТАМ извне!
    client_portal_base_url = os.getenv("CLIENT_PORTAL_URL", "http://ВАШ_ДОМЕН_ИЛИ_IP/lk.html")  
    
    link = f"{client_portal_base_url}?token={secret_token}"
    return {"link": link}


# === НАЧАЛО НОВОГО КОДА (ИМПОРТ КЛИЕНТОВ) ===

# Модель для ответа после импорта
class BulkImportResponse(BaseModel):
    status: str
    message: str
    created_clients: int
    errors: List[str]
    warnings: List[str]  

# Используем модель BulkClientItem, которая уже есть

@app.post("/api/clients/bulk_import", tags=["Клиенты (Владелец)"], response_model=BulkImportResponse)
def bulk_import_clients(
    clients_data: List[BulkClientItem], # FastAPI автоматически распарсит JSON-массив
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Массовый импорт клиентов из списка (например, из Excel) для ТЕКУЩЕЙ компании."""
    created_count = 0
    errors = []
    warnings = []

    # Получаем ВСЕХ существующих клиентов ЭТОЙ компании для быстрой проверки дубликатов
    existing_clients_in_company = db.query(Client).filter(Client.company_id == employee.company_id).all()
    existing_phones = {c.phone: c for c in existing_clients_in_company}
    existing_codes = {c.client_code_num: c for c in existing_clients_in_company if c.client_code_num is not None}
    
    for item in clients_data:
        # Валидация базовых полей
        if not item.full_name or not item.phone:
            errors.append(f"Пропущена строка: Отсутствует ФИО или Телефон.")
            continue

        # Убираем лишние символы из телефона
        cleaned_phone = re.sub(r'\D', '', item.phone) # Удаляем всё, кроме цифр
        if not cleaned_phone:
             errors.append(f"Пропущен клиент '{item.full_name}': Некорректный номер телефона '{item.phone}'.")
             continue

        # Проверка на дубликат телефона ВНУТРИ компании
        if cleaned_phone in existing_phones:
            warnings.append(f"Клиент '{item.full_name}' с телефоном {cleaned_phone} уже существует в вашей компании (пропущен).")
            continue

        new_client = Client(
            full_name=item.full_name,
            phone=cleaned_phone,
            company_id=employee.company_id # Привязываем к компании
        )

        # --- ИСПРАВЛЕННЫЙ БЛОК ОБРАБОТКИ КОДА ---
        parsed_prefix = None # Изначально префикс не определен
        parsed_num = None    # Изначально номер не определен

        # Обработка кода клиента из файла (ТОЛЬКО если он ЕСТЬ)
        if item.client_code:
            code_str = str(item.client_code).strip()
            if code_str: # Проверяем, что строка не пустая после удаления пробелов
                # Пытаемся извлечь префикс (буквы) и номер (цифры)
                match_prefix = re.match(r'^([a-zA-Z]+)', code_str)
                match_num = re.search(r'(\d+)$', code_str)

                temp_prefix = "KB" # Префикс по умолчанию, если не найден
                if match_prefix:
                    temp_prefix = match_prefix.group(1).upper()

                if match_num:
                    try:
                        num_val = int(match_num.group(1))
                        # Проверка на дубликат кода ВНУТРИ компании
                        if num_val in existing_codes:
                            warnings.append(f"Код '{num_val}' для клиента '{item.full_name}' уже занят и будет проигнорирован (останется пустым).")
                            # Не присваиваем код, оставляем parsed_num = None
                        else:
                            parsed_num = num_val # Код уникален, сохраняем его
                            parsed_prefix = temp_prefix # Сохраняем префикс (найденный или KB)
                            existing_codes.add(parsed_num) # Добавляем в сет для проверки следующих строк
                    except ValueError:
                         warnings.append(f"Не удалось распознать номер кода в '{code_str}' для клиента '{item.full_name}'. Код будет проигнорирован (останется пустым).")
                else:
                     warnings.append(f"Не удалось найти номер в коде '{code_str}' для клиента '{item.full_name}'. Код будет проигнорирован (останется пустым).")
            else:
                # Если item.client_code был, но состоял из пробелов
                 warnings.append(f"Пустая строка в колонке client_code для клиента '{item.full_name}'. Код не будет присвоен.")
        
        # Присваиваем ТОЛЬКО РАСПАРСЕННЫЕ значения (или None, если не было/не распознано)
        # Авто-генерация здесь БОЛЬШЕ НЕ ПРОИСХОДИТ
        new_client.client_code_prefix = parsed_prefix
        new_client.client_code_num = parsed_num
        # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ОБРАБОТКИ КОДА ---

        db.add(new_client)
        existing_phones.add(cleaned_phone) # Добавляем телефон в сет для проверки следующих
        created_count += 1

        # Периодически сбрасываем сессию, чтобы избежать накопления объектов в памяти
        if created_count % 100 == 0:
            try:
                db.flush()
            except Exception as e_flush:
                 db.rollback()
                 errors.append(f"Ошибка при промежуточной записи (клиент ~{created_count}): {e_flush}")
                 # Прерываем импорт при серьезной ошибке записи
                 break

    # Финальный коммит
    try:
        db.commit()
    except Exception as e_commit:
        db.rollback()
        # Если была ошибка на финальном коммите, возможно, часть данных не записалась
        errors.append(f"Критическая ошибка при финальной записи: {e_commit}. Возможно, часть клиентов не была импортирована.")
        # Обнуляем счетчик, так как не уверены, что всё записалось
        created_count = 0

    return {
        "status": "ok",
        "message": "Импорт завершен.",
        "created_clients": created_count,
        "errors": errors,
        "warnings": warnings
    }

# === КОНЕЦ НОВОГО КОДА (ИМПОРТ КЛИЕНТОВ) ===
# === КОНЕЦ НОВОГО КОДА (КЛИЕНТЫ) ===

# === НАЧАЛО НОВОГО КОДА (ЗАКАЗЫ) ===

# --- Pydantic Модели для Заказов ---

# Базовая модель заказа (для вывода и создания/обновления)
class OrderBase(BaseModel):
    track_code: str
    status: Optional[str] = "В обработке"
    purchase_type: str = "Доставка" # По умолчанию Доставка
    comment: Optional[str] = None
    party_date: Optional[date] = None # Теперь опционально при создании

    # Поля для выкупа (опциональные)
    buyout_item_cost_cny: Optional[float] = None
    buyout_commission_percent: Optional[float] = 10.0 # По умолчанию 10%
    buyout_rate_for_client: Optional[float] = None
    buyout_actual_rate: Optional[float] = None # Заполняется позже

    # Поля для расчета (только для чтения в ответе)
    calculated_weight_kg: Optional[float] = None
    calculated_price_per_kg_usd: Optional[float] = None
    calculated_exchange_rate_usd: Optional[float] = None
    calculated_final_cost_som: Optional[float] = None

# Модель для создания заказа (требуем ID клиента)
class OrderCreate(OrderBase):
    client_id: int
    location_id: Optional[int] = None

# Модель для обновления заказа
class OrderUpdate(BaseModel):
    # Позволяем менять почти все основные поля
    track_code: Optional[str] = None
    status: Optional[str] = None
    purchase_type: Optional[str] = None
    comment: Optional[str] = None
    party_date: Optional[date] = None
    client_id: Optional[int] = None # Возможность сменить клиента
    location_id: Optional[int] = None

    # Поля выкупа
    buyout_item_cost_cny: Optional[float] = None
    buyout_commission_percent: Optional[float] = None
    buyout_rate_for_client: Optional[float] = None
    buyout_actual_rate: Optional[float] = None

    # Поля расчета (эти поля обновляются через /calculate)
    # calculated_weight_kg: Optional[float] = None
    # ...

# Модель для вывода заказа (включая данные клиента)
class OrderOut(OrderBase):
    id: int
    company_id: int
    client: ClientOut # Вложенная модель клиента
    created_at: datetime
    issued_at: Optional[datetime] # Поля для выданных
    weight_kg: Optional[float]
    final_cost_som: Optional[float]

    class Config:
        orm_mode = True

# Модели для массовых действий
class BulkOrderItem(BaseModel): # Используется для ИМПОРТА
    track_code: str
    client_code: Optional[str] = None # Идентификация клиента по коду
    phone: Optional[str] = None      # ИЛИ по телефону
    comment: Optional[str] = None
    purchase_type: Optional[str] = "Доставка" # Тип заказа для импорта
    # Добавляем поля выкупа для импорта
    buyout_item_cost_cny: Optional[float] = None
    buyout_rate_for_client: Optional[float] = None
    buyout_commission_percent: Optional[float] = 10.0
    # party_date можно будет указать отдельно

class BulkOrderImportPayload(BaseModel):
    orders_data: List[BulkOrderItem]
    party_date: Optional[date] = None # Общая дата партии для импорта
    location_id: Optional[int] = None

# Используется для смены статуса, даты, удаления
class BulkActionPayload(BaseModel):
    action: str # 'update_status', 'update_party_date', 'delete', 'buyout', 'revert'
    order_ids: List[int]
    # Опциональные поля в зависимости от action
    new_status: Optional[str] = None
    new_party_date: Optional[date] = None
    buyout_actual_rate: Optional[float] = None
    # Добавляем пароль для опасных операций (удаление, смена даты)
    password: Optional[str] = None # Будем проверять пароль Владельца

# Используется для расчета стоимости
class CalculateOrderItem(BaseModel):
    order_id: int
    weight_kg: float
class CalculatePayload(BaseModel):
    orders: List[CalculateOrderItem]
    price_per_kg_usd: float
    exchange_rate_usd: float
    new_status: Optional[str] = None # Возможность сразу сменить статус

# Используется для выдачи
class IssueOrderItem(BaseModel):
    order_id: int
    weight_kg: float = Field(..., gt=0)
class IssuePayload(BaseModel):
    orders: List[IssueOrderItem]
    price_per_kg_usd: float = Field(..., gt=0)
    exchange_rate_usd: float = Field(..., gt=0)
    paid_cash: float = Field(..., ge=0) # Может быть 0
    paid_card: float = Field(..., ge=0) # Может быть 0
    card_payment_type: Optional[str] = None # Тип карты, если оплата картой

# --- Эндпоинты для Заказов ---

# main.py (Полностью заменяет get_orders)

@app.get("/api/orders", tags=["Заказы (Владелец)"], response_model=List[OrderOut])
def get_orders(
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db),
    party_dates: Optional[List[date]] = Query(None),
    statuses: Optional[List[str]] = Query(None),
    client_id: Optional[int] = Query(None),
    # --- НОВЫЙ ПАРАМЕТР: Фильтр по филиалу ---
    location_id: Optional[int] = Query(None) 
):
    """
    Получает список заказов компании с возможностью фильтрации.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Всегда видит только заказы своего филиала.
    """
    if employee.company_id is None:
         raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    query = db.query(Order).options(joinedload(Order.client)).filter(
        Order.company_id == employee.company_id 
    )

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---
    if employee.role.name == 'Владелец':
        # Владелец: фильтруем по location_id, ЕСЛИ он передан
        if location_id is not None:
            # Проверяем, что филиал принадлежит компании (на всякий случай)
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден в вашей компании.")
            query = query.filter(Order.location_id == location_id)
            print(f"[Заказы] Владелец ID={employee.id} фильтрует по филиалу ID={location_id}")
        else:
            print(f"[Заказы] Владелец ID={employee.id} просматривает заказы ВСЕХ филиалов.")
            # Если location_id не передан, Владелец видит все
            pass 
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда фильтруем по его location_id
        if employee.location_id is None:
             # Если у сотрудника нет филиала, он не должен видеть заказы
             print(f"[Заказы][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
             return [] # Возвращаем пустой список
        query = query.filter(Order.location_id == employee.location_id)
        print(f"[Заказы] Сотрудник ID={employee.id} просматривает заказы своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    # Фильтры по дате и статусу (остаются без изменений)
    if party_dates:
        query = query.filter(Order.party_date.in_(party_dates))
        
    statuses_to_filter = statuses
    if not statuses_to_filter:
        statuses_to_filter = [s for s in ORDER_STATUSES if s != "Выдан"]
    if statuses_to_filter:
        query = query.filter(Order.status.in_(statuses_to_filter))

    # Фильтр по клиенту (остается без изменений)
    if client_id is not None:
        client_check = db.query(Client).filter(Client.id == client_id, Client.company_id == employee.company_id).first()
        if not client_check:
             raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")
        query = query.filter(Order.client_id == client_id)

    # Сортировка (остается без изменений)
    orders = query.order_by(Order.party_date.desc().nullslast(), Order.id.desc()).all()
    return orders

@app.post("/api/orders", tags=["Заказы (Владелец)"], response_model=OrderOut)
def create_order(
    payload: OrderCreate,
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db)
):
    """Создает новый заказ для клиента ТЕКУЩЕЙ компании, привязывая его к филиалу."""
    if employee.company_id is None: # SuperAdmin не может
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    # 1. Проверяем клиента
    client = db.query(Client).filter(
        Client.id == payload.client_id,
        Client.company_id == employee.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    # 2. Проверка дубликата трек-кода
    if not payload.track_code.startswith("PENDING-"):
         existing_order = db.query(Order).filter(
              Order.track_code == payload.track_code,
              Order.company_id == employee.company_id
         ).first()
         if existing_order:
              raise HTTPException(status_code=400, detail=f"Заказ с трек-кодом '{payload.track_code}' уже существует в вашей компании.")

    # 3. Определение location_id для заказа
    order_location_id = None
    if employee.role.name == 'Владелец':
        # Владелец: Используем location_id из payload ИЛИ его собственный location_id
        if payload.location_id:
            # Проверяем, что указанный филиал принадлежит компании
            loc_check = db.query(Location).filter(Location.id == payload.location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                raise HTTPException(status_code=404, detail="Указанный филиал не найден в вашей компании.")
            order_location_id = payload.location_id
            print(f"[Create Order] Владелец ID={employee.id} выбрал филиал ID={order_location_id}")
        elif employee.location_id:
             order_location_id = employee.location_id # Используем основной филиал Владельца
             print(f"[Create Order] Владелец ID={employee.id} использует свой филиал ID={order_location_id}")
        else:
             # Если у Владельца нет location_id и он не выбрал филиал, ищем первый филиал компании
             first_location = db.query(Location).filter(Location.company_id == employee.company_id).first()
             if not first_location:
                  raise HTTPException(status_code=400, detail="Не найден ни один филиал для привязки заказа.")
             order_location_id = first_location.id
             print(f"[Create Order] Владелец ID={employee.id} не привязан к филиалу, используется первый найденный: ID={order_location_id}")
    else:
        # Обычный сотрудник: Всегда используем его location_id
        if not employee.location_id:
            raise HTTPException(status_code=400, detail="Ошибка: Ваш профиль не привязан к филиалу.")
        order_location_id = employee.location_id
        print(f"[Create Order] Сотрудник ID={employee.id} использует свой филиал ID={order_location_id}")
        
    # 4. Установка статуса и даты партии
    order_status = "Ожидает выкупа" if payload.purchase_type == "Выкуп" else "В обработке"
    order_party_date = payload.party_date if payload.party_date else date.today()

    # 5. Создание объекта Order
    new_order = Order(
        client_id=payload.client_id,
        track_code=payload.track_code,
        status=order_status,
        purchase_type=payload.purchase_type,
        comment=payload.comment,
        party_date=order_party_date,
        buyout_item_cost_cny=payload.buyout_item_cost_cny,
        buyout_commission_percent=payload.buyout_commission_percent,
        buyout_rate_for_client=payload.buyout_rate_for_client,
        buyout_actual_rate=payload.buyout_actual_rate,
        # Привязка к компании и филиалу
        company_id=employee.company_id, 
        location_id=order_location_id # <-- ПРИВЯЗКА К ФИЛИАЛУ
    )
    
    # 6. Сохранение в БД
    try:
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        db.refresh(new_order, attribute_names=['client']) # Загружаем клиента для ответа
        print(f"[Create Order] Заказ ID={new_order.id} успешно создан для филиала ID={order_location_id}")
        return new_order
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при создании заказа:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при создании заказа: {e}")


# main.py (Полностью заменяет функцию update_order)

@app.patch("/api/orders/{order_id}", tags=["Заказы (Владелец)"], response_model=OrderOut)
async def update_order( # Добавляем async для будущих уведомлений, если понадобятся
    order_id: int,
    payload: OrderUpdate, # Используем модель OrderUpdate, которая теперь включает location_id
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db)
):
    """Обновляет данные заказа ТЕКУЩЕЙ компании, включая филиал (для Владельца)."""
    
    # 1. Находим заказ, проверяем принадлежность к компании
    order = db.query(Order).options(joinedload(Order.client)).filter( 
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True) # Берем только переданные поля
    original_status = order.status # Запоминаем для возможных уведомлений

    # 2. Обработка изменения location_id (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА)
    if 'location_id' in update_data:
        if employee.role.name != 'Владелец':
            # Обычный сотрудник не может менять филиал, удаляем поле из данных для обновления
            del update_data['location_id']  
            print(f"[Update Order] Сотрудник ID={employee.id} не может менять филиал заказа ID={order_id}.")
        elif update_data['location_id'] != order.location_id: 
            # Владелец меняет филиал, проверяем, существует ли новый филиал в ЕГО компании
            new_location = db.query(Location).filter(
                Location.id == update_data['location_id'],
                Location.company_id == employee.company_id # Проверка принадлежности филиала
            ).first()
            if not new_location:
                raise HTTPException(status_code=404, detail="Новый филиал не найден в вашей компании.")
            print(f"[Update Order] Владелец ID={employee.id} меняет филиал заказа ID={order_id} на ID={update_data['location_id']}")
        # Если location_id передан, но совпадает со старым, ничего не делаем

    # 3. Обработка смены клиента (если client_id передан)
    if 'client_id' in update_data and update_data['client_id'] != order.client_id:
        new_client = db.query(Client).filter(
            Client.id == update_data['client_id'],
            Client.company_id == employee.company_id # Проверяем принадлежность клиента
        ).first()
        if not new_client:
             raise HTTPException(status_code=404, detail="Новый клиент не найден в вашей компании.")
        # SQLAlchemy обновит client_id автоматически при применении update_data
        print(f"[Update Order] Заказ ID={order_id} переносится на клиента ID={update_data['client_id']}")

    # 4. Проверка дубликата трек-кода при изменении
    if 'track_code' in update_data and update_data['track_code'] != order.track_code:
        if not update_data['track_code'].startswith("PENDING-"): # Игнорируем PENDING-*
             existing_order = db.query(Order).filter(
                 Order.track_code == update_data['track_code'],
                 Order.company_id == employee.company_id,
                 Order.id != order_id # Исключаем текущий заказ
             ).first()
             if existing_order:
                  raise HTTPException(status_code=400, detail=f"Другой заказ с трек-кодом '{update_data['track_code']}' уже существует.")

    # 5. Проверка корректности статуса
    if 'status' in update_data and update_data['status'] not in ORDER_STATUSES:
         raise HTTPException(status_code=400, detail=f"Недопустимый статус заказа: {update_data['status']}")
         
    # 6. Применяем все собранные обновления к объекту заказа
    try:
        for key, value in update_data.items():
            setattr(order, key, value)
        
        db.commit() # Сохраняем все изменения
        db.refresh(order) # Обновляем объект из БД
        db.refresh(order, attribute_names=['client']) # Обновляем связанные данные клиента для ответа

        # [ЗАМЕТКА]: Здесь можно добавить логику отправки уведомлений, если статус изменился
        # if 'status' in update_data and update_data['status'] != original_status:
        #     # ... код отправки уведомления ...

        print(f"[Update Order] Заказ ID={order_id} успешно обновлен.")
        return order # Возвращаем обновленный заказ

    except Exception as e:
        db.rollback() # Откатываем изменения при ошибке
        import traceback
        print(f"!!! Ошибка БД при обновлении заказа ID={order_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении заказа: {e}")

@app.delete("/api/orders/{order_id}", tags=["Заказы (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db),
    # Добавляем требование пароля через Query параметр или Header
    password: str = Query(...) # Требуем пароль как параметр запроса
):
    """Удаляет заказ ТЕКУЩЕЙ компании (ТРЕБУЕТ ПАРОЛЬ ВЛАДЕЛЬЦА)."""
    # Проверяем пароль текущего сотрудника (Владельца)
    if employee.password != password:
         raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения удаления.")

    order = db.query(Order).filter(
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")

    # Доп. проверка: Нельзя удалить ВЫДАННЫЙ заказ? (По желанию)
    # if order.status == "Выдан":
    #     raise HTTPException(status_code=400, detail="Нельзя удалить уже выданный заказ.")

    db.delete(order)
    db.commit()
    return None

@app.get("/api/orders/parties", tags=["Заказы (Владелец)"], response_model=List[date])
def get_order_parties(
    employee: Employee = Depends(get_current_company_employee),
    db: Session = Depends(get_db)
):
    """Получает список уникальных дат партий для ТЕКУЩЕЙ компании."""
    parties = db.query(Order.party_date).filter(
        Order.company_id == employee.company_id,
        Order.party_date.isnot(None) # Исключаем заказы без даты
    ).distinct().order_by(Order.party_date.desc()).all()
    
    # Извлекаем даты из кортежей
    return [p[0] for p in parties]


# === НАЧАЛО ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ bulk_order_action ===

# Эндпоинт для массовых действий (смена статуса, даты, удаление)

@app.post("/api/orders/bulk_action", tags=["Заказы (Владелец)"])
async def bulk_order_action(
    payload: BulkActionPayload,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Выполняет массовые действия над заказами ТЕКУЩЕЙ компании."""
    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="Не выбраны заказы для действия.")

    # 1. Проверка существования и принадлежности к компании
    query = db.query(Order).options(joinedload(Order.client)).filter(
        Order.id.in_(payload.order_ids),
        Order.company_id == employee.company_id 
    )
    orders_to_action = query.all()

    requested_ids_set = set(payload.order_ids)
    found_ids_set = {o.id for o in orders_to_action}

    if len(found_ids_set) != len(requested_ids_set):
        missing_ids = list(requested_ids_set - found_ids_set)
        raise HTTPException(status_code=404, detail=f"Некоторые заказы не найдены в вашей компании: {missing_ids}")

    # --- Обработка различных действий ---

    if payload.action == 'update_status':
        if not payload.new_status or payload.new_status not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус для массового обновления.")
        
        count = query.update({"status": payload.new_status}, synchronize_session='fetch')
        db.commit()

        # [NOTE]: Здесь должна быть асинхронная логика уведомлений, если она нужна.

        return {"status": "ok", "message": f"Статус '{payload.new_status}' установлен для {count} заказов."}

    elif payload.action == 'update_party_date':
        # Требуем пароль Владельца для смены даты
        if not payload.password or employee.password != payload.password:
            raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения смены даты партии.")
        if not payload.new_party_date:
            raise HTTPException(status_code=400, detail="Не указана новая дата партии.")

        count = query.update({"party_date": payload.new_party_date}, synchronize_session='fetch') 
        db.commit()
        return {"status": "ok", "message": f"Дата партии обновлена для {count} заказов."}

    # --- НОВАЯ ЛОГИКА: МАССОВЫЙ ВЫКУП ---
    elif payload.action == 'buyout':
        if not payload.buyout_actual_rate or payload.buyout_actual_rate <= 0:
            raise HTTPException(status_code=400, detail="Не указан корректный реальный курс выкупа.")
            
        # 2. Проверяем, что все выбранные заказы имеют статус "Ожидает выкупа"
        if not all(o.status == "Ожидает выкупа" for o in orders_to_action):
            raise HTTPException(status_code=400, detail="Массовый выкуп возможен только для заказов со статусом 'Ожидает выкупа'.")

        try:
            # Обновляем статус, а также сохраняем реальный курс выкупа
            count = query.update({
                "status": "Выкуплен", 
                "buyout_actual_rate": payload.buyout_actual_rate
            }, synchronize_session='fetch')
            db.commit()
            return {"status": "ok", "message": f"Выкуп и статус 'Выкуплен' успешно применены к {count} заказам."}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при массовом выкупе: {e}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    elif payload.action == 'delete':
        # Требуем пароль Владельца для удаления
        if not payload.password or employee.password != payload.password:
            raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения удаления.")

        ids_to_delete = [o.id for o in orders_to_action] 

        # Удаление
        query_to_delete = db.query(Order).filter(Order.id.in_(ids_to_delete))
        count = query_to_delete.delete(synchronize_session=False) 
        db.commit()
        return {"status": "ok", "message": f"Удалено {count} заказов."}

    else:
        raise HTTPException(status_code=400, detail="Неизвестное массовое действие.")

# === КОНЕЦ ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ bulk_order_action ===

# main.py (Полностью заменяет функцию bulk_import_orders)

@app.post("/api/orders/bulk_import", tags=["Заказы (Владелец)"], response_model=BulkImportResponse)
def bulk_import_orders(
    payload: BulkOrderImportPayload,
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db)
):
    """Массовый импорт заказов из Excel для ТЕКУЩЕЙ компании с привязкой к филиалу."""
    if employee.company_id is None: # SuperAdmin не может
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    created_count = 0
    errors = []
    warnings = []
    # Дата партии по умолчанию - сегодня, если не указана в payload
    import_party_date = payload.party_date if payload.party_date else date.today()

    # --- ЛОГИКА ОПРЕДЕЛЕНИЯ ФИЛИАЛА ДЛЯ ИМПОРТА ---
    import_location_id = None
    if employee.role.name == 'Владелец':
        # Владелец: Используем location_id из payload ИЛИ его собственный location_id
        if payload.location_id:
            # Владелец выбрал филиал в интерфейсе, проверяем его
            loc_check = db.query(Location).filter(Location.id == payload.location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                raise HTTPException(status_code=404, detail="Выбранный для импорта филиал не найден в вашей компании.")
            import_location_id = payload.location_id
            print(f"[Import Orders] Владелец ID={employee.id} импортирует в филиал ID={import_location_id}")
        elif employee.location_id:
             # Используем основной филиал Владельца, если не выбран другой
             import_location_id = employee.location_id
             print(f"[Import Orders] Владелец ID={employee.id} импортирует в свой филиал ID={import_location_id} (по умолчанию)")
        else:
             # Если у Владельца нет location_id и он не выбрал филиал, ищем первый филиал компании
             first_location = db.query(Location).filter(Location.company_id == employee.company_id).first()
             if not first_location:
                  # Это не должно произойти, т.к. при создании компании создается "Главный филиал"
                  raise HTTPException(status_code=400, detail="Критическая ошибка: Не найден ни один филиал в компании для импорта заказов.")
             import_location_id = first_location.id
             print(f"[Import Orders] Владелец ID={employee.id} не привязан к филиалу, импорт в первый найденный: ID={import_location_id}")
    else:
        # Обычный сотрудник: Всегда импортирует в свой филиал
        if not employee.location_id:
            raise HTTPException(status_code=400, detail="Ошибка: Ваш профиль не привязан к филиалу, импорт невозможен.")
        import_location_id = employee.location_id
        print(f"[Import Orders] Сотрудник ID={employee.id} импортирует в свой филиал ID={import_location_id}")
    # --- КОНЕЦ ЛОГИКИ ОПРЕДЕЛЕНИЯ ФИЛИАЛА ---

    # --- Загрузка существующих данных для проверок ---
    company_clients = db.query(Client).filter(Client.company_id == employee.company_id).all()
    clients_by_phone = {c.phone: c for c in company_clients}
    clients_by_code_num = {c.client_code_num: c for c in company_clients if c.client_code_num is not None}
    existing_track_codes = {o.track_code for o in db.query(Order.track_code).filter(Order.company_id == employee.company_id)}
    unknown_client_counter = 1
    # --- Конец загрузки ---

    # --- Обработка каждой строки из payload ---
    for item in payload.orders_data:
        # Валидация трек-кода
        if not item.track_code or not item.track_code.strip():
            errors.append(f"Пропущена строка: отсутствует трек-код.")
            continue
        track_code = item.track_code.strip()
        if track_code in existing_track_codes:
             warnings.append(f"Заказ с трек-кодом '{track_code}' уже существует (пропущен).")
             continue

        # Поиск/создание клиента
        client = None
        client_identifier = ""
        if item.client_code:
             code_str = str(item.client_code).strip()
             client_identifier = f"код '{code_str}'"
             match_num = re.search(r'(\d+)$', code_str)
             if match_num:
                 try:
                     num_val = int(match_num.group(1))
                     client = clients_by_code_num.get(num_val)
                 except ValueError: pass
        if not client and item.phone:
             cleaned_phone = re.sub(r'\D', '', str(item.phone))
             if not client_identifier: client_identifier = f"тел. '{cleaned_phone}'"
             if cleaned_phone: client = clients_by_phone.get(cleaned_phone)
        if not client:
             # --- ИСПРАВЛЕНИЕ: Генерируем уникальный телефон ---
             timestamp_ms = int(datetime.now().timestamp() * 1000)
             unknown_client_phone = f"unknown_{employee.company_id}_{unknown_client_counter}_{timestamp_ms}"
             # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
             client = Client(
                 full_name=f"Неизвестный ({client_identifier or track_code})",
                 phone=unknown_client_phone, # Используем новый уникальный телефон
                 company_id=employee.company_id,
             )
             db.add(client)
             db.flush()
             # Не добавляем в clients_by_phone
             warnings.append(f"Для заказа '{track_code}' не найден клиент ({client_identifier or 'нет идентификатора'}). Создан '{client.full_name}'.")
             unknown_client_counter += 1

        # Определение статуса
        order_status = "Ожидает выкупа" if item.purchase_type == "Выкуп" else "В обработке"

        # Создание объекта Order с ОБЯЗАТЕЛЬНЫМ location_id
        new_order = Order(
            track_code=track_code,
            client_id=client.id,
            company_id=employee.company_id,
            location_id=import_location_id, # <-- ПРИВЯЗКА К ФИЛИАЛУ
            purchase_type=item.purchase_type or "Доставка",
            status=order_status,
            party_date=import_party_date,
            comment=item.comment,
            buyout_item_cost_cny=item.buyout_item_cost_cny,
            buyout_rate_for_client=item.buyout_rate_for_client,
            buyout_commission_percent=item.buyout_commission_percent or 10.0
        )
        db.add(new_order)
        existing_track_codes.add(track_code)
        created_count += 1

        # Периодический flush
        if created_count % 100 == 0:
            try: db.flush()
            except Exception as e_f:
                 db.rollback(); errors.append(f"Ошибка flush (~{created_count}): {e_f}"); break
    # --- Конец цикла обработки строк ---

    # Финальный commit
    try: db.commit()
    except Exception as e_c:
         db.rollback(); errors.append(f"Критическая ошибка commit: {e_c}"); created_count = 0

    return {
        "status": "ok",
        "message": "Импорт заказов завершен.",
        "created_clients": created_count, # Название поля неудачное, но оставим
        "errors": errors,
        "warnings": warnings
    }

# === КОНЕЦ НОВОГО КОДА (ЗАКАЗЫ) ===

# === НАЧАЛО НОВОГО КОДА (СМЕНЫ И ТИПЫ РАСХОДОВ) ===

# --- Эндпоинты для Смен ---

@app.get("/api/shifts/active", tags=["Смены"], response_model=Optional[ShiftOut])
def get_active_shift(
    employee: Employee = Depends(get_current_active_employee),  
    db: Session = Depends(get_db)
):
    """
    Возвращает активную смену для филиала текущего сотрудника.
    Если сотрудник - Владелец, возвращает активную смену его основного филиала 
    для возможности закрытия.
    """
    if employee.company_id is None:
        return None

    location_id_to_check = employee.location_id
    
    if location_id_to_check is None:
        return None

    # Ищем активную смену в филиале, к которому привязан сотрудник/владелец.
    # Если найдена, это активная смена "этого рабочего места".
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == location_id_to_check,
        Shift.end_time == None
    ).first()

    return active_shift

# main.py (Функция get_all_active_shifts)

@app.get("/api/shifts/all_active", tags=["Смены"], response_model=List[ShiftOut])
def get_all_active_shifts(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Возвращает ВСЕ активные смены для ТЕКУЩЕЙ компании."""
    active_shifts = db.query(Shift).options(
        joinedload(Shift.employee) # <-- ЭТО КРИТИЧЕСКАЯ ЧАСТЬ
    ).filter(
        Shift.company_id == employee.company_id,
        Shift.end_time == None
    ).all()
    return active_shifts
    
# === КОНЕЦ ИСПРАВЛЕННОЙ ФУНКЦИИ get_active_shift ===

# === НАЧАЛО ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ open_shift ===
@app.post("/api/shifts/open", tags=["Касса и Смены"], response_model=ShiftOut)
def open_shift(
    payload: ShiftOpenPayload,
    opener_employee: Employee = Depends(get_current_active_employee), # Сотрудник, выполняющий действие
    db: Session = Depends(get_db)
):
    """Открывает новую смену."""
    # --- ДОБАВЛЕНО ЛОГИРОВАНИЕ ---
    print(f"--- Попытка открыть смену ---")
    print(f"Действие выполняет: ID={opener_employee.id}, Имя={opener_employee.full_name}, Роль={opener_employee.role.name}, Компания={opener_employee.company_id}")
    print(f"Полученные данные (payload): {payload.dict()}")
    # --- КОНЕЦ ЛОГИРОВАНИЯ ---

    try: # Оборачиваем всю логику в try...except
        if opener_employee.company_id is None:
             print("[ОШИБКА] Супер-админ не может открывать смены.") # Лог
             raise HTTPException(status_code=403, detail="Супер-админ не может открывать смены.")

        # Проверяем права на открытие смены
        opener_perms = {p.codename for p in opener_employee.role.permissions}
        if 'open_close_shift' not in opener_perms:
            print(f"[ОШИБКА] У сотрудника ID={opener_employee.id} нет прав 'open_close_shift'.") # Лог
            raise HTTPException(status_code=403, detail="У вас нет прав на открытие/закрытие смен.")

        # 1. Проверка: Сотрудник, ФИЛИАЛ и Компания должны совпадать
        print(f"Проверка целевого сотрудника ID={payload.employee_id} и филиала ID={payload.location_id} в компании ID={opener_employee.company_id}...") # Лог
        target_employee = db.query(Employee).filter(
             Employee.id == payload.employee_id,
             Employee.company_id == opener_employee.company_id
        ).first()
        target_location = db.query(Location).filter(
             Location.id == payload.location_id,
             Location.company_id == opener_employee.company_id
        ).first()

        if not target_employee:
             print(f"[ОШИБКА] Целевой сотрудник ID={payload.employee_id} не найден в компании ID={opener_employee.company_id}.") # Лог
             raise HTTPException(status_code=404, detail="Целевой сотрудник не найден в вашей компании.")
        if not target_location:
             print(f"[ОШИБКА] Целевой филиал ID={payload.location_id} не найден в компании ID={opener_employee.company_id}.") # Лог
             raise HTTPException(status_code=404, detail="Целевой филиал не найден в вашей компании.")
        print(f"Сотрудник и филиал найдены: Сотрудник='{target_employee.full_name}', Филиал='{target_location.name}'.") # Лог

        # 2. Проверка: В этом филиале не должно быть уже открытой смены
        print(f"Проверка существующей активной смены в филиале ID={payload.location_id}...") # Лог
        existing_active_shift = db.query(Shift).filter(
            Shift.company_id == opener_employee.company_id,
            Shift.location_id == payload.location_id, # Проверяем именно ЦЕЛЕВОЙ филиал
            Shift.end_time == None
        ).first()
        if existing_active_shift:
            print(f"[ОШИБКА] Активная смена ID={existing_active_shift.id} уже существует в филиале ID={payload.location_id}.") # Лог
            raise HTTPException(status_code=400, detail=f"Нельзя открыть новую смену в филиале '{target_location.name}', пока не закрыта предыдущая.")
        print("Активной смены в этом филиале нет. Продолжаем...") # Лог

        # 3. Создаем новую смену
        print("Создание объекта Shift...") # Лог
        new_shift = Shift(
            starting_cash=payload.starting_cash,
            exchange_rate_usd=payload.exchange_rate_usd,
            price_per_kg_usd=payload.price_per_kg_usd,
            employee_id=payload.employee_id, # Сотрудник, который будет работать
            location_id=payload.location_id, # Филиал, где открыта смена
            company_id=opener_employee.company_id # Компания
        )
        print(f"Объект Shift создан (еще не в БД): {new_shift.__dict__}") # Лог

        try:
            print("Добавление смены в сессию (db.add)...") # Лог
            db.add(new_shift)
            print("Выполнение db.commit...") # Лог
            db.commit()
            print("Выполнение db.refresh...") # Лог
            db.refresh(new_shift)
            print(f"Смена ID={new_shift.id} успешно сохранена в БД.") # Лог
            return new_shift
        except Exception as e_db:
            db.rollback()
            import traceback
            print(f"!!! КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ при сохранении смены:\n{traceback.format_exc()}") # Лог
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при открытии смены: {e_db}")

    except HTTPException as http_exc:
         # Просто пробрасываем HTTP исключения дальше
         raise http_exc
    except Exception as e_main:
        # Ловим любые другие неожиданные ошибки
        import traceback
        print(f"!!! НЕОЖИДАННАЯ КРИТИЧЕСКАЯ ОШИБКА в функции open_shift:\n{traceback.format_exc()}") # Лог
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при открытии смены: {e_main}")

# === КОНЕЦ ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ open_shift ===

@app.post("/api/shifts/close", tags=["Касса и Смены"], response_model=ShiftOut)
def close_shift(
    payload: ShiftClosePayload,
    closer_employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Закрывает ТЕКУЩУЮ АКТИВНУЮ смену в ФИЛИАЛЕ сотрудника."""
    if closer_employee.is_super_admin:
         raise HTTPException(status_code=403, detail="Супер-админ не может закрывать смены.")

    # Проверяем права на закрытие смены
    closer_perms = {p.codename for p in closer_employee.role.permissions}
    if 'open_close_shift' not in closer_perms:
         raise HTTPException(status_code=403, detail="У вас нет прав на открытие/закрытие смен.")

    # Находим активную смену в ТЕКУЩЕМ филиале сотрудника
    active_shift = db.query(Shift).filter(
        Shift.company_id == closer_employee.company_id,
        Shift.location_id == closer_employee.location_id, # Важно: закрываем смену своего филиала
        Shift.end_time == None
    ).first()

    if not active_shift:
        # Используем 404, чтобы фронтенд понял, что активной смены нет
        raise HTTPException(status_code=404, detail="Активная смена в вашем филиале не найдена.")

    # Закрываем смену
    active_shift.end_time = datetime.now() # Используем aware datetime
    active_shift.closing_cash = payload.closing_cash
    db.commit()
    db.refresh(active_shift)
    return active_shift


# --- Эндпоинты для Типов Расходов ---

@app.get("/api/expense_types", tags=["Расходы (Владелец)"], response_model=List[ExpenseTypeOut])
def get_expense_types(
    employee: Employee = Depends(get_current_company_employee), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Получает все типы расходов для ТЕКУЩЕЙ компании."""
    types = db.query(ExpenseType).filter(
        ExpenseType.company_id == employee.company_id
    ).order_by(ExpenseType.name).all()
    return types

@app.post("/api/expense_types", tags=["Расходы (Владелец)"], response_model=ExpenseTypeOut)
def create_expense_type(
    payload: ExpenseTypeCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новый тип расхода для ТЕКУЩЕЙ компании."""
    # Проверка прав (на всякий случай, хотя依赖 уже проверила)
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    # Проверка на дубликат имени ВНУТРИ компании
    if db.query(ExpenseType).filter(ExpenseType.name == payload.name, ExpenseType.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Тип расхода с таким названием уже существует.")

    new_type = ExpenseType(
        name=payload.name,
        company_id=employee.company_id
    )
    db.add(new_type)
    db.commit()
    db.refresh(new_type)
    return new_type

@app.patch("/api/expense_types/{type_id}", tags=["Расходы (Владелец)"], response_model=ExpenseTypeOut)
def update_expense_type(
    type_id: int,
    payload: ExpenseTypeUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет название типа расхода ТЕКУЩЕЙ компании."""
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    exp_type = db.query(ExpenseType).filter(
        ExpenseType.id == type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not exp_type:
        raise HTTPException(status_code=404, detail="Тип расхода не найден.")

    # Проверка на дубликат нового имени
    if payload.name != exp_type.name and db.query(ExpenseType).filter(ExpenseType.name == payload.name, ExpenseType.company_id == employee.company_id).first():
         raise HTTPException(status_code=400, detail="Тип расхода с таким новым названием уже существует.")

    exp_type.name = payload.name
    db.commit()
    db.refresh(exp_type)
    return exp_type

@app.delete("/api/expense_types/{type_id}", tags=["Расходы (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_expense_type(
    type_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Удаляет тип расхода ТЕКУЩЕЙ компании."""
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    exp_type = db.query(ExpenseType).filter(
        ExpenseType.id == type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not exp_type:
        raise HTTPException(status_code=404, detail="Тип расхода не найден.")

    # Проверка, используется ли тип в каких-либо расходах
    expense_count = db.query(Expense).filter(Expense.expense_type_id == type_id).count()
    if expense_count > 0:
        raise HTTPException(status_code=400, detail=f"Нельзя удалить тип '{exp_type.name}', так как он используется в {expense_count} записях о расходах.")

    db.delete(exp_type)
    db.commit()
    return None

# === НАЧАЛО НОВОГО КОДА (РАСХОДЫ) ===

# --- Эндпоинты для Расходов ---

# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ create_expense)
@app.post("/api/expenses", tags=["Расходы"], response_model=ExpenseOut)
def create_expense(
    payload: ExpenseCreate, # Теперь payload содержит shift_id
    employee: Employee = Depends(get_current_active_employee), 
    db: Session = Depends(get_db)
):
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    perms = {p.codename for p in employee.role.permissions}
    if 'add_expense' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на добавление расходов.")

    expense_type = db.query(ExpenseType).filter(
        ExpenseType.id == payload.expense_type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not expense_type:
        raise HTTPException(status_code=404, detail="Указанный тип расхода не найден.")

    shift_id_for_expense = None 

    if employee.role.name == 'Владелец':
        # Владелец: Используем shift_id из payload (если он есть и валиден)
        if payload.shift_id is not None:
            shift_check = db.query(Shift).filter(
                Shift.id == payload.shift_id, 
                Shift.company_id == employee.company_id,
                Shift.end_time == None).first()
            if shift_check:
                shift_id_for_expense = payload.shift_id
                print(f"[Expense] Владелец ID={employee.id} привязывает расход к смене ID={payload.shift_id}")
            else:
                print(f"[Expense] Владелец ID={employee.id} пытался привязать расход к неактивной/чужой смене {payload.shift_id}. Сохранено как Общий.")
                shift_id_for_expense = None 
        else:
            shift_id_for_expense = None
            print(f"[Expense] Владелец ID={employee.id} добавляет расход без привязки (Общий).")
    else:
        # Сотрудник: Требуется активная смена в его филиале
        active_shift = db.query(Shift).filter(
            Shift.company_id == employee.company_id,
            Shift.location_id == employee.location_id,
            Shift.end_time == None
        ).first()
        if not active_shift:
            raise HTTPException(status_code=400, detail="Нет активной смены для добавления расхода. Откройте смену.")
        shift_id_for_expense = active_shift.id 
        print(f"[Expense] Сотрудник ID={employee.id} добавляет расход к смене ID={active_shift.id}")

    new_expense = Expense(
        amount=payload.amount,
        notes=payload.notes,
        expense_type_id=payload.expense_type_id,
        shift_id=shift_id_for_expense, 
        company_id=employee.company_id 
    )

    try:
        db.add(new_expense)
        db.commit()
        db.refresh(new_expense)
        db.refresh(new_expense, attribute_names=['expense_type'])
        print(f"[Expense] Расход ID={new_expense.id} успешно добавлен.")
        return new_expense
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных: {e}")

# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ get_expenses)

@app.get("/api/expenses", tags=["Расходы"], response_model=List[ExpenseOut])
def get_expenses(
    start_date: date, # Обязательный параметр начала периода
    end_date: date,   # Обязательный параметр конца периода
    employee: Employee = Depends(get_current_active_employee), # Любой сотрудник компании
    db: Session = Depends(get_db)
):
    """Получает список расходов ТЕКУЩЕЙ компании за указанный период."""
    # === ИСПРАВЛЕНИЕ КРИТИЧЕСКОЙ ОШИБКИ: Используем company_id вместо is_super_admin ===
    if employee.company_id is None: 
         # Супер-админу пока не даем доступ к расходам компаний
         raise HTTPException(status_code=403, detail="Доступ к расходам для SuperAdmin не реализован.")
    # === КОНЕЦ ИСПРАВЛЕНИЯ ===

    # Проверка прав на просмотр расходов 
    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms and 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр расходов.")

    print(f"[Expense] Запрос списка расходов для компании ID={employee.company_id} за период {start_date} - {end_date}")

    # Формируем границы периода (включая весь день end_date)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    # --- ИСПРАВЛЕНИЕ ОШИБКИ ЗАГРУЗКИ ---
    # Запрашиваем расходы компании за период, сразу подгружая связанные данные
    expenses = db.query(Expense).options(
        # ИСПРАВЛЕНИЕ 1: Правильно загружаем Тип Расхода
        joinedload(Expense.expense_type), 
        # ИСПРАВЛЕНИЕ 2: Правильно загружаем Смену и Сотрудника смены
        joinedload(Expense.shift).joinedload(Shift.employee) 
    ).filter(
    # --- КОНЕЦ ИСПРАВЛЕНИЙ ---
        Expense.company_id == employee.company_id,
        Expense.created_at >= start_datetime,
        Expense.created_at <= end_datetime
    ).order_by(Expense.created_at.desc()).all() # Сортируем по дате (новые вверху)

    print(f"[Expense] Найдено {len(expenses)} расходов за период.")
    return expenses


@app.patch("/api/expenses/{expense_id}", tags=["Расходы"], response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Обновляет существующий расход."""
    if employee.is_super_admin:
         raise HTTPException(status_code=403, detail="Супер-админ не может редактировать расходы.")

    # Находим расход, который нужно обновить
    expense = db.query(Expense).options(
         joinedload(Expense.shift) # Загружаем смену для проверки даты
    ).filter(
        Expense.id == expense_id,
        Expense.company_id == employee.company_id # Убеждаемся, что расход из той же компании
    ).first()

    if not expense:
        raise HTTPException(status_code=404, detail="Расход не найден в вашей компании.")

    # --- Проверка Прав на Редактирование ---
    can_edit = False
    perms = {p.codename for p in employee.role.permissions}

    # Проверяем, активна ли смена, к которой привязан расход
    is_shift_active = expense.shift and expense.shift.end_time is None
    
    # 1. Можно редактировать расходы в ТЕКУЩЕЙ АКТИВНОЙ смене, если есть право 'add_expense'
    if is_shift_active and 'add_expense' in perms:
          can_edit = True
          print(f"[Expense Update] Разрешено: Редактирование в активной смене.")

    # 2. Владелец может редактировать ЛЮБЫЕ расходы своей компании
    if employee.role.name == 'Владелец':
         can_edit = True
         print(f"[Expense Update] Разрешено: Редактирование Владельцем.")

    if not can_edit:
        print(f"[Expense Update] Запрещено: Сотрудник ID={employee.id} не может редактировать расход ID={expense_id} (Смена закрыта или нет прав).")
        raise HTTPException(status_code=403, detail="У вас нет прав на редактирование этого расхода (возможно, он из закрытой смены).")
    # --- Конец Проверки Прав ---


    update_data = payload.dict(exclude_unset=True) # Берем только переданные поля

    # Проверяем новый тип расхода, если он передан
    if 'expense_type_id' in update_data:
        new_expense_type = db.query(ExpenseType).filter(
            ExpenseType.id == update_data['expense_type_id'],
            ExpenseType.company_id == employee.company_id
        ).first()
        if not new_expense_type:
            raise HTTPException(status_code=404, detail="Новый тип расхода не найден в вашей компании.")

    # Применяем обновления
    print(f"[Expense Update] Обновление расхода ID={expense_id}. Данные:", update_data)
    for key, value in update_data.items():
        setattr(expense, key, value)

    try:
        db.commit()
        db.refresh(expense)
        # Перезагружаем тип расхода для корректного ответа
        db.refresh(expense, attribute_names=['expense_type'])
        print(f"[Expense Update] Расход ID={expense_id} успешно обновлен.")
        return expense
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при обновлении расхода ID={expense_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении расхода: {e}")

# === НАЧАЛО НОВОЙ ФУНКЦИИ DELETE ===
@app.delete("/api/expenses/{expense_id}", tags=["Расходы"], status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db),
    password: str = Query(...) # Требуем пароль как параметр запроса
):
    """Удаляет запись о расходе (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА И ТРЕБУЕТ ПАРОЛЬ)."""
    
    if employee.is_super_admin:
        raise HTTPException(status_code=403, detail="Супер-админ не может удалять расходы компаний.")

    # Проверка: Только Владелец может удалять расходы
    if employee.role.name != 'Владелец':
        raise HTTPException(status_code=403, detail="Только Владелец компании может удалять записи о расходах.")
    
    # Проверка пароля Владельца
    if employee.password != password:
        raise HTTPException(status_code=403, detail="Неверный пароль Владельца для подтверждения удаления.")

    # Находим расход, который нужно удалить
    expense = db.query(Expense).options(
        joinedload(Expense.shift)
    ).filter(
        Expense.id == expense_id,
        Expense.company_id == employee.company_id # Убеждаемся, что расход из той же компании
    ).first()

    if not expense:
        raise HTTPException(status_code=404, detail="Расход не найден в вашей компании.")

    # Запрещаем удаление, если смена уже закрыта (дополнительная мера безопасности)
    if expense.shift and expense.shift.end_time is not None:
        raise HTTPException(status_code=400, detail="Нельзя удалить расход из закрытой смены.")

    # Удаляем расход
    try:
        db.delete(expense)
        db.commit()
        print(f"[Expense Delete] Расход ID={expense_id} успешно удален Владельцем ID={employee.id}.")
        return None
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при удалении расхода ID={expense_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при удалении расхода: {e}")
# === КОНЕЦ НОВОЙ ФУНКЦИИ DELETE ===

# === КОНЕЦ НОВОГО КОДА (РАСХОДЫ) ===

# main.py (Добавляем полный блок для Выдачи)

# --- Эндпоинты для Выдачи Заказов ---
# main.py (Полностью заменяет get_orders_ready_for_issue)

@app.get("/api/orders/ready_for_issue", tags=["Выдача"], response_model=List[OrderOut])
def get_orders_ready_for_issue(
    employee: Employee = Depends(get_current_active_employee), 
    db: Session = Depends(get_db),
    # --- НОВЫЙ ПАРАМЕТР: Фильтр по филиалу (для Владельца) ---
    location_id: Optional[int] = Query(None) 
):
    """
    Получает список заказов со статусом 'Готов к выдаче'.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Всегда видит только заказы своего филиала.
    Требует права 'issue_orders'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр заказов для выдачи.")

    query = db.query(Order).options(
        joinedload(Order.client) 
    ).filter(
        Order.company_id == employee.company_id,
        Order.status == "Готов к выдаче" # Основной фильтр для выдачи
    )

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---
    if employee.role.name == 'Владелец':
        if location_id is not None:
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден.")
            query = query.filter(Order.location_id == location_id)
            print(f"[Выдача] Владелец ID={employee.id} фильтрует по филиалу ID={location_id}")
        else:
             print(f"[Выдача] Владелец ID={employee.id} видит готовые заказы ВСЕХ филиалов.")
             pass # Владелец видит все, если location_id не указан
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда фильтруем по его location_id
        if employee.location_id is None:
             print(f"[Выдача][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
             return [] 
        query = query.filter(Order.location_id == employee.location_id)
        print(f"[Выдача] Сотрудник ID={employee.id} видит готовые заказы своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    # Сортировка
    orders = query.order_by(Order.client_id, Order.id).all() 

    print(f"[Выдача] Найдено {len(orders)} заказов для выдачи (с учетом фильтра филиала).")
    return orders

@app.post("/api/orders/issue", tags=["Выдача"])
def issue_orders(
    payload: IssuePayload,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Оформляет выдачу одного или нескольких заказов.
    Требует права 'issue_orders' и активную смену.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    # Проверка прав
    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на выдачу заказов.")

    # Проверка активной смены (выдача всегда привязана к смене)
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == employee.location_id, # В филиале текущего сотрудника
        Shift.end_time == None
    ).first()
    if not active_shift:
        raise HTTPException(status_code=400, detail="Нет активной смены для оформления выдачи. Откройте смену.")

    order_ids = [item.order_id for item in payload.orders]
    
    # Находим заказы, проверяем их статус и принадлежность компании
    orders_to_issue = db.query(Order).filter(
        Order.id.in_(order_ids),
        Order.company_id == employee.company_id
    ).all()

    # Проверки
    if len(orders_to_issue) != len(order_ids):
        found_ids = {o.id for o in orders_to_issue}
        missing_ids = [oid for oid in order_ids if oid not in found_ids]
        raise HTTPException(status_code=404, detail=f"Заказы с ID {missing_ids} не найдены в вашей компании.")
        
    for order in orders_to_issue:
        if order.status != "Готов к выдаче":
            raise HTTPException(status_code=400, detail=f"Заказ #{order.id} ({order.track_code}) не готов к выдаче (статус: {order.status}).")
            
    # Расчет общей суммы к оплате
    total_cost_to_pay = 0
    order_weights = {item.order_id: item.weight_kg for item in payload.orders}
    
    for order in orders_to_issue:
        weight = order_weights.get(order.id)
        if not weight or weight <= 0:
             raise HTTPException(status_code=400, detail=f"Не указан корректный вес для заказа #{order.id}.")
        
        # Используем предрасчитанную стоимость, ЕСЛИ она есть, иначе считаем по данным из payload
        if order.calculated_final_cost_som and order.calculated_weight_kg == weight:
             cost = order.calculated_final_cost_som
        else:
             cost = weight * payload.price_per_kg_usd * payload.exchange_rate_usd
        
        total_cost_to_pay += cost

    total_paid = payload.paid_cash + payload.paid_card
    
    # Проверка оплаты (оплачено должно быть не меньше, чем к оплате)
    # Допускаем небольшую погрешность в 1 сом
    if total_paid < (total_cost_to_pay - 1): 
         raise HTTPException(status_code=400, detail=f"Недостаточно оплаты. К оплате: {total_cost_to_pay:.2f} сом, Оплачено: {total_paid:.2f} сом.")

    # Оформляем выдачу для каждого заказа
    now = datetime.now() # Время выдачи
    issued_count = 0
    try:
        for order in orders_to_issue:
            item_data = next((item for item in payload.orders if item.order_id == order.id), None)
            if item_data: # Должен всегда находиться
                order.status = "Выдан"
                order.weight_kg = item_data.weight_kg # Фактический вес при выдаче
                order.price_per_kg_usd = payload.price_per_kg_usd
                order.exchange_rate_usd = payload.exchange_rate_usd
                # Перезаписываем final_cost_som на основе фактических данных при выдаче
                order.final_cost_som = (item_data.weight_kg * payload.price_per_kg_usd * payload.exchange_rate_usd)
                
                # Распределяем оплату пропорционально (упрощенный вариант - делим поровну)
                # TODO: Можно улучшить распределение, если нужно
                order.paid_cash_som = payload.paid_cash / len(orders_to_issue)
                order.paid_card_som = payload.paid_card / len(orders_to_issue)
                order.card_payment_type = payload.card_payment_type if payload.paid_card > 0 else None
                
                order.issued_at = now # Время выдачи
                order.shift_id = active_shift.id # Привязка к смене
                order.reverted_at = None # Сбрасываем флаг возврата, если он был
                issued_count += 1
                
        db.commit()
        print(f"[Выдача] Успешно выдано {issued_count} заказов. Смена ID={active_shift.id}, Сотрудник ID={employee.id}")
        return {"status": "ok", "message": f"Успешно выдано {issued_count} заказов."}
        
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при оформлении выдачи:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при оформлении выдачи: {e}")


# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ get_issued_orders)

@app.get("/api/orders/issued", tags=["Выдача"], response_model=List[OrderOut])
def get_issued_orders(
    start_date: date, 
    end_date: date,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db),
    # --- НОВЫЙ НЕОБЯЗАТЕЛЬНЫЙ ПАРАМЕТР ---
    location_id: Optional[int] = Query(None)
):
    """
    Получает историю выданных заказов за период.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Видит только свой филиал.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")
        
    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms and 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр истории выданных.")

    print(f"[Выдача История] Запрос для компании ID={employee.company_id} за {start_date} - {end_date}")

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    query = db.query(Order).options(
        joinedload(Order.client)
    ).filter(
        Order.company_id == employee.company_id,
        Order.status == "Выдан",
        Order.issued_at >= start_datetime,
        Order.issued_at <= end_datetime
    )

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---
    if employee.role.name == 'Владелец':
        if location_id is not None:
            # Проверяем филиал на всякий случай
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден.")
            query = query.filter(Order.location_id == location_id)
            print(f"[Выдача История] Владелец ID={employee.id} фильтрует по филиалу ID={location_id}")
        # else: Владелец видит все, если location_id не указан
            
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда фильтруем по его location_id
        if employee.location_id is None:
             print(f"[Выдача История][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
             return [] 
        query = query.filter(Order.location_id == employee.location_id)
        print(f"[Выдача История] Сотрудник ID={employee.id} видит историю своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    orders = query.order_by(Order.issued_at.desc()).all()

    print(f"[Выдача История] Найдено {len(orders)} выданных заказов за период (с учетом фильтра).")
    return orders

@app.patch("/api/orders/{order_id}/revert_status", tags=["Выдача"], response_model=OrderOut)
def revert_order_status(
    order_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Возвращает статус выданного заказа обратно на 'Готов к выдаче'.
    Требует права 'issue_orders'. Доступно Владельцу или сотруднику в активной смене.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms: # Требуем те же права, что и на выдачу
        raise HTTPException(status_code=403, detail="У вас нет прав на отмену выдачи.")

    order = db.query(Order).options(joinedload(Order.shift)).filter( # Загружаем смену для проверки
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")
    if order.status != "Выдан":
        raise HTTPException(status_code=400, detail="Можно вернуть только заказ со статусом 'Выдан'.")

    # --- ПРОВЕРКА ВОЗМОЖНОСТИ ВОЗВРАТА ---
    can_revert = False
    if employee.role.name == 'Владелец':
        can_revert = True # Владелец может всегда
    else:
        # Сотрудник может вернуть, только если СМЕНА, в которую была выдача, ЕЩЕ АКТИВНА
        if order.shift and order.shift.end_time is None:
            # И если это смена ТЕКУЩЕГО СОТРУДНИКА (доп. безопасность)
            if order.shift.employee_id == employee.id:
                 can_revert = True
    
    if not can_revert:
         raise HTTPException(status_code=403, detail="Отмена выдачи невозможна (смена закрыта или у вас нет прав).")
    # --- КОНЕЦ ПРОВЕРКИ ---

    try:
        order.status = "Готов к выдаче"
        order.reverted_at = datetime.now() # Фиксируем время возврата
        # Обнуляем данные о выдаче
        order.issued_at = None
        order.shift_id = None
        order.weight_kg = None # Сбрасываем фактический вес
        order.final_cost_som = None # Сбрасываем фактическую стоимость
        order.paid_cash_som = None
        order.paid_card_som = None
        order.card_payment_type = None
        
        db.commit()
        db.refresh(order)
        db.refresh(order, attribute_names=['client']) # Обновляем клиента для ответа
        print(f"[Выдача] Статус заказа ID={order_id} возвращен на 'Готов к выдаче'. Сотрудник ID={employee.id}")
        return order
        
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при возврате статуса заказа ID={order_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при возврате статуса: {e}")

# --- КОНЕЦ БЛОКА ВЫДАЧИ ---

# main.py (Добавьте этот блок)

# --- Эндпоинты для Отчетов (Multi-Tenant) ---

def calculate_shift_report_data(db: Session, shift: Shift) -> ShiftReport:
    """Вспомогательная функция для расчета данных по одной смене."""
    
    # 1. Доходы (только заказы, выданные в ЭТУ смену)
    issued_orders_in_shift = db.query(Order).filter(
        Order.shift_id == shift.id, 
        Order.status == "Выдан"
    ).all()
    total_cash_income = sum(o.paid_cash_som for o in issued_orders_in_shift if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders_in_shift if o.paid_card_som)

    # 2. Расходы (только расходы, привязанные к ЭТОЙ смене)
    # Исключаем ЗП и Аванс из операционных расходов смены
    expenses_in_shift = db.query(Expense).join(ExpenseType).filter(
        Expense.shift_id == shift.id,
        ExpenseType.name.notin_(['Зарплата', 'Аванс']) 
    ).all()
    total_expenses = sum(exp.amount for exp in expenses_in_shift)

    # 3. Возвраты (Заказы, возвращенные В ТЕЧЕНИЕ этой смены)
    # (Эта логика может быть сложной, если возврат происходит в другую смену, пока упрощаем)
    total_returns = 0 # TODO: Реализовать логику возвратов, если потребуется

    # 4. Расчет
    calculated_cash = shift.starting_cash + total_cash_income - total_expenses - total_returns
    discrepancy = None
    if shift.end_time and shift.closing_cash is not None:
        discrepancy = shift.closing_cash - calculated_cash

    # Загружаем связанные данные (если они еще не загружены)
    location_name = db.query(Location.name).filter(Location.id == shift.location_id).scalar() or "Неизвестный филиал"
    employee_name = db.query(Employee.full_name).filter(Employee.id == shift.employee_id).scalar() or "Неизвестный сотрудник"

    return ShiftReport(
        shift_id=shift.id,
        shift_start_time=shift.start_time,
        shift_end_time=shift.end_time,
        employee_name=employee_name,
        location_name=location_name,
        starting_cash=shift.starting_cash,
        cash_income=total_cash_income,
        card_income=total_card_income,
        total_expenses=total_expenses,
        total_returns=total_returns,
        calculated_cash=calculated_cash,
        actual_closing_cash=shift.closing_cash,
        discrepancy=discrepancy
    )

@app.get("/api/reports/shift/current", tags=["Отчеты"], response_model=ShiftReport)
def get_current_shift_report(
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Получает отчет для ТЕКУЩЕЙ АКТИВНОЙ смены сотрудника.
    Доступно сотрудникам с правом 'view_shift_report'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")
        
    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр отчета по смене.")

    if employee.location_id is None:
         raise HTTPException(status_code=400, detail="Вы не привязаны к филиалу.")

    # Ищем активную смену в филиале сотрудника
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == employee.location_id,
        Shift.end_time == None
    ).first()

    if not active_shift:
        raise HTTPException(status_code=404, detail="Активная смена в вашем филиале не найдена.")

    # Рассчитываем отчет
    report_data = calculate_shift_report_data(db, active_shift)
    return report_data

# main.py (Добавьте этот НОВЫЙ эндпоинт)

@app.get("/api/reports/shift/location/{location_id}", tags=["Отчеты"], response_model=ShiftReport)
def get_current_shift_report_by_location(
    location_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Получает отчет для АКТИВНОЙ смены в УКАЗАННОМ ФИЛИАЛЕ.
    Доступно Владельцу или сотруднику этого филиала.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр отчета.")

    # Проверка, что филиал принадлежит компании
    loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
    if not loc_check:
         raise HTTPException(status_code=404, detail="Филиал не найден в вашей компании.")

    # Владелец может смотреть любой свой филиал
    if employee.role.name != 'Владелец':
        # Сотрудник может смотреть только свой филиал
        if employee.location_id != location_id:
             raise HTTPException(status_code=403, detail="Вы не можете просматривать отчеты других филиалов.")

    # Ищем активную смену в УКАЗАННОМ филиале
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == location_id, # <-- Используем location_id из URL
        Shift.end_time == None
    ).first()

    if not active_shift:
        raise HTTPException(status_code=404, detail=f"Активная смена в филиале '{loc_check.name}' не найдена.")

    # Рассчитываем отчет
    report_data = calculate_shift_report_data(db, active_shift)
    return report_data

@app.get("/api/reports/shift/{shift_id}", tags=["Отчеты"], response_model=ShiftReport)
def get_past_shift_report(
    shift_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Получает отчет для УКАЗАННОЙ (закрытой) смены. Доступно Владельцу."""
    # (Мы можем расширить права, если нужно)
    perms = {p.codename for p in employee.role.permissions}
    if 'view_full_reports' not in perms: # Только те, кто видит сводные отчеты
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр истории отчетов.")

    shift = db.query(Shift).filter(
        Shift.id == shift_id,
        Shift.company_id == employee.company_id # Проверка принадлежности компании
    ).first()

    if not shift:
        raise HTTPException(status_code=404, detail="Смена не найдена в вашей компании.")

    report_data = calculate_shift_report_data(db, shift)
    return report_data


@app.get("/api/reports/summary", tags=["Отчеты"])
def get_summary_report(
    start_date: date,
    end_date: date,
    location_id: Optional[int] = Query(None), # Добавляем необязательный фильтр по филиалу
    db: Session = Depends(get_db),
    current_employee: Employee = Depends(get_current_company_employee) # Используем новую зависимость
):
    company_id = current_employee.company_id

    # Определяем ID филиалов, к которым у пользователя есть доступ
    accessible_location_ids = []
    if current_employee.is_company_owner:
        if location_id: # Если Владелец выбрал конкретный филиал
             # Проверяем, что выбранный филиал принадлежит этой компании
             location = db.query(Location).filter(Location.id == location_id, Location.company_id == company_id).first()
             if not location:
                  raise HTTPException(status_code=404, detail="Выбранный филиал не найден или не принадлежит вашей компании.")
             accessible_location_ids = [location_id]
        else: # Если Владелец не выбрал филиал (отчет по всей компании)
             accessible_location_ids = [loc.id for loc in db.query(Location.id).filter(Location.company_id == company_id).all()]
    else: # Обычный сотрудник видит отчет только по своему филиалу
        accessible_location_ids = [current_employee.location_id]

    if not accessible_location_ids:
         # На случай, если у компании нет филиалов или произошла ошибка
         raise HTTPException(status_code=400, detail="Не удалось определить филиалы для отчета.")


    # --- Корректная обработка диапазона дат ---
    start_datetime = datetime.combine(start_date, time.min) # Начало дня start_date 00:00:00
    end_datetime_exclusive = datetime.combine(end_date + timedelta(days=1), time.min) # Начало СЛЕДУЮЩЕГО дня (для <)

    # --- Фильтруем выданные заказы по компании, доступным филиалам и дате ---
    issued_orders_query = db.query(Order).filter(
        Order.company_id == company_id,
        Order.location_id.in_(accessible_location_ids), # Фильтр по филиалам
        Order.status == "Выдан",
        Order.issued_at >= start_datetime,
        Order.issued_at < end_datetime_exclusive
    )
    issued_orders = issued_orders_query.all()

    # --- Фильтруем все расходы по компании, доступным филиалам и дате ---
    # Включаем "Общие расходы" Владельца (shift_id is NULL), если он смотрит отчет по всей компании
    all_expenses_query = db.query(Expense).options(joinedload(Expense.expense_type)).filter(
        Expense.company_id == company_id,
        Expense.created_at >= start_datetime,
        Expense.created_at < end_datetime_exclusive
    )
    # Фильтруем по shift.location_id ИЛИ учитываем общие расходы (shift_id is NULL)
    all_expenses_query = all_expenses_query.join(Shift, Expense.shift_id == Shift.id, isouter=True).filter(
         or_(
              Shift.location_id.in_(accessible_location_ids),
              Expense.shift_id == None # Включаем общие расходы Владельца
         )
    )

    all_expenses = all_expenses_query.all()

    # --- Расчеты ---
    total_cash_income = sum(o.paid_cash_som for o in issued_orders if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders if o.paid_card_som)
    total_income = total_cash_income + total_card_income

    total_expenses = sum(e.amount for e in all_expenses)

    expenses_by_type = {}
    for exp in all_expenses:
        type_name = exp.expense_type.name
        if type_name not in expenses_by_type:
            expenses_by_type[type_name] = 0
        expenses_by_type[type_name] += exp.amount

    net_profit = total_income - total_expenses

    # --- Фильтруем смены по компании, доступным филиалам и дате ---
    shifts_in_period_query = db.query(Shift).options(joinedload(Shift.employee), joinedload(Shift.location)).filter(
        Shift.company_id == company_id,
        Shift.location_id.in_(accessible_location_ids), # Фильтр по филиалам
        Shift.start_time >= start_datetime,
        Shift.start_time < end_datetime_exclusive # Используем конец дня end_date
    )
    shifts_in_period = shifts_in_period_query.order_by(Shift.start_time.desc()).all()

    # --- Формируем ответ ---
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "location_id_filter": location_id, # Добавляем информацию о фильтре
        "total_income": total_income,
        "total_cash_income": total_cash_income,
        "total_card_income": total_card_income,
        "total_expenses": total_expenses,
        "expenses_by_type": expenses_by_type,
        "net_profit": net_profit,
        # Добавляем больше деталей о сменах
        "shifts": [
            {
                "id": shift.id,
                "start_time": shift.start_time,
                "end_time": shift.end_time,
                "employee": {
                    "id": shift.employee.id,
                    "full_name": shift.employee.full_name
                } if shift.employee else None,
                 "location": { # Добавляем информацию о филиале смены
                     "id": shift.location.id,
                     "name": shift.location.name
                 } if shift.location else None
            } for shift in shifts_in_period
        ]
    }
    return {"status": "ok", "summary": summary}

# --- 7. УТИЛИТЫ ---

# Этот эндпоинт больше не нужен, т.к. таблицы создаются при запуске
# @app.get("/api/create_tables", tags=["Утилиты"])
# def create_tables_endpoint():
#     try:  
#         Base.metadata.create_all(bind=engine)
#         return {"status": "ok", "message": "Таблицы успешно созданы/обновлены!"}
#     except Exception as e:  
#         raise HTTPException(status_code=500, detail=f"Ошибка: {e}")

# Этот эндпоинт нам пока не нужен
# @app.get("/api/order_statuses", tags=["Утилиты"])
# def get_order_statuses():  
#     return {"status": "ok", "statuses": ORDER_STATUSES}

@app.on_event("startup")
def on_startup():
    """Создает все таблицы при запуске, если их нет."""
    try:
        Base.metadata.create_all(bind=engine)
        print("Таблицы успешно проверены/созданы.")
    except Exception as e:
        print(f"ОШИБКА при создании таблиц: {e}")


@app.get("/", tags=["Утилиты"])
def read_root():  
    return {"status": "ok", "message": "Сервер Карго CRM (Multi-Tenant) запущен!"}
