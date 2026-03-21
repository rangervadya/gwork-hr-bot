# vk_bot.py - Модуль для работы с VK ботом
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
        logger.info("🔄 VKBot: экземпляр создан")
        
    def auth(self) -> bool:
        """Авторизация в VK"""
        logger.info("🔄 VKBot.auth(): начало авторизации")
        logger.info(f"🔑 Токен (первые 10 символов): {self.token[:10] if self.token else 'НЕТ'}...")
        
        try:
            logger.info("🔄 Создание VkApi с токеном...")
            self.vk_session = vk_api.VkApi(token=self.token)
            
            logger.info("🔄 Получение API...")
            self.vk = self.vk_session.get_api()
            
            # Проверяем доступ к API
            try:
                logger.info("🔄 Проверка токена: получение информации о пользователе...")
                user_info = self.vk.users.get()
                if user_info and len(user_info) > 0:
                    user = user_info[0]
                    logger.info(f"✅ VK авторизован как пользователь: {user.get('first_name', '')} {user.get('last_name', '')} (ID: {user.get('id', 'неизвестно')})")
                else:
                    logger.warning("⚠️ Не удалось получить информацию о пользователе, но токен может работать")
            except vk_api.exceptions.ApiError as e:
                logger.error(f"❌ Ошибка при проверке пользователя: {e}")
                if e.code == 5:
                    logger.error("❌ Ошибка авторизации: неверный токен или истек срок действия")
                return False
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка при проверке пользователя: {e}")
                return False
            
            # Получаем список групп пользователя
            logger.info("🔄 Получение списка групп пользователя...")
            try:
                groups = self.vk.groups.get(extended=1, filter='admin')
                logger.info(f"✅ Получен ответ groups.get: найдено групп: {len(groups.get('items', [])) if groups else 0}")
                
                if groups and groups.get('items') and len(groups['items']) > 0:
                    # Берём первую группу, где пользователь администратор
                    group = groups['items'][0]
                    self.group_id = group['id']
                    group_name = group.get('name', 'Без названия')
                    logger.info(f"✅ Бот будет работать от имени группы: {group_name} (ID: {self.group_id})")
                    logger.info(f"📊 Информация о группе: {group}")
                else:
                    logger.error("❌ У пользователя нет групп с правами администратора")
                    logger.info(f"📊 Ответ groups: {groups}")
                    
                    # Пробуем получить список групп без фильтра admin
                    try:
                        logger.info("🔄 Пробуем получить все группы пользователя...")
                        all_groups = self.vk.groups.get(extended=1)
                        logger.info(f"✅ Найдено всех групп: {len(all_groups.get('items', [])) if all_groups else 0}")
                        if all_groups and all_groups.get('items'):
                            for g in all_groups['items']:
                                logger.info(f"📊 Группа: {g.get('name')} (ID: {g.get('id')}) - админ: {g.get('is_admin', False)}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка при получении всех групп: {e}")
                    
                    return False
            except vk_api.exceptions.ApiError as e:
                logger.error(f"❌ Ошибка API при получении списка групп: {e}")
                if e.code == 15:
                    logger.error("❌ Нет прав доступа к группам. Токен должен иметь права groups")
                elif e.code == 27:
                    logger.error("❌ Ошибка авторизации группы. Используйте пользовательский токен, а не токен сообщества.")
                return False
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка при получении групп: {e}")
                traceback.print_exc()
                return False
            
            # Настраиваем LongPoll для группы
            logger.info(f"🔄 Настройка LongPoll для группы {self.group_id}...")
            try:
                from vk_api.bot_longpoll import VkBotLongPoll
                self.longpoll = VkBotLongPoll(self.vk_session, self.group_id)
                logger.info(f"✅ VK LongPoll успешно настроен для группы {self.group_id}")
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
            if e.code == 5:
                logger.error("❌ Неверный токен или истек срок действия. Получите новый токен.")
            elif e.code == 15:
                logger.error("❌ Недостаточно прав доступа. Проверьте scope токена.")
            elif e.code == 27:
                logger.error("❌ Ошибка авторизации группы. Используйте пользовательский токен, а не токен сообщества.")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка авторизации VK: {e}")
            traceback.print_exc()
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
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"❌ Ошибка VK API при отправке сообщения {user_id}: {e}")
            if e.code == 901:
                logger.warning("⚠️ Пользователь запретил сообщения от сообщества")
            return False
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
                logger.warning(f"⚠️ Нет контакта для кандидата {candidate.id}")
                return False
            
            logger.info(f"🔄 Отправка VK сообщения кандидату {candidate.id}, контакт: {contact}")
            
            # Ожидаем, что контакт в формате "vk123456" или просто "123456"
            vk_id_match = re.search(r'vk(\d+)|^(\d+)$', str(contact))
            if not vk_id_match:
                logger.info(f"⚠️ Контакт {contact} не является VK ID")
                return False
            
            vk_id = vk_id_match.group(1) or vk_id_match.group(2)
            if not vk_id:
                logger.warning(f"⚠️ Не удалось извлечь VK ID из контакта {contact}")
                return False
            
            vk_id = int(vk_id)
            logger.info(f"✅ Извлечён VK ID: {vk_id}")
            
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
                        logger.info(f"✅ Информация о кандидате {candidate.id} обновлена")
                
                return True
            else:
                logger.warning(f"⚠️ Не удалось отправить сообщение VK ID {vk_id}")
                return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки VK сообщения: {e}")
            traceback.print_exc()
            return False
    
    def get_user_info(self, user_id: int) -> Optional[Dict]:
        """Получение информации о пользователе VK"""
        try:
            users = self.vk.users.get(
                user_ids=user_id,
                fields='first_name,last_name,domain,photo_100'
            )
            if users and len(users) > 0:
                return users[0]
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о пользователе {user_id}: {e}")
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
            text = message.get('text', '').strip()
            payload = message.get('payload')
            
            logger.info(f"📨 VK сообщение от {user_id}: {text[:50]}...")
            
            # ===== ОБРАБОТКА КОМАНДЫ /start =====
            if text == '/start' or text == 'start' or text == '/start@' or text == 'начать':
                welcome_text = (
                    "👋 <b>Добро пожаловать в GWork HR Bot!</b>\n\n"
                    "Я помогаю находить кандидатов и автоматизировать HR-процессы.\n\n"
                    "🔍 <b>Как я работаю:</b>\n"
                    "• Ищу кандидатов в 5+ источниках (HeadHunter, SuperJob, Habr, Trudvsem, Telegram)\n"
                    "• Автоматически проверяю резюме на соответствие требованиям\n"
                    "• Общаюсь с кандидатами и провожу предквалификацию\n"
                    "• Назначаю собеседования и отправляю приглашения\n\n"
                    "📱 <b>Для работы используйте Telegram бота:</b>\n"
                    "@goodWorkingBot\n\n"
                    "Там вы можете:\n"
                    "✅ Создать вакансию (/new_job)\n"
                    "✅ Настроить фильтры (/filters)\n"
                    "✅ Посмотреть кандидатов (/candidates)\n"
                    "✅ Получить аналитику (/analytics)\n\n"
                    "Вопросы? Обратитесь к администратору."
                )
                self.send_message(user_id, welcome_text)
                return {
                    'user_id': user_id,
                    'text': text,
                    'payload': payload,
                    'message': message,
                    'handled': True
                }
            
            # ===== ОБРАБОТКА КОМАНДЫ /help =====
            if text == '/help' or text == 'help' or text == 'помощь':
                help_text = (
                    "🤖 <b>GWork HR Bot - Помощь</b>\n\n"
                    "<b>Основные команды в Telegram:</b>\n"
                    "/start - начать работу\n"
                    "/onboarding - создать профиль компании\n"
                    "/new_job - создать новую вакансию\n"
                    "/candidates - список кандидатов\n"
                    "/filters - управление фильтрами\n"
                    "/analytics - аналитика по вакансии\n"
                    "/export - скачать отчёт (CSV/HTML)\n\n"
                    "<b>В VK бот:</b>\n"
                    "Я автоматически обрабатываю сообщения от кандидатов.\n"
                    "Если вы кандидат, я задам вопросы и приглашу на собеседование.\n\n"
                    "По всем вопросам обращайтесь к администратору."
                )
                self.send_message(user_id, help_text)
                return {
                    'user_id': user_id,
                    'text': text,
                    'payload': payload,
                    'message': message,
                    'handled': True
                }
            
            # ===== ОБЫЧНАЯ ОБРАБОТКА СООБЩЕНИЙ ОТ КАНДИДАТОВ =====
            # Здесь будет логика обработки сообщений от кандидатов
            # Вызывается из основного бота
            
            return {
                'user_id': user_id,
                'text': text,
                'payload': payload,
                'message': message,
                'handled': False
            }
        
        return None
    
    async def start_polling(self, message_handler=None):
        """
        Запуск long polling для получения сообщений
        
        Args:
            message_handler: Функция-обработчик сообщений
        """
        if not self.vk_session or not self.vk:
            logger.error("❌ VK сессия не инициализирована. Сначала выполните auth()")
            return
        
        if not self.longpoll:
            logger.error("❌ LongPoll не настроен")
            return
        
        self.running = True
        logger.info(f"🔄 VK LongPoll запущен для группы {self.group_id}, ожидание сообщений...")
        
        try:
            for event in self.longpoll.listen():
                if not self.running:
                    logger.info("🛑 VK LongPoll остановлен по запросу")
                    break
                
                if event.type == VkBotEventType.MESSAGE_NEW:
                    logger.info(f"📨 Получено новое сообщение от {event.object.message['from_id']}")
                    data = await self.process_event(event)
                    
                    if message_handler and data and not data.get('handled', False):
                        try:
                            # Вызываем обработчик из основного бота
                            await message_handler(data)
                        except Exception as e:
                            logger.error(f"❌ Ошибка в обработчике сообщений: {e}")
                            traceback.print_exc()
                
                elif event.type == VkBotEventType.MESSAGE_EVENT:
                    logger.info(f"🔄 Получен callback от кнопки")
                
                elif event.type == VkBotEventType.MESSAGE_TYPING_STATE:
                    pass  # Игнорируем события печатания
                
                else:
                    logger.debug(f"📨 Получено событие типа: {event.type}")
                        
        except vk_api.exceptions.ApiError as e:
            logger.error(f"❌ Ошибка VK API в LongPoll: {e}")
            if e.code == 2:
                logger.error("❌ Истекло время ожидания, переподключение...")
            elif e.code == 9:
                logger.error("❌ Слишком много запросов, пауза...")
                await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Ошибка в VK LongPoll: {e}")
            traceback.print_exc()
            await asyncio.sleep(3)  # Пауза перед переподключением
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
    
    logger.info("=" * 60)
    logger.info("📱 ИНИЦИАЛИЗАЦИЯ VK БОТА")
    logger.info("=" * 60)
    
    vk_token = getattr(settings, 'vk_token', None)
    if not vk_token:
        logger.warning("⚠️ VK_TOKEN не настроен в .env")
        logger.info("📱 Для использования VK бота добавьте VK_TOKEN в переменные окружения")
        return None
    
    # Маскируем токен для логов
    masked_token = vk_token[:10] + "..." if len(vk_token) > 10 else "слишком короткий"
    logger.info(f"✅ VK_TOKEN найден (первые символы: {masked_token})")
    logger.info(f"📊 Длина токена: {len(vk_token)} символов")
    
    try:
        logger.info("🔄 Создание экземпляра VKBot...")
        vk_bot = VKBot(vk_token)
        logger.info("✅ Экземпляр VKBot создан")
        
        logger.info("🔄 Вызов vk_bot.auth()...")
        if vk_bot.auth():
            logger.info("=" * 60)
            logger.info("✅ VK БОТ УСПЕШНО ИНИЦИАЛИЗИРОВАН")
            logger.info("=" * 60)
            return vk_bot
        else:
            logger.error("❌ vk_bot.auth() вернул False")
            logger.info("📱 Проверьте:")
            logger.info("  1. Токен должен быть пользовательским, а не от сообщества")
            logger.info("  2. У токена должны быть права: messages, groups, offline")
            logger.info("  3. Вы должны быть администратором группы")
            logger.info("  4. LongPoll API должен быть включен в настройках группы")
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации VK бота: {e}")
        logger.error("📱 Подробная информация об ошибке:")
        traceback.print_exc()
        return None
