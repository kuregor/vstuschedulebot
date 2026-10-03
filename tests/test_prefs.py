"""Личные настройки приложения: что бот соглашается хранить.

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from bot.webapp.api import TEACHER_NAME_MAX, clean_prefs


def test_known_settings_pass_as_is():
    prefs = {"real": True, "teacher": "Королева И.Ю."}
    assert clean_prefs(prefs) == prefs


def test_empty_teacher_means_own_group():
    assert clean_prefs({"real": False, "teacher": ""}) == {"real": False, "teacher": ""}


def test_unknown_keys_and_wrong_types_are_dropped():
    assert clean_prefs({"real": "yes", "teacher": 5, "admin": True}) == {}
    # 1 — не True: в JSON это разные вещи, и хранить надо то, что прислали
    assert clean_prefs({"real": 1}) == {}


def test_not_an_object_gives_nothing():
    assert clean_prefs(None) == {}
    assert clean_prefs(["real", True]) == {}


def test_teacher_is_trimmed_but_not_renamed():
    # имя приходит из справочника уже сведённым — сервер его не переписывает
    assert clean_prefs({"teacher": "  доц. Королева И. Ю  "}) == {
        "teacher": "доц. Королева И. Ю"
    }
    assert len(clean_prefs({"teacher": "Я" * 500})["teacher"]) == TEACHER_NAME_MAX
