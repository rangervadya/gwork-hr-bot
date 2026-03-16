# yandex_calendar.py
import caldav
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
import uuid
import re

from config import settings

logger = logging.getLogger(__name__)

# URL CalDAV сервера Яндекса
YANDEX_CALDAV_URL = "https://caldav.yandex.ru"


class YandexCalendarClient:
    """Клиент для работы с Яндекс.Календарём через CalDAV"""
    
    def __init__(self, owner_id: int, username: str = None, password: str = None):
        """
        Инициализирует клиент для конкретного пользователя
        """
        self.owner_id = owner_id
        self.username = username or settings.yandex_login
        self.password = password or settings.yandex_app_password
        self.client = None
        self.calendar = None
        self._connect()
    
    def _connect(self):
        """Подключение к CalDAV серверу Яндекса"""
        try:
            # Подключаемся к серверу
            self.client = caldav.DAVClient(
                url=YANDEX_CALDAV_URL,
                username=self.username,
                password=self.password
            )
            
            # Получаем principal (основной календарь пользователя)
            principal = self.client.principal()
            
            # Находим календарь по умолчанию
            calendars = principal.calendars()
            if calendars:
                self.calendar = calendars[0]
                logger.info(f"✅ Подключен календарь: {self.calendar.name} для owner {self.owner_id}")
            else:
                # Если нет календаря, создаём
                self.calendar = principal.make_calendar(name="GWork HR Bot")
                logger.info(f"✅ Создан новый календарь для owner {self.owner_id}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к CalDAV: {e}")
            raise
    
    def get_free_slots(self, target_date: datetime.date, duration_minutes: int = 60, working_hours: tuple = (9, 18)) -> List[Dict[str, Any]]:
        """
        Получает свободные слоты на указанную дату
        
        Args:
            target_date: Дата для поиска
            duration_minutes: Длительность слота в минутах
            working_hours: Рабочие часы (начало, конец)
        
        Returns:
            Список свободных слотов с временем начала и конца
        """
        try:
            # Начало и конец рабочего дня
            tz = timezone(timedelta(hours=3))  # Московское время
            start_of_day = datetime.combine(
                target_date, datetime.min.time(), tzinfo=tz
            ).replace(hour=working_hours[0])
            end_of_day = datetime.combine(
                target_date, datetime.min.time(), tzinfo=tz
            ).replace(hour=working_hours[1])
            
            # Получаем все события за день
            events = self.calendar.date_search(
                start=start_of_day,
                end=end_of_day,
                expand=True
            )
            
            # Парсим занятые слоты
            busy_slots = []
            for event in events:
                # Получаем данные события
                event_data = event.vobject_instance.vevent
                event_start = event_data.dtstart.value
                event_end = event_data.dtend.value
                
                # Приводим к timezone-aware
                if event_start.tzinfo is None:
                    event_start = event_start.replace(tzinfo=tz)
                if event_end.tzinfo is None:
                    event_end = event_end.replace(tzinfo=tz)
                
                busy_slots.append({
                    'start': event_start,
                    'end': event_end,
                    'summary': event_data.summary.value if hasattr(event_data, 'summary') else 'Занято'
                })
            
            # Сортируем занятые слоты по времени
            busy_slots.sort(key=lambda x: x['start'])
            
            # Генерируем свободные слоты
            free_slots = []
            current_time = start_of_day
            
            while current_time + timedelta(minutes=duration_minutes) <= end_of_day:
                slot_end = current_time + timedelta(minutes=duration_minutes)
                is_free = True
                
                for busy in busy_slots:
                    if not (slot_end <= busy['start'] or current_time >= busy['end']):
                        is_free = False
                        # Прыгаем к концу занятого слота
                        current_time = busy['end']
                        break
                
                if is_free:
                    free_slots.append({
                        'start': current_time,
                        'end': slot_end,
                        'text': f"{current_time.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}"
                    })
                    current_time = slot_end
                else:
                    continue
            
            logger.info(f"📅 На {target_date.strftime('%d.%m.%Y')} найдено {len(free_slots)} свободных слотов")
            return free_slots
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения слотов: {e}")
            return []
    
    def create_event(
        self,
        summary: str,
        description: str,
        location: str,
        start_time: datetime,
        end_time: datetime,
        attendees: List[str] = None,
        reminders: List[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Создаёт событие в календаре
        
        Args:
            summary: Название события
            description: Описание
            location: Место проведения
            start_time: Время начала
            end_time: Время окончания
            attendees: Список email участников
            reminders: Напоминания в минутах до события
        
        Returns:
            Информация о созданном событии
        """
        try:
            # Создаём уникальный идентификатор для события
            event_uid = str(uuid.uuid4())
            
            # Формируем описание с участниками
            full_description = description
            if attendees:
                full_description += f"\n\nУчастники: {', '.join(attendees)}"
            
            # Формируем напоминания
            alarm_text = ""
            if reminders:
                for minutes in reminders:
                    alarm_text += f"""
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Напоминание
TRIGGER:-PT{minutes}M
END:VALARM"""
            
            # Создаём событие в формате iCalendar
            event_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//GWork HR Bot//EN
BEGIN:VEVENT
UID:{event_uid}
DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}
DTSTART:{start_time.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_time.strftime('%Y%m%dT%H%M%S')}
SUMMARY:{summary}
LOCATION:{location}
DESCRIPTION:{full_description}{alarm_text}
END:VEVENT
END:VCALENDAR"""
            
            # Сохраняем событие в календарь
            self.calendar.save_event(event_data)
            
            logger.info(f"✅ Событие создано: {summary} на {start_time.strftime('%d.%m.%Y %H:%M')}")
            return {
                'id': event_uid,
                'summary': summary,
                'start': start_time.isoformat(),
                'end': end_time.isoformat(),
                'location': location
            }
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания события: {e}")
            return None
    
    def get_events(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Получает события на ближайшие дни
        
        Args:
            days: Количество дней для поиска
        
        Returns:
            Список событий
        """
        try:
            tz = timezone(timedelta(hours=3))
            now = datetime.now(tz)
            later = now + timedelta(days=days)
            
            events = self.calendar.date_search(
                start=now,
                end=later,
                expand=True
            )
            
            result = []
            for event in events:
                event_data = event.vobject_instance.vevent
                event_start = event_data.dtstart.value
                event_end = event_data.dtend.value
                event_summary = event_data.summary.value if hasattr(event_data, 'summary') else "Без названия"
                
                # Приводим к строке
                if hasattr(event_start, 'isoformat'):
                    start_str = event_start.isoformat()
                else:
                    start_str = str(event_start)
                
                result.append({
                    'summary': event_summary,
                    'start': start_str,
                    'end': event_end.isoformat() if hasattr(event_end, 'isoformat') else str(event_end),
                    'uid': event_data.uid.value if hasattr(event_data, 'uid') else None
                })
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения событий: {e}")
            return []
    
    def delete_event(self, event_uid: str) -> bool:
        """
        Удаляет событие из календаря
        
        Args:
            event_uid: ID события
        
        Returns:
            True если успешно, False если нет
        """
        try:
            # Ищем событие по UID
            events = self.calendar.date_search(
                start=datetime.now() - timedelta(days=30),
                end=datetime.now() + timedelta(days=30)
            )
            
            for event in events:
                event_data = event.vobject_instance.vevent
                if hasattr(event_data, 'uid') and event_data.uid.value == event_uid:
                    event.delete()
                    logger.info(f"✅ Событие {event_uid} удалено")
                    return True
            
            logger.warning(f"⚠️ Событие {event_uid} не найдено")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка удаления события: {e}")
            return False