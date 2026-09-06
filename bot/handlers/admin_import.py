"""Загрузка расписания: /import <ссылка на .xls> или просто пересланный файл."""
from __future__ import annotations

import logging
import os
import tempfile

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..config import settings
from ..db.session import SessionLocal
from ..services.import_service import ImportResult, import_from_file, import_from_url

router = Router()
log = logging.getLogger(__name__)

LEVEL_TITLE = {"master": "магистратура", "bachelor": "бакалавриат"}


def _is_admin(user_id: int) -> bool:
    # Пустой ADMIN_IDS = импорт доступен всем (удобно на этапе настройки).
    return not settings.admin_ids or user_id in settings.admin_ids


def _report(result: ImportResult, source: str) -> str:
    lines = [
        "✅ <b>Расписание загружено</b>",
        f"Источник: <code>{source}</code>",
        f"Уровень: <b>{LEVEL_TITLE[result.program_level.value]}</b>"
        + (f", {result.faculty}" if result.faculty else "")
        + (f", {result.course} курс" if result.course else ""),
        f"Групп: <b>{len(result.groups)}</b> — {', '.join(result.groups)}",
        f"Занятий: <b>{result.lessons_count}</b>, дат в календаре: <b>{result.dates_count}</b>",
    ]
    if result.warnings:
        lines.append(f"\n⚠️ Замечаний при разборе: {len(result.warnings)}")
        lines.extend(f"• {w}" for w in result.warnings[:5])
    lines.append("\nОткрыть: /schedule")
    return "\n".join(lines)


@router.message(Command("import"))
async def cmd_import(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return

    # Ссылку берём целиком (в адресах ВолгГТУ бывают пробелы и кириллица).
    payload = (message.text or "").split(maxsplit=1)
    url = payload[1].strip() if len(payload) > 1 else ""
    if not url.lower().startswith(("http://", "https://")):
        await message.answer(
            "Пришлите ссылку на файл расписания, например:\n"
            "<code>/import https://www.vstu.ru/upload/raspisanie/z/"
            "ОН_Магистратура_1 курс ФЭВТ.xls</code>\n\n"
            "Либо просто отправьте сам файл .xls в этот чат."
        )
        return

    status = await message.answer("⏳ Скачиваю и разбираю файл…")
    try:
        async with SessionLocal() as session:
            result = await import_from_url(session, url)
    except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
        log.exception("Импорт по ссылке не удался")
        await status.edit_text(f"❌ Не получилось: {exc}")
        return
    await status.edit_text(_report(result, url))


@router.message(F.document)
async def on_document(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    name = message.document.file_name or "schedule.xls"
    if not name.lower().endswith((".xls", ".xlsx")):
        await message.answer("Нужен файл расписания в формате .xls")
        return

    status = await message.answer("⏳ Разбираю файл…")
    fd, path = tempfile.mkstemp(suffix=".xls")
    os.close(fd)
    try:
        await message.bot.download(message.document, destination=path)
        async with SessionLocal() as session:
            result = await import_from_file(session, path, name)
    except Exception as exc:  # noqa: BLE001
        log.exception("Импорт файла не удался")
        await status.edit_text(f"❌ Не получилось: {exc}")
        return
    finally:
        if os.path.exists(path):
            os.unlink(path)
    await status.edit_text(_report(result, name))
