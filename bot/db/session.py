"""Асинхронный движок и фабрика сессий."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import settings
from .models import Base

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Колонки, добавленные к уже существовавшим таблицам. create_all создаёт
# только недостающие таблицы и не трогает те, что есть, поэтому на базе,
# заведённой прошлой версией бота, такую колонку надо досоздать руками.
LATE_COLUMNS = [
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS source_id INTEGER "
    "REFERENCES schedule_sources(id) ON DELETE SET NULL",
]


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет (для MVP вместо миграций)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in LATE_COLUMNS:
            await conn.execute(text(statement))
