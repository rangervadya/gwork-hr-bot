# vk_bot.py - Модуль для работы с VK ботом
import asyncio
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

from config import settings
from models import Candidate, CandidateStatus
from db import get_session

logger = logging.getLogger(__name__)


class VKBot:
    """Класс для работы с ботом ВКонтакте"""
    
    def __init__(self, token: str, group_id: Optional[int] = None):
        """
        Инициализация VK бота
        
        Args:
            token: Токен доступа VK API
            group_id: ID группы (если не указан, будет получен автоматически)
        """
        self.token = token
        self.group_id = group_id
        self.vk_session = None
        self.vk = None
        self.longpoll = None
        self.running = False
        
    def auth(self) -> bool:
        """Авторизация в VK"""
        try:
            self.vk_session = vk_api.VkApi(token=self.token)
            self.vk = self.vk_session.get_api()
            
            # Получаем информацию о группе, если group_id не указан
            if not self.group_id:
                groups = self.vk.groups.get()
                if groups and groups['items']:
                    self.group_id = groups['items'][0]
                else:
                    logger.error("❌ Не удалось определить ID группы")
                    return False
            
            self.longpoll = VkBotLongPoll(self.vk_session, self.group_id)
            logger.info(f"✅ VK бот авторизован для группы {self.group_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка авторизации VK: {e}")
            return False
    
    def send_message(self, user_id: int, message: str, keyboard: Optional[Dict] = None) -> bool:
        """
        Отправка сообщения пользователю
        
        Args:
            user_id: ID пользователя
            message: Текст сообщения
            keyboard: Клавиатура (опционально)
        """
        try:
            params = {
                'user_id': user_id,
                'message': message,
                'random_id': get_random_id()
            }
            if keyboard:
                params['keyboard'] = keyboard
            
            self.vk.messages.send(**params)
            logger.info(f"✅ Сообщение отправлено пользователю {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения VK: {e}")
            return False
    
    def send_message_to_candidate(self, candidate: Candidate, message: str) -> bool:
        """
        Отправка сообщения кандидату через VK
        
        Args:
            candidate: Объект кандидата
            message: Текст сообщения
        """
        try:
            # Проверяем, что контакт - это VK ID
            contact = candidate.contact
            if not contact:
                logger.warning(f"Нет контакта для кандидата {candidate.id}")
                return False
            
            # Ожидаем, что контакт в формате "vk123456" или просто "123456"
            vk_id_match = re.search(r'vk(\d+)|^(\d+)$', contact)
            if not vk_id_match:
                logger.info(f"Контакт {contact} не является VK ID")
                return False
            
            vk_id = vk_id_match.group(1) or vk_id_match.group(2)
            if not vk_id:
                return False
            
            vk_id = int(vk_id)
            
            # Отправляем сообщение
            success = self.send_message(vk_id, message)
            
            if success:
                # Обновляем информацию о кандидате
                with get_session() as session:
                    cand = session.query(Candidate).filter(Candidate.id == candidate.id).first()
                    if cand:
                        cand.last_message_sent = message[:500]
                        cand.last_message_at = datetime.now()
                        cand.last_activity_at = datetime.now()
                        session.commit()
            
            return success
            
        except Exception as e:
            logger.error(f"Ошибка отправки VK сообщения: {e}")
            return False
    
    def get_user_info(self, user_id: int) -> Optional[Dict]:
        """Получение информации о пользователе VK"""
        try:
            users = self.vk.users.get(
                user_ids=user_id,
                fields='first_name,last_name,domain,photo_100'
            )
            if users:
                return users[0]
            return None
        except Exception as e:
            logger.error(f"Ошибка получения информации о пользователе {user_id}: {e}")
            return None
    
    def create_keyboard(self, buttons: List[List[Dict]]) -> Dict:
        """
        Создание клавиатуры для VK
        
        Args:
            buttons: Список рядов кнопок
        """
        return {
            'inline': False,
            'buttons': buttons
        }
    
    def create_inline_keyboard(self, buttons: List[List[Dict]]) -> Dict:
        """
        Создание inline клавиатуры для VK
        
        Args:
            buttons: Список рядов кнопок
        """
        return {
            'inline': True,
            'buttons': buttons
        }
    
    def create_button(self, label: str, color: str = 'primary', payload: Optional[Dict] = None) -> Dict:
        """
        Создание кнопки
        
        Args:
            label: Текст кнопки
            color: Цвет (primary, secondary, positive, negative)
            payload: Дополнительные данные
        """
        button = {
            'action': {
                'type': 'text',
                'label': label
            },
            'color': color
        }
        if payload:
            button['action']['payload'] = payload
        return button
    
    def create_link_button(self, label: str, link: str) -> Dict:
        """Создание кнопки-ссылки"""
        return {
            'action': {
                'type': 'open_link',
                'link': link,
                'label': label
            }
        }
    
    async def process_event(self, event):
        """Обработка события VK"""
        if event.type == VkBotEventType.MESSAGE_NEW:
            message = event.object.message
            user_id = message['from_id']
            text = message.get('text', '')
            payload = message.get('payload')
            
            logger.info(f"📨 VK сообщение от {user_id}: {text[:50]}...")
            
            # Здесь будет логика обработки сообщений от кандидатов
            # Вызывается из основного бота
            
            return {
                'user_id': user_id,
                'text': text,
                'payload': payload,
                'message': message
            }
        
        return None
    
    async def start_polling(self, message_handler=None):
        """
        Запуск long polling для получения сообщений
        
        Args:
            message_handler: Функция-обработчик сообщений
        """
        if not self.auth():
            logger.error("❌ Не удалось авторизоваться в VK")
            return
        
        self.running = True
        logger.info("🔄 VK LongPoll запущен, ожидание сообщений...")
        
        try:
            for event in self.longpoll.listen():
                if not self.running:
                    break
                
                if event.type == VkBotEventType.MESSAGE_NEW:
                    data = await self.process_event(event)
                    
                    if message_handler and data:
                        # Вызываем обработчик из основного бота
                        await message_handler(data)
                        
        except Exception as e:
            logger.error(f"❌ Ошибка в VK LongPoll: {e}")
        finally:
            self.running = False
            logger.info("🛑 VK LongPoll остановлен")
    
    def stop(self):
        """Остановка бота"""
        self.running = False
        logger.info("🛑 VK бот остановлен")


# Создаём глобальный экземпляр VK бота
vk_bot = None


def init_vk_bot() -> Optional[VKBot]:
    """Инициализация VK бота"""
    global vk_bot
    
    vk_token = getattr(settings, 'vk_token', None)
    if not vk_token:
        logger.warning("⚠️ VK_TOKEN не настроен в .env")
        return None
    
    try:
        vk_bot = VKBot(vk_token)
        if vk_bot.auth():
            logger.info("✅ VK бот инициализирован")
            return vk_bot
        else:
            logger.error("❌ Не удалось инициализировать VK бот")
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации VK бота: {e}")
        return None
