"""Выборки расписания для экранов бота."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import Group, Lesson, LessonDate, ProgramLevel, User


async def list_groups(
    session: AsyncSession, level: ProgramLevel | None = None
) -> list[Group]:
    stmt = select(Group).order_by(Group.program_level, Group.name)
    if level is not None:
        stmt = stmt.where(Group.program_level == level)
    return list(await session.scalars(stmt))


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


async def lessons_of_group(session: AsyncSession, group_id: int) -> list[Lesson]:
    stmt = (
        select(Lesson)
        .where(Lesson.group_id == group_id)
        .options(selectinload(Lesson.dates))
        .order_by(Lesson.week, Lesson.weekday, Lesson.slot_from)
    )
    return list(await session.scalars(stmt))


async def lessons_on_date(
    session: AsyncSession, group_id: int, on_date: date
) -> list[Lesson]:
    stmt = (
        select(Lesson)
        .join(LessonDate, LessonDate.lesson_id == Lesson.id)
        .where(Lesson.group_id == group_id, LessonDate.on_date == on_date)
        .options(selectinload(Lesson.dates))
        .order_by(Lesson.slot_from)
    )
    return list(await session.scalars(stmt))


async def busy_days(
    session: AsyncSession, group_id: int, since: date, until: date
) -> dict[date, list[str]]:
    """{дата: [типы занятий]} — для точек на календаре."""
    stmt = (
        select(LessonDate.on_date, Lesson.lesson_type, Lesson.slot_from)
        .join(Lesson, LessonDate.lesson_id == Lesson.id)
        .where(
            Lesson.group_id == group_id,
            LessonDate.on_date >= since,
            LessonDate.on_date <= until,
        )
        .order_by(LessonDate.on_date, Lesson.slot_from)
    )
    out: dict[date, list[str]] = defaultdict(list)
    for on_date, lesson_type, _slot in await session.execute(stmt):
        out[on_date].append(lesson_type.value)
    return dict(out)


async def busy_days_count(session: AsyncSession, group_id: int) -> int:
    stmt = (
        select(func.count(func.distinct(LessonDate.on_date)))
        .join(Lesson, LessonDate.lesson_id == Lesson.id)
        .where(Lesson.group_id == group_id)
    )
    return int(await session.scalar(stmt) or 0)


async def group_date_bounds(
    session: AsyncSession, group_id: int
) -> tuple[date | None, date | None]:
    stmt = (
        select(func.min(LessonDate.on_date), func.max(LessonDate.on_date))
        .join(Lesson, LessonDate.lesson_id == Lesson.id)
        .where(Lesson.group_id == group_id)
    )
    row = (await session.execute(stmt)).one()
    return row[0], row[1]
