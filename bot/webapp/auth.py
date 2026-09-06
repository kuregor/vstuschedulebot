"""Проверка initData, которую Telegram передаёт в Mini App.

Telegram подписывает параметры запуска приложения:
    secret = HMAC_SHA256(key="WebAppData", msg=<токен бота>)
    hash   = HMAC_SHA256(key=secret, msg=<пары ключ=значение, отсортированы, через \\n>)

Без этой проверки любой мог бы открыть API от чужого имени, подставив
произвольный telegram_id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 60 * 60


class InitDataError(Exception):
    pass


def parse_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE_SECONDS) -> dict:
    """Проверяет подпись и возвращает разобранные данные запуска."""
    if not init_data:
        raise InitDataError("нет данных запуска")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise InitDataError("нет подписи")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("подпись не совпадает")

    auth_date = int(pairs.get("auth_date", "0"))
    if max_age and auth_date and time.time() - auth_date > max_age:
        raise InitDataError("данные запуска устарели")

    user_raw = pairs.get("user")
    if user_raw:
        try:
            pairs["user"] = json.loads(user_raw)
        except json.JSONDecodeError as exc:
            raise InitDataError("не удалось разобрать данные пользователя") from exc
    return pairs


def user_id_from_init_data(init_data: str, bot_token: str) -> int:
    data = parse_init_data(init_data, bot_token)
    user = data.get("user") or {}
    user_id = user.get("id")
    if not user_id:
        raise InitDataError("в данных запуска нет пользователя")
    return int(user_id)
