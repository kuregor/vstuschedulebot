"""Выборки расписания для API приложения."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import Group, Lesson, ScheduleSource, User


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def all_user_ids(session: AsyncSession) -> list[int]:
    """Telegram id всех, кто уже пользовался ботом."""
    rows = await session.execute(select(User.telegram_id))
    return [row[0] for row in rows]


async def set_user_group(session: AsyncSession, telegram_id: int, group_id: int) -> None:
    user = await get_user(session, telegram_id)
    if user is None:
        session.add(User(telegram_id=telegram_id, group_id=group_id))
    else:
        user.group_id = group_id
    await session.commit()


async def source_stamp(session: AsyncSession, source_id: int | None) -> str:
    """Метка версии файла, из которого собрано расписание группы.

    ETag и Last-Modified меняются только когда учебный отдел перевыложил файл
    на сайте, — в отличие от fetched_at, который обновляется на каждой
    проверке, даже если сайт ответил 304. Поэтому метка годится в основу
    ETag ответа: пока она та же, расписание группы не менялось.

    Запрос лёгкий: одна строка по первичному ключу, без пар и их дат.
    """
    if source_id is None:
        return ""
    row = (
        await session.execute(
            select(ScheduleSource.etag, ScheduleSource.last_modified).where(
                ScheduleSource.id == source_id
            )
        )
    ).first()
    return f"{row[0]}|{row[1]}" if row is not None else ""


async def lessons_of_group(session: AsyncSession, group_id: int) -> list[Lesson]:
    stmt = (
        select(Lesson)
        .where(Lesson.group_id == group_id)
        .options(selectinload(Lesson.dates))
        .order_by(Lesson.week, Lesson.weekday, Lesson.slot_from)
    )
    return list(await session.scalars(stmt))
