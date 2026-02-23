import asyncio
import logging
import re
import json
import random
import time
from typing import Dict, List, Optional
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse
import html
from datetime import datetime

# Импортируем AI сервис для поиска
from ai_service import ai

logger = logging.getLogger(__name__)

class AvitoScraper:
    """Класс для реального парсинга конкретных вакансий с Avito"""
    
    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        
        # Регионы Avito
        self.regions = {
            'Москва': 'moskva',
            'Санкт-Петербург': 'sankt-peterburg',
            'Казань': 'kazan',
            'Новосибирск': 'novosibirsk',
            'Екатеринбург': 'ekaterinburg',
        }
        
        # Сессия aiohttp
        self.session = None
        
    async def init_session(self):
        """Инициализация сессии"""
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
            connector = aiohttp.TCPConnector(ssl=False)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            
    async def close_session(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
            self.session = None
    
    def _get_random_headers(self) -> Dict:
        """Случайные заголовки для обхода блокировок"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': 'https://www.avito.ru/'
        }
    
    def _clean_text(self, text: str) -> str:
        """Очистка текста"""
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    async def scrape_avito_vacancies(self, query: str, city: str = None, limit: int = 5) -> List[Dict]:
        """
        Парсинг ТОЛЬКО реальных вакансий с конкретными ссылками на объявления
        """
        logger.info(f"🔍 Парсим РЕАЛЬНЫЕ вакансии Avito: '{query}' в городе '{city}'")
        
        try:
            await self.init_session()
            
            # Парсим реальные вакансии
            vacancies = await self._scrape_real_avito_vacancies(query, city, limit)
            
            if vacancies:
                logger.info(f"✅ Найдено {len(vacancies)} РЕАЛЬНЫХ вакансий с конкретными ссылками")
                
                # Анализируем каждую вакансию через DeepSeek
                for vacancy in vacancies:
                    try:
                        prompt = self._create_analysis_prompt(vacancy, query)
                        ai_analysis = await ai.analyze_vacancy_with_ai(prompt)
                        vacancy['ai_analysis'] = self._format_ai_analysis(ai_analysis, vacancy)
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Ошибка AI анализа: {e}")
                        vacancy['ai_analysis'] = self._get_default_analysis(vacancy)
                
                return vacancies
            else:
                logger.warning("❌ Реальных вакансий с конкретными ссылками не найдено")
                # Возвращаем пустой список - не генерируем фейковые ссылки!
                return []
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга Avito: {e}", exc_info=True)
            return []
    
    async def _scrape_real_avito_vacancies(self, query: str, city: str = None, limit: int = 5) -> List[Dict]:
        """Парсинг ТОЛЬКО реальных вакансий с КОНКРЕТНЫМИ ссылками"""
        try:
            region_slug = self.regions.get(city, 'rossiya')
            query_encoded = quote_plus(query)
            
            # URL для поиска вакансий
            url = f"https://www.avito.ru/{region_slug}/vakansii?q={query_encoded}"
            
            logger.info(f"🔗 Парсим URL поиска: {url}")
            
            headers = self._get_random_headers()
            
            async with self.session.get(url, headers=headers, ssl=False) as response:
                if response.status == 200:
                    html_content = await response.text()
                    return self._extract_real_vacancies_with_links(html_content, query, city, limit)
                else:
                    logger.warning(f"HTTP статус: {response.status}")
                    return []
                        
        except Exception as e:
            logger.error(f"Ошибка при парсинге: {e}")
            return []
    
    def _extract_real_vacancies_with_links(self, html_content: str, query: str, city: str, limit: int) -> List[Dict]:
        """
        Извлекает ТОЛЬКО вакансии с РЕАЛЬНЫМИ ссылками на конкретные объявления
        Формат ссылки: https://www.avito.ru/moskva/vakansii/NAZVANIE_ID
        ИЛИ https://www.avito.ru/moskva/vakansii/ID
        """
        vacancies = []
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Ищем карточки объявлений
            items = soup.select('[data-marker="item"]')
            
            if not items:
                items = soup.select('.iva-item-content-rejJg')
            
            logger.info(f"Найдено {len(items)} карточек объявлений")
            
            for item in items[:limit]:
                try:
                    # === 1. Ищем РЕАЛЬНУЮ ссылку на КОНКРЕТНОЕ объявление ===
                    link_elem = None
                    url = None
                    
                    # Пробуем разные селекторы для поиска ссылки
                    selectors = [
                        'a[href*="/vakansii/"][href*="_"]',  # ссылка с ID
                        'a[href*="/vakansii/"][href$="/"]',   # ссылка с слешем в конце
                        'a[data-marker="item-title"]',        # стандартный маркер
                        'a[href*="/vakansii/"]'              # любая ссылка на вакансии
                    ]
                    
                    for selector in selectors:
                        link_elem = item.select_one(selector)
                        if link_elem and link_elem.get('href'):
                            href = link_elem['href']
                            
                            # Формируем полную ссылку
                            if href.startswith('//'):
                                url = f"https:{href}"
                            elif href.startswith('/'):
                                url = f"https://www.avito.ru{href}"
                            else:
                                url = href
                            
                            # === 2. ПРОВЕРЯЕМ, ЧТО ЭТО ССЫЛКА НА КОНКРЕТНОЕ ОБЪЯВЛЕНИЕ ===
                            # У конкретного объявления есть ID в конце ссылки
                            has_id = bool(re.search(r'/\d+$', href)) or bool(re.search(r'_\d+$', href))
                            
                            if has_id:
                                # Это ссылка на конкретное объявление!
                                break
                            else:
                                # Это ссылка на категорию или поиск - пропускаем
                                url = None
                                continue
                    
                    if not url:
                        continue
                    
                    # === 3. ИЗВЛЕКАЕМ ID ОБЪЯВЛЕНИЯ ===
                    item_id = None
                    id_match = re.search(r'/(\d+)$', href)
                    if id_match:
                        item_id = id_match.group(1)
                    else:
                        id_match = re.search(r'_(\d+)$', href)
                        if id_match:
                            item_id = id_match.group(1)
                    
                    if not item_id:
                        # Нет ID - это не конкретное объявление
                        continue
                    
                    # === 4. ЗАГОЛОВОК ===
                    title_elem = item.select_one('[itemprop="name"], h3, [data-marker="item-title"]')
                    if not title_elem:
                        title_elem = item.select_one('.title-root-zZCwT')
                    
                    title = self._clean_text(title_elem.get_text(strip=True)) if title_elem else None
                    if not title:
                        continue
                    
                    # === 5. ЗАРПЛАТА ===
                    price_elem = item.select_one('[data-marker="item-price"]')
                    if not price_elem:
                        price_elem = item.select_one('.price-price-JP7qe')
                    
                    salary = self._clean_text(price_elem.get_text(strip=True)) if price_elem else "Договорная"
                    
                    # === 6. ОПИСАНИЕ ===
                    desc_elem = item.select_one('[data-marker="item-specific-params"]')
                    if not desc_elem:
                        desc_elem = item.select_one('.iva-item-description-StepN')
                    
                    description = self._clean_text(desc_elem.get_text(strip=True)) if desc_elem else ""
                    
                    # === 7. ГОРОД ===
                    city_elem = item.select_one('[data-marker="item-address"]')
                    if not city_elem:
                        city_elem = item.select_one('.geo-georeferences-SEtee')
                    
                    city_name = self._clean_text(city_elem.get_text(strip=True)) if city_elem else (city or "Не указан")
                    
                    # === 8. ДАТА ===
                    date_elem = item.select_one('[data-marker="item-date"]')
                    date_text = self._clean_text(date_elem.get_text(strip=True)) if date_elem else "Недавно"
                    
                    # === 9. СОЗДАЕМ ВАКАНСИЮ ТОЛЬКО С РЕАЛЬНЫМИ ДАННЫМИ ===
                    vacancy = {
                        'source': 'avito',
                        'source_id': f"av_{item_id}",
                        'title': title[:200],
                        'description': description[:500] if description else 'Описание на Avito',
                        'salary': salary,
                        'city': city_name,
                        'contacts': 'Контакты на Avito',
                        'requirements': self._extract_requirements(description),
                        'url': url,  # ЭТО РЕАЛЬНАЯ ССЫЛКА НА КОНКРЕТНОЕ ОБЪЯВЛЕНИЕ!
                        'date': date_text,
                        'query': query,
                        'item_id': item_id,
                        'is_real': True,
                        'has_real_link': True
                    }
                    
                    logger.info(f"✅ НАЙДЕНА РЕАЛЬНАЯ ВАКАНСИЯ: {title}")
                    logger.info(f"   🔗 ССЫЛКА: {url}")
                    logger.info(f"   🆔 ID: {item_id}")
                    
                    vacancies.append(vacancy)
                    
                except Exception as e:
                    logger.debug(f"Ошибка парсинга карточки: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Ошибка извлечения вакансий: {e}")
        
        return vacancies
    
    def _create_analysis_prompt(self, vacancy: Dict, user_query: str) -> str:
        """Создает промпт для анализа конкретной вакансии"""
        prompt = f"""
Проанализируй эту реальную вакансию с Avito:

Должность: {vacancy.get('title', 'Не указано')}
Зарплата: {vacancy.get('salary', 'Не указана')}
Город: {vacancy.get('city', 'Не указан')}
Описание: {vacancy.get('description', '')[:300]}

Оцени по 100-балльной шкале:
1. Насколько вакансия соответствует запросу "{user_query}"
2. Насколько привлекательная зарплата
3. Свежесть вакансии
4. Полнота описания

Ответь ТОЛЬКО в формате JSON:
{{
    "score": число_от_0_до_100,
    "recommendation": "краткая рекомендация",
    "key_points": ["плюс1", "плюс2", "плюс3"]
}}
"""
        return prompt
    
    def _format_ai_analysis(self, ai_analysis: Dict, vacancy: Dict) -> Dict:
        """Форматирует анализ от AI"""
        score = ai_analysis.get('score', 70)
        
        if score >= 80:
            color = "🟢"
            emoji = "🔥"
        elif score >= 65:
            color = "🟡"
            emoji = "✅"
        elif score >= 50:
            color = "🟠"
            emoji = "⚠️"
        else:
            color = "🔴"
            emoji = "❌"
        
        key_points = ai_analysis.get('key_points', [])
        
        # Добавляем информацию о зарплате
        salary = vacancy.get('salary', '')
        if '000' in salary:
            key_points.insert(0, f"💰 {salary}")
        
        # Добавляем информацию о свежести
        date = vacancy.get('date', '').lower()
        if 'сегодня' in date:
            key_points.insert(0, "🕒 Свежая вакансия")
        
        return {
            "compatibility_score": score,
            "recommendation": ai_analysis.get('recommendation', 'Проверьте вакансию'),
            "color": color,
            "emoji": emoji,
            "key_points": key_points[:3],
            "is_real": True
        }
    
    def _get_default_analysis(self, vacancy: Dict) -> Dict:
        """Анализ по умолчанию"""
        return {
            "compatibility_score": 70,
            "recommendation": "Проверьте вакансию по ссылке",
            "color": "🟡",
            "emoji": "✅",
            "key_points": [
                f"📍 {vacancy.get('city', 'Город не указан')}",
                f"💼 {vacancy.get('title', '')[:30]}...",
                "🔗 Реальная ссылка на Avito"
            ],
            "is_real": True
        }
    
    def _extract_requirements(self, description: str) -> List[str]:
        """Извлечение требований из описания"""
        if not description:
            return ["Требования на Avito"]
        
        requirements = []
        sentences = re.split(r'[.!?]+', description)
        
        for sentence in sentences[:2]:
            if any(word in sentence.lower() for word in ['требован', 'обязан', 'необходим']):
                clean = self._clean_text(sentence)
                if clean and len(clean) > 10:
                    requirements.append(clean[:100])
        
        if not requirements:
            requirements = ["Подробности на Avito"]
        
        return requirements[:2]

# Глобальный экземпляр
scraper = AvitoScraper()

async def test_scraper():
    """Тестирование - проверяем, что ссылки ведут на конкретные объявления"""
    print("🔍 ТЕСТ: Поиск РЕАЛЬНЫХ вакансий с КОНКРЕТНЫМИ ссылками")
    print("=" * 60)
    
    await scraper.init_session()
    
    test_cases = [
        ("администратор", "Москва"),
        ("бариста", "Санкт-Петербург"),
    ]
    
    for query, city in test_cases:
        print(f"\n📌 Поиск: '{query}' в '{city}'")
        print("-" * 50)
        
        vacancies = await scraper.scrape_avito_vacancies(query, city, limit=3)
        
        if vacancies:
            print(f"✅ Найдено РЕАЛЬНЫХ вакансий: {len(vacancies)}")
            
            for i, vac in enumerate(vacancies, 1):
                print(f"\n  {i}. {vac['title']}")
                print(f"     💰 {vac['salary']}")
                print(f"     📍 {vac['city']}")
                print(f"     🔗 {vac['url']}")
                print(f"     🆔 ID объявления: {vac.get('item_id', 'Нет ID')}")
                print(f"     📅 {vac['date']}")
                print(f"     ✅ Это ссылка на КОНКРЕТНОЕ объявление: {'ДА' if vac.get('has_real_link') else 'НЕТ'}")
                
                # Проверяем формат ссылки
                if vac['url']:
                    if re.search(r'/vakansii/\d+$', vac['url']) or re.search(r'/vakansii/.*_\d+$', vac['url']):
                        print(f"     ✅ ФОРМАТ ССЫЛКИ КОРРЕКТНЫЙ")
                    else:
                        print(f"     ❌ ФОРМАТ ССЫЛКИ НЕКОРРЕКТНЫЙ")
        else:
            print("❌ Реальных вакансий с конкретными ссылками не найдено")
            print("   Avito мог изменить структуру сайта или временно блокирует парсинг")
        
        await asyncio.sleep(1)
    
    await scraper.close_session()
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(test_scraper())