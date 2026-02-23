import aiohttp
import asyncio
import logging
import ssl
import certifi
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import base64
import time
import json

logger = logging.getLogger(__name__)

class AvitoAPI:
    def __init__(self):
        self.client_id = "E6DTCnhuX7oLiNkDPUcs"
        self.client_secret = "s9DzjCMp4UoAfSaEjE2FCnxUDG64b5jICJtLglXB"
        self.base_url = "https://api.avito.ru"
        self.access_token = None
        self.token_expires = None
        self.session = None
        self.last_request_time = 0
        self.min_delay = 2.0
        
        # Правильные endpoint'ы для Avito API
        self.endpoints = {
            'token': '/token/',
            'items': '/core/v1/items',
            'job': '/job/v1/'
        }
        
        # Категории вакансий Avito Jobs
        self.job_categories = {
            'администратор': 9,
            'продавец': 9,
            'дизайнер': 9,
            'водитель': 9,
            'программист': 9,
            'бариста': 9,
            'повар': 9,
            'менеджер': 9
        }
        
        # Создаем SSL контекст
        try:
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        except:
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
    
    async def get_session(self):
        """Получает или создает сессию"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context, force_close=True)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session
    
    async def close_session(self):
        """Закрывает сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _wait_for_rate_limit(self):
        """Ожидает, чтобы не превысить лимиты API"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_delay:
            await asyncio.sleep(self.min_delay - time_since_last)
        self.last_request_time = time.time()
    
    async def get_access_token(self) -> Optional[str]:
        """Получает access token для API Avito через OAuth 2.0 Client Credentials Flow"""
        try:
            # Проверяем, не истек ли токен
            if self.access_token and self.token_expires:
                if datetime.now() < self.token_expires:
                    return self.access_token
            
            await self._wait_for_rate_limit()
            session = await self.get_session()
            
            # Формируем Basic Auth заголовок
            auth_string = f"{self.client_id}:{self.client_secret}"
            auth_bytes = auth_string.encode('ascii')
            base64_auth = base64.b64encode(auth_bytes).decode('ascii')
            
            headers = {
                'Authorization': f'Basic {base64_auth}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            # Данные для получения токена (Client Credentials Flow)
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.client_id
            }
            
            logger.info("🔄 Получение токена Avito API...")
            
            async with session.post(
                f"{self.base_url}{self.endpoints['token']}", 
                headers=headers, 
                data=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    self.access_token = result.get('access_token')
                    expires_in = result.get('expires_in', 3600)
                    self.token_expires = datetime.now() + timedelta(seconds=expires_in - 60)
                    logger.info("✅ Токен Avito API получен")
                    return self.access_token
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка получения токена Avito: {response.status} - {error_text}")
                    
                    # Если ошибка 403, значит нужен тариф
                    if response.status == 403:
                        logger.error("❌ Для доступа к API Avito требуется платный тариф")
                        return "TARIFF_REQUIRED"
                    return None
                    
        except Exception as e:
            logger.error(f"Ошибка получения токена Avito: {e}")
            return None
    
    async def search_vacancies(self, query: str, city: str = "Москва", limit: int = 10) -> List[Dict]:
        """
        Поиск вакансий на Avito через официальное API
        """
        vacancies = []
        
        try:
            # Получаем токен
            token = await self.get_access_token()
            
            # Проверяем, требуется ли тариф
            if token == "TARIFF_REQUIRED":
                logger.warning("⚠️ Avito API требует платный тариф")
                return self._get_tariff_info(query, city)
            
            if not token:
                logger.warning("⚠️ Не удалось получить токен Avito")
                return self._get_tariff_info(query, city)
            
            await self._wait_for_rate_limit()
            session = await self.get_session()
            
            # Используем правильный endpoint для поиска вакансий
            # Согласно документации Avito API [citation:4][citation:5]
            params = {
                'q': query,
                'location': city,
                'category_id': self.job_categories.get(query, 9),  # 9 - категория "Вакансии"
                'per_page': min(limit, 30),
                'page': 1
            }
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'User-Agent': 'GWork HR Bot/1.0'
            }
            
            logger.info(f"🔍 Поиск на Avito API: {query} в городе {city}")
            
            # Используем правильный endpoint для вакансий
            async with session.get(
                f"{self.base_url}/job/v1/vacancies", 
                params=params, 
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get('vacancies', [])
                    
                    for item in items[:limit]:
                        vacancy = self._parse_job_vacancy(item, city)
                        if vacancy:
                            vacancies.append(vacancy)
                    
                    logger.info(f"✅ Найдено {len(vacancies)} вакансий на Avito")
                    
                elif response.status == 403:
                    logger.error("❌ Нет доступа к API Avito (требуется тариф)")
                    return self._get_tariff_info(query, city)
                    
                elif response.status == 429:
                    logger.warning("⚠️ Avito API rate limit")
                    return self._get_tariff_info(query, city)
                    
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка Avito API: {response.status} - {error_text}")
                    return self._get_tariff_info(query, city)
        
        except Exception as e:
            logger.error(f"Ошибка при поиске на Avito: {e}")
            return self._get_tariff_info(query, city)
        
        return vacancies
    
    def _parse_job_vacancy(self, item: Dict, city: str) -> Optional[Dict]:
        """Парсит вакансию из Job API Avito"""
        try:
            vacancy_id = item.get('id')
            title = item.get('title', '')
            
            if not title:
                return None
            
            # Формируем ссылку на вакансию
            url = f"https://www.avito.ru/{city.lower()}/vakansii/{vacancy_id}"
            
            # Зарплата
            salary = item.get('salary', {})
            salary_text = "Не указана"
            if salary:
                from_amount = salary.get('from')
                to_amount = salary.get('to')
                if from_amount and to_amount:
                    salary_text = f"{from_amount} - {to_amount} ₽"
                elif from_amount:
                    salary_text = f"от {from_amount} ₽"
                elif to_amount:
                    salary_text = f"до {to_amount} ₽"
            
            # Компания
            company = item.get('company', {}).get('name', 'Не указана')
            
            # Требования
            requirements = item.get('requirements', [])
            if isinstance(requirements, str):
                requirements = [requirements]
            
            # Описание
            description = item.get('description', '')
            
            vacancy = {
                'id': vacancy_id,
                'title': title,
                'company': company,
                'salary': salary_text,
                'city': item.get('address', {}).get('city', city),
                'url': url,
                'description': description[:300] + '...' if len(description) > 300 else description,
                'requirements': requirements[:3],
                'date': self._format_date(item.get('published_at', '')),
                'source': 'avito',
                'is_real': True
            }
            
            return vacancy
            
        except Exception as e:
            logger.error(f"Ошибка парсинга вакансии Avito: {e}")
            return None
    
    def _format_date(self, date_str: str) -> str:
        """Форматирует дату"""
        if not date_str:
            return "Недавно"
        try:
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            now = datetime.now(date_obj.tzinfo)
            delta = now - date_obj
            
            if delta.days == 0:
                return "Сегодня"
            elif delta.days == 1:
                return "Вчера"
            elif delta.days < 7:
                return f"{delta.days} дн. назад"
            else:
                return date_obj.strftime('%d.%m.%Y')
        except:
            return "Недавно"
    
    def _get_tariff_info(self, query: str, city: str) -> List[Dict]:
        """
        Возвращает информационное сообщение о необходимости тарифа
        """
        logger.info("ℹ️ Avito API требует платный тариф, показываем информационное сообщение")
        
        info_vacancy = [{
            'id': 'tariff_info',
            'title': f'🔒 Для поиска на Avito требуется тариф',
            'company': 'Avito API',
            'salary': 'Требуется подключение',
            'city': city,
            'url': 'https://www.avito.ru/business/tools/api',
            'description': f'Поиск вакансий "{query}" через API Avito требует подключения платного тарифа. Подробнее на сайте Avito для бизнеса.',
            'requirements': ['Активный тариф "Максимальный"', 'Подключение API в личном кабинете', 'Получение client_id и client_secret'],
            'date': '—',
            'source': 'avito_info',
            'is_info': True,
            'tariff_required': True
        }]
        
        return info_vacancy

# Глобальный экземпляр
avito = AvitoAPI()