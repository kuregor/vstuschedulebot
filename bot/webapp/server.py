"""Веб-сервер Mini App: статика + JSON API. Работает в одном процессе с ботом."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from ..config import settings
from ..db.session import SessionLocal
from ..services import schedule_service as svc
from ..services import source_service as sources_svc
from . import public_url
from .api import schedule_json, settings_json, source_json
from .auth import InitDataError, user_id_from_init_data

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parents[2] / "webapp"


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
    """Экран настроек: каталог сайта, состояние загрузок и текущий выбор."""
    user_id = _user_id(request)
    async with SessionLocal() as session:
        sources = await sources_svc.sync_catalog(session)
        selected = None
        if user_id is not None:
            user = await svc.get_user(session, user_id)
            selected = user.group if user and user.group_id else None
        groups = (
            await sources_svc.groups_of_source(session, selected.source_id)
            if selected is not None and selected.source_id
            else []
        )
        return web.json_response(settings_json(sources, groups, selected))


async def handle_pick_source(request: web.Request) -> web.Response:
    """Выбор файла расписания в настройках: включаем и сразу загружаем."""
    _user_id(request)  # проверка подписи Telegram; сам выбор общий для всех
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise web.HTTPBadRequest(text="Не указан файл расписания")

    async with SessionLocal() as session:
        source = await sources_svc.get_source(session, url)
        if source is None:
            raise web.HTTPNotFound(text="Такого расписания нет в каталоге сайта")
        source = await sources_svc.set_enabled(session, url, True)
        if source is None:
            raise web.HTTPNotFound(text="Такого расписания нет в каталоге сайта")
        if bool(body.get("reload")) and source.status == sources_svc.STATUS_OK:
            source = await sources_svc.load_source(session, source)
        groups = await sources_svc.groups_of_source(session, source.id)
        return web.json_response(
            {
                "source": source_json(source),
                "groups": [{"id": g.id, "name": g.name} for g in groups],
            }
        )


async def handle_schedule(request: web.Request) -> web.Response:
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

        lessons = await svc.lessons_of_group(session, group.id)
        payload = schedule_json(group, lessons)

        # Выбор группы запоминаем, чтобы бот и приложение показывали одно и то же.
        if user_id is not None and group_id:
            await svc.set_user_group(session, user_id, group.id)

    return web.json_response(payload)


async def handle_select_group(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    body = await request.json()
    group_id = int(body.get("group_id", 0))
    if user_id is None:
        return web.json_response({"ok": True, "saved": False})
    async with SessionLocal() as session:
        await svc.set_user_group(session, user_id, group_id)
    return web.json_response({"ok": True, "saved": True})


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _no_cache(_: web.Request, response: web.StreamResponse) -> None:
    """Статика и index без кэша: после пересборки образа встроенный браузер
    Telegram иначе показывает прошлую версию приложения, пока не истечёт
    эвристический срок. no-cache — это «перепроверь», ответ 304 дешёвый."""
    response.headers.setdefault("Cache-Control", "no-cache")


def create_app() -> web.Application:
    app = web.Application()
    app.on_response_prepare.append(_no_cache)
    app.add_routes(
        [
            web.get("/", handle_index),
            web.get("/api/health", handle_health),
            web.get("/api/schedule", handle_schedule),
            web.get("/api/settings", handle_settings),
            web.post("/api/source", handle_pick_source),
            web.post("/api/group", handle_select_group),
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
        "Mini App слушает http://%s:%s (публичный адрес: %s)",
        settings.webapp_host,
        settings.webapp_port,
        public_url.current() or "не задан — кнопка в боте не появится",
    )
    return runner
