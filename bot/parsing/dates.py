"""Разворачивание занятия в конкретные календарные даты семестра.

Правила (те же, что были выработаны в дизайне):

1. Если в заметке перечислены конкретные даты («29.09, 27.10, 24.11, 22.12») —
   они и есть истина, берём их как есть.
2. «занятия с 16.09» — обычный расчёт по чётности недели, но всё, что раньше
   указанной даты, отбрасывается.
3. Иначе занятие повторяется через неделю: НЕДЕЛЯ 1 — на неделях той же
   чётности, что и первая неделя семестра, НЕДЕЛЯ 2 — на противоположных.
   Предмет, идущий каждую неделю, в исходнике просто стоит в обоих блоках,
   поэтому «еженедельно» получается само собой объединением двух записей.
"""
from __future__ import annotations

from datetime import date, timedelta

from .classify import DATE_RE, FROM_DATE_RE


def first_monday(semester_start: date) -> date:
    """Понедельник недели, в которую попадает начало семестра (может быть раньше него)."""
    return semester_start - timedelta(days=semester_start.weekday())


def _year_for_month(month: int, semester_start: date) -> int:
    """Осенний семестр не переходит через год, весенний — переходит."""
    if month >= semester_start.month:
        return semester_start.year
    return semester_start.year + 1


def explicit_dates(note: str, semester_start: date, semester_end: date) -> list[date]:
    out: list[date] = []
    for day_s, month_s in DATE_RE.findall(note or ""):
        day, month = int(day_s), int(month_s)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        try:
            d = date(_year_for_month(month, semester_start), month, day)
        except ValueError:
            continue
        if semester_start <= d <= semester_end and d not in out:
            out.append(d)
    return sorted(out)


def recurring_dates(
    weekday: int, week: int, semester_start: date, semester_end: date
) -> list[date]:
    """Все даты указанного дня недели с нужной чётностью недели."""
    anchor = first_monday(semester_start) + timedelta(days=7 if week == 2 else 0)
    out: list[date] = []
    for k in range(0, 30):
        d = anchor + timedelta(days=k * 14 + weekday - 1)
        if d > semester_end:
            break
        if d >= semester_start:
            out.append(d)
    return out


def lesson_dates(
    note: str,
    weekday: int,
    week: int,
    semester_start: date,
    semester_end: date,
) -> list[date]:
    note = note or ""
    from_match = FROM_DATE_RE.search(note)
    if from_match:
        day, month = int(from_match.group(1)), int(from_match.group(2))
        try:
            threshold = date(_year_for_month(month, semester_start), month, day)
        except ValueError:
            threshold = semester_start
        return [
            d
            for d in recurring_dates(weekday, week, semester_start, semester_end)
            if d >= threshold
        ]

    explicit = explicit_dates(note, semester_start, semester_end)
    if explicit:
        return explicit

    return recurring_dates(weekday, week, semester_start, semester_end)
