"""Справочник преподавателей и их расписание по всем группам.

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

from bot.db.models import Lesson, LessonDate, LessonType
from bot.services.teacher_service import build_names, clean_name, summarize
from bot.webapp.api import teacher_etag, teacher_json, teachers_etag


def test_title_and_spaces_are_dropped():
    assert clean_name("доц.  Королева И.Ю.") == "Королева И.Ю."
    assert clean_name("доц.Смирнов Е.А") == "Смирнов Е.А."
    assert clean_name("ст. преп. Ершов Е.П.") == "Ершов Е.П."
    assert clean_name("ст.пр Фирсова С.Ю.") == "Фирсова С.Ю."
    assert clean_name("проф. Кухарь Е. И.") == "Кухарь Е.И."
    assert clean_name("доц  Гресь И.М.") == "Гресь И.М."
    assert clean_name("ГабельченкоН.И.") == "Габельченко Н.И."
    assert clean_name("   ") == ""


def test_same_person_written_differently_is_one_name():
    names = build_names(["доц. Королева И.Ю.", "Королева  И. Ю.", "Королева И.Ю"])
    assert set(names.values()) == {"Королева И.Ю."}


def test_bare_surname_joins_the_only_full_name():
    names = build_names(["Чечет", "Чечет Т.И."])
    assert names["Чечет"] == "Чечет Т.И."


def test_bare_surname_stays_when_ambiguous():
    # две Кузнецовых с разными инициалами — угадывать, о ком речь, нельзя
    names = build_names(["Кузнецова", "Кузнецова Н.В.", "Кузнецова А.А."])
    assert names["Кузнецова"] == "Кузнецова"


def test_stream_lecture_counts_once():
    names = build_names(["Королева И.Ю."])
    rows = [
        ("Королева И.Ю.", "Инфокоммуникационные технологии", 1, 2, 9),
        ("Королева И.Ю.", "Инфокоммуникационные технологии", 1, 2, 9),
        ("Королева И.Ю.", "Инфокоммуникационные технологии", 2, 2, 5),
    ]
    summary = summarize(rows, names)["Королева И.Ю."].json()
    assert summary["count"] == 2
    assert summary["subjects"] == ["Инфокоммуникационные технологии"]


def lesson(lesson_id: int, days: list[date], slot: int = 9, kind=LessonType.lek) -> Lesson:
    item = Lesson(
        id=lesson_id,
        group_id=lesson_id,
        week=1,
        weekday=2,
        slot_from=slot,
        slot_to=slot + 3,
        slot_label=f"{slot}–{slot + 3}",
        start_time="15:20",
        end_time="18:30",
        subject="Инфокоммуникационные технологии",
        teacher="доц. Королева И.Ю.",
        room="В-208",
        lesson_type=kind,
        raw_note="",
    )
    item.dates = [LessonDate(on_date=day) for day in days]
    return item


DAYS = [date(2026, 9, 15), date(2026, 10, 13)]


def test_stream_lecture_is_one_lesson_with_groups():
    rows = [
        (lesson(12, DAYS), "САПР-1.10"),
        (lesson(11, DAYS), "САПР-1.9"),
    ]
    data = teacher_json("Королева И.Ю.", rows, today=date(2026, 9, 15))
    day = data["weeks"][0]["days"][1]
    assert len(day["lessons"]) == 1
    item = day["lessons"][0]
    assert item["groups"] == ["САПР-1.9", "САПР-1.10"]
    assert item["ids"] == [11, 12]
    assert item["id"] == 11
    assert data["teacher"] == {"name": "Королева И.Ю.", "lessons": 1, "groups": 2}
    assert data["index"]["2026-09-15"] == [11]


def test_different_dates_stay_separate():
    # лабы двух групп в одном слоте через неделю — две разные пары
    rows = [
        (lesson(1, [date(2026, 9, 15)], kind=LessonType.lab), "САПР-1.1"),
        (lesson(2, [date(2026, 9, 29)], kind=LessonType.lab), "САПР-1.2"),
    ]
    data = teacher_json("Королева И.Ю.", rows, today=date(2026, 9, 15))
    assert len(data["weeks"][0]["days"][1]["lessons"]) == 2


def test_directory_etag_depends_on_catalog_and_group():
    # справочник не зависит от даты, зато «ведут у группы» — от группы
    base = teachers_etag(4, "stamp1")
    assert base == teachers_etag(4, "stamp1")
    assert base != teachers_etag(4, "stamp2")
    assert base != teachers_etag(5, "stamp1")
    assert base != teachers_etag(None, "stamp1")


def test_teacher_etag_depends_on_catalog_and_day():
    today = date(2026, 9, 15)
    base = teacher_etag("Королева И.Ю.", "stamp1", today)
    assert base == teacher_etag("Королева И.Ю.", "stamp1", today)
    assert base != teacher_etag("Королева И.Ю.", "stamp2", today)
    assert base != teacher_etag("Королева И.Ю.", "stamp1", date(2026, 9, 16))
    assert base != teacher_etag("Андреев А.Е.", "stamp1", today)
