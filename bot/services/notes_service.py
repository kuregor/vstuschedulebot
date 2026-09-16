"""Заметки к парам: чтение и запись.

Заметка живёт на месте пары в расписании (группа, дата, слот, предмет), а не
на строке `lessons`: строки пересоздаются при каждом перезаливе файла с
сайта. Приложению заметки отдаются уже привязанными к нынешним id пар — так
клиенту не нужно знать про это разделение.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Lesson, LessonDate, LessonNote

# Длиннее в карточку пары всё равно не влезет, а в базе такие тексты копятся.
MAX_TEXT = 500


def fold_subject(value: str) -> str:
    """Название предмета для привязки: регистр и лишние пробелы не в счёт."""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()[:200]


async def _places(session: AsyncSession, group_id: int) -> dict[tuple, int]:
    """{(дата, слот, предмет): id пары} — по нынешнему расписанию группы."""
    rows = await session.execute(
        select(Lesson.id, Lesson.slot_from, Lesson.subject, LessonDate.on_date)
        .join(LessonDate, LessonDate.lesson_id == Lesson.id)
        .where(Lesson.group_id == group_id)
    )
    return {
        (on_date, slot_from, fold_subject(subject)): lesson_id
        for lesson_id, slot_from, subject, on_date in rows
    }


async def list_notes(
    session: AsyncSession, telegram_id: int, group_id: int
) -> list[dict]:
    """Заметки пользователя по этой группе -> [{lesson_id, date, text}].

    Заметку, под которую в нынешнем расписании пары не нашлось (учебный отдел
    убрал занятие), не показываем, но и не удаляем: файл правят туда-сюда, и
    занятие может вернуться.
    """
    notes = list(
        await session.scalars(
            select(LessonNote).where(
                LessonNote.telegram_id == telegram_id,
                LessonNote.group_id == group_id,
            )
        )
    )
    if not notes:
        return []

    places = await _places(session, group_id)
    out: list[dict] = []
    for note in notes:
        lesson_id = places.get((note.on_date, note.slot_from, note.subject_key))
        if lesson_id is None:
            continue
        out.append(
            {
                "lesson_id": lesson_id,
                "date": note.on_date.isoformat(),
                "text": note.text,
            }
        )
    return out


async def set_note(
    session: AsyncSession,
    telegram_id: int,
    lesson_id: int,
    on_date: date,
    text: str,
) -> bool:
    """Пишет заметку к паре на дату; пустой текст удаляет её. -> удалось ли.

    Дата проверяется по самой паре: писать заметку на день, когда занятия
    нет, незачем — она всё равно никогда не покажется.
    """
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        return False
    known = await session.scalar(
        select(LessonDate.id).where(
            LessonDate.lesson_id == lesson_id, LessonDate.on_date == on_date
        )
    )
    if known is None:
        return False

    key = dict(
        telegram_id=telegram_id,
        group_id=lesson.group_id,
        on_date=on_date,
        slot_from=lesson.slot_from,
        subject_key=fold_subject(lesson.subject),
    )
    text = (text or "").strip()[:MAX_TEXT]

    if not text:
        await session.execute(
            delete(LessonNote).filter_by(**key)
        )
        await session.commit()
        return True

    note = await session.scalar(select(LessonNote).filter_by(**key))
    if note is None:
        session.add(LessonNote(**key, text=text))
    else:
        note.text = text
    await session.commit()
    return True
