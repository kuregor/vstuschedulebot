"""Веб-сервер Mini App: статика + JSON API. Работает в одном процессе с ботом."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from ..config import settings
from ..db.models import ProgramLevel
from ..db.session import SessionLocal
from ..services import schedule_service as svc
from . import public_url
from .api import group_json, schedule_json
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


async def handle_groups(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with SessionLocal() as session:
        groups = await svc.list_groups(session)
        selected = None
        if user_id is not None:
            user = await svc.get_user(session, user_id)
            selected = user.group_id if user else None
    payload = {
        "selected": selected,
        "levels": [
            {
                "level": level.value,
                "title": "Бакалавриат" if level is ProgramLevel.bachelor else "Магистратура",
                "groups": [group_json(g) for g in groups if g.program_level is level],
            }
            for level in (ProgramLevel.bachelor, ProgramLevel.master)
        ],
    }
    return web.json_response(payload)


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
                groups = await svc.list_groups(session)
                group = groups[0] if groups else None

        if group is None:
            return web.json_response(
                {"empty": True, "message": "Расписание ещё не загружено"}
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


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(
        [
            web.get("/", handle_index),
            web.get("/api/health", handle_health),
            web.get("/api/groups", handle_groups),
            web.get("/api/schedule", handle_schedule),
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
