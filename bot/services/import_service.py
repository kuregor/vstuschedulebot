"""Импорт расписания: ссылка на файл с сайта -> разбор -> запись в БД."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, unquote, urlparse

import aiohttp
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import (
    Group,
    ImportLog,
    Lesson,
    LessonDate,
    LessonType,
    ProgramLevel,
    ScheduleChange,
    ScheduleSource,
)
from ..parsing.dates import DATES_ALGO_VERSION, resolve_dates
from ..parsing.vstu_xls import LEVEL_MASTER, ParsedSchedule, parse_workbook
from ..parsing.workbook import OLE_MAGIC, ZIP_MAGIC
from . import changes as changes_svc

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_FILE_BYTES = 20 * 1024 * 1024


@dataclass
class ImportResult:
    program_level: ProgramLevel
    faculty: str
    course: int | None
    groups: list[str]
    lessons_count: int
    dates_count: int
    warnings: list[str]


def _semester_bounds() -> tuple[date, date]:
    return (
        date.fromisoformat(settings.semester_start),
        date.fromisoformat(settings.semester_end),
    )


def _dates_basis() -> str:
    """Отпечаток правил расчёта дат: границы семестра и версия алгоритма.

    Хранится у источника и сверяется на каждом импорте: пока он тот же, даты
    в базе и даты из нового разбора считались одинаково, и разницу между ними
    можно показывать людям.
    """
    start, end = _semester_bounds()
    return f"{start.isoformat()}|{end.isoformat()}|{DATES_ALGO_VERSION}"


def normalize_url(url: str) -> str:
    """В ссылках ВолгГТУ путь бывает с пробелами и кириллицей — кодируем его."""
    parts = urlparse(url.strip())
    return parts._replace(path=quote(unquote(parts.path), safe="/")).geturl()


@dataclass
class Download:
    """Результат обращения к файлу на сайте.

    path — None, когда сайт ответил 304: файл с прошлого раза не менялся,
    качать и разбирать нечего. etag и last_modified — валидаторы для
    следующей проверки.
    """

    path: str | None
    filename: str
    etag: str = ""
    last_modified: str = ""

    @property
    def unchanged(self) -> bool:
        return self.path is None


async def download_xls(url: str, etag: str = "", last_modified: str = "") -> Download:
    """Скачивает файл во временный каталог, если он изменился с прошлого раза.

    Сайт университета поддерживает условные запросы: с If-None-Match или
    If-Modified-Since он отвечает 304 без тела. Так плановое обновление
    45 файлов стоит 45 пустых ответов, пока учебный отдел ничего не правил.
    """
    url = normalize_url(url)
    filename = unquote(os.path.basename(urlparse(url).path)) or "schedule.xls"
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 304:
                return Download(None, filename, etag, last_modified)
            resp.raise_for_status()
            new_etag = resp.headers.get("ETag", "")
            new_modified = resp.headers.get("Last-Modified", "")
            # Читаем кусками до конца ответа. Не `content.read(n)`: он отдаёт
            # столько, сколько уже пришло, и книга приезжала обрезанной по
            # первому буферу — примерно 16 КБ вместо сотни.
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError("Файл слишком большой (> 20 МБ)")
                chunks.append(chunk)
    data = b"".join(chunks)
    if not data.startswith((OLE_MAGIC, ZIP_MAGIC)):
        raise ValueError(
            "По ссылке не книга Excel (ожидался .xls или .xlsx). "
            "Проверьте ссылку — она должна вести прямо на файл расписания."
        )
    suffix = ".xlsx" if data.startswith(ZIP_MAGIC) else ".xls"
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return Download(path, filename, new_etag, new_modified)


def _record_changes(
    session: AsyncSession,
    groups: dict[str, Group],
    before: dict[int, list[changes_svc.LessonSig]],
    after: dict[int, list[changes_svc.LessonSig]],
    *,
    with_dates: bool,
) -> None:
    """Складывает разницу по каждой группе в schedule_changes.

    Группы, которых в базе ещё не было, пропускаем: их «изменение» — это весь
    файл целиком, и рассылать такое некому и незачем.
    """
    for group in groups.values():
        was = before.get(group.id, [])
        if not was:
            continue
        now = after.get(group.id, [])
        diff = changes_svc.compare(was, now, with_dates=with_dates)
        if not diff:
            continue
        if changes_svc.is_mass_date_shift(diff, len(now)):
            # даты переехали у половины группы разом — это не учебный отдел
            log.warning(
                "Группа %s: даты сдвинулись у %s пар из %s, уведомление не шлём",
                group.name, len(diff), len(now),
            )
            continue
        session.add(
            ScheduleChange(
                group_id=group.id,
                summary=changes_svc.summarize(group.name, diff),
                count=len(diff),
            )
        )


async def save_schedule(
    session: AsyncSession,
    parsed: ParsedSchedule,
    source: str,
    *,
    level: ProgramLevel | None = None,
    faculty: str = "",
    course: int | None = None,
    source_id: int | None = None,
) -> ImportResult:
    """Перезаписывает расписание разобранных групп (старые занятия удаляются).

    Уровень, факультет и курс можно передать снаружи: когда файл взят с сайта,
    его раздел известен точно, а по шапке файла они определяются лишь на глаз
    (в «3 курс ФАСТиВ» из текста извлекается «ФИЗ» — кусок слова «ФИЗИКА»).
    """
    level = level or (
        ProgramLevel.master if parsed.program_level == LEVEL_MASTER else ProgramLevel.bachelor
    )
    faculty = faculty or parsed.faculty
    course = course or parsed.course
    sem_start, sem_end = _semester_bounds()

    # Даты сравниваем, только если правила их расчёта с прошлого импорта не
    # менялись: иначе разница между старыми и новыми — наша собственная.
    source_row = await session.get(ScheduleSource, source_id) if source_id else None
    basis = _dates_basis()
    compare_dates = source_row is not None and source_row.date_basis == basis
    if source_row is not None:
        source_row.date_basis = basis

    groups: dict[str, Group] = {}
    for name in parsed.groups:
        group = await session.scalar(
            select(Group).where(Group.name == name, Group.program_level == level)
        )
        if group is None:
            group = Group(name=name, program_level=level)
            session.add(group)
        group.faculty = faculty or group.faculty
        group.course = course or group.course
        if source_id is not None:
            group.source_id = source_id
        groups[name] = group
    await session.flush()

    # Перезалив: у затронутых групп чистим старые занятия целиком. Даты удаляем
    # явно — не полагаемся на ON DELETE CASCADE, чтобы не зависеть от того,
    # включена ли в БД проверка внешних ключей.
    group_ids = [g.id for g in groups.values()]
    # Снимок старых пар: после удаления сравнивать будет не с чем, а разница
    # нужна — из неё собирается уведомление «расписание изменилось».
    before = await changes_svc.snapshot(session, group_ids)
    if group_ids:
        old_lessons = select(Lesson.id).where(Lesson.group_id.in_(group_ids))
        await session.execute(
            delete(LessonDate).where(LessonDate.lesson_id.in_(old_lessons))
        )
        await session.execute(delete(Lesson).where(Lesson.group_id.in_(group_ids)))

    dates_count = 0
    after: dict[int, list[changes_svc.LessonSig]] = defaultdict(list)
    for item in parsed.lessons:
        group = groups.get(item.group)
        if group is None:
            continue
        origin, days = resolve_dates(
            item.raw_note, item.weekday, item.week, sem_start, sem_end, item.block_dates
        )
        lesson = Lesson(
            group_id=group.id,
            week=item.week,
            weekday=item.weekday,
            slot_from=item.slot_from,
            slot_to=item.slot_to,
            slot_label=item.slot_label,
            start_time=item.start_time,
            end_time=item.end_time,
            subject=item.subject,
            teacher=item.teacher,
            room=item.room,
            lesson_type=LessonType(item.lesson_type),
            raw_note=item.raw_note,
            date_origin=origin,
        )
        session.add(lesson)
        after[group.id].append(changes_svc.from_lesson(lesson, days))
        for day in days:
            lesson.dates.append(LessonDate(on_date=day))
            dates_count += 1

    _record_changes(session, groups, before, after, with_dates=compare_dates)

    session.add(
        ImportLog(
            source=source,
            program_level=level,
            faculty=faculty,
            course=course,
            groups_count=len(groups),
            lessons_count=len(parsed.lessons),
            status="ok",
            message="; ".join(parsed.warnings[:20]),
        )
    )
    await session.commit()

    return ImportResult(
        program_level=level,
        faculty=faculty,
        course=course,
        groups=parsed.groups,
        lessons_count=len(parsed.lessons),
        dates_count=dates_count,
        warnings=parsed.warnings,
    )


async def parse_in_thread(path: str, filename: str) -> ParsedSchedule:
    """Разбор книги в отдельном потоке.

    Шахматка на тысячу строк считается заметное время, а поток выполнения у
    бота и веб-сервера общий: в нём разбор останавливал бы и ответы Telegram,
    и отдачу приложения.
    """
    return await asyncio.to_thread(parse_workbook, path, filename)


async def import_from_url(
    session: AsyncSession,
    url: str,
    etag: str = "",
    last_modified: str = "",
    **overrides,
) -> tuple[ImportResult | None, Download]:
    """-> (результат, сведения о скачивании). Результат None — файл не менялся."""
    download = await download_xls(url, etag, last_modified)
    if download.unchanged:
        return None, download
    assert download.path is not None
    try:
        parsed = await parse_in_thread(download.path, download.filename)
        return await save_schedule(session, parsed, source=url, **overrides), download
    finally:
        os.unlink(download.path)
