"""Отрисовка экранов дизайна средствами Telegram (HTML-текст + эмодзи-маркеры)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from html import escape

from ..db.models import Group, Lesson, LessonType

TYPE_MARK = {
    LessonType.lek: "🟢",
    LessonType.sem: "🟡",
    LessonType.lab: "🔴",
}
TYPE_TITLE = {
    LessonType.lek: "Лекция",
    LessonType.sem: "Практика",
    LessonType.lab: "Лаба",
}

DOW_SHORT = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
DOW_FULL = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

LEGEND = "🟢 лекция · 🟡 практика · 🔴 лаба"


def plural_pairs(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} пара"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} пары"
    return f"{n} пар"


def week_of(on_date: date, semester_start: date) -> int:
    """1 = числитель, 2 = знаменатель — по чётности недели от начала семестра."""
    first_monday = semester_start - timedelta(days=semester_start.weekday())
    delta_weeks = (on_date - first_monday).days // 7
    return 1 if delta_weeks % 2 == 0 else 2


def fmt_date(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def lesson_dates_text(lesson: Lesson, limit: int = 6) -> str:
    days = sorted(d.on_date for d in lesson.dates)
    if not days:
        return lesson.raw_note or "—"
    shown = [fmt_date(d) for d in days[:limit]]
    tail = f" +{len(days) - limit}" if len(days) > limit else ""
    return ", ".join(shown) + tail


def header(group: Group, semester_start: date, today: date | None = None) -> str:
    today = today or date.today()
    course = f"{group.course} курс · " if group.course else ""
    faculty = f"{group.faculty} · " if group.faculty else ""
    week = week_of(today, semester_start)
    return (
        f"<b>{escape(group.name)}</b>\n"
        f"<i>{escape(group.level_title)} · {escape(course)}{escape(faculty)}"
        f"осенний семестр</i>\n"
        f"Сейчас: <b>Неделя {week}</b> · сегодня {fmt_date(today)}"
    )


def _lesson_line(lesson: Lesson, with_dates: bool = True) -> str:
    mark = TYPE_MARK[lesson.lesson_type]
    parts = [
        f"{mark} <b>{lesson.start_time}–{lesson.end_time}</b> "
        f"<code>пары {escape(lesson.slot_label)}</code>",
        f"   {escape(lesson.subject)}",
    ]
    meta = " · ".join(
        x for x in (escape(lesson.room), escape(lesson.teacher)) if x
    )
    if meta:
        parts.append(f"   <i>{meta}</i>")
    if with_dates:
        dates = lesson_dates_text(lesson)
        if dates and dates != "—":
            parts.append(f"   <code>{escape(dates)}</code>")
    return "\n".join(parts)


TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900


def schedule_screen(
    group: Group,
    lessons: list[Lesson],
    week_filter: str,
    semester_start: date,
    today: date | None = None,
) -> str:
    """Экран «Расписание». При переполнении лимита Telegram сжимается:
    сначала убираются даты занятий, затем текст обрезается по строкам."""
    text = _schedule_body(group, lessons, week_filter, semester_start, today, True)
    if len(text) <= SAFE_LIMIT:
        return text

    text = _schedule_body(group, lessons, week_filter, semester_start, today, False)
    if len(text) <= SAFE_LIMIT:
        return text + "\n\n<i>Даты скрыты, чтобы уместиться в сообщение — они есть в карточке пары.</i>"

    hint = "\n\n<i>Показаны не все пары — выберите одну неделю кнопкой выше.</i>"
    keep = SAFE_LIMIT - len(hint)
    cut = text[:keep].rsplit("\n", 1)[0]
    return cut + hint


def _schedule_body(
    group: Group,
    lessons: list[Lesson],
    week_filter: str,
    semester_start: date,
    today: date | None,
    with_dates: bool,
) -> str:
    today = today or date.today()
    blocks: list[str] = [header(group, semester_start, today), "", LEGEND]

    by_week: dict[int, dict[int, list[Lesson]]] = defaultdict(lambda: defaultdict(list))
    for lesson in lessons:
        by_week[lesson.week][lesson.weekday].append(lesson)

    weeks = [1, 2] if week_filter == "both" else [int(week_filter)]
    for week in weeks:
        days = by_week.get(week, {})
        total = sum(len(v) for v in days.values())
        blocks.append(f"\n<b>━━ НЕДЕЛЯ {week} ━━</b>  <i>{plural_pairs(total)}</i>")
        if not days:
            blocks.append("<i>занятий нет</i>")
            continue
        for weekday in sorted(days):
            items = sorted(days[weekday], key=lambda x: x.slot_from)
            blocks.append(
                f"\n<b>{DOW_SHORT[weekday - 1]} · {DOW_FULL[weekday - 1]}</b>"
            )
            blocks.extend(_lesson_line(lesson, with_dates) for lesson in items)
    return "\n".join(blocks)


def lesson_card(lesson: Lesson, group: Group) -> str:
    mark = TYPE_MARK[lesson.lesson_type]
    title = TYPE_TITLE[lesson.lesson_type]
    lines = [
        f"{mark} <b>{escape(title)}</b> · <i>неделя {lesson.week}, "
        f"{DOW_FULL[lesson.weekday - 1].lower()}</i>",
        "",
        f"<b>{escape(lesson.subject)}</b>",
        "",
        f"🕘 <b>{lesson.start_time} – {lesson.end_time}</b> "
        f"<code>пары {escape(lesson.slot_label)}</code>",
    ]
    if lesson.room:
        lines.append(f"📍 Аудитория: <b>{escape(lesson.room)}</b>")
    if lesson.teacher:
        lines.append(f"👤 {escape(lesson.teacher)}")
    lines.append("")
    lines.append("<b>ДАТЫ ЗАНЯТИЙ</b>")
    days = sorted(d.on_date for d in lesson.dates)
    if days:
        lines.append("<code>" + ", ".join(fmt_date(d) for d in days) + "</code>")
    else:
        lines.append("<i>нет данных</i>")
    if lesson.raw_note:
        lines.append(f"<i>в файле: {escape(lesson.raw_note)}</i>")
    lines.append(f"\n<i>{escape(group.name)}</i>")
    return "\n".join(lines)


def day_card(on_date: date, lessons: list[Lesson], semester_start: date) -> str:
    week = week_of(on_date, semester_start)
    head = (
        f"<b>{on_date.day} {MONTHS_GEN[on_date.month - 1]}</b> · "
        f"{DOW_FULL[on_date.weekday()].lower()} · <i>неделя {week}</i>"
    )
    if not lessons:
        return head + "\n\n<i>Пар нет — свободный день</i>"
    body = "\n\n".join(_lesson_line(lesson) for lesson in lessons)
    return f"{head}\n\n{body}"


def calendar_caption(
    group: Group, year: int, month: int, busy_in_month: int, busy_total: int
) -> str:
    return (
        f"<b>{escape(group.name)}</b> · календарь\n"
        f"<b>{MONTHS_NOM[month - 1]} {year}</b> — занято дней: <b>{busy_in_month}</b>\n"
        f"За семестр занято дней: <b>{busy_total}</b>\n\n"
        f"{LEGEND}\nОдин кружок — одна пара, цвет — тип занятия."
    )
