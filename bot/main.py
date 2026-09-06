"""Точка входа: запуск бота (long polling)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)

from .config import settings
from .db.session import init_db
from .handlers import admin_import, schedule
from .webapp.server import start_webapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть расписание"),
            BotCommand(command="app", description="Открыть приложение"),
            BotCommand(command="schedule", description="Расписание текстом"),
            BotCommand(command="group", description="Сменить группу"),
            BotCommand(command="import", description="Загрузить расписание по ссылке"),
        ]
    )
    # Кнопка меню слева от поля ввода открывает Mini App.
    if settings.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Расписание", web_app=WebAppInfo(url=settings.webapp_url)
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN — заполните .env (см. .env.example)")

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin_import.router)
    dp.include_router(schedule.router)

    await _set_commands(bot)

    # Веб-сервер Mini App живёт в том же процессе, что и бот.
    runner = await start_webapp()
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        logging.info("Остановлено: %s", exc)
