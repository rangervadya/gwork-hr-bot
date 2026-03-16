# filters.py
import re
from typing import Dict, List, Tuple, Optional
from models import Candidate, Vacancy, Company
import logging

logger = logging.getLogger(__name__)


# ===== НОРМАЛИЗАЦИЯ ГОРОДОВ =====

def normalize_city(city: str) -> str:
    """
    Приводит названия городов к единому формату
    """
    if not city:
        return ""
    
    city_lower = city.lower().strip()
    
    # Словарь нормализации
    city_map = {
        "москва": "Москва",
        "мск": "Москва",
        "санкт-петербург": "Санкт-Петербург",
        "спб": "Санкт-Петербург",
        "питер": "Санкт-Петербург",
        "ленинград": "Санкт-Петербург",
        "екатеринбург": "Екатеринбург",
        "екб": "Екатеринбург",
        "новосибирск": "Новосибирск",
        "нск": "Новосибирск",
        "казань": "Казань",
        "нижний новгород": "Нижний Новгород",
        "нн": "Нижний Новгород",
        "челябинск": "Челябинск",
        "самара": "Самара",
        "ростов-на-дону": "Ростов-на-Дону",
        "рнд": "Ростов-на-Дону",
        "уфа": "Уфа",
        "краснодар": "Краснодар",
        "воронеж": "Воронеж",
        "пермь": "Пермь",
        "волгоград": "Волгоград",
        "красноярск": "Красноярск",
        "саратов": "Саратов",
        "тюмень": "Тюмень",
        "томск": "Томск",
        "омск": "Омск",
        "иркутск": "Иркутск",
        "владивосток": "Владивосток",
        "хабаровск": "Хабаровск",
    }
    
    for key, value in city_map.items():
        if key in city_lower:
            return value
    
    # Если не нашли в словаре, возвращаем с большой буквы
    return city.capitalize()


# ===== ИЗВЛЕЧЕНИЕ ЗАРПЛАТЫ =====

def extract_salary(text: str) -> int | None:
    """
    Извлекает ожидаемую зарплату из текста резюме
    """
    if not text:
        return None
    
    # Ищем паттерны зарплат
    patterns = [
        r'зп[:\s]*(\d+)[\s-]*(\d*)?',
        r'зарплата[:\s]*(\d+)[\s-]*(\d*)?',
        r'от[:\s]*(\d+)\s*(?:₽|руб|тыс|к)',
        r'до[:\s]*(\d+)\s*(?:₽|руб|тыс|к)',
        r'(\d+)\s*тыс\.?\s*(?:₽|руб)',
        r'(\d+)\s*[кk]\s*(?:₽|руб)?',
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            salary = match.group(1)
            # Конвертируем в число
            try:
                salary_int = int(salary)
                # Если в тысячах, умножаем
                if 'тыс' in text_lower or 'к' in text_lower:
                    salary_int *= 1000
                return salary_int
            except:
                continue
    
    return None


# ===== ИЗВЛЕЧЕНИЕ ОПЫТА =====

def extract_experience_years(text: str) -> float | None:
    """
    Извлекает опыт работы в годах из текста
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Паттерны: "опыт 3 года", "стаж 2.5 лет", "работаю 5 лет"
    patterns = [
        r'опыт[:\s]*(\d+[.,]?\d*)\s*(лет|год|года)',
        r'стаж[:\s]*(\d+[.,]?\d*)\s*(лет|год|года)',
        r'работаю[:\s]*(\d+[.,]?\d*)\s*(лет|год|года)',
        r'(\d+[.,]?\d*)\s*(лет|год|года)[\s]*опыта',
        r'опыт\s+работы\s+(\d+[.,]?\d*)\s*(лет|год|года)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                years = float(match.group(1).replace(',', '.'))
                return years
            except:
                continue
    
    # Если нет точного совпадения, ищем упоминания опыта
    if 'без опыта' in text_lower:
        return 0.0
    elif 'опыт' in text_lower and 'нет' not in text_lower:
        return 1.0  # По умолчанию предполагаем 1 год
    
    return None


# ===== ПОДСЧЁТ КРИТИЧНЫХ ТРЕБОВАНИЙ =====

def count_critical_skills_match(candidate_text: str, critical_requirements: str) -> int:
    """
    Считает, сколько критичных требований совпало с текстом кандидата
    """
    if not critical_requirements or not candidate_text:
        return 0
    
    # Разбиваем требования на отдельные ключевые слова
    requirements = [req.strip().lower() for req in critical_requirements.split(',')]
    candidate_lower = candidate_text.lower()
    
    matches = 0
    for req in requirements:
        if len(req) > 2 and req in candidate_lower:  # Игнорируем слишком короткие слова
            matches += 1
    
    return matches


# ===== КРАСНЫЕ ФЛАГИ =====

# Словарь красных флагов по категориям
RED_FLAGS = {
    "Оформление": [
        "неофициально",
        "без оформления",
        "без трудоустройства",
        "не оформляем",
        "работа неофициально",
        "без договора",
        "в чёрную",
        "серая схема",
        "в черную",
        "серая зарплата",
        "нал не интересует",
        "наличными",
    ],
    "Зарплата": [
        "зарплата наличными",
        "в конверте",
        "чёрная зарплата",
        "серая зарплата",
        "оплата наличными",
        "кеш",
        "только нал",
        "неофициальный доход",
    ],
    "Условия": [
        "работа за еду",
        "оплата процентами",
        "процент от продаж только",
        "без оплаты",
        "неоплачиваемая стажировка",
        "испытательный срок без оплаты",
        "работа за идею",
        "бесплатно",
        "без вознаграждения",
    ],
    "График": [
        "работа 24/7",
        "без выходных",
        "ненормированный день",
        "круглосуточно",
        "в любое время",
        "готовность работать сутками",
        "без отпуска",
        "работа по вызову",
        "всегда на связи",
    ],
    "Мошенничество": [
        "быстрый заработок",
        "пассивный доход",
        "пирамида",
        "инвестируйте",
        "вложите деньги",
        "купите продукт сначала",
        "оплатите обучение",
        "взнос",
        "вступительный взнос",
        "лохотрон",
        "развод",
    ],
    "Странные требования": [
        "пришлите фото",
        "девушкам",
        "только для женщин",
        "только для мужчин",
        "без опыта, но с навыками",
        "опыт не нужен, зарплата высокая",
        "молодые девушки",
        "красивая внешность",
        "приятная внешность",
        "модельная внешность",
    ],
    "Неадекватные ожидания": [
        "зарплата от 500000",
        "миллион в месяц",
        "зарплата от 1 млн",
        "доход без ограничений",
        "зарплата заоблачная",
        "быстрое обогащение",
    ]
}


def check_red_flags(text: str) -> tuple[bool, list[str]]:
    """
    Проверяет текст на наличие красных флагов
    
    Returns:
        (has_flags: bool, flags_list: list[str]) - есть ли флаги и их список
    """
    if not text:
        return False, []
    
    text_lower = text.lower()
    found_flags = []
    
    for category, flags in RED_FLAGS.items():
        for flag in flags:
            if flag in text_lower:
                found_flags.append(f"{category}: {flag}")
                break  # Один флаг на категорию достаточно
    
    return len(found_flags) > 0, found_flags


def get_red_flags_score(text: str) -> int:
    """
    Возвращает штрафной балл на основе красных флагов
    Чем больше флагов, тем ниже должен быть скор
    """
    has_flags, flags = check_red_flags(text)
    
    if not has_flags:
        return 0
    
    # Базовый штраф за наличие флагов
    base_penalty = 20
    
    # Дополнительный штраф за серьёзные категории
    serious_categories = ["Мошенничество", "Оформление", "Зарплата", "Неадекватные ожидания"]
    flags_text = " ".join(flags).lower()
    
    extra_penalty = 0
    for category in serious_categories:
        # Проверяем, есть ли флаги из этой категории
        category_flags = [f for f in flags if category in f]
        if category_flags:
            extra_penalty += 15
    
    # Общий штраф не более 70 баллов
    return min(base_penalty + extra_penalty, 70)


def red_flags_description(flags: list[str]) -> str:
    """Формирует описание найденных красных флагов"""
    if not flags:
        return ""
    
    categories = {}
    for flag in flags:
        category = flag.split(":")[0]
        if category not in categories:
            categories[category] = []
        categories[category].append(flag.split(":")[1].strip())
    
    description = "⚠️ <b>Обнаружены красные флаги:</b>\n"
    for category, items in categories.items():
        description += f"• {category}: {', '.join(items)}\n"
    
    return description


# ===== НОРМАЛИЗАЦИЯ ОПЫТА =====

def parse_experience_to_years(exp_text: str) -> float | None:
    """
    Преобразует текстовое описание опыта в годы
    Примеры: "3 года 2 месяца" → 3.17, "1 год" → 1.0, "6 месяцев" → 0.5
    """
    if not exp_text:
        return None
    
    text_lower = exp_text.lower()
    
    # Ищем годы
    years_pattern = r'(\d+)\s*(?:год|лет|года)'
    years_match = re.search(years_pattern, text_lower)
    years = 0
    if years_match:
        years = int(years_match.group(1))
    
    # Ищем месяцы
    months_pattern = r'(\d+)\s*(?:месяц|месяцев|мес)'
    months_match = re.search(months_pattern, text_lower)
    months = 0
    if months_match:
        months = int(months_match.group(1))
    
    # Если ничего не нашли, возвращаем None
    if years == 0 and months == 0:
        return None
    
    # Преобразуем в годы с дробной частью
    total_years = years + (months / 12.0)
    return round(total_years, 2)


def normalize_experience_level(years: float | None) -> str | None:
    """
    Преобразует опыт в годах в уровни: '0-1', '1-3', '3-5', '5+'
    """
    if years is None:
        return None
    
    if years < 1:
        return "0-1"
    elif years < 3:
        return "1-3"
    elif years < 5:
        return "3-5"
    else:
        return "5+"


# ===== СТАНДАРТИЗАЦИЯ НАВЫКОВ =====

# Словарь для стандартизации навыков
SKILL_NORMALIZATION = {
    # Python
    "python": "Python",
    "питон": "Python",
    "пайтон": "Python",
    
    # JavaScript
    "javascript": "JavaScript",
    "js": "JavaScript",
    "джаваскрипт": "JavaScript",
    
    # Java
    "java": "Java",
    "джава": "Java",
    
    # C++
    "c++": "C++",
    "си плюс плюс": "C++",
    
    # C#
    "c#": "C#",
    "си шарп": "C#",
    
    # PHP
    "php": "PHP",
    "пхп": "PHP",
    
    # Ruby
    "ruby": "Ruby",
    "руби": "Ruby",
    
    # SQL
    "sql": "SQL",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "базы данных": "SQL",
    "бд": "SQL",
    
    # 1С
    "1с": "1С",
    "1c": "1С",
    
    # Дизайн
    "photoshop": "Photoshop",
    "фотошоп": "Photoshop",
    "figma": "Figma",
    "фигма": "Figma",
    "illustrator": "Illustrator",
    "coreldraw": "CorelDRAW",
    "blender": "Blender",
    "3d max": "3ds Max",
    
    # Git
    "git": "Git",
    "гит": "Git",
    
    # Docker
    "docker": "Docker",
    "докер": "Docker",
    
    # Linux
    "linux": "Linux",
    "линукс": "Linux",
    
    # Office
    "excel": "Excel",
    "эксель": "Excel",
    "word": "Word",
    "ворд": "Word",
    "powerpoint": "PowerPoint",
    "access": "Access",
    "outlook": "Outlook",
    
    # Soft skills
    "коммуникабельность": "Коммуникабельность",
    "общительность": "Коммуникабельность",
    "ответственность": "Ответственность",
    "исполнительность": "Исполнительность",
    "стрессоустойчивость": "Стрессоустойчивость",
    "обучаемость": "Обучаемость",
    "работа в команде": "Работа в команде",
    "командная работа": "Работа в команде",
    "лидерство": "Лидерство",
    "организованность": "Организованность",
    "пунктуальность": "Пунктуальность",
    "инициативность": "Инициативность",
    "креативность": "Креативность",
    "аналитическое мышление": "Аналитическое мышление",
}


def normalize_skill(skill: str) -> str:
    """
    Приводит название навыка к стандартному виду
    """
    if not skill:
        return ""
    
    skill_lower = skill.lower().strip()
    
    # Прямое совпадение
    if skill_lower in SKILL_NORMALIZATION:
        return SKILL_NORMALIZATION[skill_lower]
    
    # Частичное совпадение
    for key, value in SKILL_NORMALIZATION.items():
        if key in skill_lower:
            return value
    
    # Если не нашли, возвращаем с большой буквы
    return skill.capitalize()


def normalize_skills_list(skills_text: str) -> list[str]:
    """
    Принимает текст с навыками (разделёнными запятыми) и возвращает список стандартизированных навыков
    """
    if not skills_text:
        return []
    
    # Разделяем по запятой
    raw_skills = [s.strip() for s in skills_text.split(',')]
    
    # Нормализуем каждый навык
    normalized = []
    for skill in raw_skills:
        if skill and len(skill) > 1:  # Игнорируем пустые и слишком короткие
            norm = normalize_skill(skill)
            if norm and norm not in normalized:  # Убираем дубликаты
                normalized.append(norm)
    
    return normalized


# ===== ИЗВЛЕЧЕНИЕ КЛЮЧЕВЫХ СЛОВ =====

def extract_keywords(text: str, min_length: int = 4) -> list[str]:
    """
    Извлекает ключевые слова из текста (слова длиннее min_length)
    """
    if not text:
        return []
    
    # Разделяем на слова (русские и английские)
    words = re.findall(r'\b[a-zA-Zа-яА-Я]{4,}\b', text.lower())
    
    # Стоп-слова (часто встречающиеся, но неинформативные)
    stop_words = {
        'это', 'что', 'как', 'так', 'для', 'все', 'еще', 'уже', 'которые',
        'можно', 'нужно', 'будет', 'когда', 'только', 'после', 'перед',
        'очень', 'также', 'занимаюсь', 'работаю', 'являюсь', 'имеется',
        'качестве', 'основном', 'помощь', 'своих', 'своей', 'своем',
        'был', 'была', 'были', 'было', 'этого', 'этом', 'тому', 'этот',
        'всем', 'всего', 'всех', 'ними', 'ними', 'вас', 'вам', 'ваш',
        'нашей', 'нашего', 'нашим', 'нашем', 'которые', 'который',
        'которая', 'которое', 'которые', 'также', 'именно', 'всегда',
        'никогда', 'сегодня', 'завтра', 'вчера', 'сейчас', 'потом',
    }
    
    keywords = [w for w in words if w not in stop_words and len(w) >= min_length]
    
    # Возвращаем уникальные ключевые слова
    return sorted(list(set(keywords)))


def calculate_keyword_match(candidate_keywords: list[str], vacancy_keywords: list[str]) -> float:
    """
    Рассчитывает процент совпадения ключевых слов
    """
    if not vacancy_keywords:
        return 0.0
    
    if not candidate_keywords:
        return 0.0
    
    # Считаем пересечение множеств
    candidate_set = set(candidate_keywords)
    vacancy_set = set(vacancy_keywords)
    
    matches = len(candidate_set.intersection(vacancy_set))
    total = len(vacancy_set)
    
    return (matches / total) * 100


# ===== УЛУЧШЕННАЯ НОРМАЛИЗАЦИЯ ГОРОДОВ =====

# Расширенный словарь городов
EXTENDED_CITY_MAP = {
    "москва": "Москва",
    "мск": "Москва",
    "москва-сити": "Москва",
    "санкт-петербург": "Санкт-Петербург",
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "ленинград": "Санкт-Петербург",
    "петербург": "Санкт-Петербург",
    "екатеринбург": "Екатеринбург",
    "екб": "Екатеринбург",
    "новосибирск": "Новосибирск",
    "нск": "Новосибирск",
    "казань": "Казань",
    "нижний новгород": "Нижний Новгород",
    "нн": "Нижний Новгород",
    "челябинск": "Челябинск",
    "самара": "Самара",
    "ростов-на-дону": "Ростов-на-Дону",
    "рнд": "Ростов-на-Дону",
    "уфа": "Уфа",
    "краснодар": "Краснодар",
    "воронеж": "Воронеж",
    "пермь": "Пермь",
    "волгоград": "Волгоград",
    "красноярск": "Красноярск",
    "саратов": "Саратов",
    "тюмень": "Тюмень",
    "томск": "Томск",
    "омск": "Омск",
    "иркутск": "Иркутск",
    "владивосток": "Владивосток",
    "хабаровск": "Хабаровск",
    "калининград": "Калининград",
    "сочи": "Сочи",
    "тула": "Тула",
    "ярославль": "Ярославль",
    "рязань": "Рязань",
    "липецк": "Липецк",
    "ижевск": "Ижевск",
    "набережные челны": "Набережные Челны",
    "ульяновск": "Ульяновск",
    "пенза": "Пенза",
    "киров": "Киров",
    "чебоксары": "Чебоксары",
    "барнаул": "Барнаул",
    "кемерово": "Кемерово",
    "новокузнецк": "Новокузнецк",
    "оренбург": "Оренбург",
    "тольятти": "Тольятти",
    "астрахань": "Астрахань",
    "махачкала": "Махачкала",
    "владикавказ": "Владикавказ",
    "ставрополь": "Ставрополь",
    "белгород": "Белгород",
    "брянск": "Брянск",
    "владимир": "Владимир",
    "иваново": "Иваново",
    "калуга": "Калуга",
    "кострома": "Кострома",
    "курск": "Курск",
    "орел": "Орёл",
    "смоленск": "Смоленск",
    "тверь": "Тверь",
    "тамбов": "Тамбов",
}


def normalize_city_extended(city: str) -> str:
    """
    Улучшенная нормализация города с расширенным словарём
    """
    if not city:
        return ""
    
    city_lower = city.lower().strip()
    
    # Прямое совпадение
    if city_lower in EXTENDED_CITY_MAP:
        return EXTENDED_CITY_MAP[city_lower]
    
    # Проверяем вхождение
    for key, value in EXTENDED_CITY_MAP.items():
        if key in city_lower:
            return value
    
    # Если не нашли, возвращаем с большой буквы
    return city.capitalize()


def extract_city_from_text(text: str) -> str | None:
    """
    Извлекает название города из текста (например, из резюме)
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Ищем паттерны: "г. Москва", "город Москва", "г Москва"
    patterns = [
        r'г\.?\s*([а-яА-Я\-]{3,})',
        r'город\s*([а-яА-Я\-]{3,})',
        r'проживаю\s*в\s*([а-яА-Я\-]{3,})',
        r'живу\s*в\s*([а-яА-Я\-]{3,})',
        r'нахожусь\s*в\s*([а-яА-Я\-]{3,})',
        r'район\s*([а-яА-Я\-]{3,})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            city_candidate = match.group(1)
            # Проверяем, есть ли такой город в словаре
            normalized = normalize_city_extended(city_candidate)
            if normalized and normalized != city_candidate.capitalize():
                return normalized
    
    return None


# ===== ЖЁСТКИЕ ФИЛЬТРЫ =====

def apply_hard_filters(candidate: Candidate, vacancy: Vacancy, company: Company = None) -> tuple[bool, str]:
    """
    Применяет жёсткие фильтры к кандидату с учётом настроек компании
    
    Returns:
        (passed: bool, reason: str) - прошёл ли фильтры и причина отказа
    """
    
    # Получаем настройки фильтров
    filters_settings = {}
    if company:
        filters_settings = getattr(company, 'filters_settings', {})
    
    # Проверка красных флагов (всегда включена)
    text_to_check = f"{candidate.experience_text} {candidate.skills_text} {candidate.raw_text}"
    has_red_flags, red_flags_list = check_red_flags(text_to_check)
    
    if has_red_flags:
        # Сохраняем красные флаги в кандидата
        candidate.red_flags = red_flags_list
        
        # Если есть флаги мошенничества или оформления, сразу отсеиваем
        serious_flags = any(
            "Мошенничество" in flag or 
            "Оформление" in flag or 
            "Неадекватные ожидания" in flag 
            for flag in red_flags_list
        )
        if serious_flags:
            return False, f"Обнаружены подозрительные фразы: {', '.join(red_flags_list[:2])}"
    
    # 1. Фильтр по городу
    if filters_settings.get('city', True):
        candidate_city = normalize_city(candidate.city)
        vacancy_city = normalize_city(vacancy.city)
        
        if candidate_city and vacancy_city and candidate_city != vacancy_city:
            return False, f"Город {candidate.city} не соответствует требуемому {vacancy.city}"
    
    # 2. Фильтр по зарплате
    if filters_settings.get('salary', True):
        if vacancy.salary_to and candidate.salary_expectations:
            if candidate.salary_expectations > vacancy.salary_to * 1.2:  # Запас 20%
                return False, f"Ожидания по зарплате ({candidate.salary_expectations}) выше вилки ({vacancy.salary_to})"
    
    # 3. Фильтр по опыту
    if filters_settings.get('experience', True):
        if vacancy.experience_required:
            if not candidate.experience_years or candidate.experience_years < 1:
                return False, "Требуется опыт работы, но у кандидата нет опыта"
    
    # 4. Фильтр по критичным требованиям (МОЖНО ПРОПУСТИТЬ)
    if filters_settings.get('skills', True):
        if vacancy.must_have and vacancy.must_have.strip() and vacancy.must_have != "-":
            matches = count_critical_skills_match(
                f"{candidate.experience_text} {candidate.skills_text}",
                vacancy.must_have
            )
            candidate.critical_skills_match = matches
            
            if matches == 0 and len(vacancy.must_have.split(',')) > 0:
                # Проверяем, не является ли требование пустым или прочерком
                return False, f"Не найдены критичные требования: {vacancy.must_have}"
    
    return True, ""