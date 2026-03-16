from __future__ import annotations

from typing import Optional, Tuple

import re

from aiogram.types import Message

from db import get_session
from models import Candidate, CandidateStatus, Vacancy, Company


def _parse_contact_and_city(text: str, fallback_city: str) -> Tuple[str, str]:
    """
    Простейший парсер контакта и города из текста.
    Ищем:
    - @username
    - телефон
    - упоминание города вида 'г. Казань'
    """
    contact = ""
    city = fallback_city

    username_match = re.search(r"@[\w\d_]{3,32}", text)
    if username_match:
        contact = username_match.group(0)

    phone_match = re.search(r"(\+?\d[\d \-\(\)]{8,}\d)", text)
    if phone_match and not contact:
        contact = phone_match.group(1).strip()

    city_match = re.search(r"г\.\s*([А-ЯЁA-Z][а-яёa-z\- ]{2,})", text)
    if city_match:
        city = city_match.group(1).strip()

    return contact, city


async def import_candidate_from_forward(message: Message) -> Optional[int]:
    """
    Полуавтоматический импорт кандидата из пересланного сообщения Telegram.

    Использование:
    — собственник пересылает пост/сообщение кандидата с описанием в чат с ботом
    — пишет команду /from_tg в ответ на это сообщение
    Функция пытается создать карточку кандидата по последней вакансии.
    """
    if not message.reply_to_message or not message.reply_to_message.text:
        await message.answer("Эту команду нужно отправить в ответ на пересланное сообщение кандидата.")
        return None

    text = message.reply_to_message.text.strip()
    if not text:
        await message.answer("В пересланном сообщении нет текста, нечего парсить.")
        return None

    with get_session() as session:
        vacancies = (
            session.query(Vacancy)
            .join(Company, Vacancy.company_id == Company.id)
            .filter(Company.owner_id == message.from_user.id)
            .order_by(Vacancy.created_at.desc())
            .all()
        )
        if not vacancies:
            await message.answer("Сначала создайте вакансию: /new_job")
            return None
        vacancy = vacancies[0]

        # Наивный парсинг: первая строка — имя/ник, остальное — опыт/описание.
        lines = text.splitlines()
        name = lines[0][:255]
        experience_text = "\n".join(lines[1:]) or text

        contact, city = _parse_contact_and_city(text, fallback_city=vacancy.city)

        candidate = Candidate(
            vacancy_id=vacancy.id,
            name_or_nick=name,
            contact=contact,
            city=city,
            experience_text=experience_text,
            skills_text="",
            source="telegram",
            source_link="",
            raw_text=text,
            status=CandidateStatus.FOUND.value,
        )
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id

    await message.answer(
        "Кандидат добавлен из Telegram.\n"
        f"Имя/ник: {name}\n"
        f"Город: {city}\n"
        f"Контакт: {contact or '—'}\n\n"
        "Полный текст сохранён в карточке. Скоринг и воронка доступны через /candidates и /pipeline."
    )

    return candidate_id

