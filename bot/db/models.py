"""Схема БД: группы разнесены по уровням (бакалавриат / магистратура)."""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProgramLevel(str, enum.Enum):
    bachelor = "bachelor"
    master = "master"


class LessonType(str, enum.Enum):
    lek = "lek"
    sem = "sem"
    lab = "lab"


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("name", "program_level", name="uq_group_name_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    program_level: Mapped[ProgramLevel] = mapped_column(
        Enum(ProgramLevel, name="program_level"), index=True
    )
    faculty: Mapped[str | None] = mapped_column(String(32))
    course: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    @property
    def level_title(self) -> str:
        return "Магистратура" if self.program_level is ProgramLevel.master else "Бакалавриат"


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    week: Mapped[int] = mapped_column(Integer)  # 1 = числитель, 2 = знаменатель
    weekday: Mapped[int] = mapped_column(Integer)  # 1 = понедельник ... 6 = суббота
    slot_from: Mapped[int] = mapped_column(Integer)
    slot_to: Mapped[int] = mapped_column(Integer)
    slot_label: Mapped[str] = mapped_column(String(16))
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    subject: Mapped[str] = mapped_column(Text)
    teacher: Mapped[str] = mapped_column(String(128), default="")
    room: Mapped[str] = mapped_column(String(32), default="")
    lesson_type: Mapped[LessonType] = mapped_column(
        Enum(LessonType, name="lesson_type"), default=LessonType.sem
    )
    raw_note: Mapped[str] = mapped_column(Text, default="")

    group: Mapped[Group] = relationship(back_populates="lessons")
    dates: Mapped[list["LessonDate"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan", lazy="selectin"
    )


class LessonDate(Base):
    """Конкретные календарные даты занятия — из них строится вкладка «Календарь»."""

    __tablename__ = "lesson_dates"
    __table_args__ = (UniqueConstraint("lesson_id", "on_date", name="uq_lesson_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), index=True
    )
    on_date: Mapped[date] = mapped_column(Date, index=True)

    lesson: Mapped[Lesson] = relationship(back_populates="dates")


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped[Group | None] = relationship(lazy="joined")


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text)  # URL или имя файла
    program_level: Mapped[ProgramLevel | None] = mapped_column(
        Enum(ProgramLevel, name="program_level")
    )
    faculty: Mapped[str | None] = mapped_column(String(32))
    course: Mapped[int | None] = mapped_column(Integer)
    groups_count: Mapped[int] = mapped_column(Integer, default=0)
    lessons_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
