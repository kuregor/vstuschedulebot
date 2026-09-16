"""Рассылка уведомлений об изменениях в расписании.

Разницу считает импорт и складывает в schedule_changes; отсюда она уходит
тем, у кого эта группа выбрана. Отправка отделена от импорта намеренно:
файлы обновляются пачкой по 45 штук, а Telegram не любит очередь запросов
без передышки — и если бот перезапустится посреди рассылки, неотправленное
останется в таблице и уедет на следующем круге.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ScheduleChange, User
from ..db.session import SessionLocal
from ..webapp import public_url

log = logging.getLogger(__name__)

# За один проход берём столько изменений: больше — только если бот долго
# лежал, и тогда остаток уедет следующим кругом.
BATCH = 50
# Пауза между сообщениями: Telegram считает частоту отправки.
SEND_PAUSE_SECONDS = 0.05
# Старое изменение уже не новость: в приложении его давно видно, а сообщение
# «аудитория 302 → 415» про позапрошлую неделю — просто шум.
STALE_AFTER = timedelta(days=3)


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
