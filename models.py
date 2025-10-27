# models.py (ПОЛНАЯ НОВАЯ ВЕРСИЯ ДЛЯ MULTI-TENANT)

from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, func, Date, Boolean, Table, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# --- СВЯЗУЮЩАЯ ТАБЛИЦА ДЛЯ СИСТЕМЫ ДОСТУПОВ ---
# Остается без изменений, так как Role будет привязана к Company
role_permissions_table = Table('role_permissions', Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)

# --- НОВЫЕ ОСНОВНЫЕ МОДЕЛИ: КОМПАНИЯ И ФИЛИАЛ ---

class Company(Base):
    """
    Представляет "Арендатора" (Tenant) - отдельную карго-компанию.
    Например: "Карго Экспрес Кара Балта", "ВИШКАРГО".
    Это "владелец" всех остальных данных.
    """
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # Название компании
    # Уникальный код для логина сотрудников (чтобы они не вводили полное имя)
    company_code = Column(String, unique=True, index=True, nullable=True) 
    is_active = Column(Boolean, default=True) # Контроль оплаты
    subscription_paid_until = Column(Date, nullable=True) # Контроль оплаты
    contact_person = Column(String, nullable=True) # Контактное лицо (для вас)
    contact_phone = Column(String, nullable=True) # Телефон (для вас)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Связи (кто принадлежит этой компании)
    locations = relationship("Location", back_populates="company")
    clients = relationship("Client", back_populates="company")
    orders = relationship("Order", back_populates="company")
    employees = relationship("Employee", back_populates="company")
    roles = relationship("Role", back_populates="company")
    shifts = relationship("Shift", back_populates="company")
    expenses = relationship("Expense", back_populates="company")
    expense_types = relationship("ExpenseType", back_populates="company")
    settings = relationship("Setting", back_populates="company")

class Location(Base):
    """
    Представляет "Точку" или "Филиал" (Отделение)
    Например: "Филиал в Бишкеке", "Филиал в Оше".
    Принадлежит одной Компании.
    """
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False) # "Главный офис", "Склад 1"
    address = Column(String, nullable=True)
    
    # Связь с Компанией (Владельцем)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="locations")
    
    # Сотрудники и смены на этом филиале
    employees = relationship("Employee", back_populates="location")
    shifts = relationship("Shift", back_populates="location")

# --- ИЗМЕНЕННЫЕ МОДЕЛИ: КЛИЕНТЫ И ЗАКАЗЫ ---

class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, index=True, nullable=False) # Убираем global unique
    client_code_prefix = Column(String, default="KB")
    client_code_num = Column(Integer, nullable=True) # Убираем global unique
    telegram_chat_id = Column(String, unique=True, nullable=True) # Оставляем unique, т.к. 1 телеграм = 1 человек
    status = Column(String, default="Розница")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    orders = relationship("Order", back_populates="client")

    # НОВАЯ СВЯЗЬ: К какой компании принадлежит этот клиент
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="clients")

    # НОВОЕ ПРАВИЛО: Код клиента и телефон должны быть уникальны ВНУТРИ ОДНОЙ КОМПАНИИ
    __table_args__ = (
        UniqueConstraint('phone', 'company_id', name='_phone_company_uc'),
        UniqueConstraint('client_code_num', 'company_id', name='_client_code_company_uc'),
    )


class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True, index=True)
    track_code = Column(String, index=True, nullable=False) # Трек-код может дублироваться у разных компаний
    status = Column(String, default="В обработке")
    purchase_type = Column(String, nullable=False)
    comment = Column(String, nullable=True)
    party_date = Column(Date, server_default=func.current_date(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    weight_kg = Column(Float, nullable=True)
    price_per_kg_usd = Column(Float, nullable=True)
    exchange_rate_usd = Column(Float, nullable=True)
    final_cost_som = Column(Float, nullable=True)
    paid_cash_som = Column(Float, nullable=True)
    paid_card_som = Column(Float, nullable=True)
    card_payment_type = Column(String, nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    reverted_at = Column(DateTime(timezone=True), nullable=True)
    calculated_weight_kg = Column(Float, nullable=True)
    calculated_price_per_kg_usd = Column(Float, nullable=True)
    calculated_exchange_rate_usd = Column(Float, nullable=True)
    calculated_final_cost_som = Column(Float, nullable=True)
    
    # Поля для выкупа
    buyout_item_cost_cny = Column(Float, nullable=True)
    buyout_commission_percent = Column(Float, default=10.0)
    buyout_rate_for_client = Column(Float, nullable=True)
    buyout_actual_rate = Column(Float, nullable=True)
    
    # Связь с клиентом (остается)
    client_id = Column(Integer, ForeignKey('clients.id'))
    client = relationship("Client", back_populates="orders")
    
    # Связь со сменой (остается)
    shift_id = Column(Integer, ForeignKey('shifts.id'), nullable=True)

    # НОВАЯ СВЯЗЬ: К какой компании принадлежит этот заказ (для быстрого поиска)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="orders")


# --- ИЗМЕНЕННЫЕ МОДЕЛИ: ПЕРСОНАЛ И ДОСТУП ---

class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False) # Убираем global unique
    
    employees = relationship("Employee", back_populates="role")
    permissions = relationship("Permission", secondary=role_permissions_table, back_populates="roles")
    
    # НОВАЯ СВЯЗЬ: Роль принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="roles")
    
    # НОВОЕ ПРАВИЛО: Название роли уникально внутри компании
    __table_args__ = (UniqueConstraint('name', 'company_id', name='_role_name_company_uc'),)


class Permission(Base):
    """
    Разрешения - ГЛОБАЛЬНЫЕ. 
    Мы (Супер-Админ) определяем, какие доступы ВООБЩЕ СУЩЕСТВУЮТ в системе.
    А владелец компании уже "навешивает" их на свои роли.
    """
    __tablename__ = 'permissions'
    id = Column(Integer, primary_key=True)
    codename = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)
    
    roles = relationship("Role", secondary=role_permissions_table, back_populates="permissions")


class Employee(Base):
    __tablename__ = 'employees'
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    password = Column(String, nullable=False) # Пароль для входа
    is_active = Column(Boolean, default=True)
    
    # ЯВЛЯЕТСЯ ЛИ СОТРУДНИК ВЛАДЕЛЬЦЕМ КОМПАНИИ (для управления филиалами и сотрудниками)
    is_company_owner = Column(Boolean, default=False) 
    
    role_id = Column(Integer, ForeignKey('roles.id'))
    role = relationship("Role", back_populates="employees")
    shifts = relationship("Shift", back_populates="employee")
    
    # НОВАЯ СВЯЗЬ: Сотрудник принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="employees")
    
    # НОВАЯ СВЯЗЬ: Сотрудник привязан к филиалу
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=False, index=True)
    location = relationship("Location", back_populates="employees")

# --- ИЗМЕНЕННЫЕ МОДЕЛИ: ФИНАНСЫ ---

class Shift(Base):
    __tablename__ = 'shifts'
    id = Column(Integer, primary_key=True)
    start_time = Column(DateTime(timezone=True), server_default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)
    starting_cash = Column(Float, nullable=False)
    closing_cash = Column(Float, nullable=True)
    exchange_rate_usd = Column(Float, nullable=False)
    price_per_kg_usd = Column(Float, nullable=False)
    
    employee_id = Column(Integer, ForeignKey('employees.id'))
    employee = relationship("Employee", back_populates="shifts")
    expenses = relationship("Expense", back_populates="shift")
    
    # НОВАЯ СВЯЗЬ: Смена принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="shifts")
    
    # НОВАЯ СВЯЗЬ: Смена открыта на филиале
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=False, index=True)
    location = relationship("Location", back_populates="shifts")


class ExpenseType(Base):
    __tablename__ = 'expense_types'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False) # Убираем global unique
    expenses = relationship("Expense", back_populates="expense_type")
    
    # НОВАЯ СВЯЗЬ: Тип расхода принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="expense_types")
    
    # НОВОЕ ПРАВИЛО: Название типа уникально внутри компании
    __table_args__ = (UniqueConstraint('name', 'company_id', name='_exp_type_name_company_uc'),)


class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    shift_id = Column(Integer, ForeignKey('shifts.id'))
    shift = relationship("Shift", back_populates="expenses")
    
    expense_type_id = Column(Integer, ForeignKey('expense_types.id'))
    expense_type = relationship("ExpenseType", back_populates="expenses")
    
    # НОВАЯ СВЯЗЬ: Расход принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="expenses")


# --- ИЗМЕНЕННАЯ МОДЕЛЬ: НАСТРОЙКИ ---

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False) # Убираем global unique
    value = Column(String, nullable=True)
    
    # НОВАЯ СВЯЗЬ: Настройка принадлежит компании
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="settings")
    
    # НОВОЕ ПРАВИЛО: Ключ настройки уникален внутри компании
    __table_args__ = (UniqueConstraint('key', 'company_id', name='_setting_key_company_uc'),)