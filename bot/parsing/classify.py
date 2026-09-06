"""Определение роли ячейки и типа занятия по её тексту.

В исходном xls нет отдельных полей "предмет / преподаватель / аудитория" —
всё это просто текст в соседних ячейках. Различаем по форме записи:

* предмет      — ПОЛНОСТЬЮ ЗАГЛАВНЫМИ ("СИСТЕМНАЯ ИНЖЕНЕРИЯ", "ПРОФ. ИН-ЯЗ КОММУНИКАЦИЯ");
* преподаватель — Фамилия + инициалы, иногда со званием ("доц. Кравченя П.Д.");
* аудитория    — короткий код ("В-1302а", "408а", "329", "Б-602");
* заметка      — даты ("14.09, 12.10, ..."), "занятия с 16.09", "лаб.", "9-12ч".
"""
from __future__ import annotations

import re

DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")
HOURS_RE = re.compile(r"^\d{1,2}\s*-\s*\d{1,2}\s*ч\.?$", re.IGNORECASE)
FROM_DATE_RE = re.compile(r"занятия\s+с\s+(\d{1,2})\.(\d{1,2})", re.IGNORECASE)
SUBGROUP_RE = re.compile(r"подгруппа|подгруппам", re.IGNORECASE)
LAB_MARK_RE = re.compile(r"^лаб\.?$", re.IGNORECASE)

TEACHER_RE = re.compile(
    r"^(?:доц\.?|проф\.?|ст\.?\s*пр\.?|асс\.?|преп\.?|ст\.?\s*преп\.?)?\s*"
    r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s*"
    r"[А-ЯЁ]\.\s*[А-ЯЁ]?\.?$"
)
ROOM_RE = re.compile(r"^(?:[А-ЯЁA-Z]\s*-\s*)?\d{1,4}\s*[а-яёa-z]?(?:\s*-\s*\d)?$", re.IGNORECASE)

LECTURE_HINT_RE = re.compile(r"\(\s*лекц", re.IGNORECASE)
LAB_HINT_RE = re.compile(r"\(\s*лаб|лабораторн", re.IGNORECASE)
SEMINAR_HINT_RE = re.compile(r"\(\s*(?:пр|практ|сем)|семинар|практическ", re.IGNORECASE)

TYPE_LECTURE = "lek"
TYPE_SEMINAR = "sem"
TYPE_LAB = "lab"
TYPE_OTHER = "other"


def is_note(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return bool(
        DATE_RE.search(t)
        or HOURS_RE.match(t)
        or FROM_DATE_RE.search(t)
        or SUBGROUP_RE.search(t)
        or LAB_MARK_RE.match(t)
    )


def is_teacher(text: str) -> bool:
    return bool(TEACHER_RE.match(text.strip()))


def is_room(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 12:
        return False
    return bool(ROOM_RE.match(t))


def is_subject(text: str) -> bool:
    """Предмет — длинный текст заглавными, не похожий на заметку/аудиторию.

    Уточнения в скобках пишутся строчными («... (лекция)») и в подсчёте
    доли заглавных не участвуют, иначе такие пары терялись бы.
    """
    t = text.strip()
    if len(t) < 4:
        return False
    if is_note(t) or is_room(t) or is_teacher(t):
        return False
    core = re.sub(r"\([^)]*\)", " ", t)
    letters = [ch for ch in core if ch.isalpha()]
    if len(letters) < 4:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / len(letters) >= 0.9


def classify_cell(text: str) -> str:
    """-> 'subject' | 'teacher' | 'room' | 'note' | 'other'"""
    t = text.strip()
    if not t:
        return "other"
    if is_note(t):
        return "note"
    if is_teacher(t):
        return "teacher"
    if is_subject(t):
        return "subject"
    if is_room(t):
        return "room"
    return "other"


def lesson_type(subject: str, notes: str) -> str:
    """Тип занятия. В исходнике он размечен непоследовательно, поэтому это
    эвристика: явные подсказки в тексте, иначе TYPE_OTHER (не выдумываем)."""
    blob = f"{subject} {notes}"
    if LAB_HINT_RE.search(blob) or LAB_MARK_RE.match(notes.strip()):
        return TYPE_LAB
    if LECTURE_HINT_RE.search(blob):
        return TYPE_LECTURE
    if SEMINAR_HINT_RE.search(blob):
        return TYPE_SEMINAR
    return TYPE_OTHER


def clean_subject(subject: str) -> str:
    """Убирает служебные суффиксы типа '(лекция)' из названия предмета."""
    out = re.sub(r"\s*\((?:лекц\w*|лаб\w*|практ\w*|сем\w*)\.?\)\s*", " ", subject, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()
