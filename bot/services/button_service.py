"""Кнопки, открывающие приложение: кнопка меню и кнопки под сообщениями.

Обе хранят адрес приложения на стороне Telegram, а адрес быстрого туннеля
меняется при каждом перезапуске. Кнопку меню бот переставляет методом
setChatMenuButton, кнопку под сообщением — правкой самого сообщения. Для
второго он запоминает отправленные сообщения с кнопкой (таблица
`app_buttons`): историю чата ботам читать нельзя, и без этой записи найти
их было бы негде.

Живыми держим только последние KEEP_PER_CHAT сообщений каждого чата. У
вытесненных кнопка снимается сразу: при следующей смене адреса она всё
равно стала бы мёртвой, а сообщение без кнопки честнее кнопки, ведущей в
«no tunnel here». Войти в приложение можно и кнопкой меню, и из последних
сообщений — они всегда на живом адресе.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy import delete, select, update

from ..db.models import AppButton
from ..db.session import SessionLocal
from ..webapp import public_url
from . import schedule_service as svc

log = logging.getLogger(__name__)

# Сколько последних сообщений с кнопкой держать в каждом чате на живом адресе.
KEEP_PER_CHAT = 3
# Пауза между запросами при обходе чатов: Telegram не любит очередь запросов
# без передышки.
PAUSE_SECONDS = 0.05

# Что делать со строкой, когда кнопку в сообщении не удалось поправить.
DONE = "done"    # кнопка уже ведёт куда надо
GONE = "gone"    # сообщения больше нет или бот заблокирован — забыть строку
RETRY = "retry"  # сеть или сервер Telegram — попробовать на следующем круге

# Отправка нового сообщения вытесняет старые, смена адреса правит их все.
# Вперемешку эти два дела вернули бы кнопку в сообщение, у которого её
# только что сняли, — и о нём бот уже не помнил бы.
_lock = asyncio.Lock()


def keyboard(url: str) -> InlineKeyboardMarkup | None:
    """Кнопка «открыть расписание» под сообщением; адреса нет — кнопки нет."""
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Открыть расписание", web_app=WebAppInfo(url=url))]
        ]
    )


def menu_button(url: str) -> MenuButtonWebApp | MenuButtonCommands:
    """Кнопка меню слева от поля ввода: открывает Mini App или список команд."""
    if url:
        return MenuButtonWebApp(text="Расписание", web_app=WebAppInfo(url=url))
    return MenuButtonCommands()


def surplus(message_ids: list[int], keep: int = KEEP_PER_CHAT) -> list[int]:
    """Сообщения чата сверх последних `keep` — у них кнопку пора снять.

    Номера сообщений в чате только растут, поэтому последние — самые большие.
    """
    return sorted(message_ids, reverse=True)[keep:]


def verdict(exc: TelegramAPIError) -> str:
    """Что значит отказ Telegram поправить кнопку в сообщении."""
    if isinstance(exc, TelegramBadRequest) and "not modified" in exc.message:
        # кнопка уже такая — например, бот упал посреди прошлого обхода
        return DONE
    if isinstance(exc, (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound)):
        # сообщение удалено, чат пропал, бот заблокирован: повтор не поможет
        return GONE
    return RETRY


async def point_menu(bot: Bot, url: str) -> None:
    """Ставит кнопку меню всем: и по умолчанию, и каждому знакомому чату.

    У чата, где приложение уже открывали, есть собственная кнопка меню, и она
    главнее общей. Поэтому обновления одной только общей мало: пользователь
    продолжал бы нажимать свою, оставшуюся на адресе прошлого туннеля. Чаты
    берём из таблицы пользователей — тех, кто уже писал боту.
    """
    button = menu_button(url)
    await bot.set_chat_menu_button(menu_button=button)

    async with SessionLocal() as session:
        chat_ids = await svc.all_user_ids(session)
    for chat_id in chat_ids:
        try:
            await bot.set_chat_menu_button(chat_id=chat_id, menu_button=button)
        except TelegramAPIError as exc:
            # чат удалён, бот заблокирован — остальных это не касается
            log.warning("Кнопка меню для %s не обновлена: %s", chat_id, exc)
        await asyncio.sleep(PAUSE_SECONDS)


async def send(bot: Bot, chat_id: int, text: str) -> Message:
    """Отправляет сообщение с кнопкой приложения и запоминает его.

    Адреса нет — сообщение уходит без кнопки, и помнить о нём незачем.
    Запись не удалась — сообщение всё равно ушло, и ронять из-за этого
    рассылку нельзя: упавший проход повторил бы её, и люди получили бы
    уведомление дважды.
    """
    url = public_url.current()
    message = await bot.send_message(chat_id, text, reply_markup=keyboard(url))
    if url:
        try:
            await _remember(bot, chat_id, message.message_id, url)
        except Exception:
            log.exception("Сообщение %s в чате %s не запомнено", message.message_id, chat_id)
    return message


async def _remember(bot: Bot, chat_id: int, message_id: int, url: str) -> None:
    """Записывает сообщение с кнопкой и снимает кнопку у вытесненных.

    Строка вытесненного сообщения забывается, только когда кнопки у него
    точно больше нет. Не вышло из-за сети — строка остаётся: сообщение
    снова окажется лишним при следующей отправке, а до тех пор его кнопку
    переводят на новый адрес вместе с остальными.
    """
    async with _lock:
        async with SessionLocal() as session:
            session.add(AppButton(chat_id=chat_id, message_id=message_id, url=url))
            await session.flush()
            known = list(
                await session.scalars(
                    select(AppButton.message_id).where(AppButton.chat_id == chat_id)
                )
            )
            await session.commit()

        dropped = []
        for old in surplus(known):
            try:
                # без reply_markup Telegram убирает клавиатуру у сообщения
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=old)
            except TelegramAPIError as exc:
                # «not modified» — кнопки уже нет; удалено или бот заблокирован —
                # снимать нечего. Сеть — попробуем при следующей отправке.
                if verdict(exc) == RETRY:
                    log.info("Кнопка у сообщения %s в чате %s не снята: %s", old, chat_id, exc)
                    continue
            dropped.append(old)
            await asyncio.sleep(PAUSE_SECONDS)

        if dropped:
            async with SessionLocal() as session:
                await session.execute(
                    delete(AppButton).where(
                        AppButton.chat_id == chat_id, AppButton.message_id.in_(dropped)
                    )
                )
                await session.commit()


async def _point_message(bot: Bot, chat_id: int, message_id: int, url: str) -> str:
    """Переводит кнопку одного сообщения на адрес -> DONE, GONE или RETRY."""
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=keyboard(url)
        )
    except TelegramRetryAfter as exc:
        # Telegram просит подождать — ждём и пробуем ещё раз, один
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=keyboard(url)
            )
        except TelegramAPIError as again:
            return verdict(again)
    except TelegramAPIError as exc:
        return verdict(exc)
    return DONE


async def point_messages(bot: Bot, url: str) -> bool:
    """Переводит кнопки под запомненными сообщениями на новый адрес.

    Правит только те, что ещё ведут на прежний: после перезапуска бота на
    том же адресе обход не стоит ни одного запроса. Возвращает True, когда
    повторять нечего; False — часть сообщений упёрлась в сеть или сервер
    Telegram, и их стоит попробовать на следующем круге.
    """
    if not url:
        return True
    async with _lock:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(AppButton.chat_id, AppButton.message_id).where(
                        AppButton.url != url
                    )
                )
            ).all()
            if not rows:
                return True

            moved = forgotten = 0
            complete = True
            for chat_id, message_id in rows:
                result = await _point_message(bot, chat_id, message_id, url)
                where = (AppButton.chat_id == chat_id, AppButton.message_id == message_id)
                if result == DONE:
                    await session.execute(update(AppButton).where(*where).values(url=url))
                    moved += 1
                elif result == GONE:
                    await session.execute(delete(AppButton).where(*where))
                    forgotten += 1
                else:
                    complete = False
                await asyncio.sleep(PAUSE_SECONDS)
            await session.commit()

    log.info(
        "Кнопки под сообщениями ведут на %s: переведено %s, забыто %s%s",
        url,
        moved,
        forgotten,
        "" if complete else ", остальные — на следующем круге",
    )
    return complete
