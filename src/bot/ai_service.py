import os
import json
import logging
from typing import Dict, List, Optional
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.enabled = bool(self.api_key)
        
        if self.enabled:
            logger.info("✅ DeepSeek API включен")
        else:
            logger.warning("⚠️ DeepSeek API отключен (нет API ключа)")
    
    async def analyze_vacancy_with_ai(self, prompt: str) -> Dict:
        """Анализ вакансии с помощью DeepSeek"""
        try:
            if not self.enabled:
                # Демо-режим
                return {
                    "position": "Администратор",
                    "requirements": ["Опыт работы от 1 года", "Знание ПК", "Коммуникабельность"],
                    "experience": "от 1 года",
                    "schedule": "2/2",
                    "salary": "40000-50000 руб.",
                    "location": "Москва",
                    "contacts": "Контакты в описании",
                    "urgency": "normal",
                    "compatibility_score": 80,
                    "summary": "Хорошая вакансия для администратора с базовыми требованиями"
                }
            
            # Реальный вызов DeepSeek API
            # Пока возвращаем демо-данные
            return {
                "position": "Сотрудник",
                "requirements": ["Базовые требования"],
                "experience": "от 1 года",
                "schedule": "Полный день",
                "salary": "30000-50000 руб.",
                "location": "Город",
                "contacts": "Не указаны",
                "urgency": "normal",
                "compatibility_score": 75,
                "summary": "Стандартная вакансия"
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа вакансии ИИ: {e}")
            return {}
    
    async def score_candidate(self, candidate_data: Dict, vacancy_data: Dict) -> Dict:
        """Оценивает кандидата (заглушка если нет API)"""
        if not self.enabled:
            return self._get_mock_score(candidate_data, vacancy_data)
        
        # Здесь будет реальный вызов API
        # Пока возвращаем заглушку
        return self._get_mock_score(candidate_data, vacancy_data)
    
    def _get_mock_score(self, candidate: Dict, vacancy: Dict) -> Dict:
        """Заглушка для оценки кандидата"""
        score = 50  # базовая оценка
        
        # Простая логика оценки
        if candidate.get('city') == vacancy.get('city'):
            score += 20
        
        if candidate.get('experience'):
            if 'год' in candidate['experience'].lower() or 'опыт' in candidate['experience'].lower():
                score += 15
        
        if candidate.get('skills'):
            score += min(len(candidate['skills']) * 5, 20)
        
        score = max(0, min(100, score))
        
        # Определяем рекомендацию
        if score >= 80:
            recommendation = "пригласить"
            verdict = "Отличный кандидат"
            strengths = ["Совпадение локации", "Соответствующий опыт"]
            weaknesses = []
        elif score >= 60:
            recommendation = "рассмотреть"
            verdict = "Хороший кандидат"
            strengths = ["Базовое соответствие"]
            weaknesses = ["Требуется уточнение опыта"]
        else:
            recommendation = "отклонить"
            verdict = "Низкое соответствие"
            strengths = []
            weaknesses = ["Не совпадает город", "Мало опыта"]
        
        return {
            "score": score,
            "verdict": verdict,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendation": recommendation,
            "suggested_questions": [
                "Расскажите о вашем опыте работы?",
                "Какие ваши зарплатные ожидания?",
                "Когда можете выйти на работу?"
            ]
        }
    
    async def generate_message(self, candidate_name: str, company_info: Dict, vacancy_title: str) -> str:
        """Генерирует сообщение кандидату"""
        company_name = company_info.get('company_name', 'Наша компания')
        style = company_info.get('communication_style', 'neutral')
        
        if style == 'strict' or style == 'Строгий':
            return f"Уважаемый(ая) {candidate_name}! Компания «{company_name}» рассматривает вашу кандидатуру на позицию {vacancy_title}. Предлагаем обсудить детали в ближайшее время."
        elif style == 'friendly' or style == 'Дружелюбный':
            return f"Привет, {candidate_name}! 👋 Мы из {company_name} увидели твой профиль для позиции {vacancy_title}. Давай пообщаемся и обсудим детали!"
        else:
            return f"Здравствуйте, {candidate_name}! Компания {company_name} заинтересовалась вашей кандидатурой на позицию {vacancy_title}. Предлагаем обсудить детали сотрудничества."
    
    async def analyze_prequalification(self, candidate_name: str, vacancy_title: str, qa_text: str) -> dict:
        """Анализирует ответы на предквалификацию"""
        try:
            # Простая логика анализа ответов
            score = 70  # начальная оценка
            key_points = []
            
            # Анализ по опыту
            if "год" in qa_text.lower() or "лет" in qa_text.lower():
                if "1" in qa_text or "2" in qa_text or "3" in qa_text:
                    score += 15
                    key_points.append("Имеет опыт работы")
                elif "0" in qa_text or "месяц" in qa_text.lower() or "полгода" in qa_text.lower():
                    score -= 10
                    key_points.append("Мало опыта")
            
            # Анализ по зарплатным ожиданиям
            if "30000" in qa_text or "40000" in qa_text or "50000" in qa_text:
                score += 10
                key_points.append("Зарплатные ожидания в рамках рынка")
            elif "60000" in qa_text or "70000" in qa_text or "80000" in qa_text:
                score -= 10
                key_points.append("Высокие зарплатные ожидания")
            
            # Анализ по готовности к работе
            if "готов" in qa_text.lower() or "могу" in qa_text.lower() or "сразу" in qa_text.lower():
                score += 10
                key_points.append("Готов к быстрому выходу на работу")
            elif "через месяц" in qa_text.lower() or "после" in qa_text.lower():
                score -= 5
                key_points.append("Не готов к быстрому выходу")
            
            # Анализ по мотивации
            if "интерес" in qa_text.lower() or "нравится" in qa_text.lower() or "хочу" in qa_text.lower():
                score += 5
                key_points.append("Проявляет интерес")
            
            # Ограничиваем score
            score = max(0, min(100, score))
            
            # Определяем рекомендацию
            if score >= 75:
                recommendation = "✅ Рекомендуется вести дальше"
                should_continue = True
            elif score >= 55:
                recommendation = "🟡 Можно рассмотреть"
                should_continue = True
            else:
                recommendation = "❌ Не рекомендуется вести"
                should_continue = False
            
            # Добавляем базовые пункты если пусто
            if not key_points:
                key_points = ["Базовое соответствие требованиям"]
            
            # Генерируем итог
            summary = f'Кандидат {candidate_name} получил {score}/100 баллов. '
            if should_continue:
                summary += "Демонстрирует подходящие характеристики для вакансии. Рекомендуется продолжить общение."
            else:
                summary += "Есть значительные расхождения с требованиями вакансии. Рекомендуется отказать."
            
            return {
                'score': score,
                'recommendation': recommendation,
                'should_continue': should_continue,
                'key_points': key_points,
                'summary': summary,
                'analyzed_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Ошибка анализа предквалификации: {e}")
            return {
                'score': 0,
                'recommendation': 'Ошибка анализа',
                'should_continue': False,
                'key_points': ['Ошибка при анализе'],
                'summary': 'Произошла ошибка при анализе ответов',
                'analyzed_at': datetime.now().isoformat()
            }

# Глобальный экземпляр AI сервиса
ai = AIService()