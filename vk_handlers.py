# vk_handlers.py - Полноценный обработчик команд для VK бота
import asyncio
import logging
import sys
import importlib
from datetime import datetime
from typing import Dict, Any

from config import settings
from db import get_session
from models import Candidate, CandidateStatus, Company, Vacancy
import vk_bot as vk_module

logger = logging.getLogger(__name__)

# Состояния для диалога с пользователем
user_states = {}


class VKUserState:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.state = None
        self.data = {}
        self.step = 0
        self.vacancy_id = None
        self.page = 0


async def handle_vk_message(data: Dict[str, Any]):
    """Главный обработчик сообщений от VK"""
    user_id = data['user_id']
    text = data['text'].strip()
    
    logger.info(f"📨 VK: {user_id} -> {text[:50]}")
    
    vk_bot = vk_module.vk_bot
    if vk_bot is None:
        logger.error("❌ VK бот не инициализирован")
        return
    
    if user_id not in user_states:
        user_states[user_id] = VKUserState(user_id)
    state = user_states[user_id]
    
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == user_id).first()
    
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
    
    elif text == '/help' or text == 'help' or text == 'помощь':
        await handle_help(user_id, vk_bot)
        return
    
    # Состояния диалога
    if state.state == 'onboarding':
        await handle_onboarding_step(user_id, vk_bot, state, text)
    elif state.state == 'new_job':
        await handle_new_job_step(user_id, vk_bot, state, text)
    else:
        vk_bot.send_message(
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
            f"/analytics — аналитика\n"
            f"/help — справка"
        )
    else:
        text = (
            "👋 <b>Добро пожаловать в GWork HR Bot!</b>\n\n"
            "Я помогаю находить кандидатов и автоматизировать HR-процессы.\n\n"
            "🔍 <b>Что я умею:</b>\n"
            "• Ищу кандидатов в 5+ источниках\n"
            "• Автоматически проверяю резюме\n"
            "• Общаюсь с кандидатами и провожу предквалификацию\n"
            "• Назначаю собеседования\n\n"
            "📋 <b>Как создать вакансию:</b>\n"
            "1. Напишите /new_job\n"
            "2. Укажите роль и город\n"
            "3. Задайте параметры поиска\n\n"
            "Для начала работы напишите /onboarding"
        )
    vk_bot.send_message(user_id, text)


async def handle_onboarding_start(user_id: int, vk_bot, state):
    """Начало онбординга"""
    state.state = 'onboarding'
    state.data = {}
    state.step = 1
    
    vk_bot.send_message(
        user_id,
        "📝 <b>Расскажите о компании:</b>\n\n"
        "Название и сфера деятельности.\n"
        "Например: Салон красоты \"Лилия\", услуги маникюра"
    )


async def handle_onboarding_step(user_id: int, vk_bot, state, text: str):
    """Шаги онбординга"""
    step = state.step
    
    if step == 1:
        state.data['name'] = text
        state.step = 2
        vk_bot.send_message(user_id, "📍 <b>Где находитесь?</b>\n\nГород и район.")
    
    elif step == 2:
        state.data['location'] = text
        state.step = 3
        vk_bot.send_message(user_id, "📅 <b>График работы:</b>\n\nНапример: 2/2 с 10:00 до 22:00")
    
    elif step == 3:
        state.data['schedule'] = text
        state.step = 4
        vk_bot.send_message(user_id, "💰 <b>Зарплатная вилка:</b>\n\nНапример: от 40 000 до 60 000")
    
    elif step == 4:
        state.data['salary'] = text
        state.step = 5
        vk_bot.send_message(user_id, "🎭 <b>Тон общения:</b>\n\nстрогий, дружелюбный или нейтральный")
    
    elif step == 5:
        state.data['tone'] = text
        
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
            company.filters_settings = {'city': True, 'salary': True, 'experience': True, 'skills': True}
            session.commit()
        
        state.state = None
        state.data = {}
        state.step = 0
        
        vk_bot.send_message(
            user_id,
            "✅ <b>Профиль компании сохранён!</b>\n\n"
            "Теперь создайте вакансию: /new_job"
        )


async def handle_new_job_start(user_id: int, vk_bot, state):
    """Начало создания вакансии"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == user_id).first()
        if not company:
            vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
            return
    
    state.state = 'new_job'
    state.data = {}
    state.step = 1
    
    vk_bot.send_message(
        user_id,
        "🔍 <b>Кого ищем?</b>\n\nОпишите роль:\nНапример: Администратор"
    )


async def handle_new_job_step(user_id: int, vk_bot, state, text: str):
    """Шаги создания вакансии"""
    step = state.step
    
    if step == 1:
        state.data['role'] = text
        state.step = 2
        vk_bot.send_message(user_id, "🌆 <b>Город поиска:</b>\n\nНапример: Москва")
    
    elif step == 2:
        state.data['city'] = text
        state.step = 3
        vk_bot.send_message(user_id, "📅 <b>График работы:</b>\n\nНапример: 5/2")
    
    elif step == 3:
        state.data['schedule'] = text
        state.step = 4
        vk_bot.send_message(user_id, "💰 <b>Зарплата ОТ (число или '-'):</b>")
    
    elif step == 4:
        salary_from = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_from'] = salary_from
        state.step = 5
        vk_bot.send_message(user_id, "💰 <b>Зарплата ДО (число или '-'):</b>")
    
    elif step == 5:
        salary_to = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_to'] = salary_to
        state.step = 6
        vk_bot.send_message(user_id, "⏰ <b>Когда нужен сотрудник?</b>")
    
    elif step == 6:
        state.data['start_when'] = text
        state.step = 7
        vk_bot.send_message(user_id, "⚠️ <b>Критичные требования (или '-'):</b>")
    
    elif step == 7:
        must_have = '-' if text == '-' else text
        
        with get_session() as session:
            company = session.query(Company).filter(Company.owner_id == user_id).first()
            vacancy = Vacancy(
                company_id=company.id,
                role=state.data['role'],
                city=state.data['city'],
                experience_required=False,
                schedule=state.data['schedule'],
                salary_from=state.data.get('salary_from'),
                salary_to=state.data.get('salary_to'),
                start_when=state.data['start_when'],
                must_have=must_have,
            )
            session.add(vacancy)
            session.flush()
            vacancy_id = vacancy.id
            session.commit()
            
            logger.info(f"✅ Вакансия создана: ID={vacancy_id}, роль={vacancy.role}, город={vacancy.city}")
        
        state.state = None
        state.data = {}
        state.step = 0
        
        vk_bot.send_message(
            user_id,
            f"✅ <b>Вакансия создана!</b>\n\n"
            f"🔍 Начинаю поиск кандидатов в HeadHunter, SuperJob и других источниках...\n"
            f"Это может занять 1-3 минуты.\n\n"
            f"После завершения поиска используйте /candidates"
        )
        
        # ЗАПУСКАЕМ ПОИСК
        logger.info(f"🔍 ЗАПУСК ПОИСКА для вакансии {vacancy_id}")
        
        # Небольшая задержка перед запуском
        await asyncio.sleep(0.5)
        
        # Создаём задачу
        asyncio.create_task(search_and_notify(user_id, vacancy_id, vk_bot))


async def search_and_notify(user_id: int, vacancy_id: int, vk_bot):
    """Поиск реальных кандидатов и уведомление"""
    logger.info(f"🔍🔍🔍 search_and_notify: НАЧАЛО ВЫПОЛНЕНИЯ для вакансии {vacancy_id}")
    
    try:
        # Проверяем, существует ли вакансия
        logger.info(f"🔍 Проверяем вакансию {vacancy_id} в БД...")
        with get_session() as session:
            vacancy = session.query(Vacancy).filter(Vacancy.id == vacancy_id).first()
            if vacancy:
                logger.info(f"🔍 Вакансия найдена: ID={vacancy.id}, роль={vacancy.role}, город={vacancy.city}")
            else:
                logger.error(f"❌ Вакансия {vacancy_id} не найдена в базе данных!")
                vk_bot.send_message(user_id, f"❌ Ошибка: вакансия не найдена")
                return
        
        # Импортируем функцию поиска
        logger.info("🔄 Импортируем gather_real_candidates из bot...")
        try:
            from bot import gather_real_candidates
            logger.info("✅ gather_real_candidates импортирована успешно")
        except ImportError as e:
            logger.error(f"❌ Ошибка импорта gather_real_candidates: {e}")
            vk_bot.send_message(user_id, f"❌ Ошибка: не могу найти функцию поиска кандидатов. {e}")
            return
        
        # Запускаем поиск
        logger.info(f"🔍 Вызываю gather_real_candidates({vacancy_id})...")
        await gather_real_candidates(vacancy_id)
        logger.info(f"✅ gather_real_candidates завершён")
        
        # Проверяем результаты
        with get_session() as session:
            count = session.query(Candidate).filter(Candidate.vacancy_id == vacancy_id).count()
            candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy_id).limit(5).all()
            
            logger.info(f"📊 Результаты поиска: найдено {count} кандидатов")
            for c in candidates:
                logger.info(f"   - {c.name_or_nick} | источник: {c.source} | оценка: {c.score} | статус: {c.status}")
        
        if count > 0:
            vk_bot.send_message(
                user_id,
                f"✅ Поиск завершён! Найдено кандидатов: {count}\n\n"
                f"Посмотреть кандидатов: /candidates"
            )
        else:
            hh_token_set = bool(settings.hh_api_token)
            superjob_key_set = bool(settings.superjob_api_key)
            
            message = (
                f"⚠️ Поиск завершён, но кандидаты не найдены.\n\n"
                f"📊 Статистика:\n"
                f"• HeadHunter токен: {'✅ установлен' if hh_token_set else '❌ не установлен'}\n"
                f"• SuperJob ключ: {'✅ установлен' if superjob_key_set else '❌ не установлен'}\n\n"
                f"Возможные причины:\n"
                f"• Нет подходящих резюме в выбранном городе\n"
                f"• Требуется настройка API токенов в Render\n"
                f"• Ошибки в работе API (проверьте логи выше)"
            )
            vk_bot.send_message(user_id, message)
            
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        import traceback
        traceback.print_exc()
        vk_bot.send_message(
            user_id,
            f"❌ Ошибка при поиске кандидатов: {str(e)[:200]}\n\n"
            f"Подробности в логах Render."
        )


async def handle_candidates(user_id: int, vk_bot, company, state):
    """Показ списка кандидатов"""
    if not company:
        vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
        if not vacancy:
            vk_bot.send_message(user_id, "📭 Нет вакансий. Создайте: /new_job")
            return
        
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).order_by(Candidate.score.desc()).all()
        
        if not candidates:
            vk_bot.send_message(
                user_id,
                "📭 Кандидаты не найдены.\n\n"
                "Возможно, поиск ещё не завершён или не запущен.\n"
                "Проверьте, что вы создали вакансию через /new_job\n"
                "и подождите 1-3 минуты после создания."
            )
            return
        
        # Формируем список кандидатов
        text = f"📊 <b>Вакансия: {vacancy.role} ({vacancy.city})</b>\n\n"
        text += f"Найдено кандидатов: {len(candidates)}\n\n"
        
        for i, c in enumerate(candidates[:10], 1):
            source_emoji = {
                'hh': '🇭',
                'superjob': '🟢',
                'habr': '👨‍💻',
                'trudvsem': '🏢',
                'telegram': '✈️',
                'test': '🧪'
            }.get(c.source, '📌')
            
            status_emoji = {
                'found': '🔍',
                'filtered': '🟢',
                'invited': '📩',
                'qualified': '✅',
                'interview': '📅',
                'rejected': '❌'
            }.get(c.status, '📌')
            
            text += f"{i}. {source_emoji} <b>{c.name_or_nick}</b>\n"
            text += f"   📍 {c.city} | 💼 {c.experience_text[:60]}\n"
            text += f"   📊 Оценка: {c.score}/100 | {status_emoji} {c.status}\n\n"
        
        if len(candidates) > 10:
            text += f"\n... и ещё {len(candidates) - 10} кандидатов"
        
        vk_bot.send_message(user_id, text)


async def handle_filters(user_id: int, vk_bot, company):
    """Управление фильтрами"""
    if not company:
        vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    filters = getattr(company, 'filters_settings', {})
    
    text = (
        f"🔧 <b>Управление фильтрами</b>\n\n"
        f"🏙️ Город: {'✅' if filters.get('city', True) else '❌'}\n"
        f"💰 Зарплата: {'✅' if filters.get('salary', True) else '❌'}\n"
        f"💼 Опыт: {'✅' if filters.get('experience', True) else '❌'}\n"
        f"📋 Требования: {'✅' if filters.get('skills', True) else '❌'}\n\n"
        f"Для изменения используйте Telegram бота @goodWorkingBot"
    )
    vk_bot.send_message(user_id, text)


async def handle_analytics(user_id: int, vk_bot, company):
    """Аналитика"""
    if not company:
        vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
        if not vacancy:
            vk_bot.send_message(user_id, "📭 Нет вакансий")
            return
        
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
        total = len(candidates)
        
        status_counts = {}
        source_counts = {}
        
        for c in candidates:
            status_counts[c.status] = status_counts.get(c.status, 0) + 1
            source_counts[c.source] = source_counts.get(c.source, 0) + 1
        
        text = (
            f"📊 <b>Аналитика: {vacancy.role}</b>\n\n"
            f"Всего кандидатов: {total}\n\n"
            f"<b>По статусам:</b>\n"
            f"🔍 Найдено: {status_counts.get('found', 0)}\n"
            f"🟢 Подходят: {status_counts.get('filtered', 0)}\n"
            f"📩 Приглашены: {status_counts.get('invited', 0)}\n"
            f"✅ Квалифицированы: {status_counts.get('qualified', 0)}\n"
            f"📅 Собеседование: {status_counts.get('interview', 0)}\n"
            f"❌ Отсеяно: {status_counts.get('rejected', 0)}\n\n"
            f"<b>По источникам:</b>\n"
            f"🇭 HeadHunter: {source_counts.get('hh', 0)}\n"
            f"🟢 SuperJob: {source_counts.get('superjob', 0)}\n"
            f"👨‍💻 Habr: {source_counts.get('habr', 0)}\n"
            f"🏢 Trudvsem: {source_counts.get('trudvsem', 0)}\n"
            f"✈️ Telegram: {source_counts.get('telegram', 0)}"
        )
        vk_bot.send_message(user_id, text)


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
        "По всем вопросам обращайтесь к администратору."
    )
    vk_bot.send_message(user_id, text)
