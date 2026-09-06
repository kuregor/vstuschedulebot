"""Тесты разбора ячеек и расчёта дат (без файла .xls — он не хранится в репозитории).

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

from bot.parsing.classify import (
    TYPE_LAB,
    TYPE_LECTURE,
    TYPE_OTHER,
    classify_cell,
    clean_subject,
    lesson_type,
)
from bot.parsing.dates import explicit_dates, lesson_dates, recurring_dates
from bot.parsing.vstu_xls import detect_program_level

SEM_START = date(2026, 9, 1)  # вторник
SEM_END = date(2026, 12, 31)


def test_classify_cells():
    assert classify_cell("СИСТЕМНАЯ ИНЖЕНЕРИЯ") == "subject"
    assert classify_cell("МАТЕМАТИЧЕСКИЕ МЕТОДЫ В ФИЗИКЕ (лекция)") == "subject"
    assert classify_cell("доц. Кравченя П.Д.") == "teacher"
    assert classify_cell("Ионкина Е.Ю.") == "teacher"
    assert classify_cell("В-1302а") == "room"
    assert classify_cell("408а") == "room"
    assert classify_cell("14.09, 12.10, 09.11,07.12") == "note"
    assert classify_cell("занятия с 16.09") == "note"
    assert classify_cell("9-12ч") == "note"
    assert classify_cell("лаб.") == "note"


def test_lesson_type_and_clean_subject():
    assert lesson_type("ТЕХНОЛОГИИ АНАЛИЗА ДАННЫХ (лекция)", "") == TYPE_LECTURE
    assert lesson_type("АНАЛИЗ И ВИЗУАЛИЗАЦИЯ ДАННЫХ", "лаб.") == TYPE_LAB
    assert lesson_type("СИСТЕМНАЯ ИНЖЕНЕРИЯ", "01.10, 29.10") == TYPE_OTHER
    assert clean_subject("ТЕХНОЛОГИИ АНАЛИЗА ДАННЫХ (лекция)") == "ТЕХНОЛОГИИ АНАЛИЗА ДАННЫХ"


def test_program_level_detection():
    assert detect_program_level("Учебные занятия 1 курса магистров ФЭВТ") == "master"
    assert detect_program_level("", "ОН_Магистратура_1 курс ФЭВТ.xls") == "master"
    assert detect_program_level("", "ОН_Бакалавриат_2 курс ФЭВТ.xls") == "bachelor"


def test_explicit_dates_are_taken_as_is():
    got = explicit_dates("29.09, 27.10, 24.11,22.12", SEM_START, SEM_END)
    assert got == [date(2026, 9, 29), date(2026, 10, 27), date(2026, 11, 24), date(2026, 12, 22)]


def test_recurring_dates_alternate_by_week_parity():
    # семестр начинается во вторник 01.09.2026; первый понедельник — 31.08
    week1_tue = recurring_dates(weekday=2, week=1, semester_start=SEM_START, semester_end=SEM_END)
    week2_tue = recurring_dates(weekday=2, week=2, semester_start=SEM_START, semester_end=SEM_END)
    assert week1_tue[0] == date(2026, 9, 1)
    assert week2_tue[0] == date(2026, 9, 8)
    # чередование через неделю
    assert (week1_tue[1] - week1_tue[0]).days == 14
    # вместе недели дают каждую неделю — так в файле выглядит «еженедельно»
    assert not set(week1_tue) & set(week2_tue)


def test_lesson_dates_respects_start_date_note():
    got = lesson_dates("занятия с 16.09", weekday=3, week=1, semester_start=SEM_START, semester_end=SEM_END)
    assert got[0] == date(2026, 9, 16)
    assert all(d >= date(2026, 9, 16) for d in got)


def test_lesson_dates_prefers_explicit_list():
    got = lesson_dates("30.09, 28.10", weekday=3, week=1, semester_start=SEM_START, semester_end=SEM_END)
    assert got == [date(2026, 9, 30), date(2026, 10, 28)]
