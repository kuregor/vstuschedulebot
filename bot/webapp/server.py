"""Веб-сервер Mini App: статика + JSON API. Работает в одном процессе с ботом."""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path

from aiohttp import web

from ..config import settings
from ..db.session import SessionLocal
from ..services import schedule_service as svc
from ..services import source_service as sources_svc
from . import public_url
from .api import schedule_etag, schedule_json, settings_json, source_json
from .auth import InitDataError, user_id_from_init_data

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parents[2] / "webapp"
# Файлы, от которых зависит версия приложения в адресах статики.
VERSIONED_ASSETS = ("app.js", "app.css", "telegram-web-app.js")
# Год: адрес со старой версией никто больше не запросит, index на него не ссылается.
IMMUTABLE_MAX_AGE = 365 * 24 * 3600


def _init_data(request: web.Request) -> str:
    return request.headers.get("X-Telegram-Init-Data", "") or request.query.get(
        "init_data", ""
    )


def _user_id(request: web.Request) -> int | None:
    """Telegram id из подписанных данных запуска; None — гостевой режим."""
    raw = _init_data(request)
    if not raw:
        if settings.webapp_allow_insecure:
            return None
        raise web.HTTPUnauthorized(text="Откройте приложение через Telegram")
    try:
        return user_id_from_init_data(raw, settings.bot_token)
    except InitDataError as exc:
        if settings.webapp_allow_insecure:
            log.warning("initData не прошла проверку (отладочный режим): %s", exc)
            return None
        raise web.HTTPUnauthorized(text=f"Проверка Telegram не пройдена: {exc}") from exc


async def handle_settings(request: web.Request) -> web.Response:
    """Экран настроек: каталог сайта, состояние загрузок и текущий выбор.

    Каталог не сверяется с сайтом на каждый запрос — этим занимается фоновая
    задача. Только на пустой базе (первый запуск) читаем его сразу, иначе
    первый открывший настройки увидел бы пустой экран.
    """
    user_id = _user_id(request)
    async with SessionLocal() as session:
        sources = await sources_svc.list_sources(session)
        if not sources:
            sources = await sources_svc.sync_catalog(session)
        selected = None
        notify = True
        if user_id is not None:
            user = await svc.get_user(session, user_id)
            selected = user.group if user and user.group_id else None
            notify = user.notify if user is not None else True
        groups = (
            await sources_svc.groups_of_source(session, selected.source_id)
            if selected is not None and selected.source_id
            else []
        )
        return web.json_response(settings_json(sources, groups, selected, notify))


async def handle_pick_source(request: web.Request) -> web.Response:
    """Выбор файла расписания в настройках.

    Обычно файл уже загружен фоновой задачей, и ответ — просто список его
    групп. Если нет (первые минуты после запуска на пустой базе), качаем сейчас.
    """
    _user_id(request)  # проверка подписи Telegram; сам выбор общий для всех
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise web.HTTPBadRequest(text="Не указан файл расписания")

    async with SessionLocal() as session:
        source = await sources_svc.set_enabled(session, url, True)
        if source is None:
            raise web.HTTPNotFound(text="Такого расписания нет в каталоге сайта")
        groups = await sources_svc.groups_of_source(session, source.id)
        return web.json_response(
            {
                "source": source_json(source),
                "groups": [{"id": g.id, "name": g.name} for g in groups],
            }
        )


async def _remember_group(
    session, user_id: int | None, requested: str | None, group_id: int
) -> None:
    """Запоминает выбор группы, чтобы бот и приложение показывали одно и то же.

    Записываем только при настоящей смене: раньше строка пользователя
    переписывалась на каждое открытие приложения, а это лишний UPDATE на
    каждый запрос — в том числе на тот, что заканчивается ответом 304.
    """
    if user_id is None or not requested:
        return
    user = await svc.get_user(session, user_id)
    if user is not None and user.group_id == group_id:
        return
    await svc.set_user_group(session, user_id, group_id)


async def handle_schedule(request: web.Request) -> web.Response:
    """Расписание группы; при неизменившемся файле — 304 без тела.

    Пары и их даты — самая тяжёлая часть запроса (сотни строк на группу), а
    меняются они только когда учебный отдел перевыложил файл на сайте.
    Поэтому сначала считаем версию ответа по лёгким полям и сверяем её с
    присланной: совпала — отвечаем 304, и ни lessons, ни lesson_dates из базы
    не читаются вовсе. Приложение в этот момент уже показывает расписание из
    своего кэша, так что для человека ответ мгновенный.
    """
    user_id = _user_id(request)
    group_id = request.query.get("group_id")

    async with SessionLocal() as session:
        if group_id:
            group = await svc.get_group(session, int(group_id))
        else:
            user = await svc.get_user(session, user_id) if user_id is not None else None
            group = user.group if user and user.group_id else None

        if group is None:
            # Группа выбирается в настройках — туда и отправляем.
            return web.json_response(
                {
                    "empty": True,
                    "message": "Откройте «Настройки» и выберите факультет, курс и группу — "
                    "расписание загрузится с сайта ВолгГТУ.",
                }
            )

        today = date.today()
        stamp = await svc.source_stamp(session, group.source_id)
        etag = schedule_etag(group, stamp, today)
        if request.headers.get("If-None-Match") == etag:
            await _remember_group(session, user_id, group_id, group.id)
            return web.Response(status=304, headers={"ETag": etag})

        lessons = await svc.lessons_of_group(session, group.id)
        payload = schedule_json(group, lessons, today, version=etag)
        await _remember_group(session, user_id, group_id, group.id)

    return web.json_response(payload, headers={"ETag": etag})


async def handle_select_group(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    body = await request.json()
    group_id = int(body.get("group_id", 0))
    if user_id is None:
        return web.json_response({"ok": True, "saved": False})
    async with SessionLocal() as session:
        await svc.set_user_group(session, user_id, group_id)
    return web.json_response({"ok": True, "saved": True})


async def handle_notify(request: web.Request) -> web.Response:
    """Переключатель «писать ли об изменениях расписания».

    В гостевом режиме (отладка без подписи Telegram) сохранять некому —
    отвечаем честно, что не сохранили.
    """
    user_id = _user_id(request)
    body = await request.json()
    on = bool(body.get("on", True))
    if user_id is None:
        return web.json_response({"ok": True, "saved": False, "on": on})
    async with SessionLocal() as session:
        await svc.set_user_notify(session, user_id, on)
    return web.json_response({"ok": True, "saved": True, "on": on})


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def asset_version() -> str:
    """Версия статики — от самих файлов приложения.

    Встроенный браузер Telegram (особенно на iPhone) берёт app.js и app.css из
    кэша, не спрашивая сервер, даже при Cache-Control: no-cache. Единственное,
    что он перечитывает, — сама страница. Поэтому адреса статики несут версию:
    изменился файл — изменился адрес, и старая копия в кэше просто не нужна.
    """
    digest = hashlib.sha1()
    for name in VERSIONED_ASSETS:
        try:
            stat = (STATIC_DIR / name).stat()
        except FileNotFoundError:
            continue
        digest.update(f"{name}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    return digest.hexdigest()[:10]


async def handle_index(_: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html.replace("{{v}}", asset_version()),
        content_type="text/html",
        charset="utf-8",
    )


@web.middleware
async def delivery_middleware(request: web.Request, handler):
    """Кэш и сжатие.

    * Страница и API — no-cache: перепроверяются каждый раз, а 304 дешёвый.
    * Статика с версией в адресе — immutable на год: повторное открытие не
      качает ни мост Telegram, ни скрипты приложения. Для узкого канала это
      главное: 116 КБ моста через такой прокси приходят кусками и не всегда.
    * JSON и страница сжимаются на лету. Статика сжата заранее: рядом с файлом
      лежит его .gz, и aiohttp отдаёт его сам, если клиент понимает gzip.
    """
    response = await handler(request)
    versioned_static = request.path.startswith("/static/") and "v" in request.query
    if versioned_static:
        response.headers["Cache-Control"] = f"public, max-age={IMMUTABLE_MAX_AGE}, immutable"
    else:
        response.headers.setdefault("Cache-Control", "no-cache")
    if isinstance(response, web.Response) and response.content_type in (
        "application/json",
        "text/html",
    ):
        response.enable_compression()
    return response


def create_app() -> web.Application:
    app = web.Application(middlewares=[delivery_middleware])
    app.add_routes(
        [
            web.get("/", handle_index),
            web.get("/api/health", handle_health),
            web.get("/api/schedule", handle_schedule),
            web.get("/api/settings", handle_settings),
            web.post("/api/source", handle_pick_source),
            web.post("/api/group", handle_select_group),
            web.post("/api/notify", handle_notify),
            web.static("/static", STATIC_DIR),
        ]
    )
    return app


async def start_webapp() -> web.AppRunner:
    runner = web.AppRunner(create_app())
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    log.info(
        "Mini App слушает http://%s:%s (публичный адрес: %s), версия статики %s",
        settings.webapp_host,
        settings.webapp_port,
        public_url.current() or "не задан — кнопка в боте не появится",
        asset_version(),
    )
    return runner
