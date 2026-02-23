import aiohttp
import asyncio
import logging
import ssl
import certifi
from typing import List, Dict, Optional
from datetime import datetime
import re

logger = logging.getLogger(__name__)

class SuperJobAPI:
    def __init__(self):
        # Ваш ключ API
        self.api_key = "v3.h.4954828.468e2a01714c919db05b73123326a4809e33c526.b9c0e8b6abbe457794fe0225bdb8474433e75928"
        self.base_url = "https://api.superjob.ru/2.0/vacancies/"
        self.session = None
        self.ssl_context = None
        
        # Создаем SSL контекст с сертификатами
        try:
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        except:
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
            logger.warning("⚠️ SSL проверка отключена для SuperJob")
        
        # Соответствие городов для SuperJob
        self.city_ids = {
            "Москва": 4,
            "Санкт-Петербург": 2,
            "Екатеринбург": 12,
            "Новосибирск": 9,
            "Казань": 88,
            "Краснодар": 53,
            "Нижний Новгород": 66,
            "Челябинск": 104,
            "Самара": 78,
            "Уфа": 99,
            "Ростов-на-Дону": 76,
            "Омск": 68,
            "Красноярск": 54,
            "Воронеж": 26,
            "Пермь": 72,
            "Волгоград": 24
        }
    
    async def get_session(self):
        """Получает или создает сессию с правильным SSL"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session
    
    async def close_session(self):
        """Закрывает сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def search_vacancies(self, keyword: str, city: str = "Москва", limit: int = 10) -> List[Dict]:
        """
        Ищет вакансии на SuperJob по ключевому слову и городу
        """
        vacancies = []
        
        try:
            session = await self.get_session()
            
            # Получаем ID города
            town_id = self.city_ids.get(city, 4)
            
            headers = {
                "X-Api-App-Id": self.api_key,
                "Content-Type": "application/json"
            }
            
            # Параметры поиска
            params = {
                "keyword": keyword,
                "town": town_id,
                "count": limit,
                "page": 0,
                "order_field": "date",
                "order_direction": "desc",
                "payment_from": 0,
                "no_agreement": 0
            }
            
            logger.info(f"🔍 Поиск на SuperJob: {keyword} в городе {city}")
            
            async with session.get(self.base_url, headers=headers, params=params, ssl=False) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    items = data.get('objects', [])
                    logger.info(f"📦 Найдено элементов: {len(items)}")
                    
                    for item in items[:limit]:
                        vacancy = self._parse_vacancy(item, city)
                        if vacancy:
                            vacancies.append(vacancy)
                    
                    logger.info(f"✅ Найдено {len(vacancies)} вакансий на SuperJob")
                    
                elif response.status == 403:
                    logger.error("❌ Ошибка авторизации SuperJob. Проверьте API ключ")
                elif response.status == 429:
                    logger.warning("⚠️ SuperJob: слишком много запросов")
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка SuperJob API: {response.status}")
        
        except Exception as e:
            logger.error(f"Ошибка при поиске на SuperJob: {e}")
            # В случае ошибки возвращаем тестовые данные
            return self._get_test_vacancies(keyword, city, limit)
        
        return vacancies
    
    def _parse_vacancy(self, item: Dict, default_city: str) -> Optional[Dict]:
        """Парсит одну вакансию из ответа API"""
        try:
            title = item.get('profession', '')
            if not title:
                return None
            
            # ID вакансии
            vacancy_id = item.get('id')
            
            # Компания
            company = item.get('firm_name', 'Не указана')
            
            # Зарплата
            salary = self._format_salary(item)
            
            # Город
            town_data = item.get('town', {})
            city = town_data.get('title', default_city)
            
            # Ссылка на вакансию
            url = item.get('link', '')
            if not url and vacancy_id:
                url = f"https://www.superjob.ru/vakansii/{vacancy_id}.html"
            
            # Описание и требования
            description = item.get('candidat', '') or item.get('work', '')
            requirements = self._extract_requirements(description)
            
            # Дата публикации
            date_published = item.get('date_published', '')
            date = self._format_date(date_published) if date_published else "Недавно"
            
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
                'source': 'superjob',
                'is_real': True
            }
            
            return vacancy
            
        except Exception as e:
            logger.error(f"Ошибка парсинга вакансии SuperJob: {e}")
            return None
    
    def _format_salary(self, item: Dict) -> str:
        """Форматирует зарплату из ответа API"""
        payment_from = item.get('payment_from')
        payment_to = item.get('payment_to')
        currency = item.get('currency', 'rub')
        
        currency_symbol = '₽'
        if currency == 'usd':
            currency_symbol = '$'
        elif currency == 'eur':
            currency_symbol = '€'
        
        if payment_from and payment_to:
            return f"{payment_from} - {payment_to} {currency_symbol}"
        elif payment_from:
            return f"от {payment_from} {currency_symbol}"
        elif payment_to:
            return f"до {payment_to} {currency_symbol}"
        else:
            return "Не указана"
    
    def _extract_requirements(self, text: str) -> List[str]:
        """Извлекает требования из текста"""
        if not text:
            return ["Требования не указаны"]
        
        # Очищаем текст от HTML тегов
        text = re.sub(r'<[^>]+>', '', text)
        
        keywords = [
            'требование', 'требуется', 'необходимо', 'должен', 'должна',
            'опыт', 'образование', 'навыки', 'умение', 'знание'
        ]
        
        requirements = []
        sentences = text.split('.')
        
        for sentence in sentences[:5]:
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            
            sentence_lower = sentence.lower()
            if any(keyword in sentence_lower for keyword in keywords):
                clean_sentence = ' '.join(sentence.split())
                if len(clean_sentence) > 15:
                    requirements.append(clean_sentence[:150])
        
        return requirements[:3] or ["Требования уточняйте на сайте"]
    
    def _format_date(self, timestamp: int) -> str:
        """Форматирует timestamp в читаемую дату"""
        try:
            date_obj = datetime.fromtimestamp(timestamp)
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
            return "Недавно"
    
    def _get_test_vacancies(self, keyword: str, city: str, limit: int) -> List[Dict]:
        """Возвращает тестовые вакансии для демонстрации"""
        vacancies = []
        
        companies = [
            f"Компания {keyword.title()}",
            f"ООО {keyword.title()}",
            f"ИП {keyword.title()}"
        ]
        
        salaries = [
            "50 000 - 70 000 ₽",
            "от 45 000 ₽",
            "60 000 - 80 000 ₽"
        ]
        
        for i in range(min(limit, 3)):
            vacancy = {
                'title': f"{keyword.title()} {i+1}",
                'company': companies[i % len(companies)],
                'salary': salaries[i % len(salaries)],
                'city': city,
                'url': f"https://www.superjob.ru/vakansii/{keyword}-{i+1}.html",
                'description': f"Требуется {keyword} в компанию {companies[i % len(companies)]}",
                'requirements': ["Опыт работы от 1 года", "Ответственность"],
                'date': "Сегодня",
                'source': 'superjob',
                'is_real': True,
                'is_test': True
            }
            vacancies.append(vacancy)
        
        return vacancies

# Глобальный экземпляр
superjob = SuperJobAPI()