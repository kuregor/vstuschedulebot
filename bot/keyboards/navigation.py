"""Inline-клавиатуры: вкладки, фильтр недель, список пар, календарь, выбор группы."""
from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db.models import Group, Lesson, LessonType, ProgramLevel
from ..utils.formatting import DOW_SHORT, TYPE_MARK

TAB_LIST = "list"
TAB_CAL = "cal"


def schedule_kb(
    group: Group, lessons: list[Lesson], week_filter: str, tab: str = TAB_LIST
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.row(InlineKeyboardButton(text=f"▾ {group.name}", callback_data="grp:pick"))

    seg = [("both", "Обе недели"), ("1", "1-я"), ("2", "2-я")]
    kb.row(
        *[
            InlineKeyboardButton(
                text=("• " + label if week_filter == key else label),
                callback_data=f"wk:{key}",
            )
            for key, label in seg
        ]
    )

    shown = [
        lesson
        for lesson in lessons
        if week_filter == "both" or lesson.week == int(week_filter)
    ]
    shown.sort(key=lambda x: (x.week, x.weekday, x.slot_from))
    for lesson in shown[:40]:
        mark = TYPE_MARK[lesson.lesson_type]
        title = lesson.subject if len(lesson.subject) <= 28 else lesson.subject[:27] + "…"
        kb.row(
            InlineKeyboardButton(
                text=f"{mark} {DOW_SHORT[lesson.weekday - 1]} {lesson.start_time} · {title}",
                callback_data=f"les:{lesson.id}",
            )
        )

    kb.row(
        InlineKeyboardButton(
            text=("• Расписание" if tab == TAB_LIST else "Расписание"),
            callback_data="tab:list",
        ),
        InlineKeyboardButton(
            text=("• Календарь" if tab == TAB_CAL else "Календарь"),
            callback_data="tab:cal",
        ),
    )
    return kb.as_markup()


def _day_dots(types: list[str], limit: int = 3) -> str:
    order = {"lek": 0, "sem": 1, "lab": 2, "other": 3}
    ordered = sorted(types, key=lambda t: order.get(t, 9))
    dots = "".join(TYPE_MARK[LessonType(t)] for t in ordered[:limit])
    return dots + ("+" if len(ordered) > limit else "")


def calendar_kb(
    year: int,
    month: int,
    busy: dict[date, list[str]],
    today: date,
    bounds: tuple[date | None, date | None] = (None, None),
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(*[InlineKeyboardButton(text=d, callback_data="noop") for d in DOW_SHORT])

    weeks = _calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in weeks:
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
                continue
            current = date(year, month, day)
            types = busy.get(current, [])
            label = str(day)
            if current == today:
                label = f"[{day}]"
            if types:
                label = f"{label}{_day_dots(types)}"
            row.append(
                InlineKeyboardButton(text=label, callback_data=f"day:{current.isoformat()}")
            )
        kb.row(*row)

    first, last = bounds
    prev_month = date(year, month, 1) - timedelta(days=1)
    next_month = date(year, month, _calendar.monthrange(year, month)[1]) + timedelta(days=1)
    nav: list[InlineKeyboardButton] = []
    if first is None or prev_month >= date(first.year, first.month, 1):
        nav.append(
            InlineKeyboardButton(
                text="‹ пред.",
                callback_data=f"cal:{prev_month.year}-{prev_month.month}",
            )
        )
    if last is None or next_month <= last:
        nav.append(
            InlineKeyboardButton(
                text="след. ›",
                callback_data=f"cal:{next_month.year}-{next_month.month}",
            )
        )
    if nav:
        kb.row(*nav)

    kb.row(
        InlineKeyboardButton(text="Расписание", callback_data="tab:list"),
        InlineKeyboardButton(text="• Календарь", callback_data="tab:cal"),
    )
    return kb.as_markup()


def groups_kb(groups: list[Group], level: ProgramLevel | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=("• Бакалавриат" if level is ProgramLevel.bachelor else "Бакалавриат"),
            callback_data="lvl:bachelor",
        ),
        InlineKeyboardButton(
            text=("• Магистратура" if level is ProgramLevel.master else "Магистратура"),
            callback_data="lvl:master",
        ),
    )
    row: list[InlineKeyboardButton] = []
    for group in groups:
        row.append(InlineKeyboardButton(text=group.name, callback_data=f"grp:{group.id}"))
        if len(row) == 3:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    return kb.as_markup()


def back_kb(target: str = "tab:list") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data=target)]]
    )
