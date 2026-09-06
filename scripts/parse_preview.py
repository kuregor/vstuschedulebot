"""Проверка парсера без БД и Telegram.

    python scripts\\parse_preview.py "путь\\к\\файлу.xls" [ГРУППА]

Печатает найденные группы, занятия и предупреждения. Удобно, чтобы сверить
разбор нового файла с оригиналом до загрузки в бота.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.parsing.dates import lesson_dates  # noqa: E402
from bot.parsing.vstu_xls import DAY_NAMES, parse_workbook  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    only_group = sys.argv[2] if len(sys.argv) > 2 else None

    parsed = parse_workbook(path, os.path.basename(path))
    sem_start = date(parsed.year_start or date.today().year, 9, 1)
    sem_end = date(sem_start.year, 12, 31)

    print(f"Уровень:   {parsed.program_level}")
    print(f"Факультет: {parsed.faculty or '—'}   Курс: {parsed.course or '—'}")
    print(f"Семестр:   {sem_start} … {sem_end}")
    print(f"Группы:    {', '.join(parsed.groups)}")
    print(f"Занятий:   {len(parsed.lessons)}")
    print()

    counts = Counter(lesson.group for lesson in parsed.lessons)
    for group in parsed.groups:
        print(f"  {group:12} {counts.get(group, 0):3} занятий")
    print()

    for lesson in parsed.lessons:
        if only_group and lesson.group != only_group:
            continue
        days = lesson_dates(lesson.raw_note, lesson.weekday, lesson.week, sem_start, sem_end)
        dates_s = ", ".join(f"{d.day:02d}.{d.month:02d}" for d in days[:6])
        print(
            f"{lesson.group:10} Н{lesson.week} {DAY_NAMES[lesson.weekday - 1][:3]} "
            f"{lesson.slot_label:>5} {lesson.start_time}-{lesson.end_time} "
            f"[{lesson.lesson_type:5}] {lesson.subject[:44]:44} | "
            f"{lesson.teacher:20} | {lesson.room:9} | {dates_s}"
        )

    if parsed.warnings:
        print(f"\nПредупреждений: {len(parsed.warnings)}")
        for w in parsed.warnings:
            print("  •", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
