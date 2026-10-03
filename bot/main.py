"""Точка входа: запуск бота (long polling)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import settings
from .db.session import SessionLocal, init_db
from .handlers import schedule
from .services import button_service, notify_service, source_service
from .webapp import public_url
from .webapp.server import start_webapp

# Как часто сверяться с адресом, который публикует туннель. Это чтение файла,
# в Telegram уходит только смена адреса, поэтому опрос можно держать частым:
# после переподключения туннеля кнопки чинятся за несколько секунд.
POLL_PUBLIC_URL_SECONDS = 5
# Как часто заглядывать в очередь сообщений — изменений расписания и
# напоминаний. Появляются они редко, но ждать их в очереди незачем: проверка —
# два запроса по индексу.
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


async def _keep_buttons_alive(bot: Bot) -> None:
    """Держит кнопки приложения на актуальном адресе туннеля.

    Адрес быстрого туннеля меняется при каждом переподключении, а кнопки
    живут на стороне Telegram: пока их не переставить, они открывают мёртвую
    страницу. Кнопок два вида — меню чата и кнопки под сообщениями бота
    (приветствие, уведомления, напоминания), — и обе переводятся на новый
    адрес здесь же, см. button_service. Без этого после каждого перезапуска
    приходилось заново нажимать /start.

    Ставит кнопки сразу и дальше проверяет адрес по кругу. Одно чтение адреса
    на проход — иначе кнопка и запомненное значение расходятся: при старте
    контейнеров бот и туннель поднимаются одновременно, и между двумя
    чтениями адрес успевает смениться. Тогда кнопка осталась бы на старом
    адресе, а сторож считал бы, что всё в порядке.

    Меню и сообщения помнят свой адрес раздельно: сообщение, упёршееся в
    сеть, повторяется на следующем круге, и переставлять ради него кнопку
    меню во всех чатах незачем.
    """
    menu_url: str | None = None
    messages_url: str | None = None
    while True:
        url = public_url.current()
        if url != menu_url:
            try:
                await button_service.point_menu(bot, url)
            except Exception:  # сеть или лимиты Telegram — повторим на следующем круге
                log.exception("Не удалось обновить кнопку меню")
            else:
                log.info("Кнопка меню ведёт на %s", url or "список команд")
                menu_url = url
        if url and url != messages_url:
            try:
                if await button_service.point_messages(bot, url):
                    messages_url = url
            except Exception:  # база или сеть — повторим на следующем круге
                log.exception("Не удалось обновить кнопки под сообщениями")
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


async def _deliver_messages(bot: Bot) -> None:
    """Разносит уведомления о правках расписания и напоминания о заметках.

    Отдельной задачей, а не сразу после разбора файла: обновление идёт пачкой
    по всему каталогу сайта, и рассылка посреди него растянула бы загрузку.
    Напоминания живут здесь же — им нужен тот же круг и тот же бот, а точность
    в полминуты для «напомнить за день до пары» с запасом достаточна.
    """
    while True:
        try:
            await notify_service.send_pending(bot)
            await notify_service.send_reminders(bot)
        except Exception:  # сеть или лимиты Telegram — повторим на следующем круге
            log.exception("Рассылка сообщений не удалась")
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
    menu_keeper = asyncio.create_task(_keep_buttons_alive(bot))
    refresher = asyncio.create_task(_keep_schedules_fresh())
    notifier = asyncio.create_task(_deliver_messages(bot))

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
