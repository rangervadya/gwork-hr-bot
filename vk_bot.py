# vk_bot.py - Модуль для работы с VK ботом (токен сообщества)
import asyncio
import logging
import re
import traceback
from typing import List, Dict, Any, Optional
from datetime import datetime

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

from config import settings
from models import Candidate, CandidateStatus
from db import get_session
from vk_handlers import handle_vk_message

logger = logging.getLogger(__name__)


class VKBot:
    """Класс для работы с ботом ВКонтакте (токен сообщества)"""
    
    def __init__(self, token: str, group_id: int):
        """
        Инициализация VK бота
        
        Args:
            token: Токен доступа сообщества (из настроек группы)
            group_id: ID группы (обязателен)
        """
        self.token = token
        self.group_id = group_id
        self.vk_session = None
        self.vk = None
        self.longpoll = None
        self.running = False
        logger.info(f"🔄 VKBot: экземпляр создан для группы {group_id}")
        
    def auth(self) -> bool:
        """Авторизация в VK с токеном сообщества"""
        logger.info("🔄 VKBot.auth(): начало авторизации (токен сообщества)")
        logger.info(f"🔑 Токен (первые 10 символов): {self.token[:10] if self.token else 'НЕТ'}...")
        logger.info(f"📊 ID группы: {self.group_id}")
        
        try:
            logger.info("🔄 Создание VkApi с токеном сообщества...")
            self.vk_session = vk_api.VkApi(token=self.token)
            
            logger.info("🔄 Получение API...")
            self.vk = self.vk_session.get_api()
            
            # Проверяем доступ к API (для токена сообщества)
            try:
                logger.info("🔄 Проверка токена: получение информации о группе...")
                group_info = self.vk.groups.getById(group_id=self.group_id)
                if group_info and len(group_info) > 0:
                    group = group_info[0]
                    logger.info(f"✅ VK авторизован для группы: {group.get('name', 'Без названия')} (ID: {self.group_id})")
                else:
                    logger.warning("⚠️ Не удалось получить информацию о группе")
            except vk_api.exceptions.ApiError as e:
                logger.error(f"❌ Ошибка при проверке группы: {e}")
                if e.code == 5:
                    logger.error("❌ Ошибка авторизации: неверный токен сообщества")
                return False
            
            # Настраиваем LongPoll для группы
            logger.info(f"🔄 Настройка LongPoll для группы {self.group_id}...")
            try:
                self.longpoll = VkBotLongPoll(self.vk_session, self.group_id)
                logger.info(f"✅ VK LongPoll успешно настроен для группы {self.group_id}")
                logger.info(f"📊 LongPoll server: {self.longpoll.server}")
                logger.info(f"📊 LongPoll key: {self.longpoll.key[:10] if self.longpoll.key else 'None'}...")
            except vk_api.exceptions.ApiError as e:
                logger.error(f"❌ Ошибка VK API при настройке LongPoll: {e}")
                if e.code == 100:
                    logger.error("❌ LongPoll не включен в настройках группы. Включите LongPoll API в разделе 'Работа с API'")
                return False
            except Exception as e:
                logger.error(f"❌ Ошибка настройки LongPoll: {e}")
                traceback.print_exc()
                return False
            
            logger.info("✅ VKBot.auth(): авторизация успешно завершена")
            return True
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"❌ Ошибка авторизации VK (ApiError): {e}")
            logger.error(f"📊 Код ошибки: {e.code if hasattr(e, 'code') else 'неизвестно'}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка авторизации VK: {e}")
            traceback.print_exc()
            return False
    
    def send_message(self, user_id: int, message: str, keyboard: Optional[Dict] = None) -> bool:
        """
        Отправка сообщения пользователю от имени ГРУППЫ
        
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
            logger.info(f"✅ Сообщение отправлено пользователю {user_id} от имени группы {self.group_id}")
            return True
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"❌ Ошибка VK API при отправке сообщения {user_id}: {e}")
            if e.code == 901:
                logger.warning("⚠️ Пользователь запретил сообщения от сообщества")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения VK: {e}")
            return False
    
    def send_message_to_candidate(self, candidate: Candidate, message: str) -> bool:
        """Отправка сообщения кандидату через VK от имени группы"""
        try:
            contact = candidate.contact
            if not contact:
                logger.warning(f"⚠️ Нет контакта для кандидата {candidate.id}")
                return False
            
            logger.info(f"🔄 Отправка VK сообщения кандидату {candidate.id}, контакт: {contact}")
            
            vk_id_match = re.search(r'vk(\d+)|^(\d+)$', str(contact))
            if not vk_id_match:
                logger.info(f"⚠️ Контакт {contact} не является VK ID")
                return False
            
            vk_id = vk_id_match.group(1) or vk_id_match.group(2)
            if not vk_id:
                return False
            
            vk_id = int(vk_id)
            logger.info(f"✅ Извлечён VK ID: {vk_id}")
            
            success = self.send_message(vk_id, message)
            
            if success:
                with get_session() as session:
                    cand = session.query(Candidate).filter(Candidate.id == candidate.id).first()
                    if cand:
                        cand.last_message_sent = message[:500]
                        cand.last_message_at = datetime.now()
                        cand.last_activity_at = datetime.now()
                        session.commit()
                return True
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки VK сообщения: {e}")
            return False
    
    async def process_event(self, event):
        """Обработка события VK"""
        logger.info(f"📨 process_event: получен event типа {event.type}")
        
        if event.type == VkBotEventType.MESSAGE_NEW:
            message = event.object.message
            user_id = message['from_id']
            text = message.get('text', '').strip()
            payload = message.get('payload')
            
            logger.info(f"📨 VK сообщение от {user_id} в группу {self.group_id}: текст = '{text}'")
            
            return {
                'user_id': user_id,
                'text': text,
                'payload': payload,
                'message': message,
                'handled': False
            }
        
        logger.info(f"📨 Игнорируем событие типа: {event.type}")
        return None
    
    async def start_polling(self, message_handler=None):
        """Запуск long polling для получения сообщений"""
        if not self.vk_session or not self.vk:
            logger.error("❌ VK сессия не инициализирована")
            return
        
        if not self.longpoll:
            logger.error("❌ LongPoll не настроен")
            return
        
        self.running = True
        logger.info(f"🔄 VK LongPoll запущен для группы {self.group_id}, ожидание сообщений...")
        logger.info(f"📊 LongPoll server: {self.longpoll.server}")
        logger.info(f"📊 LongPoll key: {self.longpoll.key[:10] if self.longpoll.key else 'None'}...")
        
        try:
            for event in self.longpoll.listen():
                logger.info(f"📨 Получено событие: type={event.type}")
                
                if not self.running:
                    break
                
                if event.type == VkBotEventType.MESSAGE_NEW:
                    logger.info("📨 Это MESSAGE_NEW, обрабатываем...")
                    data = await self.process_event(event)
                    if message_handler and data:
                        try:
                            logger.info("📨 Вызываем message_handler...")
                            # Передаём только data (один аргумент)
                            await message_handler(data)
                            logger.info("✅ message_handler выполнен")
                        except Exception as e:
                            logger.error(f"❌ Ошибка в обработчике: {e}")
                            traceback.print_exc()
                else:
                    logger.info(f"📨 Игнорируем событие типа: {event.type}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка в VK LongPoll: {e}")
            traceback.print_exc()
            await asyncio.sleep(3)
        finally:
            self.running = False
            logger.info("🛑 VK LongPoll остановлен")
    
    def stop(self):
        self.running = False


# Создаём глобальный экземпляр VK бота
vk_bot = None


def init_vk_bot() -> Optional[VKBot]:
    """Инициализация VK бота с токеном сообщества"""
    global vk_bot
    
    logger.info("=" * 60)
    logger.info("📱 ИНИЦИАЛИЗАЦИЯ VK БОТА (токен сообщества)")
    logger.info("=" * 60)
    
    vk_token = getattr(settings, 'vk_token', None)
    vk_group_id = getattr(settings, 'vk_group_id', 0)
    
    if not vk_token:
        logger.warning("⚠️ VK_TOKEN не настроен в .env")
        return None
    
    if not vk_group_id:
        logger.warning("⚠️ VK_GROUP_ID не настроен в .env")
        logger.info("📱 Добавьте VK_GROUP_ID в переменные окружения (ID группы)")
        return None
    
    logger.info(f"✅ VK_TOKEN найден (первые символы: {vk_token[:10]}...)")
    logger.info(f"✅ VK_GROUP_ID: {vk_group_id}")
    
    try:
        vk_bot = VKBot(vk_token, vk_group_id)
        
        if vk_bot.auth():
            logger.info("=" * 60)
            logger.info("✅ VK БОТ УСПЕШНО ИНИЦИАЛИЗИРОВАН (от имени группы)")
            logger.info("=" * 60)
            return vk_bot
        else:
            logger.error("❌ Ошибка авторизации VK бота")
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации VK бота: {e}")
        traceback.print_exc()
        return None
