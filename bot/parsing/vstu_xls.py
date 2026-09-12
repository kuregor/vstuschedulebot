"""Парсер расписаний ВолгГТУ (.xls и .xlsx, формат "шахматка" учебного отдела).

Структура листа (проверено на файле «ОН_Магистратура_1 курс ФЭВТ.xls»):

    строка 4-5   — шапка: "Учебные занятия 1 курса магистров ФЭВТ",
                   "на I семестр 2026 - 2027 учебного года";
    строка 8     — заголовок таблицы: колонки 1-4 = Сентябрь..Декабрь,
                   колонка 5 = номера пар, дальше по 4 колонки на группу
                   (ЭВМ-1.2, ЭВМ-1.3, САПР-1.1, ...);
    колонка 0    — названия дней; каждый день занимает 18 строк
                   (6 пар × 3 строки). Дни идут ДВАЖДЫ: первые шесть —
                   НЕДЕЛЯ 1, следующие шесть — НЕДЕЛЯ 2 (числитель/знаменатель);
    колонка 5    — метки пар: "1- 2", "3- 4", "5- 6", "7- 8", "9-10", "11-12";
    колонки слева от дней — числа месяцев: под "Сентябрь", "Октябрь" и так
                   далее внутри блока дня перечислены даты, когда этот день
                   с этой чётностью недели действительно идёт;
    колонки группы — сверху вниз: НАЗВАНИЕ ПРЕДМЕТА (объединённая ячейка),
                   при необходимости строка-заметка (конкретные даты,
                   "занятия с 16.09", "лаб.", "9-12ч"), затем строка
                   "преподаватель + аудитория". Занятие на 4 часа занимает
                   две пары подряд — тогда блок тянется на две "тройки" строк.

Лекция, общая для нескольких групп, объединена по колонкам — MergedGrid
возвращает её текст для каждой из накрытых групп, поэтому занятие само собой
дублируется во все нужные группы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .classify import (
    DATE_RE,
    TYPE_SEMINAR,
    classify_cell,
    clean_subject,
    hours_to_slots,
    is_room,
    marked_as_lecture,
    is_teacher,
    lesson_type,
)
from .dates import runs_biweekly
from .grid import MergedGrid
from .workbook import open_sheet

DAY_NAMES = [
    "ПОНЕДЕЛЬНИК",
    "ВТОРНИК",
    "СРЕДА",
    "ЧЕТВЕРГ",
    "ПЯТНИЦА",
    "СУББОТА",
]
DAY_INDEX = {name: i + 1 for i, name in enumerate(DAY_NAMES)}

# Заголовки колонок с датами занятий: под каждым в блоке дня стоят числа.
MONTH_HEADERS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

SLOT_LABELS = ["1-2", "3-4", "5-6", "7-8", "9-10", "11-12"]
SLOT_TIMES = [
    ("08:30", "10:00"),
    ("10:10", "11:40"),
    ("11:50", "13:20"),
    ("13:40", "15:10"),
    ("15:20", "16:50"),
    ("17:00", "18:30"),
]

ROWS_PER_SLOT = 3
SLOTS_PER_DAY = len(SLOT_LABELS)
ROWS_PER_DAY = ROWS_PER_SLOT * SLOTS_PER_DAY
DAYS_PER_WEEK = len(DAY_NAMES)

# Название группы: «САПР-1.4», «ИВТ -160», «Ф - 169», «СП - 1П»,
# «ПП-351 (мясо)», «ППМ 2» (у магистратуры дефиса иногда нет),
# «УТС-1н», «ФТКМ - 1Св» (хвост после номера бывает и строчными).
# Уточнение в скобках и пробелы к названию не относятся: перед проверкой их
# убираем, а показываем группу уже в опрятном виде. Одиночное случайное
# совпадение не страшно — колонки групп ищутся по строке с наибольшим их числом.
GROUP_NAME_RE = re.compile(r"^[А-ЯЁA-Z]{1,6}-?\d+(?:\.\d+)?[А-ЯЁA-Zа-яё]{0,2}$")


def _group_core(text: str) -> str:
    return re.sub(r"\([^)]*\)", "", text).replace(" ", "").strip()


def is_group_name(text: str) -> bool:
    return bool(GROUP_NAME_RE.match(_group_core(text)))


def tidy_group_name(text: str) -> str:
    """«ИВТ -160» -> «ИВТ-160», «ПП-351  (мясо)» -> «ПП-351 (мясо)»."""
    out = re.sub(r"\s*-\s*", "-", text.strip(), count=1)
    return re.sub(r"\s+", " ", out).strip()


COURSE_RE = re.compile(r"(\d+)\s*курс", re.IGNORECASE)
YEAR_RE = re.compile(r"(20\d{2})\s*[-–]\s*(20\d{2})")
FACULTY_RE = re.compile(r"\b(Ф[А-ЯЁ]{2,5})\b")

LEVEL_MASTER = "master"
LEVEL_BACHELOR = "bachelor"


@dataclass
class ParsedLesson:
    group: str
    week: int  # 1 или 2 (числитель/знаменатель)
    weekday: int  # 1 = понедельник ... 6 = суббота
    slot_from: int  # индекс пары 0..5
    slot_to: int  # индекс последней пары занятия (для 4-часовых блоков > slot_from)
    subject: str
    teacher: str = ""
    room: str = ""
    lesson_type: str = TYPE_SEMINAR
    raw_note: str = ""
    # Даты занятия (месяц, число) из колонок слева — то, что проставил
    # учебный отдел. Пусто, если в файле этих колонок нет.
    block_dates: list[tuple[int, int]] = field(default_factory=list)
    # предмет записан ячейкой, объединённой по колонкам нескольких групп
    shared_cell: bool = False
    # в блоке прямо написано «(лекция)»
    marked_lecture: bool = False

    @property
    def slot_label(self) -> str:
        if self.slot_from == self.slot_to:
            return SLOT_LABELS[self.slot_from]
        left = SLOT_LABELS[self.slot_from].split("-")[0]
        right = SLOT_LABELS[self.slot_to].split("-")[1]
        return f"{left}-{right}"

    @property
    def start_time(self) -> str:
        return SLOT_TIMES[self.slot_from][0]

    @property
    def end_time(self) -> str:
        return SLOT_TIMES[self.slot_to][1]


@dataclass
class ParsedSchedule:
    program_level: str = LEVEL_MASTER
    faculty: str = ""
    course: int | None = None
    year_start: int | None = None
    title: str = ""
    groups: list[str] = field(default_factory=list)
    lessons: list[ParsedLesson] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _header_text(grid: MergedGrid, max_row: int = 8) -> str:
    parts: list[str] = []
    for r in range(0, max_row):
        for c in range(0, min(grid.ncols, 30)):
            if grid.is_top_left(r, c):
                t = grid.text(r, c)
                if t:
                    parts.append(t)
    return " ".join(parts)


def detect_program_level(header: str, filename: str = "") -> str:
    blob = f"{header} {filename}".lower()
    if "магистр" in blob:
        return LEVEL_MASTER
    if "бакалавр" in blob:
        return LEVEL_BACHELOR
    if "специалит" in blob or "специалист" in blob:
        return LEVEL_BACHELOR
    # По умолчанию считаем бакалавриатом: их файлов кратно больше.
    return LEVEL_BACHELOR


def _find_group_columns(
    grid: MergedGrid, max_row: int | None = None
) -> tuple[int, list[tuple[str, int]]]:
    """Ищет строку заголовка с названиями групп -> (row, [(имя, колонка), ...]).

    Искать разрешено только выше первого дня недели: ниже начинается сетка
    занятий, а код аудитории («Б-602», «А 404») по форме от названия группы
    не отличается, и строка с парой аудиторий перетянула бы заголовок на себя.
    """
    limit = min(grid.nrows, 40 if max_row is None else max_row)
    best: tuple[int, list[tuple[str, int]]] = (-1, [])
    for r in range(0, limit):
        found: list[tuple[str, int]] = []
        for c in range(0, grid.ncols):
            if not grid.is_top_left(r, c):
                continue
            t = grid.text(r, c)
            if t and is_group_name(t):
                found.append((tidy_group_name(t), c))
        if len(found) > len(best[1]):
            best = (r, found)
    return best


def find_day_column(grid: MergedGrid, limit: int = 8) -> int:
    """Колонка с названиями дней недели.

    У магистратуры дни стоят в колонке 0, у бакалавриата — в колонке 4: слева
    от них четыре колонки с числами месяцев (Сентябрь … Декабрь). Колонку
    ищем по числу попаданий в список дней, а не по фиксированному номеру.
    """
    best_col, best_hits = 0, 0
    for c in range(0, min(limit, grid.ncols)):
        hits = sum(
            1
            for r in range(0, grid.nrows)
            if grid.text(r, c).upper().strip() in DAY_INDEX
        )
        if hits > best_hits:
            best_col, best_hits = c, hits
    return best_col


def find_month_columns(grid: MergedGrid, limit_col: int, max_row: int) -> dict[int, int]:
    """{колонка: номер месяца} по заголовкам в левой части листа.

    Порядок колонок у факультетов разный: у бакалавриата месяцы идут первыми,
    а день недели за ними, у магистратуры наоборот. Поэтому ищем месяцы во
    всём, что левее первой группы, а не на заранее известных местах.

    Нужны хотя бы два месяца в строке: одиночное слово «Сентябрь» попадается
    и в тексте шапки.
    """
    for r in range(0, min(max_row, grid.nrows)):
        found = {
            c: MONTH_HEADERS[grid.text(r, c).strip().lower()]
            for c in range(0, min(limit_col, grid.ncols))
            if grid.text(r, c).strip().lower() in MONTH_HEADERS
        }
        if len(found) >= 2:
            return found
    return {}


def block_dates(
    grid: MergedGrid, month_cols: dict[int, int], row0: int
) -> list[tuple[int, int]]:
    """Даты (месяц, число) одного блока дня из колонок с числами месяцев.

    Числа разбросаны по строкам блока: в месяце таких дат две-три, а строк
    восемнадцать. К какой паре число ближе, значения не имеет — это даты
    всего дня целиком, поэтому собираем их по всему блоку.

    Берём только ячейки, где стоит одно число: в некоторых файлах заголовки
    месяцев повторяются внутри блока, а кое-где попадаются обрывки пометок
    вроде «-3».
    """
    out: list[tuple[int, int]] = []
    for col, month in sorted(month_cols.items()):
        for row in range(row0, min(row0 + ROWS_PER_DAY, grid.nrows)):
            text = grid.text(row, col).strip()
            if not text.isdigit():
                continue
            day = int(text)
            if 1 <= day <= 31 and (month, day) not in out:
                out.append((month, day))
    return sorted(out)


def _find_day_blocks(grid: MergedGrid, day_col: int = 0) -> list[tuple[int, int, int]]:
    """-> [(week, weekday, row0), ...] в порядке появления в файле."""
    hits: list[tuple[int, int]] = []  # (row, weekday)
    for r in range(0, grid.nrows):
        t = grid.text(r, day_col).upper().strip()
        if t in DAY_INDEX and grid.is_top_left(r, day_col):
            hits.append((r, DAY_INDEX[t]))
    blocks: list[tuple[int, int, int]] = []
    for i, (row, weekday) in enumerate(hits):
        week = 1 if i < DAYS_PER_WEEK else 2
        blocks.append((week, weekday, row))
    return blocks


def _dates_ascending(note: str) -> bool:
    """Даты в ячейке перечисляются по возрастанию; нарушение — обычно опечатка."""
    pairs = [(int(m), int(d)) for d, m in DATE_RE.findall(note or "")]
    return pairs == sorted(pairs)


def _row_cells(grid: MergedGrid, row: int, c0: int, c1: int) -> list[tuple[int, str]]:
    """[(колонка, значение)] по строке без повторов объединённых ячеек."""
    out: list[tuple[int, str]] = []
    seen = set()
    for c in range(max(c0, 0), min(c1, grid.ncols)):
        span = grid.span(row, c)
        if span in seen:
            continue
        seen.add(span)
        t = grid.text(span.r0, span.c0)
        if t:
            out.append((c, t))
    return out


def _find_teacher(cells: list[tuple[int, str]]) -> tuple[int, str] | None:
    for col, val in cells:
        if is_teacher(val):
            return col, val
    return None


def _extract_room(cells: list[tuple[int, str]], teacher_col: int, limit: int) -> str:
    """Аудитория стоит правее преподавателя, иногда с выходом за 4 колонки блока."""
    for col, val in cells:
        if col <= teacher_col or col >= limit:
            continue
        if is_teacher(val):
            # дошли до данных следующей группы — своей аудитории нет
            break
        if is_room(val):
            return val
    return ""


def _parse_group_day(
    grid: MergedGrid,
    group: str,
    gc0: int,
    col_limit: int,
    week: int,
    weekday: int,
    row0: int,
    warnings: list[str],
    day_dates: list[tuple[int, int]] | None = None,
) -> list[ParsedLesson]:
    """Линейный проход сверху вниз по колонкам одной группы внутри блока дня."""
    lessons: list[ParsedLesson] = []
    block_width = 4
    current: dict | None = None

    def flush(end_row: int) -> None:
        nonlocal current
        if current is None:
            return
        slot_from = min((current["row"] - row0) // ROWS_PER_SLOT, SLOTS_PER_DAY - 1)
        slot_to = min(max((end_row - row0) // ROWS_PER_SLOT, slot_from), SLOTS_PER_DAY - 1)
        note = ", ".join(current["notes"])
        subject_raw = current["subject"]

        # Пометка «1-4ч» внутри блока — настоящие часы занятия. Учебный отдел
        # иногда ставит блок не в свою строку сетки, и тогда позиция врёт,
        # а пометка нет: время берём из неё.
        block_label = SLOT_LABELS[slot_from]
        by_hours = hours_to_slots(note)
        moved = by_hours is not None and by_hours != (slot_from, slot_to)
        if by_hours is not None:
            slot_from, slot_to = by_hours
        lessons.append(
            ParsedLesson(
                group=group,
                week=week,
                weekday=weekday,
                slot_from=slot_from,
                slot_to=slot_to,
                subject=clean_subject(subject_raw),
                teacher=current["teacher"],
                room=current["room"],
                raw_note=note,
                block_dates=list(day_dates or ()),
                shared_cell=current["shared"],
                marked_lecture=marked_as_lecture(subject_raw, note),
            )
        )
        where = (
            f"{group}: неделя {week}, {DAY_NAMES[weekday - 1].lower()}, "
            f"«{clean_subject(subject_raw)}»"
        )
        if not current["teacher"]:
            warnings.append(f"{where} — не найден преподаватель")
        if moved:
            warnings.append(
                f"{where} — блок стоит на парах {block_label}, но внутри указано "
                f"«{SLOT_LABELS[slot_from].split('-')[0]}-"
                f"{SLOT_LABELS[slot_to].split('-')[1]}ч»: время взято из пометки"
            )
        if not _dates_ascending(note):
            warnings.append(
                f"{where} — даты в файле идут не по возрастанию "
                f"(«{note}»), возможна опечатка в исходнике"
            )
        current = None

    def add_notes(values: list[str]) -> None:
        assert current is not None
        for v in values:
            norm = re.sub(r"\s+", " ", v).strip()
            if norm and norm not in current["notes"]:
                current["notes"].append(norm)

    for row in range(row0, min(row0 + ROWS_PER_DAY, grid.nrows)):
        cells = _row_cells(grid, row, gc0, gc0 + block_width)
        if not cells and current is None:
            continue
        roles = [(col, val, classify_cell(val)) for col, val in cells]
        subjects = [val for _, val, role in roles if role == "subject"]
        notes = [val for _, val, role in roles if role == "note"]

        if subjects:
            subject = subjects[0]
            if current is not None and current["subject"] == subject:
                # продолжение того же объединения (занятие на 4 часа) — не новая пара
                continue
            same_slot = (row - row0) // ROWS_PER_SLOT == (
                current["row"] - row0
            ) // ROWS_PER_SLOT if current is not None else False
            if current is not None and same_slot and not current["teacher"]:
                # Длинное название разбито по строкам отдельными ячейками
                # («НЕОРГАНИЧЕСКАЯ» / «ХИМИЯ»). Внутри одной тройки строк двух
                # разных пар быть не может, а преподаватель ещё не встретился —
                # значит это продолжение названия, а не новое занятие.
                current["subject"] = f"{current['subject']} {subject}".strip()
                add_notes(notes)
                continue
            flush(row - 1)
            span = grid.span(row, gc0)
            current = {
                "row": row,
                "subject": subject,
                "notes": [],
                "teacher": "",
                "room": "",
                # лекция, объединённая на несколько групп: преподавателя и
                # аудиторию ищем по всей ширине объединения
                "search_c0": min(span.c0, gc0),
                "search_c1": max(span.c1, gc0 + block_width),
                # ячейка предмета шире четырёх колонок группы — значит эта пара
                # стоит сразу у нескольких групп, то есть читается лекцией
                "shared": span.width > block_width,
            }
            add_notes(notes)  # заметка иногда стоит в той же строке, что и предмет
            continue

        if current is None:
            continue

        # Преподаватель/аудитория ищутся сначала в своих 4 колонках, затем —
        # по всей ширине объединения предмета (лекция на несколько групп
        # подписана один раз, в колонках первой из них).
        found = _find_teacher([(col, val) for col, val, _ in roles])
        if found is None and current["search_c1"] - current["search_c0"] > block_width:
            wide_cells = _row_cells(grid, row, current["search_c0"], current["search_c1"])
            found = _find_teacher(wide_cells)

        if found is not None:
            teacher_col, teacher = found
            current["teacher"] = teacher
            room_limit = max(current["search_c1"], min(col_limit, gc0 + 8))
            room_cells = _row_cells(grid, row, teacher_col, room_limit)
            current["room"] = _extract_room(room_cells, teacher_col, room_limit)
            flush(row)
            continue

        add_notes(notes)

    flush(min(row0 + ROWS_PER_DAY, grid.nrows) - 1)
    return lessons


def _share_key(lesson: ParsedLesson) -> tuple:
    """Чем опознаётся одна и та же пара, стоящая у нескольких групп: время,
    предмет, аудитория и даты занятий.

    Даты в ключе обязательны. Две группы часто ходят в одну аудиторию к одному
    преподавателю в одно и то же время, но по очереди — через неделю друг от
    друга: это лабораторные по подгруппам, а не общая лекция. Отличаются они
    как раз перечнем дат. Преподаватель в ключ не входит — его фамилию в файле
    пишут по-разному («Харланов А.В» и «Харламов А.В.»)."""
    dates = frozenset(DATE_RE.findall(lesson.raw_note or ""))
    return (
        lesson.week,
        lesson.weekday,
        lesson.slot_from,
        lesson.subject,
        lesson.room,
        dates,
    )


def assign_lesson_types(lessons: list[ParsedLesson]) -> None:
    """Проставляет тип занятия. Виден только на всём файле сразу: одна и та же
    пара у нескольких групп — лекция, поэтому по группам это не определить."""
    groups_by_key: dict[tuple, set[str]] = {}
    for lesson in lessons:
        if not lesson.room:
            # без аудитории совпадение ненадёжно (пустое поле склеило бы
            # разные пары) — для таких полагаемся только на объединение ячеек
            continue
        groups_by_key.setdefault(_share_key(lesson), set()).add(lesson.group)

    for lesson in lessons:
        shared = lesson.shared_cell or (
            len(groups_by_key.get(_share_key(lesson), ())) > 1 if lesson.room else False
        )
        lesson.lesson_type = lesson_type(
            shared,
            lesson.slot_to - lesson.slot_from + 1,
            runs_biweekly(lesson.raw_note),
            marked_lecture=lesson.marked_lecture,
        )


def parse_workbook(path: str, filename: str = "") -> ParsedSchedule:
    grid = MergedGrid(open_sheet(path))

    header = _header_text(grid)
    result = ParsedSchedule(title=header.strip()[:500])
    result.program_level = detect_program_level(header, filename)

    m = COURSE_RE.search(header)
    if m:
        result.course = int(m.group(1))
    m = YEAR_RE.search(header)
    if m:
        result.year_start = int(m.group(1))
    m = FACULTY_RE.search(header)
    if m:
        result.faculty = m.group(1)

    day_col = find_day_column(grid)
    blocks = _find_day_blocks(grid, day_col)
    if not blocks:
        raise ValueError("Не найдены блоки дней недели — формат файла не распознан")

    header_row, group_cols = _find_group_columns(grid, max_row=blocks[0][2])
    if not group_cols:
        raise ValueError("Не найдена строка с названиями групп — формат файла не распознан")
    result.groups = [name for name, _ in group_cols]

    month_cols = find_month_columns(
        grid, limit_col=group_cols[0][1], max_row=blocks[0][2]
    )
    if not month_cols:
        result.warnings.append(
            "в файле нет колонок с числами месяцев — даты занятий рассчитаны "
            "по чётности недели"
        )
    dates_of_block = {
        row0: block_dates(grid, month_cols, row0) for _w, _d, row0 in blocks
    }

    for idx, (name, gc0) in enumerate(group_cols):
        col_limit = group_cols[idx + 1][1] if idx + 1 < len(group_cols) else grid.ncols
        for week, weekday, row0 in blocks:
            result.lessons.extend(
                _parse_group_day(
                    grid, name, gc0, col_limit, week, weekday, row0,
                    result.warnings, dates_of_block[row0],
                )
            )

    assign_lesson_types(result.lessons)

    return result
