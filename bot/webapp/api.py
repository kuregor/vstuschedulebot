"""JSON API для Mini App.

Структура ответа повторяет модель данных макета: недели -> дни -> пары,
сгруппированные по времени начала (в макете параллельные пары в одно время
показываются одним блоком с меткой «2 ПАРЫ»).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from ..config import settings
from ..db.models import Group, Lesson, ProgramLevel, ScheduleSource
from ..utils.formatting import (
    DOW_FULL,
    DOW_SHORT,
    MONTHS_NOM,
    plural_pairs,
    week_of,
)

MONTH_SHORT = [
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


def semester_bounds() -> tuple[date, date]:
    return (
        date.fromisoformat(settings.semester_start),
        date.fromisoformat(settings.semester_end),
    )


LEVEL_TITLES = [
    (ProgramLevel.bachelor, "Бакалавриат"),
    (ProgramLevel.master, "Магистратура"),
]


def _updated_label(moment: datetime | None) -> str:
    if moment is None:
        return ""
    local = moment.astimezone() if moment.tzinfo else moment
    return f"{local.day:02d}.{local.month:02d} в {local.hour:02d}:{local.minute:02d}"


def source_json(source: ScheduleSource) -> dict:
    return {
        "url": source.url,
        "dep": source.dep,
        "level": source.program_level.value,
        "faculty": source.faculty,
        "course": source.course,
        "title": source.title,
        "file_name": source.file_name,
        "enabled": source.enabled,
        "status": source.status,
        "message": source.message or "",
        "groups": source.groups_count,
        "lessons": source.lessons_count,
        "updated": _updated_label(source.fetched_at),
    }


def settings_json(
    sources: list[ScheduleSource],
    groups: list[Group],
    selected_group: Group | None,
) -> dict:
    """Данные экрана настроек: уровень -> факультет -> курс (файл) -> группа.

    Факультеты собираются из самих файлов, а не из списка разделов сайта: у
    магистратуры все расписания лежат на одной странице, и факультет там
    известен только по названию файла («1 курс ФЭВТ»).
    """
    levels = []
    for level, title in LEVEL_TITLES:
        by_faculty: dict[str, dict] = {}
        for source in sources:
            if source.program_level is not level:
                continue
            key = source.faculty or source.dep
            faculty = by_faculty.get(key)
            if faculty is None:
                faculty = by_faculty[key] = {
                    "key": key,
                    "short": source.faculty or source.dep_title,
                    "title": source.dep_title,
                    "files": [],
                }
            faculty["files"].append(source_json(source))
        levels.append(
            {
                "key": level.value,
                "title": title,
                "faculties": sorted(by_faculty.values(), key=lambda f: f["short"]),
            }
        )

    selected_source = selected_group.source if selected_group else None
    return {
        "levels": levels,
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "selected": {
            "level": selected_source.program_level.value if selected_source else "",
            "faculty": (selected_source.faculty or selected_source.dep)
            if selected_source
            else "",
            "url": selected_source.url if selected_source else "",
            "group_id": selected_group.id if selected_group else None,
            "group_name": selected_group.name if selected_group else "",
        },
    }


def group_json(group: Group) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "level": group.program_level.value,
        "level_title": group.level_title,
        "faculty": group.faculty or "",
        "course": group.course,
    }


def _lesson_json(lesson: Lesson) -> dict:
    days = sorted(d.on_date for d in lesson.dates)
    return {
        "id": lesson.id,
        "week": lesson.week,
        "weekday": lesson.weekday,
        "slot": lesson.slot_label,
        "start": lesson.start_time,
        "end": lesson.end_time,
        "type": lesson.lesson_type.value,
        "title": lesson.subject,
        "room": lesson.room,
        "teacher": lesson.teacher,
        "note": lesson.raw_note,
        "dates": [d.isoformat() for d in days],
    }


def _day_dates_label(week: int, weekday: int, start: date, end: date) -> str:
    """«01 · 15 · 29 сент» — как в макете: даты этого дня в первом месяце."""
    first_monday = start - timedelta(days=start.weekday())
    anchor = first_monday + timedelta(days=7 if week == 2 else 0)
    out: list[str] = []
    month = None
    for k in range(0, 30):
        day = anchor + timedelta(days=k * 14 + weekday - 1)
        if day > end:
            break
        if day < start:
            continue
        if month is None:
            month = day.month
        if day.month != month:
            break
        out.append(f"{day.day:02d}")
    if not out:
        return ""
    return " · ".join(out) + f" {MONTH_SHORT[(month or start.month) - 1]}"


def _week_range_label(week: int, start: date) -> str:
    first_monday = start - timedelta(days=start.weekday())
    monday = first_monday + timedelta(days=7 if week == 2 else 0)
    left = max(monday, start)
    right = monday + timedelta(days=5)
    if left.month == right.month:
        return f"{left.day:02d}–{right.day:02d} {MONTH_SHORT[left.month - 1]}"
    return (
        f"{left.day:02d} {MONTH_SHORT[left.month - 1]} – "
        f"{right.day:02d} {MONTH_SHORT[right.month - 1]}"
    )


def schedule_json(group: Group, lessons: list[Lesson], today: date | None = None) -> dict:
    start, end = semester_bounds()
    today = today or date.today()

    by_week: dict[int, dict[int, list[Lesson]]] = defaultdict(lambda: defaultdict(list))
    for lesson in lessons:
        by_week[lesson.week][lesson.weekday].append(lesson)

    weeks = []
    for week in (1, 2):
        days_map = by_week.get(week, {})
        total = sum(len(v) for v in days_map.values())
        days = []
        for weekday in range(1, 7):
            items = sorted(days_map.get(weekday, []), key=lambda x: (x.slot_from, x.start_time))
            is_today = today.isoweekday() == weekday and week_of(today, start) == week
            days.append(
                {
                    "dow": weekday,
                    "short": DOW_SHORT[weekday - 1],
                    "name": DOW_FULL[weekday - 1],
                    "dates": _day_dates_label(week, weekday, start, end),
                    "is_today": is_today,
                    "lessons": [_lesson_json(lesson) for lesson in items],
                }
            )
        weeks.append(
            {
                "id": week,
                "label": f"НЕДЕЛЯ {week}",
                "range": _week_range_label(week, start),
                "count": plural_pairs(total),
                "days": days,
            }
        )

    # индекс «дата -> id пар» для вкладки «Календарь»
    index: dict[str, list[int]] = defaultdict(list)
    for lesson in lessons:
        for item in lesson.dates:
            index[item.on_date.isoformat()].append(lesson.id)

    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        months.append(
            {
                "year": cursor.year,
                "month": cursor.month,
                "name": MONTHS_NOM[cursor.month - 1],
            }
        )
        cursor = date(
            cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1
        )

    busy_total = len({d for d in index})
    study_total = 0
    day = start
    while day <= end:
        if day.isoweekday() != 7:
            study_total += 1
        day += timedelta(days=1)

    return {
        "group": group_json(group),
        "semester": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "today": today.isoformat(),
            "current_week": week_of(today, start),
            "title": "Осенний семестр" if start.month >= 7 else "Весенний семестр",
        },
        "weeks": weeks,
        "months": months,
        "index": index,
        "busy": {"days": busy_total, "study_days": study_total},
    }
