from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.vacancy_service import VacancyService
from services.company_service import CompanyService
import re

router = Router()

class VacancyCreationStates(StatesGroup):
    waiting_title = State()
    waiting_experience = State()
    waiting_schedule = State()
    waiting_salary = State()
    waiting_requirements = State()
    waiting_need_date = State()

@router.message(Command("new_vacancy"))
async def cmd_new_vacancy(message: Message, state: FSMContext):
    """Начало создания вакансии через быстрый бриф"""
    
    # Проверяем есть ли профиль компании
    company_service = CompanyService()
    company = await company_service.get_company_profile(message.from_user.id)
    
    if not company:
        await message.answer(
            "❌ *Сначала настройте профиль компании!*\n\n"
            "Используйте команду /start для настройки.",
            parse_mode="Markdown"
        )
        return
    
    await message.answer(
        "📝 *Создание новой вакансии*\n\n"
        "Опишите кого ищете одним сообщением, например:\n"
        "• 'Ищу администратора в салон красоты'\n"
        "• 'Нужен бариста в Москву'\n"
        "• 'Требуется менеджер по продажам'\n\n"
        "Или просто напишите название должности:",
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_title)

@router.message(VacancyCreationStates.waiting_title)
async def process_vacancy_title(message: Message, state: FSMContext):
    """Обработка названия вакансии"""
    title = message.text.strip()
    if len(title) < 3:
        await message.answer("Пожалуйста, введите корректное название должности:")
        return
    
    await state.update_data(title=title)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, опыт обязателен")],
            [KeyboardButton(text="❌ Нет, можно без опыта")],
            [KeyboardButton(text="⚠️ Желательно, но не обязательно")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"Должность: *{title}*\n\n"
        "*Требуется ли опыт работы?*",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_experience)

@router.message(VacancyCreationStates.waiting_experience)
async def process_experience(message: Message, state: FSMContext):
    """Обработка требования опыта"""
    text = message.text.lower()
    if "да" in text or "обязателен" in text:
        experience_required = True
    elif "нет" in text or "без опыта" in text:
        experience_required = False
    else:
        experience_required = True  # по умолчанию
    
    await state.update_data(experience_required=experience_required)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="5/2 (пн-пт)")],
            [KeyboardButton(text="2/2 (смены)")],
            [KeyboardButton(text="Гибкий график")],
            [KeyboardButton(text="Удаленная работа")],
            [KeyboardButton(text="Вахтовый метод")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        "*Укажите график работы:*",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_schedule)

@router.message(VacancyCreationStates.waiting_schedule)
async def process_schedule(message: Message, state: FSMContext):
    """Обработка графика работы"""
    await state.update_data(schedule=message.text)
    
    await message.answer(
        f"График: *{message.text}*\n\n"
        "*Укажите зарплатную вилку (пример: 30000-50000):*",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_salary)

@router.message(VacancyCreationStates.waiting_salary)
async def process_salary(message: Message, state: FSMContext):
    """Обработка зарплаты"""
    text = message.text.strip()
    
    # Пытаемся извлечь числа из текста
    numbers = re.findall(r'\d+', text)
    if len(numbers) >= 2:
        salary_min = int(numbers[0])
        salary_max = int(numbers[1])
    elif len(numbers) == 1:
        salary_min = int(numbers[0])
        salary_max = int(numbers[0]) + 10000  # Делаем диапазон
    else:
        # Если не нашли числа, используем дефолтные значения
        salary_min = 30000
        salary_max = 50000
    
    await state.update_data(salary_min=salary_min, salary_max=salary_max)
    
    await message.answer(
        f"💰 Зарплата: *{salary_min}-{salary_max} руб.*\n\n"
        "*Есть ли критичные требования?* (например: грамотная речь, знание 1С, водительские права)\n\n"
        "Перечислите через запятую или напишите 'нет':",
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_requirements)

@router.message(VacancyCreationStates.waiting_requirements)
async def process_requirements(message: Message, state: FSMContext):
    """Обработка требований"""
    text = message.text.strip().lower()
    
    if text == "нет":
        requirements = []
    else:
        # Разбиваем по запятым, точкам, или "и"
        requirements = [req.strip() for req in re.split(r'[,\.и]', text) if req.strip()]
    
    await state.update_data(critical_requirements=requirements)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔥 Срочно (за 1-3 дня)")],
            [KeyboardButton(text="🚀 В течение недели")],
            [KeyboardButton(text="📅 В течение месяца")],
            [KeyboardButton(text="⏳ Не срочно")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        "*Когда нужно вывести человека на работу?*",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(VacancyCreationStates.waiting_need_date)

@router.message(VacancyCreationStates.waiting_need_date)
async def process_need_date(message: Message, state: FSMContext):
    """Обработка сроков и завершение создания вакансии"""
    need_date = message.text
    
    # Получаем все данные
    data = await state.get_data()
    
    # Создаем вакансию
    vacancy_service = VacancyService()
    try:
        vacancy = await vacancy_service.create_vacancy_from_brief(
            owner_id=message.from_user.id,
            brief_data={
                "title": data.get("title"),
                "experience_required": data.get("experience_required", True),
                "schedule": data.get("schedule"),
                "salary_min": data.get("salary_min"),
                "salary_max": data.get("salary_max"),
                "critical_requirements": data.get("critical_requirements", []),
                "need_date": need_date
            }
        )
        
        # Форматируем требования
        requirements_text = ""
        if data.get("critical_requirements"):
            requirements_text = "\n".join([f"• {req}" for req in data.get("critical_requirements")])
        
        await message.answer(
            "✅ *Вакансия создана!*\n\n"
            f"*Должность:* {vacancy.title}\n"
            f"*Опыт:* {'Требуется' if vacancy.experience_required else 'Не требуется'}\n"
            f"*График:* {vacancy.schedule}\n"
            f"*Зарплата:* {vacancy.salary_min}-{vacancy.salary_max} руб.\n"
            f"*Сроки:* {need_date}\n"
            f"*Критичные требования:*\n{requirements_text}\n\n"
            "Теперь я начну поиск кандидатов. Используйте:\n"
            "• /candidates - просмотр кандидатов\n"
            "• /vacancies - список вакансий\n"
            "• /find - начать поиск",
            parse_mode="Markdown",
            reply_markup=None
        )
        
    except Exception as e:
        await message.answer(
            f"❌ Ошибка при создании вакансии: {str(e)}",
            reply_markup=None
        )
    
    await state.clear()