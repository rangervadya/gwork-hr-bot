# FIXED VERSION: All sources + Hard Filters + Red Flags + Normalization + Pre-qualification + Date + Export + Analytics + Email + Calendar + Web Server for Render + VK Support
import asyncio
import logging
import re
import os
import json
import tempfile
import threading
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func
from flask import Flask, jsonify

from config import settings
from db import get_session, init_db
from deepseek_client import DeepSeekClient
from models import Candidate, CandidateStatus, Company, InterviewSlot, Vacancy, VacancyTemplate
from telegram_import import import_candidate_from_forward
from telegram_parser import telegram_parser
from filters import (
    extract_salary, 
    extract_experience_years, 
    normalize_city, 
    apply_hard_filters,
    check_red_flags,
    get_red_flags_score,
    red_flags_description,
    normalize_experience_level,
    parse_experience_to_years,
    normalize_skills_list,
    extract_keywords,
    normalize_city_extended,
    extract_city_from_text
)
from pre_qualification import PreQualificationAnalyzer, format_qualification_results
from export_utils import (
    generate_csv_report,
    generate_html_report,
    filter_by_date,
    sort_candidates,
    get_date_filter_keyboard,
    get_sort_keyboard
)
from analytics import (
    AnalyticsService,
    format_analytics_report
)
from email_service import email_service
from yandex_calendar import YandexCalendarClient
from vk_bot import init_vk_bot, vk_bot, VKBot


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER HEALTH CHECKS =====
app = Flask(__name__)

@app.route('/')
@app.route('/health')
@app.route('/ping')
def health_check():
    """Endpoint для проверки здоровья сервиса Render.com"""
    return jsonify({
        'status': 'ok',
        'service': 'GWork HR Bot',
        'timestamp': datetime.now().isoformat()
    }), 200

def run_web_server():
    """Запускает Flask сервер в отдельном потоке"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Запуск веб-сервера для health checks на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


bot = Bot(settings.bot_token)
dp = Dispatcher()
router = Router()
dp.include_router(router)

deepseek = DeepSeekClient()


def clean_html(text: str) -> str:
    """Очищает текст от HTML-тегов"""
    if not text:
        return ""
    text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('</p>', '\n').replace('<p>', '')
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С VK =====

async def send_vk_message_to_candidate(candidate: Candidate, message_text: str) -> bool:
    """
    Отправляет сообщение кандидату через VK
    """
    global vk_bot
    
    if not vk_bot:
        logger.warning("⚠️ VK бот не инициализирован")
        return False
    
    # Используем метод отправки сообщения кандидату
    return vk_bot.send_message_to_candidate(candidate, message_text)


async def handle_vk_message(message_data: Dict[str, Any]):
    """
    Обрабатывает сообщение из VK
    """
    try:
        user_id = message_data['user_id']
        text = message_data['text']
        payload = message_data.get('payload')
        
        logger.info(f"📨 Обработка VK сообщения от {user_id}: {text[:50]}...")

        # Импортируем vk_bot внутри функции, чтобы избежать циклического импорта
        import sys
        import importlib
        
        # Перезагружаем модуль, чтобы получить актуальный экземпляр
        if 'vk_bot' in sys.modules:
            importlib.reload(sys.modules['vk_bot'])
        
        from vk_bot import vk_bot as vk_bot_instance
        
        if vk_bot_instance is None:
            logger.error("❌ VK бот не инициализирован, пробуем инициализировать...")
            from vk_bot import init_vk_bot
            init_vk_bot()
            from vk_bot import vk_bot as vk_bot_instance
        
        if vk_bot_instance is None:
            logger.error("❌ VK бот не инициализирован, сообщение не будет отправлено")
            return
        
        # ===== ОБРАБОТКА КОМАНДЫ /start =====
        if text == '/start' or text == 'start' or text == 'начать':
            logger.info(f"📨 VK: получена команда /start от {user_id}")
            with get_session() as session:
                company = session.query(Company).filter(Company.owner_id == user_id).first()
                logger.info(f"📨 VK: компания найдена: {company is not None}")
    
            if vk_bot:
                logger.info(f"📨 VK: vk_bot существует, отправляю сообщение...")
        
            if company:
                welcome_text = (
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
                welcome_text = (
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
            
            logger.info(f"📨 VK: текст сообщения готов, длина: {len(welcome_text)}")
            logger.info(f"📨 VK: вызываю vk_bot.send_message({user_id}, ...)")
            
            try:
                result = await vk_bot_instance.send_message(user_id, welcome_text)
                logger.info(f"📨 VK: результат отправки: {result}")
                if result:
                    logger.info(f"✅ VK: сообщение успешно отправлено пользователю {user_id}")
                else:
                    logger.error(f"❌ VK: send_message вернул False")
            except Exception as e:
                logger.error(f"❌ VK: ошибка при отправке: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.error("❌ VK: vk_bot не инициализирован!")
        
        return
        
        # ===== ОБРАБОТКА КОМАНДЫ /help =====
        if text == '/help' or text == 'help' or text == 'помощь':
            help_text = (
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
            if vk_bot:
                await vk_bot.send_message(user_id, help_text)
            return
        # Ищем кандидата по VK ID в контактах
        with get_session() as session:
            # Ищем кандидата, у которого контакт содержит этот VK ID
            candidates = session.query(Candidate).filter(
                Candidate.contact.like(f"%{user_id}%")
            ).all()
            
            if not candidates:
                logger.info(f"Кандидат с VK ID {user_id} не найден")
                return
            
            candidate = candidates[0]  # Берём первого подходящего
            
            # Проверяем, не отсеян ли кандидат
            if candidate.status == CandidateStatus.REJECTED.value:
                return
            
            vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).first()
            company = session.query(Company).filter(Company.id == vacancy.company_id).first()
            
            if not vacancy or not company:
                return
            
            # Обновляем информацию о последнем ответе
            candidate.last_reply = text[:500]
            candidate.last_reply_at = datetime.now()
            candidate.last_activity_at = datetime.now()
            
            # Здесь можно добавить логику обработки ответов,
            # аналогичную той, что в handle_candidate_reply для Telegram
            
            session.commit()
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки VK сообщения: {e}")


# ===== ПОИСК РЕАЛЬНЫХ РЕЗЮМЕ В HEADHUNTER =====
async def search_hh_resumes(query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск РЕАЛЬНЫХ РЕЗЮМЕ на HeadHunter с использованием токена
    """
    try:
        if not settings.hh_api_token:
            logger.warning("⚠️ HH_API_TOKEN не настроен")
            return []
        
        city_map = {
            "москва": "1",
            "санкт-петербург": "2",
            "спб": "2",
            "екатеринбург": "3",
            "новосибирск": "4",
            "казань": "88",
            "нижний новгород": "47",
            "челябинск": "104",
            "самара": "78",
            "ростов-на-дону": "76",
            "уфа": "99",
            "краснодар": "53",
            "воронеж": "26",
            "пермь": "72",
            "волгоград": "24",
        }
        
        area = city_map.get(city.lower(), "1")
        
        headers = {
            "Authorization": f"Bearer {settings.hh_api_token}",
            "User-Agent": "GWorkBot/1.0 (hr-bot)"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.hh.ru/resumes",
                params={
                    "text": query,
                    "area": area,
                    "per_page": limit,
                    "order_by": "relevance",
                    "clusters": False
                },
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                
                logger.info(f"✅ HH.ru: найдено {len(items)} резюме")
                
                candidates = []
                for item in items:
                    resume_id = item.get("id")
                    if not resume_id:
                        continue
                    
                    resume_response = await client.get(
                        f"https://api.hh.ru/resumes/{resume_id}",
                        headers=headers
                    )
                    
                    if resume_response.status_code != 200:
                        continue
                    
                    resume_data = resume_response.json()
                    
                    first_name = resume_data.get("first_name", "")
                    last_name = resume_data.get("last_name", "")
                    middle_name = resume_data.get("middle_name", "")
                    name_parts = [last_name, first_name, middle_name]
                    full_name = " ".join([p for p in name_parts if p]) or "Кандидат"
                    
                    area_data = resume_data.get("area", {})
                    city_name = area_data.get("name", city) if isinstance(area_data, dict) else city
                    
                    salary = resume_data.get("salary", {})
                    salary_text = "Не указано"
                    if salary:
                        amount = salary.get("amount")
                        currency = salary.get("currency", "руб")
                        if amount:
                            salary_text = f"{amount} {currency}"
                    
                    experience_items = resume_data.get("experience", [])
                    experience_text = []
                    for exp in experience_items[:3]:
                        position = exp.get("position", "")
                        company = exp.get("company", "")
                        start = exp.get("start", "")
                        end = exp.get("end", "настоящее время")
                        if position and company:
                            experience_text.append(f"{position} в {company} ({start}-{end})")
                    
                    experience_str = "\n".join(experience_text) if experience_text else "Опыт не указан"
                    
                    skills = resume_data.get("skills", "")
                    skills_list = [s.strip() for s in skills.split(",")] if skills else []
                    
                    contacts = []
                    for contact in resume_data.get("contact", []):
                        contact_type = contact.get("type", {}).get("name", "")
                        contact_value = contact.get("value", {}).get("email", "") or contact.get("value", {}).get("phone", "")
                        if contact_value:
                            contacts.append(f"{contact_type}: {contact_value}")
                    
                    contact_str = "\n".join(contacts) if contacts else ""
                    
                    resume_url = resume_data.get("alternate_url", "")
                    
                    age = resume_data.get("age", "")
                    age_text = f"Возраст: {age}" if age else ""
                    
                    about_parts = []
                    if salary_text != "Не указано":
                        about_parts.append(f"💰 {salary_text}")
                    if age_text:
                        about_parts.append(f"🎂 {age_text}")
                    
                    about_text = "\n".join(about_parts) if about_parts else resume_data.get("summary", "")[:200]
                    
                    candidates.append({
                        "name": full_name,
                        "city": city_name,
                        "experience": experience_str[:300],
                        "skills": skills_list[:8],
                        "about": about_text[:200],
                        "source": "hh",
                        "url": resume_url,
                        "contact": contact_str,
                        "is_real": True
                    })
                
                logger.info(f"✅ HH.ru: обработано {len(candidates)} резюме")
                return candidates
                
            elif response.status_code == 403:
                logger.error("❌ Нет доступа к резюме. Проверьте права токена (нужен токен работодателя)")
                return []
            else:
                logger.error(f"❌ HH.ru ошибка: {response.status_code}")
                return []
                
    except Exception as e:
        logger.error(f"❌ HH.ru ошибка: {e}")
        return []


# ===== ПОИСК КАНДИДАТОВ В SUPERJOB =====
async def search_superjob_real_candidates(query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Поиск кандидатов на SuperJob"""
    try:
        if not settings.superjob_api_key:
            logger.warning("⚠️ SUPERJOB_API_KEY не настроен")
            return []
        
        city_map = {
            "москва": "4",
            "санкт-петербург": "2",
            "спб": "2",
            "екатеринбург": "12",
            "новосибирск": "9",
            "казань": "21",
        }
        
        town_id = city_map.get(city.lower(), "4")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.superjob.ru/2.0/resumes/",
                params={
                    "keyword": query,
                    "town": town_id,
                    "count": limit,
                    "order_field": "date",
                    "order_direction": "desc"
                },
                headers={
                    "X-Api-App-Id": settings.superjob_api_key,
                    "User-Agent": "GWorkBot/1.0"
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                objects = data.get("objects", [])
                
                logger.info(f"✅ SuperJob: найдено {len(objects)} резюме")
                
                candidates = []
                for obj in objects:
                    first_name = obj.get("first_name", "")
                    last_name = obj.get("last_name", "")
                    middle_name = obj.get("middle_name", "")
                    name_parts = [last_name, first_name, middle_name]
                    full_name = " ".join([p for p in name_parts if p]) or "Кандидат"
                    
                    town = obj.get("town", {})
                    city_name = town.get("title", city) if isinstance(town, dict) else city
                    
                    payment_from = obj.get("payment_from", "")
                    payment_to = obj.get("payment_to", "")
                    currency = obj.get("currency", "rub")
                    
                    salary_text = ""
                    if payment_from and payment_to:
                        salary_text = f"{payment_from}-{payment_to} {currency}"
                    elif payment_from:
                        salary_text = f"от {payment_from} {currency}"
                    elif payment_to:
                        salary_text = f"до {payment_to} {currency}"
                    
                    experience_text = obj.get("experience", "")
                    
                    age = obj.get("age", "")
                    if age:
                        experience_text = f"Возраст: {age}, {experience_text}"
                    
                    education = obj.get("education", {})
                    education_text = education.get("name", "") if isinstance(education, dict) else ""
                    
                    skills_text = obj.get("skills", "")
                    skills_list = [s.strip() for s in skills_text.split(",")] if skills_text else []
                    
                    contacts = []
                    phones = obj.get("phone", [])
                    for phone in phones:
                        number = phone.get("number", "")
                        if number:
                            contacts.append(f"📞 {number}")
                    
                    emails = obj.get("email", [])
                    for email in emails:
                        if email:
                            contacts.append(f"📧 {email}")
                    
                    contact_str = "\n".join(contacts) if contacts else ""
                    
                    resume_url = obj.get("link", "")
                    
                    about_parts = []
                    if salary_text:
                        about_parts.append(f"💰 {salary_text}")
                    if education_text:
                        about_parts.append(f"🎓 {education_text}")
                    
                    about_text = "\n".join(about_parts) if about_parts else ""
                    
                    candidates.append({
                        "name": full_name,
                        "city": city_name,
                        "experience": experience_text[:300],
                        "skills": skills_list[:8],
                        "about": about_text[:200],
                        "source": "superjob",
                        "url": resume_url,
                        "contact": contact_str,
                        "is_real": True
                    })
                
                logger.info(f"✅ SuperJob: обработано {len(candidates)} кандидатов")
                return candidates
            else:
                logger.error(f"❌ SuperJob ошибка: {response.status_code}")
                return []
    except Exception as e:
        logger.error(f"❌ SuperJob ошибка: {e}")
        return []


# ===== ПОИСК КАНДИДАТОВ В HABR CAREER =====
async def search_habr_candidates(query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск кандидатов на Habr Career
    """
    try:
        if not settings.habr_client_id or not settings.habr_client_secret:
            logger.warning("⚠️ HABR_CLIENT_ID или HABR_CLIENT_SECRET не настроены")
            return []
        
        city_map = {
            "москва": "1",
            "санкт-петербург": "2",
            "спб": "2",
            "екатеринбург": "3",
            "новосибирск": "4",
            "казань": "88",
        }
        
        area = city_map.get(city.lower(), "1")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.hh.ru/resumes",
                params={
                    "text": query,
                    "area": area,
                    "per_page": limit,
                    "order_by": "relevance"
                },
                headers={
                    "User-Agent": "GWorkBot/1.0 (habr integration)"
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                
                logger.info(f"✅ Habr Career: найдено {len(items)} резюме")
                
                candidates = []
                for item in items:
                    resume_id = item.get("id")
                    if not resume_id:
                        continue
                    
                    resume_response = await client.get(
                        f"https://api.hh.ru/resumes/{resume_id}",
                        headers={"User-Agent": "GWorkBot/1.0 (habr)"}
                    )
                    
                    if resume_response.status_code != 200:
                        continue
                    
                    resume_data = resume_response.json()
                    
                    first_name = resume_data.get("first_name", "")
                    last_name = resume_data.get("last_name", "")
                    full_name = f"{last_name} {first_name}".strip() or "Кандидат"
                    
                    area_data = resume_data.get("area", {})
                    city_name = area_data.get("name", city) if isinstance(area_data, dict) else city
                    
                    salary = resume_data.get("salary", {})
                    salary_text = "Не указано"
                    if salary:
                        amount = salary.get("amount")
                        currency = salary.get("currency", "руб")
                        if amount:
                            salary_text = f"{amount} {currency}"
                    
                    experience_items = resume_data.get("experience", [])
                    experience_text = []
                    for exp in experience_items[:3]:
                        position = exp.get("position", "")
                        company = exp.get("company", "")
                        if position and company:
                            experience_text.append(f"{position} в {company}")
                    
                    skills = resume_data.get("skills", "")
                    skills_list = [s.strip() for s in skills.split(",")] if skills else []
                    
                    resume_url = resume_data.get("alternate_url", "")
                    
                    candidates.append({
                        "name": f"👨‍💻 {full_name}",
                        "city": city_name,
                        "experience": "\n".join(experience_text) if experience_text else "Опыт не указан",
                        "skills": skills_list[:8],
                        "about": f"💰 {salary_text}",
                        "source": "habr",
                        "url": resume_url,
                        "contact": "",
                        "is_real": True
                    })
                
                logger.info(f"✅ Habr: обработано {len(candidates)} кандидатов")
                return candidates
            else:
                logger.error(f"❌ Habr ошибка: {response.status_code}")
                return []
                
    except Exception as e:
        logger.error(f"❌ Habr ошибка: {e}")
        return []


# ===== ПОИСК КАНДИДАТОВ В TRUDVSEM (РАБОТА В РОССИИ) =====
async def search_trudvsem_candidates(query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск кандидатов на портале Работа в России
    """
    try:
        region_map = {
            "москва": "77",
            "санкт-петербург": "78",
            "спб": "78",
            "екатеринбург": "66",
            "новосибирск": "54",
            "казань": "16",
            "краснодар": "23",
            "ростов-на-дону": "61",
            "самара": "63",
            "нижний новгород": "52",
        }
        
        region = region_map.get(city.lower(), "77")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://opendata.trudvsem.ru/api/v1/vacancies",
                params={
                    "text": query,
                    "region": region,
                    "limit": limit,
                    "offset": 0
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                
                try:
                    vacancies = data.get("results", {}).get("vacancies", [])
                except:
                    vacancies = []
                
                logger.info(f"✅ Trudvsem: найдено {len(vacancies)} вакансий")
                
                candidates = []
                for item in vacancies[:limit]:
                    vacancy = item.get("vacancy", {})
                    
                    name = vacancy.get("job-name", "Без названия")
                    
                    company = vacancy.get("company", {})
                    company_name = company.get("short_name") or company.get("name", "Не указана")
                    
                    salary_min = vacancy.get("salary_min", "")
                    salary_max = vacancy.get("salary_max", "")
                    salary_text = "Не указана"
                    
                    if salary_min and salary_max:
                        salary_text = f"{salary_min}-{salary_max} руб."
                    elif salary_min:
                        salary_text = f"от {salary_min} руб."
                    elif salary_max:
                        salary_text = f"до {salary_max} руб."
                    
                    requirement = vacancy.get("requirement", {})
                    experience = requirement.get("experience", "Опыт не указан")
                    
                    vacancy_url = vacancy.get("vac_url", "")
                    
                    candidates.append({
                        "name": f"🏢 {name}",
                        "city": city,
                        "experience": experience,
                        "skills": [query],
                        "about": f"Компания: {company_name}\n💰 {salary_text}",
                        "source": "trudvsem",
                        "url": vacancy_url,
                        "contact": "",
                        "is_real": True
                    })
                
                logger.info(f"✅ Trudvsem: обработано {len(candidates)} вакансий")
                return candidates
            else:
                logger.error(f"❌ Trudvsem ошибка: {response.status_code}")
                return []
                
    except Exception as e:
        logger.error(f"❌ Trudvsem ошибка: {e}")
        return []


# ===== АВТОМАТИЧЕСКАЯ ОТПРАВКА СООБЩЕНИЙ КАНДИДАТАМ =====

async def send_message_to_candidate(candidate: Candidate, message_text: str) -> bool:
    """
    Отправляет сообщение кандидату через Telegram или VK
    Возвращает True если успешно, False если нет
    """
    try:
        contact = candidate.contact
        if not contact:
            logger.warning(f"Нет контакта для кандидата {candidate.id}")
            return False
        
        # Проверяем, является ли контакт Telegram username
        if contact.startswith('@'):
            username = contact[1:]
            try:
                await bot.send_message(
                    chat_id=f"@{username}",
                    text=message_text,
                    parse_mode="HTML"
                )
                logger.info(f"✅ Сообщение отправлено @{username}")
                
                # Обновляем время последнего сообщения
                with get_session() as session:
                    cand = session.query(Candidate).filter(Candidate.id == candidate.id).first()
                    if cand:
                        cand.last_message_sent = message_text[:500]
                        cand.last_message_at = datetime.now()
                        cand.last_activity_at = datetime.now()
                        session.commit()
                
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка отправки @{username}: {e}")
                return False
        
        # Проверяем, является ли контакт VK ID
        vk_match = re.search(r'vk(\d+)|^(\d+)$', contact)
        if vk_match:
            vk_id = vk_match.group(1) or vk_match.group(2)
            if vk_id and vk_bot:
                success = vk_bot.send_message_to_candidate(candidate, message_text)
                if success:
                    logger.info(f"✅ Сообщение отправлено VK ID {vk_id}")
                return success
        
        logger.info(f"Контакт {contact} не является Telegram username или VK ID")
        return False
        
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return False


def generate_invite_message(candidate: Candidate, vacancy: Vacancy, company: Company) -> str:
    """Генерирует приглашение для кандидата с учётом тона компании"""
    
    tone = company.tone.lower() if company.tone else "нейтральный"
    
    base_message = (
        f"👋 <b>Здравствуйте, {candidate.name_or_nick}!</b>\n\n"
        f"Меня зовут ИИ-HR, я помогаю компании <b>{company.name_and_industry}</b> "
        f"в <b>{company.location}</b> с подбором персонала.\n\n"
        f"Мы сейчас ищем <b>{vacancy.role}</b>. "
        f"Ваш опыт нам показался интересным, поэтому хотел бы задать несколько уточняющих вопросов:\n\n"
        f"1️⃣ Подходит ли вам график работы <b>{vacancy.schedule}</b>?\n"
        f"2️⃣ Какие у вас зарплатные ожидания?\n"
        f"3️⃣ Когда вы могли бы выйти на работу?\n\n"
    )
    
    if "строг" in tone:
        closing = (
            f"Прошу ответить в ближайшее время. "
            f"С уважением, HR-отдел {company.name_and_industry}."
        )
    elif "дружелюбн" in tone:
        closing = (
            f"Буду рад получить обратную связь! "
            f"Хорошего дня 😊"
        )
    else:
        closing = (
            f"Буду благодарен за ответ. "
            f"С уважением, HR-отдел {company.name_and_industry}."
        )
    
    return base_message + closing


def generate_followup_message(candidate: Candidate, vacancy: Vacancy, company: Company, step: int = 1, slots: List[Dict] = None) -> str:
    """Генерирует последующие сообщения в диалоге"""
    
    if step == 2:
        return (
            f"Спасибо за ответ! Теперь второй вопрос:\n\n"
            f"2️⃣ Какие у вас зарплатные ожидания?"
        )
    elif step == 3:
        return (
            f"Спасибо! И последний вопрос:\n\n"
            f"3️⃣ Когда вы могли бы выйти на работу?"
        )
    elif step == 4:
        slots_text = ""
        if slots:
            for i, slot in enumerate(slots[:3], 1):
                slots_text += f"{i}. {slot['text']}\n"
        
        return (
            f"Отлично! По вашим ответам вы нам подходите. "
            f"Предлагаю договориться о собеседовании.\n\n"
            f"У нас есть свободные слоты:\n"
            f"{slots_text}\n"
            f"Какое время вам удобно? (напишите цифру 1, 2 или 3)"
        )
    elif step == 5:
        return (
            f"Спасибо за ответы! У меня есть несколько уточняющих вопросов.\n\n"
            f"Пожалуйста, ответьте на них."
        )
    elif step == 6:
        return (
            f"Отлично! Записал вас на собеседование.\n\n"
            f"📍 Место: {company.location}\n"
            f"📞 Контакт: +7 (999) 123-45-67\n\n"
            f"Если не сможете прийти, пожалуйста, предупредите заранее."
        )
    else:
        return ""


def score_single_candidate(candidate_id: int) -> None:
    """Доскоировать одного кандидата"""
    with get_session() as session:
        candidate = (
            session.query(Candidate)
            .filter(Candidate.id == candidate_id)
            .one_or_none()
        )
        if not candidate:
            return
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).one()
        company = session.query(Company).filter(Company.id == vacancy.company_id).one()

        vacancy_desc = vacancy_to_description(vacancy, company)
        payload = [
            {
                "id": candidate.id,
                "name": candidate.name_or_nick,
                "city": candidate.city,
                "text": candidate.raw_text,
                "skills": candidate.skills_text,
                "source": candidate.source,
            }
        ]

    scores = deepseek.score_candidates(vacancy_desc, payload)
    scores_by_id = {int(s["id"]): s for s in scores if "id" in s}
    result = scores_by_id.get(candidate_id)

    with get_session() as session:
        candidate = (
            session.query(Candidate)
            .filter(Candidate.id == candidate_id)
            .one_or_none()
        )
        if not candidate:
            return

        if result:
            candidate.score = int(result.get("score", 0))
            candidate.explanation = str(result.get("explanation", ""))
        else:
            base = 50
            if vacancy.city.lower() in candidate.city.lower():
                base += 15
            candidate.score = max(0, min(100, base))
            if not candidate.explanation:
                candidate.explanation = "эвристическая оценка по городу"
        if candidate.score >= 80:
            candidate.status = CandidateStatus.FILTERED.value
        session.commit()


class OnboardingStates(StatesGroup):
    name_and_industry = State()
    location = State()
    schedule = State()
    salary_range = State()
    tone = State()
    # interview_how = State()  # Удалено


class VacancyStates(StatesGroup):
    role = State()
    city = State()
    experience_required = State()
    schedule = State()
    salary_from = State()
    salary_to = State()
    start_when = State()
    must_have = State()


class TemplateStates(StatesGroup):
    choosing = State()


class EmailState(StatesGroup):
    waiting_email = State()


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь глобальным администратором"""
    return user_id in settings.admin_ids if settings.admin_ids else False


def is_company_owner(user_id: int, company: Company) -> bool:
    """Проверяет, является ли пользователь владельцем компании"""
    return company.owner_id == user_id


def can_manage_filters(user_id: int, company: Company) -> bool:
    """
    Проверяет, может ли пользователь управлять фильтрами компании
    (администратор или владелец компании)
    """
    return is_admin(user_id) or is_company_owner(user_id, company)


def vacancy_to_description(v: Vacancy, c: Company) -> str:
    return (
        f"Компания: {c.name_and_industry} ({c.location})\n"
        f"Роль: {v.role}\n"
        f"Город: {v.city}\n"
        f"График: {v.schedule}\n"
        f"Зарплата: {v.salary_from or '-'} - {v.salary_to or '-'}\n"
        f"Критичные требования: {v.must_have if v.must_have and v.must_have != '-' else 'Не указаны'}\n"
    )


def build_candidate_card_text(c: Candidate) -> str:
    """Формирует текст карточки кандидата с нормализованными данными и предквалификацией"""
    source_display = {
        "hh": "🇭 HeadHunter",
        "superjob": "🟢 SuperJob",
        "habr": "👨‍💻 Habr Career", 
        "trudvsem": "🏢 Работа в России",
        "telegram": "✈️ Telegram"
    }
    
    source_text = source_display.get(c.source, c.source)
    
    # Форматируем дату создания
    created_date = c.created_at.strftime("%d.%m.%Y %H:%M") if c.created_at else "неизвестно"
    
    # Отметка нового кандидата
    new_tag = " 🆕" if c.is_new else ""
    
    lines = [
        f"👤 <b>{c.name_or_nick}</b>{new_tag}",
        f"📍 Город: {c.city}",
        f"💼 Опыт: {c.experience_text}",
        f"📅 Найден: {created_date}",
    ]
    
    if c.last_activity_at:
        last_activity = c.last_activity_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"⏱️ Последняя активность: {last_activity}")
    
    # Нормализованные данные
    if c.normalized_city:
        lines.append(f"🏙️ Нормализованный город: {c.normalized_city}")
    
    if c.normalized_city_from_text:
        lines.append(f"📍 Город из текста: {c.normalized_city_from_text}")
    
    if c.experience_years:
        lines.append(f"⏳ Опыт (годы): {c.experience_years}")
    
    if c.normalized_experience_level:
        lines.append(f"📊 Уровень опыта: {c.normalized_experience_level}")
    
    if c.skills_text:
        lines.append(f"🛠️ Навыки: {c.skills_text}")
    
    if c.extracted_skills:
        skills_str = ", ".join(c.extracted_skills[:8])
        lines.append(f"🔧 Нормализованные навыки: {skills_str}")
    
    if c.extracted_keywords:
        keywords_str = ", ".join(c.extracted_keywords[:5])
        lines.append(f"🔑 Ключевые слова: {keywords_str}")
        if len(c.extracted_keywords) > 5:
            lines.append(f"   ... и ещё {len(c.extracted_keywords) - 5}")
    
    if c.keyword_match_percentage:
        lines.append(f"📈 Совпадение ключевых слов: {c.keyword_match_percentage:.1f}%")
    
    if c.raw_text:
        clean_text = clean_html(c.raw_text)
        short_text = clean_text[:200] + "..." if len(clean_text) > 200 else clean_text
        lines.append(f"📝 {short_text}")
    
    if c.contact:
        lines.append(f"📞 Контакты: {c.contact}")
    
    if c.source_link and "example.com" not in c.source_link:
        lines.append(f"🔗 <a href='{c.source_link}'>Профиль на {c.source}</a>")
    
    # Информация о зарплате
    if c.salary_expectations:
        lines.append(f"💰 Ожидания по зарплате: {c.salary_expectations} руб.")
    
    # Красные флаги
    if c.red_flags:
        lines.append("")
        lines.append("⚠️ <b>Красные флаги:</b>")
        for flag in c.red_flags[:3]:
            lines.append(f"   • {flag}")
        if len(c.red_flags) > 3:
            lines.append(f"   ... и ещё {len(c.red_flags) - 3}")
    
    # Причина отсева
    if c.rejection_reason:
        lines.append(f"❌ <b>Отсеян:</b> {c.rejection_reason}")
    
    if c.critical_skills_match:
        lines.append(f"✅ Совпадение по требованиям: {c.critical_skills_match}")
    
    # Результаты предквалификации
    if c.qualification_score:
        lines.append(f"")
        lines.append(f"📊 <b>Предквалификация: {c.qualification_score:.1f}/100</b>")
        
        if c.qualification_details and 'verdict_text' in c.qualification_details:
            lines.append(f"   {c.qualification_details['verdict_text']}")
        
        if c.qualification_details and c.qualification_details.get('schedule'):
            s = c.qualification_details['schedule']
            lines.append(f"   📅 График: {s['score']}/100 — {s['note'][:30]}")
        
        if c.qualification_details and c.qualification_details.get('salary'):
            s = c.qualification_details['salary']
            lines.append(f"   💰 Зарплата: {s['score']}/100 — {s['note'][:30]}")
        
        if c.qualification_details and c.qualification_details.get('timing'):
            s = c.qualification_details['timing']
            lines.append(f"   ⏰ Сроки: {s['score']}/100 — {s['note'][:30]}")
        
        if c.qualification_details and c.qualification_details.get('tone'):
            s = c.qualification_details['tone']
            lines.append(f"   💬 Тон: {s['score']}/100 — {s['note'][:30]}")
    
    # Ответы кандидата
    if c.answers_schedule:
        lines.append(f"")
        lines.append(f"📝 <b>Ответы кандидата:</b>")
        lines.append(f"📅 График: {c.answers_schedule[:100]}")
    
    if c.answers_salary:
        lines.append(f"💰 Зарплата: {c.answers_salary[:100]}")
    
    if c.answers_timing:
        lines.append(f"⏰ Сроки: {c.answers_timing[:100]}")
    
    # Информация о календаре
    if c.calendar_event_id:
        lines.append(f"")
        lines.append(f"📅 <b>Событие в календаре:</b>")
        lines.append(f"   ID: {c.calendar_event_id}")
        if c.calendar_created_at:
            lines.append(f"   Создано: {c.calendar_created_at.strftime('%d.%m.%Y %H:%M')}")
    
    lines.extend([
        "",
        f"📈 <b>Оценка: {c.score}/100</b>",
        f"📝 {c.explanation or '—'}",
        f"📌 Статус: {c.status}",
        f"📊 Источник: {source_text}",
    ])
    
    if getattr(c, "interview_slot_text", None):
        lines.append(f"📅 Слот: {c.interview_slot_text}")
    
    if c.calendar_event_link:
        lines.append(f"📅 <a href='{c.calendar_event_link}'>Событие в календаре</a>")
    
    return "\n".join(lines)


def build_candidate_keyboard(candidate_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Пригласить", callback_data=f"invite:{candidate_id}")
    kb.button(text="💬 Задать вопрос", callback_data=f"ask:{candidate_id}")
    kb.button(text="💡 Скрипт диалога", callback_data=f"script_btn:{candidate_id}")
    kb.button(text="❌ Пропустить", callback_data=f"skip:{candidate_id}")
    kb.button(text="⭐ В избранное", callback_data=f"fav:{candidate_id}")
    kb.button(text="📅 Собеседование", callback_data=f"st:interview:{candidate_id}")
    kb.button(text="✅ Оффер", callback_data=f"st:offer:{candidate_id}")
    kb.button(text="⛔ Отказ", callback_data=f"st:reject:{candidate_id}")
    kb.button(text="🚫 Не пришёл", callback_data=f"st:noshow:{candidate_id}")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message):
    # Проверяем, есть ли профиль компании
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
    
    if company:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Поиск кандидатов", callback_data="search")],
            [InlineKeyboardButton(text="📋 Мои кандидаты", callback_data="candidates")],
            [InlineKeyboardButton(text="🔧 Фильтры", callback_data="filters_menu")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="🚩 Красные флаги", callback_data="red_flags")],
            [InlineKeyboardButton(text="📈 Нормализация", callback_data="normalization")],
            [InlineKeyboardButton(text="📅 Сортировка", callback_data="sort_menu")],
            [InlineKeyboardButton(text="📤 Экспорт", callback_data="export_menu")],
            [InlineKeyboardButton(text="📊 Аналитика", callback_data="analytics_menu")],
            [InlineKeyboardButton(text="📧 Email", callback_data="email_menu")],
            [InlineKeyboardButton(text="📅 Календарь", callback_data="calendar_menu")],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
        ])
        
        await message.answer(
            f"👋 <b>С возвращением, {company.name_and_industry}!</b>\n\n"
            f"📍 {company.location}\n"
            f"📅 {company.schedule}\n"
            f"💰 {company.salary_range}\n\n"
            f"<b>Основные команды:</b>\n"
            f"/filters — управление фильтрами\n"
            f"/sort — сортировка кандидатов\n"
            f"/export — скачать отчёт (CSV/HTML)\n"
            f"/send_report — отправить отчёт на email\n"
            f"/analytics — аналитика по вакансии\n"
            f"/calendar_setup — настроить Яндекс.Календарь\n"
            f"/set_email — настроить email для отчётов",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        await message.answer(
            "👋 <b>Добро пожаловать в GWork HR Bot!</b>\n\n"
            "Я помогу вам находить реальных кандидатов в 5 источниках:\n"
            "• 🇭 HeadHunter\n"
            "• 🟢 SuperJob\n"
            "• 👨‍💻 Habr Career\n"
            "• 🏢 Работа в России\n"
            "• ✈️ Telegram\n\n"
            "Я умею автоматически общаться с кандидатами и проводить предквалификацию!\n\n"
            "Для начала работы введите /onboarding",
            parse_mode="HTML"
        )


@router.message(Command("onboarding"))
async def cmd_onboarding(message: Message, state: FSMContext):
    # Любой может пройти онбординг, проверка не нужна
    await state.set_state(OnboardingStates.name_and_industry)
    await message.answer(
        "📝 <b>Расскажите о компании:</b>\n\n"
        "Название и сфера деятельности.\n"
        "Например: <i>Салон красоты “Лилия”, услуги маникюра и косметологии</i>",
        parse_mode="HTML"
    )


@router.message(OnboardingStates.name_and_industry)
async def onboarding_name(message: Message, state: FSMContext):
    await state.update_data(name_and_industry=message.text.strip())
    await state.set_state(OnboardingStates.location)
    await message.answer(
        "📍 <b>Где находитесь?</b>\n\n"
        "Город и район.\n"
        "Например: <i>Казань, центр, рядом с метро Площадь Тукая</i>",
        parse_mode="HTML"
    )


@router.message(OnboardingStates.location)
async def onboarding_location(message: Message, state: FSMContext):
    await state.update_data(location=message.text.strip())
    await state.set_state(OnboardingStates.schedule)
    await message.answer(
        "📅 <b>График работы:</b>\n\n"
        "Например: <i>2/2 с 10:00 до 22:00</i>",
        parse_mode="HTML"
    )


@router.message(OnboardingStates.schedule)
async def onboarding_schedule(message: Message, state: FSMContext):
    await state.update_data(schedule=message.text.strip())
    await state.set_state(OnboardingStates.salary_range)
    await message.answer(
        "💰 <b>Зарплатная вилка:</b>\n\n"
        "Например: <i>от 40 000 до 60 000 + бонусы от продаж</i>",
        parse_mode="HTML"
    )


@router.message(OnboardingStates.salary_range)
async def onboarding_salary(message: Message, state: FSMContext):
    await state.update_data(salary_range=message.text.strip())
    await state.set_state(OnboardingStates.tone)
    await message.answer(
        "🎭 <b>Тон общения с кандидатами:</b>\n\n"
        "Напишите: <i>строгий</i>, <i>дружелюбный</i> или <i>нейтральный</i>",
        parse_mode="HTML"
    )


@router.message(OnboardingStates.tone)
async def onboarding_tone(message: Message, state: FSMContext):
    data = await state.get_data()
    tone_value = message.text.strip()
    
    await state.update_data(tone=tone_value)
    await state.clear()  # Завершаем онбординг

    with get_session() as session:
        company = (
            session.query(Company)
            .filter(Company.owner_id == message.from_user.id)
            .one_or_none()
        )
        if company is None:
            company = Company(owner_id=message.from_user.id)
            session.add(company)

        company.name_and_industry = data.get("name_and_industry", "")
        company.location = data.get("location", "")
        company.schedule = data.get("schedule", "")
        company.salary_range = data.get("salary_range", "")
        company.tone = tone_value
        
        # Инициализируем настройки фильтров
        company.filters_settings = {
            'city': True,
            'salary': True,
            'experience': True,
            'skills': True
        }
        
        session.commit()

    await message.answer(
        "✅ <b>Профиль компании сохранён!</b>\n\n"
        "Теперь можно создать вакансию командой /new_job\n"
        "И настроить фильтры: /filters\n"
        "Для получения email-отчётов: /set_email\n"
        "Для настройки календаря: /calendar_setup",
        parse_mode="HTML"
    )


@router.message(Command("new_job"))
async def cmd_new_job(message: Message, state: FSMContext):
    with get_session() as session:
        company = (
            session.query(Company)
            .filter(Company.owner_id == message.from_user.id)
            .one_or_none()
        )
        if company is None:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return

    await state.set_state(VacancyStates.role)
    await message.answer(
        "🔍 <b>Кого ищем?</b>\n\n"
        "Опишите роль:\n"
        "Например: <i>Администратор в салон красоты</i>",
        parse_mode="HTML"
    )


@router.message(VacancyStates.role)
async def vacancy_role(message: Message, state: FSMContext):
    await state.update_data(role=message.text.strip())
    await state.set_state(VacancyStates.city)
    await message.answer(
        "🌆 <b>Город поиска:</b>\n\n"
        "Например: <i>Москва</i> или <i>Санкт-Петербург</i>",
        parse_mode="HTML"
    )


@router.message(VacancyStates.city)
async def vacancy_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await state.set_state(VacancyStates.experience_required)
    await message.answer(
        "💼 <b>Опыт обязателен?</b>\n\n"
        "Ответьте: <i>да</i> или <i>нет</i>",
        parse_mode="HTML"
    )


@router.message(VacancyStates.experience_required)
async def vacancy_experience(message: Message, state: FSMContext):
    txt = message.text.strip().lower()
    exp_required = txt.startswith("д")
    await state.update_data(experience_required=exp_required)
    await state.set_state(VacancyStates.schedule)
    await message.answer(
        "📅 <b>График работы:</b>\n\n"
        "Например: <i>2/2</i>, <i>5/2</i> или <i>гибкий</i>",
        parse_mode="HTML"
    )


@router.message(VacancyStates.schedule)
async def vacancy_schedule(message: Message, state: FSMContext):
    await state.update_data(schedule=message.text.strip())
    await state.set_state(VacancyStates.salary_from)
    await message.answer(
        "💰 <b>Зарплата ОТ:</b>\n\n"
        "Введите число или '-' для пропуска",
        parse_mode="HTML"
    )


@router.message(VacancyStates.salary_from)
async def vacancy_salary_from(message: Message, state: FSMContext):
    txt = message.text.strip()
    salary_from = None
    if txt != "-":
        try:
            salary_from = int(txt)
        except ValueError:
            await message.answer("❌ Введите число или '-'")
            return
    await state.update_data(salary_from=salary_from)
    await state.set_state(VacancyStates.salary_to)
    await message.answer(
        "💰 <b>Зарплата ДО:</b>\n\n"
        "Введите число или '-' для пропуска",
        parse_mode="HTML"
    )


@router.message(VacancyStates.salary_to)
async def vacancy_salary_to(message: Message, state: FSMContext):
    txt = message.text.strip()
    salary_to = None
    if txt != "-":
        try:
            salary_to = int(txt)
        except ValueError:
            await message.answer("❌ Введите число или '-'")
            return
    await state.update_data(salary_to=salary_to)
    await state.set_state(VacancyStates.start_when)
    await message.answer(
        "⏰ <b>Когда нужен сотрудник?</b>\n\n"
        "Например: <i>как можно скорее</i> или <i>через месяц</i>",
        parse_mode="HTML"
    )


@router.message(VacancyStates.start_when)
async def vacancy_start_when(message: Message, state: FSMContext):
    await state.update_data(start_when=message.text.strip())
    await state.set_state(VacancyStates.must_have)
    await message.answer(
        "⚠️ <b>Критичные требования (можно пропустить, введя '-'):</b>\n\n"
        "Через запятую. Например: <i>грамотная речь, 1С, продажи</i>\n"
        "Или введите '-' чтобы пропустить",
        parse_mode="HTML"
    )


@router.message(VacancyStates.must_have)
async def vacancy_must_have(message: Message, state: FSMContext):
    data = await state.get_data()
    must_have_text = message.text.strip()
    
    # Проверяем, не хочет ли пользователь пропустить
    if must_have_text == "-" or must_have_text.lower() in ["нет", "нету", "пропустить", "skip"]:
        must_have_text = "-"  # Ставим прочерк для обозначения "нет требований"
        await message.answer("✅ Критичные требования пропущены")
    else:
        await message.answer(f"✅ Критичные требования сохранены: {must_have_text}")
    
    await state.clear()

    with get_session() as session:
        company = (
            session.query(Company)
            .filter(Company.owner_id == message.from_user.id)
            .one()
        )
        vacancy = Vacancy(
            company_id=company.id,
            role=data["role"],
            city=data["city"],
            experience_required=data["experience_required"],
            schedule=data["schedule"],
            salary_from=data.get("salary_from"),
            salary_to=data.get("salary_to"),
            start_when=data["start_when"],
            must_have=must_have_text,
        )
        session.add(vacancy)
        session.flush()
        vacancy_id = vacancy.id

        desc_lines = [
            f"{company.name_and_industry} ({company.location}) ищет {vacancy.role}.",
            f"📅 График: {vacancy.schedule}.",
        ]
        if vacancy.salary_from or vacancy.salary_to:
            salary_from = vacancy.salary_from or "-"
            salary_to = vacancy.salary_to or "-"
            desc_lines.append(f"💰 Зарплата: от {salary_from} до {salary_to} руб.")
        
        if vacancy.must_have and vacancy.must_have != "-":
            desc_lines.append(f"⚠️ Требования: {vacancy.must_have}.")
        
        desc_lines.append(f"⏰ Запуск: {vacancy.start_when}.")
        vacancy_text = "\n".join(desc_lines)

        template = VacancyTemplate(
            company_id=company.id,
            title=f"{vacancy.role} · {vacancy.city}",
            role=vacancy.role,
            city=vacancy.city,
            schedule=vacancy.schedule,
            salary_from=vacancy.salary_from,
            salary_to=vacancy.salary_to,
            must_have=vacancy.must_have,
            description=vacancy_text,
        )
        session.add(template)
        session.commit()
        
        # Обновляем дату последнего поиска
        vacancy.last_search_at = datetime.now()
        session.commit()

    await message.answer(
        f"✅ <b>Вакансия создана!</b>\n\n"
        f"Ищу <b>реальных кандидатов</b> в 5 источниках...",
        parse_mode="HTML"
    )

    await gather_real_candidates(vacancy_id)

    await message.answer(
        "✅ <b>Поиск завершён!</b>\n\n"
        "Посмотреть кандидатов: /candidates\n"
        "Настроить фильтры: /filters\n"
        "Статистика красных флагов: /red_flags\n"
        "Статистика нормализации: /stats_normalized\n"
        "Сортировка: /sort\n"
        "Скачать отчёт: /export\n"
        "Отправить на email: /send_report\n"
        "Аналитика: /analytics\n"
        "Email отчёты: /set_email\n"
        "Календарь: /calendar_setup",
        parse_mode="HTML"
    )


async def gather_real_candidates(vacancy_id: int) -> None:
    """Сбор реальных кандидатов из ВСЕХ источников с применением фильтров и нормализацией"""
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.id == vacancy_id).one()
        company = session.query(Company).filter(Company.id == vacancy.company_id).one()

        candidates: List[Candidate] = []

        # 1) HeadHunter
        logger.info(f"🔍 Поиск резюме в HeadHunter: {vacancy.role} в {vacancy.city}")
        try:
            hh_candidates = await search_hh_resumes(vacancy.role, vacancy.city, limit=5)
            for cand in hh_candidates:
                c = Candidate(
                    vacancy_id=vacancy.id,
                    name_or_nick=cand["name"],
                    contact=cand.get("contact", ""),
                    city=cand["city"],
                    experience_text=cand["experience"],
                    skills_text=", ".join(cand["skills"]),
                    source="hh",
                    source_link=cand["url"],
                    raw_text=cand["about"],
                    status=CandidateStatus.FOUND.value,
                    dialog_step=0,
                    first_seen_at=datetime.now(),
                    is_new=True
                )
                
                # Извлекаем дополнительные данные для фильтров и нормализации
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                # Нормализация опыта
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                # Нормализация навыков
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                # Извлечение города из текста
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                # Извлечение ключевых слов
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                # Применяем жёсткие фильтры с учётом настроек компании
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
        except Exception as e:
            logger.error(f"❌ Ошибка HeadHunter: {e}")

        # 2) SuperJob
        logger.info(f"🔍 Поиск кандидатов в SuperJob: {vacancy.role} в {vacancy.city}")
        try:
            sj_candidates = await search_superjob_real_candidates(vacancy.role, vacancy.city, limit=5)
            for cand in sj_candidates:
                c = Candidate(
                    vacancy_id=vacancy.id,
                    name_or_nick=cand["name"],
                    contact=cand.get("contact", ""),
                    city=cand["city"],
                    experience_text=cand["experience"],
                    skills_text=", ".join(cand["skills"]),
                    source="superjob",
                    source_link=cand["url"],
                    raw_text=cand["about"],
                    status=CandidateStatus.FOUND.value,
                    dialog_step=0,
                    first_seen_at=datetime.now(),
                    is_new=True
                )
                
                # Извлекаем дополнительные данные для фильтров и нормализации
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                # Нормализация опыта
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                # Нормализация навыков
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                # Извлечение города из текста
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                # Извлечение ключевых слов
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                # Применяем жёсткие фильтры с учётом настроек компании
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
        except Exception as e:
            logger.error(f"❌ Ошибка SuperJob: {e}")

        # 3) Habr Career
        logger.info(f"🔍 Поиск кандидатов в Habr Career: {vacancy.role} в {vacancy.city}")
        try:
            habr_candidates = await search_habr_candidates(vacancy.role, vacancy.city, limit=5)
            for cand in habr_candidates:
                c = Candidate(
                    vacancy_id=vacancy.id,
                    name_or_nick=cand["name"],
                    contact=cand.get("contact", ""),
                    city=cand["city"],
                    experience_text=cand["experience"],
                    skills_text=", ".join(cand["skills"]),
                    source="habr",
                    source_link=cand["url"],
                    raw_text=cand["about"],
                    status=CandidateStatus.FOUND.value,
                    dialog_step=0,
                    first_seen_at=datetime.now(),
                    is_new=True
                )
                
                # Извлекаем дополнительные данные для фильтров и нормализации
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                # Нормализация опыта
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                # Нормализация навыков
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                # Извлечение города из текста
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                # Извлечение ключевых слов
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                # Применяем жёсткие фильтры с учётом настроек компании
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
        except Exception as e:
            logger.error(f"❌ Ошибка Habr: {e}")

        # 4) Trudvsem
        logger.info(f"🔍 Поиск кандидатов в Trudvsem: {vacancy.role} в {vacancy.city}")
        try:
            trudvsem_candidates = await search_trudvsem_candidates(vacancy.role, vacancy.city, limit=5)
            for cand in trudvsem_candidates:
                c = Candidate(
                    vacancy_id=vacancy.id,
                    name_or_nick=cand["name"],
                    contact=cand.get("contact", ""),
                    city=cand["city"],
                    experience_text=cand["experience"],
                    skills_text=", ".join(cand["skills"]),
                    source="trudvsem",
                    source_link=cand["url"],
                    raw_text=cand["about"],
                    status=CandidateStatus.FOUND.value,
                    dialog_step=0,
                    first_seen_at=datetime.now(),
                    is_new=True
                )
                
                # Извлекаем дополнительные данные для фильтров и нормализации
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                # Нормализация опыта
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                # Нормализация навыков
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                # Извлечение города из текста
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                # Извлечение ключевых слов
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                # Применяем жёсткие фильтры с учётом настроек компании
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
        except Exception as e:
            logger.error(f"❌ Ошибка Trudvsem: {e}")

        # 5) Telegram
        logger.info(f"🔍 Поиск кандидатов в Telegram: {vacancy.role} в {vacancy.city}")
        try:
            tg_candidates = await telegram_parser.search_candidates(vacancy.role, vacancy.city, limit=5)
            for cand in tg_candidates:
                c = Candidate(
                    vacancy_id=vacancy.id,
                    name_or_nick=cand["name"],
                    contact=cand.get("contact", ""),
                    city=cand["city"],
                    experience_text=cand["experience"],
                    skills_text=", ".join(cand["skills"]),
                    source="telegram",
                    source_link=cand["url"],
                    raw_text=cand["about"],
                    status=CandidateStatus.FOUND.value,
                    dialog_step=0,
                    first_seen_at=datetime.now(),
                    is_new=True
                )
                
                # Извлекаем дополнительные данные для фильтров и нормализации
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                # Нормализация опыта
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                # Нормализация навыков
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                # Извлечение города из текста
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                # Извлечение ключевых слов
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                # Применяем жёсткие фильтры с учётом настроек компании
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
        except Exception as e:
            logger.error(f"❌ Ошибка Telegram: {e}")

        session.commit()
        logger.info(f"✅ ВСЕГО найдено кандидатов: {len(candidates)}")
        
        # Считаем сколько прошло фильтры
        passed_count = len([c for c in candidates if c.status != CandidateStatus.REJECTED.value])
        logger.info(f"✅ Прошли фильтры: {passed_count}")
        logger.info(f"❌ Отсеяно: {len(candidates) - passed_count}")

        # Скоринг через DeepSeek (только для прошедших фильтры)
        filtered_candidates = [c for c in candidates if c.status != CandidateStatus.REJECTED.value]
        if filtered_candidates:
            vacancy_desc = vacancy_to_description(vacancy, company)
            payload = [
                {
                    "id": c.id,
                    "name": c.name_or_nick,
                    "city": c.city,
                    "text": c.raw_text,
                    "skills": c.skills_text,
                    "source": c.source,
                }
                for c in filtered_candidates
            ]

            try:
                scores = deepseek.score_candidates(vacancy_desc, payload)
                scores_by_id = {int(s["id"]): s for s in scores if "id" in s}

                for c in filtered_candidates:
                    result = scores_by_id.get(c.id)
                    if result:
                        c.score = int(result.get("score", 0))
                        c.explanation = str(result.get("explanation", ""))
                    else:
                        base = 50
                        if vacancy.city.lower() in c.city.lower():
                            base += 15
                        c.score = max(0, min(100, base))
                        c.explanation = "эвристическая оценка по городу"
                    
                    # Применяем штраф за красные флаги
                    if c.red_flags:
                        penalty = get_red_flags_score(c.raw_text)
                        if penalty > 0:
                            old_score = c.score
                            c.score = max(0, c.score - penalty)
                            if c.explanation:
                                c.explanation += f" | Штраф за красные флаги: -{penalty} баллов"
                            else:
                                c.explanation = f"Штраф за красные флаги: -{penalty} баллов"
                            logger.info(f"Кандидат {c.name_or_nick}: скор снижен с {old_score} до {c.score} (штраф {penalty})")
                    
                    if c.score >= 80:
                        c.status = CandidateStatus.FILTERED.value
                
                session.commit()
                logger.info("✅ Скоринг завершён")
            except Exception as e:
                logger.error(f"❌ Ошибка скоринга: {e}")


def group_candidates_for_report(vacancy_id: int):
    with get_session() as session:
        all_cands = (
            session.query(Candidate)
            .filter(Candidate.vacancy_id == vacancy_id)
            .order_by(Candidate.score.desc())
            .all()
        )
    top = [c for c in all_cands if c.score >= 80]
    mid = [c for c in all_cands if 60 <= c.score < 80]
    rest = [c for c in all_cands if c.score < 60]
    rejected = [c for c in all_cands if c.status == CandidateStatus.REJECTED.value]
    clarify = [c for c in all_cands if c.status == CandidateStatus.CLARIFY.value]
    qualified = [c for c in all_cands if c.status == CandidateStatus.QUALIFIED.value]
    return all_cands, top, mid, rest, rejected, clarify, qualified


def candidates_page(candidates: List[Candidate], page: int, per_page: int = 5):
    start = page * per_page
    end = start + per_page
    return candidates[start:end], len(candidates) > end


async def _send_candidates_page(
    target: Message | CallbackQuery,
    user_id: int,
    page: int,
    per_page: int = 5,
) -> None:
    """Отправить одну страницу кандидатов"""
    with get_session() as session:
        vacancies = (
            session.query(Vacancy)
            .join(Company, Vacancy.company_id == Company.id)
            .filter(Company.owner_id == user_id)
            .order_by(Vacancy.created_at.desc())
            .all()
        )
    if not vacancies:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет вакансий", show_alert=True)
        else:
            await target.answer("📭 Нет вакансий. Создайте: /new_job")
        return

    vacancy = vacancies[0]
    all_cands, top, mid, rest, rejected, clarify, qualified = group_candidates_for_report(vacancy.id)
    if not all_cands:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет кандидатов", show_alert=True)
        else:
            await target.answer("❌ Кандидаты не найдены")
        return

    total_pages = (len(all_cands) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    page_cands, has_next = candidates_page(all_cands, page=page, per_page=per_page)

    summary = (
        f"📊 <b>Вакансия: {vacancy.role} ({vacancy.city})</b>\n\n"
        f"Найдено кандидатов: {len(all_cands)}\n"
        f"🔥 Отлично (80+): {len(top)}\n"
        f"🟢 Хорошо (60-79): {len(mid)}\n"
        f"❌ Отсеяно фильтрами: {len(rejected)}\n"
        f"⚠️ Требуют уточнения: {len(clarify)}\n"
        f"✅ Прошли предквалификацию: {len(qualified)}\n\n"
        f"📄 Страница {page + 1} из {total_pages}"
    )
    
    if isinstance(target, CallbackQuery):
        await target.answer()
        await target.message.answer(summary, parse_mode="HTML")
    else:
        await target.answer(summary, parse_mode="HTML")

    for c in page_cands:
        # Показываем только не отсеянных кандидатов
        if c.status != CandidateStatus.REJECTED.value:
            msg = target.message if isinstance(target, CallbackQuery) else target
            await msg.answer(
                build_candidate_card_text(c), 
                parse_mode="HTML",
                reply_markup=build_candidate_keyboard(c.id),
                disable_web_page_preview=False
            )

    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="◀ Пред.", callback_data=f"cand:{page - 1}")
    if has_next:
        kb.button(text="След. ▶", callback_data=f"cand:{page + 1}")
    if kb.buttons:
        msg = target.message if isinstance(target, CallbackQuery) else target
        await msg.answer("📌 Навигация:", reply_markup=kb.as_markup())


@router.message(Command("candidates"))
async def cmd_candidates(message: Message):
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    await _send_candidates_page(message, message.from_user.id, 0)


@router.callback_query(F.data.startswith("cand:"))
async def cb_candidates_page(callback: CallbackQuery):
    try:
        page = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    await _send_candidates_page(callback, callback.from_user.id, page)


# ===== ФИЛЬТРЫ И СТАТИСТИКА =====

@router.message(Command("filters"))
async def cmd_filters(message: Message):
    """Управление фильтрами"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        
        # Проверяем, есть ли компания
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        # Получаем настройки фильтров
        filters_settings = getattr(company, 'filters_settings', {})
        if not filters_settings:
            filters_settings = {
                'city': True,
                'salary': True,
                'experience': True,
                'skills': True
            }
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Включить все", callback_data="filters:on_all")
    kb.button(text="🔍 Выключить все", callback_data="filters:off_all")
    kb.button(text=f"🏙️ Город: {'✅' if filters_settings.get('city', True) else '❌'}", callback_data="filters:city")
    kb.button(text=f"💰 Зарплата: {'✅' if filters_settings.get('salary', True) else '❌'}", callback_data="filters:salary")
    kb.button(text=f"💼 Опыт: {'✅' if filters_settings.get('experience', True) else '❌'}", callback_data="filters:experience")
    kb.button(text=f"📋 Требования: {'✅' if filters_settings.get('skills', True) else '❌'}", callback_data="filters:skills")
    kb.button(text="📊 Статистика отсева", callback_data="filters:stats")
    kb.button(text="🚩 Красные флаги", callback_data="red_flags")
    kb.button(text="📈 Нормализация", callback_data="normalization")
    kb.button(text="🗑️ Архив отсеянных", callback_data="filters:archive")
    kb.adjust(2, 2, 2, 2, 1, 1)
    
    text = (
        f"🔧 <b>Управление фильтрами</b> — компания: {company.name_and_industry}\n\n"
        "Жёсткие фильтры автоматически отсеивают неподходящих кандидатов:\n"
        "• 🏙️ <b>Город</b> — кандидат должен быть в том же городе\n"
        "• 💰 <b>Зарплата</b> — ожидания не выше вилки +20%\n"
        "• 💼 <b>Опыт</b> — если требуется, должен быть опыт\n"
        "• 📋 <b>Требования</b> — наличие критичных навыков\n"
        "• 🚩 <b>Красные флаги</b> — отсев подозрительных объявлений\n\n"
        "Текущий статус:\n"
        f"🏙️ Город: {'✅ включён' if filters_settings.get('city', True) else '❌ выключен'}\n"
        f"💰 Зарплата: {'✅ включён' if filters_settings.get('salary', True) else '❌ выключен'}\n"
        f"💼 Опыт: {'✅ включён' if filters_settings.get('experience', True) else '❌ выключен'}\n"
        f"📋 Требования: {'✅ включён' if filters_settings.get('skills', True) else '❌ выключен'}"
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("filters:"))
async def cb_filters(callback: CallbackQuery):
    """Обработка кнопок фильтров"""
    action = callback.data.split(":")[1]
    
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == callback.from_user.id).first()
        
        # Проверяем, есть ли компания
        if not company:
            await callback.answer("❌ Компания не найдена. Пройдите онбординг: /onboarding", show_alert=True)
            return
        
        # Получаем или создаём настройки фильтров
        filters_settings = getattr(company, 'filters_settings', {})
        if not filters_settings:
            filters_settings = {
                'city': True,
                'salary': True,
                'experience': True,
                'skills': True
            }
        
        if action == "on_all":
            filters_settings = {k: True for k in filters_settings}
            await callback.answer("✅ Все фильтры включены")
        
        elif action == "off_all":
            filters_settings = {k: False for k in filters_settings}
            await callback.answer("❌ Все фильтры выключены")
        
        elif action == "city":
            filters_settings['city'] = not filters_settings.get('city', True)
            await callback.answer(f"🏙️ Фильтр по городу: {'включён' if filters_settings['city'] else 'выключен'}")
        
        elif action == "salary":
            filters_settings['salary'] = not filters_settings.get('salary', True)
            await callback.answer(f"💰 Фильтр по зарплате: {'включён' if filters_settings['salary'] else 'выключен'}")
        
        elif action == "experience":
            filters_settings['experience'] = not filters_settings.get('experience', True)
            await callback.answer(f"💼 Фильтр по опыту: {'включён' if filters_settings['experience'] else 'выключен'}")
        
        elif action == "skills":
            filters_settings['skills'] = not filters_settings.get('skills', True)
            await callback.answer(f"📋 Фильтр по требованиям: {'включён' if filters_settings['skills'] else 'выключен'}")
        
        elif action == "stats":
            # Статистика отсева
            vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
            if vacancy:
                total = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).count()
                rejected = session.query(Candidate).filter(
                    Candidate.vacancy_id == vacancy.id,
                    Candidate.status == CandidateStatus.REJECTED.value
                ).count()
                
                with_red_flags = session.query(Candidate).filter(
                    Candidate.vacancy_id == vacancy.id,
                    Candidate.red_flags.isnot(None)
                ).count()
                
                reasons = session.query(Candidate.rejection_reason, func.count(Candidate.id)).filter(
                    Candidate.vacancy_id == vacancy.id,
                    Candidate.rejection_reason.isnot(None)
                ).group_by(Candidate.rejection_reason).all()
                
                text = f"📊 <b>Статистика отсева</b>\n\n"
                text += f"Вакансия: {vacancy.role}\n"
                text += f"Всего кандидатов: {total}\n"
                text += f"Отсеяно фильтрами: {rejected}\n"
                text += f"С красными флагами: {with_red_flags}\n\n"
                
                if reasons:
                    text += "Причины отсева:\n"
                    for reason, count in reasons[:5]:
                        text += f"• {reason}: {count}\n"
                else:
                    text += "Нет данных об отсеве"
                
                await callback.message.answer(text, parse_mode="HTML")
            else:
                await callback.message.answer("📭 Нет вакансий для статистики")
            await callback.answer()
            return
        
        elif action == "archive":
            # Просмотр архива отсеянных
            vacancy = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).first()
            if vacancy:
                rejected = session.query(Candidate).filter(
                    Candidate.vacancy_id == vacancy.id,
                    Candidate.status == CandidateStatus.REJECTED.value
                ).order_by(Candidate.created_at.desc()).limit(10).all()
                
                if rejected:
                    text = "🗑️ <b>Последние отсеянные кандидаты:</b>\n\n"
                    for c in rejected:
                        text += f"• {c.name_or_nick}\n"
                        if c.rejection_reason:
                            text += f"  Причина: {c.rejection_reason}\n"
                        if c.red_flags:
                            text += f"  🚩 Красные флаги: {len(c.red_flags)}\n"
                    await callback.message.answer(text, parse_mode="HTML")
                else:
                    await callback.message.answer("📭 Нет отсеянных кандидатов")
            else:
                await callback.message.answer("📭 Нет вакансий")
            await callback.answer()
            return
        
        # Сохраняем настройки
        company.filters_settings = filters_settings
        session.commit()
        
        # Обновляем сообщение
        await callback.message.delete()
        await cmd_filters(callback.message)
        await callback.answer()


@router.callback_query(lambda c: c.data == "filters_menu")
async def cb_filters_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_filters(callback.message)


@router.message(Command("red_flags"))
async def cmd_red_flags(message: Message):
    """Статистика красных флагов"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        
        if not vacancies:
            await message.answer("📭 Нет вакансий")
            return
        
        vacancy = vacancies[0]
        
        # Считаем статистику
        total = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).count()
        with_flags = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.red_flags.isnot(None)
        ).count()
        
        # Получаем частоту разных флагов
        flag_stats = {}
        candidates_with_flags = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.red_flags.isnot(None)
        ).all()
        
        for c in candidates_with_flags:
            if c.red_flags:
                for flag in c.red_flags:
                    category = flag.split(":")[0]
                    flag_stats[category] = flag_stats.get(category, 0) + 1
        
        text = f"📊 <b>Статистика красных флагов</b>\n\n"
        text += f"Вакансия: {vacancy.role}\n"
        text += f"Всего кандидатов: {total}\n"
        text += f"С красными флагами: {with_flags}"
        if total > 0:
            text += f" ({with_flags/total*100:.1f}%)\n\n"
        else:
            text += "\n\n"
        
        if flag_stats:
            text += "По категориям:\n"
            for category, count in sorted(flag_stats.items(), key=lambda x: x[1], reverse=True):
                text += f"• {category}: {count}\n"
        else:
            text += "Нет данных о красных флагах"
        
        await message.answer(text, parse_mode="HTML")


@router.callback_query(lambda c: c.data == "red_flags")
async def cb_red_flags(callback: CallbackQuery):
    await callback.answer()
    await cmd_red_flags(callback.message)


@router.message(Command("stats_normalized"))
async def cmd_stats_normalized(message: Message):
    """Статистика нормализованных данных"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        
        if not vacancies:
            await message.answer("📭 Нет вакансий")
            return
        
        vacancy = vacancies[0]
        
        # Считаем статистику
        total = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).count()
        
        with_exp_years = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.experience_years.isnot(None)
        ).count()
        
        with_norm_skills = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.extracted_skills.isnot(None)
        ).count()
        
        with_keywords = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.extracted_keywords.isnot(None)
        ).count()
        
        text = f"📊 <b>Статистика нормализации</b>\n\n"
        text += f"Вакансия: {vacancy.role}\n"
        text += f"Всего кандидатов: {total}\n\n"
        
        if total > 0:
            text += f"📈 С нормализованным опытом: {with_exp_years} ({with_exp_years/total*100:.1f}%)\n"
            text += f"🛠️ С нормализованными навыками: {with_norm_skills} ({with_norm_skills/total*100:.1f}%)\n"
            text += f"🔑 С извлечёнными ключевыми словами: {with_keywords} ({with_keywords/total*100:.1f}%)\n"
        else:
            text += "Нет данных для статистики"
        
        await message.answer(text, parse_mode="HTML")


@router.callback_query(lambda c: c.data == "normalization")
async def cb_normalization(callback: CallbackQuery):
    await callback.answer()
    await cmd_stats_normalized(callback.message)


@router.message(Command("rejected"))
async def cmd_rejected(message: Message):
    """Просмотр отсеянных кандидатов"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        
        if not vacancies:
            await message.answer("📭 Нет вакансий")
            return
        
        vacancy = vacancies[0]
        rejected = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.status == CandidateStatus.REJECTED.value
        ).order_by(Candidate.created_at.desc()).all()
        
        if not rejected:
            await message.answer(f"✅ Нет отсеянных кандидатов по вакансии {vacancy.role}")
            return
        
        text = f"🗑️ <b>Отсеянные кандидаты по вакансии {vacancy.role}:</b>\n\n"
        for c in rejected[:10]:
            text += f"• {c.name_or_nick}\n"
            if c.rejection_reason:
                text += f"  Причина: {c.rejection_reason}\n"
            if c.red_flags:
                text += f"  🚩 Флаги: {len(c.red_flags)}\n"
        if len(rejected) > 10:
            text += f"\n... и ещё {len(rejected) - 10}"
        
        await message.answer(text, parse_mode="HTML")


# ===== ДАТА ПУБЛИКАЦИИ И СОРТИРОВКА =====

@router.message(Command("sort"))
async def cmd_sort(message: Message):
    """Сортировка кандидатов"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    if not candidates:
        await message.answer("📭 Нет кандидатов для сортировки")
        return
    
    # Создаём клавиатуру с вариантами сортировки
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 По оценке", callback_data="sort_candidates:score")
    kb.button(text="📅 По дате (новые)", callback_data="sort_candidates:date")
    kb.button(text="👤 По имени", callback_data="sort_candidates:name")
    kb.button(text="📍 По городу", callback_data="sort_candidates:city")
    kb.button(text="🔥 Только 80+", callback_data="sort_candidates:top")
    kb.button(text="🆕 За последние 3 дня", callback_data="sort_candidates:new")
    kb.adjust(2, 2, 2)
    
    await message.answer(
        f"📊 <b>Сортировка кандидатов</b>\n\n"
        f"Всего кандидатов: {len(candidates)}\n"
        f"Новых за сегодня: {sum(1 for c in candidates if c.created_at and (datetime.now() - c.created_at).days < 1)}\n\n"
        "Выберите вариант сортировки:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("sort_candidates:"))
async def cb_sort_candidates(callback: CallbackQuery):
    """Обработка сортировки кандидатов"""
    sort_by = callback.data.split(":")[1]
    
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == callback.from_user.id).first()
        if not company:
            await callback.answer("❌ Компания не найдена", show_alert=True)
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await callback.answer("📭 Нет вакансий", show_alert=True)
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    from export_utils import sort_candidates, filter_by_date
    
    # Применяем фильтры и сортировку
    if sort_by == 'new':
        filtered = filter_by_date(candidates, days=3)
        sorted_cands = sort_candidates(filtered, sort_by='date')
        title = "🆕 За последние 3 дня"
    elif sort_by == 'top':
        filtered = [c for c in candidates if c.score >= 80]
        sorted_cands = sort_candidates(filtered, sort_by='score')
        title = "🔥 Топ-кандидаты (80+)"
    else:
        sorted_cands = sort_candidates(candidates, sort_by=sort_by)
        title = {
            'score': "📊 По оценке",
            'date': "📅 По дате",
            'name': "👤 По имени",
            'city': "📍 По городу"
        }.get(sort_by, "📊 Результаты")
    
    if not sorted_cands:
        await callback.message.answer(f"{title}: кандидаты не найдены")
        await callback.answer()
        return
    
    text = f"{title}\n\n"
    for i, c in enumerate(sorted_cands[:10], 1):
        days_old = (datetime.now() - c.created_at).days if c.created_at else 0
        new_tag = " 🆕" if days_old < 1 else ""
        
        text += f"{i}. <b>{c.name_or_nick}</b> — {c.score}/100"
        text += f"{new_tag}\n"
        text += f"   📍 {c.city} | 📅 {c.created_at.strftime('%d.%m.%Y') if c.created_at else 'неизв.'}\n"
    
    if len(sorted_cands) > 10:
        text += f"\n... и ещё {len(sorted_cands) - 10} кандидатов"
    
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "sort_menu")
async def cb_sort_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_sort(callback.message)


@router.message(Command("report_stats"))
async def cmd_report_stats(message: Message):
    """Статистика по кандидатам с датами"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    # Статистика по датам
    now = datetime.now()
    today = sum(1 for c in candidates if c.created_at and c.created_at.date() == now.date())
    yesterday = sum(1 for c in candidates if c.created_at and c.created_at.date() == (now - timedelta(days=1)).date())
    this_week = sum(1 for c in candidates if c.created_at and (now - c.created_at).days < 7)
    this_month = sum(1 for c in candidates if c.created_at and (now - c.created_at).days < 30)
    
    text = (
        f"📊 <b>Статистика по вакансии {vacancy.role}</b>\n\n"
        f"Всего кандидатов: {len(candidates)}\n\n"
        f"<b>По датам:</b>\n"
        f"• Сегодня: {today}\n"
        f"• Вчера: {yesterday}\n"
        f"• За неделю: {this_week}\n"
        f"• За месяц: {this_month}\n\n"
        f"<b>По статусам:</b>\n"
        f"• 🔍 Найдено: {sum(1 for c in candidates if c.status == 'found')}\n"
        f"• 🟢 Подходят: {sum(1 for c in candidates if c.status == 'filtered')}\n"
        f"• 📩 Приглашены: {sum(1 for c in candidates if c.status == 'invited')}\n"
        f"• 💬 Отвечают: {sum(1 for c in candidates if c.status == 'answering')}\n"
        f"• ✅ Квалифицированы: {sum(1 for c in candidates if c.status == 'qualified')}\n"
        f"• 📅 Собеседование: {sum(1 for c in candidates if c.status == 'interview')}\n"
        f"• ❌ Отсеяно: {sum(1 for c in candidates if c.status == 'rejected')}\n"
    )
    
    await message.answer(text, parse_mode="HTML")


# ===== ЭКСПОРТ ОТЧЁТОВ =====

@router.message(Command("export"))
async def cmd_export(message: Message):
    """Экспорт отчёта по кандидатам (только CSV/HTML, для email используйте /send_report)"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    from export_utils import generate_csv_report, generate_html_report
    
    # Только CSV и HTML, без кнопки email
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 CSV", callback_data="export:csv")
    kb.button(text="📊 HTML", callback_data="export:html")
    kb.adjust(2)
    
    await message.answer(
        f"📊 <b>Экспорт отчёта по вакансии {vacancy.role}</b>\n\n"
        f"Всего кандидатов: {len(candidates)}\n\n"
        f"Для отправки на email используйте команду:\n"
        f"<code>/send_report</code> — отправить отчёт на ваш email\n\n"
        f"Или выберите формат для скачивания:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("export:"))
async def cb_export(callback: CallbackQuery, state: FSMContext):
    """Обработка экспорта (только CSV и HTML)"""
    export_format = callback.data.split(":")[1]
    
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == callback.from_user.id).first()
        if not company:
            await callback.answer("❌ Компания не найдена", show_alert=True)
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await callback.answer("📭 Нет вакансий", show_alert=True)
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    from export_utils import generate_csv_report, generate_html_report
    import tempfile
    import os
    
    if export_format == 'csv':
        csv_data = generate_csv_report(candidates, vacancy)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_data)
            temp_file = f.name
        
        document = FSInputFile(temp_file, filename=f"candidates_{vacancy.role}_{datetime.now().strftime('%Y%m%d')}.csv")
        await callback.message.answer_document(document, caption=f"📊 Отчёт по вакансии {vacancy.role}")
        
        os.unlink(temp_file)
        
    elif export_format == 'html':
        html_data = generate_html_report(candidates, vacancy, company)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(html_data)
            temp_file = f.name
        
        document = FSInputFile(temp_file, filename=f"report_{vacancy.role}_{datetime.now().strftime('%Y%m%d')}.html")
        await callback.message.answer_document(document, caption=f"📊 Отчёт по вакансии {vacancy.role}")
        
        os.unlink(temp_file)
    
    await callback.answer()


@router.callback_query(lambda c: c.data == "export_menu")
async def cb_export_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_export(callback.message)


# ===== АНАЛИТИКА =====

@router.message(Command("analytics"))
async def cmd_analytics(message: Message):
    """Подробная аналитика по вакансии"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
    
    from analytics import format_analytics_report
    report = format_analytics_report(vacancy.id)
    
    await message.answer(report, parse_mode="HTML")


@router.message(Command("sources"))
async def cmd_sources(message: Message):
    """Статистика по источникам кандидатов"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
    
    from analytics import AnalyticsService
    sources = AnalyticsService.get_source_stats(vacancy.id)
    total = sum(sources.values())
    
    text = f"📊 <b>Статистика по источникам</b>\n\n"
    text += f"Всего кандидатов: {total}\n\n"
    
    emoji_map = {
        'hh': '🇭',
        'superjob': '🟢',
        'habr': '👨‍💻',
        'trudvsem': '🏢',
        'telegram': '✈️'
    }
    
    for source, count in sorted(sources.items(), key=lambda x: x[1], reverse=True):
        emoji = emoji_map.get(source, '📋')
        percentage = round((count / total) * 100, 1) if total > 0 else 0
        text += f"{emoji} <b>{source}</b>: {count} ({percentage}%)\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("conversion"))
async def cmd_conversion(message: Message):
    """Конверсия по этапам воронки"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
    
    from analytics import AnalyticsService
    stats = AnalyticsService.get_pipeline_stats(vacancy.id)
    conversion = AnalyticsService.get_conversion_rates(vacancy.id)
    
    total = stats.get(CandidateStatus.FOUND.value, 0)
    filtered = stats.get(CandidateStatus.FILTERED.value, 0)
    invited = stats.get(CandidateStatus.INVITED.value, 0)
    answering = stats.get(CandidateStatus.ANSWERING.value, 0)
    qualified = stats.get(CandidateStatus.QUALIFIED.value, 0)
    interview = stats.get(CandidateStatus.INTERVIEW.value, 0)
    
    text = f"📊 <b>Воронка конверсии</b>\n\n"
    
    # Рисуем прогресс-бар
    def progress_bar(value, total, width=20):
        if total == 0:
            return "░" * width
        filled = int((value / total) * width)
        return "█" * filled + "░" * (width - filled)
    
    text += f"🔍 Найдено: {total}\n"
    text += f"   {progress_bar(filtered, total)} {filtered} ({conversion.get('found_to_filtered', 0)}%)\n\n"
    
    text += f"🟢 Отобрано: {filtered}\n"
    text += f"   {progress_bar(invited, filtered)} {invited} ({conversion.get('filtered_to_invited', 0)}%)\n\n"
    
    text += f"📩 Приглашены: {invited}\n"
    text += f"   {progress_bar(answering, invited)} {answering} ({conversion.get('invited_to_answering', 0)}%)\n\n"
    
    text += f"💬 Ответили: {answering}\n"
    text += f"   {progress_bar(qualified, answering)} {qualified} ({conversion.get('answering_to_qualified', 0)}%)\n\n"
    
    text += f"✅ Квалифицированы: {qualified}\n"
    text += f"   {progress_bar(interview, qualified)} {interview} ({conversion.get('qualified_to_interview', 0)}%)\n\n"
    
    text += f"📅 Собеседование: {interview}\n"
    text += f"<b>Общая конверсия: {conversion.get('overall_conversion', 0)}%</b>"
    
    await message.answer(text, parse_mode="HTML")


@router.callback_query(lambda c: c.data == "analytics_menu")
async def cb_analytics_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_analytics(callback.message)


# ===== EMAIL УВЕДОМЛЕНИЯ =====

@router.message(Command("set_email"))
async def cmd_set_email(message: Message, state: FSMContext):
    """Установка email для отчётов"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    await state.set_state(EmailState.waiting_email)
    await message.answer(
        "📧 Введите email для получения отчётов:\n"
        "Например: <i>your@email.com</i>",
        parse_mode="HTML"
    )


@router.message(EmailState.waiting_email)
async def process_set_email(message: Message, state: FSMContext):
    """Обработка ввода email"""
    email = message.text.strip()
    
    # Простая валидация email
    if '@' not in email or '.' not in email:
        await message.answer("❌ Некорректный email. Попробуйте ещё раз:")
        return
    
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if company:
            company.report_email = email
            session.commit()
    
    await message.answer(f"✅ Email {email} сохранён для отправки отчётов!\n\n"
                         f"Теперь вы можете получать:\n"
                         f"• /send_report — отправить отчёт сейчас\n"
                         f"• Ежедневные отчёты (будут приходить автоматически)")
    await state.clear()


@router.message(Command("send_report"))
async def cmd_send_report(message: Message):
    """Отправить отчёт на email"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        if not company.report_email:
            await message.answer("❌ Сначала установите email через /set_email")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()
        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return
        
        vacancy = vacancies[0]
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
    
    from email_service import email_service
    
    status_msg = await message.answer("📧 Отправляю отчёт...")
    
    success = email_service.send_daily_report(company, vacancy, candidates)
    
    if success:
        await status_msg.edit_text(f"✅ Отчёт отправлен на {company.report_email}")
    else:
        await status_msg.edit_text("❌ Не удалось отправить отчёт. Проверьте настройки email.")


@router.message(Command("test_email"))
async def cmd_test_email(message: Message):
    """Тест отправки email"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        if not company.report_email:
            await message.answer("❌ Сначала установите email через /set_email")
            return
    
    from email_service import email_service
    
    if not email_service.is_configured():
        await message.answer(
            "❌ Email не настроен в боте.\n"
            "Администратор должен добавить SMTP настройки в .env:\n"
            "SMTP_SERVER=smtp.yandex.ru\n"
            "SMTP_PORT=465\n"
            "SMTP_USERNAME=ваш_логин@yandex.ru\n"
            "SMTP_PASSWORD=пароль_приложения\n"
            "FROM_EMAIL=ваш_логин@yandex.ru"
        )
        return
    
    test_html = """
    <h1>✅ Тестовое письмо</h1>
    <p>Если вы это видите, значит email настроен правильно!</p>
    <p>Теперь вы будете получать:</p>
    <ul>
        <li>Ежедневные отчёты по кандидатам</li>
        <li>Уведомления о новых кандидатах</li>
        <li>Напоминания о собеседованиях</li>
    </ul>
    """
    
    status_msg = await message.answer("📧 Отправляю тестовое письмо...")
    
    success = email_service.send_email(company.report_email, "✅ Тестовое письмо от GWork HR Bot", test_html)
    
    if success:
        await status_msg.edit_text(f"✅ Тестовое письмо отправлено на {company.report_email}")
    else:
        await status_msg.edit_text("❌ Не удалось отправить тестовое письмо. Проверьте логи.")


@router.callback_query(lambda c: c.data == "email_menu")
async def cb_email_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_set_email(callback.message)


# ===== ЯНДЕКС.КАЛЕНДАРЬ =====

@router.message(Command("calendar_setup"))
async def cmd_calendar_setup(message: Message):
    """Настройка Яндекс.Календаря"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    if not settings.yandex_login or not settings.yandex_app_password:
        await message.answer(
            "❌ Данные Яндекс.Календаря не настроены.\n\n"
            "Добавьте в файл .env:\n"
            "YANDEX_LOGIN=ваш_логин@yandex.ru\n"
            "YANDEX_APP_PASSWORD=пароль_приложения"
        )
        return
    
    status_msg = await message.answer("🔄 Подключаюсь к Яндекс.Календарю...")
    
    try:
        from yandex_calendar import YandexCalendarClient
        
        client = YandexCalendarClient(message.from_user.id)
        
        # Получаем события на сегодня для проверки
        events = client.get_events(days=1)
        
        # Сохраняем статус в компании
        with get_session() as session:
            company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
            if company:
                company.calendar_connected = True
                company.calendar_email = settings.yandex_login
                session.commit()
        
        await status_msg.edit_text(
            "✅ <b>Яндекс.Календарь успешно подключён!</b>\n\n"
            f"📅 Найдено событий на сегодня: {len(events)}\n\n"
            f"Теперь при выборе кандидатом времени собеседования "
            f"событие будет автоматически создаваться в вашем календаре.\n\n"
            f"Для проверки используйте:\n"
            f"/calendar_test — показать свободные слоты на завтра\n"
            f"/calendar_events — показать ближайшие события",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка подключения: {e}")


@router.message(Command("calendar_test"))
async def cmd_calendar_test(message: Message):
    """Тест Яндекс.Календаря - показывает свободные слоты на завтра"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    if not settings.yandex_login or not settings.yandex_app_password:
        await message.answer(
            "❌ Данные Яндекс.Календаря не настроены.\n"
            "Используйте /calendar_setup для настройки"
        )
        return
    
    status_msg = await message.answer("🔄 Получаю свободные слоты...")
    
    try:
        from yandex_calendar import YandexCalendarClient
        
        client = YandexCalendarClient(message.from_user.id)
        
        # Получаем слоты на завтра
        tomorrow = datetime.now().date() + timedelta(days=1)
        slots = client.get_free_slots(tomorrow, duration_minutes=60)
        
        if not slots:
            await status_msg.edit_text(f"📅 На завтра ({tomorrow.strftime('%d.%m.%Y')}) нет свободных слотов.")
            return
        
        text = f"📅 <b>Свободные слоты на завтра ({tomorrow.strftime('%d.%m.%Y')}):</b>\n\n"
        for slot in slots[:10]:
            text += f"• {slot['text']}\n"
        
        await status_msg.edit_text(text, parse_mode="HTML")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")


@router.message(Command("calendar_events"))
async def cmd_calendar_events(message: Message):
    """Показывает ближайшие события в календаре"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    if not settings.yandex_login or not settings.yandex_app_password:
        await message.answer("❌ Календарь не настроен. Используйте /calendar_setup")
        return
    
    status_msg = await message.answer("🔄 Загружаю события...")
    
    try:
        from yandex_calendar import YandexCalendarClient
        
        client = YandexCalendarClient(message.from_user.id)
        events = client.get_events(days=7)
        
        if not events:
            await status_msg.edit_text("📭 На ближайшую неделю событий нет")
            return
        
        text = "📅 <b>События на ближайшую неделю:</b>\n\n"
        for event in events[:10]:
            # Форматируем время
            start_time = event['start']
            if 'T' in start_time:
                # 2026-03-13T15:00:00+03:00 -> 13.03.2026 15:00
                date_part = start_time.split('T')[0]
                time_part = start_time.split('T')[1][:5]
                formatted_date = f"{date_part[8:10]}.{date_part[5:7]}.{date_part[:4]} {time_part}"
            else:
                formatted_date = start_time
            
            text += f"• <b>{event['summary']}</b> — {formatted_date}\n"
        
        if len(events) > 10:
            text += f"\n... и ещё {len(events) - 10} событий"
        
        await status_msg.edit_text(text, parse_mode="HTML")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data == "calendar_menu")
async def cb_calendar_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_calendar_setup(callback.message)


# ===== ОБРАБОТЧИКИ КНОПОК =====

@router.callback_query(F.data.startswith("invite:"))
async def cb_invite(callback: CallbackQuery):
    candidate_id = int(callback.data.split(":", 1)[1])
    
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        if not candidate:
            await callback.answer("❌ Кандидат не найден", show_alert=True)
            return
        
        # Проверяем, не отсеян ли кандидат
        if candidate.status == CandidateStatus.REJECTED.value:
            await callback.answer("❌ Кандидат отсеян фильтрами", show_alert=True)
            return
        
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).one()
        company = session.query(Company).filter(Company.id == vacancy.company_id).one()
        
        message_text = generate_invite_message(candidate, vacancy, company)
        
        sent = await send_message_to_candidate(candidate, message_text)
        
        if sent:
            candidate.status = CandidateStatus.INVITED.value
            candidate.dialog_step = 1
            candidate.last_activity_at = datetime.now()
            session.commit()
            
            await callback.answer("✅ Приглашение отправлено!")
            
            await callback.message.answer(
                f"📤 <b>Приглашение отправлено кандидату {candidate.name_or_nick}</b>\n\n"
                f"Текст сообщения:\n{message_text}",
                parse_mode="HTML"
            )
        else:
            contact_info = f"\n📞 Контакт: {candidate.contact}" if candidate.contact else ""
            await callback.answer("⚠️ Не удалось отправить автоматически")
            await callback.message.answer(
                f"📝 <b>Пример сообщения для {candidate.name_or_nick}:</b>\n\n"
                f"{message_text}\n{contact_info}",
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(callback: CallbackQuery):
    candidate_id = int(callback.data.split(":", 1)[1])
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        if not candidate:
            await callback.answer("❌ Кандидат не найден", show_alert=True)
            return
        candidate.status = CandidateStatus.ARCHIVE.value
        candidate.last_activity_at = datetime.now()
        session.commit()
    await callback.answer("📦 В архиве")


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(callback: CallbackQuery):
    candidate_id = int(callback.data.split(":", 1)[1])
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        if not candidate:
            await callback.answer("❌ Кандидат не найден", show_alert=True)
            return
        candidate.status = CandidateStatus.FAVORITE.value
        candidate.last_activity_at = datetime.now()
        session.commit()
    await callback.answer("⭐ В избранном")


@router.callback_query(F.data.startswith("ask:"))
async def cb_ask(callback: CallbackQuery):
    candidate_id = int(callback.data.split(":", 1)[1])
    with get_session() as session:
        candidate = session.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        if not candidate:
            await callback.answer("❌ Кандидат не найден", show_alert=True)
            return
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).one()
    
    await callback.answer()
    await callback.message.answer(
        f"❓ <b>Пример уточняющих вопросов кандидату {candidate.name_or_nick}:</b>\n\n"
        f"• Подходит ли вам график работы ({vacancy.schedule})?\n"
        f"• Какие у вас зарплатные ожидания?\n"
        f"• Когда вы могли бы выйти на работу?\n"
        f"• Есть ли у вас опыт работы в похожей сфере?",
        parse_mode="HTML"
    )


def build_script_text(candidate: Candidate, vacancy: Vacancy, company: Company) -> str:
    return (
        f"<b>Сценарий диалога с кандидатом {candidate.name_or_nick}</b>\n"
        f"на вакансию «{vacancy.role}» в компании {company.name_and_industry}:\n\n"
        "🔹 <b>Сообщение 1 (первый контакт):</b>\n"
        f"«Здравствуйте, {candidate.name_or_nick}! Меня зовут ИИ-HR, я помогаю "
        f"компании {company.name_and_industry} в {company.location} с подбором персонала. "
        f"Мы сейчас ищем {vacancy.role}. По вашему опыту вы можете подойти, поэтому "
        "хотел(а) бы задать пару уточняющих вопросов:\n"
        "1) Насколько вам комфортен график работы, который мы предлагаем?\n"
        "2) Какие у вас примерные зарплатные ожидания?\n"
        "3) Когда вы могли бы выйти на работу, если всё подойдёт?\n"
        "Можно ответить в свободной форме.»\n\n"
        "🔹 <b>Сообщение 2 (после ответа, если всё ок):</b>\n"
        "«Спасибо за ответы! По вашим откликам вы нам подходите. "
        "Предлагаю договориться о коротком собеседовании (онлайн/офлайн). "
        "Можем предложить несколько вариантов времени, а вы выберете удобный. "
        "После подтверждения пришлём вам все детали.»\n\n"
        "🔹 <b>Сообщение 3 (если кандидат не подходит):</b>\n"
        "«Спасибо большое за ответы! Сейчас, к сожалению, у нас нет подходящей позиции "
        "под ваш профиль, но мы сохраним ваши контакты и при появлении релевантных "
        "вакансий обязательно свяжемся. Хорошего дня!»"
    )


@router.callback_query(F.data.startswith("script_btn:"))
async def cb_script_from_button(callback: CallbackQuery):
    """Выдаёт скрипт диалога по кнопке из карточки."""
    try:
        candidate_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный кандидат.", show_alert=True)
        return

    with get_session() as session:
        candidate = (
            session.query(Candidate)
            .filter(Candidate.id == candidate_id)
            .one_or_none()
        )
        if not candidate:
            await callback.answer("Кандидат не найден.", show_alert=True)
            return
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).one()
        company = session.query(Company).filter(Company.id == vacancy.company_id).one()

    await callback.answer()
    await callback.message.answer(build_script_text(candidate, vacancy, company), parse_mode="HTML")


@router.message()
async def handle_candidate_reply(message: Message):
    """Обрабатывает ответы от кандидатов с предквалификацией"""
    if is_admin(message.from_user.id):
        return
    
    username = message.from_user.username
    if not username:
        return
    
    with get_session() as session:
        candidate = session.query(Candidate).filter(
            Candidate.contact == f"@{username}"
        ).first()
        
        if not candidate:
            return
        
        # Проверяем, не отсеян ли кандидат
        if candidate.status == CandidateStatus.REJECTED.value:
            return
        
        vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).first()
        company = session.query(Company).filter(Company.id == vacancy.company_id).first()
        
        if not vacancy or not company:
            return
        
        candidate.last_reply = message.text[:500]
        candidate.last_reply_at = datetime.now()
        candidate.last_activity_at = datetime.now()
        
        current_step = getattr(candidate, 'dialog_step', 1)
        
        if current_step == 1:
            # Сохраняем ответ на первый вопрос (график)
            candidate.answers_schedule = message.text[:500]
            candidate.dialog_step = 2
            
            # Отправляем второй вопрос
            reply_text = generate_followup_message(candidate, vacancy, company, step=2)
            await send_message_to_candidate(candidate, reply_text)
            
        elif current_step == 2:
            # Сохраняем ответ на второй вопрос (зарплата)
            candidate.answers_salary = message.text[:500]
            candidate.dialog_step = 3
            
            # Отправляем третий вопрос
            reply_text = generate_followup_message(candidate, vacancy, company, step=3)
            await send_message_to_candidate(candidate, reply_text)
            
        elif current_step == 3:
            # Сохраняем ответ на третий вопрос (сроки)
            candidate.answers_timing = message.text[:500]
            candidate.dialog_step = 4
            
            # Анализируем все ответы
            from pre_qualification import PreQualificationAnalyzer, format_qualification_results
            
            answers = {
                'schedule': candidate.answers_schedule or "",
                'salary': candidate.answers_salary or "",
                'timing': candidate.answers_timing or ""
            }
            
            analyzer = PreQualificationAnalyzer(candidate, vacancy, company)
            results = analyzer.analyze_all(answers)
            
            # Сохраняем результаты
            candidate.qualification_score = results['total_score']
            candidate.qualification_details = results
            candidate.qualification_date = datetime.now()
            candidate.qualification_history = results.get('history', [])
            candidate.extracted_keywords_from_answers = results.get('keywords', [])
            
            # Снимаем флаг нового кандидата после взаимодействия
            candidate.is_new = False
            
            # Принимаем решение
            if results['verdict'] == 'lead':
                candidate.status = CandidateStatus.QUALIFIED.value
                
                # Получаем реальные слоты из календаря, если он настроен
                try:
                    from yandex_calendar import YandexCalendarClient
                    
                    if settings.yandex_login and settings.yandex_app_password:
                        client = YandexCalendarClient(company.owner_id)
                        tomorrow = datetime.now().date() + timedelta(days=1)
                        day_after = datetime.now().date() + timedelta(days=2)
                        
                        slots_tomorrow = client.get_free_slots(tomorrow, duration_minutes=60)
                        slots_day_after = client.get_free_slots(day_after, duration_minutes=60)
                        
                        # Объединяем слоты
                        all_slots = slots_tomorrow + slots_day_after
                        test_slots = all_slots[:3] if all_slots else []
                    else:
                        # Если календарь не настроен, используем тестовые слоты
                        test_slots = [
                            {'start': datetime.now() + timedelta(hours=24), 'end': datetime.now() + timedelta(hours=25), 'text': '10:00 - 11:00 (тест)'},
                            {'start': datetime.now() + timedelta(hours=26), 'end': datetime.now() + timedelta(hours=27), 'text': '12:00 - 13:00 (тест)'},
                            {'start': datetime.now() + timedelta(hours=29), 'end': datetime.now() + timedelta(hours=30), 'text': '15:00 - 16:00 (тест)'}
                        ]
                except Exception as e:
                    logger.error(f"Ошибка получения слотов из календаря: {e}")
                    test_slots = [
                        {'start': datetime.now() + timedelta(hours=24), 'end': datetime.now() + timedelta(hours=25), 'text': '10:00 - 11:00'},
                        {'start': datetime.now() + timedelta(hours=26), 'end': datetime.now() + timedelta(hours=27), 'text': '12:00 - 13:00'},
                        {'start': datetime.now() + timedelta(hours=29), 'end': datetime.now() + timedelta(hours=30), 'text': '15:00 - 16:00'}
                    ]
                
                slots_data = []
                for slot in test_slots[:3]:
                    slots_data.append({
                        'start': slot['start'].isoformat(),
                        'end': slot['end'].isoformat(),
                        'text': slot['text']
                    })
                candidate.available_slots = slots_data
                
                reply_text = generate_followup_message(candidate, vacancy, company, step=4, slots=test_slots)
                
            elif results['verdict'] == 'clarify':
                candidate.status = CandidateStatus.CLARIFY.value
                
                # Генерируем уточняющие вопросы
                questions = analyzer.generate_followup_questions(results)
                if questions:
                    reply_text = (
                        f"Спасибо за ответы! У меня есть несколько уточняющих вопросов:\n\n"
                        f"{chr(10).join(['• ' + q for q in questions])}\n\n"
                        f"Пожалуйста, ответьте на них."
                    )
                else:
                    reply_text = (
                        f"Спасибо за ответы! Мне нужно уточнить некоторые детали. "
                        f"Мы скоро свяжемся с вами."
                    )
                candidate.dialog_step = 5  # Шаг уточнения
                
            else:  # reject
                candidate.status = CandidateStatus.REJECTED.value
                candidate.rejection_reason = "Не прошёл предквалификацию"
                reply_text = (
                    f"Спасибо за ответы! К сожалению, сейчас у нас нет подходящей "
                    f"вакансии для вас. Мы сохраним ваши контакты и свяжемся, "
                    f"когда появится подходящее предложение. Хорошего дня!"
                )
            
            # Отправляем ответ
            await send_message_to_candidate(candidate, reply_text)
            
            # Уведомляем администратора
            admin_text = format_qualification_results(results)
            await bot.send_message(
                chat_id=company.owner_id,
                text=admin_text,
                parse_mode="HTML"
            )
            
        elif current_step == 4:
            # Обработка выбора времени с созданием события в календаре
            try:
                choice = int(message.text.strip())
                if 1 <= choice <= 3 and candidate.available_slots:
                    slots_data = candidate.available_slots
                    if slots_data and len(slots_data) >= choice:
                        selected_slot = slots_data[choice - 1]
                        
                        # Сохраняем выбранный слот
                        candidate.interview_slot_text = selected_slot['text']
                        candidate.status = CandidateStatus.INTERVIEW.value
                        candidate.dialog_step = 6
                        
                        # Пытаемся создать событие в календаре
                        calendar_note = ""
                        event = None
                        
                        try:
                            from yandex_calendar import YandexCalendarClient
                            
                            # Проверяем, настроен ли календарь
                            if settings.yandex_login and settings.yandex_app_password:
                                client = YandexCalendarClient(company.owner_id)
                                
                                # Парсим время из выбранного слота
                                start_time = datetime.fromisoformat(selected_slot['start'])
                                end_time = datetime.fromisoformat(selected_slot['end'])
                                
                                # Создаём событие
                                event = client.create_event(
                                    summary=f"Собеседование: {vacancy.role} - {candidate.name_or_nick}",
                                    description=f"Кандидат: {candidate.name_or_nick}\n"
                                              f"Источник: {candidate.source}\n"
                                              f"Ссылка: {candidate.source_link}\n"
                                              f"Контакт: {candidate.contact}\n\n"
                                              f"Вакансия: {vacancy.role}\n"
                                              f"Город: {vacancy.city}",
                                    location=company.location,
                                    start_time=start_time,
                                    end_time=end_time,
                                    attendees=[company.report_email] if company.report_email else None,
                                    reminders=[30, 60]  # Напоминания за 30 и 60 минут
                                )
                                
                                if event:
                                    candidate.calendar_event_id = event['id']
                                    candidate.calendar_created_at = datetime.now()
                                    calendar_note = "\n\n✅ Событие добавлено в ваш календарь!"
                                else:
                                    calendar_note = "\n\n⚠️ Не удалось создать событие в календаре, но время записано."
                            else:
                                calendar_note = ""
                                
                        except Exception as e:
                            logger.error(f"Ошибка создания события в календаре: {e}")
                            calendar_note = "\n\n⚠️ Ошибка синхронизации с календарём."
                        
                        # Отправляем подтверждение кандидату
                        reply_text = generate_followup_message(candidate, vacancy, company, step=6) + calendar_note
                        await send_message_to_candidate(candidate, reply_text)
                        
                        # Уведомляем администратора
                        admin_text = f"✅ <b>Кандидат выбрал время!</b>\n\n"
                        admin_text += f"Кандидат: {candidate.name_or_nick}\n"
                        admin_text += f"Выбранное время: {selected_slot['text']}\n"
                        
                        if event:
                            admin_text += f"\n📅 Событие создано в календаре"
                        else:
                            admin_text += f"\n📞 Свяжитесь с кандидатом для подтверждения."
                        
                        await bot.send_message(
                            chat_id=company.owner_id,
                            text=admin_text,
                            parse_mode="HTML"
                        )
            except (ValueError, IndexError):
                pass
        
        elif current_step == 5:
            # Обработка уточняющих вопросов
            candidate.answers_clarify = message.text[:500]
            
            # Повторный анализ с новыми ответами
            from pre_qualification import PreQualificationAnalyzer, format_qualification_results
            
            answers = {
                'schedule': candidate.answers_schedule or "",
                'salary': candidate.answers_salary or "",
                'timing': candidate.answers_timing or "",
                'clarify': candidate.answers_clarify or ""
            }
            
            analyzer = PreQualificationAnalyzer(candidate, vacancy, company)
            results = analyzer.analyze_all(answers)
            
            candidate.qualification_score = results['total_score']
            candidate.qualification_details = results
            candidate.qualification_history = results.get('history', [])
            candidate.extracted_keywords_from_answers = results.get('keywords', [])
            
            if results['verdict'] == 'lead':
                candidate.status = CandidateStatus.QUALIFIED.value
                candidate.dialog_step = 4  # Переходим к выбору времени
                
                # Получаем реальные слоты из календаря
                try:
                    from yandex_calendar import YandexCalendarClient
                    
                    if settings.yandex_login and settings.yandex_app_password:
                        client = YandexCalendarClient(company.owner_id)
                        tomorrow = datetime.now().date() + timedelta(days=1)
                        day_after = datetime.now().date() + timedelta(days=2)
                        
                        slots_tomorrow = client.get_free_slots(tomorrow, duration_minutes=60)
                        slots_day_after = client.get_free_slots(day_after, duration_minutes=60)
                        
                        all_slots = slots_tomorrow + slots_day_after
                        test_slots = all_slots[:3] if all_slots else []
                    else:
                        test_slots = [
                            {'start': datetime.now() + timedelta(hours=24), 'end': datetime.now() + timedelta(hours=25), 'text': '10:00 - 11:00'},
                            {'start': datetime.now() + timedelta(hours=26), 'end': datetime.now() + timedelta(hours=27), 'text': '12:00 - 13:00'},
                            {'start': datetime.now() + timedelta(hours=29), 'end': datetime.now() + timedelta(hours=30), 'text': '15:00 - 16:00'}
                        ]
                except Exception as e:
                    logger.error(f"Ошибка получения слотов: {e}")
                    test_slots = [
                        {'start': datetime.now() + timedelta(hours=24), 'end': datetime.now() + timedelta(hours=25), 'text': '10:00 - 11:00'},
                        {'start': datetime.now() + timedelta(hours=26), 'end': datetime.now() + timedelta(hours=27), 'text': '12:00 - 13:00'},
                        {'start': datetime.now() + timedelta(hours=29), 'end': datetime.now() + timedelta(hours=30), 'text': '15:00 - 16:00'}
                    ]
                
                slots_data = []
                for slot in test_slots[:3]:
                    slots_data.append({
                        'start': slot['start'].isoformat(),
                        'end': slot['end'].isoformat(),
                        'text': slot['text']
                    })
                candidate.available_slots = slots_data
                
                reply_text = generate_followup_message(candidate, vacancy, company, step=4, slots=test_slots)
                await send_message_to_candidate(candidate, reply_text)
                
                # Уведомляем администратора
                admin_text = format_qualification_results(results)
                await bot.send_message(
                    chat_id=company.owner_id,
                    text=admin_text,
                    parse_mode="HTML"
                )
            else:
                candidate.status = CandidateStatus.REJECTED.value
                candidate.rejection_reason = "Не прошёл предквалификацию после уточнений"
                reply_text = (
                    f"Спасибо за ответы! К сожалению, сейчас у нас нет подходящей "
                    f"вакансии для вас. Мы сохраним ваши контакты и свяжемся, "
                    f"когда появится подходящее предложение. Хорошего дня!"
                )
                await send_message_to_candidate(candidate, reply_text)
        
        session.commit()


@router.message(Command("pipeline"))
async def cmd_pipeline(message: Message):
    """Воронка по последней вакансии"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
        vacancies = session.query(Vacancy).filter(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc()).all()

        if not vacancies:
            await message.answer("📭 Нет вакансий. Создайте: /new_job")
            return

        vacancy = vacancies[0]
        qs = (
            session.query(Candidate.status, func.count(Candidate.id))
            .filter(Candidate.vacancy_id == vacancy.id)
            .group_by(Candidate.status)
            .all()
        )

    total_by_status = {status: count for status, count in qs}

    def cnt(s: str) -> int:
        return total_by_status.get(s, 0)

    text = (
        f"📊 <b>Воронка по вакансии: {vacancy.role} ({vacancy.city})</b>\n\n"
        f"🔍 Найдено: {cnt(CandidateStatus.FOUND.value)}\n"
        f"🟢 Подходят: {cnt(CandidateStatus.FILTERED.value)}\n"
        f"📩 Приглашены: {cnt(CandidateStatus.INVITED.value)}\n"
        f"💬 Отвечают: {cnt(CandidateStatus.ANSWERING.value)}\n"
        f"⚠️ Уточнение: {cnt(CandidateStatus.CLARIFY.value)}\n"
        f"✅ Квалифицированы: {cnt(CandidateStatus.QUALIFIED.value)}\n"
        f"📅 Собеседование: {cnt(CandidateStatus.INTERVIEW.value)}\n"
        f"⭐ Избранные: {cnt(CandidateStatus.FAVORITE.value)}\n"
        f"❌ Отсеяно фильтрами: {cnt(CandidateStatus.REJECTED.value)}\n"
        f"📦 Архив: {cnt(CandidateStatus.ARCHIVE.value)}"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("help_hr"))
async def cmd_help_hr(message: Message):
    """Подробная справка"""
    help_text = """
<b>🤖 GWork HR Bot - Справка</b>

<b>🔧 НАСТРОЙКА</b>
/onboarding - профиль компании
/new_job - новая вакансия
/filters - управление фильтрами
/calendar_setup - настроить Яндекс.Календарь
/set_email - настроить email для отчётов

<b>👥 КАНДИДАТЫ</b>
/candidates - список кандидатов
/pipeline - воронка
/rejected - отсеянные кандидаты
/red_flags - статистика красных флагов
/stats_normalized - статистика нормализации

<b>📅 СОРТИРОВКА И ДАТЫ</b>
/sort - сортировка кандидатов
/report_stats - статистика по датам

<b>📤 ЭКСПОРТ</b>
/export - скачать отчёт (CSV/HTML)
/send_report - отправить отчёт на email

<b>📊 АНАЛИТИКА</b>
/analytics - полный аналитический отчёт
/sources - статистика по источникам
/conversion - воронка конверсии

<b>📧 EMAIL-УВЕДОМЛЕНИЯ</b>
/set_email - установить email для отчётов
/send_report - отправить отчёт сейчас
/test_email - проверить настройки email

<b>📅 КАЛЕНДАРЬ</b>
/calendar_setup - настроить Яндекс.Календарь
/calendar_test - показать свободные слоты
/calendar_events - показать ближайшие события

<b>📊 СТАТУСЫ</b>
🔍 Найдено - новые кандидаты
🟢 Подходят - score 60+
📩 Приглашены - отправлено сообщение
💬 Отвечают - кандидат ответил
⚠️ Уточнение - нужно дополнительное общение
✅ Квалифицированы - прошли предквалификацию
📅 Собеседование - назначена встреча
❌ Отсеяно - не прошли фильтры
📦 Архив - ручной отказ

<b>🌐 ИСТОЧНИКИ КАНДИДАТОВ</b>
🇭 HeadHunter - резюме
🟢 SuperJob - резюме
👨‍💻 Habr Career - IT-специалисты
🏢 Работа в России - гос.портал
✈️ Telegram - парсинг каналов
📱 VK - ВКонтакте

<b>🔍 ЖЁСТКИЕ ФИЛЬТРЫ</b>
• Город должен совпадать
• Зарплата в пределах вилки
• Опыт (если требуется)
• 📋 Требования (можно пропустить)
• 🚩 Красные флаги - отсев подозрительных

<b>📈 ПРЕДКВАЛИФИКАЦИЯ</b>
• Анализ ответов кандидата
• Оценка по 4 критериям
• Автоматическое решение (вести/уточнить/не вести)
"""
    await message.answer(help_text, parse_mode="HTML")


@router.callback_query(lambda c: c.data == "search")
async def cb_search(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await cmd_find(callback.message, state)


@router.callback_query(lambda c: c.data == "candidates")
async def cb_candidates(callback: CallbackQuery):
    await callback.answer()
    await cmd_candidates(callback.message)


@router.callback_query(lambda c: c.data == "stats")
async def cb_stats(callback: CallbackQuery):
    await callback.answer()
    await cmd_pipeline(callback.message)


@router.callback_query(lambda c: c.data == "help")
async def cb_help(callback: CallbackQuery):
    await callback.answer()
    await cmd_help_hr(callback.message)


@router.message(Command("find"))
async def cmd_find(message: Message, state: FSMContext):
    """Поиск кандидатов (заглушка)"""
    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
    
    await message.answer(
        "🔍 <b>Поиск кандидатов</b>\n\n"
        "Эта функция находится в разработке.\n"
        "Пока вы можете просматривать уже найденных кандидатов через /candidates",
        parse_mode="HTML"
    )


async def main() -> None:
    init_db()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env")
    
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК GWork HR BOT")
    logger.info("=" * 60)
    logger.info(f"✅ BOT_TOKEN: {'установлен' if settings.bot_token else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ HH_API_TOKEN: {'установлен' if settings.hh_api_token else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ SUPERJOB_API_KEY: {'установлен' if settings.superjob_api_key else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ HABR_CLIENT_ID: {'установлен' if settings.habr_client_id else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ DEEPSEEK_API_KEY: {'установлен' if settings.deepseek_api_key else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ YANDEX_LOGIN: {'установлен' if settings.yandex_login else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ VK_TOKEN: {'установлен' if settings.vk_token else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"✅ SMTP: {'настроен' if email_service.is_configured() else 'НЕ НАСТРОЕН'}")
    logger.info("=" * 60)
    
    # Запускаем веб-сервер для Render health checks в отдельном потоке
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("🌐 Веб-сервер для health checks запущен в фоновом режиме")
    
    # Инициализируем VK бота, если есть токен
    vk_bot_instance = None
    if settings.has_vk:
        vk_bot_instance = init_vk_bot()
        if vk_bot_instance:
            # Запускаем VK бота в отдельной задаче
            asyncio.create_task(vk_bot_instance.start_polling(handle_vk_message))
            logger.info("📱 VK бот запущен в фоновом режиме")
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
