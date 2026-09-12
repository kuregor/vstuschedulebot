"""Конфигурация бота: всё читается из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # .env необязателен, но с ним удобнее
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://schedule:schedule@localhost:5432/schedule",
        )
    )
    # Как часто перекачивать выбранные в настройках расписания с сайта ВолгГТУ.
    refresh_hours: int = field(
        default_factory=lambda: max(1, int(os.getenv("SCHEDULE_REFRESH_HOURS", "6")))
    )
    # Границы семестра для расчёта конкретных дат занятий.
    semester_year: int = field(default_factory=lambda: int(os.getenv("SEMESTER_YEAR", "2026")))
    semester_start: str = field(default_factory=lambda: os.getenv("SEMESTER_START", "2026-09-01"))
    semester_end: str = field(default_factory=lambda: os.getenv("SEMESTER_END", "2026-12-31"))

    # Mini App: публичный https-адрес (его Telegram открывает внутри себя)
    # и локальный сокет, который слушает встроенный веб-сервер.
    # WEBAPP_URL — постоянный адрес (домен). Задан — он и используется.
    webapp_url: str = field(default_factory=lambda: os.getenv("WEBAPP_URL", ""))
    # Файл, куда контейнер туннеля localhost.run пишет актуальный адрес.
    # Читается только когда WEBAPP_URL пуст: адрес быстрого туннеля меняется
    # при каждом переподключении, а из .env он читался бы один раз при импорте.
    webapp_url_file: str = field(
        default_factory=lambda: os.getenv("WEBAPP_URL_FILE", "/state/url")
    )
    webapp_host: str = field(default_factory=lambda: os.getenv("WEBAPP_HOST", "0.0.0.0"))
    webapp_port: int = field(default_factory=lambda: int(os.getenv("WEBAPP_PORT", "8080")))
    # Открывать приложение без подписи Telegram (для отладки в браузере).
    webapp_allow_insecure: bool = field(
        default_factory=lambda: os.getenv("WEBAPP_ALLOW_INSECURE", "0") == "1"
    )


def load_settings() -> Settings:
    return Settings()


settings = load_settings()
