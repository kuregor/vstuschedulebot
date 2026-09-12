"""Публичный адрес Mini App.

Два источника, в порядке старшинства:

1. ``WEBAPP_URL`` из .env — постоянный адрес (свой домен за Cloudflare Tunnel
   или сервер с HTTPS). Задан явно, значит именно его и надо показывать.
2. Файл, который публикует контейнер туннеля localhost.run. Адрес быстрого
   туннеля меняется при каждом переподключении, поэтому из .env его брать
   нельзя: значение читается при импорте и устаревает молча. Контейнер пишет
   актуальный адрес на общий том, а бот читает его на каждое построение кнопки.

Ни того ни другого нет — адреса нет, бот работает текстовыми экранами.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


def current() -> str:
    """Актуальный https-адрес приложения. Пустая строка — адреса нет."""
    fixed = settings.webapp_url.strip()
    if fixed:
        return fixed
    if settings.webapp_url_file:
        try:
            published = Path(settings.webapp_url_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if published.startswith("https://"):
            return published
    return ""
