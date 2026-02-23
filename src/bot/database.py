import sqlite3
import json
import logging
import os
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path=None):
        if db_path:
            self.db_path = db_path
        else:
            if os.path.exists('/app/data'):
                self.db_path = '/app/data/hrbot.db'
            else:
                self.db_path = 'hrbot.db'

        self.init_db()
        self.ensure_tables()
    
    def init_db(self):
        """Инициализация базы данных"""
        try:
            logger.info(f"🔄 Инициализация базы данных: {self.db_path}")
            
            # Проверяем доступность директории
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                logger.info(f"📁 Создана директория: {db_dir}")
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Таблица компаний
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER UNIQUE,
                company_name TEXT NOT NULL,
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
            
            # Таблица сообщений
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                message_type TEXT,
                content TEXT,
                sent_at TIMESTAMP,
                response TEXT,
                responded_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Таблица предквалификации
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS prequalification (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                question TEXT,
                answer TEXT,
                question_index INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidate_id) REFERENCES candidates (id)
            )
            ''')
            
            # Таблица анализа предквалификации
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS prequalification_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER UNIQUE,
                score INTEGER,
                recommendation TEXT,
                key_points TEXT,
                summary TEXT,
                should_continue BOOLEAN,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidate_id) REFERENCES candidates (id)
            )
            ''')
            
            # Таблица собеседований
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                user_id INTEGER,
                interview_date DATE,
                interview_time TEXT,
                status TEXT DEFAULT 'scheduled',
                notes TEXT,
                location TEXT,
                contact_person TEXT,
                reminder_sent BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidate_id) REFERENCES candidates (id)
            )
            ''')
            
            # Таблица истории статусов кандидатов
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS candidate_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                old_status TEXT,
                new_status TEXT,
                changed_by INTEGER,
                reason TEXT,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidate_id) REFERENCES candidates (id)
            )
            ''')
            
            # Таблица заметок к кандидатам
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS candidate_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                user_id INTEGER,
                note TEXT,
                is_private BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (candidate_id) REFERENCES candidates (id)
            )
            ''')
            
            # Таблица внешних вакансий (реальные с Avito)
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
                url TEXT NOT NULL,
                date TEXT,
                user_id INTEGER NOT NULL,
                status TEXT DEFAULT 'new',
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ai_analysis TEXT,
                raw_data TEXT
            )
            ''')
            
            # Таблица настроек мониторинга
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                city TEXT,
                keywords TEXT,
                interval_minutes INTEGER DEFAULT 60,
                is_active BOOLEAN DEFAULT 0,
                sources TEXT,
                last_check TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Таблица Telegram каналов
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_username TEXT,
                channel_title TEXT,
                is_active BOOLEAN DEFAULT 1,
                last_checked TIMESTAMP,
                vacancy_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, channel_username)
            )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"✅ База данных создана: {self.db_path}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания БД: {e}", exc_info=True)
            raise e
    
    def ensure_tables(self):
        """Проверяет и создает таблицы если их нет"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Проверяем существование таблицы companies
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='companies'")
            if not cursor.fetchone():
                logger.warning("⚠️ Таблица companies не найдена, создаем...")
                cursor.execute('''
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER UNIQUE,
                    company_name TEXT NOT NULL,
                    industry TEXT,
                    city TEXT,
                    schedule TEXT,
                    salary TEXT,
                    communication_style TEXT DEFAULT 'neutral',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')
            
            # Проверяем существование таблицы external_vacancies
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='external_vacancies'")
            if not cursor.fetchone():
                logger.warning("⚠️ Таблица external_vacancies не найдена, создаем...")
                cursor.execute('''
                CREATE TABLE external_vacancies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT,
                    salary TEXT,
                    city TEXT,
                    contacts TEXT,
                    requirements TEXT,
                    url TEXT NOT NULL,
                    date TEXT,
                    user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'new',
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ai_analysis TEXT,
                    raw_data TEXT
                )
                ''')
            
            conn.commit()
            conn.close()
            logger.info("✅ Таблицы проверены/созданы")
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки таблиц: {e}")
    
    def save_company(self, owner_id: int, company_data: Dict) -> bool:
        """
        Сохраняет профиль компании
        Возвращает True только если запись успешно сохранена и подтверждена
        """
        conn = None
        try:
            logger.info(f"🔄 Сохранение компании для owner_id: {owner_id}")
            logger.info(f"📊 Данные компании: {company_data}")
            
            # Проверяем наличие обязательных полей
            company_name = company_data.get('company_name', '').strip()
            if not company_name:
                logger.error("❌ Пустое название компании")
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Получаем значения с дефолтами
            industry = company_data.get('industry', 'Не указано').strip()
            city = company_data.get('city', 'Не указан').strip()
            schedule = company_data.get('schedule', 'Не указан').strip()
            salary = company_data.get('salary', 'Не указана').strip()
            communication_style = company_data.get('communication_style', 'neutral').strip()
            
            # Проверяем существующую запись
            cursor.execute("SELECT id FROM companies WHERE owner_id = ?", (owner_id,))
            existing = cursor.fetchone()
            
            if existing:
                # Обновляем существующую запись
                cursor.execute('''
                UPDATE companies SET
                    company_name = ?, 
                    industry = ?, 
                    city = ?,
                    schedule = ?, 
                    salary = ?, 
                    communication_style = ?,
                    created_at = CURRENT_TIMESTAMP
                WHERE owner_id = ?
                ''', (
                    company_name,
                    industry,
                    city,
                    schedule,
                    salary,
                    communication_style,
                    owner_id
                ))
                logger.info(f"📝 Обновлена существующая компания для user_id: {owner_id}")
                
                # Проверяем, что обновление затронуло строку
                if cursor.rowcount == 0:
                    logger.error(f"❌ Обновление не затронуло строки для user_id: {owner_id}")
                    conn.rollback()
                    return False
            else:
                # Создаем новую запись
                cursor.execute('''
                INSERT INTO companies 
                (owner_id, company_name, industry, city, schedule, salary, communication_style)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    owner_id,
                    company_name,
                    industry,
                    city,
                    schedule,
                    salary,
                    communication_style
                ))
                logger.info(f"➕ Создана новая компания для user_id: {owner_id}")
            
            conn.commit()
            
            # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: сразу читаем запись
            cursor.execute("SELECT id, company_name FROM companies WHERE owner_id = ?", (owner_id,))
            saved = cursor.fetchone()
            
            if saved:
                logger.info(f"✅ Компания успешно сохранена: {saved[1]} (ID: {saved[0]}) для user_id: {owner_id}")
                return True
            else:
                logger.error(f"❌ Компания не найдена после сохранения для user_id: {owner_id}")
                return False
                
        except sqlite3.IntegrityError as e:
            logger.error(f"❌ Ошибка целостности SQLite: {e}")
            if conn:
                conn.rollback()
            return False
        except sqlite3.Error as e:
            logger.error(f"❌ Ошибка SQLite при сохранении компании: {e}")
            if conn:
                conn.rollback()
            return False
        except Exception as e:
            logger.error(f"❌ Общая ошибка при сохранении компании: {e}", exc_info=True)
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()
    
    def get_company(self, owner_id: int) -> Optional[Dict]:
        """Получает профиль компании"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM companies WHERE owner_id = ?", (owner_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                company = {}
                for i, col in enumerate(column_names):
                    company[col] = row[i]
                
                logger.info(f"✅ Найдена компания для user_id {owner_id}: {company.get('company_name')}")
                return company
            else:
                logger.warning(f"⚠️ Компания не найдена для user_id: {owner_id}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения компании для user_id {owner_id}: {e}")
            return None
    
    def save_vacancy(self, owner_id: int, vacancy_data: Dict) -> Optional[int]:
        """Сохраняет вакансию"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO vacancies (owner_id, title, query, schedule, salary_min, salary_max, critical_requirements)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                owner_id,
                vacancy_data.get('title', ''),
                vacancy_data.get('query', ''),
                vacancy_data.get('schedule', ''),
                vacancy_data.get('salary_min', 0),
                vacancy_data.get('salary_max', 0),
                json.dumps(vacancy_data.get('requirements', []), ensure_ascii=False)
            ))
            
            vacancy_id = cursor.lastrowid
            conn.commit()
            conn.close()
            logger.info(f"✅ Вакансия сохранена: {vacancy_id} для user_id: {owner_id}")
            return vacancy_id
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения вакансии: {e}")
            return None
    
    def get_vacancies(self, owner_id: int) -> List[Dict]:
        """Получает все вакансии пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM vacancies WHERE owner_id = ? ORDER BY created_at DESC", (owner_id,))
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            vacancies = []
            for row in rows:
                vacancy = {}
                for i, col in enumerate(column_names):
                    if col == 'critical_requirements':
                        try:
                            vacancy[col] = json.loads(row[i]) if row[i] else []
                        except:
                            vacancy[col] = []
                    else:
                        vacancy[col] = row[i]
                vacancies.append(vacancy)
            return vacancies
        except Exception as e:
            logger.error(f"❌ Ошибка получения вакансий: {e}")
            return []
    
    def get_vacancy(self, vacancy_id: int) -> Optional[Dict]:
        """Получает вакансию по ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                vacancy = {}
                for i, col in enumerate(column_names):
                    if col == 'critical_requirements':
                        try:
                            vacancy[col] = json.loads(row[i]) if row[i] else []
                        except:
                            vacancy[col] = []
                    else:
                        vacancy[col] = row[i]
                return vacancy
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения вакансии: {e}")
            return None
    
    def add_candidate(self, vacancy_id: int, candidate_data: Dict) -> Optional[int]:
        """Добавляет кандидата"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO candidates (vacancy_id, name, source, city, experience, skills, ai_score, ai_verdict, status, external_vacancy_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                vacancy_id,
                candidate_data.get('name', ''),
                candidate_data.get('source', 'telegram'),
                candidate_data.get('city', ''),
                candidate_data.get('experience', ''),
                json.dumps(candidate_data.get('skills', []), ensure_ascii=False),
                candidate_data.get('ai_score', 0),
                candidate_data.get('ai_verdict', ''),
                candidate_data.get('status', 'new'),
                candidate_data.get('external_vacancy_id')
            ))
            
            candidate_id = cursor.lastrowid
            conn.commit()
            conn.close()
            logger.info(f"✅ Кандидат добавлен: {candidate_id}")
            return candidate_id
        except Exception as e:
            logger.error(f"❌ Ошибка добавления кандидата: {e}")
            return None
    
    def get_candidates(self, vacancy_id: Optional[int] = None, owner_id: Optional[int] = None) -> List[Dict]:
        """Получает кандидатов"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if vacancy_id:
                cursor.execute("SELECT * FROM candidates WHERE vacancy_id = ? ORDER BY ai_score DESC", (vacancy_id,))
            elif owner_id:
                cursor.execute('''
                SELECT c.* FROM candidates c
                JOIN vacancies v ON c.vacancy_id = v.id
                WHERE v.owner_id = ?
                ORDER BY c.ai_score DESC
                ''', (owner_id,))
            else:
                cursor.execute("SELECT * FROM candidates ORDER BY ai_score DESC")
            
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            candidates = []
            for row in rows:
                candidate = {}
                for i, col in enumerate(column_names):
                    if col == 'skills':
                        try:
                            candidate[col] = json.loads(row[i]) if row[i] else []
                        except:
                            candidate[col] = []
                    elif col == 'is_favorite':
                        candidate[col] = bool(row[i])
                    else:
                        candidate[col] = row[i]
                candidates.append(candidate)
            return candidates
        except Exception as e:
            logger.error(f"❌ Ошибка получения кандидатов: {e}")
            return []
    
    def get_candidate(self, candidate_id: int) -> Optional[Dict]:
        """Получает кандидата по ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                candidate = {}
                for i, col in enumerate(column_names):
                    if col == 'skills':
                        try:
                            candidate[col] = json.loads(row[i]) if row[i] else []
                        except:
                            candidate[col] = []
                    elif col == 'is_favorite':
                        candidate[col] = bool(row[i])
                    else:
                        candidate[col] = row[i]
                return candidate
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения кандидата: {e}")
            return None
    
    def update_candidate_status(self, candidate_id: int, status: str) -> bool:
        """Обновляет статус кандидата"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT status FROM candidates WHERE id = ?", (candidate_id,))
            old_status_row = cursor.fetchone()
            old_status = old_status_row[0] if old_status_row else None
            
            cursor.execute(
                "UPDATE candidates SET status = ? WHERE id = ?",
                (status, candidate_id)
            )
            
            if old_status and old_status != status:
                cursor.execute('''
                INSERT INTO candidate_status_history (candidate_id, old_status, new_status)
                VALUES (?, ?, ?)
                ''', (candidate_id, old_status, status))
            
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка обновления кандидата: {e}")
            return False
    
    def toggle_candidate_favorite(self, candidate_id: int, is_favorite: bool) -> bool:
        """Добавляет/убирает кандидата из избранного"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            favorite_value = 1 if is_favorite else 0
            cursor.execute(
                "UPDATE candidates SET is_favorite = ? WHERE id = ?",
                (favorite_value, candidate_id)
            )
            
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка избранного: {e}")
            return False
    
    def add_message(self, candidate_id: int, message_type: str, content: str, sent: bool = True) -> Optional[int]:
        """Добавляет сообщение кандидату"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if sent:
                cursor.execute('''
                INSERT INTO messages (candidate_id, message_type, content, sent_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (candidate_id, message_type, content))
            else:
                cursor.execute('''
                INSERT INTO messages (candidate_id, message_type, content)
                VALUES (?, ?, ?)
                ''', (candidate_id, message_type, content))
            
            message_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            if sent:
                self.update_candidate_status(candidate_id, 'contacted')
            
            return message_id
        except Exception as e:
            logger.error(f"❌ Ошибка добавления сообщения: {e}")
            return None
    
    def get_messages(self, candidate_id: int) -> List[Dict]:
        """Получает все сообщения кандидата"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages WHERE candidate_id = ? ORDER BY created_at", (candidate_id,))
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            messages = []
            for row in rows:
                message = {}
                for i, col in enumerate(column_names):
                    message[col] = row[i]
                messages.append(message)
            return messages
        except Exception as e:
            logger.error(f"❌ Ошибка получения сообщений: {e}")
            return []
    
    def get_candidate_stats(self, owner_id: int) -> Dict:
        """Статистика по кандидатам"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN ai_score >= 80 THEN 1 ELSE 0 END) as top,
                SUM(CASE WHEN ai_score >= 60 AND ai_score < 80 THEN 1 ELSE 0 END) as good,
                SUM(CASE WHEN is_favorite = 1 THEN 1 ELSE 0 END) as favorites,
                SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress
            FROM candidates c
            JOIN vacancies v ON c.vacancy_id = v.id
            WHERE v.owner_id = ?
            ''', (owner_id,))
            
            stats_row = cursor.fetchone()
            
            conn.close()
            
            if stats_row:
                return {
                    'total': stats_row[0] or 0,
                    'top': stats_row[1] or 0,
                    'good': stats_row[2] or 0,
                    'favorites': stats_row[3] or 0,
                    'in_progress': stats_row[4] or 0
                }
            return {'total': 0, 'top': 0, 'good': 0, 'favorites': 0, 'in_progress': 0}
        except Exception as e:
            logger.error(f"❌ Ошибка статистики: {e}")
            return {'total': 0, 'top': 0, 'good': 0, 'favorites': 0, 'in_progress': 0}
    
    def save_prequalification_answer(self, candidate_id: int, question: str, answer: str, question_index: Optional[int] = None) -> bool:
        """Сохраняет ответ на вопрос предквалификации"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO prequalification (candidate_id, question, answer, question_index)
            VALUES (?, ?, ?, ?)
            ''', (candidate_id, question, answer, question_index))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения ответа предквалификации: {e}")
            return False
    
    def get_prequalification_answers(self, candidate_id: int) -> List[Dict]:
        """Получает все ответы на предквалификацию"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
            SELECT question, answer, question_index, created_at 
            FROM prequalification 
            WHERE candidate_id = ? 
            ORDER BY question_index ASC, created_at ASC
            """, (candidate_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            answers = []
            for row in rows:
                answers.append({
                    'question': row[0],
                    'answer': row[1],
                    'question_index': row[2],
                    'created_at': row[3]
                })
            return answers
        except Exception as e:
            logger.error(f"❌ Ошибка получения ответов предквалификации: {e}")
            return []
    
    def save_prequalification_analysis(self, candidate_id: int, analysis: Dict) -> bool:
        """Сохраняет анализ предквалификации от ИИ"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM prequalification_analysis WHERE candidate_id = ?", (candidate_id,))
            existing = cursor.fetchone()
            
            key_points_str = json.dumps(analysis.get('key_points', []), ensure_ascii=False)
            should_continue = 1 if analysis.get('should_continue', False) else 0
            
            if existing:
                cursor.execute('''
                UPDATE prequalification_analysis SET
                    score = ?, recommendation = ?, key_points = ?,
                    summary = ?, should_continue = ?, analyzed_at = CURRENT_TIMESTAMP
                WHERE candidate_id = ?
                ''', (
                    analysis.get('score', 0),
                    analysis.get('recommendation', ''),
                    key_points_str,
                    analysis.get('summary', ''),
                    should_continue,
                    candidate_id
                ))
            else:
                cursor.execute('''
                INSERT INTO prequalification_analysis (candidate_id, score, recommendation, key_points, summary, should_continue)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    candidate_id,
                    analysis.get('score', 0),
                    analysis.get('recommendation', ''),
                    key_points_str,
                    analysis.get('summary', ''),
                    should_continue
                ))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения анализа предквалификации: {e}")
            return False
    
    def get_prequalification_analysis(self, candidate_id: int) -> Optional[Dict]:
        """Получает анализ предквалификации"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prequalification_analysis WHERE candidate_id = ?", (candidate_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                analysis = {}
                for i, col in enumerate(column_names):
                    if col == 'key_points':
                        try:
                            analysis[col] = json.loads(row[i]) if row[i] else []
                        except:
                            analysis[col] = []
                    elif col == 'should_continue':
                        analysis[col] = bool(row[i])
                    else:
                        analysis[col] = row[i]
                return analysis
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения анализа предквалификации: {e}")
            return None
    
    def save_interview(self, candidate_id: int, user_id: int, interview_data: Dict) -> Optional[int]:
        """Сохраняет информацию о собеседовании"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO interviews (candidate_id, user_id, interview_date, interview_time, 
                                   status, notes, location, contact_person)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                candidate_id,
                user_id,
                interview_data.get('date'),
                interview_data.get('time'),
                interview_data.get('status', 'scheduled'),
                interview_data.get('notes', ''),
                interview_data.get('location', ''),
                interview_data.get('contact_person', 'Менеджер по подбору')
            ))
            
            interview_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return interview_id
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения собеседования: {e}")
            return None
    
    def get_interview(self, interview_id: int) -> Optional[Dict]:
        """Получает собеседование по ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                interview = {}
                for i, col in enumerate(column_names):
                    if col == 'reminder_sent':
                        interview[col] = bool(row[i])
                    else:
                        interview[col] = row[i]
                return interview
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения собеседования: {e}")
            return None
    
    def get_user_interviews(self, user_id: int, days_ahead: int = 7) -> List[Dict]:
        """Получает предстоящие собеседования пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            future_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
            today = datetime.now().strftime('%Y-%m-%d')
            
            cursor.execute("""
            SELECT i.*, c.name as candidate_name, c.city as candidate_city, 
                   v.title as vacancy_title
            FROM interviews i
            LEFT JOIN candidates c ON i.candidate_id = c.id
            LEFT JOIN vacancies v ON c.vacancy_id = v.id
            WHERE i.user_id = ? 
            AND i.interview_date >= ?
            AND i.interview_date <= ?
            ORDER BY i.interview_date, i.interview_time
            """, (user_id, today, future_date))
            
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            interviews = []
            for row in rows:
                interview = {}
                for i, col in enumerate(column_names):
                    if col == 'reminder_sent':
                        interview[col] = bool(row[i])
                    else:
                        interview[col] = row[i]
                interviews.append(interview)
            return interviews
        except Exception as e:
            logger.error(f"❌ Ошибка получения собеседований пользователя: {e}")
            return []
    
    def update_interview_status(self, interview_id: int, status: str) -> bool:
        """Обновляет статус собеседования"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                "UPDATE interviews SET status = ? WHERE id = ?",
                (status, interview_id)
            )
            
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статуса собеседования: {e}")
            return False
    
    def get_candidate_status_history(self, candidate_id: int) -> List[Dict]:
        """Получает историю статусов кандидата"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            SELECT old_status, new_status, reason, changed_at
            FROM candidate_status_history
            WHERE candidate_id = ?
            ORDER BY changed_at DESC
            LIMIT 10
            ''', (candidate_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            history = []
            for row in rows:
                history.append({
                    'old_status': row[0],
                    'new_status': row[1],
                    'reason': row[2],
                    'changed_at': row[3]
                })
            return history
        except Exception as e:
            logger.error(f"❌ Ошибка получения истории статусов: {e}")
            return []
    
    def add_candidate_note(self, candidate_id: int, note: str, user_id: int) -> bool:
        """Добавляет заметку к кандидату"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO candidate_notes (candidate_id, user_id, note)
            VALUES (?, ?, ?)
            ''', (candidate_id, user_id, note))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка добавления заметки: {e}")
            return False
    
    def get_candidate_notes(self, candidate_id: int) -> List[Dict]:
        """Получает заметки кандидата"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            SELECT note, user_id, created_at
            FROM candidate_notes
            WHERE candidate_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            ''', (candidate_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            notes = []
            for row in rows:
                notes.append({
                    'note': row[0],
                    'user_id': row[1],
                    'created_at': row[2]
                })
            return notes
        except Exception as e:
            logger.error(f"❌ Ошибка получения заметок: {e}")
            return []
    
    def get_candidate_sources(self, owner_id: int) -> List[Dict]:
        """Получает источники кандидатов"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT 
                source,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM candidates c 
                    JOIN vacancies v ON c.vacancy_id = v.id WHERE v.owner_id = ?), 1) as percentage
            FROM candidates c
            JOIN vacancies v ON c.vacancy_id = v.id
            WHERE v.owner_id = ?
            GROUP BY source
            ORDER BY count DESC
            LIMIT 10
            ''', (owner_id, owner_id))
            
            rows = cursor.fetchall()
            conn.close()
            
            sources = []
            for row in rows:
                sources.append({
                    'source': row[0],
                    'count': row[1],
                    'percentage': row[2]
                })
            return sources
        except Exception as e:
            logger.error(f"❌ Ошибка получения источников: {e}")
            return []
    
    def save_external_vacancy(self, vacancy: Dict) -> Optional[int]:
        """Сохранение вакансии из внешних источников"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Проверяем наличие URL
            if not vacancy.get('url'):
                logger.warning("⚠️ У вакансии нет URL, добавляем заглушку")
                vacancy['url'] = f"https://www.avito.ru/вакансии/{vacancy.get('city', 'россия')}/{vacancy.get('title', 'вакансия').replace(' ', '_')}"
            
            if vacancy.get('source_id'):
                cursor.execute('''
                SELECT id FROM external_vacancies 
                WHERE source = ? AND source_id = ? AND user_id = ?
                ''', (
                    vacancy.get('source'),
                    vacancy.get('source_id'),
                    vacancy.get('user_id')
                ))
                existing = cursor.fetchone()
                
                if existing:
                    logger.info(f"✅ Вакансия уже существует: {vacancy.get('source_id')}")
                    return None
            
            cursor.execute('''
            INSERT OR REPLACE INTO external_vacancies 
            (source, source_id, title, description, salary, city, 
             contacts, requirements, url, date, user_id, status, 
             collected_at, ai_analysis, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                vacancy.get('source'),
                vacancy.get('source_id'),
                vacancy.get('title'),
                vacancy.get('description', '')[:1000],
                vacancy.get('salary'),
                vacancy.get('city'),
                vacancy.get('contacts', ''),
                json.dumps(vacancy.get('requirements', []), ensure_ascii=False),
                vacancy.get('url', ''),
                vacancy.get('date', datetime.now().isoformat()),
                vacancy.get('user_id'),
                vacancy.get('status', 'new'),
                vacancy.get('collected_at', datetime.now().isoformat()),
                json.dumps(vacancy.get('ai_analysis', {}), ensure_ascii=False),
                json.dumps(vacancy.get('raw_data', {}), ensure_ascii=False)
            ))
            
            vacancy_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            logger.info(f"✅ Сохранена внешняя вакансия: {vacancy.get('title')} с URL: {vacancy.get('url')}")
            return vacancy_id
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения внешней вакансии: {e}")
            return None
    
    def get_external_vacancies(self, user_id: int, limit: int = 50, 
                               status: str = None, source: str = None) -> List[Dict]:
        """Получение вакансий из внешних источников"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            query = '''
                SELECT * FROM external_vacancies 
                WHERE user_id = ?
            '''
            params = [user_id]
            
            if status:
                query += ' AND status = ?'
                params.append(status)
            
            if source:
                query += ' AND source = ?'
                params.append(source)
            
            query += ' ORDER BY collected_at DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            
            vacancies = []
            for row in rows:
                vac = {}
                for i, col in enumerate(column_names):
                    if col in ['requirements', 'ai_analysis', 'raw_data']:
                        try:
                            vac[col] = json.loads(row[i]) if row[i] else ([] if col == 'requirements' else {})
                        except:
                            vac[col] = [] if col == 'requirements' else {}
                    else:
                        vac[col] = row[i]
                vacancies.append(vac)
            
            conn.close()
            return vacancies
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения внешних вакансий: {e}")
            return []
    
    def get_external_vacancy_by_id(self, vacancy_id: int) -> Optional[Dict]:
        """Получение внешней вакансии по ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_vacancies WHERE id = ?", (vacancy_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                vac = {}
                for i, col in enumerate(column_names):
                    if col in ['requirements', 'ai_analysis', 'raw_data']:
                        try:
                            vac[col] = json.loads(row[i]) if row[i] else ([] if col == 'requirements' else {})
                        except:
                            vac[col] = [] if col == 'requirements' else {}
                    else:
                        vac[col] = row[i]
                return vac
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения внешней вакансии: {e}")
            return None
    
    def update_vacancy_status(self, vacancy_id: int, status: str) -> bool:
        """Обновление статуса вакансии"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE external_vacancies SET status = ? WHERE id = ?',
                (status, vacancy_id)
            )
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статуса вакансии: {e}")
            return False
    
    def save_monitoring_settings(self, user_id: int, settings: Dict) -> bool:
        """Сохранение настроек мониторинга"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM monitoring_settings WHERE user_id = ?", (user_id,))
            existing = cursor.fetchone()
            
            keywords_str = json.dumps(settings.get('keywords', []), ensure_ascii=False)
            sources_str = json.dumps(settings.get('sources', ['telegram']), ensure_ascii=False)
            is_active = 1 if settings.get('is_active', True) else 0
            
            if existing:
                cursor.execute('''
                UPDATE monitoring_settings SET
                    city = ?, keywords = ?, interval_minutes = ?,
                    is_active = ?, sources = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                ''', (
                    settings.get('city'),
                    keywords_str,
                    settings.get('interval', 60),
                    is_active,
                    sources_str,
                    user_id
                ))
            else:
                cursor.execute('''
                INSERT INTO monitoring_settings 
                (user_id, city, keywords, interval_minutes, is_active, sources)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_id,
                    settings.get('city'),
                    keywords_str,
                    settings.get('interval', 60),
                    is_active,
                    sources_str
                ))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения настроек мониторинга: {e}")
            return False
    
    def get_monitoring_settings(self, user_id: int) -> Optional[Dict]:
        """Получение настроек мониторинга"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM monitoring_settings WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            if row and column_names:
                settings = {}
                for i, col in enumerate(column_names):
                    if col in ['keywords', 'sources']:
                        try:
                            settings[col] = json.loads(row[i]) if row[i] else []
                        except:
                            settings[col] = [] if col == 'keywords' else ['telegram']
                    elif col == 'is_active':
                        settings[col] = bool(row[i])
                    else:
                        settings[col] = row[i]
                return settings
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения настроек мониторинга: {e}")
            return None
    
    def update_monitoring_status(self, user_id: int, is_active: bool) -> bool:
        """Обновление статуса мониторинга"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            active_value = 1 if is_active else 0
            cursor.execute(
                'UPDATE monitoring_settings SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                (active_value, user_id)
            )
            
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статуса мониторинга: {e}")
            return False
    
    def add_telegram_channel(self, user_id: int, channel_username: str, channel_title: str = '') -> bool:
        """Добавление Telegram канала для мониторинга"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR REPLACE INTO telegram_channels (user_id, channel_username, channel_title)
            VALUES (?, ?, ?)
            ''', (user_id, channel_username, channel_title))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка добавления Telegram канала: {e}")
            return False
    
    def get_telegram_channels(self, user_id: int) -> List[Dict]:
        """Получение Telegram каналов пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM telegram_channels WHERE user_id = ? AND is_active = 1", (user_id,))
            rows = cursor.fetchall()
            
            # Получаем названия колонок
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            conn.close()
            
            channels = []
            for row in rows:
                channel = {}
                for i, col in enumerate(column_names):
                    if col == 'is_active':
                        channel[col] = bool(row[i])
                    else:
                        channel[col] = row[i]
                channels.append(channel)
            return channels
        except Exception as e:
            logger.error(f"❌ Ошибка получения Telegram каналов: {e}")
            return []
    
    def update_channel_stats(self, channel_id: int, vacancy_count: int) -> bool:
        """Обновление статистики канала"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE telegram_channels SET last_checked = CURRENT_TIMESTAMP, vacancy_count = ? WHERE id = ?',
                (vacancy_count, channel_id)
            )
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статистики канала: {e}")
            return False
    
    def get_users_with_active_monitoring(self) -> List[Dict]:
        """Получение пользователей с активным мониторингом"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            SELECT DISTINCT m.user_id, c.company_name, c.city, m.keywords, m.sources
            FROM monitoring_settings m
            JOIN companies c ON m.user_id = c.owner_id
            WHERE m.is_active = 1
            ''')
            rows = cursor.fetchall()
            conn.close()
            
            users = []
            for row in rows:
                users.append({
                    'user_id': row[0],
                    'company_name': row[1],
                    'city': row[2],
                    'keywords': json.loads(row[3]) if row[3] else [],
                    'sources': json.loads(row[4]) if row[4] else []
                })
            return users
        except Exception as e:
            logger.error(f"❌ Ошибка получения пользователей с мониторингом: {e}")
            return []
    
    def get_vacancy_stats(self, user_id: int) -> Dict:
        """Статистика по найденным вакансиям"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived
            FROM external_vacancies 
            WHERE user_id = ?
            ''', (user_id,))
            
            stats_row = cursor.fetchone()
            
            cursor.execute('''
            SELECT source, COUNT(*) as count
            FROM external_vacancies
            WHERE user_id = ?
            GROUP BY source
            ORDER BY count DESC
            ''', (user_id,))
            
            sources_rows = cursor.fetchall()
            
            cursor.execute('''
            SELECT city, COUNT(*) as count
            FROM external_vacancies
            WHERE user_id = ? AND city IS NOT NULL AND city != ''
            GROUP BY city
            ORDER BY count DESC
            LIMIT 5
            ''', (user_id,))
            
            cities_rows = cursor.fetchall()
            
            conn.close()
            
            stats = {
                'total': stats_row[0] or 0,
                'new': stats_row[1] or 0,
                'processed': stats_row[2] or 0,
                'archived': stats_row[3] or 0
            }
            
            stats['by_source'] = {row[0]: row[1] for row in sources_rows}
            stats['by_city'] = {row[0]: row[1] for row in cities_rows}
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики вакансий: {e}")
            return {'total': 0, 'new': 0, 'processed': 0, 'archived': 0, 'by_source': {}, 'by_city': {}}
    
    def get_active_vacancy_monitoring_count(self) -> int:
        """Получение количества активных мониторингов"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM monitoring_settings WHERE is_active = 1")
            count = cursor.fetchone()[0]
            conn.close()
            return count or 0
        except Exception as e:
            logger.error(f"❌ Ошибка получения количества мониторингов: {e}")
            return 0

# Глобальный экземпляр базы данных
db = Database()