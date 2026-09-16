"""Рассылка уведомлений об изменениях в расписании.

Разницу считает импорт и складывает в schedule_changes; отсюда она уходит
тем, у кого эта группа выбрана. Отправка отделена от импорта намеренно:
файлы обновляются пачкой по 45 штук, а Telegram не любит очередь запросов
без передышки — и если бот перезапустится посреди рассылки, неотправленное
останется в таблице и уедет на следующем круге.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Group, Lesson, LessonNote, ScheduleChange, User
from ..db.session import SessionLocal
from ..utils.formatting import DOW_FULL
from ..webapp import public_url
from . import notes_service

log = logging.getLogger(__name__)

# За один проход берём столько изменений: больше — только если бот долго
# лежал, и тогда остаток уедет следующим кругом.
BATCH = 50
# Пауза между сообщениями: Telegram считает частоту отправки.
SEND_PAUSE_SECONDS = 0.05
# Старое изменение уже не новость: в приложении его давно видно, а сообщение
# «аудитория 302 → 415» про позапрошлую неделю — просто шум.
STALE_AFTER = timedelta(days=3)
# Напоминание, опоздавшее на полдня (бот лежал), уже не помогает: пара либо
# прошла, либо вот-вот начнётся, и человек всё равно смотрит расписание сам.
REMINDER_STALE_AFTER = timedelta(hours=12)


def _keyboard() -> InlineKeyboardMarkup | None:
    """Кнопка «открыть расписание» — если у бота сейчас есть публичный адрес."""
    url = public_url.current()
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Открыть расписание", web_app=WebAppInfo(url=url))]
        ]
    )


async def _recipients(session: AsyncSession, group_id: int) -> list[int]:
    """Кому писать: выбравшие эту группу и не отключившие уведомления."""
    rows = await session.execute(
        select(User.telegram_id).where(
            User.group_id == group_id, User.notify.is_(True)
        )
    )
    return [row[0] for row in rows]


def _is_stale(change: ScheduleChange, now: datetime) -> bool:
    created = change.created_at
    if created is None:
        return False
    if created.tzinfo is None:  # на случай базы без часового пояса
        created = created.replace(tzinfo=timezone.utc)
    return now - created > STALE_AFTER


async def send_pending(bot: Bot) -> int:
    """Отправляет неразосланные изменения -> сколько сообщений ушло.

    Строка отмечается разосланной после попытки, даже если кто-то из
    получателей заблокировал бота: иначе одна такая строка держала бы очередь
    и уведомление приходило бы снова и снова.
    """
    now = datetime.now(timezone.utc)
    sent = 0
    async with SessionLocal() as session:
        pending = list(
            await session.scalars(
                select(ScheduleChange)
                .where(ScheduleChange.notified.is_(False))
                .order_by(ScheduleChange.id)
                .limit(BATCH)
            )
        )
        if not pending:
            return 0

        keyboard = _keyboard()
        for change in pending:
            change.notified = True
            if _is_stale(change, now):
                log.info("Изменение %s устарело, не рассылаем", change.id)
                continue
            for chat_id in await _recipients(session, change.group_id):
                try:
                    await bot.send_message(
                        chat_id, change.summary, reply_markup=keyboard
                    )
                    sent += 1
                except TelegramAPIError as exc:
                    # заблокировали бота, удалили чат — остальных это не касается
                    log.warning("Уведомление для %s не ушло: %s", chat_id, exc)
                await asyncio.sleep(SEND_PAUSE_SECONDS)
        await session.commit()

    if sent:
        log.info("Разослано уведомлений об изменениях: %s", sent)
    return sent


def _reminder_text(group: Group, lesson: Lesson, note: LessonNote) -> str:
    """Напоминание о заметке: когда пара, что за пара и что записано."""
    when = f"{DOW_FULL[lesson.weekday - 1].lower()}, {note.on_date:%d.%m}"
    place = f"{when} · {lesson.start_time}"
    if lesson.room:
        place += f" · ауд. {html.escape(lesson.room)}"
    lines = [
        f"⏰ Напоминание — <b>{html.escape(group.name)}</b>",
        "",
        f"<b>{html.escape(lesson.subject)}</b>",
        place,
    ]
    if note.text:
        lines += ["", html.escape(note.text)]
    return "\n".join(lines)


async def send_reminders(bot: Bot) -> int:
    """Отправляет напоминания, которым подошло время -> сколько ушло.

    Отметка об отправке ставится в любом случае: если пары в расписании уже
    нет или человек заблокировал бота, напоминание не должно возвращаться на
    каждом круге.
    """
    now = datetime.now().astimezone()
    sent = 0
    async with SessionLocal() as session:
        pending = await notes_service.due(session, now)
        if not pending:
            return 0

        keyboard = _keyboard()
        for note in pending:
            note.remind_sent = True
            planned = note.remind_at
            if planned is not None and planned.tzinfo is None:
                planned = planned.replace(tzinfo=timezone.utc)
            if planned is not None and now - planned > REMINDER_STALE_AFTER:
                log.info("Напоминание %s опоздало, не шлём", note.id)
                continue

            place = await notes_service.lesson_of(session, note)
            if place is None:  # пару убрали из расписания — напоминать не о чем
                log.info("Напоминание %s: пары уже нет в расписании", note.id)
                continue

            group, lesson = place
            try:
                await bot.send_message(
                    note.telegram_id,
                    _reminder_text(group, lesson, note),
                    reply_markup=keyboard,
                )
                sent += 1
            except TelegramAPIError as exc:
                log.warning("Напоминание для %s не ушло: %s", note.telegram_id, exc)
            await asyncio.sleep(SEND_PAUSE_SECONDS)
        await session.commit()

    if sent:
        log.info("Отправлено напоминаний: %s", sent)
    return sent
