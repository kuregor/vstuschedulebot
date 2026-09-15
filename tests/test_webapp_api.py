"""Подписи недель и версия ответа в API приложения.

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

from bot.db.models import Group, ProgramLevel
from bot.webapp.api import (
    _week_range_label,
    schedule_etag,
    schedule_json,
    week_occurrence,
)

START, END = date(2026, 9, 1), date(2026, 12, 31)


def label(week: int, today: date) -> str:
    """Подпись недели так, как её собирает schedule_json."""
    return _week_range_label(week_occurrence(week, START, END, today), today)


def test_current_week_is_marked():
    # 12 сентября — пятница недели 2 (семестр начался во вторник 1 сентября)
    today = date(2026, 9, 12)
    assert week_occurrence(2, START, END, today) == (date(2026, 9, 7), date(2026, 9, 12))
    assert label(2, today) == "07–12 сен · сейчас"


def test_other_week_shows_next_occurrence():
    # неделя 1 на этот момент уже прошла — показываем следующую такую же
    today = date(2026, 9, 12)
    assert label(1, today) == "14–19 сен"


def test_label_moves_with_the_semester():
    # та же неделя 1, но в октябре — подпись другая
    assert label(1, date(2026, 10, 20)) == "26–31 окт"


def test_week_across_two_months():
    assert label(1, date(2026, 10, 1)) == "28 сен – 03 окт · сейчас"


def test_first_week_starts_at_semester_start():
    # семестр начинается во вторник, понедельник 31 августа в него не входит
    left, _ = week_occurrence(1, START, END, date(2026, 9, 1))
    assert left == START


def test_after_classes_last_week_stays():
    # занятия кончились — подпись замирает на последней неделе, а не пустеет
    left, right = week_occurrence(1, START, END, date(2027, 3, 1))
    assert right <= END
    assert left <= right


class FakeGroup:
    """Для расчёта версии от группы нужен только её id."""

    def __init__(self, group_id: int) -> None:
        self.id = group_id


STAMP = '"abc123"|Mon, 14 Sep 2026 10:00:00 GMT'


def test_etag_holds_while_nothing_changes():
    group, today = FakeGroup(7), date(2026, 9, 15)
    assert schedule_etag(group, STAMP, today) == schedule_etag(group, STAMP, today)


def test_etag_changes_when_site_file_changes():
    # учебный отдел перевыложил файл — сайт отдал новые ETag/Last-Modified
    group, today = FakeGroup(7), date(2026, 9, 15)
    other = '"zzz999"|Tue, 15 Sep 2026 08:30:00 GMT'
    assert schedule_etag(group, STAMP, today) != schedule_etag(group, other, today)


def test_etag_changes_next_day():
    # в ответе есть is_today, current_week и подпись «· сейчас» —
    # со вчерашним кэшем они показывали бы не тот день
    group = FakeGroup(7)
    assert schedule_etag(group, STAMP, date(2026, 9, 15)) != schedule_etag(
        group, STAMP, date(2026, 9, 16)
    )


def test_etag_differs_between_groups():
    today = date(2026, 9, 15)
    assert schedule_etag(FakeGroup(7), STAMP, today) != schedule_etag(
        FakeGroup(8), STAMP, today
    )


def test_week_carries_machine_dates():
    """Границы недели уходят клиенту и датами: по ним он оставляет в списке
    только пары, которые на этом повторении недели действительно идут."""
    group = Group(id=1, name="САПР-1.4", program_level=ProgramLevel.master, course=1)
    weeks = schedule_json(group, [], today=date(2026, 9, 15))["weeks"]
    assert [(w["from"], w["to"]) for w in weeks] == [
        ("2026-09-14", "2026-09-19"),
        ("2026-09-21", "2026-09-26"),
    ]
