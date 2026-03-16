from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from config import settings


engine = create_engine(
    settings.db_url,
    echo=False,
    future=True,
)


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db() -> None:
    from models import Company, Vacancy, Candidate, InterviewSlot, VacancyTemplate  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Миграция: добавить колонку interview_slot_text, если её ещё нет (для старых БД)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE candidates ADD COLUMN interview_slot_text VARCHAR(255)"))
            conn.commit()
    except Exception:
        pass


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

