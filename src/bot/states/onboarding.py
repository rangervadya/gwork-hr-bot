from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    # Шаг 1: Основная информация
    company_name = State()
    industry = State()
    city = State()
    
    # Шаг 2: Условия работы
    schedule = State()
    salary_range = State()
    
    # Шаг 3: Стиль общения
    communication_style = State()
    
    # Шаг 4: Календарь (опционально)
    calendar_link = State()
    
    # Шаг 5: Создание первой вакансии
    vacancy_title = State()