"""Обёртка над листом xlrd, прозрачно разворачивающая объединённые ячейки.

Расписания ВолГТУ — это одна большая "шахматка" с сотнями merged-диапазонов
(название предмета, "занята несколькими группами" лекция и т.п.). xlrd хранит
значение только в верхней левой ячейке объединения; для любой другой ячейки
внутри диапазона cell_value() вернёт "". MergedGrid.value(r, c) возвращает
значение диапазона независимо от того, в какую его ячейку мы попали — это и
есть ключевой инструмент парсера: обращаясь к "родной" колонке группы, мы
всегда получаем текст занятия, даже если реальное объединение начинается в
колонке соседней (общей) группы.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    r0: int
    r1: int  # exclusive
    c0: int
    c1: int  # exclusive

    @property
    def height(self) -> int:
        return self.r1 - self.r0

    @property
    def width(self) -> int:
        return self.c1 - self.c0


class MergedGrid:
    def __init__(self, sheet):
        self.sheet = sheet
        self.nrows = sheet.nrows
        self.ncols = sheet.ncols
        self._span_at: dict[tuple[int, int], Span] = {}
        for r0, r1, c0, c1 in sheet.merged_cells:
            span = Span(r0, r1, c0, c1)
            for r in range(r0, r1):
                for c in range(c0, c1):
                    self._span_at[(r, c)] = span

    def span(self, r: int, c: int) -> Span:
        return self._span_at.get((r, c), Span(r, r + 1, c, c + 1))

    def value(self, r: int, c: int):
        if r < 0 or c < 0 or r >= self.nrows or c >= self.ncols:
            return ""
        span = self._span_at.get((r, c))
        if span is None:
            return self.sheet.cell_value(r, c)
        return self.sheet.cell_value(span.r0, span.c0)

    def text(self, r: int, c: int) -> str:
        v = self.value(r, c)
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return str(v)
        return str(v).strip()

    def is_top_left(self, r: int, c: int) -> bool:
        span = self._span_at.get((r, c))
        return span is None or (span.r0 == r and span.c0 == c)

    def row_values(self, r: int, c0: int, c1: int) -> list[str]:
        """Уникальные (по объединению) непустые значения в строке r на [c0, c1)."""
        seen: set[Span] = set()
        out: list[str] = []
        for c in range(c0, c1):
            span = self._span_at.get((r, c), Span(r, r + 1, c, c + 1))
            if span in seen:
                continue
            seen.add(span)
            val = self.text(span.r0, span.c0)
            if val:
                out.append(val)
        return out
