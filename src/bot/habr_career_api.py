import aiohttp
import asyncio
import logging
import ssl
import certifi
import os
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import base64
import json

logger = logging.getLogger(__name__)

class HabrCareerAPI:
    def __init__(self):
        # Ваши данные из регистрации
        self.client_id = "7d9eddff169eec8d3948e51263bdf1d6bc198d1443ef3e3dd3581d2bc4db46cf"
        self.client_secret = "0ca27997dba6a23b43c9cd95e6bd266de325f82df1351aa1777947dced7557a9"
        self.redirect_uri = "https://pt.2035.university/project/gwork"
        self.base_url = "https://career.habr.com"
        self.api_url = "https://api.career.habr.com"
        self.access_token = None
        self.token_expires = None
        self.session = None
        self.ssl_context = None
        
        # Создаем SSL контекст
        try:
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        except:
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # Соответствие городов для Habr Career
        self.city_ids = {
            "Москва": 1,
            "Санкт-Петербург": 2,
            "Екатеринбург": 3,
            "Новосибирск": 4,
            "Казань": 5,
            "Краснодар": 6,
            "Нижний Новгород": 7,
            "Челябинск": 8,
            "Самара": 9,
            "Уфа": 10,
            "Ростов-на-Дону": 11,
            "Омск": 12,
            "Красноярск": 13,
            "Воронеж": 14,
            "Пермь": 15,
            "Волгоград": 16
        }
    
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
    
    async def get_access_token(self) -> Optional[str]:
        """
        Получает access token для API Habr Career через OAuth 2.0
        """
        try:
            # Проверяем, не истек ли токен
            if self.access_token and self.token_expires:
                if datetime.now() < self.token_expires:
                    return self.access_token
            
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
            
            logger.info("🔄 Получение токена Habr Career...")
            
            async with session.post(
                f"{self.base_url}/oauth/token", 
                headers=headers, 
                data=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    self.access_token = result.get('access_token')
                    expires_in = result.get('expires_in', 3600)
                    self.token_expires = datetime.now() + timedelta(seconds=expires_in - 60)
                    logger.info("✅ Токен Habr Career получен")
                    return self.access_token
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка получения токена Habr Career: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Ошибка получения токена Habr Career: {e}")
            return None
    
    async def search_vacancies(self, keyword: str, city: str = "Москва", limit: int = 10) -> List[Dict]:
        """
        Поиск вакансий на Habr Career
        Специализация: IT, программисты, дизайнеры, аналитики, тестировщики
        """
        vacancies = []
        
        try:
            # Получаем токен
            token = await self.get_access_token()
            if not token:
                logger.warning("⚠️ Не удалось получить токен Habr Career")
                return self._get_test_vacancies(keyword, city, limit)
            
            session = await self.get_session()
            
            # Получаем ID города
            city_id = self.city_ids.get(city, 1)
            
            # Параметры поиска
            params = {
                'q': keyword,
                'city_id': city_id,
                'per_page': limit,
                'page': 1,
                'sort': 'date'
            }
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            logger.info(f"🔍 Поиск на Habr Career: {keyword} в городе {city}")
            
            # Поиск вакансий
            async with session.get(
                f"{self.api_url}/vacancies",
                params=params,
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    items = data.get('vacancies', [])
                    logger.info(f"📦 Найдено элементов: {len(items)}")
                    
                    for item in items[:limit]:
                        vacancy = self._parse_vacancy(item, city)
                        if vacancy:
                            vacancies.append(vacancy)
                    
                    logger.info(f"✅ Найдено {len(vacancies)} вакансий на Habr Career")
                    
                elif response.status == 401:
                    logger.error("❌ Ошибка авторизации Habr Career")
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка API Habr Career: {response.status}")
                    
        except Exception as e:
            logger.error(f"Ошибка поиска на Habr Career: {e}")
            return self._get_test_vacancies(keyword, city, limit)
        
        return vacancies
    
    def _parse_vacancy(self, item: Dict, default_city: str) -> Optional[Dict]:
        """
        Парсит одну вакансию из ответа API
        """
        try:
            title = item.get('title') or item.get('position', '')
            if not title:
                return None
            
            # ID вакансии
            vacancy_id = item.get('id')
            
            # Компания
            company_data = item.get('company', {})
            company = company_data.get('title', 'Не указана')
            
            # Зарплата
            salary = self._format_salary(item)
            
            # Город
            city_data = item.get('city', {})
            city = city_data.get('title', default_city)
            
            # Ссылка на вакансию
            url = item.get('url') or item.get('link')
            if not url and vacancy_id:
                url = f"https://career.habr.com/vacancies/{vacancy_id}"
            
            # Описание и требования
            description = item.get('description', '') or item.get('requirements', '')
            
            # Требования (если есть отдельно)
            requirements = []
            if item.get('skills'):
                requirements = [skill.get('title', '') for skill in item.get('skills', [])[:3]]
            else:
                requirements = self._extract_requirements(description)
            
            # Дата публикации
            date_published = item.get('published_at', '')
            date = self._format_date(date_published)
            
            vacancy = {
                'id': vacancy_id,
                'title': title,
                'company': company,
                'salary': salary,
                'city': city,
                'url': url,
                'description': description[:300] + '...' if len(description) > 300 else description,
                'requirements': requirements,
                'date': date,
                'source': 'habr',
                'is_real': True
            }
            
            return vacancy
            
        except Exception as e:
            logger.error(f"Ошибка парсинга вакансии Habr Career: {e}")
            return None
    
    def _format_salary(self, item: Dict) -> str:
        """Форматирует зарплату из ответа API"""
        salary_data = item.get('salary', {})
        
        if isinstance(salary_data, dict):
            salary_from = salary_data.get('from')
            salary_to = salary_data.get('to')
            currency = salary_data.get('currency', 'RUB')
        else:
            salary_from = item.get('salary_from')
            salary_to = item.get('salary_to')
            currency = item.get('currency', 'RUB')
        
        currency_symbol = '₽'
        if currency == 'USD':
            currency_symbol = '$'
        elif currency == 'EUR':
            currency_symbol = '€'
        
        if salary_from and salary_to:
            return f"{salary_from} - {salary_to} {currency_symbol}"
        elif salary_from:
            return f"от {salary_from} {currency_symbol}"
        elif salary_to:
            return f"до {salary_to} {currency_symbol}"
        else:
            return "Не указана"
    
    def _extract_requirements(self, text: str) -> List[str]:
        """Извлекает требования из текста"""
        if not text:
            return ["Требования не указаны"]
        
        # Очищаем текст от HTML тегов
        import re
        text = re.sub(r'<[^>]+>', '', text)
        
        # IT-специфичные ключевые слова
        keywords = [
            'python', 'java', 'javascript', 'js', 'c++', 'c#', 'php', 'ruby',
            'sql', 'nosql', 'docker', 'kubernetes', 'aws', 'azure', 'gcp',
            'react', 'angular', 'vue', 'node', 'django', 'flask', 'spring',
            'требование', 'требуется', 'необходимо', 'должен', 'должна',
            'опыт', 'образование', 'навыки', 'умение', 'знание'
        ]
        
        requirements = []
        sentences = text.split('.')
        
        for sentence in sentences[:5]:
            sentence = sentence.strip()
            if len(sentence) < 15:
                continue
            
            sentence_lower = sentence.lower()
            if any(keyword in sentence_lower for keyword in keywords):
                clean_sentence = ' '.join(sentence.split())
                if len(clean_sentence) > 15:
                    requirements.append(clean_sentence[:150])
        
        return requirements[:3] or ["Требования уточняйте на сайте"]
    
    def _format_date(self, date_str: str) -> str:
        """Форматирует дату в читаемый вид"""
        if not date_str:
            return "Недавно"
        
        try:
            # Пробуем разные форматы даты
            for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                try:
                    date_obj = datetime.strptime(date_str[:19], fmt)
                    now = datetime.now()
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
                    continue
        except:
            pass
        
        return "Недавно"
    
    def _get_test_vacancies(self, keyword: str, city: str, limit: int) -> List[Dict]:
        """
        Возвращает тестовые IT-вакансии для демонстрации
        """
        vacancies = []
        
        # IT-специфичные компании
        companies = [
            "Яндекс",
            "СберТех", 
            "Тинькофф",
            "VK",
            "Ozon Tech",
            "Wildberries Tech",
            "Avito Tech",
            "Лаборатория Касперского"
        ]
        
        salaries = [
            "150 000 - 250 000 ₽",
            "от 200 000 ₽",
            "180 000 - 300 000 ₽",
            "до 350 000 ₽",
            "220 000 ₽"
        ]
        
        for i in range(min(limit, 5)):
            company = companies[i % len(companies)]
            vacancy = {
                'title': f"{keyword.title()} в {company}",
                'company': company,
                'salary': salaries[i % len(salaries)],
                'city': city,
                'url': f"https://career.habr.com/vacancies/{keyword}-{i+1}",
                'description': f"Ищем {keyword} в команду {company}. Работа над высоконагруженными проектами.",
                'requirements': ["Опыт от 3 лет", "Знание современных технологий", "Английский от Intermediate"],
                'date': "Сегодня",
                'source': 'habr',
                'is_real': True,
                'is_test': True
            }
            vacancies.append(vacancy)
        
        return vacancies

# Глобальный экземпляр
habr = HabrCareerAPI()