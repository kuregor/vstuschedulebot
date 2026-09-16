"""Заметки к парам: чтение и запись.

Заметка живёт на месте пары в расписании (группа, дата, слот, предмет), а не
на строке `lessons`: строки пересоздаются при каждом перезаливе файла с
сайта. Приложению заметки отдаются уже привязанными к нынешним id пар — так
клиенту не нужно знать про это разделение.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Group, Lesson, LessonDate, LessonNote

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
                # «2026-09-27T09:00» — местное время, в нём же его и выбирали.
                # В базе момент лежит с часовым поясом и читается в UTC,
                # поэтому переводим обратно в пояс бота, а не печатаем как есть.
                "remind": note.remind_at.astimezone().strftime("%Y-%m-%dT%H:%M")
                if note.remind_at
                else "",
            }
        )
    return out


async def _place(
    session: AsyncSession, telegram_id: int, lesson_id: int, on_date: date
) -> dict | None:
    """Ключ заметки по паре и дате; None — такой пары в этот день нет.

    Дату проверяем по самой паре: заметка на день без занятия никогда не
    показалась бы, а напоминание пришло бы в пустоту.
    """
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        return None
    known = await session.scalar(
        select(LessonDate.id).where(
            LessonDate.lesson_id == lesson_id, LessonDate.on_date == on_date
        )
    )
    if known is None:
        return None
    return dict(
        telegram_id=telegram_id,
        group_id=lesson.group_id,
        on_date=on_date,
        slot_from=lesson.slot_from,
        subject_key=fold_subject(lesson.subject),
    )


async def _drop_if_empty(session: AsyncSession, note: LessonNote) -> None:
    """Строка без текста и без напоминания в базе не нужна."""
    if not note.text and note.remind_at is None:
        await session.delete(note)


async def set_text(
    session: AsyncSession,
    telegram_id: int,
    lesson_id: int,
    on_date: date,
    text: str,
) -> bool:
    """Пишет текст заметки; пустой стирает её, не трогая напоминание."""
    key = await _place(session, telegram_id, lesson_id, on_date)
    if key is None:
        return False

    note = await session.scalar(select(LessonNote).filter_by(**key))
    text = (text or "").strip()[:MAX_TEXT]
    if note is None:
        if text:
            session.add(LessonNote(**key, text=text))
    else:
        note.text = text
        if not text:
            # Заметку убрали — напоминать больше не о чем, снимаем и его.
            note.remind_at = None
            note.remind_sent = False
        await _drop_if_empty(session, note)
    await session.commit()
    return True


async def set_reminder(
    session: AsyncSession,
    telegram_id: int,
    lesson_id: int,
    on_date: date,
    at: datetime | None,
) -> bool:
    """Ставит или снимает напоминание о заметке.

    Новое время снимает отметку об отправке: человек перенёс напоминание, и
    оно должно прийти заново.
    """
    key = await _place(session, telegram_id, lesson_id, on_date)
    if key is None:
        return False

    note = await session.scalar(select(LessonNote).filter_by(**key))
    if note is None:
        if at is None:
            return True
        session.add(LessonNote(**key, text="", remind_at=at, remind_sent=False))
    else:
        note.remind_at = at
        note.remind_sent = False
        await _drop_if_empty(session, note)
    await session.commit()
    return True


async def due(session: AsyncSession, now: datetime) -> list[LessonNote]:
    """Напоминания, которым пора уйти."""
    stmt = (
        select(LessonNote)
        .where(
            LessonNote.remind_at.is_not(None),
            LessonNote.remind_sent.is_(False),
            LessonNote.remind_at <= now,
        )
        .order_by(LessonNote.remind_at)
        .limit(50)
    )
    return list(await session.scalars(stmt))


async def lesson_of(session: AsyncSession, note: LessonNote) -> tuple[Group, Lesson] | None:
    """Группа и пара, к которым привязана заметка, — для текста напоминания."""
    stmt = (
        select(Group, Lesson)
        .join(Lesson, Lesson.group_id == Group.id)
        .join(LessonDate, LessonDate.lesson_id == Lesson.id)
        .where(
            Lesson.group_id == note.group_id,
            Lesson.slot_from == note.slot_from,
            LessonDate.on_date == note.on_date,
        )
    )
    for group, lesson in await session.execute(stmt):
        if fold_subject(lesson.subject) == note.subject_key:
            return group, lesson
    return None
