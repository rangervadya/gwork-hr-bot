# analytics.py
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from sqlalchemy import func, and_
from models import Candidate, CandidateStatus, Vacancy, Company
import logging

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Сервис для аналитики и статистики"""
    
    @staticmethod
    def get_pipeline_stats(vacancy_id: int) -> Dict[str, int]:
        """
        Получает статистику по воронке для конкретной вакансии
        """
        from db import get_session
        
        with get_session() as session:
            status_counts = session.query(
                Candidate.status, func.count(Candidate.id)
            ).filter(
                Candidate.vacancy_id == vacancy_id
            ).group_by(Candidate.status).all()
        
        result = {status: count for status, count in status_counts}
        
        # Добавляем нулевые значения для отсутствующих статусов
        all_statuses = [s.value for s in CandidateStatus]
        for status in all_statuses:
            if status not in result:
                result[status] = 0
        
        return result
    
    @staticmethod
    def get_source_stats(vacancy_id: int) -> Dict[str, int]:
        """
        Статистика по источникам кандидатов
        """
        from db import get_session
        
        with get_session() as session:
            source_counts = session.query(
                Candidate.source, func.count(Candidate.id)
            ).filter(
                Candidate.vacancy_id == vacancy_id
            ).group_by(Candidate.source).all()
        
        return {source: count for source, count in source_counts}
    
    @staticmethod
    def get_conversion_rates(vacancy_id: int) -> Dict[str, float]:
        """
        Рассчитывает конверсию на каждом этапе воронки
        """
        stats = AnalyticsService.get_pipeline_stats(vacancy_id)
        
        total = stats.get(CandidateStatus.FOUND.value, 0)
        if total == 0:
            return {}
        
        rates = {}
        
        # Конверсия в filtered
        filtered = stats.get(CandidateStatus.FILTERED.value, 0)
        rates['found_to_filtered'] = round((filtered / total) * 100, 1)
        
        # Конверсия в invited
        invited = stats.get(CandidateStatus.INVITED.value, 0)
        rates['filtered_to_invited'] = round((invited / max(filtered, 1)) * 100, 1)
        
        # Конверсия в answering
        answering = stats.get(CandidateStatus.ANSWERING.value, 0)
        rates['invited_to_answering'] = round((answering / max(invited, 1)) * 100, 1)
        
        # Конверсия в qualified
        qualified = stats.get(CandidateStatus.QUALIFIED.value, 0)
        rates['answering_to_qualified'] = round((qualified / max(answering, 1)) * 100, 1)
        
        # Конверсия в interview
        interview = stats.get(CandidateStatus.INTERVIEW.value, 0)
        rates['qualified_to_interview'] = round((interview / max(qualified, 1)) * 100, 1)
        
        # Общая конверсия
        rates['overall_conversion'] = round((interview / total) * 100, 1)
        
        return rates
    
    @staticmethod
    def get_time_stats(vacancy_id: int) -> Dict[str, any]:
        """
        Статистика по времени: среднее время на каждом этапе
        """
        from db import get_session
        from sqlalchemy import func
        
        with get_session() as session:
            # Среднее время от создания до приглашения
            invited_time = session.query(
                func.avg(
                    func.julianday(Candidate.last_message_at) - 
                    func.julianday(Candidate.created_at)
                )
            ).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.status == CandidateStatus.INVITED.value,
                Candidate.last_message_at.isnot(None)
            ).scalar()
            
            # Среднее время от приглашения до ответа
            answering_time = session.query(
                func.avg(
                    func.julianday(Candidate.last_reply_at) - 
                    func.julianday(Candidate.last_message_at)
                )
            ).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.status.in_([CandidateStatus.ANSWERING.value, CandidateStatus.QUALIFIED.value]),
                Candidate.last_reply_at.isnot(None),
                Candidate.last_message_at.isnot(None)
            ).scalar()
            
            # Среднее время от ответа до квалификации
            qualify_time = session.query(
                func.avg(
                    func.julianday(Candidate.qualification_date) - 
                    func.julianday(Candidate.last_reply_at)
                )
            ).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.qualification_date.isnot(None),
                Candidate.last_reply_at.isnot(None)
            ).scalar()
        
        return {
            'avg_invite_time': round(invited_time * 24, 1) if invited_time else None,  # в часах
            'avg_response_time': round(answering_time * 24, 1) if answering_time else None,  # в часах
            'avg_qualify_time': round(qualify_time * 24, 1) if qualify_time else None,  # в часах
        }
    
    @staticmethod
    def get_red_flags_stats(vacancy_id: int) -> Dict[str, int]:
        """
        Статистика по красным флагам
        """
        from db import get_session
        import json
        
        flag_counts = {}
        
        with get_session() as session:
            candidates = session.query(Candidate).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.red_flags.isnot(None)
            ).all()
            
            for c in candidates:
                if c.red_flags:
                    for flag in c.red_flags:
                        flag_counts[flag] = flag_counts.get(flag, 0) + 1
        
        return flag_counts
    
    @staticmethod
    def get_daily_stats(vacancy_id: int, days: int = 30) -> Dict[str, List]:
        """
        Ежедневная статистика за последние N дней
        """
        from db import get_session
        from sqlalchemy import func, cast, Date
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with get_session() as session:
            daily_counts = session.query(
                cast(Candidate.created_at, Date).label('date'),
                func.count(Candidate.id).label('count')
            ).filter(
                Candidate.vacancy_id == vacancy_id,
                Candidate.created_at >= cutoff_date
            ).group_by(
                cast(Candidate.created_at, Date)
            ).order_by('date').all()
        
        dates = []
        counts = []
        
        for date, count in daily_counts:
            dates.append(date.strftime('%d.%m'))
            counts.append(count)
        
        return {
            'dates': dates,
            'counts': counts
        }
    
    @staticmethod
    def get_score_distribution(vacancy_id: int) -> Dict[str, int]:
        """
        Распределение кандидатов по оценкам
        """
        from db import get_session
        
        distribution = {
            '0-20': 0,
            '21-40': 0,
            '41-60': 0,
            '61-80': 0,
            '81-100': 0
        }
        
        with get_session() as session:
            candidates = session.query(Candidate).filter(
                Candidate.vacancy_id == vacancy_id
            ).all()
            
            for c in candidates:
                score = c.score
                if score <= 20:
                    distribution['0-20'] += 1
                elif score <= 40:
                    distribution['21-40'] += 1
                elif score <= 60:
                    distribution['41-60'] += 1
                elif score <= 80:
                    distribution['61-80'] += 1
                else:
                    distribution['81-100'] += 1
        
        return distribution


def format_analytics_report(vacancy_id: int) -> str:
    """
    Форматирует аналитический отчёт для отображения в Telegram
    """
    stats = AnalyticsService.get_pipeline_stats(vacancy_id)
    sources = AnalyticsService.get_source_stats(vacancy_id)
    conversion = AnalyticsService.get_conversion_rates(vacancy_id)
    time_stats = AnalyticsService.get_time_stats(vacancy_id)
    score_dist = AnalyticsService.get_score_distribution(vacancy_id)
    daily = AnalyticsService.get_daily_stats(vacancy_id, days=7)
    
    total = stats.get(CandidateStatus.FOUND.value, 0)
    
    text = f"📊 <b>Аналитика по вакансии</b>\n\n"
    
    text += f"<b>Общая статистика:</b>\n"
    text += f"• Всего кандидатов: {total}\n"
    text += f"• Прошли фильтры: {stats.get(CandidateStatus.FILTERED.value, 0)}\n"
    text += f"• Приглашены: {stats.get(CandidateStatus.INVITED.value, 0)}\n"
    text += f"• Ответили: {stats.get(CandidateStatus.ANSWERING.value, 0)}\n"
    text += f"• Квалифицированы: {stats.get(CandidateStatus.QUALIFIED.value, 0)}\n"
    text += f"• Собеседования: {stats.get(CandidateStatus.INTERVIEW.value, 0)}\n\n"
    
    text += f"<b>Конверсия:</b>\n"
    if conversion:
        text += f"• Найдено → Отобрано: {conversion.get('found_to_filtered', 0)}%\n"
        text += f"• Отобрано → Приглашены: {conversion.get('filtered_to_invited', 0)}%\n"
        text += f"• Приглашены → Ответили: {conversion.get('invited_to_answering', 0)}%\n"
        text += f"• Ответили → Квалифицированы: {conversion.get('answering_to_qualified', 0)}%\n"
        text += f"• Квалифицированы → Собеседование: {conversion.get('qualified_to_interview', 0)}%\n"
        text += f"• <b>Общая конверсия: {conversion.get('overall_conversion', 0)}%</b>\n\n"
    
    text += f"<b>Источники кандидатов:</b>\n"
    for source, count in sources.items():
        emoji = {
            'hh': '🇭',
            'superjob': '🟢',
            'habr': '👨‍💻',
            'trudvsem': '🏢',
            'telegram': '✈️'
        }.get(source, '📋')
        percentage = round((count / total) * 100, 1) if total > 0 else 0
        text += f"• {emoji} {source}: {count} ({percentage}%)\n"
    
    text += f"\n<b>Распределение по оценкам:</b>\n"
    for range_name, count in score_dist.items():
        percentage = round((count / total) * 100, 1) if total > 0 else 0
        text += f"• {range_name}: {count} ({percentage}%)\n"
    
    if time_stats.get('avg_invite_time'):
        text += f"\n<b>Среднее время:</b>\n"
        text += f"• До приглашения: {time_stats['avg_invite_time']} ч\n"
        text += f"• До ответа: {time_stats['avg_response_time']} ч\n"
        text += f"• До квалификации: {time_stats['avg_qualify_time']} ч\n"
    
    if daily['dates']:
        text += f"\n<b>Динамика за последние 7 дней:</b>\n"
        for i, date in enumerate(daily['dates'][-7:]):
            text += f"• {date}: {daily['counts'][-7:][i]} кандидатов\n"
    
    return text