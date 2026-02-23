#!/usr/bin/env python3
"""
Скрипт для очистки и пересоздания базы данных.
Запустите этот файл ОДИН РАЗ перед запуском бота.
"""

import os
import sqlite3

def recreate_database():
    """Пересоздает базу данных с правильной структурой"""
    
    # Удаляем старую базу данных если есть
    if os.path.exists("hrbot.db"):
        os.remove("hrbot.db")
        print("🗑️ Старая база данных удалена")
    
    # Создаем новую базу данных
    conn = sqlite3.connect("hrbot.db")
    cursor = conn.cursor()
    
    # Таблица компаний - ИСПРАВЛЕННАЯ СТРУКТУРА
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER UNIQUE,
        company_name TEXT,
        industry TEXT,
        city TEXT,
        schedule TEXT,
        salary TEXT,
        communication_style TEXT DEFAULT 'neutral',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица вакансий
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS vacancies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        title TEXT,
        query TEXT,
        experience_required BOOLEAN DEFAULT 1,
        schedule TEXT,
        salary_min INTEGER,
        salary_max INTEGER,
        critical_requirements TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица кандидатов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vacancy_id INTEGER,
        name TEXT,
        source TEXT,
        city TEXT,
        experience TEXT,
        skills TEXT,
        ai_score INTEGER,
        ai_verdict TEXT,
        status TEXT DEFAULT 'new',
        is_favorite BOOLEAN DEFAULT 0,
        external_vacancy_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица внешних вакансий
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS external_vacancies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_id TEXT UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        salary TEXT,
        city TEXT,
        contacts TEXT,
        requirements TEXT,
        url TEXT,
        date TEXT,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT 'new',
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ai_analysis TEXT,
        raw_data TEXT,
        FOREIGN KEY (user_id) REFERENCES companies (owner_id)
    )
    ''')
    
    # Создаем индексы
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ext_vac_user ON external_vacancies(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ext_vac_status ON external_vacancies(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ext_vac_source ON external_vacancies(source)')
    
    conn.commit()
    conn.close()
    
    print("✅ База данных успешно создана с правильной структурой!")
    print("📊 Структура таблиц:")
    print("   - companies: owner_id, company_name, industry, city, schedule, salary, communication_style")
    print("   - vacancies: title, schedule, salary_min, salary_max")
    print("   - candidates: имя, оценка ИИ, статус")
    print("   - external_vacancies: вакансии с Avito")

if __name__ == "__main__":
    print("🔄 Пересоздание базы данных...")
    recreate_database()
    print("\n🎉 База данных готова! Теперь запустите бота:")
    print("   python main.py")