"""Проверка подписи данных запуска Mini App."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from bot.webapp.auth import InitDataError, parse_init_data, user_id_from_init_data

TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id: int = 42, auth_date: int | None = None, token: str = TOKEN) -> str:
    pairs = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_init_data_gives_user_id():
    assert user_id_from_init_data(make_init_data(777), TOKEN) == 777


def test_signature_from_other_token_is_rejected():
    forged = make_init_data(777, token="999:OTHER-TOKEN")
    with pytest.raises(InitDataError):
        parse_init_data(forged, TOKEN)


def test_tampered_user_is_rejected():
    raw = make_init_data(777)
    tampered = raw.replace("%22id%22%3A+777", "%22id%22%3A+1")
    with pytest.raises(InitDataError):
        parse_init_data(tampered, TOKEN)


def test_stale_init_data_is_rejected():
    old = make_init_data(777, auth_date=int(time.time()) - 90000)
    with pytest.raises(InitDataError):
        parse_init_data(old, TOKEN)


def test_empty_init_data_is_rejected():
    with pytest.raises(InitDataError):
        parse_init_data("", TOKEN)
