"""Подписи и счёт недель — общее для API приложения.

Экранов текстом у бота больше нет: расписание показывает Mini App, а здесь
осталось то, чем подписываются его дни, месяцы и недели.
"""
from __future__ import annotations

from datetime import date, timedelta

DOW_SHORT = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
DOW_FULL = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def plural_pairs(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} пара"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} пары"
    return f"{n} пар"


def week_of(on_date: date, semester_start: date) -> int:
    """1 = числитель, 2 = знаменатель — по чётности недели от начала семестра."""
    first_monday = semester_start - timedelta(days=semester_start.weekday())
    delta_weeks = (on_date - first_monday).days // 7
    return 1 if delta_weeks % 2 == 0 else 2
