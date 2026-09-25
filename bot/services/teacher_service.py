"""Преподаватели: справочник по всем файлам расписания и выборка их пар.

В файлах учебного отдела преподаватель записан как попало: «доц. Королева
И.Ю.», «Королева  И. Ю.», «доц.Королева И.Ю» и просто «Королева». Для
приложения это один человек, поэтому имя сначала приводится к виду «Королева
И.Ю.»: без должности, с одним пробелом и слитными инициалами.

Запись одной фамилией без инициалов приклеивается к полному имени, если
такая фамилия с инициалами в расписаниях ровно одна. Две Кузнецовых с разными
инициалами — и «Кузнецова» остаётся отдельной строкой: угадывать, о какой из
них речь, хуже, чем показать как есть.

Своей таблицы у преподавателей нет: справочник собирается из `lessons` на
лету. Строк там несколько тысяч, и запрос с разбором занимает миллисекунды,
а хранить отдельно значило бы следить, чтобы копия не разошлась с парами при
каждом перезаливе файла.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import Group, Lesson, ScheduleSource
from .notes_service import fold_subject

# Должность перед фамилией: «доц.», «проф.», «ст.пр.», «ст. преп.», «асс.»,
# «преп.» — с точкой и без, с пробелом и слитно.
TITLE = re.compile(r"^(?:доц|проф|ст\.?\s*преп|ст\.?\s*пр|асс|преп)\.?\s*", re.IGNORECASE)
# «Е. И.» -> «Е.И.»: пробел между инициалами встречается через раз.
INITIALS_GAP = re.compile(r"(?<=[А-ЯЁ]\.)\s+(?=[А-ЯЁ]\.?$|[А-ЯЁ]\.[А-ЯЁ])")
# «А.В» -> «А.В.»: последняя точка тоже теряется.
INITIALS_TAIL = re.compile(r"(\s[А-ЯЁ]\.[А-ЯЁ])$")
# «ГабельченкоН.И.» -> «Габельченко Н.И.»: бывает и без пробела после фамилии.
SURNAME_GLUED = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ]\.)")

# Сколько предметов показывать под именем в общем справочнике. Весь список
# уходит в приложение одним ответом, и полный перечень предметов у шести
# сотен человек раздул бы его в разы, а на экране видны первые два.
SUBJECTS_IN_DIRECTORY = 2


def clean_name(raw: str) -> str:
    """«доц.  Королева И. Ю» -> «Королева И.Ю.»; пустая строка — нет имени."""
    name = re.sub(r"\s+", " ", raw or "").strip()
    while True:
        stripped = TITLE.sub("", name, count=1).strip()
        if stripped == name:
            break
        name = stripped
    name = SURNAME_GLUED.sub(" ", name)
    name = INITIALS_GAP.sub("", name)
    name = INITIALS_TAIL.sub(r"\1.", name)
    return name


def _surname(name: str) -> str:
    return name.split(" ", 1)[0].casefold()


def build_names(raws: Iterable[str]) -> dict[str, str]:
    """{строка из файла: имя в приложении} для всех записей преподавателей.

    Сюда же сводятся записи одной фамилией — см. описание модуля.
    """
    clean = {raw: clean_name(raw) for raw in set(raws)}
    clean = {raw: name for raw, name in clean.items() if name}

    full_by_surname: dict[str, set[str]] = defaultdict(set)
    for name in clean.values():
        if " " in name:
            full_by_surname[_surname(name)].add(name)

    out: dict[str, str] = {}
    for raw, name in clean.items():
        if " " not in name:
            fulls = full_by_surname.get(name.casefold(), set())
            if len(fulls) == 1:
                name = next(iter(fulls))
        out[raw] = name
    return out


def fold_name(value: str) -> str:
    """Для поиска: регистр и «ё» не в счёт."""
    return (value or "").casefold().replace("ё", "е")


@dataclass
class TeacherSummary:
    """Строка справочника: имя, сколько пар ведёт и какие предметы."""

    name: str
    places: set = field(default_factory=set)
    subjects: Counter = field(default_factory=Counter)
    titles: dict = field(default_factory=dict)

    def add(self, week: int, weekday: int, slot_from: int, subject: str) -> None:
        key = fold_subject(subject)
        # Поток из нескольких групп на одной лекции — одна пара, а не пять:
        # место в сетке недель плюс предмет считаются один раз.
        self.places.add((week, weekday, slot_from, key))
        self.subjects[key] += 1
        self.titles.setdefault(key, subject.strip())

    def json(self, subjects_limit: int | None = None) -> dict:
        ordered = [self.titles[key] for key, _ in self.subjects.most_common(subjects_limit)]
        return {"name": self.name, "count": len(self.places), "subjects": ordered}


def summarize(rows: Iterable[tuple], names: dict[str, str]) -> dict[str, TeacherSummary]:
    """Строки (преподаватель, предмет, неделя, день, слот) -> справочник по именам."""
    out: dict[str, TeacherSummary] = {}
    for raw, subject, week, weekday, slot_from in rows:
        name = names.get(raw)
        if not name:
            continue
        summary = out.get(name)
        if summary is None:
            summary = out[name] = TeacherSummary(name)
        summary.add(week, weekday, slot_from, subject)
    return out


async def directory_rows(session: AsyncSession, group_id: int | None = None) -> list[tuple]:
    """Преподаватель, предмет и место пары — по всем группам или по одной."""
    stmt = select(
        Lesson.teacher, Lesson.subject, Lesson.week, Lesson.weekday, Lesson.slot_from
    ).where(Lesson.teacher != "")
    if group_id is not None:
        stmt = stmt.where(Lesson.group_id == group_id)
    return [tuple(row) for row in await session.execute(stmt)]


async def all_names(session: AsyncSession) -> dict[str, str]:
    """Сопоставление «как записано в файле -> имя» по всей базе.

    Нужна вся база, а не одна группа: запись одной фамилией сводится к полному
    имени, только если однофамильцев с инициалами нигде больше нет.
    """
    rows = await session.execute(select(Lesson.teacher).where(Lesson.teacher != "").distinct())
    return build_names(row[0] for row in rows)


async def lessons_of_teacher(
    session: AsyncSession, name: str
) -> list[tuple[Lesson, str]]:
    """Пары преподавателя во всех группах: (пара, название группы)."""
    raws = [raw for raw, clean in (await all_names(session)).items() if clean == name]
    if not raws:
        return []
    stmt = (
        select(Lesson, Group.name)
        .join(Group, Group.id == Lesson.group_id)
        .where(Lesson.teacher.in_(raws))
        .options(selectinload(Lesson.dates))
        .order_by(Lesson.week, Lesson.weekday, Lesson.slot_from, Lesson.start_time)
    )
    return [(lesson, group_name) for lesson, group_name in await session.execute(stmt)]


async def catalog_stamp(session: AsyncSession) -> str:
    """Версия всех загруженных файлов разом — основа ETag расписания преподавателя.

    Пары одного преподавателя разбросаны по разным файлам, и ответ устаревает,
    когда перевыложили любой из них. ETag и Last-Modified сайта меняются
    только при настоящей правке файла, `date_basis` — при смене правил дат.
    Запрос лёгкий: по строке на файл каталога, без пар.
    """
    rows = await session.execute(
        select(
            ScheduleSource.id,
            ScheduleSource.etag,
            ScheduleSource.last_modified,
            ScheduleSource.date_basis,
            ScheduleSource.lessons_count,
        )
        .where(ScheduleSource.status == "ok")
        .order_by(ScheduleSource.id)
    )
    raw = ";".join("|".join(str(part) for part in row) for row in rows)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
