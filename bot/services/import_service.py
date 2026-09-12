"""Импорт расписания: ссылка на файл с сайта -> разбор -> запись в БД."""
from __future__ import annotations

import asyncio
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
from ..parsing.workbook import OLE_MAGIC, ZIP_MAGIC

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
