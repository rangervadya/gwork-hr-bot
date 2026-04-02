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
        self.last_candidate_id = None


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
    
    # Обработка команд с параметрами
    if text.startswith('/invite_'):
        candidate_id = text.replace('/invite_', '').strip()
        if candidate_id.isdigit():
            await handle_invite(user_id, vk_bot, int(candidate_id))
            return
    
    if text.startswith('/skip_'):
        candidate_id = text.replace('/skip_', '').strip()
        if candidate_id.isdigit():
            await handle_skip(user_id, vk_bot, int(candidate_id), state)
            return
    
    if text.startswith('/fav_'):
        candidate_id = text.replace('/fav_', '').strip()
        if candidate_id.isdigit():
            await handle_fav(user_id, vk_bot, int(candidate_id))
            return
    
    if text.startswith('/ask_'):
        candidate_id = text.replace('/ask_', '').strip()
        if candidate_id.isdigit():
            await handle_ask(user_id, vk_bot, int(candidate_id))
            return
    
    # Обработка основных команд
    if text == '/start' or text == 'start' or text == 'начать':
        await handle_start(user_id, vk_bot, company)
        return
    
    elif text == '/onboarding' or text == 'онбординг':
        await handle_onboarding_start(user_id, vk_bot, state)
        return
    
    elif text == '/new_job' or text == 'новая вакансия':
        await handle_new_job_start(user_id, vk_bot, state)
        return
    
    elif text == '/search' or text == 'поиск':
        await handle_search(user_id, vk_bot, company, state)
        return
    
    elif text == '/candidates' or text == 'кандидаты':
        await handle_candidates(user_id, vk_bot, company, state)
        return
    
    elif text == '/next_page' or text == 'дальше':
        state.page += 1
        await handle_candidates(user_id, vk_bot, company, state)
        return
    
    elif text == '/prev_page' or text == 'назад':
        state.page = max(0, state.page - 1)
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
        dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
        vk_bot.send_message(
            user_id,
            "❓ Неизвестная команда.\n\n"
            "Доступные команды:\n"
            "/start — начать работу\n"
            "/new_job — создать вакансию\n"
            "/search — запустить поиск кандидатов\n"
            "/candidates — список кандидатов\n"
            "/next_page — следующая страница\n"
            "/prev_page — предыдущая страница\n"
            "/filters — управление фильтрами\n"
            "/analytics — аналитика\n"
            "/help — справка\n\n"
            "Для действий с кандидатами:\n"
            "/invite_<id> — пригласить\n"
            "/skip_<id> — пропустить\n"
            "/fav_<id> — в избранное\n"
            "/ask_<id> — задать вопрос"
            f"{dev_info}"
        )


async def handle_start(user_id: int, vk_bot, company):
    """Обработка /start"""
    dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
    
    if company:
        text = (
            f"👋 С возвращением, {company.name_and_industry}!\n\n"
            f"📍 {company.location}\n"
            f"📅 {company.schedule}\n"
            f"💰 {company.salary_range}\n\n"
            f"Доступные команды:\n"
            f"/new_job — создать новую вакансию\n"
            f"/search — запустить поиск кандидатов\n"
            f"/candidates — список кандидатов\n"
            f"/filters — управление фильтрами\n"
            f"/analytics — аналитика\n"
            f"/help — справка"
            f"{dev_info}"
        )
    else:
        text = (
            "👋 Добро пожаловать в GWork HR Bot!\n\n"
            "Я помогаю находить кандидатов и автоматизировать HR-процессы.\n\n"
            "🔍 Что я умею:\n"
            "• Ищу кандидатов в 5+ источниках\n"
            "• Автоматически проверяю резюме\n"
            "• Общаюсь с кандидатами и провожу предквалификацию\n"
            "• Назначаю собеседования\n\n"
            "📋 Как создать вакансию:\n"
            "1. Напишите /onboarding\n"
            "2. Напишите /new_job\n"
            "3. Укажите роль и город\n"
            "4. Задайте параметры поиска\n\n"
            "После создания вакансии запустите поиск: /search\n\n"
            "Для начала работы напишите /onboarding"
            f"{dev_info}"
        )
    vk_bot.send_message(user_id, text)


async def handle_onboarding_start(user_id: int, vk_bot, state):
    """Начало онбординга"""
    state.state = 'onboarding'
    state.data = {}
    state.step = 1
    
    vk_bot.send_message(
        user_id,
        "📝 Расскажите о компании:\n\n"
        "Название и сфера деятельности.\n"
        "Например: Салон красоты \"Лилия\", услуги маникюра"
    )


async def handle_onboarding_step(user_id: int, vk_bot, state, text: str):
    """Шаги онбординга"""
    step = state.step
    
    if step == 1:
        state.data['name'] = text
        state.step = 2
        vk_bot.send_message(user_id, "📍 Где находитесь?\n\nГород и район.")
    
    elif step == 2:
        state.data['location'] = text
        state.step = 3
        vk_bot.send_message(user_id, "📅 График работы:\n\nНапример: 2/2 с 10:00 до 22:00")
    
    elif step == 3:
        state.data['schedule'] = text
        state.step = 4
        vk_bot.send_message(user_id, "💰 Зарплатная вилка:\n\nНапример: от 40 000 до 60 000")
    
    elif step == 4:
        state.data['salary'] = text
        state.step = 5
        vk_bot.send_message(user_id, "🎭 Тон общения:\n\nстрогий, дружелюбный или нейтральный")
    
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
        
        dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
        vk_bot.send_message(
            user_id,
            "✅ Профиль компании сохранён!\n\n"
            "Теперь создайте вакансию: /new_job"
            f"{dev_info}"
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
        "🔍 Кого ищем?\n\nОпишите роль:\nНапример: Администратор"
    )


async def handle_new_job_step(user_id: int, vk_bot, state, text: str):
    """Шаги создания вакансии"""
    step = state.step
    
    if step == 1:
        state.data['role'] = text
        state.step = 2
        vk_bot.send_message(user_id, "🌆 Город поиска:\n\nНапример: Москва")
    
    elif step == 2:
        state.data['city'] = text
        state.step = 3
        vk_bot.send_message(user_id, "📅 График работы:\n\nНапример: 5/2")
    
    elif step == 3:
        state.data['schedule'] = text
        state.step = 4
        vk_bot.send_message(user_id, "💰 Зарплата ОТ (число или '-'):")
    
    elif step == 4:
        salary_from = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_from'] = salary_from
        state.step = 5
        vk_bot.send_message(user_id, "💰 Зарплата ДО (число или '-'):")
    
    elif step == 5:
        salary_to = None if text == '-' else int(text) if text.isdigit() else None
        state.data['salary_to'] = salary_to
        state.step = 6
        vk_bot.send_message(user_id, "⏰ Когда нужен сотрудник?")
    
    elif step == 6:
        state.data['start_when'] = text
        state.step = 7
        vk_bot.send_message(user_id, "⚠️ Критичные требования (или '-'):")
    
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
            
            # Сохраняем данные для сообщения ДО очистки state
            role = state.data['role']
            city = state.data['city']
            salary_from = state.data.get('salary_from', '-')
            salary_to = state.data.get('salary_to', '-')
            schedule = state.data['schedule']
            
            logger.info(f"✅ Вакансия создана: ID={vacancy_id}, роль={role}, город={city}")
        
        state.state = None
        state.data = {}
        state.step = 0
        
        dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
        vk_bot.send_message(
            user_id,
            f"✅ Вакансия создана!\n\n"
            f"📋 {role} в городе {city}\n"
            f"💰 Зарплата: {salary_from} - {salary_to}\n"
            f"📅 График: {schedule}\n\n"
            f"Теперь запустите поиск кандидатов: /search"
            f"{dev_info}"
        )


async def handle_search(user_id: int, vk_bot, company, state):
    """Ручной запуск поиска кандидатов"""
    if not company:
        vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
        if not vacancy:
            vk_bot.send_message(user_id, "📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy_id = vacancy.id
        role = vacancy.role
        city = vacancy.city
        
        vk_bot.send_message(
            user_id,
            f"🔍 Начинаю поиск кандидатов для вакансии:\n"
            f"📋 {role} в городе {city}\n\n"
            f"Это может занять 1-3 минуты.\n\n"
            f"После завершения используйте /candidates"
        )
        
        # Запускаем поиск
        await search_and_notify(user_id, vacancy_id, vk_bot)


async def auto_invite_candidates(vacancy_id: int, vk_bot, user_id: int):
    """Автоматически приглашает кандидатов с высоким рейтингом"""
    try:
        with get_session() as session:
            vacancy = session.query(Vacancy).filter(Vacancy.id == vacancy_id).first()
            if not vacancy:
                return
            
            company = session.query(Company).filter(Company.id == vacancy.company_id).first()
            
            # Ищем кандидатов с оценкой 80+
            top_candidates = session.query(Candidate).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.score >= 80,
                Candidate.status == CandidateStatus.FOUND.value
            ).all()
            
            invited_count = 0
            for candidate in top_candidates:
                # Проверяем, есть ли контакт для отправки
                if candidate.contact and candidate.contact.isdigit():
                    invite_text = generate_invite_message_simple(candidate, vacancy, company)
                    success = vk_bot.send_message(int(candidate.contact), invite_text)
                    if success:
                        candidate.status = CandidateStatus.INVITED.value
                        invited_count += 1
                        logger.info(f"✅ Авто-приглашение отправлено {candidate.name_or_nick}")
            
            if invited_count > 0:
                session.commit()
                await vk_bot.send_message(
                    user_id,
                    f"📨 Автоматически отправлено {invited_count} приглашений кандидатам с высоким рейтингом!"
                )
            
    except Exception as e:
        logger.error(f"❌ Ошибка авто-приглашения: {e}")


def generate_invite_message_simple(candidate: Candidate, vacancy: Vacancy, company: Company) -> str:
    """Генерирует простое приглашение для кандидата (без HTML)"""
    return (
        f"👋 Здравствуйте, {candidate.name_or_nick}!\n\n"
        f"Меня зовут ИИ-HR, я помогаю компании {company.name_and_industry} "
        f"в {company.location} с подбором персонала.\n\n"
        f"Мы сейчас ищем {vacancy.role}. "
        f"Ваш опыт нам показался интересным.\n\n"
        f"Приглашаем вас на собеседование!\n\n"
        f"📍 Место: {company.location}\n"
        f"📞 Контакт: {company.report_email or 'уточните у администратора'}\n\n"
        f"Пожалуйста, напишите удобное для вас время.\n\n"
        f"📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
    )


async def search_and_notify(user_id: int, vacancy_id: int, vk_bot):
    """Поиск реальных кандидатов и уведомление"""
    logger.info(f"🔍🔍🔍 search_and_notify: НАЧАЛО ВЫПОЛНЕНИЯ для вакансии {vacancy_id}")
    
    try:
        with get_session() as session:
            vacancy = session.query(Vacancy).filter(Vacancy.id == vacancy_id).first()
            if vacancy:
                logger.info(f"🔍 Вакансия найдена: {vacancy.role} в {vacancy.city}")
            else:
                logger.error(f"❌ Вакансия {vacancy_id} не найдена!")
                vk_bot.send_message(user_id, f"❌ Ошибка: вакансия не найдена")
                return
        
        # Импортируем функцию поиска
        logger.info("🔄 Импортируем gather_real_candidates из bot...")
        from bot import gather_real_candidates
        logger.info("✅ gather_real_candidates импортирована")
        
        # Запускаем поиск
        logger.info(f"🔍 Вызываю gather_real_candidates({vacancy_id})...")
        await gather_real_candidates(vacancy_id)
        logger.info(f"✅ gather_real_candidates завершён")
        
        # Проверяем результаты
        with get_session() as session:
            count = session.query(Candidate).filter(Candidate.vacancy_id == vacancy_id).count()
            logger.info(f"📊 Результаты поиска: найдено {count} кандидатов")
        
        # Автоматически приглашаем топ-кандидатов
        await auto_invite_candidates(vacancy_id, vk_bot, user_id)
        
        if count > 0:
            vk_bot.send_message(
                user_id,
                f"✅ Поиск завершён! Найдено кандидатов: {count}\n\n"
                f"Топ-кандидаты (оценка 80+) получили автоматические приглашения!\n\n"
                f"Посмотреть всех кандидатов: /candidates"
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
                f"• Проверьте логи Render для деталей"
            )
            vk_bot.send_message(user_id, message)
            
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        import traceback
        traceback.print_exc()
        vk_bot.send_message(user_id, f"❌ Ошибка поиска: {str(e)[:200]}")


async def handle_candidates(user_id: int, vk_bot, company, state):
    """Показ списка кандидатов с командами для действий и ссылками (без HTML)"""
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
                "Запустите поиск: /search\n\n"
                "После завершения поиска используйте /candidates"
            )
            return
        
        # Пагинация
        page = state.page
        per_page = 3
        total = len(candidates)
        total_pages = (total + per_page - 1) // per_page
        start = page * per_page
        end = min(start + per_page, total)
        
        # Статистика
        text = f"📊 Вакансия: {vacancy.role} ({vacancy.city})\n\n"
        text += f"Всего кандидатов: {total}\n"
        text += f"Страница {page + 1} из {total_pages}\n\n"
        
        vk_bot.send_message(user_id, text)
        
        # Показываем кандидатов
        for i, c in enumerate(candidates[start:end], start + 1):
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
                'rejected': '❌',
                'favorite': '⭐',
                'archive': '📦'
            }.get(c.status, '📌')
            
            card = (
                f"👤 {c.name_or_nick} (ID: {c.id})\n"
                f"📍 {c.city}\n"
                f"💼 {c.experience_text[:100]}\n"
                f"🛠️ Навыки: {c.skills_text[:80]}\n"
                f"📊 Оценка: {c.score}/100 | {status_emoji} {c.status}\n"
                f"📎 Источник: {source_emoji}\n"
                f"📞 Контакт: {c.contact or 'не указан'}\n"
            )
            
            # Добавляем ссылку на профиль (обычным текстом, без HTML)
            if c.source_link and c.source_link not in ['', 'None']:
                card += f"🔗 Ссылка на профиль ({c.source}): {c.source_link}\n"
            
            card += (
                f"\nДействия (напишите команду):\n"
                f"✅ /invite_{c.id} — пригласить\n"
                f"❌ /skip_{c.id} — пропустить\n"
                f"⭐ /fav_{c.id} — в избранное\n"
                f"💬 /ask_{c.id} — задать вопрос\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            vk_bot.send_message(user_id, card)
        
        # Навигация
        if total_pages > 1:
            nav_text = "📌 Навигация:\n"
            if page > 0:
                nav_text += "◀️ /prev_page — предыдущая страница\n"
            if page < total_pages - 1:
                nav_text += "▶️ /next_page — следующая страница"
            vk_bot.send_message(user_id, nav_text)


async def handle_invite(user_id: int, vk_bot, candidate_id: int):
    """Пригласить кандидата"""
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            vk_bot.send_message(user_id, "❌ Кандидат не найден")
            return
        
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).first()
        company = session.query(Company).filter(Company.id == vacancy.company_id).first()
        
        # Генерируем приглашение (без HTML)
        invite_text = generate_invite_message_simple(candidate, vacancy, company)
        
        # Отправляем сообщение кандидату (если есть контакт)
        if candidate.contact and candidate.contact.isdigit():
            success = vk_bot.send_message(int(candidate.contact), invite_text)
            if success:
                candidate.status = CandidateStatus.INVITED.value
                session.commit()
                vk_bot.send_message(user_id, f"✅ Приглашение отправлено кандидату {candidate.name_or_nick}")
                return
        
        # Если нет контакта или не удалось отправить
        contact_info = f"📞 Контакт кандидата: {candidate.contact or 'не указан'}"
        source_link = f"🔗 Ссылка на профиль: {candidate.source_link}" if candidate.source_link else ""
        
        vk_bot.send_message(
            user_id,
            f"📝 Пример приглашения для {candidate.name_or_nick}:\n\n{invite_text}\n\n{contact_info}\n{source_link}"
        )


async def handle_skip(user_id: int, vk_bot, candidate_id: int, state):
    """Пропустить кандидата (в архив)"""
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            vk_bot.send_message(user_id, "❌ Кандидат не найден")
            return
        
        candidate.status = CandidateStatus.ARCHIVE.value
        candidate.rejection_reason = "Пропущен HR"
        session.commit()
        
        vk_bot.send_message(user_id, f"✅ Кандидат {candidate.name_or_nick} перемещён в архив")


async def handle_fav(user_id: int, vk_bot, candidate_id: int):
    """Добавить кандидата в избранное"""
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            vk_bot.send_message(user_id, "❌ Кандидат не найден")
            return
        
        candidate.status = CandidateStatus.FAVORITE.value
        session.commit()
        
        vk_bot.send_message(user_id, f"⭐ Кандидат {candidate.name_or_nick} добавлен в избранное")


async def handle_ask(user_id: int, vk_bot, candidate_id: int):
    """Задать вопрос кандидату"""
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            vk_bot.send_message(user_id, "❌ Кандидат не найден")
            return
        
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).first()
        
        questions = (
            f"❓ Примеры вопросов для {candidate.name_or_nick}:\n\n"
            f"• Подходит ли вам график работы ({vacancy.schedule})?\n"
            f"• Какие у вас зарплатные ожидания?\n"
            f"• Когда вы могли бы выйти на работу?\n"
            f"• Расскажите о вашем опыте в {vacancy.role}?\n\n"
            f"📞 Контакт кандидата: {candidate.contact or 'не указан'}"
        )
        
        if candidate.source_link:
            questions += f"\n🔗 Ссылка на профиль: {candidate.source_link}"
        
        vk_bot.send_message(user_id, questions)


async def handle_filters(user_id: int, vk_bot, company):
    """Управление фильтрами"""
    if not company:
        vk_bot.send_message(user_id, "❌ Сначала пройдите онбординг: /onboarding")
        return
    
    filters = getattr(company, 'filters_settings', {})
    
    text = (
        f"🔧 Управление фильтрами\n\n"
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
        
        dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
        
        text = (
            f"📊 Аналитика: {vacancy.role}\n\n"
            f"Всего кандидатов: {total}\n\n"
            f"По статусам:\n"
            f"🔍 Найдено: {status_counts.get('found', 0)}\n"
            f"🟢 Подходят: {status_counts.get('filtered', 0)}\n"
            f"📩 Приглашены: {status_counts.get('invited', 0)}\n"
            f"⭐ Избранные: {status_counts.get('favorite', 0)}\n"
            f"✅ Квалифицированы: {status_counts.get('qualified', 0)}\n"
            f"📅 Собеседование: {status_counts.get('interview', 0)}\n"
            f"❌ Отсеяно: {status_counts.get('rejected', 0)}\n"
            f"📦 Архив: {status_counts.get('archive', 0)}\n\n"
            f"По источникам:\n"
            f"🇭 HeadHunter: {source_counts.get('hh', 0)}\n"
            f"🟢 SuperJob: {source_counts.get('superjob', 0)}\n"
            f"👨‍💻 Habr: {source_counts.get('habr', 0)}\n"
            f"🏢 Trudvsem: {source_counts.get('trudvsem', 0)}\n"
            f"✈️ Telegram: {source_counts.get('telegram', 0)}"
            f"{dev_info}"
        )
        vk_bot.send_message(user_id, text)


async def handle_help(user_id: int, vk_bot):
    """Справка"""
    dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
    
    text = (
        "🤖 GWork HR Bot - Справка\n\n"
        "🔧 НАСТРОЙКА\n"
        "/onboarding — профиль компании\n"
        "/new_job — новая вакансия\n"
        "/filters — управление фильтрами\n\n"
        "👥 КАНДИДАТЫ\n"
        "/search — запустить поиск кандидатов\n"
        "/candidates — список кандидатов\n"
        "/next_page — следующая страница\n"
        "/prev_page — предыдущая страница\n"
        "/analytics — аналитика\n\n"
        "⚡ ДЕЙСТВИЯ С КАНДИДАТАМИ\n"
        "/invite_<id> — пригласить\n"
        "/skip_<id> — пропустить\n"
        "/fav_<id> — в избранное\n"
        "/ask_<id> — задать вопрос\n\n"
        "🌐 ИСТОЧНИКИ КАНДИДАТОВ\n"
        "🇭 HeadHunter | 🟢 SuperJob\n"
        "👨‍💻 Habr Career | 🏢 Работа в России | ✈️ Telegram"
        f"{dev_info}"
    )
    vk_bot.send_message(user_id, text)
