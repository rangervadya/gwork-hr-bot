import aiohttp
import asyncio
import logging
import ssl
import certifi
from typing import List, Dict, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)

class HHAPI:
    def __init__(self):
        self.client_id = "INHUQJN7GKH3VPKJ8VM7D56GNVPGFG20NBFE4DP14BMI1C6O9H9JG51L8IE7B36J"
        self.client_secret = "VN5J9PA6H6350UTOR6VFLIHHNKTU8948F9V27RU7H9QR0NQFCQNDUVS0FMQBBBNP"
        self.redirect_uri = "https://pt.2035.university/project/gwork"
        self.base_url = "https://api.hh.ru"
        self.access_token = None
        self.token_expires = None
        self.session = None
        self.ssl_context = None
        
        # Создаем SSL контекст с сертификатами
        try:
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        except:
            # Если не получается, создаем без проверки (для разработки)
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
            logger.warning("⚠️ SSL проверка отключена (только для разработки)")
        
    async def get_session(self):
        """Получает или создает сессию с правильными настройками SSL"""
        if self.session is None or self.session.closed:
            # Создаем коннектор с нашим SSL контекстом
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session
    
    async def close_session(self):
        """Закрывает сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get_access_token(self) -> Optional[str]:
        """Получает access token для API HH.ru"""
        try:
            # Проверяем, не истек ли токен
            if self.access_token and self.token_expires:
                if datetime.now() < self.token_expires:
                    return self.access_token
            
            # Для публичного доступа HH.ru не требует токен
            self.access_token = None
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения токена: {e}")
            return None
    
    async def search_vacancies(self, query: str, city: str = "Москва", limit: int = 10) -> List[Dict]:
        """Поиск вакансий на HH.ru"""
        vacancies = []
        
        try:
            session = await self.get_session()
            
            # Параметры запроса - исправлено!
            params = {
                'text': query,
                'area': self._get_city_id(city),
                'per_page': min(limit, 20),
                'page': 0,
                'order_by': 'relevance'
            }
            
            headers = {
                'User-Agent': 'GWork/1.0 (pt.2035.university)',
                'Content-Type': 'application/json'
            }
            
            logger.info(f"🔍 Поиск на HH.ru: {query} в городе {city}")
            logger.info(f"📊 Параметры запроса: {params}")
            
            async with session.get(f"{self.base_url}/vacancies", params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    for item in data.get('items', []):
                        vacancy = await self._parse_vacancy(item)
                        if vacancy:
                            vacancies.append(vacancy)
                    
                    logger.info(f"✅ Найдено {len(vacancies)} вакансий на HH.ru")
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка API HH.ru: {response.status} - {error_text}")
                    
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Ошибка подключения к HH.ru: {e}")
        except aiohttp.ClientSSLError as e:
            logger.error(f"Ошибка SSL при подключении к HH.ru: {e}")
            # Пробуем еще раз с другим подходом
            try:
                # Создаем новую сессию с отключенной проверкой SSL
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as temp_session:
                    async with temp_session.get(f"{self.base_url}/vacancies", params=params, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            for item in data.get('items', []):
                                vacancy = await self._parse_vacancy(item)
                                if vacancy:
                                    vacancies.append(vacancy)
                            logger.info(f"✅ Найдено {len(vacancies)} вакансий на HH.ru (без SSL)")
            except Exception as e2:
                logger.error(f"Повторная ошибка: {e2}")
        except Exception as e:
            logger.error(f"Ошибка поиска вакансий: {e}")
        
        return vacancies
    
    def _get_city_id(self, city_name: str) -> str:
        """Получает ID города для HH.ru"""
        cities = {
            "Москва": "1",
            "Санкт-Петербург": "2",
            "Екатеринбург": "3",
            "Новосибирск": "4",
            "Казань": "88",
            "Нижний Новгород": "66",
            "Челябинск": "104",
            "Самара": "78",
            "Омск": "68",
            "Ростов-на-Дону": "76",
            "Уфа": "99",
            "Красноярск": "54",
            "Пермь": "72",
            "Воронеж": "26",
            "Волгоград": "24",
            "Краснодар": "53",
            "Саратов": "79",
            "Тюмень": "95",
            "Тольятти": "91",
            "Ижевск": "44",
            "Барнаул": "17",
            "Ульяновск": "98",
            "Владивосток": "22",
            "Ярославль": "112",
            "Иркутск": "46",
            "Хабаровск": "101",
            "Новокузнецк": "65",
            "Оренбург": "69",
            "Кемерово": "50",
            "Рязань": "77",
            "Астрахань": "16",
            "Набережные Челны": "63",
            "Пенза": "71",
            "Липецк": "59",
            "Киров": "52",
            "Чебоксары": "103",
            "Тула": "93",
            "Калининград": "48",
            "Курск": "57",
            "Сочи": "84",
            "Улан-Удэ": "97",
            "Ставрополь": "86",
            "Махачкала": "62",
            "Владимир": "23",
            "Смоленск": "83",
            "Брянск": "20",
            "Тамбов": "89"
        }
        return cities.get(city_name, "1")  # По умолчанию Москва
    
    async def _parse_vacancy(self, item: Dict) -> Optional[Dict]:
        """Парсит вакансию из ответа API"""
        try:
            # Получаем зарплату
            salary = "Не указана"
            salary_info = item.get('salary')
            if salary_info and isinstance(salary_info, dict):
                salary_from = salary_info.get('from')
                salary_to = salary_info.get('to')
                currency = salary_info.get('currency', 'RUR')
                
                # Конвертируем валюту
                currency_symbol = '₽'
                if currency == 'USD':
                    currency_symbol = '$'
                elif currency == 'EUR':
                    currency_symbol = '€'
                elif currency == 'KZT':
                    currency_symbol = '₸'
                
                if salary_from and salary_to:
                    salary = f"{salary_from} - {salary_to} {currency_symbol}"
                elif salary_from:
                    salary = f"от {salary_from} {currency_symbol}"
                elif salary_to:
                    salary = f"до {salary_to} {currency_symbol}"
            
            # Получаем требования
            requirements = []
            snippet = item.get('snippet', {})
            if snippet and snippet.get('requirement'):
                req_text = snippet['requirement']
                # Убираем HTML теги
                req_text = req_text.replace('<highlighttext>', '').replace('</highlighttext>', '')
                # Разбиваем на предложения
                requirements = [r.strip() for r in req_text.split('.') if len(r.strip()) > 10]
            
            # Получаем обязанности
            responsibilities = []
            if snippet and snippet.get('responsibility'):
                resp_text = snippet['responsibility']
                resp_text = resp_text.replace('<highlighttext>', '').replace('</highlighttext>', '')
                responsibilities = [r.strip() for r in resp_text.split('.') if len(r.strip()) > 10]
            
            # Объединяем требования и обязанности для ключевых моментов
            key_points = requirements[:3] + responsibilities[:2]
            
            vacancy = {
                'id': item.get('id'),
                'source': 'hh',
                'source_id': str(item.get('id')),
                'title': item.get('name', ''),
                'description': ' '.join(responsibilities) if responsibilities else '',
                'salary': salary,
                'city': item.get('area', {}).get('name', 'Не указан'),
                'company': item.get('employer', {}).get('name', ''),
                'url': item.get('alternate_url', ''),
                'date': item.get('published_at', '')[:10] if item.get('published_at') else '',
                'requirements': requirements[:5],
                'key_points': key_points[:3],
                'contacts': 'Контакты доступны после отклика на сайте'
            }
            
            return vacancy
            
        except Exception as e:
            logger.error(f"Ошибка парсинга вакансии: {e}")
            return None
    
    async def get_vacancy_details(self, vacancy_id: str) -> Optional[Dict]:
        """Получает детальную информацию о вакансии"""
        try:
            session = await self.get_session()
            
            headers = {
                'User-Agent': 'GWork/1.0 (pt.2035.university)',
                'Content-Type': 'application/json'
            }
            
            async with session.get(f"{self.base_url}/vacancies/{vacancy_id}", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Ошибка получения деталей вакансии: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Ошибка получения деталей вакансии: {e}")
            return None
    
    async def get_areas(self) -> List[Dict]:
        """Получает список всех городов/регионов"""
        try:
            session = await self.get_session()
            
            headers = {
                'User-Agent': 'GWork/1.0 (pt.2035.university)',
                'Content-Type': 'application/json'
            }
            
            async with session.get(f"{self.base_url}/areas", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Ошибка получения списка городов: {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"Ошибка получения списка городов: {e}")
            return []

# Глобальный экземпляр
hh = HHAPI()