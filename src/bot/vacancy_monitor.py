import asyncio
import logging
from datetime import datetime
from typing import Dict, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

class VacancyMonitor:
    """Мониторинг вакансий по расписанию"""
    
    def __init__(self, scraper, ai_service):
        self.scraper = scraper
        self.ai = ai_service
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        
        # Настройки мониторинга
        self.config = {
            'telegram_interval_minutes': 60,
            'max_vacancies_per_source': 20,
            'auto_analyze': True
        }
        
        # Кэш обработанных вакансий
        self.processed_cache = set()
        
    async def start_monitoring(self, user_id: int, filters: Dict = None):
        """Запуск мониторинга для конкретного пользователя"""
        self.is_running = True
        
        # Немедленный сбор при старте
        await self.collect_vacancies(user_id, filters)
        
        # Настраиваем периодический сбор
        self.scheduler.add_job(
            self.collect_vacancies,
            IntervalTrigger(minutes=self.config['telegram_interval_minutes']),
            args=[user_id, filters],
            id=f'vacancy_monitor_{user_id}'
        )
        
        self.scheduler.start()
        logger.info(f"Started monitoring for user {user_id}")
        return True
    
    async def stop_monitoring(self, user_id: int):
        """Остановка мониторинга"""
        try:
            self.scheduler.remove_job(f'vacancy_monitor_{user_id}')
            self.is_running = False
            logger.info(f"Stopped monitoring for user {user_id}")
        except:
            pass
    
    async def collect_vacancies(self, user_id: int, filters: Dict):
        """Сбор вакансий из всех источников"""
        try:
            logger.info(f"Collecting vacancies for user {user_id}")
            
            vacancies = []
            
            # 1. Сбор из Telegram
            telegram_vacancies = await self.scraper.monitor_telegram_channels(
                limit=self.config['max_vacancies_per_source']
            )
            
            # Фильтруем по городу пользователя
            if filters and filters.get('city'):
                city_filter = filters['city'].lower()
                telegram_vacancies = [
                    v for v in telegram_vacancies 
                    if city_filter in v.get('city', '').lower()
                ]
            
            vacancies.extend(telegram_vacancies)
            
            # 2. Сохраняем в базу
            new_vacancies = 0
            for vacancy in vacancies:
                # Проверяем, не обрабатывали ли уже эту вакансию
                vacancy_hash = self._get_vacancy_hash(vacancy)
                if vacancy_hash in self.processed_cache:
                    continue
                
                # Анализируем нейросетью
                if self.config['auto_analyze']:
                    analysis = await self.scraper.analyze_with_ai(vacancy, self.ai)
                    vacancy['ai_analysis'] = analysis
                
                # Сохраняем в базу
                vacancy['user_id'] = user_id
                vacancy['status'] = 'new'
                vacancy['collected_at'] = datetime.now().isoformat()
                
                from database import db
                db.save_external_vacancy(vacancy)
                self.processed_cache.add(vacancy_hash)
                new_vacancies += 1
            
            logger.info(f"Collected {new_vacancies} new vacancies for user {user_id}")
            
            # 3. Уведомляем пользователя о новых вакансиях
            if new_vacancies > 0:
                await self.notify_user(user_id, new_vacancies)
            
            return {
                'success': True,
                'count': new_vacancies
            }
            
        except Exception as e:
            logger.error(f"Error collecting vacancies: {e}")
            return {'success': False, 'error': str(e)}
    
    async def notify_user(self, user_id: int, new_count: int):
        """Уведомление пользователя о новых вакансиях"""
        try:
            from main import bot
            
            message = f"📥 *Найдены новые вакансии!*\n\n"
            message += f"Найдено {new_count} новых вакансий\n\n"
            message += "Просмотреть вакансии: /external_vacancies\n"
            message += "Настроить мониторинг: /monitor_vacancies"
            
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Error notifying user: {e}")
    
    def _get_vacancy_hash(self, vacancy: Dict) -> str:
        """Создание хэша для идентификации вакансии"""
        import hashlib
        hash_string = f"{vacancy.get('source')}_{vacancy.get('title', '')}"
        return hashlib.md5(hash_string.encode()).hexdigest()