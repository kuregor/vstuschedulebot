"""Источники расписания: каталог сайта в БД, загрузка и автообновление.

Пользователь выбирает в настройках уровень, факультет и курс — это и есть
конкретный файл на сайте. Выбранный файл помечается `enabled`, сразу
скачивается и дальше обновляется сам: учебный отдел перевыкладывает файлы по
тем же адресам, поэтому достаточно периодически перекачивать включённые.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Group, ProgramLevel, ScheduleSource
from ..db.session import SessionLocal
from . import vstu_site
from .import_service import import_from_url

log = logging.getLogger(__name__)

STATUS_NEW = "new"
STATUS_LOADING = "loading"
STATUS_OK = "ok"
STATUS_ERROR = "error"

LEVEL_BY_SITE = {
    vstu_site.LEVEL_BACHELOR: ProgramLevel.bachelor,
    vstu_site.LEVEL_MASTER: ProgramLevel.master,
}

# Одновременно тянем несколько файлов, но не весь каталог разом: сайт
# университета отвечает неспешно, а книги по сотне-другой килобайт.
REFRESH_CONCURRENCY = 3

# Один и тот же файл не должен качаться дважды одновременно: настройки может
# открыть сразу несколько человек, и фоновое обновление идёт своим чередом.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(url: str) -> asyncio.Lock:
    lock = _locks.get(url)
    if lock is None:
        lock = _locks[url] = asyncio.Lock()
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def sync_catalog(session: AsyncSession, force: bool = False) -> list[ScheduleSource]:
    """Сверяет каталог сайта с таблицей источников -> список всех источников.

    Строки не удаляются: если файл с сайта пропал, пользовательский выбор и
    уже загруженное расписание из-за этого терять незачем.
    """
    catalog = await vstu_site.get_catalog(force=force)

    known = {row.url: row for row in await session.scalars(select(ScheduleSource))}
    for item in catalog.files:
        row = known.get(item.url)
        if row is None:
            row = ScheduleSource(url=item.url, status=STATUS_NEW)
            session.add(row)
            known[item.url] = row
        row.dep = item.dep
        row.dep_title = item.dep_title
        row.faculty = item.faculty
        row.program_level = LEVEL_BY_SITE[item.level]
        row.course = item.course
        row.title = item.title
        row.file_name = item.file_name
    await session.commit()

    if catalog.errors:
        log.warning("Каталог сайта прочитан не полностью: %s", "; ".join(catalog.errors))
    return await list_sources(session)


async def list_sources(session: AsyncSession) -> list[ScheduleSource]:
    stmt = select(ScheduleSource).order_by(
        ScheduleSource.program_level,
        ScheduleSource.faculty,
        ScheduleSource.course,
        ScheduleSource.title,
    )
    return list(await session.scalars(stmt))


async def get_source(session: AsyncSession, url: str) -> ScheduleSource | None:
    return await session.scalar(select(ScheduleSource).where(ScheduleSource.url == url))


async def groups_of_source(session: AsyncSession, source_id: int) -> list[Group]:
    stmt = (
        select(Group).where(Group.source_id == source_id).order_by(Group.name)
    )
    return list(await session.scalars(stmt))


async def load_source(session: AsyncSession, source: ScheduleSource) -> ScheduleSource:
    """Скачивает и разбирает файл источника, обновляя его состояние в БД."""
    url = source.url
    async with _lock_for(url):
        # Пока ждали замок, файл мог загрузить кто-то другой — перечитываем.
        source = await get_source(session, url) or source
        source.status = STATUS_LOADING
        await session.commit()

        try:
            result = await import_from_url(
                session,
                url,
                level=source.program_level,
                faculty=source.faculty,
                course=source.course,
                source_id=source.id,
            )
        except Exception as exc:  # noqa: BLE001 — причину показываем в настройках
            log.exception("Источник %s не загрузился", url)
            source = await get_source(session, url) or source
            source.status = STATUS_ERROR
            source.message = str(exc)[:500]
            await session.commit()
            return source

        source = await get_source(session, url) or source
        source.status = STATUS_OK
        source.message = "; ".join(result.warnings[:5])
        source.groups_count = len(result.groups)
        source.lessons_count = result.lessons_count
        source.fetched_at = _now()
        await session.commit()
        return source


async def set_enabled(
    session: AsyncSession, url: str, enabled: bool
) -> ScheduleSource | None:
    """Включает или выключает источник; включённый сразу загружается."""
    source = await get_source(session, url)
    if source is None:
        return None
    source.enabled = enabled
    await session.commit()
    if enabled and source.status != STATUS_OK:
        source = await load_source(session, source)
    return source


def is_stale(source: ScheduleSource, max_age: timedelta) -> bool:
    if source.status != STATUS_OK or source.fetched_at is None:
        return True
    fetched = source.fetched_at
    if fetched.tzinfo is None:  # на случай базы без часового пояса
        fetched = fetched.replace(tzinfo=timezone.utc)
    return _now() - fetched > max_age


async def refresh_enabled(max_age: timedelta) -> int:
    """Перекачивает включённые источники, которые давно не обновлялись.

    Каждый файл берёт свою сессию БД: загрузка идёт параллельно, а одна
    сессия SQLAlchemy для одновременной работы не предназначена.
    """
    async with SessionLocal() as session:
        sources = [
            row
            for row in await list_sources(session)
            if row.enabled and is_stale(row, max_age)
        ]
        urls = [row.url for row in sources]

    if not urls:
        return 0

    semaphore = asyncio.Semaphore(REFRESH_CONCURRENCY)

    async def one(url: str) -> None:
        async with semaphore, SessionLocal() as session:
            source = await get_source(session, url)
            if source is not None:
                await load_source(session, source)

    await asyncio.gather(*(one(url) for url in urls))
    log.info("Обновлено расписаний: %s", len(urls))
    return len(urls)
