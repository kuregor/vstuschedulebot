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
# та же пометка, но найденная внутри заметки: «03.09, 29.10, 1-4ч»
HOURS_IN_NOTE_RE = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*ч\.?", re.IGNORECASE)
FROM_DATE_RE = re.compile(r"занятия\s+с\s+(\d{1,2})\.(\d{1,2})", re.IGNORECASE)
SUBGROUP_RE = re.compile(r"подгруппа|подгруппам", re.IGNORECASE)
LAB_MARK_RE = re.compile(r"^лаб\.?$", re.IGNORECASE)

TEACHER_RE = re.compile(
    r"^(?:доц\.?|проф\.?|ст\.?\s*пр\.?|асс\.?|преп\.?|ст\.?\s*преп\.?)?\s*"
    r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s*"
    r"[А-ЯЁ]\.\s*[А-ЯЁ]?\.?$"
)
ROOM_RE = re.compile(r"^(?:[А-ЯЁA-Z]\s*-\s*)?\d{1,4}\s*[а-яёa-z]?(?:\s*-\s*\d)?$", re.IGNORECASE)

# Явная пометка типа в тексте блока: только в скобках, чтобы не спутать с
# названиями предметов вроде «ПРОИЗВОДСТВЕННАЯ ПРАКТИКА» или «ФИЗИЧЕСКИЙ ПРАКТИКУМ»
LECTURE_MARK_RE = re.compile(r"\(\s*лекц", re.IGNORECASE)

SLOTS_IN_DAY = 6  # пар в дне: «1-2» … «11-12»

TYPE_LECTURE = "lek"
TYPE_SEMINAR = "sem"
TYPE_LAB = "lab"


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


def marked_as_lecture(subject: str, note: str) -> bool:
    """В блоке прямо написано «(лекция)» — это главнее любых догадок."""
    return bool(LECTURE_MARK_RE.search(f"{subject} {note}"))


def lesson_type(
    shared_with_groups: bool, slots: int, biweekly: bool, marked_lecture: bool = False
) -> str:
    """Тип занятия. В тексте файла он размечен лишь местами, поэтому в
    основном берётся из структуры расписания. Правила по порядку:

    1. в блоке написано «(лекция)» — лекция;
    2. пара стоит одновременно у нескольких групп — лекция;
    3. пара на один слот (2 академических часа) либо идущая раз в две
       недели, сколько бы слотов ни занимала, — практика;
    4. остальное, то есть два слота (4 часа) реже чем раз в две недели, —
       лабораторная.

    Правила покрывают все случаи, поэтому «тип не указан» больше не бывает.
    """
    if marked_lecture or shared_with_groups:
        return TYPE_LECTURE
    if biweekly or slots < 2:
        return TYPE_SEMINAR
    return TYPE_LAB


def hours_to_slots(note: str) -> tuple[int, int] | None:
    """Пометка вида «1-4ч» — настоящие академические часы занятия.

    Учебный отдел иногда ставит блок не в ту строку сетки, а фактическое время
    дописывает внутрь блока. Такая пометка главнее позиции блока.
    Часы 1-2 — это первая пара, 3-4 — вторая и так далее: слот = (час - 1) // 2.
    """
    m = HOURS_IN_NOTE_RE.search(note or "")
    if not m:
        return None
    first, last = int(m.group(1)), int(m.group(2))
    if not 1 <= first <= last <= 2 * SLOTS_IN_DAY:
        return None
    return (first - 1) // 2, (last - 1) // 2


def clean_subject(subject: str) -> str:
    """Убирает служебные суффиксы типа '(лекция)' из названия предмета."""
    out = re.sub(r"\s*\((?:лекц\w*|лаб\w*|практ\w*|сем\w*)\.?\)\s*", " ", subject, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()
