# pre_qualification.py
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from models import Candidate, Vacancy, Company
import logging

logger = logging.getLogger(__name__)


class PreQualificationAnalyzer:
    """
    Улучшенный анализатор для предквалификации кандидатов на основе их ответов
    """
    
    def __init__(self, candidate: Candidate, vacancy: Vacancy, company: Company):
        self.candidate = candidate
        self.vacancy = vacancy
        self.company = company
        self.scores = {}
        self.notes = []
        self.history = []
    
    def analyze_schedule(self, answer: str) -> Tuple[int, str, List[str]]:
        """
        Анализирует ответ о графике работы (улучшенная версия)
        """
        answer_lower = answer.lower()
        vacancy_schedule = self.vacancy.schedule.lower()
        
        score = 0
        note = ""
        keywords = []
        
        # Извлекаем ключевые слова
        if 'любой' in answer_lower or 'любое' in answer_lower:
            keywords.append('готов к любому графику')
        
        if 'гибкий' in answer_lower:
            keywords.append('гибкий график')
        
        if 'сменный' in answer_lower:
            keywords.append('сменный график')
        
        if '2/2' in answer_lower:
            keywords.append('2/2')
        
        if '5/2' in answer_lower:
            keywords.append('5/2')
        
        # Проверяем совпадение с требуемым графиком
        if vacancy_schedule in answer_lower:
            score = 100
            note = "График полностью совпадает"
        elif "любой" in answer_lower or "любое" in answer_lower or "не важно" in answer_lower:
            score = 80
            note = "Кандидату подходит любой график"
        elif "гибкий" in answer_lower:
            score = 60
            note = "Готов к гибкому графику"
        elif "сменный" in answer_lower:
            score = 50
            note = "Рассматривает сменный график"
        else:
            # Проверяем частичное совпадение
            words = vacancy_schedule.split()
            matches = sum(1 for word in words if word in answer_lower)
            if matches > 0:
                score = 40 + matches * 10
                note = f"Частичное совпадение по графику"
            else:
                score = 20
                note = "График не обсуждён"
        
        return score, note, keywords
    
    def analyze_salary(self, answer: str) -> Tuple[int, str, List[str]]:
        """
        Анализирует ответ о зарплатных ожиданиях (улучшенная версия)
        """
        answer_lower = answer.lower()
        keywords = []
        
        # Извлекаем числа из ответа
        numbers = re.findall(r'\d+', answer)
        if not numbers:
            return 30, "Зарплатные ожидания не указаны", keywords
        
        # Берём первое число как ожидаемую зарплату
        expected_salary = int(numbers[0])
        
        # Проверяем, не указана ли зарплата в тысячах
        if 'тыс' in answer_lower or 'к' in answer_lower:
            expected_salary *= 1000
        
        keywords.append(f"ожидает {expected_salary} руб.")
        
        score = 0
        note = ""
        
        # Сравниваем с вилкой вакансии
        if self.vacancy.salary_from and self.vacancy.salary_to:
            # Есть и минимум, и максимум
            if self.vacancy.salary_from <= expected_salary <= self.vacancy.salary_to:
                score = 100
                note = f"Зарплата {expected_salary} входит в вилку {self.vacancy.salary_from}-{self.vacancy.salary_to}"
            elif expected_salary < self.vacancy.salary_from:
                score = 80
                note = f"Зарплата {expected_salary} ниже вилки, кандидат готов на меньшую сумму"
            else:
                # Выше максимума
                diff = (expected_salary - self.vacancy.salary_to) / self.vacancy.salary_to * 100
                if diff <= 20:
                    score = 60
                    note = f"Зарплата выше вилки на {diff:.0f}% - можно обсудить"
                else:
                    score = 20
                    note = f"Зарплата значительно выше вилки ({diff:.0f}%)"
        
        elif self.vacancy.salary_from:
            # Только минимум
            if expected_salary >= self.vacancy.salary_from * 0.8:
                score = 80
                note = f"Зарплата {expected_salary} соответствует ожиданиям"
            else:
                score = 40
                note = f"Зарплата ниже ожиданий"
        
        elif self.vacancy.salary_to:
            # Только максимум
            if expected_salary <= self.vacancy.salary_to * 1.2:
                score = 80
                note = f"Зарплата {expected_salary} в пределах допустимого"
            else:
                score = 30
                note = f"Зарплата выше максимума"
        
        else:
            # Нет вилки
            score = 50
            note = f"Зарплатные ожидания: {expected_salary}"
        
        return score, note, keywords
    
    def analyze_timing(self, answer: str) -> Tuple[int, str, List[str]]:
        """
        Анализирует ответ о сроках выхода на работу (улучшенная версия)
        """
        answer_lower = answer.lower()
        keywords = []
        
        score = 0
        note = ""
        
        # Ищем ключевые слова о сроках
        if any(word in answer_lower for word in ['завтра', 'сегодня', 'сейчас', 'немедленно']):
            score = 100
            note = "Готов выйти немедленно"
            keywords.append("немедленный выход")
        elif any(word in answer_lower for word in ['через неделю', 'через 1 неделю']):
            score = 90
            note = "Готов выйти через неделю"
            keywords.append("выход через неделю")
        elif any(word in answer_lower for word in ['2 недели', 'две недели']):
            score = 80
            note = "Готов выйти через 2 недели"
            keywords.append("выход через 2 недели")
        elif any(word in answer_lower for word in ['месяц', '30 дней']):
            score = 60
            note = "Готов выйти через месяц"
            keywords.append("выход через месяц")
        elif 'отработка' in answer_lower:
            # Ищем число в контексте отработки
            numbers = re.findall(r'\d+', answer_lower)
            if numbers:
                days = int(numbers[0])
                if days <= 14:
                    score = 70
                    note = f"Нужна отработка {days} дней"
                    keywords.append(f"отработка {days} дней")
                else:
                    score = 40
                    note = f"Длительная отработка {days} дней"
                    keywords.append(f"длительная отработка {days} дней")
            else:
                score = 50
                note = "Требуется отработка"
                keywords.append("требуется отработка")
        else:
            score = 30
            note = "Сроки не определены"
        
        return score, note, keywords
    
    def analyze_tone(self, answer: str) -> Tuple[int, str, List[str]]:
        """
        Анализирует тон ответа (мотивация, заинтересованность) - улучшенная версия
        """
        answer_lower = answer.lower()
        keywords = []
        
        score = 0
        note = ""
        
        # Позитивные индикаторы
        positive_words = ['да', 'конечно', 'согласен', 'готов', 'интересно', 'хорошо', 'отлично', 'спасибо']
        # Негативные индикаторы
        negative_words = ['нет', 'не знаю', 'подумаю', 'возможно', 'может быть', 'не уверен', 'против']
        # Заинтересованность
        interested_words = ['подробнее', 'расскажите', 'узнать', 'хотелось бы', 'интересует', 'когда', 'где']
        # Вежливость
        polite_words = ['здравствуйте', 'добрый', 'привет', 'спасибо', 'пожалуйста', 'до свидания']
        
        pos_count = sum(1 for word in positive_words if word in answer_lower)
        neg_count = sum(1 for word in negative_words if word in answer_lower)
        int_count = sum(1 for word in interested_words if word in answer_lower)
        pol_count = sum(1 for word in polite_words if word in answer_lower)
        
        # Оценка на основе длины ответа
        length_score = min(len(answer) / 10, 20)  # До 20 баллов за длину
        
        if pos_count > neg_count:
            score = 70 + int_count * 5 + pol_count * 3 + length_score
            note = "Позитивный и заинтересованный ответ"
            keywords.append("позитивный настрой")
        elif pos_count == neg_count:
            score = 50 + int_count * 5 + length_score
            note = "Нейтральный ответ"
        else:
            score = 30 + length_score
            note = "Неуверенный или негативный ответ"
            keywords.append("негативный настрой")
        
        if int_count > 0:
            keywords.append("проявляет интерес")
        
        if pol_count > 0:
            keywords.append("вежливый")
        
        return min(score, 100), note, keywords
    
    def analyze_all(self, answers: Dict[str, str]) -> Dict:
        """
        Анализирует все ответы кандидата и выносит вердикт (улучшенная версия)
        """
        results = {}
        all_keywords = []
        
        # Сохраняем историю
        self.history.append({
            'timestamp': datetime.now().isoformat(),
            'answers': answers.copy()
        })
        
        # Анализируем каждый ответ
        if 'schedule' in answers:
            score, note, keywords = self.analyze_schedule(answers['schedule'])
            results['schedule'] = {'score': score, 'note': note, 'keywords': keywords}
            self.scores['schedule'] = score
            all_keywords.extend(keywords)
        
        if 'salary' in answers:
            score, note, keywords = self.analyze_salary(answers['salary'])
            results['salary'] = {'score': score, 'note': note, 'keywords': keywords}
            self.scores['salary'] = score
            all_keywords.extend(keywords)
        
        if 'timing' in answers:
            score, note, keywords = self.analyze_timing(answers['timing'])
            results['timing'] = {'score': score, 'note': note, 'keywords': keywords}
            self.scores['timing'] = score
            all_keywords.extend(keywords)
        
        # Анализируем тон (на основе всех ответов)
        all_answers = " ".join(answers.values())
        tone_score, tone_note, tone_keywords = self.analyze_tone(all_answers)
        results['tone'] = {'score': tone_score, 'note': tone_note, 'keywords': tone_keywords}
        self.scores['tone'] = tone_score
        all_keywords.extend(tone_keywords)
        
        # Вычисляем общий балл
        total_score = sum(self.scores.values()) / len(self.scores)
        results['total_score'] = total_score
        results['keywords'] = list(set(all_keywords))  # Уникальные ключевые слова
        
        # Выносим вердикт
        if total_score >= 70:
            results['verdict'] = 'lead'
            results['verdict_text'] = '✅ ВЕСТИ'
            results['verdict_description'] = 'Кандидат подходит, продолжаем коммуникацию'
        elif total_score >= 40:
            results['verdict'] = 'clarify'
            results['verdict_text'] = '⚠️ УТОЧНИТЬ'
            results['verdict_description'] = 'Есть вопросы, требуется дополнительное общение'
        else:
            results['verdict'] = 'reject'
            results['verdict_text'] = '❌ НЕ ВЕСТИ'
            results['verdict_description'] = 'Кандидат не подходит, закрываем диалог'
        
        results['history'] = self.history
        
        return results
    
    def generate_followup_questions(self, results: Dict) -> List[str]:
        """
        Генерирует уточняющие вопросы на основе анализа (улучшенная версия)
        """
        questions = []
        
        if results.get('schedule', {}).get('score', 100) < 50:
            questions.append("Какой график работы был бы для вас наиболее комфортным?")
        
        if results.get('salary', {}).get('score', 100) < 50:
            questions.append("Какая зарплата была бы для вас комфортной? (можно указать диапазон)")
        
        if results.get('timing', {}).get('score', 100) < 50:
            questions.append("Когда именно вы могли бы приступить к работе? Есть ли необходимость в отработке?")
        
        if results.get('tone', {}).get('score', 100) < 40:
            questions.append("Расскажите подробнее о вашем опыте и что вы ищете в новой работе?")
        
        # Если всё хорошо, но есть место для уточнения
        if len(questions) == 0 and results.get('total_score', 0) < 80:
            questions.append("Есть ли у вас вопросы к нам?")
        
        return questions
    
    def get_history(self) -> List[Dict]:
        """
        Возвращает историю диалога
        """
        return self.history


def format_qualification_results(results: Dict) -> str:
    """
    Форматирует результаты предквалификации для отображения (улучшенная версия)
    """
    text = f"📋 <b>Результаты предквалификации</b>\n\n"
    
    text += f"<b>Общий балл: {results['total_score']:.1f}/100</b>\n"
    text += f"Вердикт: {results['verdict_text']}\n"
    text += f"{results['verdict_description']}\n\n"
    
    if 'keywords' in results and results['keywords']:
        text += f"🔑 <b>Ключевые слова:</b> {', '.join(results['keywords'][:5])}\n\n"
    
    text += "<b>Детальный анализ:</b>\n"
    
    if 'schedule' in results:
        s = results['schedule']
        text += f"• 📅 График: {s['score']}/100 — {s['note']}\n"
    
    if 'salary' in results:
        s = results['salary']
        text += f"• 💰 Зарплата: {s['score']}/100 — {s['note']}\n"
    
    if 'timing' in results:
        s = results['timing']
        text += f"• ⏰ Сроки: {s['score']}/100 — {s['note']}\n"
    
    if 'tone' in results:
        s = results['tone']
        text += f"• 💬 Тон ответа: {s['score']}/100 — {s['note']}\n"
    
    return text