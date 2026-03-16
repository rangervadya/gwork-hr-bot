# export_utils.py
import csv
import io
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models import Candidate, Vacancy, Company

logger = logging.getLogger(__name__)


def generate_csv_report(candidates: List[Candidate], vacancy: Vacancy) -> str:
    """
    Генерирует CSV отчёт по кандидатам
    """
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Заголовки
    writer.writerow([
        'ID',
        'Имя',
        'Город',
        'Опыт',
        'Навыки',
        'Источник',
        'Оценка',
        'Статус',
        'Дата найден',
        'Телефон',
        'Email',
        'Telegram',
        'Результат предквалификации'
    ])
    
    # Данные
    for c in candidates:
        # Извлекаем контакты
        phone = ""
        email = ""
        telegram = ""
        
        if c.contact:
            if '@' in c.contact and '.' in c.contact:
                email = c.contact
            elif c.contact.startswith('@'):
                telegram = c.contact
            elif c.contact.replace('+', '').replace('-', '').replace(' ', '').isdigit():
                phone = c.contact
        
        writer.writerow([
            c.id,
            c.name_or_nick,
            c.city,
            c.experience_text[:100],
            c.skills_text[:100],
            c.source,
            c.score,
            c.status,
            c.created_at.strftime('%d.%m.%Y %H:%M') if c.created_at else '',
            phone,
            email,
            telegram,
            f"{c.qualification_score:.1f}" if c.qualification_score else 'Нет'
        ])
    
    return output.getvalue()


def generate_html_report(candidates: List[Candidate], vacancy: Vacancy, company: Company) -> str:
    """
    Генерирует HTML отчёт по кандидатам для отправки на почту
    """
    # Подсчёт статистики
    total = len(candidates)
    qualified = len([c for c in candidates if c.status == 'qualified'])
    interview = len([c for c in candidates if c.status == 'interview'])
    rejected = len([c for c in candidates if c.status == 'rejected'])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1 {{ color: #333; }}
            .stats {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th {{ background: #4CAF50; color: white; padding: 10px; text-align: left; }}
            td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
            tr:hover {{ background: #f5f5f5; }}
            .qualified {{ color: green; font-weight: bold; }}
            .interview {{ color: blue; font-weight: bold; }}
            .rejected {{ color: red; font-weight: bold; }}
            .new {{ background-color: #e8f5e8; }}
        </style>
    </head>
    <body>
        <h1>📊 Отчёт по вакансии: {vacancy.role}</h1>
        <p>Компания: {company.name_and_industry} | Город: {vacancy.city} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        
        <div class="stats">
            <h2>Статистика</h2>
            <p>Всего кандидатов: <b>{total}</b></p>
            <p>✅ Прошли предквалификацию: <b>{qualified}</b></p>
            <p>📅 Назначено собеседований: <b>{interview}</b></p>
            <p>❌ Отсеяно: <b>{rejected}</b></p>
        </div>
        
        <h2>Список кандидатов</h2>
        <table>
            <tr>
                <th>ID</th>
                <th>Имя</th>
                <th>Город</th>
                <th>Опыт</th>
                <th>Оценка</th>
                <th>Статус</th>
                <th>Дата</th>
                <th>Контакт</th>
            </tr>
    """
    
    for c in candidates[:50]:  # Ограничим 50 кандидатами для отчёта
        row_class = "new" if (datetime.now() - c.created_at) < timedelta(days=1) else ""
        status_class = ""
        if c.status == 'qualified':
            status_class = "qualified"
        elif c.status == 'interview':
            status_class = "interview"
        elif c.status == 'rejected':
            status_class = "rejected"
        
        html += f"""
            <tr class="{row_class}">
                <td>{c.id}</td>
                <td>{c.name_or_nick}</td>
                <td>{c.city}</td>
                <td>{c.experience_text[:50]}</td>
                <td>{c.score}</td>
                <td class="{status_class}">{c.status}</td>
                <td>{c.created_at.strftime('%d.%m.%Y') if c.created_at else ''}</td>
                <td>{c.contact}</td>
            </tr>
        """
    
    html += """
        </table>
        <p><small>Отчёт сгенерирован автоматически ботом GWork HR</small></p>
    </body>
    </html>
    """
    
    return html


def filter_by_date(candidates: List[Candidate], days: int = None) -> List[Candidate]:
    """
    Фильтрует кандидатов по дате (последние N дней)
    """
    if not days:
        return candidates
    
    cutoff_date = datetime.now() - timedelta(days=days)
    return [c for c in candidates if c.created_at and c.created_at >= cutoff_date]


def sort_candidates(candidates: List[Candidate], sort_by: str = 'score') -> List[Candidate]:
    """
    Сортирует кандидатов по различным критериям
    """
    if sort_by == 'date':
        return sorted(candidates, key=lambda x: x.created_at or datetime.min, reverse=True)
    elif sort_by == 'name':
        return sorted(candidates, key=lambda x: x.name_or_nick or '')
    elif sort_by == 'city':
        return sorted(candidates, key=lambda x: x.city or '')
    else:  # по умолчанию по скору
        return sorted(candidates, key=lambda x: x.score or 0, reverse=True)


def get_date_filter_keyboard() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для фильтра по дате
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Все", callback_data="date:all")
    kb.button(text="🆕 За 1 день", callback_data="date:1")
    kb.button(text="📆 За 3 дня", callback_data="date:3")
    kb.button(text="📅 За неделю", callback_data="date:7")
    kb.button(text="📅 За месяц", callback_data="date:30")
    kb.adjust(3, 2)
    return kb.as_markup()


def get_sort_keyboard() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для сортировки
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 По оценке", callback_data="sort:score")
    kb.button(text="📅 По дате", callback_data="sort:date")
    kb.button(text="👤 По имени", callback_data="sort:name")
    kb.button(text="📍 По городу", callback_data="sort:city")
    kb.adjust(2, 2)
    return kb.as_markup()