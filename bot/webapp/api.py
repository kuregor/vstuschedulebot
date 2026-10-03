"""JSON API для Mini App.

Структура ответа повторяет модель данных макета: недели -> дни -> пары,
сгруппированные по времени начала (в макете параллельные пары в одно время
показываются одним блоком с меткой «2 ПАРЫ»).
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..config import settings
from ..db.models import Group, Lesson, ProgramLevel, ScheduleSource
from ..services.notes_service import fold_subject
from ..services.teacher_service import SUBJECTS_IN_DIRECTORY, TeacherSummary
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


# Версия формата ответа /api/schedule. Её поднимают руками, когда меняется
# структура JSON: файл на сайте при этом прежний, ETag совпал бы, и у людей
# в кэше остался бы ответ, который новое приложение уже не понимает.
SCHEDULE_FORMAT_VERSION = 2


def schedule_etag(group: Group, stamp: str, today: date) -> str:
    """Версия ответа /api/schedule — считается без чтения пар.

    В неё входит всё, от чего ответ зависит: сама группа, версия её файла на
    сайте (`stamp`), границы семестра и сегодняшняя дата. Дата обязательна:
    в ответе есть is_today, current_week и подпись «· сейчас», поэтому даже с
    неизменившимся файлом вчерашний ответ уже не годится.

    Значение уезжает клиенту и в заголовке ETag, и полем `version` в теле:
    из тела его достать проще, а в заголовке он нужен для условного запроса.
    """
    start, end = semester_bounds()
    raw = "|".join(
        [
            str(SCHEDULE_FORMAT_VERSION),
            str(group.id),
            stamp,
            today.isoformat(),
            start.isoformat(),
            end.isoformat(),
        ]
    )
    return '"' + hashlib.sha1(raw.encode()).hexdigest()[:16] + '"'


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
    """Строка каталога для экрана настроек — только то, что он показывает.

    Текст предупреждений разбора отдаётся лишь при ошибке: у 45 файлов он
    весил больше ста килобайт на каждое открытие настроек, а на экране из
    него видна только причина, по которой файл не загрузился.
    """
    return {
        "url": source.url,
        "level": source.program_level.value,
        "faculty": source.faculty,
        "course": source.course,
        "title": source.title,
        "enabled": source.enabled,
        "status": source.status,
        "message": (source.message or "") if source.status == "error" else "",
        "groups": source.groups_count,
        "lessons": source.lessons_count,
        "updated": _updated_label(source.fetched_at),
    }


def settings_json(
    sources: list[ScheduleSource],
    groups: list[Group],
    selected_group: Group | None,
    notify: bool = True,
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

    # Источник ищем в уже прочитанном списке, а не через связь группы: у
    # асинхронной сессии дозагрузка связи вне await запрещена, и такой обход
    # держался бы лишь на том, что нужная строка случайно оказалась в сессии.
    selected_source = next(
        (s for s in sources if selected_group is not None and s.id == selected_group.source_id),
        None,
    )
    return {
        "levels": levels,
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "notify": notify,
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


# Длиннее имя преподавателя быть не может: столько вмещает lessons.teacher.
TEACHER_NAME_MAX = 128


def clean_prefs(raw) -> dict:
    """Личные настройки приложения из запроса — только известные ключи.

    Бот хранит их одним JSON и сам в них не заглядывает, поэтому всё, что
    пришло, проверяется здесь: чужой ключ или не тот тип просто отбрасываются,
    а не ложатся в базу как есть.

    * `real` — режим «Реальные пары» на вкладке расписания;
    * `teacher` — чьё расписание открыто вместо группы, пусто — своя группа.
      Имя приходит из справочника уже сведённым, и переписывать его нельзя:
      приложение узнаёт открытого преподавателя в списке по точному
      совпадению.
    """
    if not isinstance(raw, dict):
        return {}
    prefs: dict = {}
    if isinstance(raw.get("real"), bool):
        prefs["real"] = raw["real"]
    if isinstance(raw.get("teacher"), str):
        prefs["teacher"] = raw["teacher"].strip()[:TEACHER_NAME_MAX]
    return prefs


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


def week_occurrence(
    week: int, start: date, end: date, today: date
) -> tuple[date, date]:
    """Ближайшее повторение недели -> (понедельник, суббота).

    Неделя 1 и неделя 2 чередуются весь семестр, поэтому «01–05 сен» — это
    лишь первая из восьми одинаковых недель, и к октябрю такая подпись
    ничего не значит. Берём ту неделю, которая идёт сейчас, а если она уже
    прошла — следующую такую же. После конца занятий остаётся последняя.
    """
    anchor = start - timedelta(days=start.weekday()) + timedelta(
        days=7 if week == 2 else 0
    )
    spans: list[tuple[date, date]] = []
    step = 0
    while True:
        monday = anchor + timedelta(days=step * 14)
        if monday > end:
            break
        saturday = monday + timedelta(days=5)
        if saturday >= start:
            spans.append((max(monday, start), min(saturday, end)))
        step += 1
    if not spans:
        return start, end
    for span in spans:
        if today <= span[1]:
            return span
    return spans[-1]


def _week_range_label(span: tuple[date, date], today: date) -> str:
    left, right = span
    if left.month == right.month:
        label = f"{left.day:02d}–{right.day:02d} {MONTH_SHORT[left.month - 1]}"
    else:
        label = (
            f"{left.day:02d} {MONTH_SHORT[left.month - 1]} – "
            f"{right.day:02d} {MONTH_SHORT[right.month - 1]}"
        )
    if left <= today <= right:
        label += " · сейчас"
    return label


def _in_grid_order(lessons: list[Lesson]) -> list[Lesson]:
    """Порядок пар в списке: неделя, день, номер пары, время начала."""
    return sorted(lessons, key=lambda x: (x.week, x.weekday, x.slot_from, x.start_time))


def schedule_json(
    group: Group,
    lessons: list[Lesson],
    today: date | None = None,
    version: str = "",
) -> dict:
    items = [_lesson_json(lesson) for lesson in _in_grid_order(lessons)]
    return {
        # По этой метке приложение потом спрашивает «не изменилось ли»
        # и в ответ получает 304 без тела.
        "version": version,
        "group": group_json(group),
        **_layout(items, today or date.today()),
    }


def _layout(items: list[dict], today: date) -> dict:
    """Недели, дни, месяцы календаря и индекс дат — из готовых строк пар.

    Общая часть расписания группы и преподавателя: чья это сетка, ей не
    важно. Пары приходят уже упорядоченными (см. `_in_grid_order`), внутри
    дня этот порядок сохраняется.
    """
    start, end = semester_bounds()

    by_week: dict[int, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        by_week[item["week"]][item["weekday"]].append(item)

    weeks = []
    for week in (1, 2):
        days_map = by_week.get(week, {})
        total = sum(len(v) for v in days_map.values())
        span = week_occurrence(week, start, end, today)
        days = []
        for weekday in range(1, 7):
            is_today = today.isoweekday() == weekday and week_of(today, start) == week
            days.append(
                {
                    "dow": weekday,
                    "short": DOW_SHORT[weekday - 1],
                    "name": DOW_FULL[weekday - 1],
                    "dates": _day_dates_label(week, weekday, start, end),
                    "is_today": is_today,
                    "lessons": days_map.get(weekday, []),
                }
            )
        weeks.append(
            {
                "id": week,
                "label": f"НЕДЕЛЯ {week}",
                "range": _week_range_label(span, today),
                # те же границы, но машинными датами: по ним приложение
                # оставляет в списке только пары, которые в это повторение
                # недели действительно идут (режим «Реальные пары»)
                "from": span[0].isoformat(),
                "to": span[1].isoformat(),
                "count": plural_pairs(total),
                "days": days,
            }
        )

    # индекс «дата -> id пар» для вкладки «Календарь»
    index: dict[str, list[int]] = defaultdict(list)
    for item in items:
        for on in item["dates"]:
            index[on].append(item["id"])

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


# ── преподаватель ──────────────────────────────────────────────────────


def teacher_etag(name: str, stamp: str, today: date) -> str:
    """Версия расписания преподавателя — как `schedule_etag`, только от
    всего каталога сразу: его пары собраны из разных файлов."""
    start, end = semester_bounds()
    raw = "|".join(
        [
            str(SCHEDULE_FORMAT_VERSION),
            "teacher",
            name,
            stamp,
            today.isoformat(),
            start.isoformat(),
            end.isoformat(),
        ]
    )
    return '"' + hashlib.sha1(raw.encode()).hexdigest()[:16] + '"'


def teachers_etag(group_id: int | None, stamp: str) -> str:
    """Версия справочника преподавателей: каталог файлов плюс группа, от
    которой считается «ведут у группы». От даты он не зависит."""
    raw = "|".join([str(SCHEDULE_FORMAT_VERSION), "teachers", str(group_id or ""), stamp])
    return '"' + hashlib.sha1(raw.encode()).hexdigest()[:16] + '"'


def _natural(value: str) -> list:
    """«САПР-1.10» после «САПР-1.9»: числа в названии группы сравниваются как числа."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def teacher_json(
    name: str,
    rows: list[tuple[Lesson, str]],
    today: date | None = None,
    version: str = "",
) -> dict:
    """Расписание преподавателя по всем группам — в той же раскладке, что у группы.

    Лекция на поток лежит в базе строкой у каждой группы потока. Для
    преподавателя это одна пара, поэтому строки с одним местом в сетке,
    предметом, типом, аудиторией и одинаковыми датами сливаются в одну, а
    группы перечисляются в `groups`. Даты в ключе нарочно: лабы двух групп в
    одном слоте через неделю — это две разные пары, и пусть они стоят блоком
    «2 ПАРЫ», как у группы.

    `id` сводной пары — меньший из id её строк, `ids` — все: по ним
    приложение узнаёт пару своей группы и вешает на неё заметки.
    """
    merged: dict[tuple, list[tuple[Lesson, str]]] = {}
    for lesson, group_name in rows:
        days = tuple(sorted(d.on_date for d in lesson.dates))
        key = (
            lesson.week,
            lesson.weekday,
            lesson.slot_from,
            lesson.slot_to,
            lesson.start_time,
            lesson.end_time,
            fold_subject(lesson.subject),
            lesson.lesson_type.value,
            fold_subject(lesson.room),
            days,
        )
        merged.setdefault(key, []).append((lesson, group_name))

    items = []
    for entries in merged.values():
        entries.sort(key=lambda entry: _natural(entry[1]))
        lessons = [lesson for lesson, _ in entries]
        item = _lesson_json(lessons[0])
        item["id"] = min(lesson.id for lesson in lessons)
        item["ids"] = sorted(lesson.id for lesson in lessons)
        item["groups"] = list(dict.fromkeys(group_name for _, group_name in entries))
        item["teacher"] = name
        item["note"] = next((lesson.raw_note for lesson in lessons if lesson.raw_note), "")
        items.append((lessons[0], item))

    items.sort(key=lambda pair: (pair[0].week, pair[0].weekday, pair[0].slot_from, pair[0].start_time))
    body = [item for _, item in items]
    groups = {group for item in body for group in item["groups"]}
    return {
        "version": version,
        "teacher": {"name": name, "lessons": len(body), "groups": len(groups)},
        **_layout(body, today or date.today()),
    }


def teachers_json(
    everyone: dict[str, TeacherSummary],
    at_group: dict[str, TeacherSummary],
    group: Group | None,
    version: str = "",
) -> dict:
    """Справочник для шторки выбора преподавателя.

    `teachers` — все, кто есть в расписаниях, для поиска по фамилии; предметов
    у каждого не больше двух, иначе ответ раздувается в разы. `mine` — те, кто
    ведёт у выбранной группы: с этого списка шторка открывается, и число пар
    с предметами здесь про эту группу.
    """
    return {
        # как у расписания: по этой метке приложение потом спрашивает
        # «не изменилось ли» и держит ответ в своём кэше
        "version": version,
        "group": group.name if group is not None else "",
        "teachers": [
            summary.json(SUBJECTS_IN_DIRECTORY)
            for summary in sorted(everyone.values(), key=lambda s: s.name)
        ],
        "mine": [summary.json() for summary in sorted(at_group.values(), key=lambda s: s.name)],
    }
