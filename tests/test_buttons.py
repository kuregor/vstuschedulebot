"""Кнопки приложения под сообщениями: какие держать живыми и что делать с отказом.

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from bot.services.button_service import DONE, GONE, RETRY, surplus, verdict


def test_few_messages_all_stay_alive():
    assert surplus([10, 12, 11], keep=3) == []


def test_oldest_messages_lose_the_button():
    # номера приходят вразнобой — лишними считаются самые ранние
    assert surplus([5, 40, 17, 23], keep=3) == [5]
    assert surplus([5, 40, 17, 23, 2], keep=2) == [17, 5, 2]


def test_same_button_counts_as_done():
    # бот упал посреди прошлого обхода, и эта кнопка уже на новом адресе
    exc = TelegramBadRequest(
        method=None,
        message="Bad Request: message is not modified: specified new message "
        "content and reply markup are exactly the same",
    )
    assert verdict(exc) == DONE


def test_deleted_message_is_forgotten():
    exc = TelegramBadRequest(method=None, message="Bad Request: message to edit not found")
    assert verdict(exc) == GONE


def test_blocked_bot_is_forgotten():
    exc = TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
    assert verdict(exc) == GONE


def test_network_trouble_is_retried():
    assert verdict(TelegramNetworkError(method=None, message="Request timeout error")) == RETRY
    assert verdict(TelegramServerError(method=None, message="Bad Gateway")) == RETRY
    flood = TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=3)
    assert verdict(flood) == RETRY
