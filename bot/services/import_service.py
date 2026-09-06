"""Импорт расписания: ссылка или файл .xls -> разбор -> запись в БД."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, unquote, urlparse

import aiohttp
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import Group, ImportLog, Lesson, LessonDate, LessonType, ProgramLevel
from ..parsing.dates import lesson_dates
from ..parsing.vstu_xls import LEVEL_MASTER, ParsedSchedule, parse_workbook

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


def normalize_url(url: str) -> str:
    """В ссылках ВолгГТУ путь бывает с пробелами и кириллицей — кодируем его."""
    parts = urlparse(url.strip())
    return parts._replace(path=quote(unquote(parts.path), safe="/")).geturl()


async def download_xls(url: str) -> tuple[str, str]:
    """Скачивает файл во временный каталог -> (путь, исходное имя файла)."""
    url = normalize_url(url)
    filename = unquote(os.path.basename(urlparse(url).path)) or "schedule.xls"
    async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.content.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Файл слишком большой (> 20 МБ)")
    if not data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError(
            "Это не файл .xls (ожидался формат Excel 97-2003). "
            "Проверьте ссылку — она должна вести прямо на файл расписания."
        )
    fd, path = tempfile.mkstemp(suffix=".xls")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path, filename


async def save_schedule(
    session: AsyncSession, parsed: ParsedSchedule, source: str
) -> ImportResult:
    """Перезаписывает расписание разобранных групп (старые занятия удаляются)."""
    level = (
        ProgramLevel.master if parsed.program_level == LEVEL_MASTER else ProgramLevel.bachelor
    )
    sem_start, sem_end = _semester_bounds()

    groups: dict[str, Group] = {}
    for name in parsed.groups:
        group = await session.scalar(
            select(Group).where(Group.name == name, Group.program_level == level)
        )
        if group is None:
            group = Group(name=name, program_level=level)
            session.add(group)
        group.faculty = parsed.faculty or group.faculty
        group.course = parsed.course or group.course
        groups[name] = group
    await session.flush()

    # Перезалив: у затронутых групп чистим старые занятия целиком. Даты удаляем
    # явно — не полагаемся на ON DELETE CASCADE, чтобы не зависеть от того,
    # включена ли в БД проверка внешних ключей.
    group_ids = [g.id for g in groups.values()]
    if group_ids:
        old_lessons = select(Lesson.id).where(Lesson.group_id.in_(group_ids))
        await session.execute(
            delete(LessonDate).where(LessonDate.lesson_id.in_(old_lessons))
        )
        await session.execute(delete(Lesson).where(Lesson.group_id.in_(group_ids)))

    dates_count = 0
    for item in parsed.lessons:
        group = groups.get(item.group)
        if group is None:
            continue
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
        )
        session.add(lesson)
        for day in lesson_dates(item.raw_note, item.weekday, item.week, sem_start, sem_end):
            lesson.dates.append(LessonDate(on_date=day))
            dates_count += 1

    session.add(
        ImportLog(
            source=source,
            program_level=level,
            faculty=parsed.faculty,
            course=parsed.course,
            groups_count=len(groups),
            lessons_count=len(parsed.lessons),
            status="ok",
            message="; ".join(parsed.warnings[:20]),
        )
    )
    await session.commit()

    return ImportResult(
        program_level=level,
        faculty=parsed.faculty,
        course=parsed.course,
        groups=parsed.groups,
        lessons_count=len(parsed.lessons),
        dates_count=dates_count,
        warnings=parsed.warnings,
    )


async def import_from_url(session: AsyncSession, url: str) -> ImportResult:
    path, filename = await download_xls(url)
    try:
        parsed = parse_workbook(path, filename)
        return await save_schedule(session, parsed, source=url)
    finally:
        os.unlink(path)


async def import_from_file(
    session: AsyncSession, path: str, filename: str
) -> ImportResult:
    parsed = parse_workbook(path, filename)
    return await save_schedule(session, parsed, source=filename)
