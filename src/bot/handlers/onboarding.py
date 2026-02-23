from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state

from bot.states.onboarding import OnboardingStates
from services.company_service import CompanyService
from config import config

router = Router()

# Клавиатуры для выбора
INDUSTRY_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💅 Салон красоты/SPA")],
        [KeyboardButton(text="☕ Кафе/ресторан")],
        [KeyboardButton(text="🛍️ Розничная торговля")],
        [KeyboardButton(text="🏢 Офис/Администрирование")],
        [KeyboardButton(text="🏥 Медицина/здоровье")],
        [KeyboardButton(text="📦 Другое")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

SCHEDULE_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="5/2 (пн-пт)")],
        [KeyboardButton(text="2/2 (смены)")],
        [KeyboardButton(text="Гибкий график")],
        [KeyboardButton(text="Удаленная работа")],
        [KeyboardButton(text="Вахтовый метод")]
    ],
    resize_keyboard=True
)

COMMUNICATION_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👔 Строгий/формальный")],
        [KeyboardButton(text="😊 Дружелюбный/неформальный")],
        [KeyboardButton(text="🎯 Нейтральный/профессиональный")]
    ],
    resize_keyboard=True
)

@router.message(Command("start"), StateFilter(default_state))
async def cmd_start(message: Message, state: FSMContext):
    """Начало онбординга"""
    await message.answer(
        "👋 *Добро пожаловать в GWork HR Assistant!*\n\n"
        "Я помогу автоматизировать подбор сотрудников для вашего бизнеса.\n\n"
        "Для начала давайте настроим профиль вашей компании.\n\n"
        "*Как называется ваша компания?*",
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.company_name)

@router.message(OnboardingStates.company_name)
async def process_company_name(message: Message, state: FSMContext):
    """Обработка названия компании"""
    if len(message.text) < 2:
        await message.answer("Пожалуйста, введите корректное название компании:")
        return
    
    await state.update_data(company_name=message.text)
    
    await message.answer(
        f"Отлично, *{message.text}*! 🏢\n\n"
        "*В какой сфере работает ваша компания?*",
        reply_markup=INDUSTRY_KEYBOARD,
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.industry)

@router.message(OnboardingStates.industry)
async def process_industry(message: Message, state: FSMContext):
    """Обработка сферы деятельности"""
    await state.update_data(industry=message.text)
    
    await message.answer(
        f"Сфера: *{message.text}* 📊\n\n"
        "*В каком городе находится компания?*",
        reply_markup=None,  # Убираем клавиатуру
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.city)

@router.message(OnboardingStates.city)
async def process_city(message: Message, state: FSMContext):
    """Обработка города"""
    await state.update_data(city=message.text.title())
    
    await message.answer(
        f"📍 Город: *{message.text}*\n\n"
        "*Какой график работы обычно в компании?*",
        reply_markup=SCHEDULE_KEYBOARD,
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.schedule)

@router.message(OnboardingStates.schedule)
async def process_schedule(message: Message, state: FSMContext):
    """Обработка графика работы"""
    await state.update_data(schedule=message.text)
    
    await message.answer(
        f"График: *{message.text}* 🕐\n\n"
        "*Укажите зарплатную вилку для большинства позиций (пример: 30000-50000):*",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.salary_range)

@router.message(OnboardingStates.salary_range)
async def process_salary_range(message: Message, state: FSMContext):
    """Обработка зарплатной вилки"""
    # Простая валидация
    text = message.text.strip()
    if "-" in text:
        try:
            parts = text.split("-")
            if len(parts) == 2:
                min_salary = int(parts[0].strip())
                max_salary = int(parts[1].strip())
                if min_salary < max_salary:
                    await state.update_data(salary_range=text)
                    
                    await message.answer(
                        f"💰 Зарплата: *{text} руб.*\n\n"
                        "*Выберите стиль общения с кандидатами:*",
                        reply_markup=COMMUNICATION_KEYBOARD,
                        parse_mode="Markdown"
                    )
                    await state.set_state(OnboardingStates.communication_style)
                    return
        except:
            pass
    
    await message.answer("Пожалуйста, укажите в формате: 30000-50000")

@router.message(OnboardingStates.communication_style)
async def process_communication_style(message: Message, state: FSMContext):
    """Обработка стиля общения"""
    style_map = {
        "👔 Строгий/формальный": "strict",
        "😊 Дружелюбный/неформальный": "friendly",
        "🎯 Нейтральный/профессиональный": "neutral"
    }
    
    style = style_map.get(message.text, "neutral")
    await state.update_data(communication_style=style)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Указать ссылку на календарь", callback_data="set_calendar")],
            [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_calendar")]
        ]
    )
    
    await message.answer(
        "📅 *Настройка календаря для собеседований*\n\n"
        "Для автоматического назначения собеседований можно добавить ссылку на календарь "
        "(Google Calendar, Yandex Calendar и др.).\n\n"
        "Хотите добавить сейчас?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "skip_calendar")
async def skip_calendar(callback_query, state: FSMContext):
    """Пропуск настройки календаря"""
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await finish_onboarding(callback_query.message, state)

@router.callback_query(F.data == "set_calendar")
async def set_calendar(callback_query, state: FSMContext):
    """Начало настройки календаря"""
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await callback_query.message.answer(
        "📅 *Добавление календаря*\n\n"
        "Отправьте ссылку на ваш календарь.\n"
        "*Примеры:*\n"
        "• Ссылка на Google Calendar\n"
        "• Ссылка на Yandex Calendar\n"
        "• Или напишите 'ручной выбор' для ручного назначения времени",
        parse_mode="Markdown"
    )
    await state.set_state(OnboardingStates.calendar_link)

@router.message(OnboardingStates.calendar_link)
async def process_calendar_link(message: Message, state: FSMContext):
    """Обработка ссылки на календарь"""
    calendar_link = message.text if message.text.lower() != "ручной выбор" else None
    await state.update_data(calendar_link=calendar_link)
    await finish_onboarding(message, state)

async def finish_onboarding(message: Message, state: FSMContext):
    """Завершение онбординга и сохранение в БД"""
    data = await state.get_data()
    
    # Сохраняем в БД
    company_service = CompanyService()
    try:
        company = await company_service.create_company_profile(
            owner_id=message.from_user.id,
            company_name=data.get("company_name"),
            industry=data.get("industry"),
            city=data.get("city"),
            salary_range=data.get("salary_range"),
            communication_style=data.get("communication_style"),
            calendar_link=data.get("calendar_link")
        )
        
        await message.answer(
            "🎉 *Профиль компании создан!*\n\n"
            f"🏢 *Компания:* {company.company_name}\n"
            f"📊 *Сфера:* {company.industry}\n"
            f"📍 *Город:* {company.city}\n"
            f"💰 *Зарплатная вилка:* {company.salary_range}\n"
            f"💬 *Стиль общения:* {company.communication_style}\n\n"
            "Теперь вы можете:\n"
            "• Создать вакансию: /new_vacancy\n"
            "• Найти кандидатов: /find_candidates\n"
            "• Посмотреть профиль: /profile",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await message.answer(
            f"❌ Ошибка при сохранении: {str(e)}\n"
            "Попробуйте снова: /start"
        )
    
    await state.clear()