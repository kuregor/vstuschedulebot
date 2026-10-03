"""Выборки расписания для API приложения."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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


async def ensure_user(session: AsyncSession, telegram_id: int) -> None:
    """Заводит строку пользователя, если её ещё нет.

    По списку пользователей бот переставляет кнопку меню, когда меняется
    адрес приложения. Человек, который нажал /start и ещё не выбрал группу,
    в этот список иначе не попадал бы, и его кнопка осталась бы на мёртвом
    адресе: /start ставит кнопку лично его чату, а она главнее общей.
    """
    await session.execute(
        insert(User).values(telegram_id=telegram_id).on_conflict_do_nothing()
    )
    await session.commit()


async def set_user_group(session: AsyncSession, telegram_id: int, group_id: int) -> None:
    user = await get_user(session, telegram_id)
    if user is None:
        session.add(User(telegram_id=telegram_id, group_id=group_id))
    else:
        user.group_id = group_id
    await session.commit()


async def set_user_notify(session: AsyncSession, telegram_id: int, on: bool) -> None:
    """Включает или выключает сообщения об изменениях расписания."""
    user = await get_user(session, telegram_id)
    if user is None:
        session.add(User(telegram_id=telegram_id, notify=on))
    else:
        user.notify = on
    await session.commit()


async def set_user_prefs(session: AsyncSession, telegram_id: int, prefs: dict) -> dict:
    """Дописывает личные настройки приложения -> все настройки после записи.

    Ключи, которых в запросе нет, остаются как были: настройка, заведённая
    новой версией приложения, не должна пропадать из-за старой версии,
    оставшейся в кэше другого телефона. Не изменилось ничего — не пишем.
    """
    user = await get_user(session, telegram_id)
    merged = {**((user.prefs if user is not None else None) or {}), **prefs}
    if user is None:
        session.add(User(telegram_id=telegram_id, prefs=merged))
    elif user.prefs != merged:
        user.prefs = merged
    else:
        return merged
    await session.commit()
    return merged


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
