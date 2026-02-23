import asyncio
import logging
import os
import re
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv

# Импортируем наши модули
from database import db
from ai_service import ai
from hh_api import hh
from superjob_api import superjob
from habr_career_api import habr

load_dotenv()

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден в .env")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ===== СОСТОЯНИЯ =====
class OnboardingStates(StatesGroup):
    company_name = State()
    industry = State()
    city = State()
    schedule = State()
    salary = State()
    communication_style = State()

class VacancyStates(StatesGroup):
    waiting_query = State()

class HHSearchStates(StatesGroup):
    waiting_query = State()

class SuperJobSearchStates(StatesGroup):
    waiting_query = State()

class HabrSearchStates(StatesGroup):
    waiting_query = State()

class UniversalSearchStates(StatesGroup):
    waiting_query = State()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def analyze_vacancy_with_ai(vacancy: Dict, company_profile: Dict) -> Dict:
    """Анализирует вакансию с помощью ИИ"""
    try:
        # Формируем данные для анализа
        analysis = await ai.score_candidate(
            candidate_data={
                'title': vacancy.get('title'),
                'description': vacancy.get('description', '')[:500],
                'requirements': vacancy.get('requirements', []),
                'city': vacancy.get('city'),
                'salary': vacancy.get('salary')
            },
            vacancy_data=company_profile
        )
        
        if analysis:
            score = analysis.get('score', 70)
            return {
                'compatibility_score': score,
                'recommendation': analysis.get('verdict', 'Анализ выполнен'),
                'key_points': analysis.get('strengths', []) + analysis.get('weaknesses', []),
                'color': '🔥' if score > 85 else '🟢' if score > 70 else '🟡' if score > 50 else '⚪'
            }
        else:
            return {
                'compatibility_score': 70,
                'recommendation': 'Стандартный анализ',
                'key_points': ['Требуется дополнительный анализ'],
                'color': '🟡'
            }
            
    except Exception as e:
        logger.error(f"Ошибка анализа ИИ: {e}")
        return {
            'compatibility_score': 50,
            'recommendation': 'Ошибка анализа',
            'key_points': ['Не удалось проанализировать'],
            'color': '⚪'
        }

def format_vacancy_with_ai(vacancy: Dict, index: int = None) -> str:
    """Форматирует вакансию с оценкой ИИ"""
    title = vacancy.get('title', 'Без названия')
    salary = vacancy.get('salary', 'Не указана')
    city = vacancy.get('city', 'Не указан')
    company_name = vacancy.get('company', '')
    ai_analysis = vacancy.get('ai_analysis', {})
    score = ai_analysis.get('compatibility_score', 0)
    color = ai_analysis.get('color', '⚪')
    url = vacancy.get('url', '')
    source = vacancy.get('source', 'unknown')
    
    if index:
        prefix = f"{index}. "
    else:
        prefix = ""
    
    # Эмодзи для разных источников
    source_emoji = {
        'hh': '🇭',
        'superjob': '🟢',
        'habr': '🤖'
    }.get(source, '📋')
    
    source_name = {
        'hh': 'HH.ru',
        'superjob': 'SuperJob',
        'habr': 'Habr Career'
    }.get(source, source)
    
    message = f"{prefix}{source_emoji} <b>{title}</b> ({source_name})\n"
    
    if company_name and company_name != 'Не указана':
        message += f"🏢 <b>Компания:</b> {company_name}\n"
    
    if salary and salary != 'Не указана':
        message += f"💰 <b>Зарплата:</b> {salary}\n"
    
    message += f"📍 <b>Город:</b> {city}\n"
    message += f"{color} <b>Оценка ИИ:</b> {score}/100\n"
    
    if url and url != '#':
        message += f"🔗 <a href='{url}'>Перейти к вакансии</a>\n"
    
    return message

def format_candidate_with_ai(candidate: Dict, index: int = None, vacancy_url: str = None) -> str:
    """Форматирует кандидата с оценкой ИИ и ссылкой"""
    name = candidate.get('name', 'Без имени')
    score = candidate.get('ai_score', 0)
    verdict = candidate.get('ai_verdict', 'Нет данных')
    city = candidate.get('city', 'Не указан')
    skills = candidate.get('skills', [])
    source = candidate.get('source', 'unknown')
    
    if index:
        prefix = f"{index}. "
    else:
        prefix = ""
    
    # Определяем цвет в зависимости от оценки
    if score >= 80:
        color = "🔥"
    elif score >= 60:
        color = "🟢"
    else:
        color = "⚪"
    
    # Эмодзи для разных источников
    source_emoji = {
        'hh': '🇭',
        'superjob': '🟢',
        'habr': '🤖'
    }.get(source, '📋')
    
    source_name = {
        'hh': 'HH.ru',
        'superjob': 'SuperJob',
        'habr': 'Habr Career'
    }.get(source, source)
    
    message = f"{prefix}{color} <b>{name}</b> {source_emoji} ({source_name})\n"
    message += f"   ⭐ <b>Оценка ИИ:</b> {score}/100\n"
    message += f"   📍 <b>Город:</b> {city}\n"
    
    if skills:
        skills_str = ", ".join(skills[:3])
        message += f"   🛠️ <b>Навыки:</b> {skills_str}\n"
    
    message += f"   📊 <b>Вердикт:</b> {verdict}\n"
    
    if vacancy_url and vacancy_url != '#' and vacancy_url.startswith('http'):
        message += f"   🔗 <b>Вакансия:</b> <a href='{vacancy_url}'>Ссылка</a>\n"
    
    return message

# ===== ПРОВЕРКА ПРОФИЛЯ =====
async def check_profile(user_id: int) -> bool:
    """Проверяет наличие профиля компании"""
    company = db.get_company(user_id)
    return company is not None

# ===== КОМАНДЫ =====

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало работы - настройка профиля"""
    await state.clear()
    
    user_id = message.from_user.id
    company = db.get_company(user_id)
    
    if company:
        await message.answer(
            f"👋 <b>С возвращением, {company.get('company_name')}!</b>\n\n"
            f"🏢 <b>Компания:</b> {company.get('company_name')}\n"
            f"📍 <b>Город:</b> {company.get('city')}\n"
            f"💰 <b>Зарплата:</b> {company.get('salary')}\n\n"
            f"📋 <b>Доступные команды:</b>\n"
            f"/search - поиск на всех площадках с ИИ анализом\n"
            f"/new_vacancy - создать вакансию и найти кандидатов\n"
            f"/candidates - список найденных кандидатов\n"
            f"/hh_search - поиск только на HH.ru\n"
            f"/superjob_search - поиск только на SuperJob\n"
            f"/habr_search - поиск только на Habr Career (IT)\n"
            f"/profile - профиль компании\n"
            f"/stats - статистика\n"
            f"/help - помощь",
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "👋 <b>Добро пожаловать в GWork HR Assistant с ИИ!</b>\n\n"
        "Я помогу автоматизировать подбор сотрудников с помощью искусственного интеллекта.\n\n"
        "🎯 <b>Что я умею:</b>\n"
        "• 🤖 Анализировать вакансии через ИИ\n"
        "• 📊 Оценивать совместимость кандидатов\n"
        "• 🔍 Искать на HH.ru, SuperJob и Habr Career\n"
        "• 📋 Вести CRM кандидатов\n\n"
        "Для начала давайте настроим профиль вашей компании.\n\n"
        "<b>Как называется ваша компания?</b>",
        parse_mode='HTML'
    )
    await state.set_state(OnboardingStates.company_name)

@dp.message(OnboardingStates.company_name)
async def process_company_name(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    text = message.text.strip()
    if not text or len(text) < 2:
        await message.answer("Пожалуйста, введите корректное название компании (минимум 2 символа):")
        return
    
    await state.update_data(company_name=text)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💅 Салон красоты")],
            [KeyboardButton(text="☕ Кафе/ресторан")],
            [KeyboardButton(text="🛍️ Розничная торговля")],
            [KeyboardButton(text="🏢 Офис")],
            [KeyboardButton(text="📦 Другое")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer(
        f"✅ <b>Отлично, {text}!</b>\n\n"
        "<b>В какой сфере работает ваша компания?</b>\n\n"
        "<i>Выберите вариант на клавиатуре или напишите свой</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )
    await state.set_state(OnboardingStates.industry)

@dp.message(OnboardingStates.industry)
async def process_industry(message: types.Message, state: FSMContext):
    """Обработка сферы деятельности"""
    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, укажите сферу деятельности:")
        return
    
    await state.update_data(industry=text)
    await message.answer(
        f"✅ <b>Сфера: {text}</b>\n\n"
        "<b>В каком городе находится компания?</b>",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(OnboardingStates.city)

@dp.message(OnboardingStates.city)
async def process_city(message: types.Message, state: FSMContext):
    """Обработка города"""
    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, укажите город:")
        return
    
    await state.update_data(city=text)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="5/2 (пн-пт)")],
            [KeyboardButton(text="2/2 (смены)")],
            [KeyboardButton(text="Гибкий график")],
            [KeyboardButton(text="Удаленная работа")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"✅ <b>Город: {text}</b>\n\n"
        "<b>Какой график работы обычно в компании?</b>\n\n"
        "<i>Выберите вариант на клавиатуре или напишите свой</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )
    await state.set_state(OnboardingStates.schedule)

@dp.message(OnboardingStates.schedule)
async def process_schedule(message: types.Message, state: FSMContext):
    """Обработка графика"""
    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, укажите график работы:")
        return
    
    await state.update_data(schedule=text)
    await message.answer(
        f"✅ <b>График: {text}</b>\n\n"
        "<b>Укажите зарплатную вилку (пример: 30000-50000):</b>",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(OnboardingStates.salary)

@dp.message(OnboardingStates.salary)
async def process_salary(message: types.Message, state: FSMContext):
    """Обработка зарплаты"""
    text = message.text.strip()
    
    if not text:
        await message.answer("Пожалуйста, укажите зарплату:")
        return
    
    # Проверяем формат (число или диапазон)
    text_clean = text.replace(' ', '').replace(',', '').replace('-', '-')
    if not re.match(r'^\d+$|^\d+[\-\–\—]\d+$', text_clean):
        await message.answer("❌ Пожалуйста, укажите в формате: 30000 или 30000-50000")
        return
    
    await state.update_data(salary=text)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👔 Строгий")],
            [KeyboardButton(text="😊 Дружелюбный")],
            [KeyboardButton(text="🎯 Нейтральный")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"✅ <b>Зарплата: {text} руб.</b>\n\n"
        "<b>Выберите стиль общения с кандидатами:</b>",
        parse_mode='HTML',
        reply_markup=keyboard
    )
    await state.set_state(OnboardingStates.communication_style)

@dp.message(OnboardingStates.communication_style)
async def process_communication_style(message: types.Message, state: FSMContext):
    """Завершение онбординга"""
    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, выберите стиль общения:")
        return
    
    data = await state.get_data()
    user_id = message.from_user.id
    
    company_data = {
        'company_name': data.get('company_name', ''),
        'industry': data.get('industry', ''),
        'city': data.get('city', ''),
        'schedule': data.get('schedule', ''),
        'salary': data.get('salary', ''),
        'communication_style': text
    }
    
    success = db.save_company(user_id, company_data)
    
    if success:
        await message.answer(
            f"🎉 <b>Профиль компании создан!</b>\n\n"
            f"🏢 <b>Компания:</b> {data.get('company_name')}\n"
            f"📍 <b>Город:</b> {data.get('city')}\n"
            f"💰 <b>Зарплата:</b> {data.get('salary')}\n"
            f"📊 <b>Сфера:</b> {data.get('industry')}\n"
            f"🕐 <b>График:</b> {data.get('schedule')}\n"
            f"💬 <b>Стиль:</b> {text}\n\n"
            f"📋 <b>Доступные команды:</b>\n"
            f"/search - поиск на всех площадках с ИИ анализом\n"
            f"/new_vacancy - создать вакансию и найти кандидатов\n"
            f"/candidates - список найденных кандидатов\n"
            f"/hh_search - поиск только на HH.ru\n"
            f"/superjob_search - поиск только на SuperJob\n"
            f"/habr_search - поиск только на Habr Career (IT)\n"
            f"/profile - профиль компании\n"
            f"/stats - статистика\n"
            f"/help - помощь",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await message.answer(
            "❌ Ошибка при сохранении профиля. Попробуйте снова: /start",
            reply_markup=ReplyKeyboardRemove()
        )
    
    await state.clear()

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Просмотр профиля компании"""
    user_id = message.from_user.id
    company = db.get_company(user_id)
    
    if not company:
        await message.answer("❌ Профиль не настроен. Используйте /start для настройки")
        return
    
    response = (
        "🏢 <b>Профиль компании</b>\n\n"
        f"<b>Название:</b> {company['company_name']}\n"
        f"<b>Сфера:</b> {company['industry']}\n"
        f"<b>Город:</b> {company['city']}\n"
        f"<b>Зарплата:</b> {company['salary']}\n"
        f"<b>График:</b> {company['schedule']}\n"
        f"<b>Стиль общения:</b> {company['communication_style']}\n"
        f"<b>Создан:</b> {company['created_at']}\n\n"
        f"<i>Для изменения профиля используйте /start</i>"
    )
    
    await message.answer(response, parse_mode='HTML')

# ===== ПОИСК ТОЛЬКО НА HH.RU =====
@dp.message(Command("hh_search"))
async def cmd_hh_search(message: types.Message, state: FSMContext):
    """Поиск только на HH.ru"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    await message.answer(
        "🇭 <b>Поиск на HH.ru</b>\n\n"
        "Введите должность для поиска, например:\n"
        "• <code>дизайнер</code>\n"
        "• <code>программист</code>\n"
        "• <code>бухгалтер</code>\n"
        "• <code>администратор</code>",
        parse_mode='HTML'
    )
    await state.set_state(HHSearchStates.waiting_query)

@dp.message(HHSearchStates.waiting_query)
async def process_hh_search(message: types.Message, state: FSMContext):
    """Обработка поиска на HH.ru"""
    query = message.text.strip().lower()
    
    if not query:
        await message.answer("Пожалуйста, введите должность для поиска:")
        return
    
    user_id = message.from_user.id
    company_profile = db.get_company(user_id)
    
    if not company_profile:
        await message.answer("❌ Профиль не найден. Используйте /start")
        await state.clear()
        return
    
    city = company_profile.get('city', 'Москва')
    
    status_msg = await message.answer(
        f"🇭 <b>Ищем на HH.ru:</b> {query}\n"
        f"📍 <b>Город:</b> {city}\n\n"
        f"⏳ Загружаем вакансии...",
        parse_mode='HTML'
    )
    
    try:
        vacancies = await hh.search_vacancies(
            query=query,
            city=city,
            limit=10
        )
        
        await status_msg.delete()
        
        if not vacancies:
            await message.answer(
                f"❌ По запросу <b>«{query}»</b> на HH.ru ничего не найдено.\n\n"
                f"Попробуйте изменить формулировку запроса.",
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Анализируем вакансии
        analyzed_vacancies = []
        for vacancy in vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'hh'
            analyzed_vacancies.append(vacancy)
        
        # Сортируем по оценке ИИ
        analyzed_vacancies.sort(key=lambda x: x.get('ai_analysis', {}).get('compatibility_score', 0), reverse=True)
        
        # Формируем ответ
        response = f"✅ <b>Найдено {len(analyzed_vacancies)} вакансий на HH.ru</b>\n\n"
        response += f"🔍 <b>Запрос:</b> {query}\n"
        response += f"📍 <b>Город:</b> {city}\n\n"
        
        response += "📊 <b>Результаты поиска:</b>\n\n"
        
        for i, vacancy in enumerate(analyzed_vacancies[:7], 1):
            response += format_vacancy_with_ai(vacancy, i) + "\n"
        
        if len(analyzed_vacancies) > 7:
            response += f"... и еще {len(analyzed_vacancies) - 7} вакансий\n\n"
        
        await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка поиска на HH.ru: {e}")
        await message.answer("❌ Ошибка при поиске. Попробуйте позже.")
    finally:
        await state.clear()

# ===== ПОИСК ТОЛЬКО НА SUPERJOB =====
@dp.message(Command("superjob_search"))
async def cmd_superjob_search(message: types.Message, state: FSMContext):
    """Поиск только на SuperJob"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    await message.answer(
        "🟢 <b>Поиск на SuperJob</b>\n\n"
        "Введите должность для поиска, например:\n"
        "• <code>дизайнер</code>\n"
        "• <code>программист</code>\n"
        "• <code>бухгалтер</code>\n"
        "• <code>администратор</code>\n\n"
        "<i>SuperJob - бесплатный источник вакансий с отличным API!</i>",
        parse_mode='HTML'
    )
    await state.set_state(SuperJobSearchStates.waiting_query)

@dp.message(SuperJobSearchStates.waiting_query)
async def process_superjob_search(message: types.Message, state: FSMContext):
    """Обработка поиска на SuperJob"""
    query = message.text.strip().lower()
    
    if not query:
        await message.answer("Пожалуйста, введите должность для поиска:")
        return
    
    user_id = message.from_user.id
    company_profile = db.get_company(user_id)
    
    if not company_profile:
        await message.answer("❌ Профиль не найден. Используйте /start")
        await state.clear()
        return
    
    city = company_profile.get('city', 'Москва')
    
    status_msg = await message.answer(
        f"🟢 <b>Ищем на SuperJob:</b> {query}\n"
        f"📍 <b>Город:</b> {city}\n\n"
        f"⏳ Загружаем вакансии...",
        parse_mode='HTML'
    )
    
    try:
        vacancies = await superjob.search_vacancies(
            keyword=query,
            city=city,
            limit=10
        )
        
        await status_msg.delete()
        
        if not vacancies:
            await message.answer(
                f"❌ По запросу <b>«{query}»</b> на SuperJob ничего не найдено.\n\n"
                f"Попробуйте изменить формулировку запроса.",
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Анализируем вакансии
        analyzed_vacancies = []
        for vacancy in vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'superjob'
            analyzed_vacancies.append(vacancy)
        
        # Сортируем по оценке ИИ
        analyzed_vacancies.sort(key=lambda x: x.get('ai_analysis', {}).get('compatibility_score', 0), reverse=True)
        
        # Формируем ответ
        response = f"✅ <b>Найдено {len(analyzed_vacancies)} вакансий на SuperJob</b>\n\n"
        response += f"🔍 <b>Запрос:</b> {query}\n"
        response += f"📍 <b>Город:</b> {city}\n\n"
        
        response += "📊 <b>Результаты поиска:</b>\n\n"
        
        for i, vacancy in enumerate(analyzed_vacancies[:7], 1):
            response += format_vacancy_with_ai(vacancy, i) + "\n"
        
        if len(analyzed_vacancies) > 7:
            response += f"... и еще {len(analyzed_vacancies) - 7} вакансий\n\n"
        
        await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка поиска на SuperJob: {e}")
        await message.answer("❌ Ошибка при поиске. Попробуйте позже.")
    finally:
        await state.clear()

# ===== ПОИСК ТОЛЬКО НА HABR CAREER =====
@dp.message(Command("habr_search"))
async def cmd_habr_search(message: types.Message, state: FSMContext):
    """Поиск только на Habr Career"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    await message.answer(
        "🤖 <b>Поиск на Habr Career</b>\n\n"
        "Введите IT-специальность для поиска, например:\n"
        "• <code>python разработчик</code>\n"
        "• <code>frontend разработчик</code>\n"
        "• <code>дизайнер</code>\n"
        "• <code>аналитик</code>\n"
        "• <code>тестировщик</code>\n"
        "• <code>devops</code>\n\n"
        "<i>Habr Career - лучший источник IT-специалистов!</i>",
        parse_mode='HTML'
    )
    await state.set_state(HabrSearchStates.waiting_query)

@dp.message(HabrSearchStates.waiting_query)
async def process_habr_search(message: types.Message, state: FSMContext):
    """Обработка поиска на Habr Career"""
    query = message.text.strip().lower()
    
    if not query:
        await message.answer("Пожалуйста, введите специальность для поиска:")
        return
    
    user_id = message.from_user.id
    company_profile = db.get_company(user_id)
    
    if not company_profile:
        await message.answer("❌ Профиль не найден. Используйте /start")
        await state.clear()
        return
    
    city = company_profile.get('city', 'Москва')
    
    status_msg = await message.answer(
        f"🤖 <b>Ищем на Habr Career:</b> {query}\n"
        f"📍 <b>Город:</b> {city}\n\n"
        f"⏳ Загружаем IT-вакансии...",
        parse_mode='HTML'
    )
    
    try:
        vacancies = await habr.search_vacancies(
            keyword=query,
            city=city,
            limit=10
        )
        
        await status_msg.delete()
        
        if not vacancies:
            await message.answer(
                f"❌ По запросу <b>«{query}»</b> на Habr Career ничего не найдено.\n\n"
                f"Попробуйте изменить формулировку запроса или искать на HH.ru/SuperJob.",
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Анализируем вакансии
        analyzed_vacancies = []
        for vacancy in vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'habr'
            analyzed_vacancies.append(vacancy)
        
        # Сортируем по оценке ИИ
        analyzed_vacancies.sort(key=lambda x: x.get('ai_analysis', {}).get('compatibility_score', 0), reverse=True)
        
        # Формируем ответ
        response = f"✅ <b>Найдено {len(analyzed_vacancies)} IT-вакансий на Habr Career</b>\n\n"
        response += f"🔍 <b>Запрос:</b> {query}\n"
        response += f"📍 <b>Город:</b> {city}\n\n"
        
        response += "📊 <b>Результаты поиска:</b>\n\n"
        
        for i, vacancy in enumerate(analyzed_vacancies[:7], 1):
            response += format_vacancy_with_ai(vacancy, i) + "\n"
        
        if len(analyzed_vacancies) > 7:
            response += f"... и еще {len(analyzed_vacancies) - 7} вакансий\n\n"
        
        await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка поиска на Habr Career: {e}")
        await message.answer("❌ Ошибка при поиске. Попробуйте позже.")
    finally:
        await state.clear()

# ===== ПОИСК НА ВСЕХ ПЛОЩАДКАХ =====
@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    """Поиск на всех площадках"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    await message.answer(
        "🔍 <b>Поиск на всех площадках</b>\n\n"
        "Введите должность для поиска, например:\n"
        "• <code>дизайнер</code>\n"
        "• <code>программист</code>\n"
        "• <code>бухгалтер</code>\n"
        "• <code>администратор</code>\n\n"
        "<i>Я найду вакансии на HH.ru, SuperJob и Habr Career</i>",
        parse_mode='HTML'
    )
    await state.set_state(UniversalSearchStates.waiting_query)

@dp.message(UniversalSearchStates.waiting_query)
async def process_universal_search(message: types.Message, state: FSMContext):
    """Обработка поиска на всех площадках"""
    query = message.text.strip().lower()
    
    if not query:
        await message.answer("Пожалуйста, введите должность для поиска:")
        return
    
    user_id = message.from_user.id
    company_profile = db.get_company(user_id)
    
    if not company_profile:
        await message.answer("❌ Профиль не найден. Используйте /start")
        await state.clear()
        return
    
    city = company_profile.get('city', 'Москва')
    
    status_msg = await message.answer(
        f"🔍 <b>Ищем на всех площадках:</b> {query}\n"
        f"📍 <b>Город:</b> {city}\n\n"
        f"⏳ HH.ru...\n"
        f"⏳ SuperJob...\n"
        f"⏳ Habr Career...\n",
        parse_mode='HTML'
    )
    
    try:
        # Поиск на HH.ru
        hh_vacancies = await hh.search_vacancies(
            query=query,
            city=city,
            limit=4
        )
        
        # Поиск на SuperJob
        sj_vacancies = await superjob.search_vacancies(
            keyword=query,
            city=city,
            limit=4
        )
        
        # Поиск на Habr Career
        habr_vacancies = await habr.search_vacancies(
            keyword=query,
            city=city,
            limit=4
        )
        
        await status_msg.delete()
        
        all_vacancies = []
        
        # Анализируем HH.ru вакансии
        for vacancy in hh_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'hh'
            all_vacancies.append(vacancy)
        
        # Анализируем SuperJob вакансии
        for vacancy in sj_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'superjob'
            all_vacancies.append(vacancy)
        
        # Анализируем Habr Career вакансии
        for vacancy in habr_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company_profile)
            vacancy['ai_analysis'] = analysis
            vacancy['source'] = 'habr'
            all_vacancies.append(vacancy)
        
        # Сортируем по оценке ИИ
        all_vacancies.sort(key=lambda x: x.get('ai_analysis', {}).get('compatibility_score', 0), reverse=True)
        
        if not all_vacancies:
            await message.answer(
                f"❌ По запросу <b>«{query}»</b> ничего не найдено.\n\n"
                f"Попробуйте изменить формулировку запроса.",
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Формируем ответ
        response = f"✅ <b>Найдено {len(all_vacancies)} вакансий</b>\n\n"
        response += f"🔍 <b>Запрос:</b> {query}\n"
        response += f"📍 <b>Город:</b> {city}\n\n"
        
        # Статистика по источникам
        hh_count = len([v for v in all_vacancies if v.get('source') == 'hh'])
        sj_count = len([v for v in all_vacancies if v.get('source') == 'superjob'])
        habr_count = len([v for v in all_vacancies if v.get('source') == 'habr'])
        
        response += f"🇭 HH.ru: {hh_count} | 🟢 SuperJob: {sj_count} | 🤖 Habr: {habr_count}\n\n"
        
        response += "📊 <b>Топ-результаты (по версии ИИ):</b>\n\n"
        
        for i, vacancy in enumerate(all_vacancies[:9], 1):
            response += format_vacancy_with_ai(vacancy, i) + "\n"
        
        if len(all_vacancies) > 9:
            response += f"... и еще {len(all_vacancies) - 9} вакансий\n\n"
        
        await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await message.answer("❌ Ошибка при поиске. Попробуйте позже.")
    finally:
        await state.clear()

# ===== СОЗДАНИЕ ВАКАНСИИ =====
@dp.message(Command("new_vacancy"))
async def cmd_new_vacancy(message: types.Message, state: FSMContext):
    """Создание новой вакансии"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    await message.answer(
        "📝 <b>Создание вакансии</b>\n\n"
        "Напишите, кого вы ищете, например:\n"
        "• <code>ищу дизайнера</code>\n"
        "• <code>нужен программист</code>\n"
        "• <code>требуется бухгалтер</code>\n"
        "• <code>вакансия администратора</code>\n\n"
        "<i>Я создам вакансию и найду кандидатов на всех площадках</i>",
        parse_mode='HTML'
    )
    await state.set_state(VacancyStates.waiting_query)

@dp.message(VacancyStates.waiting_query)
async def process_new_vacancy(message: types.Message, state: FSMContext):
    """Обработка создания вакансии"""
    query = message.text.strip()
    
    if not query:
        await message.answer("Пожалуйста, напишите запрос:")
        return
    
    user_id = message.from_user.id
    company = db.get_company(user_id)
    
    if not company:
        await message.answer("❌ Профиль не найден. Используйте /start")
        await state.clear()
        return
    
    # Извлекаем должность из запроса
    position = query.lower()
    position = position.replace('ищу ', '').replace('нужен ', '').replace('требуется ', '').replace('вакансия ', '').replace('ищем ', '').strip()
    
    status_msg = await message.answer(
        f"📝 <b>Создаю вакансию и ищу кандидатов...</b>\n\n"
        f"🔍 Должность: {position}\n"
        f"📍 Город: {company.get('city')}\n\n"
        f"🤖 Поиск на HH.ru, SuperJob и Habr Career...",
        parse_mode='HTML'
    )
    
    try:
        # Сохраняем вакансию
        vacancy_data = {
            'title': position.title(),
            'query': query,
            'schedule': company.get('schedule', '2/2'),
            'salary_min': 30000,
            'salary_max': 50000,
            'requirements': []
        }
        
        vacancy_id = db.save_vacancy(user_id, vacancy_data)
        
        # Поиск на HH.ru
        hh_vacancies = await hh.search_vacancies(
            query=position,
            city=company.get('city', 'Москва'),
            limit=3
        )
        
        # Поиск на SuperJob
        sj_vacancies = await superjob.search_vacancies(
            keyword=position,
            city=company.get('city', 'Москва'),
            limit=3
        )
        
        # Поиск на Habr Career
        habr_vacancies = await habr.search_vacancies(
            keyword=position,
            city=company.get('city', 'Москва'),
            limit=3
        )
        
        await status_msg.delete()
        
        all_candidates = []
        
        # Обрабатываем HH.ru вакансии
        for vacancy in hh_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company)
            vacancy['ai_analysis'] = analysis
            
            candidate_data = {
                'name': f"Кандидат: {vacancy.get('title', 'Без названия')}",
                'source': 'hh',
                'city': vacancy.get('city', company.get('city')),
                'skills': vacancy.get('requirements', [])[:3],
                'ai_score': analysis.get('compatibility_score', 70),
                'ai_verdict': analysis.get('recommendation', 'Рекомендуется к рассмотрению'),
                'status': 'new',
                'external_vacancy_id': vacancy.get('id')
            }
            db.add_candidate(vacancy_id, candidate_data)
            all_candidates.append(vacancy)
        
        # Обрабатываем SuperJob вакансии
        for vacancy in sj_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company)
            vacancy['ai_analysis'] = analysis
            
            candidate_data = {
                'name': f"Кандидат: {vacancy.get('title', 'Без названия')}",
                'source': 'superjob',
                'city': vacancy.get('city', company.get('city')),
                'skills': vacancy.get('requirements', [])[:3],
                'ai_score': analysis.get('compatibility_score', 70),
                'ai_verdict': analysis.get('recommendation', 'Рекомендуется к рассмотрению'),
                'status': 'new',
                'external_vacancy_id': vacancy.get('id')
            }
            db.add_candidate(vacancy_id, candidate_data)
            all_candidates.append(vacancy)
        
        # Обрабатываем Habr Career вакансии
        for vacancy in habr_vacancies:
            analysis = await analyze_vacancy_with_ai(vacancy, company)
            vacancy['ai_analysis'] = analysis
            
            candidate_data = {
                'name': f"Кандидат: {vacancy.get('title', 'Без названия')}",
                'source': 'habr',
                'city': vacancy.get('city', company.get('city')),
                'skills': vacancy.get('requirements', [])[:3],
                'ai_score': analysis.get('compatibility_score', 70),
                'ai_verdict': analysis.get('recommendation', 'Рекомендуется к рассмотрению'),
                'status': 'new',
                'external_vacancy_id': vacancy.get('id')
            }
            db.add_candidate(vacancy_id, candidate_data)
            all_candidates.append(vacancy)
        
        # Сортируем по оценке ИИ
        all_candidates.sort(key=lambda x: x.get('ai_analysis', {}).get('compatibility_score', 0), reverse=True)
        
        if not all_candidates:
            await message.answer(
                f"❌ По запросу <b>«{position}»</b> кандидатов не найдено.\n\n"
                f"Попробуйте изменить запрос или найти кандидатов вручную:\n"
                f"/search {position}",
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Формируем ответ
        hh_count = len([c for c in all_candidates if c.get('source') == 'hh'])
        sj_count = len([c for c in all_candidates if c.get('source') == 'superjob'])
        habr_count = len([c for c in all_candidates if c.get('source') == 'habr'])
        
        response = f"✅ <b>Вакансия создана! Найдено кандидатов: {len(all_candidates)}</b>\n\n"
        response += f"💼 <b>Должность:</b> {position.title()}\n"
        response += f"📍 <b>Город:</b> {company.get('city')}\n\n"
        
        response += f"🇭 HH.ru: {hh_count} | 🟢 SuperJob: {sj_count} | 🤖 Habr: {habr_count}\n\n"
        
        response += "🏆 <b>Топ-кандидаты (по версии ИИ):</b>\n\n"
        
        for i, candidate in enumerate(all_candidates[:5], 1):
            ai = candidate.get('ai_analysis', {})
            score = ai.get('compatibility_score', 0)
            color = ai.get('color', '⚪')
            source_emoji = "🇭" if candidate.get('source') == 'hh' else "🟢" if candidate.get('source') == 'superjob' else "🤖"
            
            response += f"{i}. {color} {source_emoji} <b>{candidate.get('title', 'Без названия')}</b>\n"
            response += f"   🏢 Компания: {candidate.get('company', 'Не указана')}\n"
            response += f"   💰 {candidate.get('salary', 'Не указана')}\n"
            response += f"   ⭐ Оценка: {score}/100\n"
            
            if candidate.get('url') and candidate['url'] != '#':
                response += f"   🔗 <a href='{candidate['url']}'>Ссылка на вакансию</a>\n"
            response += "\n"
        
        response += f"📋 Для просмотра всех кандидатов используйте /candidates"
        
        await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"Ошибка создания вакансии: {e}")
        await message.answer("❌ Ошибка при создании вакансии. Попробуйте позже.")
    finally:
        await state.clear()

# ===== СПИСОК КАНДИДАТОВ =====
@dp.message(Command("candidates"))
async def cmd_candidates(message: types.Message):
    """Список кандидатов"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    candidates = db.get_candidates(owner_id=user_id)
    
    if not candidates:
        await message.answer(
            "👥 <b>Кандидатов пока нет</b>\n\n"
            "Создайте вакансию, чтобы найти кандидатов:\n"
            "• /new_vacancy - создать вакансию",
            parse_mode='HTML'
        )
        return
    
    # Сортируем по оценке ИИ
    candidates.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
    
    total = len(candidates)
    hh_count = len([c for c in candidates if c.get('source') == 'hh'])
    sj_count = len([c for c in candidates if c.get('source') == 'superjob'])
    habr_count = len([c for c in candidates if c.get('source') == 'habr'])
    top = len([c for c in candidates if c.get('ai_score', 0) >= 80])
    good = len([c for c in candidates if 60 <= c.get('ai_score', 0) < 80])
    
    response = f"👥 <b>Все кандидаты ({total})</b>\n"
    response += f"✅ Отлично: {top} | 🟡 Хорошо: {good} | ⚪ Другие: {total - top - good}\n"
    response += f"🇭 HH.ru: {hh_count} | 🟢 SuperJob: {sj_count} | 🤖 Habr: {habr_count}\n\n"
    
    for i, candidate in enumerate(candidates[:10], 1):
        response += format_candidate_with_ai(candidate, i) + "\n"
    
    if len(candidates) > 10:
        response += f"\n... и еще {len(candidates) - 10} кандидатов\n"
    
    await message.answer(response, parse_mode='HTML', disable_web_page_preview=True)

# ===== СТАТИСТИКА =====
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Статистика"""
    user_id = message.from_user.id
    
    if not await check_profile(user_id):
        await message.answer("❌ Сначала настройте профиль: /start")
        return
    
    vacancies = db.get_vacancies(user_id)
    candidates = db.get_candidates(owner_id=user_id)
    
    total_candidates = len(candidates)
    hh_candidates = len([c for c in candidates if c.get('source') == 'hh'])
    sj_candidates = len([c for c in candidates if c.get('source') == 'superjob'])
    habr_candidates = len([c for c in candidates if c.get('source') == 'habr'])
    
    avg_score = sum(c.get('ai_score', 0) for c in candidates) / max(total_candidates, 1) if total_candidates > 0 else 0
    
    response = "📊 <b>СТАТИСТИКА</b>\n\n"
    response += f"📋 <b>Вакансий:</b> {len(vacancies)}\n"
    response += f"👥 <b>Кандидатов:</b> {total_candidates}\n"
    response += f"🇭 <b>С HH.ru:</b> {hh_candidates}\n"
    response += f"🟢 <b>С SuperJob:</b> {sj_candidates}\n"
    response += f"🤖 <b>С Habr Career:</b> {habr_candidates}\n"
    response += f"⭐ <b>Средняя оценка ИИ:</b> {avg_score:.1f}/100\n"
    
    await message.answer(response, parse_mode='HTML')

# ===== ПОМОЩЬ =====
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка"""
    help_text = """
📋 <b>Команды GWork:</b>

⚙️ <b>Настройка:</b>
/start - настройка профиля компании
/profile - просмотр профиля
/stats - статистика

🔍 <b>Поиск кандидатов:</b>
/search [должность] - поиск на HH.ru, SuperJob и Habr
/hh_search [должность] - поиск только на HH.ru
/superjob_search [должность] - поиск только на SuperJob
/habr_search [должность] - поиск только на Habr Career (IT)

📝 <b>Управление вакансиями:</b>
/new_vacancy - создать вакансию и найти кандидатов
/candidates - список найденных кандидатов

❓ <b>Другое:</b>
/help - эта справка

💡 <b>Примеры для IT-поиска:</b>
• /habr_search python разработчик
• /habr_search frontend
• /habr_search дизайнер
• /habr_search аналитик данных

💡 <b>Общие примеры:</b>
• /search дизайнер
• /superjob_search программист
• /new_vacancy ищу бухгалтера

    """
    
    await message.answer(help_text, parse_mode='HTML')

# ===== ОБРАБОТКА НЕИЗВЕСТНЫХ КОМАНД =====
@dp.message()
async def handle_unknown(message: types.Message):
    """Обработка неизвестных команд"""
    text = message.text.strip()
    
    if any(word in text.lower() for word in ['ищу', 'нужен', 'требуется', 'ищем', 'найди']):
        user_id = message.from_user.id
        if await check_profile(user_id):
            profession = text.lower().replace('ищу', '').replace('нужен', '').replace('требуется', '').replace('ищем', '').replace('найди', '').strip()
            await message.answer(
                f"🔍 Похоже, вы ищете <b>{profession}</b>!\n\n"
                f"Используйте команды:\n"
                f"• /search {profession} - поиск на всех площадках\n"
                f"• /superjob_search {profession} - поиск на SuperJob\n"
                f"• /habr_search {profession} - поиск на Habr Career (IT)\n"
                f"• /hh_search {profession} - поиск на HH.ru\n"
                f"• /new_vacancy - создать вакансию",
                parse_mode='HTML'
            )
        else:
            await message.answer("Сначала настройте профиль: /start")
    else:
        await message.answer(
            "❓ Неизвестная команда.\n"
            "Используйте /help для списка команд"
        )

# ===== ЗАПУСК БОТА =====
async def main():
    """Запуск бота"""
    logger.info("🚀 Запуск GWork HR Bot с HH.ru, SuperJob и Habr Career...")
    
    # Проверяем статус AI
    if ai.enabled:
        logger.info("✅ DeepSeek AI подключен")
    else:
        logger.warning("⚠️ DeepSeek AI отключен (работаем в демо-режиме)")
    
    # Инициализируем базу данных
    try:
        db.get_company(0)
        logger.info("✅ База данных подключена")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
    
    logger.info("✅ Бот готов к работе!")
    logger.info("📍 Площадки: HH.ru 🇭, SuperJob 🟢, Habr Career 🤖")
    
    try:
        await dp.start_polling(bot)
    finally:
        await hh.close_session()
        await superjob.close_session()
        await habr.close_session()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())