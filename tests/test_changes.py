"""Разница между старым и новым расписанием группы.

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

from bot.parsing.dates import ORIGIN_CALC, ORIGIN_FILE, ORIGIN_NOTE
from bot.services.changes import LessonSig, compare, is_mass_date_shift, summarize

TODAY = date(2026, 9, 10)


def sig(**over) -> LessonSig:
    """Пара «вторник недели 1, 2 пара, матанализ» — базовая для всех проверок."""
    base = dict(
        week=1,
        weekday=2,
        slot_from=2,
        slot_label="2",
        start="10:10",
        end="11:45",
        subject="Математический анализ",
        teacher="Иванов И.И.",
        room="302",
        kind="lek",
        dates=("2026-09-15", "2026-10-13", "2026-11-10"),
        origin=ORIGIN_FILE,
    )
    base.update(over)
    return LessonSig(**base)


def test_nothing_changed():
    assert compare([sig()], [sig()]) == []


def test_room_change_is_one_edit():
    diff = compare([sig()], [sig(room="415")])
    assert [c.kind for c in diff] == ["edit"]
    assert diff[0].details == ("аудитория 302 → 415",)


def test_teacher_and_type_change_together():
    diff = compare([sig()], [sig(teacher="Петров П.П.", kind="sem")])
    assert diff[0].details == (
        "преподаватель Иванов И.И. → Петров П.П.",
        "лекция → практика",
    )


def test_new_lesson_is_addition():
    other = sig(subject="Физика", slot_from=3, slot_label="3", start="12:00")
    diff = compare([sig()], [sig(), other])
    assert [c.kind for c in diff] == ["add"]
    assert diff[0].lesson.subject == "Физика"


def test_dropped_lesson_is_removal():
    diff = compare([sig(), sig(subject="Физика", slot_from=3, slot_label="3")], [sig()])
    assert [c.kind for c in diff] == ["remove"]
    assert diff[0].lesson.subject == "Физика"


def test_subject_case_and_spaces_do_not_count():
    # учебный отдел переименовал предмет только регистром — это не изменение
    assert compare([sig()], [sig(subject="МАТЕМАТИЧЕСКИЙ  анализ")]) == []


def test_changes_are_sorted_by_place_in_the_grid():
    old = [sig(), sig(week=2, weekday=1, slot_from=1, slot_label="1")]
    new = [
        sig(room="415"),
        sig(week=2, weekday=1, slot_from=1, slot_label="1", room="100"),
    ]
    diff = compare(old, new)
    assert [(c.lesson.week, c.lesson.weekday) for c in diff] == [(1, 2), (2, 1)]


def test_summary_names_group_and_day():
    text = summarize("САПР-1.4", compare([sig()], [sig(room="415")]))
    assert "САПР-1.4" in text
    assert "Неделя 1 · вторник" in text
    assert "аудитория 302 → 415" in text


def test_summary_escapes_html():
    # название предмета уходит в сообщение с parse_mode=HTML
    diff = compare([sig(subject="Теория <и> практика")], [])
    assert "&lt;и&gt;" in summarize("САПР-1.4", diff)


# ── даты занятий: перенос, прошлое, происхождение ─────────────────


def dated(**over) -> list:
    """Сравнение с включёнными датами — как при неизменных правилах расчёта."""
    return compare(
        [sig()], [sig(**over)], today=TODAY, with_dates=True
    )


def test_dates_are_ignored_unless_asked():
    # правила расчёта дат менялись — сравнивать их нельзя
    assert compare([sig()], [sig(dates=("2026-09-22",))], today=TODAY) == []


def test_single_move_reads_as_transfer():
    diff = dated(dates=("2026-09-15", "2026-10-20", "2026-11-10"))
    assert diff[0].dates == "перенос 13.10 → 20.10"


def test_several_dates_are_listed_both_ways():
    diff = dated(dates=("2026-09-15", "2026-11-10", "2026-12-01", "2026-12-15"))
    assert diff[0].dates == "даты +01.12, 15.12, −13.10"


def test_past_dates_do_not_count():
    # 15.09 уже позади 10.09? нет — а вот 01.09 прошло, и его пропажа молчит
    was = sig(dates=("2026-09-01", "2026-09-15"))
    now = sig(dates=("2026-09-15",))
    assert compare([was], [now], today=TODAY, with_dates=True) == []


def test_calculated_dates_are_not_compared():
    was = sig(origin=ORIGIN_CALC)
    now = sig(origin=ORIGIN_CALC, dates=("2026-09-15", "2026-12-22"))
    assert compare([was], [now], today=TODAY, with_dates=True) == []


def test_changed_origin_is_not_compared():
    # в новом файле появились числа там, где мы их раньше считали сами
    now = sig(origin=ORIGIN_NOTE, dates=("2026-09-22",))
    assert compare([sig()], [now], today=TODAY, with_dates=True) == []


def test_transfer_joins_other_details():
    diff = dated(room="415", dates=("2026-09-15", "2026-10-20", "2026-11-10"))
    text = summarize("САПР-1.4", diff)
    assert "аудитория 302 → 415; перенос 13.10 → 20.10" in text


def test_mass_shift_is_not_a_change_of_schedule():
    moved = [sig(subject=f"Предмет {i}", slot_from=i) for i in range(6)]
    shifted = [
        sig(subject=f"Предмет {i}", slot_from=i, dates=("2026-09-15", "2026-10-20"))
        for i in range(6)
    ]
    diff = compare(moved, shifted, today=TODAY, with_dates=True)
    assert len(diff) == 6
    assert is_mass_date_shift(diff, lessons_total=6)


def test_single_transfer_is_not_a_mass_shift():
    diff = dated(dates=("2026-09-15", "2026-10-20", "2026-11-10"))
    assert not is_mass_date_shift(diff, lessons_total=15)


def test_room_change_never_looks_like_a_recount():
    assert not is_mass_date_shift(dated(room="415"), lessons_total=1)
