"""Точка входа: запуск бота (long polling)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)

from .config import settings
from .db.session import SessionLocal, init_db
from .handlers import schedule
from .services import notify_service
from .services import schedule_service as svc
from .services import source_service
from .webapp import public_url
from .webapp.server import start_webapp

# Как часто сверяться с адресом, который публикует туннель. Это чтение файла,
# в Telegram уходит только смена адреса, поэтому опрос можно держать частым:
# после переподключения туннеля кнопка чинится за несколько секунд.
POLL_PUBLIC_URL_SECONDS = 5
# Пауза между чатами: Telegram не любит очередь запросов без передышки
MENU_BUTTON_PAUSE_SECONDS = 0.05
# Как часто заглядывать в очередь уведомлений об изменениях расписания.
# Сами изменения появляются редко, но ждать их в очереди незачем: проверка —
# один запрос по индексу.
NOTIFY_POLL_SECONDS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть расписание"),
            BotCommand(command="app", description="Открыть приложение"),
        ]
    )


def _menu_button(url: str) -> MenuButtonWebApp | MenuButtonCommands:
    """Кнопка меню слева от поля ввода: открывает Mini App или список команд."""
    if url:
        return MenuButtonWebApp(text="Расписание", web_app=WebAppInfo(url=url))
    return MenuButtonCommands()


async def _apply_menu_button(bot: Bot, url: str) -> None:
    """Ставит кнопку меню всем: и по умолчанию, и каждому знакомому чату.

    У чата, где приложение уже открывали, есть собственная кнопка меню, и она
    главнее общей. Поэтому обновления одной только общей мало: пользователь
    продолжал бы нажимать свою, оставшуюся на адресе прошлого туннеля. Чаты
    берём из таблицы пользователей — тех, кто уже писал боту.
    """
    button = _menu_button(url)
    await bot.set_chat_menu_button(menu_button=button)

    async with SessionLocal() as session:
        chat_ids = await svc.all_user_ids(session)
    for chat_id in chat_ids:
        try:
            await bot.set_chat_menu_button(chat_id=chat_id, menu_button=button)
        except TelegramAPIError as exc:
            # чат удалён, бот заблокирован — остальных это не касается
            log.warning("Кнопка меню для %s не обновлена: %s", chat_id, exc)
        await asyncio.sleep(MENU_BUTTON_PAUSE_SECONDS)


async def _keep_menu_button(bot: Bot) -> None:
    """Держит кнопку меню на актуальном адресе туннеля.

    Адрес быстрого туннеля меняется при каждом переподключении, а кнопка меню
    живёт на стороне Telegram: пока её не переставить, она открывает мёртвую
    страницу. Кнопки внутри сообщений этим не страдают — они строятся заново
    на каждый /start.

    Ставит кнопку сразу и дальше проверяет адрес по кругу. Одно чтение адреса
    на проход — иначе кнопка и запомненное значение расходятся: при старте
    контейнеров бот и туннель поднимаются одновременно, и между двумя
    чтениями адрес успевает смениться. Тогда кнопка осталась бы на старом
    адресе, а сторож считал бы, что всё в порядке.
    """
    known: str | None = None
    while True:
        url = public_url.current()
        if url != known:
            try:
                await _apply_menu_button(bot, url)
            except Exception:  # сеть или лимиты Telegram — повторим на следующем круге
                log.exception("Не удалось обновить кнопку меню")
            else:
                log.info("Кнопка меню ведёт на %s", url or "список команд")
                known = url
        await asyncio.sleep(POLL_PUBLIC_URL_SECONDS)


async def _keep_schedules_fresh() -> None:
    """Держит в базе все расписания каталога.

    При старте докачивается всё, чего ещё нет, — после этого в настройках
    группы любого факультета видны сразу, без ожидания. Дальше раз в
    SCHEDULE_REFRESH_HOURS сверяемся с сайтом условными запросами: учебный
    отдел правит файлы по тем же адресам, и пока файл не менялся, проверка
    стоит один ответ 304 без тела. Новые группы из обновлённого файла
    добавляются в таблицу groups, старые не удаляются.
    """
    max_age = timedelta(hours=settings.refresh_hours)
    while True:
        try:
            async with SessionLocal() as session:
                await source_service.sync_catalog(session, force=True)
            await source_service.refresh_all(max_age)
        except Exception:  # сеть или сайт недоступны — попробуем на следующем круге
            log.exception("Обновление расписаний не удалось")
        await asyncio.sleep(settings.refresh_hours * 3600)


async def _deliver_changes(bot: Bot) -> None:
    """Разносит уведомления о правках расписания, которые нашёл импорт.

    Отдельной задачей, а не сразу после разбора файла: обновление идёт пачкой
    по всему каталогу сайта, и рассылка посреди него растянула бы загрузку.
    """
    while True:
        try:
            await notify_service.send_pending(bot)
        except Exception:  # сеть или лимиты Telegram — повторим на следующем круге
            log.exception("Рассылка уведомлений не удалась")
        await asyncio.sleep(NOTIFY_POLL_SECONDS)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN — заполните .env (см. .env.example)")

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(schedule.router)

    await _set_commands(bot)
    menu_keeper = asyncio.create_task(_keep_menu_button(bot))
    refresher = asyncio.create_task(_keep_schedules_fresh())
    notifier = asyncio.create_task(_deliver_changes(bot))

    # Веб-сервер Mini App живёт в том же процессе, что и бот.
    runner = await start_webapp()
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        menu_keeper.cancel()
        refresher.cancel()
        notifier.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        logging.info("Остановлено: %s", exc)
