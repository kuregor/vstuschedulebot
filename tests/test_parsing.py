"""Тесты разбора ячеек и расчёта дат (без файла .xls — он не хранится в репозитории).

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

from bot.parsing.classify import (
    TYPE_LAB,
    TYPE_LECTURE,
    TYPE_SEMINAR,
    classify_cell,
    clean_subject,
    collapse_letter_spacing,
    hours_to_slots,
    lesson_type,
    marked_as_lecture,
)
from bot.parsing.dates import (
    explicit_dates,
    lesson_dates,
    recurring_dates,
    runs_biweekly,
)
from bot.parsing.vstu_xls import (
    ParsedLesson,
    assign_lesson_types,
    detect_program_level,
    is_group_name,
    tidy_group_name,
)

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


def test_lesson_type_rules():
    # пара сразу у нескольких групп — лекция, что бы ни было с длительностью
    assert lesson_type(True, 2, biweekly=False) == TYPE_LECTURE
    assert lesson_type(True, 1, biweekly=True) == TYPE_LECTURE
    # своя пара на 4 академических часа реже, чем раз в две недели, — лаба
    assert lesson_type(False, 2, biweekly=False) == TYPE_LAB
    # один слот (2 часа) — практика, сколько бы раз ни повторялась
    assert lesson_type(False, 1, biweekly=False) == TYPE_SEMINAR
    assert lesson_type(False, 1, biweekly=True) == TYPE_SEMINAR
    # 4 часа, но раз в две недели — тоже практика (так стоит «проф. ин-яз»)
    assert lesson_type(False, 2, biweekly=True) == TYPE_SEMINAR


def _lesson(group: str, slot_from: int, slot_to: int, subject: str, room: str, **kw):
    return ParsedLesson(
        group=group,
        week=1,
        weekday=1,
        slot_from=slot_from,
        slot_to=slot_to,
        subject=subject,
        room=room,
        **kw,
    )


def test_same_pair_in_several_groups_is_lecture():
    note = "30.09, 28.10, 25.11,23.12"
    lessons = [
        _lesson("ЭВМ-1.2", 0, 1, "БОЛЬШИЕ ДАННЫЕ", "В-209", raw_note=note),
        _lesson("ЭВМ-1.3", 0, 1, "БОЛЬШИЕ ДАННЫЕ", "В-209", raw_note=note),
        # своя пара той же группы в другое время — не лекция
        _lesson("ЭВМ-1.2", 4, 5, "СИСТЕМНАЯ ИНЖЕНЕРИЯ", "В-402", raw_note=note),
    ]
    assign_lesson_types(lessons)
    assert [l.lesson_type for l in lessons] == [TYPE_LECTURE, TYPE_LECTURE, TYPE_LAB]


def test_same_subject_in_different_rooms_is_not_lecture():
    """Один предмет в одно время, но в разных аудиториях — это разные занятия."""
    lessons = [
        _lesson("САПР-1.1", 0, 0, "ИНОСТРАННЫЙ ЯЗЫК", "Б-602"),
        _lesson("САПР-1.3", 0, 0, "ИНОСТРАННЫЙ ЯЗЫК", "Б-604"),
    ]
    assign_lesson_types(lessons)
    assert [l.lesson_type for l in lessons] == [TYPE_SEMINAR, TYPE_SEMINAR]


def test_subgroups_taking_turns_are_labs_not_lecture():
    """Две группы в одной аудитории в одно время, но по очереди — через неделю
    друг от друга: это лабы по подгруппам, а не общая лекция."""
    lessons = [
        _lesson("ЭВМ-1.2", 0, 1, "БОЛЬШИЕ ДАННЫЕ", "В-1301",
                raw_note="03.09, 29.10, 26.11,24.12"),
        _lesson("ЭВМ-1.3", 0, 1, "БОЛЬШИЕ ДАННЫЕ", "В-1301",
                raw_note="17.09, 15.10, 12.11,10.12"),
    ]
    assign_lesson_types(lessons)
    assert [l.lesson_type for l in lessons] == [TYPE_LAB, TYPE_LAB]


def test_biweekly_four_hour_pair_is_practice():
    """«Проф. ин-яз» идёт 4 часа, но раз в две недели — практика, не лаба."""
    lessons = [_lesson("Ф-1", 0, 1, "ПРОФ. ИН-ЯЗ КОММУНИКАЦИЯ", "408а")]
    assign_lesson_types(lessons)
    assert lessons[0].lesson_type == TYPE_SEMINAR


def test_merged_cell_marks_lecture_even_without_room_match():
    lessons = [_lesson("Ф-1", 0, 1, "ФИЗИКА", "", shared_cell=True)]
    assign_lesson_types(lessons)
    assert lessons[0].lesson_type == TYPE_LECTURE


def test_hours_note_overrides_block_position():
    """«1-4ч» внутри блока — настоящее время пары, даже если блок стоит ниже."""
    assert hours_to_slots("03.09, 29.10, 26.11,24.12, 1-4ч") == (0, 1)
    assert hours_to_slots("16.09, 14.10, 11.11,09.12, 5-8 ч") == (2, 3)
    assert hours_to_slots("9-12ч") == (4, 5)
    # обычные заметки временем не считаются
    assert hours_to_slots("14.09, 12.10, 09.11,07.12") is None
    assert hours_to_slots("занятия с 16.09") is None
    # часов в дне только 12
    assert hours_to_slots("1-14ч") is None


def test_explicit_lecture_mark_wins():
    """Если в блоке написано «(лекция)» — это лекция, что бы ни говорила сетка."""
    assert marked_as_lecture("МАТЕМАТИЧЕСКИЕ МЕТОДЫ В ФИЗИКЕ (лекция)", "") is True
    assert lesson_type(False, 1, biweekly=True, marked_lecture=True) == TYPE_LECTURE
    # названия предметов со словом «практика» пометкой не считаются
    assert marked_as_lecture("ПРОИЗВОДСТВЕННАЯ ПРАКТИКА: НАУЧНО-ИССЛЕД. РАБОТА", "") is False
    assert marked_as_lecture("СПЕЦИАЛЬНЫЙ ФИЗИЧЕСКИЙ ПРАКТИКУМ", "") is False


def test_marked_lecture_in_single_group_pair():
    lessons = [_lesson("Ф-1", 4, 4, "МАТЕМАТИЧЕСКИЕ МЕТОДЫ В ФИЗИКЕ", "315а",
                       marked_lecture=True)]
    assign_lesson_types(lessons)
    assert lessons[0].lesson_type == TYPE_LECTURE


def test_biweekly_detection():
    assert runs_biweekly("") is True                       # нет дат — чередование
    assert runs_biweekly("занятия с 16.09") is True        # оно же, но позже начинается
    assert runs_biweekly("15.09, 29.09, 13.10, 27.10") is True   # шаг две недели
    assert runs_biweekly("03.09, 29.10, 26.11,24.12") is False   # раз в месяц
    assert runs_biweekly("02.10") is False                 # разовое занятие
    # опечатка в датах («13.10» вместо «13.11») — шаг считать нельзя
    assert runs_biweekly("02.10, 16.10, 13.10, 11.12") is False


def test_hours_note_is_not_confused_with_subgroups():
    assert hours_to_slots("1-2 подгруппа") is None
    assert hours_to_slots("по подгруппам, 14.09") is None


def test_clean_subject():
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


def test_teacher_may_come_without_initials():
    # часть факультетов пишет в расписании одну фамилию
    assert classify_cell("Бикус") == "teacher"
    assert classify_cell("доц. Кравченя П.Д.") == "teacher"
    # название предмета заглавными за преподавателя не принимаем
    assert classify_cell("ХИМИЯ") == "subject"


def test_letter_spaced_titles_are_collapsed():
    assert collapse_letter_spacing(
        "Н    Е   О    Р    Г    А    Н    И    Ч    Е    С    К     А     Я"
        "                      Х    И    М    И    Я"
    ) == "НЕОРГАНИЧЕСКАЯ ХИМИЯ"
    # один разрядкой набранный термин остаётся одним словом
    assert collapse_letter_spacing(
        "Ф         И         Л         О         С         О         Ф         И         Я"
    ) == "ФИЛОСОФИЯ"
    # обычное название не трогаем
    assert collapse_letter_spacing("МАТЕМАТИЧЕСКИЙ АНАЛИЗ") == "МАТЕМАТИЧЕСКИЙ АНАЛИЗ"


def test_group_names_of_all_faculties():
    for name in ["САПР-1.4", "ИВТ -160", "Ф - 169", "СП - 1П", "ПП-351 (мясо)",
                 "ППМ 2", "УТС-1н", "ФТКМ - 1Св"]:
        assert is_group_name(name), name
    for name in ["Сентябрь", "ПОНЕДЕЛЬНИК", "1- 2", "ФИЗИКА", ""]:
        assert not is_group_name(name), name


def test_group_name_is_tidied_for_display():
    assert tidy_group_name("ИВТ -160") == "ИВТ-160"
    assert tidy_group_name("Ф - 169") == "Ф-169"
    assert tidy_group_name("ПП-351  (мясо)") == "ПП-351 (мясо)"
