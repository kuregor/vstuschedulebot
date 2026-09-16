"""Что изменилось в расписании группы: сравнение старых пар с новыми.

Учебный отдел правит файл на сайте прямо по ходу семестра — переносит пару в
другую аудиторию, меняет преподавателя, добавляет занятие в пустой слот.
Файл при этом перевыкладывается целиком, и бот перезаливает расписание всех
групп из него. Разницу видно только здесь: до того, как старые пары удалены.

Пары сопоставляются по месту в сетке и названию предмета — «вторник недели 1,
2 пара, Математический анализ». Всё остальное (аудитория, преподаватель, тип,
время) считается подробностями: они попадают в текст как «было → стало».
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from ..db.models import Lesson, LessonDate
from ..parsing.dates import ORIGIN_CALC
from ..utils.formatting import DOW_FULL

TYPE_TITLES = {"lek": "лекция", "sem": "практика", "lab": "лаба"}

# Больше двух десятков строк в сообщении никто не читает, а Telegram режет
# текст по 4096 символов. Остаток сворачивается в «и ещё N изменений».
MAX_LINES = 20
# Сколько дат перечислять в строке про перенос, прежде чем свернуть в «+N».
MAX_DATES = 4


@dataclass(frozen=True)
class LessonSig:
    """Пара в том виде, в каком её сравнивают: без id, дат и текста из файла."""

    week: int
    weekday: int
    slot_from: int
    slot_label: str
    start: str
    end: str
    subject: str
    teacher: str
    room: str
    kind: str
    # даты занятия строками «ГГГГ-ММ-ДД» и то, откуда они взялись
    dates: tuple[str, ...] = ()
    origin: str = ORIGIN_CALC

    @property
    def key(self) -> tuple[int, int, int, str]:
        """Место в сетке плюс предмет — по нему пара узнаётся в новом файле."""
        return (self.week, self.weekday, self.slot_from, _fold(self.subject))

    @property
    def place(self) -> str:
        return f"{self.slot_label} пара, {self.start}"


def _fold(value: str) -> str:
    """Название предмета для сравнения: регистр и лишние пробелы не в счёт."""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


@dataclass(frozen=True)
class Change:
    kind: str  # add | remove | edit
    lesson: LessonSig
    details: tuple[str, ...] = ()
    # перенос занятия: отдельно от остальных подробностей, потому что по нему
    # отличают правку учебного отдела от нашего собственного пересчёта дат
    dates: str = ""


def from_lesson(lesson: Lesson, dates: list | None = None) -> LessonSig:
    days = dates if dates is not None else [d.on_date for d in lesson.dates]
    return LessonSig(
        week=lesson.week,
        weekday=lesson.weekday,
        slot_from=lesson.slot_from,
        slot_label=lesson.slot_label,
        start=lesson.start_time,
        end=lesson.end_time,
        subject=lesson.subject,
        teacher=lesson.teacher or "",
        room=lesson.room or "",
        kind=lesson.lesson_type.value
        if hasattr(lesson.lesson_type, "value")
        else str(lesson.lesson_type),
        dates=tuple(sorted(d.isoformat() for d in days)),
        origin=lesson.date_origin or ORIGIN_CALC,
    )


def _short(iso_date: str) -> str:
    """«2026-10-13» -> «13.10» — как даты подписаны в приложении."""
    parts = iso_date.split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else iso_date


def _some(days: list[str]) -> str:
    shown = ", ".join(_short(d) for d in days[:MAX_DATES])
    rest = len(days) - MAX_DATES
    return shown + (f" и ещё {rest}" if rest > 0 else "")


def _dates_detail(before: LessonSig, after: LessonSig, today: str) -> str:
    """Изменение дат занятия одной строкой; пусто — сравнивать нечего.

    Сравниваются только даты, прочитанные из файла: посчитанные по чётности
    (ORIGIN_CALC) держатся на границах семестра, а не на расписании, и
    «разница» в них означала бы лишь то, что мы сами поменяли настройки.
    Смена происхождения — из той же породы: в новом файле появились числа
    там, где их не было, и сравнивать эти списки бессмысленно.

    Прошедшие даты отбрасываем: перенос вчерашней пары — не новость, а на
    старых числах учебный отдел правит файл чаще всего.
    """
    if ORIGIN_CALC in (before.origin, after.origin) or before.origin != after.origin:
        return ""
    was = [d for d in before.dates if d >= today]
    now = [d for d in after.dates if d >= today]
    gone = [d for d in was if d not in now]
    added = [d for d in now if d not in was]
    if not gone and not added:
        return ""
    if len(gone) == 1 and len(added) == 1:
        return f"перенос {_short(gone[0])} → {_short(added[0])}"
    parts = []
    if added:
        parts.append("+" + _some(added))
    if gone:
        parts.append("−" + _some(gone))
    return "даты " + ", ".join(parts)


def _details(before: LessonSig, after: LessonSig) -> tuple[str, ...]:
    """Чем отличаются две пары одного предмета в одном слоте."""
    out: list[str] = []
    if before.room != after.room:
        out.append(f"аудитория {before.room or '—'} → {after.room or '—'}")
    if before.teacher != after.teacher:
        out.append(f"преподаватель {before.teacher or '—'} → {after.teacher or '—'}")
    if before.kind != after.kind:
        out.append(
            f"{TYPE_TITLES.get(before.kind, before.kind)} → "
            f"{TYPE_TITLES.get(after.kind, after.kind)}"
        )
    if (before.start, before.end) != (after.start, after.end):
        out.append(f"время {before.start}–{before.end} → {after.start}–{after.end}")
    return tuple(out)


def compare(
    before: list[LessonSig],
    after: list[LessonSig],
    *,
    today: date | None = None,
    with_dates: bool = False,
) -> list[Change]:
    """Старые пары против новых -> список изменений, по порядку в сетке.

    Один слот держит несколько пар с одним предметом редко, но держит
    (подгруппы), поэтому под ключом лежит список: одинаковое число строк
    сравнивается попарно, лишние считаются добавленными или убранными.

    with_dates включает сравнение дат занятий. Он выключен по умолчанию и
    поднимается только тогда, когда правила расчёта дат с прошлого импорта
    не менялись, — иначе разница получится не про расписание (см. _dates_detail).
    """
    today_iso = (today or date.today()).isoformat()
    old_map: dict[tuple, list[LessonSig]] = defaultdict(list)
    new_map: dict[tuple, list[LessonSig]] = defaultdict(list)
    for sig in before:
        old_map[sig.key].append(sig)
    for sig in after:
        new_map[sig.key].append(sig)

    changes: list[Change] = []
    for key in set(old_map) | set(new_map):
        olds, news = old_map.get(key, []), new_map.get(key, [])
        for was, now in zip(olds, news):
            details = _details(was, now)
            moved = _dates_detail(was, now, today_iso) if with_dates else ""
            if details or moved:
                changes.append(Change("edit", now, details, moved))
        for gone in olds[len(news):]:
            changes.append(Change("remove", gone))
        for fresh in news[len(olds):]:
            changes.append(Change("add", fresh))

    changes.sort(key=lambda c: (c.lesson.week, c.lesson.weekday, c.lesson.slot_from))
    return changes


def _line(change: Change) -> str:
    """Одна строка сообщения. Текст из файла экранируем: сообщения идут HTML."""
    lesson = change.lesson
    subject = html.escape(lesson.subject)
    place = html.escape(lesson.place)
    if change.kind == "add":
        tail = ", ".join(
            filter(
                None,
                [
                    TYPE_TITLES.get(lesson.kind, ""),
                    f"ауд. {html.escape(lesson.room)}" if lesson.room else "",
                    html.escape(lesson.teacher),
                ],
            )
        )
        return f"➕ {place} — <b>{subject}</b>" + (f" ({tail})" if tail else "")
    if change.kind == "remove":
        return f"➖ {place} — <b>{subject}</b> убрана"
    parts = [*change.details, *([change.dates] if change.dates else [])]
    return f"✏️ {place} — <b>{subject}</b>: " + html.escape("; ".join(parts))


def is_mass_date_shift(changes: list[Change], lessons_total: int) -> bool:
    """Похоже ли это на пересчёт дат у всей группы, а не на правку расписания.

    Учебный отдел переносит одно-два занятия; если же «переехала» половина
    пар и больше ничего не изменилось — колонки с числами в файле съехали
    или мы сами поправили разбор. Рассылать такое нельзя: двадцать строк
    про даты, которых никто не менял.
    """
    if not changes or lessons_total <= 0:
        return False
    if any(c.kind != "edit" or c.details for c in changes):
        return False
    moved = sum(1 for c in changes if c.dates)
    return moved * 2 > lessons_total


def summarize(group_name: str, changes: list[Change]) -> str:
    """Готовое сообщение: заголовок и строки, сгруппированные по дням недели."""
    head = f"🔔 Расписание изменилось — <b>{html.escape(group_name)}</b>"
    body: list[str] = []
    shown = 0
    current: tuple[int, int] | None = None
    for change in changes:
        if shown >= MAX_LINES:
            break
        day = (change.lesson.week, change.lesson.weekday)
        if day != current:
            current = day
            body.append(
                f"\n<b>Неделя {day[0]} · {DOW_FULL[day[1] - 1].lower()}</b>"
            )
        body.append(_line(change))
        shown += 1

    rest = len(changes) - shown
    if rest > 0:
        body.append(f"\n…и ещё {rest} изменений — смотрите в приложении.")
    return "\n".join([head, *body])


async def snapshot(session, group_ids: list[int]) -> dict[int, list[LessonSig]]:
    """Пары указанных групп, какими они лежат в базе сейчас.

    Вызывается перед перезаливом файла — потом этих строк уже не будет.
    Берём только сравниваемые колонки: даты занятий здесь не нужны, а тянуть
    их на каждую пару значило бы лишний запрос на сотни строк.
    """
    if not group_ids:
        return {}
    rows = list(
        await session.execute(
            select(
                Lesson.id,
                Lesson.group_id,
                Lesson.week,
                Lesson.weekday,
                Lesson.slot_from,
                Lesson.slot_label,
                Lesson.start_time,
                Lesson.end_time,
                Lesson.subject,
                Lesson.teacher,
                Lesson.room,
                Lesson.lesson_type,
                Lesson.date_origin,
            ).where(Lesson.group_id.in_(group_ids))
        )
    )
    # даты отдельным запросом: одним списком на все пары дешевле, чем связь
    # на каждую из них
    days: dict[int, list[str]] = defaultdict(list)
    if rows:
        date_rows = await session.execute(
            select(LessonDate.lesson_id, LessonDate.on_date).where(
                LessonDate.lesson_id.in_([row.id for row in rows])
            )
        )
        for lesson_id, on_date in date_rows:
            days[lesson_id].append(on_date.isoformat())

    out: dict[int, list[LessonSig]] = defaultdict(list)
    for row in rows:
        out[row.group_id].append(
            LessonSig(
                week=row.week,
                weekday=row.weekday,
                slot_from=row.slot_from,
                slot_label=row.slot_label,
                start=row.start_time,
                end=row.end_time,
                subject=row.subject,
                teacher=row.teacher or "",
                room=row.room or "",
                kind=row.lesson_type.value
                if hasattr(row.lesson_type, "value")
                else str(row.lesson_type),
                dates=tuple(sorted(days.get(row.id, []))),
                origin=row.date_origin or ORIGIN_CALC,
            )
        )
    return out
