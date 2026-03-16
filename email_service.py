# email_service.py
import smtplib
import logging
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from typing import List, Optional
import os
import tempfile

from models import Candidate, Vacancy, Company
from export_utils import generate_html_report, generate_csv_report

logger = logging.getLogger(__name__)

# Настройки email для Яндекс.Почты
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.yandex.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USERNAME)


class EmailService:
    """Сервис для отправки email-уведомлений через Яндекс.Почту"""
    
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.username = SMTP_USERNAME
        self.password = SMTP_PASSWORD
        self.from_email = FROM_EMAIL
        
        if not self.username or not self.password:
            logger.warning("⚠️ SMTP credentials not configured. Email service disabled.")
        else:
            # При инициализации проверяем соединение
            logger.info(f"📧 Email service configured for {self.username}")
    
    def is_configured(self) -> bool:
        """Проверяет, настроен ли email"""
        return bool(self.username and self.password)
    
    def test_connection(self) -> bool:
        """Тестирует соединение с SMTP сервером"""
        if not self.is_configured():
            logger.error("❌ Email service not configured")
            return False
        
        try:
            logger.info(f"🔌 Тестируем соединение с {self.smtp_server}:{self.smtp_port}")
            
            # Создаем SSL контекст
            context = ssl.create_default_context()
            
            # Пробуем подключиться
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context, timeout=30) as server:
                server.login(self.username, self.password)
                logger.info(f"✅ Соединение с SMTP сервером успешно")
                return True
                
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ Ошибка аутентификации: {e}")
            logger.error("   Проверьте логин и пароль приложения в .env файле")
            logger.error("   Убедитесь, что пароль создан именно для Почты, а не для другого приложения")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP ошибка: {e}")
            return False
        except ConnectionRefusedError:
            logger.error(f"❌ Соединение отклонено. Проверьте порт: {self.smtp_port}")
            logger.error("   Для Яндекс Почты используйте порт 465 (SSL)")
            return False
        except TimeoutError:
            logger.error(f"❌ Таймаут соединения. Проверьте доступность {self.smtp_server}")
            return False
        except Exception as e:
            logger.error(f"❌ Неизвестная ошибка: {e}")
            return False
    
    def send_email(self, to_email: str, subject: str, html_content: str, attachments: List[str] = None) -> bool:
        """
        Отправляет email с HTML-содержимым через Яндекс.Почту
        """
        if not self.is_configured():
            logger.error("❌ Email service not configured")
            return False
        
        try:
            # Создаём SSL контекст
            context = ssl.create_default_context()
            
            # Создаём сообщение
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = to_email
            
            # Добавляем HTML-часть
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)
            
            # Добавляем вложения
            if attachments:
                for file_path in attachments:
                    with open(file_path, 'rb') as f:
                        part = MIMEApplication(f.read(), Name=os.path.basename(file_path))
                        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                        msg.attach(part)
            
            # Отправляем через SSL
            logger.info(f"📧 Отправка email на {to_email} через {self.smtp_server}:{self.smtp_port}")
            
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=context, timeout=30) as server:
                # Можно добавить отладку для диагностики
                # server.set_debuglevel(1)
                
                server.login(self.username, self.password)
                server.send_message(msg)
            
            logger.info(f"✅ Email успешно отправлен на {to_email}")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ Ошибка аутентификации: {e}")
            logger.error("   Проверьте логин и пароль приложения в .env файле")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP ошибка: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Не удалось отправить email: {e}")
            return False
    
    def send_daily_report(self, company: Company, vacancy: Vacancy, candidates: List[Candidate]) -> bool:
        """
        Отправляет ежедневный отчёт по вакансии
        """
        if not company.report_email:
            logger.warning(f"⚠️ Нет email для отчётов у компании {company.id}")
            return False
        
        # Генерируем HTML-отчёт
        html_report = generate_html_report(candidates, vacancy, company)
        
        # Создаём CSV-вложение
        csv_data = generate_csv_report(candidates, vacancy)
        
        # Сохраняем CSV во временный файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_data)
            csv_file = f.name
        
        subject = f"📊 Ежедневный отчёт: {vacancy.role} ({datetime.now().strftime('%d.%m.%Y')})"
        
        # Отправляем
        result = self.send_email(company.report_email, subject, html_report, [csv_file])
        
        # Удаляем временный файл
        try:
            os.unlink(csv_file)
        except:
            pass
        
        return result
    
    def send_new_candidates_alert(self, company: Company, vacancy: Vacancy, new_candidates: List[Candidate]) -> bool:
        """
        Отправляет уведомление о новых кандидатах
        """
        if not company.report_email:
            return False
        
        if not new_candidates:
            return False
        
        # Формируем HTML-содержимое
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #333; }}
                .candidate {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #4CAF50; }}
                .score {{ font-weight: bold; color: #4CAF50; }}
                .new {{ background-color: #e8f5e8; }}
            </style>
        </head>
        <body>
            <h1>🆕 Новые кандидаты за последние 24 часа</h1>
            <p>Вакансия: <b>{vacancy.role}</b> ({vacancy.city})</p>
            <p>Найдено новых кандидатов: <b>{len(new_candidates)}</b></p>
            
            <h2>Список новых кандидатов:</h2>
        """
        
        for c in new_candidates:
            html += f"""
            <div class="candidate new">
                <h3>{c.name_or_nick}</h3>
                <p>📍 {c.city} | 💼 {c.experience_text[:100]}</p>
                <p>🛠️ {c.skills_text[:100]}</p>
                <p class="score">📊 Оценка: {c.score}/100</p>
                <p>📅 Найден: {c.created_at.strftime('%d.%m.%Y %H:%M')}</p>
                <p><a href="{c.source_link}">Ссылка на профиль</a></p>
            </div>
            """
        
        html += """
            <p><small>Отчёт сгенерирован автоматически ботом GWork HR</small></p>
        </body>
        </html>
        """
        
        subject = f"🆕 Новые кандидаты: {vacancy.role} ({len(new_candidates)})"
        
        return self.send_email(company.report_email, subject, html)
    
    def send_interview_reminder(self, company: Company, candidate: Candidate, vacancy: Vacancy) -> bool:
        """
        Отправляет напоминание о предстоящем собеседовании
        """
        if not company.report_email:
            return False
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .reminder {{ background: #fff3cd; padding: 20px; border-radius: 5px; border-left: 4px solid #ffc107; }}
                .candidate {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="reminder">
                <h1>⏰ Напоминание о собеседовании</h1>
                <p>Завтра назначено собеседование с кандидатом!</p>
            </div>
            
            <div class="candidate">
                <h2>{candidate.name_or_nick}</h2>
                <p>📍 Город: {candidate.city}</p>
                <p>💼 Опыт: {candidate.experience_text[:200]}</p>
                <p>🛠️ Навыки: {candidate.skills_text[:200]}</p>
                <p>📊 Оценка: {candidate.score}/100</p>
                <p>📞 Контакт: {candidate.contact}</p>
                <p>🔗 <a href="{candidate.source_link}">Профиль кандидата</a></p>
            </div>
            
            <div class="candidate">
                <h3>Детали собеседования:</h3>
                <p>🕐 Время: {candidate.interview_slot_text}</p>
                <p>📍 Место: {company.location}</p>
            </div>
            
            <p><small>Письмо сгенерировано автоматически ботом GWork HR</small></p>
        </body>
        </html>
        """
        
        subject = f"⏰ Напоминание: собеседование с {candidate.name_or_nick} завтра"
        
        return self.send_email(company.report_email, subject, html)
    
    def send_test_email(self, to_email: str) -> bool:
        """
        Отправляет тестовое письмо для проверки настроек
        """
        # Сначала тестируем соединение
        if not self.test_connection():
            return False
        
        test_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                h1 { color: #4CAF50; }
                .success { background: #d4edda; padding: 20px; border-radius: 5px; border-left: 4px solid #28a745; }
            </style>
        </head>
        <body>
            <div class="success">
                <h1>✅ Тестовое письмо успешно отправлено!</h1>
                <p>Если вы это видите, значит email-уведомления настроены правильно.</p>
                <p>Теперь вы будете получать:</p>
                <ul>
                    <li>📊 Ежедневные отчёты по кандидатам</li>
                    <li>🆕 Уведомления о новых кандидатах</li>
                    <li>⏰ Напоминания о собеседованиях</li>
                </ul>
                <p><small>Отправлено через GWork HR Bot</small></p>
            </div>
        </body>
        </html>
        """
        
        subject = "✅ Тестовое письмо от GWork HR Bot"
        return self.send_email(to_email, subject, test_html)


# Глобальный экземпляр сервиса
email_service = EmailService()