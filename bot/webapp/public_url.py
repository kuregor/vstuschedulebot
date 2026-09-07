"""Публичный адрес Mini App.

Адрес быстрого туннеля меняется при каждом переподключении, поэтому брать его
только из .env недостаточно: значение читается при импорте и устаревает молча.
Контейнер туннеля пишет актуальный адрес в файл на общем томе, а бот читает
его на каждое построение кнопки. Файла нет (запуск с хоста, без Docker) —
работает прежнее значение WEBAPP_URL из .env.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


def current() -> str:
    """Актуальный https-адрес приложения. Пустая строка — адреса нет."""
    if settings.webapp_url_file:
        try:
            published = Path(settings.webapp_url_file).read_text(encoding="utf-8").strip()
        except OSError:
            published = ""
        if published.startswith("https://"):
            return published
    return settings.webapp_url.strip()
