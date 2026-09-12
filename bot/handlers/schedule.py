"""Единственный экран бота: кнопка, открывающая Mini App.

Расписание живёт в приложении — там и список пар, и календарь, и настройки.
Бот только даёт войти и следит, чтобы кнопка вела на живой адрес.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from ..webapp import public_url

router = Router()
log = logging.getLogger(__name__)

WELCOME = (
    "<b>Расписание ВолгГТУ</b>\n\n"
    "Нажмите кнопку ниже — расписание откроется приложением прямо в Telegram: "
    "две недели, календарь занятых дней, карточки пар.\n\n"
    "Факультет, курс и группа выбираются во вкладке «Настройки». "
    "Файл расписания бот берёт с сайта университета и обновляет сам."
)

NO_URL = (
    "<b>Расписание ВолгГТУ</b>\n\n"
    "Приложение сейчас недоступно: у бота нет публичного адреса. "
    "Если вы его запускали — проверьте, поднялся ли туннель "
    "(<code>WEBAPP_URL</code> или контейнер туннеля)."
)


def _webapp_kb() -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая Mini App внутри Telegram.

    Адрес берётся на каждое построение кнопки: у быстрого туннеля он меняется
    при переподключении, и закэшированный давал бы мёртвую страницу.
    """
    url = public_url.current()
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Открыть расписание",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


async def _refresh_menu_button(message: Message, url: str) -> None:
    """Переставляет кнопку меню персонально для этого чата.

    Общую кнопку меню клиент Telegram кэширует локально и после смены адреса
    туннеля продолжает открывать мёртвый поддомен. Кнопка, выставленная на
    конкретный чат, приходит клиенту сразу и перебивает закэшированную.
    """
    try:
        if url:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(
                    text="Расписание", web_app=WebAppInfo(url=url)
                ),
            )
        else:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id, menu_button=MenuButtonCommands()
            )
    except Exception:  # кнопка меню — украшение, из-за неё экран ронять незачем
        log.exception("Не удалось обновить кнопку меню чата %s", message.chat.id)


@router.message(CommandStart())
@router.message(Command("app"))
async def cmd_start(message: Message) -> None:
    url = public_url.current()
    await _refresh_menu_button(message, url)
    kb = _webapp_kb()
    if kb is None:
        await message.answer(NO_URL)
        return
    await message.answer(WELCOME, reply_markup=kb)
