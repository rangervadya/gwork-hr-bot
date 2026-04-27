from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    Float,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from db import Base


class Tone(str, Enum):
    STRICT = "strict"
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"


class CandidateStatus(str, Enum):
    FOUND = "found"
    FILTERED = "filtered"
    INVITED = "invited"
    ANSWERING = "answering"
    INTERVIEW = "interview"
    NO_SHOW = "no_show"
    OFFER = "offer"
    REJECTED = "rejected"
    ARCHIVE = "archive"
    FAVORITE = "favorite"
    CLARIFY = "clarify"  # Нужно уточнить
    QUALIFIED = "qualified"  # Прошёл предквалификацию


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name_and_industry: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    schedule: Mapped[str] = mapped_column(String(255))
    salary_range: Mapped[str] = mapped_column(String(255))
    tone: Mapped[str] = mapped_column(String(32), default=Tone.NEUTRAL.value)
    interview_how: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    
    # Google Calendar настройки (опционально)
    calendar_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Настройки фильтров (JSON)
    filters_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True, default={
        'city': True,
        'salary': True,
        'experience': True,
        'skills': True
    })
    """Настройки фильтров для компании"""
    
    # Настройки email для отчётов
    report_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Email для отправки отчётов"""
    
    report_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True, default="never")
    """Частота отправки отчётов: daily, weekly, monthly, never"""

    vacancies: Mapped[list["Vacancy"]] = relationship(
        "Vacancy", back_populates="company", cascade="all, delete-orphan"
    )


class Vacancy(Base):
    __tablename__ = "vacancies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    role: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(255))
    experience_required: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[str] = mapped_column(String(255))
    salary_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_when: Mapped[str] = mapped_column(String(255))
    must_have: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    
    # Дата последнего поиска кандидатов
    last_search_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Дата последнего поиска кандидатов"""

    company: Mapped[Company] = relationship("Company", back_populates="vacancies")
    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate", back_populates="vacancy", cascade="all, delete-orphan"
    )
    interview_slots: Mapped[list["InterviewSlot"]] = relationship(
        "InterviewSlot", back_populates="vacancy", cascade="all, delete-orphan"
    )


class VacancyTemplate(Base):
    """
    Шаблон вакансии для компании: текстовое описание + основные поля.
    Используется, чтобы быстро запускать похожие подборы.
    """

    __tablename__ = "vacancy_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(255))
    schedule: Mapped[str] = mapped_column(String(255))
    salary_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    must_have: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    company: Mapped[Company] = relationship("Company", backref="vacancy_templates")


class InterviewSlot(Base):
    """Окно для собеседования по вакансии (MVP: текст, например «завтра 12:00–14:00»)."""
    __tablename__ = "interview_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    slot_text: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    vacancy: Mapped[Vacancy] = relationship("Vacancy", back_populates="interview_slots")

class PaymentStatus(str, enum.Enum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    vacancy_id = Column(Integer, ForeignKey("vacancies.id"))
    amount = Column(Integer)
    currency = Column(String, default="XTR")
    tariff_key = Column(String)
    candidates_limit = Column(Integer)
    candidates_used = Column(Integer, default=0)
    status = Column(String, default=PaymentStatus.CREATED.value)
    telegram_payload = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    name_or_nick: Mapped[str] = mapped_column(String(255))
    contact: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(255))
    experience_text: Mapped[str] = mapped_column(Text)
    skills_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
    source_link: Mapped[str] = mapped_column(String(512))
    raw_text: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(32), default=CandidateStatus.FOUND.value, index=True
    )
    interview_slot_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    
    # ===== ПОЛЯ ДЛЯ АВТОМАТИЧЕСКОГО ДИАЛОГА =====
    dialog_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_sent: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # ===== ПОЛЯ ДЛЯ ХРАНЕНИЯ СЛОТОВ И КАЛЕНДАРЯ =====
    available_slots: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    """Список доступных слотов для выбора (JSON)"""
    
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """ID события в календаре"""
    
    calendar_event_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """Ссылка на событие в календаре"""
    
    calendar_reminders_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    """Были ли отправлены напоминания о событии"""

    # ===== ПОЛЯ ДЛЯ ЖЁСТКИХ ФИЛЬТРОВ =====
    salary_expectations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Ожидаемая зарплата кандидата (если удалось извлечь из текста)"""
    
    experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Опыт работы в годах (например: 0, 1.5, 3, 5)"""
    
    normalized_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """Нормализованное название города (Москва, СПб и т.д.)"""
    
    critical_skills_match: Mapped[int] = mapped_column(Integer, default=0)
    """Количество совпавших критичных требований из вакансии"""
    
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Причина отсева фильтрами (если не прошёл)"""
    
    # ===== ПОЛЯ ДЛЯ НОРМАЛИЗАЦИИ ДАННЫХ =====
    normalized_experience_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """Уровень опыта: '0-1', '1-3', '3-5', '5+' """
    
    extracted_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Нормализованный список навыков (например: ['Python', 'SQL', 'Git'])"""
    
    extracted_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Извлечённые ключевые слова из текста для анализа"""
    
    red_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Найденные красные флаги в тексте (список строк)"""
    
    # ===== ПОЛЯ ДЛЯ АНАЛИТИКИ =====
    keyword_match_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Процент совпадения ключевых слов с вакансией (0-100)"""
    
    normalized_city_from_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """Город, извлечённый из текста резюме (может отличаться от указанного)"""
    
    parsed_experience_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    """Текстовое представление опыта после парсинга (например: '3 года 2 месяца')"""

    # ===== ПОЛЯ ДЛЯ ПРЕДКВАЛИФИКАЦИИ =====
    answers_schedule: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Ответ кандидата на вопрос о графике работы"""
    
    answers_salary: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Ответ кандидата на вопрос о зарплатных ожиданиях"""
    
    answers_timing: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Ответ кандидата на вопрос о сроках выхода на работу"""
    
    answers_clarify: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Ответ кандидата на уточняющие вопросы"""
    
    qualification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Общий балл предквалификации (0-100)"""
    
    qualification_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    """Детали предквалификации (анализ по категориям: график, зарплата, сроки, тон)"""
    
    qualification_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Дата проведения предквалификации"""
    
    # ===== ПОЛЯ ДЛЯ УЛУЧШЕННОЙ ПРЕДКВАЛИФИКАЦИИ =====
    qualification_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """История ответов кандидата для улучшенной предквалификации"""
    
    extracted_keywords_from_answers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Ключевые слова, извлечённые из ответов кандидата"""
    
    # ===== ПОЛЯ ДЛЯ ДАТЫ ПУБЛИКАЦИИ И СТАТИСТИКИ =====
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Когда кандидат был впервые найден"""
    
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Последняя активность кандидата (ответ, действие)"""
    
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)
    """Флаг нового кандидата (для отметки 🆕)"""
    
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Когда кандидат был просмотрен администратором"""
    
    # ===== НОВЫЕ ПОЛЯ ДЛЯ КАЛЕНДАРЯ =====
    calendar_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Когда было создано событие в календаре"""
    
    calendar_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """Когда событие в календаре было обновлено"""
    
    calendar_attendees: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Список участников события (email'ы)"""

    vacancy: Mapped[Vacancy] = relationship("Vacancy", back_populates="candidates")
