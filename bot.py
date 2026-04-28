# bot_main.py
# FIXED VERSION: All sources + Hard Filters + Red Flags + Normalization + Pre-qualification + Date + Export + Analytics + Email + Calendar + Web Server for Render + VK Support + Auto-invites
# ВЕРСИЯ С РАЗДЕЛЬНЫМИ EVENT LOOP ДЛЯ VK И TELEGRAM
# FIXED: search_trudvsem_candidates теперь ищет ТОЛЬКО резюме/кандидатов, а не вакансии

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
    LabeledPrice,
    PreCheckoutQuery,
    SuccessfulPayment,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func
from flask import Flask, jsonify

from config import settings
from db import get_session, init_db
from deepseek_client import DeepSeekClient
from models import Candidate, CandidateStatus, Company, InterviewSlot, Vacancy, VacancyTemplate
try:
    from models import Payment, PaymentStatus
except Exception:
    Payment = None
    PaymentStatus = None
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

@app.route('/reset_webhook')
def reset_webhook():
    """Эндпоинт для сброса webhook Telegram"""
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def reset():
            await bot.delete_webhook()
            info = await bot.get_webhook_info()
            return info
        
        info = loop.run_until_complete(reset())
        loop.close()
        
        return jsonify({
            'status': 'ok',
            'message': 'Webhook удалён',
            'webhook_url': info.url if info else 'None'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
# Глобальная переменная для хранения экземпляра VK бота
vk_bot_instance = None
vk_own_loop = None
vk_thread = None

async def send_vk_message_to_candidate(candidate: Candidate, message_text: str) -> bool:
    """
    Отправляет сообщение кандидату через VK
    """
    global vk_bot_instance
    
    if not vk_bot_instance:
        logger.warning("⚠️ VK бот не инициализирован")
        return False
    
    return vk_bot_instance.send_message_to_candidate(candidate, message_text)


async def handle_vk_message(message_data: Dict[str, Any]):
    """
    Обрабатывает сообщение из VK — передаём в vk_handlers
    """
    global vk_bot_instance
    
    try:
        from vk_handlers import handle_vk_message as vk_handler
        
        # Проверяем и инициализируем VK бота если нужно
        if vk_bot_instance is None:
            logger.warning("⚠️ VK бот не инициализирован, пробуем инициализировать...")
            vk_bot_instance = init_vk_bot()
        
        if vk_bot_instance is None:
            logger.error("❌ VK бот не инициализирован")
            return
        
        # Передаём данные в обработчик
        await vk_handler(message_data)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки VK сообщения: {e}")
        import traceback
        traceback.print_exc()


def run_vk_bot_in_separate_loop():
    """
    Запускает VK бота в отдельном потоке с собственным event loop
    """
    global vk_bot_instance, vk_own_loop
    
    logger.info("🔄 Запуск VK бота в отдельном потоке со своим event loop...")
    
    # Создаём новый event loop для VK бота
    vk_own_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(vk_own_loop)
    
    try:
        # Инициализируем VK бота
        vk_bot_instance = init_vk_bot()
        
        if vk_bot_instance:
            logger.info("✅ VK бот инициализирован, запускаем polling...")
            # Запускаем polling VK бота в этом event loop
            vk_own_loop.run_until_complete(vk_bot_instance.start_polling(handle_vk_message))
        else:
            logger.error("❌ Не удалось инициализировать VK бота")
            
    except Exception as e:
        logger.error(f"❌ Ошибка в VK боте: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Очистка
        if vk_own_loop:
            vk_own_loop.close()
            logger.info("🔒 Event loop VK бота закрыт")


# ===== АВТОМАТИЧЕСКИЕ ПРИГЛАШЕНИЯ ДЛЯ ТОП-КАНДИДАТОВ =====
async def auto_invite_top_candidates(vacancy_id: int, company: Company, vacancy: Vacancy):
    """Автоматически приглашает кандидатов с высоким рейтингом"""
    with get_session() as session:
        top_candidates = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy_id,
            Candidate.score >= 80,
            Candidate.status == CandidateStatus.FILTERED.value
        ).all()
        
        invited_count = 0
        no_contact_count = 0
        email_count = 0
        phone_count = 0
        
        for candidate in top_candidates:
            # Проверяем наличие контактов
            contact_info = candidate.contact or ""
            
            # Ищем Telegram username
            tg_match = re.search(r'@(\w+)', contact_info)
            
            if tg_match:
                # Есть Telegram - отправляем автоматически
                tg_username = tg_match.group(0)
                invite_text = generate_invite_message(candidate, vacancy, company)
                try:
                    await bot.send_message(
                        chat_id=tg_username,
                        text=invite_text,
                        parse_mode="HTML"
                    )
                    candidate.status = CandidateStatus.INVITED.value
                    invited_count += 1
                    logger.info(f"✅ Авто-приглашение отправлено в Telegram: {candidate.name_or_nick} ({tg_username})")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки в Telegram {candidate.name_or_nick}: {e}")
                    # Если не отправилось, пробуем другие контакты
                    await _send_alternative_contact_notification(company, candidate, contact_info, vacancy)
            else:
                # Нет Telegram - ищем email или телефон
                no_contact_count += 1
                
                # Поиск email
                email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', contact_info)
                # Поиск телефона (российские номера)
                phone_match = re.search(r'\+?7[\s\-]?\(?[0-9]{3}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}', contact_info)
                
                if email_match:
                    email_count += 1
                    await _send_email_contact_notification(company, candidate, email_match.group(0), contact_info, vacancy)
                elif phone_match:
                    phone_count += 1
                    await _send_phone_contact_notification(company, candidate, phone_match.group(0), contact_info, vacancy)
                else:
                    # Нет никаких контактов - просто уведомляем работодателя
                    await _send_no_contact_notification(company, candidate, contact_info, vacancy)
        
        session.commit()
        
        # Отправляем сводку работодателю
        if invited_count > 0 or no_contact_count > 0:
            summary = f"📊 <b>Сводка авто-приглашений</b>\n\n"
            summary += f"✅ Отправлено в Telegram: {invited_count}\n"
            if email_count > 0:
                summary += f"📧 Найдено email: {email_count} (требуется ручной контакт)\n"
            if phone_count > 0:
                summary += f"📞 Найдено телефонов: {phone_count} (требуется ручной контакт)\n"
            if no_contact_count - email_count - phone_count > 0:
                summary += f"⚠️ Без контактов: {no_contact_count - email_count - phone_count}\n"
            
            await bot.send_message(
                chat_id=company.owner_id,
                text=summary,
                parse_mode="HTML"
            )
            logger.info(f"📨 Отправлено {invited_count} авто-приглашений, {no_contact_count} кандидатов без Telegram")


async def _send_alternative_contact_notification(company: Company, candidate: Candidate, contact_info: str, vacancy: Vacancy):
    """Отправляет уведомление работодателю о кандидате с альтернативными контактами"""
    
    # Ищем доступные контакты
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', contact_info)
    phone_match = re.search(r'\+?7[\s\-]?\(?[0-9]{3}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}', contact_info)
    
    contacts_list = []
    if email_match:
        contacts_list.append(f"📧 Email: {email_match.group(0)}")
    if phone_match:
        contacts_list.append(f"📞 Телефон: {phone_match.group(0)}")
    
    contacts_text = "\n".join(contacts_list) if contacts_list else "❌ Контакты не найдены"
    
    message = (
        f"⚠️ <b>Кандидат без Telegram</b>\n\n"
        f"👤 {candidate.name_or_nick}\n"
        f"📊 Оценка: {candidate.score}/100\n"
        f"📍 Город: {candidate.city}\n"
        f"📝 Опыт: {candidate.experience_text[:100]}...\n\n"
        f"📋 <b>Доступные контакты:</b>\n{contacts_text}\n\n"
        f"💼 Вакансия: {vacancy.role}\n"
        f"🏢 Компания: {company.name_and_industry}\n\n"
        f"<b>Действия:</b>\n"
        f"• Свяжитесь с кандидатом по указанным контактам\n"
        f"• Используйте скрипт диалога: /candidates\n"
        f"• Или посмотрите полный профиль в системе"
    )
    
    await bot.send_message(
        chat_id=company.owner_id,
        text=message,
        parse_mode="HTML"
    )
    
    # Обновляем статус кандидата
    candidate.status = CandidateStatus.CLARIFY.value
    candidate.rejection_reason = "Нет Telegram для авто-приглашения. Требуется ручной контакт."


async def _send_email_contact_notification(company: Company, candidate: Candidate, email: str, contact_info: str, vacancy: Vacancy):
    """Отправляет уведомление для кандидата с email"""
    
    message = (
        f"📧 <b>Кандидат с email (нет Telegram)</b>\n\n"
        f"👤 {candidate.name_or_nick}\n"
        f"📊 Оценка: {candidate.score}/100\n"
        f"📍 Город: {candidate.city}\n"
        f"📧 Email: {email}\n\n"
        f"💼 Вакансия: {vacancy.role}\n\n"
        f"<b>Рекомендуемый текст письма:</b>\n"
        f"<code>Здравствуйте, {candidate.name_or_nick}!\n\n"
        f"Меня зовут [Ваше имя], я из компании {company.name_and_industry}.\n"
        f"Мы ищем {vacancy.role} и ваш опыт нам показался интересным.\n\n"
        f"Приглашаю вас на собеседование. Когда вам было бы удобно?\n\n"
        f"С уважением,\n"
        f"HR-отдел {company.name_and_industry}</code>\n\n"
        f"📋 <b>Все контакты кандидата:</b>\n{contact_info}"
    )
    
    await bot.send_message(
        chat_id=company.owner_id,
        text=message,
        parse_mode="HTML"
    )
    
    candidate.status = CandidateStatus.CLARIFY.value
    candidate.rejection_reason = f"Нет Telegram. Email: {email}"


async def _send_phone_contact_notification(company: Company, candidate: Candidate, phone: str, contact_info: str, vacancy: Vacancy):
    """Отправляет уведомление для кандидата с телефоном"""
    
    message = (
        f"📞 <b>Кандидат с телефоном (нет Telegram)</b>\n\n"
        f"👤 {candidate.name_or_nick}\n"
        f"📊 Оценка: {candidate.score}/100\n"
        f"📍 Город: {candidate.city}\n"
        f"📞 Телефон: {phone}\n\n"
        f"💼 Вакансия: {vacancy.role}\n\n"
        f"<b>Рекомендуемый скрипт звонка:</b>\n"
        f"«Здравствуйте, {candidate.name_or_nick}! Меня зовут [Имя], я из компании {company.name_and_industry}. "
        f"Мы ищем {vacancy.role}, и ваш опыт нам показался интересным. "
        f"Могу я пригласить вас на собеседование? Когда вам удобно?»\n\n"
        f"📋 <b>Все контакты кандидата:</b>\n{contact_info}"
    )
    
    await bot.send_message(
        chat_id=company.owner_id,
        text=message,
        parse_mode="HTML"
    )
    
    candidate.status = CandidateStatus.CLARIFY.value
    candidate.rejection_reason = f"Нет Telegram. Телефон: {phone}"


async def _send_no_contact_notification(company: Company, candidate: Candidate, contact_info: str, vacancy: Vacancy):
    """Отправляет уведомление о кандидате без контактов"""
    
    message = (
        f"❌ <b>Кандидат без контактов!</b>\n\n"
        f"👤 {candidate.name_or_nick}\n"
        f"📊 Оценка: {candidate.score}/100\n"
        f"📍 Город: {candidate.city}\n"
        f"📝 Опыт: {candidate.experience_text[:150]}...\n\n"
        f"💼 Вакансия: {vacancy.role}\n"
        f"🔗 Ссылка на профиль: {candidate.source_link or 'Не указана'}\n\n"
        f"<b>⚠️ Внимание!</b> У кандидата не найдено контактных данных.\n"
        f"Попробуйте найти контакты через:\n"
        f"• Ссылку на профиль выше\n"
        f"• Поиск по имени в соцсетях\n"
        f"• Если это Telegram-канал - попробуйте написать в личку"
    )
    
    await bot.send_message(
        chat_id=company.owner_id,
        text=message,
        parse_mode="HTML"
    )
    
    candidate.status = CandidateStatus.CLARIFY.value
    candidate.rejection_reason = "Нет контактных данных для связи"


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
                        company_name = exp.get("company", "")
                        start = exp.get("start", "")
                        end = exp.get("end", "настоящее время")
                        if position and company_name:
                            experience_text.append(f"{position} в {company_name} ({start}-{end})")
                    
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
                        company_name = exp.get("company", "")
                        if position and company_name:
                            experience_text.append(f"{position} в {company_name}")
                    
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


# ===== ИСПРАВЛЕННАЯ ФУНКЦИЯ ПОИСКА КАНДИДАТОВ В TRUDVSEM (ТОЛЬКО РЕЗЮМЕ) =====
async def search_trudvsem_candidates(query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск кандидатов на портале Работа в России (ТОЛЬКО РЕЗЮМЕ, а не вакансии)
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
            # Ищем РЕЗЮМЕ (resumes), а не вакансии!
            response = await client.get(
                "https://opendata.trudvsem.ru/api/v1/resumes",
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
                
                # Получаем список резюме
                try:
                    resumes = data.get("results", {}).get("resumes", [])
                    if not resumes:
                        # Пробуем альтернативный формат ответа
                        resumes = data.get("resumes", [])
                except:
                    resumes = []
                
                logger.info(f"✅ Trudvsem: найдено {len(resumes)} резюме")
                
                candidates = []
                for item in resumes[:limit]:
                    # Извлекаем данные резюме
                    resume = item.get("resume", item)
                    
                    # Имя кандидата
                    first_name = resume.get("first-name", "")
                    last_name = resume.get("last-name", "")
                    middle_name = resume.get("middle-name", "")
                    
                    name_parts = [last_name, first_name, middle_name]
                    full_name = " ".join([p for p in name_parts if p]) or "Кандидат"
                    
                    # Город
                    area = resume.get("area", {})
                    city_name = area.get("name", city) if isinstance(area, dict) else city
                    
                    # Опыт работы
                    experience_items = resume.get("experience", [])
                    experience_text = []
                    for exp in experience_items[:3]:
                        position = exp.get("position", "")
                        company_name = exp.get("company", "")
                        start_date = exp.get("start-date", "")
                        end_date = exp.get("end-date", "")
                        if position and company_name:
                            experience_text.append(f"{position} в {company_name}")
                        elif position:
                            experience_text.append(f"{position}")
                    
                    experience_str = "\n".join(experience_text) if experience_text else "Опыт не указан"
                    
                    # Навыки
                    skills_list = []
                    skills_data = resume.get("skills", [])
                    if isinstance(skills_data, list):
                        for skill in skills_data:
                            if isinstance(skill, dict):
                                skill_name = skill.get("name", "")
                                if skill_name:
                                    skills_list.append(skill_name)
                            elif isinstance(skill, str):
                                skills_list.append(skill)
                    
                    # Образование
                    education = resume.get("education", [])
                    education_text = ""
                    for edu in education[:2]:
                        if isinstance(edu, dict):
                            edu_name = edu.get("name", "")
                            if edu_name:
                                education_text += f"🎓 {edu_name}\n"
                    
                    # Желаемая зарплата
                    salary = resume.get("salary", "")
                    salary_text = f"💰 {salary} руб." if salary else ""
                    
                    # Контакты
                    contacts = []
                    # Email
                    email = resume.get("email", "")
                    if email:
                        contacts.append(f"📧 {email}")
                    # Телефон
                    phone = resume.get("phone", "")
                    if phone:
                        contacts.append(f"📞 {phone}")
                    
                    contact_str = "\n".join(contacts) if contacts else ""
                    
                    # Ссылка на резюме
                    resume_url = resume.get("url", "")
                    if not resume_url:
                        resume_id = resume.get("id", "")
                        if resume_id:
                            resume_url = f"https://trudvsem.ru/resume/{resume_id}"
                    
                    # Текст резюме
                    about_parts = []
                    if salary_text:
                        about_parts.append(salary_text)
                    if education_text:
                        about_parts.append(education_text)
                    
                    about_text = "\n".join(about_parts) if about_parts else resume.get("about", "")[:200]
                    
                    # Возраст
                    birth_date = resume.get("birth-date", "")
                    age_text = ""
                    if birth_date:
                        try:
                            birth_year = int(birth_date[:4])
                            age = datetime.now().year - birth_year
                            if 16 <= age <= 100:
                                age_text = f"🎂 {age} лет"
                                about_parts.append(age_text)
                        except:
                            pass
                    
                    candidates.append({
                        "name": full_name,
                        "city": city_name,
                        "experience": experience_str[:500],
                        "skills": skills_list[:10],
                        "about": about_text[:500],
                        "source": "trudvsem",
                        "url": resume_url,
                        "contact": contact_str,
                        "is_real": True
                    })
                
                logger.info(f"✅ Trudvsem: обработано {len(candidates)} кандидатов")
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
    global vk_bot_instance
    
    try:
        contact = candidate.contact
        if not contact:
            logger.warning(f"Нет контакта для кандидата {candidate.id}")
            return False
        
        # Ищем Telegram username в контактной информации
        tg_match = re.search(r'@(\w+)', contact)
        
        if tg_match:
            username = tg_match.group(0)
            try:
                await bot.send_message(
                    chat_id=username,
                    text=message_text,
                    parse_mode="HTML"
                )
                logger.info(f"✅ Сообщение отправлено {username}")
                
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
                logger.error(f"❌ Ошибка отправки {username}: {e}")
                return False
        
        # Проверяем, является ли контакт VK ID
        vk_match = re.search(r'vk(\d+)|^(\d+)$', contact)
        if vk_match:
            vk_id = vk_match.group(1) or vk_match.group(2)
            if vk_id and vk_bot_instance:
                success = vk_bot_instance.send_message_to_candidate(candidate, message_text)
                if success:
                    logger.info(f"✅ Сообщение отправлено VK ID {vk_id}")
                return success
        
        # Если не нашли Telegram или VK, но есть email - уведомляем
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', contact)
        if email_match:
            logger.info(f"📧 У кандидата {candidate.id} есть email: {email_match.group(0)} (требуется ручная отправка)")
            # Отправляем уведомление работодателю
            with get_session() as session:
                vacancy = session.query(Vacancy).filter(Vacancy.id == candidate.vacancy_id).first()
                company = session.query(Company).filter(Company.id == vacancy.company_id).first()
                if company:
                    await _send_email_contact_notification(company, candidate, email_match.group(0), contact, vacancy)
            return False
        
        logger.info(f"Контакт {contact} не является Telegram username, VK ID или email")
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


def _get_candidate_interview_slots(company: Company) -> List[Dict[str, Any]]:
    """Возвращает ближайшие слоты для собеседования (календарь или fallback)."""
    try:
        if settings.yandex_login and settings.yandex_app_password:
            client = YandexCalendarClient(company.owner_id)
            tomorrow = datetime.now().date() + timedelta(days=1)
            day_after = datetime.now().date() + timedelta(days=2)
            all_slots = (
                client.get_free_slots(tomorrow, duration_minutes=60)
                + client.get_free_slots(day_after, duration_minutes=60)
            )
            if all_slots:
                return all_slots[:3]
    except Exception as e:
        logger.error(f"Ошибка получения слотов из календаря: {e}")

    return [
        {"start": datetime.now() + timedelta(hours=24), "end": datetime.now() + timedelta(hours=25), "text": "10:00 - 11:00"},
        {"start": datetime.now() + timedelta(hours=26), "end": datetime.now() + timedelta(hours=27), "text": "12:00 - 13:00"},
        {"start": datetime.now() + timedelta(hours=29), "end": datetime.now() + timedelta(hours=30), "text": "15:00 - 16:00"},
    ]


def _create_calendar_event_for_candidate(
    company: Company,
    vacancy: Vacancy,
    candidate: Candidate,
    selected_slot: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], str]:
    """Создаёт событие в календаре для выбранного слота."""
    event = None
    calendar_note = ""

    try:
        if settings.yandex_login and settings.yandex_app_password:
            client = YandexCalendarClient(company.owner_id)
            start_time = datetime.fromisoformat(selected_slot["start"])
            end_time = datetime.fromisoformat(selected_slot["end"])

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
                reminders=[30, 60],
            )
            if event:
                candidate.calendar_event_id = event["id"]
                candidate.calendar_created_at = datetime.now()
                calendar_note = "\n\n✅ Событие добавлено в календарь."
            else:
                calendar_note = "\n\n⚠️ Не удалось создать событие в календаре."
    except Exception as e:
        logger.error(f"Ошибка создания события в календаре: {e}")
        calendar_note = "\n\n⚠️ Ошибка синхронизации с календарём."

    return event, calendar_note

def calculate_candidate_score(candidate: Candidate, vacancy: Vacancy, company: Company) -> tuple:
    """
    Улучшенный алгоритм оценки кандидата без использования внешнего API
    Оценивает по нескольким критериям с весами
    Возвращает (score, explanation_parts)
    """
    score = 0
    explanation_parts = []
    
    # 1. Город (вес: 20 баллов)
    city_score = 0
    if vacancy.city.lower() in candidate.city.lower():
        city_score = 20
        explanation_parts.append(f"🏙️ Город совпадает: +20")
    elif candidate.normalized_city and vacancy.city.lower() in candidate.normalized_city.lower():
        city_score = 15
        explanation_parts.append(f"🏙️ Город (нормализованный) совпадает: +15")
    else:
        if candidate.normalized_city_from_text and vacancy.city.lower() in candidate.normalized_city_from_text.lower():
            city_score = 10
            explanation_parts.append(f"🏙️ Город упомянут в тексте: +10")
        else:
            explanation_parts.append(f"🏙️ Город не совпадает: +0")
    score += city_score
    
    # 2. Опыт работы (вес: 25 баллов)
    experience_score = 0
    if candidate.experience_years:
        exp_years = candidate.experience_years
        if exp_years >= 5:
            experience_score = 25
            explanation_parts.append(f"💼 Опыт {exp_years} лет (эксперт): +25")
        elif exp_years >= 3:
            experience_score = 20
            explanation_parts.append(f"💼 Опыт {exp_years} лет (хорошо): +20")
        elif exp_years >= 1:
            experience_score = 12
            explanation_parts.append(f"💼 Опыт {exp_years} лет (начальный): +12")
        else:
            experience_score = 5
            explanation_parts.append(f"💼 Опыт {exp_years} лет (мало): +5")
    else:
        exp_text = (candidate.experience_text or "").lower()
        if "более 5" in exp_text or "более пяти" in exp_text:
            experience_score = 20
            explanation_parts.append(f"💼 В тексте указан опыт более 5 лет: +20")
        elif "более 3" in exp_text or "более трех" in exp_text:
            experience_score = 15
            explanation_parts.append(f"💼 В тексте указан опыт более 3 лет: +15")
        elif "опыт работы" in exp_text or "стаж" in exp_text:
            experience_score = 10
            explanation_parts.append(f"💼 Есть упоминание опыта: +10")
        else:
            experience_score = 5
            explanation_parts.append(f"💼 Опыт не указан явно: +5")
    score += experience_score
    
    # 3. Навыки и ключевые слова (вес: 30 баллов)
    skills_score = 0
    must_have = vacancy.must_have if vacancy.must_have and vacancy.must_have != "-" else ""
    
    if must_have:
        must_have_list = [skill.strip().lower() for skill in must_have.split(",")]
        candidate_skills = (candidate.skills_text or "").lower()
        candidate_extracted = " ".join(c.extracted_skills or []) if hasattr(candidate, 'extracted_skills') else ""
        
        found_skills = 0
        for skill in must_have_list:
            if skill in candidate_skills or skill in candidate_extracted:
                found_skills += 1
        
        if found_skills > 0:
            skills_score = int((found_skills / len(must_have_list)) * 25)
            explanation_parts.append(f"📋 Найдено {found_skills}/{len(must_have_list)} требуемых навыков: +{skills_score}")
        else:
            skills_score = 5
            explanation_parts.append(f"📋 Требуемые навыки не найдены: +5")
    else:
        skills_count = len(candidate.extracted_skills) if hasattr(candidate, 'extracted_skills') and candidate.extracted_skills else 0
        if skills_count >= 10:
            skills_score = 25
            explanation_parts.append(f"📋 Много навыков ({skills_count}): +25")
        elif skills_count >= 5:
            skills_score = 18
            explanation_parts.append(f"📋 Хороший набор навыков ({skills_count}): +18")
        elif skills_count >= 2:
            skills_score = 10
            explanation_parts.append(f"📋 Базовые навыки ({skills_count}): +10")
        else:
            skills_score = 5
            explanation_parts.append(f"📋 Навыки не указаны: +5")
    score += skills_score
    
    # 4. Зарплатные ожидания (вес: 15 баллов)
    salary_score = 0
    if candidate.salary_expectations and (vacancy.salary_from or vacancy.salary_to):
        salary_expected = candidate.salary_expectations
        salary_min = vacancy.salary_from or 0
        salary_max = vacancy.salary_to or float('inf')
        
        if salary_min <= salary_expected <= salary_max:
            salary_score = 15
            explanation_parts.append(f"💰 Зарплата {salary_expected} руб. входит в вилку: +15")
        elif salary_expected < salary_min:
            diff_percent = ((salary_min - salary_expected) / salary_min) * 100 if salary_min > 0 else 0
            if diff_percent <= 20:
                salary_score = 10
                explanation_parts.append(f"💰 Зарплата немного ниже вилки: +10")
            else:
                salary_score = 5
                explanation_parts.append(f"💰 Зарплата значительно ниже вилки: +5")
        else:
            diff_percent = ((salary_expected - salary_max) / salary_max) * 100 if salary_max != float('inf') else 0
            if diff_percent <= 20:
                salary_score = 8
                explanation_parts.append(f"💰 Зарплата немного выше вилки: +8")
            else:
                salary_score = 3
                explanation_parts.append(f"💰 Зарплата значительно выше вилки: +3")
    else:
        salary_score = 7
        explanation_parts.append(f"💰 Зарплатные ожидания не указаны: +7")
    score += salary_score
    
    # 5. Качество резюме/текста (вес: 10 баллов)
    quality_score = 0
    text_length = len(candidate.raw_text or "")
    if text_length > 500:
        quality_score = 10
        explanation_parts.append(f"📝 Подробное резюме ({text_length} символов): +10")
    elif text_length > 200:
        quality_score = 7
        explanation_parts.append(f"📝 Хорошее резюме ({text_length} символов): +7")
    elif text_length > 50:
        quality_score = 4
        explanation_parts.append(f"📝 Короткое резюме ({text_length} символов): +4")
    else:
        quality_score = 1
        explanation_parts.append(f"📝 Очень краткое резюме: +1")
    score += quality_score
    
    return min(100, score), explanation_parts


async def score_candidates_with_fallback(vacancy_desc: str, candidates: List[Dict], vacancy: Vacancy, company: Company) -> Dict:
    """
    Оценивает кандидатов с использованием DeepSeek API,
    но с fallback на локальный алгоритм при ошибках
    """
    results = {}
    
    # Пытаемся использовать DeepSeek API
    try:
        scores = deepseek.score_candidates(vacancy_desc, candidates)
        if scores and len(scores) > 0:
            unique_scores = set(int(s.get("score", 0)) for s in scores if "score" in s)
            if len(unique_scores) > 1 or (len(unique_scores) == 1 and next(iter(unique_scores)) not in [65, 70]):
                logger.info(f"✅ DeepSeek вернул оценки: {unique_scores}")
                return {int(s["id"]): s for s in scores if "id" in s}
            else:
                logger.warning(f"⚠️ DeepSeek вернул подозрительные оценки: {unique_scores}, используем локальный алгоритм")
        else:
            logger.warning("⚠️ DeepSeek не вернул оценки, используем локальный алгоритм")
    except Exception as e:
        logger.error(f"❌ Ошибка DeepSeek API: {e}, используем локальный алгоритм")
    
    # Fallback: локальный алгоритм оценки
    logger.info("📊 Используем локальный алгоритм оценки кандидатов")
    
    with get_session() as session:
        for cand_data in candidates:
            candidate_id = cand_data.get("id")
            if not candidate_id:
                continue
            
            candidate = session.query(Candidate).filter(Candidate.id == candidate_id).first()
            if not candidate:
                continue
            
            score, explanation_parts = calculate_candidate_score(candidate, vacancy, company)
            
            # Учитываем красные флаги
            if candidate.red_flags:
                penalty = get_red_flags_score(candidate.raw_text)
                if penalty > 0:
                    old_score = score
                    score = max(0, score - penalty)
                    explanation_parts.append(f"🚩 Штраф за красные флаги: -{penalty}")
                    logger.info(f"Кандидат {candidate.name_or_nick}: штраф {penalty}, скор снижен с {old_score} до {score}")
            
            results[candidate_id] = {
                "id": candidate_id,
                "score": score,
                "explanation": " | ".join(explanation_parts)
            }
    
    return results


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
    dev_info = "\n\n📧 По вопросам разработки и интеграции: rangercompany@yandex.ru"
    
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
            f"/set_email — настроить email для отчётов{dev_info}",
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
            "Для начала работы введите /onboarding"
            f"{dev_info}",
            parse_mode="HTML"
        )


@router.message(Command("onboarding"))
async def cmd_onboarding(message: Message, state: FSMContext):
    await state.set_state(OnboardingStates.name_and_industry)
    await message.answer(
        "📝 <b>Расскажите о компании:</b>\n\n"
        "Название и сфера деятельности.\n"
        "Например: <i>Салон красоты \"Лилия\", услуги маникюра и косметологии</i>",
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
    await state.clear()

    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
        if company is None:
            company = Company(owner_id=message.from_user.id)
            session.add(company)

        company.name_and_industry = data.get("name_and_industry", "")
        company.location = data.get("location", "")
        company.schedule = data.get("schedule", "")
        company.salary_range = data.get("salary_range", "")
        company.tone = tone_value
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
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).first()
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
    
    if must_have_text == "-" or must_have_text.lower() in ["нет", "нету", "пропустить", "skip"]:
        must_have_text = "-"
        await message.answer("✅ Критичные требования пропущены")
    else:
        await message.answer(f"✅ Критичные требования сохранены: {must_have_text}")
    
    await state.clear()

    with get_session() as session:
        company = session.query(Company).filter(Company.owner_id == message.from_user.id).one()
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
        
        vacancy.last_search_at = datetime.now()
        session.commit()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Найти кандидатов и рассчитать тариф", callback_data=f"smart_tariff:{vacancy_id}")],
            [InlineKeyboardButton(text="🧪 Тест: 1 кандидат - 1 Star", callback_data=f"tariff:stars_test_1:{vacancy_id}")],
        ]
    )
    await message.answer(
        "✅ <b>Вакансия создана!</b>\n\n"
        "Сначала можно запустить умный расчёт тарифа по реально найденным кандидатам.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("smart_tariff:"))
async def cb_smart_tariff(callback: CallbackQuery):
    vacancy_id = int(callback.data.split(":", 1)[1])
    await callback.answer()

    status_msg = await callback.message.answer("🔍 Ищу кандидатов и рассчитываю тарифы...")
    try:
        existing = _get_payable_candidates(vacancy_id)
        if not existing:
            await gather_real_candidates(vacancy_id, limit=50, payment_id=None)
        payable_count = len(_get_payable_candidates(vacancy_id))

        if payable_count == 0:
            await status_msg.edit_text(
                "❌ Подходящие кандидаты пока не найдены.\n"
                "Попробуйте расширить требования и повторить поиск."
            )
            return

        kb = InlineKeyboardBuilder()
        kb.button(text="🧪 1 кандидат - 1 Star (тест)", callback_data=f"tariff:stars_test_1:{vacancy_id}")
        if payable_count >= 5:
            kb.button(text="🌟 5 кандидатов - 2200 Stars", callback_data=f"tariff:stars_2200_5:{vacancy_id}")
        if payable_count >= 15:
            kb.button(text="🚀 15 кандидатов - 7450 Stars", callback_data=f"tariff:stars_7450_15:{vacancy_id}")
        if payable_count >= 30:
            kb.button(text="💼 30 кандидатов - 13900 Stars", callback_data=f"tariff:stars_13900_30:{vacancy_id}")
        if payable_count >= 50:
            kb.button(text="📧 50+ / интеграция", callback_data=f"tariff:custom:{vacancy_id}")
        kb.adjust(1)

        await status_msg.edit_text(
            f"✅ Найдено подходящих кандидатов: <b>{payable_count}</b>\n\n"
            "Выберите пакет для покупки:",
            parse_mode="HTML",
            reply_markup=kb.as_markup(),
        )
    except Exception as e:
        logger.error(f"❌ Ошибка умного расчёта тарифов: {e}")
        await status_msg.edit_text("❌ Не удалось рассчитать тарифы. Попробуйте ещё раз.")


@router.callback_query(F.data.startswith("tariff:"))
async def cb_tariff(callback: CallbackQuery):
    _, tariff_key, vacancy_id_str = callback.data.split(":")
    vacancy_id = int(vacancy_id_str)
    if tariff_key == "custom":
        await callback.message.answer("📧 Для 50+ кандидатов и интеграции пишите: rangercompany@yandex.ru")
        await callback.answer()
        return
    try:
        await send_payment_invoice(callback.from_user.id, tariff_key, vacancy_id)
        await callback.answer("💳 Счет на оплату отправлен")
    except Exception as e:
        logger.error(f"❌ Ошибка создания счета: {e}")
        err = str(e).lower()
        if "provider token" in err or "payments" in err or "invoice" in err:
            await callback.message.answer(
                "❌ Не удалось создать счет в Stars.\n\n"
                "Проверьте настройки платежей у бота в BotFather:\n"
                "1) Откройте @BotFather\n"
                "2) Выберите вашего бота\n"
                "3) Включите Telegram Stars Payments\n"
                "4) Попробуйте снова"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось создать счет из-за временной ошибки.\n"
                "Попробуйте еще раз через минуту."
            )
        await callback.answer()


async def gather_real_candidates(vacancy_id: int, limit: Optional[int] = None, payment_id: Optional[int] = None) -> int:
    """Сбор реальных кандидатов из ВСЕХ источников с применением фильтров и нормализацией"""
    with get_session() as session:
        vacancy = session.query(Vacancy).filter(Vacancy.id == vacancy_id).one()
        company = session.query(Company).filter(Company.id == vacancy.company_id).one()

        candidates: List[Candidate] = []
        added_count = 0

        def limit_reached() -> bool:
            return limit is not None and added_count >= limit

        # 1) HeadHunter
        if not limit_reached():
            logger.info(f"🔍 Поиск резюме в HeadHunter: {vacancy.role} в {vacancy.city}")
        try:
            hh_candidates = await search_hh_resumes(vacancy.role, vacancy.city, limit=5 if limit is None else min(5, max(limit - added_count, 1)))
            for cand in hh_candidates:
                if limit_reached():
                    break
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
                
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
                added_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка HeadHunter: {e}")

        # 2) SuperJob
        if not limit_reached():
            logger.info(f"🔍 Поиск кандидатов в SuperJob: {vacancy.role} в {vacancy.city}")
        try:
            if limit_reached():
                sj_candidates = []
            else:
                sj_candidates = await search_superjob_real_candidates(vacancy.role, vacancy.city, limit=min(5, max(limit - added_count, 1)) if limit is not None else 5)
            for cand in sj_candidates:
                if limit_reached():
                    break
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
                
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
                added_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка SuperJob: {e}")

        # 3) Habr Career
        if not limit_reached():
            logger.info(f"🔍 Поиск кандидатов в Habr Career: {vacancy.role} в {vacancy.city}")
        try:
            if limit_reached():
                habr_candidates = []
            else:
                habr_candidates = await search_habr_candidates(vacancy.role, vacancy.city, limit=min(5, max(limit - added_count, 1)) if limit is not None else 5)
            for cand in habr_candidates:
                if limit_reached():
                    break
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
                
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
                added_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка Habr: {e}")

        # 4) Trudvsem - ИСПРАВЛЕНО: теперь ищет ТОЛЬКО резюме
        if not limit_reached():
            logger.info(f"🔍 Поиск кандидатов в Trudvsem: {vacancy.role} в {vacancy.city}")
        try:
            if limit_reached():
                trudvsem_candidates = []
            else:
                trudvsem_candidates = await search_trudvsem_candidates(vacancy.role, vacancy.city, limit=min(5, max(limit - added_count, 1)) if limit is not None else 5)
            for cand in trudvsem_candidates:
                if limit_reached():
                    break
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
                
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
                added_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка Trudvsem: {e}")

        # 5) Telegram
        if not limit_reached():
            logger.info(f"🔍 Поиск кандидатов в Telegram: {vacancy.role} в {vacancy.city}")
        try:
            if limit_reached():
                tg_candidates = []
            else:
                tg_candidates = await telegram_parser.search_candidates(vacancy.role, vacancy.city, limit=min(5, max(limit - added_count, 1)) if limit is not None else 5)
            for cand in tg_candidates:
                if limit_reached():
                    break
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
                
                c.salary_expectations = extract_salary(c.raw_text)
                c.experience_years = extract_experience_years(c.raw_text)
                c.normalized_city = normalize_city(c.city)
                
                if c.experience_years:
                    c.normalized_experience_level = normalize_experience_level(c.experience_years)
                else:
                    parsed_years = parse_experience_to_years(c.experience_text)
                    if parsed_years:
                        c.experience_years = parsed_years
                        c.normalized_experience_level = normalize_experience_level(parsed_years)
                
                if c.skills_text:
                    normalized_skills = normalize_skills_list(c.skills_text)
                    c.extracted_skills = normalized_skills
                    c.skills_text = ", ".join(normalized_skills[:8])
                
                city_from_text = extract_city_from_text(c.raw_text)
                if city_from_text:
                    c.normalized_city_from_text = city_from_text
                
                keywords = extract_keywords(f"{c.experience_text} {c.skills_text} {c.raw_text}")
                if keywords:
                    c.extracted_keywords = keywords[:20]
                
                passed, reason = apply_hard_filters(c, vacancy, company)
                if not passed:
                    c.status = CandidateStatus.REJECTED.value
                    c.rejection_reason = reason
                    logger.info(f"Кандидат {c.name_or_nick} отсеян: {reason}")
                
                session.add(c)
                candidates.append(c)
                added_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка Telegram: {e}")

        session.commit()
        logger.info(f"✅ ВСЕГО найдено кандидатов: {len(candidates)}")
        
        passed_count = len([c for c in candidates if c.status != CandidateStatus.REJECTED.value])
        logger.info(f"✅ Прошли фильтры: {passed_count}")
        logger.info(f"❌ Отсеяно: {len(candidates) - passed_count}")

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

            # Пытаемся использовать DeepSeek API
            deepseek_success = False
            try:
                scores = deepseek.score_candidates(vacancy_desc, payload)
                scores_by_id = {int(s["id"]): s for s in scores if "id" in s}
                
                # Проверяем, что оценки не все одинаковые
                unique_scores = set(int(s.get("score", 0)) for s in scores if "score" in s)
                if len(unique_scores) > 1:
                    deepseek_success = True
                    logger.info(f"✅ DeepSeek вернул оценки: {unique_scores}")
                    
                    for c in filtered_candidates:
                        result = scores_by_id.get(c.id)
                        if result:
                            c.score = int(result.get("score", 0))
                            c.explanation = str(result.get("explanation", ""))
                        else:
                            # Fallback для кандидатов без оценки
                            c.score = 50
                            c.explanation = "Оценка не получена от API"
                else:
                    logger.warning(f"⚠️ DeepSeek вернул одинаковые оценки: {unique_scores}")
            except Exception as e:
                logger.error(f"❌ Ошибка DeepSeek API: {e}")
            
            # Если DeepSeek не сработал или вернул плохие оценки - используем локальный алгоритм
            if not deepseek_success:
                logger.info("📊 Используем локальный алгоритм оценки")
                for c in filtered_candidates:
                    score = 50
                    explanation_parts = []
                    
                    # 1. Город (до 20 баллов)
                    if vacancy.city.lower() in c.city.lower():
                        score += 20
                        explanation_parts.append("🏙️ Город совпадает: +20")
                    elif c.normalized_city and vacancy.city.lower() in c.normalized_city.lower():
                        score += 15
                        explanation_parts.append("🏙️ Город (нормализованный) совпадает: +15")
                    else:
                        score += 5
                        explanation_parts.append("🏙️ Город не совпадает: +5")
                    
                    # 2. Опыт (до 25 баллов)
                    if c.experience_years:
                        exp_years = c.experience_years
                        if exp_years >= 5:
                            score += 25
                            explanation_parts.append(f"💼 Опыт {exp_years} лет (эксперт): +25")
                        elif exp_years >= 3:
                            score += 20
                            explanation_parts.append(f"💼 Опыт {exp_years} лет (хорошо): +20")
                        elif exp_years >= 1:
                            score += 12
                            explanation_parts.append(f"💼 Опыт {exp_years} лет (начальный): +12")
                        else:
                            score += 5
                            explanation_parts.append(f"💼 Опыт {exp_years} лет (мало): +5")
                    else:
                        score += 10
                        explanation_parts.append("💼 Опыт не указан: +10")
                    
                    # 3. Навыки (до 25 баллов)
                    must_have = vacancy.must_have if vacancy.must_have and vacancy.must_have != "-" else ""
                    if must_have:
                        must_have_list = [skill.strip().lower() for skill in must_have.split(",")]
                        candidate_skills = (c.skills_text or "").lower()
                        found = sum(1 for skill in must_have_list if skill in candidate_skills)
                        if found > 0:
                            skill_points = min(25, int((found / len(must_have_list)) * 25))
                            score += skill_points
                            explanation_parts.append(f"📋 Найдено {found}/{len(must_have_list)} навыков: +{skill_points}")
                        else:
                            score += 5
                            explanation_parts.append("📋 Требуемые навыки не найдены: +5")
                    else:
                        score += 15
                        explanation_parts.append("📋 Нет требований к навыкам: +15")
                    
                    # 4. Зарплата (до 15 баллов)
                    if c.salary_expectations and (vacancy.salary_from or vacancy.salary_to):
                        salary_expected = c.salary_expectations
                        salary_min = vacancy.salary_from or 0
                        salary_max = vacancy.salary_to or float('inf')
                        if salary_min <= salary_expected <= salary_max:
                            score += 15
                            explanation_parts.append(f"💰 Зарплата {salary_expected} в вилке: +15")
                        elif salary_expected < salary_min:
                            score += 8
                            explanation_parts.append(f"💰 Зарплата ниже вилки: +8")
                        else:
                            score += 5
                            explanation_parts.append(f"💰 Зарплата выше вилки: +5")
                    else:
                        score += 7
                        explanation_parts.append("💰 Зарплата не указана: +7")
                    
                    # 5. Качество резюме (до 15 баллов)
                    text_length = len(c.raw_text or "")
                    if text_length > 500:
                        score += 15
                        explanation_parts.append("📝 Подробное резюме: +15")
                    elif text_length > 200:
                        score += 10
                        explanation_parts.append("📝 Хорошее резюме: +10")
                    elif text_length > 50:
                        score += 5
                        explanation_parts.append("📝 Короткое резюме: +5")
                    else:
                        score += 2
                        explanation_parts.append("📝 Очень краткое резюме: +2")
                    
                    c.score = min(100, score)
                    c.explanation = " | ".join(explanation_parts)
            
            # Применяем штрафы за красные флаги (для всех кандидатов)
            for c in filtered_candidates:
                if c.red_flags:
                    try:
                        penalty = get_red_flags_score(c.raw_text)
                        if penalty > 0:
                            old_score = c.score
                            c.score = max(0, c.score - penalty)
                            if c.explanation:
                                c.explanation += f" | 🚩 Штраф за красные флаги: -{penalty}"
                            else:
                                c.explanation = f"Штраф за красные флаги: -{penalty}"
                            logger.info(f"Кандидат {c.name_or_nick}: скор снижен с {old_score} до {c.score} (штраф {penalty})")
                    except:
                        pass
                
                logger.info(f"📊 Кандидат {c.name_or_nick}: оценка {c.score}/100")
                
                if c.score >= 80:
                    c.status = CandidateStatus.FILTERED.value
                elif c.score >= 60:
                    c.status = CandidateStatus.FOUND.value
                else:
                    c.status = CandidateStatus.REJECTED.value
                    if not c.rejection_reason:
                        c.rejection_reason = f"Низкая оценка: {c.score}/100"
            
            session.commit()
            logger.info("✅ Скоринг завершён")
        
        if payment_id and Payment is not None:
            try:
                payment = session.query(Payment).filter(Payment.id == payment_id).first()
                if payment is not None:
                    payment.candidates_used = added_count
                    session.commit()
            except Exception as e:
                logger.error(f"❌ Не удалось обновить платеж {payment_id}: {e}")

        # === АВТОМАТИЧЕСКИЕ ПРИГЛАШЕНИЯ ДЛЯ ТОП-КАНДИДАТОВ ===
        # Отправляем только в платном сценарии (когда есть payment_id).
        if filtered_candidates and payment_id is not None:
            await auto_invite_top_candidates(vacancy_id, company, vacancy)

        return added_count


async def gather_real_candidates_with_limit(vacancy_id: int, limit: int, payment_id: Optional[int] = None) -> int:
    return await gather_real_candidates(vacancy_id, limit=limit, payment_id=payment_id)


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


def _get_payable_candidates(vacancy_id: int) -> List[Candidate]:
    """Кандидаты, доступные для платного показа (без отсеянных)."""
    with get_session() as session:
        return (
            session.query(Candidate)
            .filter(Candidate.vacancy_id == vacancy_id, Candidate.status != CandidateStatus.REJECTED.value)
            .order_by(Candidate.score.desc(), Candidate.created_at.desc())
            .all()
        )


async def _send_candidates_page_for_vacancy(
    target: Message | CallbackQuery,
    user_id: int,
    vacancy_id: int,
    page: int,
    per_page: int = 5,
    max_candidates: Optional[int] = None,
) -> None:
    """Отправить одну страницу кандидатов по конкретной вакансии."""
    with get_session() as session:
        vacancy = (
            session.query(Vacancy)
            .join(Company, Vacancy.company_id == Company.id)
            .filter(Vacancy.id == vacancy_id, Company.owner_id == user_id)
            .one_or_none()
        )

    if not vacancy:
        if isinstance(target, CallbackQuery):
            await target.answer("Вакансия не найдена", show_alert=True)
        else:
            await target.answer("❌ Вакансия не найдена")
        return

    all_cands, top, mid, rest, rejected, clarify, qualified = group_candidates_for_report(vacancy.id)
    payable_candidates = [c for c in all_cands if c.status != CandidateStatus.REJECTED.value]
    if max_candidates is not None:
        payable_candidates = payable_candidates[:max_candidates]

    if not payable_candidates:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет кандидатов", show_alert=True)
        else:
            await target.answer("❌ Кандидаты не найдены")
        return

    total_pages = (len(payable_candidates) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    page_cands, has_next = candidates_page(payable_candidates, page=page, per_page=per_page)

    summary = (
        f"📊 <b>Вакансия: {vacancy.role} ({vacancy.city})</b>\n\n"
        f"Найдено кандидатов: {len(all_cands)}\n"
        f"Доступно к показу: {len(payable_candidates)}\n"
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
        if c.status != CandidateStatus.REJECTED.value:
            msg = target.message if isinstance(target, CallbackQuery) else target
            await msg.answer(
                build_candidate_card_text(c),
                parse_mode="HTML",
                reply_markup=build_candidate_keyboard(c.id),
                disable_web_page_preview=False,
            )

    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="◀ Пред.", callback_data=f"cand:{page - 1}")
    if has_next:
        kb.button(text="След. ▶", callback_data=f"cand:{page + 1}")
    if kb.buttons:
        msg = target.message if isinstance(target, CallbackQuery) else target
        await msg.answer("📌 Навигация:", reply_markup=kb.as_markup())


async def _send_candidates_page(
    target: Message | CallbackQuery,
    user_id: int,
    page: int,
    per_page: int = 5,
) -> None:
    """Отправить одну страницу кандидатов"""
    with get_session() as session:
        vacancy = (
            session.query(Vacancy)
            .join(Company, Vacancy.company_id == Company.id)
            .filter(Company.owner_id == user_id)
            .order_by(Vacancy.created_at.desc())
            .first()
        )
    if not vacancy:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет вакансий", show_alert=True)
        else:
            await target.answer("📭 Нет вакансий. Создайте: /new_job")
        return
    await _send_candidates_page_for_vacancy(target, user_id, vacancy.id, page, per_page)


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
        
        if not company:
            await message.answer("❌ Сначала пройдите онбординг: /onboarding")
            return
        
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
        
        if not company:
            await callback.answer("❌ Компания не найдена. Пройдите онбординг: /onboarding", show_alert=True)
            return
        
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
        
        company.filters_settings = filters_settings
        session.commit()
        
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
        
        total = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).count()
        with_flags = session.query(Candidate).filter(
            Candidate.vacancy_id == vacancy.id,
            Candidate.red_flags.isnot(None)
        ).count()
        
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
    """Экспорт отчёта по кандидатам"""
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
async def cb_export(callback: CallbackQuery):
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
        
        events = client.get_events(days=1)
        
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
            start_time = event['start']
            if 'T' in start_time:
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
            candidate.answers_schedule = message.text[:500]
            candidate.dialog_step = 2
            
            reply_text = generate_followup_message(candidate, vacancy, company, step=2)
            await send_message_to_candidate(candidate, reply_text)
            
        elif current_step == 2:
            candidate.answers_salary = message.text[:500]
            candidate.dialog_step = 3
            
            reply_text = generate_followup_message(candidate, vacancy, company, step=3)
            await send_message_to_candidate(candidate, reply_text)
            
        elif current_step == 3:
            candidate.answers_timing = message.text[:500]
            candidate.dialog_step = 4
            
            from pre_qualification import PreQualificationAnalyzer, format_qualification_results
            
            answers = {
                'schedule': candidate.answers_schedule or "",
                'salary': candidate.answers_salary or "",
                'timing': candidate.answers_timing or ""
            }
            
            analyzer = PreQualificationAnalyzer(candidate, vacancy, company)
            results = analyzer.analyze_all(answers)
            
            candidate.qualification_score = results['total_score']
            candidate.qualification_details = results
            candidate.qualification_date = datetime.now()
            candidate.qualification_history = results.get('history', [])
            candidate.extracted_keywords_from_answers = results.get('keywords', [])
            
            candidate.is_new = False
            
            if results['verdict'] == 'lead':
                candidate.status = CandidateStatus.QUALIFIED.value
                test_slots = _get_candidate_interview_slots(company)
                slots_data = []
                for slot in test_slots[:3]:
                    slots_data.append({
                        'start': slot['start'].isoformat(),
                        'end': slot['end'].isoformat(),
                        'text': slot['text']
                    })
                candidate.available_slots = slots_data

                if slots_data:
                    selected_slot = slots_data[0]
                    candidate.interview_slot_text = selected_slot['text']
                    candidate.status = CandidateStatus.INTERVIEW.value
                    candidate.dialog_step = 6
                    event, calendar_note = _create_calendar_event_for_candidate(company, vacancy, candidate, selected_slot)

                    reply_text = (
                        f"Отлично! Вы нам подходите, и я автоматически забронировал ближайшее время:\n\n"
                        f"📅 {selected_slot['text']}\n"
                        f"📍 {company.location}\n\n"
                        f"Если это время неудобно, просто напишите в ответ — подберу другое."
                        f"{calendar_note}"
                    )
                    admin_text = (
                        f"✅ <b>Автоназначение собеседования</b>\n\n"
                        f"Кандидат: {candidate.name_or_nick}\n"
                        f"Время: {selected_slot['text']}\n"
                    )
                    admin_text += "\n📅 Событие создано в календаре." if event else "\n📞 Подтвердите слот с кандидатом."
                    await bot.send_message(chat_id=company.owner_id, text=admin_text, parse_mode="HTML")
                else:
                    candidate.dialog_step = 4
                    reply_text = generate_followup_message(candidate, vacancy, company, step=4, slots=test_slots)
                
            elif results['verdict'] == 'clarify':
                candidate.status = CandidateStatus.CLARIFY.value
                
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
                candidate.dialog_step = 5
                
            else:
                candidate.status = CandidateStatus.REJECTED.value
                candidate.rejection_reason = "Не прошёл предквалификацию"
                reply_text = (
                    f"Спасибо за ответы! К сожалению, сейчас у нас нет подходящей "
                    f"вакансии для вас. Мы сохраним ваши контакты и свяжемся, "
                    f"когда появится подходящее предложение. Хорошего дня!"
                )
            
            await send_message_to_candidate(candidate, reply_text)
            
            admin_text = format_qualification_results(results)
            await bot.send_message(
                chat_id=company.owner_id,
                text=admin_text,
                parse_mode="HTML"
            )
            
        elif current_step == 4:
            try:
                choice = int(message.text.strip())
                if 1 <= choice <= 3 and candidate.available_slots:
                    slots_data = candidate.available_slots
                    if slots_data and len(slots_data) >= choice:
                        selected_slot = slots_data[choice - 1]
                        
                        candidate.interview_slot_text = selected_slot['text']
                        candidate.status = CandidateStatus.INTERVIEW.value
                        candidate.dialog_step = 6
                        
                        event, calendar_note = _create_calendar_event_for_candidate(company, vacancy, candidate, selected_slot)
                        
                        reply_text = generate_followup_message(candidate, vacancy, company, step=6) + calendar_note
                        await send_message_to_candidate(candidate, reply_text)
                        
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
            candidate.answers_clarify = message.text[:500]
            
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
                test_slots = _get_candidate_interview_slots(company)
                slots_data = []
                for slot in test_slots[:3]:
                    slots_data.append({
                        'start': slot['start'].isoformat(),
                        'end': slot['end'].isoformat(),
                        'text': slot['text']
                    })
                candidate.available_slots = slots_data

                if slots_data:
                    selected_slot = slots_data[0]
                    candidate.interview_slot_text = selected_slot['text']
                    candidate.status = CandidateStatus.INTERVIEW.value
                    candidate.dialog_step = 6
                    event, calendar_note = _create_calendar_event_for_candidate(company, vacancy, candidate, selected_slot)

                    reply_text = (
                        f"Спасибо за уточнение! Я автоматически поставил собеседование на ближайший слот:\n\n"
                        f"📅 {selected_slot['text']}\n"
                        f"📍 {company.location}\n\n"
                        f"Если время не подходит — напишите, перенесу."
                        f"{calendar_note}"
                    )
                    await send_message_to_candidate(candidate, reply_text)

                    admin_schedule_text = (
                        f"✅ <b>Автоназначение после уточнений</b>\n\n"
                        f"Кандидат: {candidate.name_or_nick}\n"
                        f"Время: {selected_slot['text']}\n"
                    )
                    admin_schedule_text += "\n📅 Событие создано в календаре." if event else "\n📞 Подтвердите слот с кандидатом."
                    await bot.send_message(chat_id=company.owner_id, text=admin_schedule_text, parse_mode="HTML")
                else:
                    candidate.dialog_step = 4
                    reply_text = generate_followup_message(candidate, vacancy, company, step=4, slots=test_slots)
                    await send_message_to_candidate(candidate, reply_text)
                
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
async def cb_search(callback: CallbackQuery):
    await callback.answer()
    await cmd_find(callback.message)


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
async def cmd_find(message: Message):
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


@router.message(Command("recalculate"))
async def cmd_recalculate(message: Message):
    """Пересчитать оценки всех кандидатов по текущей вакансии"""
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
        candidates = session.query(Candidate).filter(Candidate.vacancy_id == vacancy.id).all()
        
        status_msg = await message.answer(f"🔄 Пересчитываю оценки для {len(candidates)} кандидатов...")
        
        recalculated = 0
        for candidate in candidates:
            if candidate.status != CandidateStatus.REJECTED.value or candidate.score < 60:
                # Локальная функция оценки, чтобы не было зависимости от порядка объявления
                score = 50
                explanation_parts = []
                
                # 1. Город (20 баллов)
                if vacancy.city.lower() in candidate.city.lower():
                    score += 20
                    explanation_parts.append("🏙️ Город совпадает: +20")
                elif candidate.normalized_city and vacancy.city.lower() in candidate.normalized_city.lower():
                    score += 15
                    explanation_parts.append("🏙️ Город (нормализованный) совпадает: +15")
                else:
                    score += 5
                    explanation_parts.append("🏙️ Город не совпадает: +5")
                
                # 2. Опыт (25 баллов)
                if candidate.experience_years:
                    exp_years = candidate.experience_years
                    if exp_years >= 5:
                        score += 25
                        explanation_parts.append(f"💼 Опыт {exp_years} лет: +25")
                    elif exp_years >= 3:
                        score += 20
                        explanation_parts.append(f"💼 Опыт {exp_years} лет: +20")
                    elif exp_years >= 1:
                        score += 12
                        explanation_parts.append(f"💼 Опыт {exp_years} лет: +12")
                    else:
                        score += 5
                        explanation_parts.append(f"💼 Опыт {exp_years} лет: +5")
                else:
                    score += 10
                    explanation_parts.append("💼 Опыт не указан: +10")
                
                # 3. Навыки (25 баллов)
                must_have = vacancy.must_have if vacancy.must_have and vacancy.must_have != "-" else ""
                if must_have:
                    must_have_list = [skill.strip().lower() for skill in must_have.split(",")]
                    candidate_skills = (candidate.skills_text or "").lower()
                    found = sum(1 for skill in must_have_list if skill in candidate_skills)
                    if found > 0:
                        skill_points = min(25, int((found / len(must_have_list)) * 25))
                        score += skill_points
                        explanation_parts.append(f"📋 Найдено {found}/{len(must_have_list)} навыков: +{skill_points}")
                    else:
                        score += 5
                        explanation_parts.append("📋 Требуемые навыки не найдены: +5")
                else:
                    score += 15
                    explanation_parts.append("📋 Нет требований к навыкам: +15")
                
                # 4. Зарплата (15 баллов)
                if candidate.salary_expectations and (vacancy.salary_from or vacancy.salary_to):
                    salary_expected = candidate.salary_expectations
                    salary_min = vacancy.salary_from or 0
                    salary_max = vacancy.salary_to or float('inf')
                    if salary_min <= salary_expected <= salary_max:
                        score += 15
                        explanation_parts.append(f"💰 Зарплата {salary_expected} в вилке: +15")
                    elif salary_expected < salary_min:
                        score += 8
                        explanation_parts.append(f"💰 Зарплата ниже вилки: +8")
                    else:
                        score += 5
                        explanation_parts.append(f"💰 Зарплата выше вилки: +5")
                else:
                    score += 7
                    explanation_parts.append("💰 Зарплата не указана: +7")
                
                # 5. Качество резюме (15 баллов)
                text_length = len(candidate.raw_text or "")
                if text_length > 500:
                    score += 15
                    explanation_parts.append(f"📝 Подробное резюме: +15")
                elif text_length > 200:
                    score += 10
                    explanation_parts.append(f"📝 Хорошее резюме: +10")
                elif text_length > 50:
                    score += 5
                    explanation_parts.append(f"📝 Короткое резюме: +5")
                else:
                    score += 2
                    explanation_parts.append("📝 Очень краткое резюме: +2")
                
                # Штраф за красные флаги
                if candidate.red_flags:
                    try:
                        from filters import get_red_flags_score
                        penalty = get_red_flags_score(candidate.raw_text)
                        if penalty > 0:
                            score = max(0, score - penalty)
                            explanation_parts.append(f"🚩 Штраф за красные флаги: -{penalty}")
                    except:
                        pass
                
                candidate.score = min(100, score)
                candidate.explanation = " | ".join(explanation_parts)
                
                if candidate.score >= 80:
                    candidate.status = CandidateStatus.FILTERED.value
                elif candidate.score < 60:
                    candidate.status = CandidateStatus.REJECTED.value
                    candidate.rejection_reason = f"Низкая оценка после пересчёта: {candidate.score}/100"
                
                recalculated += 1
        
        session.commit()
        
        await status_msg.edit_text(
            f"✅ <b>Пересчёт завершён!</b>\n\n"
            f"📊 Пересчитано: {recalculated} кандидатов\n"
            f"🎯 Новая оценка учитывает:\n"
            f"• Город (до 20 баллов)\n"
            f"• Опыт (до 25 баллов)\n"
            f"• Навыки (до 25 баллов)\n"
            f"• Зарплату (до 15 баллов)\n"
            f"• Качество резюме (до 15 баллов)\n\n"
            f"Посмотреть обновлённые оценки: /candidates",
            parse_mode="HTML"
        )


TARIFFS = {
    "stars_test_1": {"label": "🧪 1 кандидат (тест)", "price": 1, "candidates": 1},
    "stars_2200_5": {"label": "🌟 5 кандидатов", "price": 2200, "candidates": 5},
    "stars_7450_15": {"label": "🚀 15 кандидатов", "price": 7450, "candidates": 15},
    "stars_13900_30": {"label": "💼 30 кандидатов", "price": 13900, "candidates": 30},
}


async def send_payment_invoice(user_id: int, tariff_key: str, vacancy_id: int) -> None:
    tariff = TARIFFS.get(tariff_key)
    if tariff is None:
        raise ValueError(f"Неизвестный тариф: {tariff_key}")
    # Для Telegram Stars (XTR) отдельный provider_token не требуется.
    # В Bot API для Stars используется пустая строка.
    provider_token = ""

    await bot.send_invoice(
        chat_id=user_id,
        title=f"Тариф {tariff['label']}",
        description=f"Поиск {tariff['candidates']} кандидатов",
        payload=f"vacancy:{vacancy_id}:{tariff_key}",
        provider_token=provider_token,
        currency="XTR",
        prices=[LabeledPrice(label=tariff["label"], amount=tariff["price"])],
        start_parameter="gwork_hr_bot",
    )


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload or ""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "vacancy":
        await message.answer("❌ Не удалось разобрать данные платежа")
        return

    vacancy_id = int(parts[1])
    tariff_key = parts[2]
    tariff = TARIFFS.get(tariff_key)
    if tariff is None:
        await message.answer("❌ Неизвестный тариф")
        return

    limit = tariff["candidates"]
    payment_id = None
    if Payment is not None:
        try:
            with get_session() as session:
                pay = Payment(
                    user_id=message.from_user.id,
                    vacancy_id=vacancy_id,
                    amount=tariff["price"],
                    currency="XTR",
                    tariff_key=tariff_key,
                    candidates_limit=limit,
                    candidates_used=0,
                    status=(PaymentStatus.COMPLETED.value if PaymentStatus is not None else "completed"),
                    telegram_payload=payload,
                    completed_at=datetime.now(),
                )
                session.add(pay)
                session.commit()
                payment_id = pay.id
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения платежа: {e}")

    await message.answer(f"✅ Оплачен тариф {tariff['label']}. Проверяю доступных кандидатов...")
    payable_candidates = _get_payable_candidates(vacancy_id)
    if not payable_candidates:
        await message.answer("🔍 Кандидаты ещё не найдены, запускаю поиск...")
        await gather_real_candidates(vacancy_id, limit=50, payment_id=payment_id)
        payable_candidates = _get_payable_candidates(vacancy_id)

    if not payable_candidates:
        await message.answer(
            "❌ После поиска подходящие кандидаты не найдены.\n"
            "Оплата сохранена, можно повторить поиск позже."
        )
        return

    deliver_count = min(limit, len(payable_candidates))
    await message.answer(
        f"✅ Готово! Показываю {deliver_count} из {len(payable_candidates)} найденных кандидатов."
    )
    await _send_candidates_page_for_vacancy(
        message,
        message.from_user.id,
        vacancy_id,
        page=0,
        max_candidates=deliver_count,
    )


async def main() -> None:
    global vk_thread, vk_own_loop, vk_bot_instance
    
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
    
    # Удаляем вебхук с принудительным сбросом ожидающих обновлений
    
    # ЗАПУСКАЕМ VK БОТА В ОТДЕЛЬНОМ ПОТОКЕ СО СВОИМ EVENT LOOP
    if settings.has_vk:
        vk_thread = threading.Thread(target=run_vk_bot_in_separate_loop, daemon=True)
        vk_thread.start()
        logger.info("📱 VK бот запущен в отдельном потоке со своим event loop'ом")
    else:
        logger.info("📱 VK бот не запущен (VK_TOKEN не настроен)")
    
    # Запускаем Telegram бота в основном потоке (главный event loop)
    logger.info("🤖 Запускаем Telegram бота в основном event loop...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
