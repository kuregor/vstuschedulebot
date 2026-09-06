"""Основные экраны: список групп, расписание, календарь, карточки пары и дня."""
from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from ..config import settings
from ..db.models import Lesson, ProgramLevel
from ..db.session import SessionLocal
from ..keyboards.navigation import (
    TAB_LIST,
    back_kb,
    calendar_kb,
    groups_kb,
    schedule_kb,
)
from ..services import schedule_service as svc
from ..utils.formatting import (
    calendar_caption,
    day_card,
    lesson_card,
    schedule_screen,
)

router = Router()

# Фильтр недель на пользователя (сбрасывается при перезапуске бота).
_week_filter: dict[int, str] = {}


def _semester_start() -> date:
    return date.fromisoformat(settings.semester_start)


async def _render_schedule(user_id: int, group_id: int) -> tuple[str, object]:
    async with SessionLocal() as session:
        group = await svc.get_group(session, group_id)
        lessons = await svc.lessons_of_group(session, group_id)
    week_filter = _week_filter.get(user_id, "both")
    text = schedule_screen(group, lessons, week_filter, _semester_start())
    return text, schedule_kb(group, lessons, week_filter, TAB_LIST)


async def _render_calendar(group_id: int, year: int, month: int) -> tuple[str, object]:
    async with SessionLocal() as session:
        group = await svc.get_group(session, group_id)
        first_day = date(year, month, 1)
        last_day = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        busy = await svc.busy_days(session, group_id, first_day, last_day)
        total = await svc.busy_days_count(session, group_id)
        bounds = await svc.group_date_bounds(session, group_id)
    caption = calendar_caption(group, year, month, len(busy), total)
    return caption, calendar_kb(year, month, busy, date.today(), bounds)


async def _group_picker(level: ProgramLevel | None = None) -> tuple[str, object]:
    async with SessionLocal() as session:
        groups = await svc.list_groups(session, level)
    if not groups:
        return (
            "Расписания ещё не загружены.\n"
            "Администратор может добавить их командой <code>/import ссылка-на-.xls</code>.",
            None,
        )
    title = "Выберите группу:"
    if level is not None:
        title = f"Выберите группу ({'магистратура' if level is ProgramLevel.master else 'бакалавриат'}):"
    return title, groups_kb(groups, level)


@router.message(CommandStart())
@router.message(Command("group"))
async def cmd_start(message: Message) -> None:
    async with SessionLocal() as session:
        user = await svc.get_user(session, message.from_user.id)
    if user is not None and user.group_id:
        text, kb = await _render_schedule(message.from_user.id, user.group_id)
        await message.answer(text, reply_markup=kb)
        return
    text, kb = await _group_picker()
    await message.answer(text, reply_markup=kb)


@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    async with SessionLocal() as session:
        user = await svc.get_user(session, message.from_user.id)
    if user is None or not user.group_id:
        text, kb = await _group_picker()
        await message.answer(text, reply_markup=kb)
        return
    text, kb = await _render_schedule(message.from_user.id, user.group_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("lvl:"))
async def cb_level(query: CallbackQuery) -> None:
    level = ProgramLevel(query.data.split(":", 1)[1])
    text, kb = await _group_picker(level)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "grp:pick")
async def cb_group_pick(query: CallbackQuery) -> None:
    text, kb = await _group_picker()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.regexp(r"^grp:\d+$"))
async def cb_group_set(query: CallbackQuery) -> None:
    group_id = int(query.data.split(":", 1)[1])
    async with SessionLocal() as session:
        await svc.set_user_group(session, query.from_user.id, group_id)
    text, kb = await _render_schedule(query.from_user.id, group_id)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


async def _current_group_id(user_id: int) -> int | None:
    async with SessionLocal() as session:
        user = await svc.get_user(session, user_id)
    return user.group_id if user else None


@router.callback_query(F.data.startswith("wk:"))
async def cb_week(query: CallbackQuery) -> None:
    _week_filter[query.from_user.id] = query.data.split(":", 1)[1]
    group_id = await _current_group_id(query.from_user.id)
    if group_id is None:
        await query.answer("Сначала выберите группу")
        return
    text, kb = await _render_schedule(query.from_user.id, group_id)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "tab:list")
async def cb_tab_list(query: CallbackQuery) -> None:
    group_id = await _current_group_id(query.from_user.id)
    if group_id is None:
        await query.answer("Сначала выберите группу")
        return
    text, kb = await _render_schedule(query.from_user.id, group_id)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data == "tab:cal")
async def cb_tab_cal(query: CallbackQuery) -> None:
    group_id = await _current_group_id(query.from_user.id)
    if group_id is None:
        await query.answer("Сначала выберите группу")
        return
    async with SessionLocal() as session:
        first, _last = await svc.group_date_bounds(session, group_id)
    start = first or _semester_start()
    today = date.today()
    year, month = (today.year, today.month) if first and first <= today else (start.year, start.month)
    text, kb = await _render_calendar(group_id, year, month)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("cal:"))
async def cb_calendar_nav(query: CallbackQuery) -> None:
    group_id = await _current_group_id(query.from_user.id)
    if group_id is None:
        await query.answer("Сначала выберите группу")
        return
    year_s, month_s = query.data.split(":", 1)[1].split("-")
    text, kb = await _render_calendar(group_id, int(year_s), int(month_s))
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("les:"))
async def cb_lesson(query: CallbackQuery) -> None:
    lesson_id = int(query.data.split(":", 1)[1])
    async with SessionLocal() as session:
        lesson = await session.get(Lesson, lesson_id)
        if lesson is None:
            await query.answer("Пара не найдена")
            return
        group = await svc.get_group(session, lesson.group_id)
        text = lesson_card(lesson, group)
    await query.message.edit_text(text, reply_markup=back_kb("tab:list"))
    await query.answer()


@router.callback_query(F.data.startswith("day:"))
async def cb_day(query: CallbackQuery) -> None:
    group_id = await _current_group_id(query.from_user.id)
    if group_id is None:
        await query.answer("Сначала выберите группу")
        return
    on_date = date.fromisoformat(query.data.split(":", 1)[1])
    async with SessionLocal() as session:
        lessons = await svc.lessons_on_date(session, group_id, on_date)
    text = day_card(on_date, lessons, _semester_start())
    await query.message.edit_text(text, reply_markup=back_kb("tab:cal"))
    await query.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()
