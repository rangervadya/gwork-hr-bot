# vk_handlers.py - Полноценный обработчик команд для VK бота
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from config import settings
from db import get_session
from models import Candidate, CandidateStatus, Company, Vacancy
from filters import apply_hard_filters, extract_salary, extract_experience_years, normalize_city
from pre_qualification import PreQualificationAnalyzer

logger = logging.getLogger(__name__)

# Состояния для диалога с пользователем (FSM-like)
user_states = {}


class VKUserState:
    """Состояние пользователя в VK диалоге"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.state = None  # current state
        self.data = {}     # temporary data for onboarding/vacancy creation
        self.step = 0      # current step in multi-step process
        self.vacancy_id = None  # current vacancy for candidates list
        self.page = 0      # pagination for candidates


async def handle_vk_message(data: Dict[str, Any]):
    """
    Главный обработчик сообщений от VK
    
    Args:
        data: Словарь с данными сообщения (user_id, text, payload, message)
        vk_bot: Экземпляр VKBot для отправки сообщений
    """
    user_id = data['user_id']
    text = data['text'].strip()
    payload = data.get('payload')
    
    logger.info(f"📨 VK: {user_id} -> {text[:50]}")
    
    # Получаем или создаём состояние пользователя
    if user_id not in user_states:
        user_states[user_id] = VKUserState(user_id)
    state = user_states[user_id]
    
    # Проверяем, есть ли компания у пользователя
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == user_id).first()
        
        # Если нет компании и пользователь не в онбординге — предлагаем пройти
        if not company and state.state != 'onboarding' and text not in ['/start', '/onboarding', 'start', 'начать']:
            await vk_bot.send_message(
                user_id,
                "👋 Добро пожаловать в GWork HR Bot!\n\n"
                "Для начала работы пройдите онбординг — настройте профиль компании.\n\n"
                "Напишите: /onboarding"
            )
            return
    
    # Обработка команд
    if text == '/start' or text == 'start' or text == 'начать':
        await handle_start(user_id, vk_bot, company)
        return
    
    elif text == '/onboarding' or text == 'онбординг':
        await handle_onboarding_start(user_id, vk_bot, state)
        return
    
    elif text == '/new_job' or text == 'новая вакансия':
        await handle_new_job_start(user_id, vk_bot, state)
        return
    
    elif text == '/candidates' or text == 'кандидаты':
        await handle_candidates(user_id, vk_bot, company, state)
        return
    
    elif text == '/filters' or text == 'фильтры':
        await handle_filters(user_id, vk_bot, company)
        return
    
    elif text == '/analytics' or text == 'аналитика':
        await handle_analytics(user_id, vk_bot, company)
        return
    
    elif text == '/help' or text == 'помощь':
        await handle_help(user_id, vk_bot)
        return
    
    # Обработка состояний диалога
    if state.state == 'onboarding':
        await handle_onboarding_step(user_id, vk_bot, state, text)
    
    elif state.state == 'new_job':
        await handle_new_job_step(user_id, vk_bot, state, text)
    
    else:
        # Неизвестная команда
        await vk_bot.send_message(
            user_id,
            "❓ Неизвестная команда.\n\n"
            "Доступные команды:\n"
            "/start — начать работу\n"
            "/new_job — создать вакансию\n"
            "/candidates — список кандидатов\n"
            "/filters — управление фильтрами\n"
            "/analytics — аналитика\n"
            "/help — справка"
        )


async def handle_start(user_id: int, vk_bot, company):
    """Обработка /start"""
    if company:
        text = (
            f"👋 <b>С возвращением, {company.name_and_industry}!</b>\n\n"
            f"📍 {company.location}\n"
            f"📅 {company.schedule}\n"
            f"💰 {company.salary_range}\n\n"
            f"<b>Доступные команды:</b>\n"
            f"/new_job — создать новую вакансию\n"
            f"/candidates — список кандидатов\n"
            f"/filters — управление фильтрами\n"
            f"/analytics — аналитика по вакансии\n"
            f"/help — справка"
        )
    else:
        text = (
            "👋 <b>Добро пожаловать в GWork HR Bot!</b>\n\n"
            "Я помогаю находить кандидатов и автоматизировать HR-процессы.\n\n"
            "🔍 <b>Что я умею:</b>\n"
            "• Ищу кандидатов в 5+ источниках (HeadHunter, SuperJob, Habr, Trudvsem, Telegram)\n"
            "• Автоматически проверяю резюме на соответствие требованиям\n"
            "• Общаюсь с кандидатами и провожу предквалификацию\n"
            "• Назначаю собеседования и отправляю приглашения\n\n"
            "📋 <b>Как создать вакансию:</b>\n"
            "1. Напишите /new_job\n"
            "2. Укажите роль и город\n"
            "3. Задайте параметры поиска\n\n"
            "После создания вакансии я начну поиск кандидатов и пришлю результаты сюда!\n\n"
            "Для начала работы напишите /onboarding"
        )
    await vk_bot.send_message(user_id, text)


async def handle_onboarding_start(user_id: int, vk_bot, state):
    """Начало онбординга"""
    state.state = 'onboarding'
    state.data = {}
    state.step = 1
    
    await vk_bot.send_message(
        user_id,
        "📝 <b>Расскажите о компании:</b>\n\n"
        "Название и сфера деятельности.\n"
        "Например: Салон красоты \"Лилия\", услуги маникюра и косметологии"
    )


async def handle_onboarding_step(user_id: int, vk_bot, state, text: str):
    """Шаги онбординга"""
    step = state.step
    
    if step == 1:
        state.data['name'] = text
        state.step = 2
        await vk_bot.send_message(
            user_id,
            "📍 <b>Где находитесь?</b>\n\n"
            "Город и район.\n"
            "Например: Казань, центр, рядом с метро"
        )
    
    elif step == 2:
        state.data['location'] = text
        state.step = 3
        await vk_bot.send_message(
            user_id,
            "📅 <b>График работы:</b>\n\n"
            "Например: 2/2 с 10:00 до 22:00"
        )
    
    elif step == 3:
        state.data['schedule'] = text
        state.step = 4
        await vk_bot.send_message(
            user_id,
            "💰 <b>Зарплатная вилка:</b>\n\n"
            "Например: от 40 000 до 60 000 + бонусы"
        )
    
    elif step == 4:
        state.data['salary'] = text
        state.step = 5
        await vk_bot.send_message(
            user_id,
            "🎭 <b>Тон общения с кандидатами:</b>\n\n"
            "Напишите: строгий, дружелюбный или нейтральный"
        )
    
    elif step == 5:
        state.data['tone'] = text
        
        # Сохраняем компанию в БД
        with get_session() as session:
            company = session.query(Company).filter(Company.owner_id == user_id).first()
            if not company:
                company = Company(owner_id=user_id)
                session.add(company)
            
            company.name_and_industry = state.data['name']
            company.location = state.data['location']
            company.schedule = state.data['schedule']
            company.salary_range = state.data['salary']
            company.tone = state.data['tone']
            company.filters_settings = {
                'city': True,
                'salary': True,
                'experience': True,
                'skills': True
            }
            session.commit()
        
        # Сбрасываем состояние
        state.state = None
        state.data = {}
        state.step = 0
        
        await vk_bot.send_message(
            user_id,
            "✅ <b>Профиль компании сохранён!</b>\n\n"
            "Теперь можно создать вакансию командой /new_job\n"
            "И настроить фильтры: /filters"
        )


async def handle_new_job_start(user_id: int, vk_bot, state):
    """Начало создания вакансии"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == user_id).first()
        if not company:
            await vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
            return
    
    state.state = 'new_job'
    state.data = {}
    state.step = 1
    
    await vk_bot.send_message(
        user_id,
        "🔍 <b>Кого ищем?</b>\n\n"
        "Опишите роль:\n"
        "Например: Администратор в салон красоты"
    )


async def handle_new_job_step(user_id: int, vk_bot, state, text: str):
    """Шаги создания вакансии"""
    step = state.step
    
    if step == 1:
        state.data['role'] = text
        state.step = 2
        await vk_bot.send_message(
            user_id,
            "🌆 <b>Город поиска:</b>\n\n"
            "Например: Москва или Санкт-Петербург"
        )
    
    elif step == 2:
        state.data['city'] = text
        state.step = 3
        await vk_bot.send_message(
            user_id,
            "💼 <b>Опыт обязателен?</b>\n\n"
            "Ответьте: да или нет"
        )
    
    elif step == 3:
        state.data['experience_required'] = text.lower().startswith('д')
        state.step = 4
        await vk_bot.send_message(
            user_id,
            "📅 <b>График работы:</b>\n\n"
            "Например: 2/2, 5/2 или гибкий"
        )
    
    elif step == 4:
        state.data['schedule'] = text
        state.step = 5
        await vk_bot.send_message(
            user_id,
            "💰 <b>Зарплата ОТ:</b>\n\n"
            "Введите число или '-' для пропуска"
        )
    
    elif step == 5:
        salary_from = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_from'] = salary_from
        state.step = 6
        await vk_bot.send_message(
            user_id,
            "💰 <b>Зарплата ДО:</b>\n\n"
            "Введите число или '-' для пропуска"
        )
    
    elif step == 6:
        salary_to = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_to'] = salary_to
        state.step = 7
        await vk_bot.send_message(
            user_id,
            "⏰ <b>Когда нужен сотрудник?</b>\n\n"
            "Например: как можно скорее или через месяц"
        )
    
    elif step == 7:
        state.data['start_when'] = text
        state.step = 8
        await vk_bot.send_message(
            user_id,
            "⚠️ <b>Критичные требования (можно пропустить, введя '-'):</b>\n\n"
            "Через запятую. Например: грамотная речь, 1С, продажи"
        )
    
    elif step == 8:
        must_have = '-' if text == '-' else text
        
        # Создаём вакансию
        with get_session() as session:
            company = session.query(Company).filter(Company.owner_id == user_id).first()
            vacancy = Vacancy(
                company_id=company.id,
                role=state.data['role'],
                city=state.data['city'],
                experience_required=state.data['experience_required'],
                schedule=state.data['schedule'],
                salary_from=state.data.get('salary_from'),
                salary_to=state.data.get('salary_to'),
                start_when=state.data['start_when'],
                must_have=must_have,
            )
            session.add(vacancy)
            session.commit()
            vacancy_id = vacancy.id
        
        # Сбрасываем состояние
        state.state = None
        state.data = {}
        state.step = 0
        
        await vk_bot.send_message(
            user_id,
            f"✅ <b>Вакансия создана!</b>\n\n"
            f"Ищу <b>реальных кандидатов</b> в 5 источниках...\n\n"
            f"После поиска используйте /candidates для просмотра"
        )
        
        # Запускаем поиск кандидатов (асинхронно)
        asyncio.create_task(search_and_notify(user_id, vacancy_id, vk_bot))


async def search_and_notify(user_id: int, vacancy_id: int, vk_bot):
    """Поиск кандидатов и уведомление пользователя"""
    from bot import gather_real_candidates
    
    await vk_bot.send_message(user_id, "🔍 Поиск кандидатов... Это может занять несколько минут.")
    
    try:
        await gather_real_candidates(vacancy_id)
        
        with get_session() as session:
            count = session.query(Candidate).filter(Candidate.vacancy_id == vacancy_id).count()
        
        await vk_bot.send_message(
            user_id,
            f"✅ Поиск завершён! Найдено кандидатов: {count}\n\n"
            f"Посмотреть кандидатов: /candidates\n"
            f"Настроить фильтры: /filters"
        )
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await vk_bot.send_message(user_id, f"❌ Ошибка поиска: {e}")


async def handle_candidates(user_id: int, vk_bot, company, state):
    """Показ списка кандидатов"""
    if not company:
        await vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
        if not vacancy:
            await vk_bot.send_message(user_id, "📭 Нет вакансий. Создайте: /new_job")
            return
        
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).order_by(Candidate.score.desc()).all()
        
        if not candidates:
            await vk_bot.send_message(user_id, "📭 Кандидаты не найдены")
            return
        
        # Сохраняем текущую вакансию и сбрасываем страницу
        state.vacancy_id = vacancy.id
        state.page = 0
        
        await send_candidates_page(user_id, vk_bot, vacancy, candidates, 0)


async def send_candidates_page(user_id: int, vk_bot, vacancy, candidates, page: int, per_page: int = 3):
    """Отправка одной страницы кандидатов"""
    total = len(candidates)
    total_pages = (total + per_page - 1) // per_page
    start = page * per_page
    end = min(start + per_page, total)
    
    page_candidates = candidates[start:end]
    
    # Отправляем статистику
    text = (
        f"📊 <b>Вакансия: {vacancy.role} ({vacancy.city})</b>\n\n"
        f"Всего кандидатов: {total}\n"
        f"Страница {page + 1} из {total_pages}\n\n"
    )
    await vk_bot.send_message(user_id, text)
    
    # Отправляем каждого кандидата
    for c in page_candidates:
        if c.status != CandidateStatus.REJECTED.value:
            card = build_simple_candidate_card(c)
            await vk_bot.send_message(user_id, card)
    
    # Кнопки навигации (если есть несколько страниц)
    if total_pages > 1:
        buttons = []
        if page > 0:
            buttons.append({'label': '◀ Назад', 'payload': {'action': 'candidates_page', 'page': page - 1}})
        if page < total_pages - 1:
            buttons.append({'label': 'Вперед ▶', 'payload': {'action': 'candidates_page', 'page': page + 1}})
        
        if buttons:
            keyboard = {
                'inline': True,
                'buttons': [[{'action': {'type': 'text', 'label': b['label'], 'payload': str(b['payload'])}}] for b in buttons]
            }
            await vk_bot.send_message(user_id, "📌 Навигация:", keyboard)


def build_simple_candidate_card(c: Candidate) -> str:
    """Простая карточка кандидата для VK"""
    source_map = {
        "hh": "🇭 HeadHunter",
        "superjob": "🟢 SuperJob",
        "habr": "👨‍💻 Habr Career",
        "trudvsem": "🏢 Работа в России",
        "telegram": "✈️ Telegram"
    }
    source = source_map.get(c.source, c.source)
    
    text = (
        f"👤 <b>{c.name_or_nick}</b>\n"
        f"📍 {c.city}\n"
        f"💼 {c.experience_text[:100]}\n"
        f"📊 Оценка: {c.score}/100\n"
        f"📌 Статус: {c.status}\n"
        f"📎 Источник: {source}\n"
    )
    
    if c.contact:
        text += f"📞 Контакт: {c.contact}\n"
    
    return text


async def handle_filters(user_id: int, vk_bot, company):
    """Управление фильтрами"""
    if not company:
        await vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    filters = getattr(company, 'filters_settings', {})
    
    status_emoji = lambda x: '✅' if x else '❌'
    
    text = (
        f"🔧 <b>Управление фильтрами</b>\n\n"
        f"🏙️ Город: {status_emoji(filters.get('city', True))}\n"
        f"💰 Зарплата: {status_emoji(filters.get('salary', True))}\n"
        f"💼 Опыт: {status_emoji(filters.get('experience', True))}\n"
        f"📋 Требования: {status_emoji(filters.get('skills', True))}\n\n"
        f"Чтобы изменить фильтр, отправьте команду:\n"
        f"/filter_city — включить/выключить город\n"
        f"/filter_salary — включить/выключить зарплату\n"
        f"/filter_experience — включить/выключить опыт\n"
        f"/filter_skills — включить/выключить требования\n\n"
        f"<i>Примечание: полное управление фильтрами доступно в Telegram боте</i>"
    )
    await vk_bot.send_message(user_id, text)


async def handle_analytics(user_id: int, vk_bot, company):
    """Аналитика по вакансии"""
    if not company:
        await vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
        if not vacancy:
            await vk_bot.send_message(user_id, "📭 Нет вакансий. Создайте: /new_job")
            return
        
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
        total = len(candidates)
        
        status_counts = {
            'found': sum(1 for c in candidates if c.status == 'found'),
            'filtered': sum(1 for c in candidates if c.status == 'filtered'),
            'invited': sum(1 for c in candidates if c.status == 'invited'),
            'qualified': sum(1 for c in candidates if c.status == 'qualified'),
            'rejected': sum(1 for c in candidates if c.status == 'rejected'),
        }
        
        text = (
            f"📊 <b>Аналитика по вакансии: {vacancy.role}</b>\n\n"
            f"Всего кандидатов: {total}\n\n"
            f"🔍 Найдено: {status_counts['found']}\n"
            f"🟢 Подходят: {status_counts['filtered']}\n"
            f"📩 Приглашены: {status_counts['invited']}\n"
            f"✅ Квалифицированы: {status_counts['qualified']}\n"
            f"❌ Отсеяно: {status_counts['rejected']}\n"
        )
        await vk_bot.send_message(user_id, text)


async def handle_help(user_id: int, vk_bot):
    """Справка"""
    text = (
        "🤖 <b>GWork HR Bot - Справка</b>\n\n"
        "<b>🔧 НАСТРОЙКА</b>\n"
        "/onboarding — профиль компании\n"
        "/new_job — новая вакансия\n"
        "/filters — управление фильтрами\n\n"
        "<b>👥 КАНДИДАТЫ</b>\n"
        "/candidates — список кандидатов\n"
        "/analytics — аналитика\n\n"
        "<b>🌐 ИСТОЧНИКИ КАНДИДАТОВ</b>\n"
        "🇭 HeadHunter | 🟢 SuperJob\n"
        "👨‍💻 Habr Career | 🏢 Работа в России | ✈️ Telegram\n\n"
        "<b>🔍 ЖЁСТКИЕ ФИЛЬТРЫ</b>\n"
        "• Город должен совпадать\n"
        "• Зарплата в пределах вилки +20%\n"
        "• Опыт (если требуется)\n"
        "• Критичные требования\n"
        "• Красные флаги — отсев подозрительных\n\n"
        "<b>📈 ПРЕДКВАЛИФИКАЦИЯ</b>\n"
        "• Анализ ответов кандидата\n"
        "• Оценка по 4 критериям\n"
        "• Автоматическое решение (вести/уточнить/не вести)\n\n"
        "По всем вопросам обращайтесь к администратору."
    )
    await vk_bot.send_message(user_id, text)
