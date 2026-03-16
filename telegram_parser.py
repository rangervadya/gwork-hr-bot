# telegram_parser.py
import re
import logging
from typing import List, Dict, Any
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class TelegramParser:
    """
    Парсер Telegram-каналов для поиска кандидатов
    """
    
    def __init__(self):
        self.channels = [
            # IT и работа
            "@jobforjunior",
            "@it_vakansii_jobs",
            "@python_jobs", 
            "@remote_it_jobs",
            "@job_telegram",
            "@habr_career",
            "@rabota_ru_news",
            
            # Рабочие специальности
            "@job_vacancies",
            "@work_vacancies",
            "@job_searching",
            
            # Региональные
            "@moscow_jobs",
            "@spb_jobs",
            "@ekb_jobs",
        ]
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        ]
    
    def _clean_text(self, text: str) -> str:
        """Очищает текст от лишних символов"""
        if not text:
            return ""
        # Убираем эмодзи и специальные символы
        text = re.sub(r'[^\w\s\u0400-\u04FF@.,!?-]', '', text)
        return text.strip()
    
    def _extract_contact(self, text: str) -> str:
        """Извлекает контактную информацию из текста"""
        # Telegram username
        tg_match = re.search(r'@(\w+)', text)
        if tg_match:
            return f"@{tg_match.group(1)}"
        
        # Email
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
        if email_match:
            return email_match.group()
        
        # Телефон
        phone_match = re.search(r'(\+?\d[\d\-\(\) ]{8,}\d)', text)
        if phone_match:
            return phone_match.group(1)
        
        return ""
    
    def _extract_city(self, text: str) -> str:
        """Извлекает город из текста"""
        cities = [
            "москва", "санкт-петербург", "спб", "екатеринбург",
            "новосибирск", "казань", "нижний новгород", "челябинск",
            "самара", "уфа", "краснодар", "ростов-на-дону",
            "воронеж", "пермь", "волгоград", "красноярск"
        ]
        
        text_lower = text.lower()
        for city in cities:
            if city in text_lower:
                if city == "спб":
                    return "Санкт-Петербург"
                return city.capitalize()
        
        return "Не указан"
    
    def _extract_experience(self, text: str) -> str:
        """Извлекает опыт работы"""
        patterns = [
            r'опыт[:\s]*(\d+)[\s-]*(\d*)?\s*(лет|год|года)',
            r'стаж[:\s]*(\d+)[\s-]*(\d*)?\s*(лет|год|года)',
            r'(\d+)\s*(лет|год|года)[\s]*опыта'
        ]
        
        text_lower = text.lower()
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(0)
        
        return "Опыт не указан"
    
    def _extract_skills(self, text: str) -> List[str]:
        """Извлекает навыки из текста"""
        common_skills = [
            "python", "java", "javascript", "sql", "1с", "photoshop",
            "figma", "git", "docker", "linux", "windows", "word",
            "excel", "продажи", "обслуживание", "водительские права",
            "коммуникабельность", "ответственность", "исполнительность"
        ]
        
        text_lower = text.lower()
        skills = []
        
        for skill in common_skills:
            if skill in text_lower:
                skills.append(skill)
        
        return skills[:5]  # Не больше 5 навыков
    
    async def search_candidates(self, query: str, city: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Поиск кандидатов в Telegram-каналах
        """
        candidates = []
        channels_to_parse = self.channels[:5]  # Первые 5 каналов
        
        logger.info(f"🔍 Telegram: поиск '{query}' в {len(channels_to_parse)} каналах")
        
        async with httpx.AsyncClient() as client:
            for channel in channels_to_parse:
                try:
                    username = channel.replace('@', '')
                    url = f"https://t.me/s/{username}"
                    
                    headers = {
                        "User-Agent": self.user_agents[0],
                        "Accept": "text/html,application/xhtml+xml"
                    }
                    
                    response = await client.get(url, headers=headers, timeout=10.0)
                    
                    if response.status_code != 200:
                        continue
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    messages = soup.find_all('div', class_='tgme_widget_message_text')
                    
                    for msg in messages[:10]:  # Последние 10 сообщений
                        text = msg.get_text(strip=True)
                        
                        # Проверяем, похоже ли на резюме
                        if self._is_resume(text, query):
                            candidate = self._parse_message(text, channel)
                            if candidate:
                                candidates.append(candidate)
                                
                                if len(candidates) >= limit:
                                    return candidates
                
                except Exception as e:
                    logger.error(f"Ошибка парсинга {channel}: {e}")
                    continue
        
        logger.info(f"✅ Telegram: найдено {len(candidates)} кандидатов")
        return candidates
    
    def _is_resume(self, text: str, query: str) -> bool:
        """Проверяет, является ли сообщение резюме"""
        if len(text) < 50:
            return False
        
        text_lower = text.lower()
        query_words = query.lower().split()
        
        # Ключевые слова для поиска резюме
        resume_keywords = [
            'ищу работу', 'резюме', 'в поиске', 'рассмотрю предложения',
            'опыт работы', 'ищу вакансию', 'нужна работа', 'готов к работе'
        ]
        
        has_keyword = any(kw in text_lower for kw in resume_keywords)
        has_query = any(word in text_lower for word in query_words if len(word) > 3)
        
        return has_keyword or has_query
    
    def _parse_message(self, text: str, channel: str) -> Dict[str, Any]:
        """Парсит сообщение в карточку кандидата"""
        lines = text.split('\n')
        first_line = lines[0] if lines else ""
        
        # Извлекаем данные
        name = self._clean_text(first_line)[:50] or "Кандидат"
        contact = self._extract_contact(text)
        city = self._extract_city(text)
        experience = self._extract_experience(text)
        skills = self._extract_skills(text)
        
        # Формируем описание
        about = self._clean_text(text)[:200]
        if len(about) > 200:
            about = about[:197] + "..."
        
        return {
            "name": name,
            "city": city,
            "experience": experience,
            "skills": skills,
            "about": about,
            "source": "telegram",
            "url": f"https://t.me/{channel.replace('@', '')}",
            "contact": contact,
            "is_real": True
        }


# Глобальный экземпляр парсера
telegram_parser = TelegramParser()