import calendar
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import aiohttp
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

class CalendarService:
    def __init__(self):
        self.available_slots = {
            'monday': ['10:00', '11:00', '12:00', '14:00', '15:00', '16:00'],
            'tuesday': ['10:00', '11:00', '12:00', '14:00', '15:00', '16:00'],
            'wednesday': ['10:00', '11:00', '12:00', '14:00', '15:00', '16:00'],
            'thursday': ['10:00', '11:00', '12:00', '14:00', '15:00', '16:00'],
            'friday': ['10:00', '11:00', '12:00', '14:00', '15:00', '16:00'],
            'saturday': ['11:00', '12:00', '13:00'],
            'sunday': ['12:00', '13:00']
        }
    
    def generate_calendar(self, year: int = None, month: int = None) -> Dict:
        """Генерирует календарь на месяц"""
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        
        # Создаем календарь
        cal = calendar.Calendar()
        month_days = cal.monthdayscalendar(year, month)
        
        # Формируем удобный формат
        month_name = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                     'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'][month-1]
        
        return {
            'year': year,
            'month': month,
            'month_name': month_name,
            'days': month_days,
            'today': now.day if now.year == year and now.month == month else None
        }
    
    def create_calendar_keyboard(self, year: int, month: int) -> InlineKeyboardMarkup:
        """Создает клавиатуру с календарем"""
        cal_data = self.generate_calendar(year, month)
        
        # Создаем кнопки для заголовка (месяц и год)
        keyboard = []
        
        # Заголовок с месяцем и годом + кнопки навигации
        header_row = []
        # Кнопка предыдущий месяц
        prev_month, prev_year = self.get_previous_month(year, month)
        header_row.append(InlineKeyboardButton(
            text="◀️",
            callback_data=f"calendar_prev_{prev_year}_{prev_month}"
        ))
        
        # Месяц и год
        header_row.append(InlineKeyboardButton(
            text=f"{cal_data['month_name']} {year}",
            callback_data=f"calendar_title_{year}_{month}"
        ))
        
        # Кнопка следующий месяц
        next_month, next_year = self.get_next_month(year, month)
        header_row.append(InlineKeyboardButton(
            text="▶️",
            callback_data=f"calendar_next_{next_year}_{next_month}"
        ))
        
        keyboard.append(header_row)
        
        # Дни недели
        weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        keyboard.append([
            InlineKeyboardButton(text=day, callback_data=f"calendar_weekday_{day}")
            for day in weekdays
        ])
        
        # Дни месяца
        for week in cal_data['days']:
            week_row = []
            for day in week:
                if day == 0:
                    # Пустая кнопка для дней из других месяцев
                    week_row.append(InlineKeyboardButton(text=" ", callback_data="calendar_empty"))
                else:
                    # Проверяем доступность даты (не прошедшие дни)
                    date_obj = datetime(year, month, day)
                    is_past = date_obj.date() < datetime.now().date()
                    
                    if is_past:
                        week_row.append(InlineKeyboardButton(
                            text=f"❌{day}",
                            callback_data=f"calendar_past_{year}_{month}_{day}"
                        ))
                    else:
                        week_row.append(InlineKeyboardButton(
                            text=f"📅{day}" if day == cal_data['today'] else str(day),
                            callback_data=f"calendar_day_{year}_{month}_{day}"
                        ))
            keyboard.append(week_row)
        
        # Кнопка "Сегодня"
        keyboard.append([
            InlineKeyboardButton(text="🗓️ Сегодня", callback_data=f"calendar_today_{now.year}_{now.month}_{now.day}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="calendar_cancel")
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    def get_available_time_slots(self, date_obj: datetime) -> List[str]:
        """Получает доступные временные слоты для даты"""
        weekday_name = self.get_weekday_name(date_obj.weekday())
        
        if weekday_name in self.available_slots:
            # Фильтруем прошедшее время для сегодняшнего дня
            if date_obj.date() == datetime.now().date():
                current_time = datetime.now().time()
                available_slots = []
                for slot in self.available_slots[weekday_name]:
                    slot_time = datetime.strptime(slot, "%H:%M").time()
                    if slot_time > current_time:
                        available_slots.append(slot)
                return available_slots
            return self.available_slots[weekday_name]
        
        return []
    
    def create_time_keyboard(self, date_obj: datetime) -> InlineKeyboardMarkup:
        """Создает клавиатуру с выбором времени"""
        slots = self.get_available_time_slots(date_obj)
        
        if not slots:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Нет доступных слотов", callback_data="time_none")
            ]])
        
        # Группируем слоты по 3 в ряд
        keyboard = []
        row = []
        for i, slot in enumerate(slots, 1):
            row.append(InlineKeyboardButton(
                text=f"🕐 {slot}",
                callback_data=f"time_slot_{slot}"
            ))
            if i % 3 == 0:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        # Кнопки навигации
        keyboard.append([
            InlineKeyboardButton(text="◀️ Назад к календарю", callback_data="time_back_to_calendar"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="time_cancel")
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    def get_weekday_name(self, weekday_num: int) -> str:
        """Преобразует номер дня недели в название"""
        weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        return weekdays[weekday_num]
    
    def get_previous_month(self, year: int, month: int) -> Tuple[int, int]:
        """Возвращает предыдущий месяц"""
        if month == 1:
            return 12, year - 1
        else:
            return month - 1, year
    
    def get_next_month(self, year: int, month: int) -> Tuple[int, int]:
        """Возвращает следующий месяц"""
        if month == 12:
            return 1, year + 1
        else:
            return month + 1, year
    
    def format_interview_date(self, date_str: str, time_str: str) -> str:
        """Форматирует дату собеседования"""
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        weekday_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        month_names = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        
        weekday = weekday_names[date_obj.weekday()]
        month = month_names[date_obj.month - 1]
        
        return f"{weekday}, {date_obj.day} {month} {date_obj.year} в {time_str}"
    
    async def generate_interview_message(self, candidate_name: str, company_info: Dict, 
                                       position: str, date_str: str, time_str: str) -> str:
        """Генерирует сообщение с приглашением на собеседование"""
        company_name = company_info.get('company_name', 'Наша компания')
        formatted_date = self.format_interview_date(date_str, time_str)
        
        message = f"🎉 *Приглашение на собеседование!*\n\n"
        message += f"👤 Для: {candidate_name}\n"
        message += f"🏢 Компания: {company_name}\n"
        message += f"💼 Должность: {position}\n"
        message += f"📅 Дата и время: {formatted_date}\n\n"
        message += f"📍 Место: {company_info.get('city', 'город')}, {company_info.get('address', 'адрес будет отправлен познее')}\n\n"
        message += f"📞 Контактное лицо: Менеджер по подбору персонала\n"
        message += f"📱 Телефон: {company_info.get('phone', 'будет отправлен в личном сообщении')}\n\n"
        message += f"📋 *Что нужно взять с собой:*\n"
        message += f"• Паспорт или иной документ, удостоверяющий личность\n"
        message += f"• Резюме (если есть)\n"
        message += f"• Ручку для заполнения документов\n\n"
        message += f"💬 *Подтвердите, пожалуйста, ваше участие, ответив на это сообщение.*\n"
        message += f"Если у вас возникли вопросы или нужно перенести встречу, сообщите об этом.\n\n"
        message += f"Ждем вас! 🤝"
        
        return message
    
    def get_upcoming_interviews(self, user_id: int, days_ahead: int = 7) -> List[Dict]:
        """Получает предстоящие собеседования (заглушка)"""
        # Здесь будет интеграция с БД
        # Пока возвращаем тестовые данные
        return [
            {
                'id': 1,
                'candidate_name': 'Анна Иванова',
                'position': 'Администратор',
                'date': '2024-12-20',
                'time': '14:00',
                'status': 'confirmed'
            },
            {
                'id': 2,
                'candidate_name': 'Мария Петрова',
                'position': 'Бариста',
                'date': '2024-12-21',
                'time': '11:00',
                'status': 'pending'
            }
        ]

# Глобальный экземпляр сервиса календаря
calendar_service = CalendarService()