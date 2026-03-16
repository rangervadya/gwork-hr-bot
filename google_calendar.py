# google_calendar.py
import os
import pickle
import datetime
from typing import Optional, List, Dict, Any
from datetime import timezone, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import settings
import logging

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarClient:
    """Клиент для работы с Google Calendar API"""
    
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        self.token_file = f"token_{owner_id}.pickle"
        self.creds = None
        self.service = None
        self._authenticate()
    
    def _authenticate(self):
        """Аутентификация и сохранение токена"""
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                self.creds = pickle.load(token)
        
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    raise FileNotFoundError("credentials.json не найден")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES
                )
                self.creds = flow.run_local_server(port=0)
            
            with open(self.token_file, 'wb') as token:
                pickle.dump(self.creds, token)
        
        self.service = build('calendar', 'v3', credentials=self.creds)
        logger.info(f"✅ Google Calendar аутентифицирован для owner {self.owner_id}")
    
    def create_event(
        self,
        summary: str,
        description: str,
        location: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        attendees: List[str] = None,
        timezone: str = 'Europe/Moscow'
    ) -> Optional[Dict[str, Any]]:
        """
        Создаёт событие в календаре
        """
        try:
            start = {
                'dateTime': start_time.isoformat(),
                'timeZone': timezone,
            }
            end = {
                'dateTime': end_time.isoformat(),
                'timeZone': timezone,
            }
            
            event_body = {
                'summary': summary,
                'location': location,
                'description': description,
                'start': start,
                'end': end,
            }
            
            if attendees:
                event_body['attendees'] = [{'email': email} for email in attendees]
                event_body['reminders'] = {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 60},
                        {'method': 'popup', 'minutes': 30},
                    ],
                }
            
            event = self.service.events().insert(
                calendarId='primary',
                body=event_body,
                sendUpdates='all'
            ).execute()
            
            logger.info(f"✅ Событие создано: {event.get('htmlLink')}")
            return {
                'id': event.get('id'),
                'link': event.get('htmlLink'),
                'summary': event.get('summary'),
                'start': event.get('start'),
            }
            
        except HttpError as error:
            logger.error(f"❌ Ошибка создания события: {error}")
            return None
    
    def get_events(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Получает события на ближайшие дни
        """
        try:
            now = datetime.datetime.now(timezone.utc).isoformat()
            later = (datetime.datetime.now(timezone.utc) + 
                    timedelta(days=days)).isoformat()
            
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now,
                timeMax=later,
                maxResults=50,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            if not events:
                logger.info("📭 Нет событий на ближайшие дни")
                return []
            
            formatted_events = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                formatted_events.append({
                    'id': event.get('id'),
                    'summary': event.get('summary'),
                    'start': start,
                    'link': event.get('htmlLink'),
                })
            
            return formatted_events
            
        except HttpError as error:
            logger.error(f"❌ Ошибка получения событий: {error}")
            return []
    
    def get_free_slots(self, target_date: datetime.date, duration_minutes: int = 60) -> List[Dict[str, Any]]:
        """
        Получает свободные слоты на указанную дату
        """
        try:
            # Начало и конец рабочего дня (9:00 - 18:00)
            start_of_day = datetime.datetime.combine(
                target_date, datetime.time(9, 0), tzinfo=timezone(timedelta(hours=3))
            )
            end_of_day = datetime.datetime.combine(
                target_date, datetime.time(18, 0), tzinfo=timezone(timedelta(hours=3))
            )
            
            body = {
                "timeMin": start_of_day.isoformat(),
                "timeMax": end_of_day.isoformat(),
                "items": [{"id": "primary"}]
            }
            
            free_busy = self.service.freebusy().query(body=body).execute()
            busy_slots = free_busy['calendars']['primary'].get('busy', [])
            
            free_slots = []
            current_time = start_of_day
            
            while current_time + timedelta(minutes=duration_minutes) <= end_of_day:
                slot_end = current_time + timedelta(minutes=duration_minutes)
                is_free = True
                
                for busy in busy_slots:
                    busy_start = datetime.datetime.fromisoformat(busy['start'])
                    busy_end = datetime.datetime.fromisoformat(busy['end'])
                    
                    if not (slot_end <= busy_start or current_time >= busy_end):
                        is_free = False
                        current_time = busy_end
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
            
            return free_slots
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения свободных слотов: {e}")
            return []
    
    def delete_event(self, event_id: str) -> bool:
        """Удаляет событие из календаря"""
        try:
            self.service.events().delete(
                calendarId='primary',
                eventId=event_id
            ).execute()
            logger.info(f"✅ Событие {event_id} удалено")
            return True
        except HttpError as error:
            logger.error(f"❌ Ошибка удаления события: {error}")
            return False