"""Запис і агрегація даних про харчування."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.db.models import (
    ClassAssignment,
    EntrySource,
    MealEntry,
    MealEntryAudit,
    SchoolClass,
)
from school_bot.domain.calendar import previous_school_day


@dataclass(slots=True)
class ClassDayStatus:
    school_class: SchoolClass
    entry: MealEntry | None

    @property
    def submitted(self) -> bool:
        return self.entry is not None

    @property
    def count(self) -> int | None:
        return self.entry.eating_count if self.entry else None


@dataclass(slots=True)
class DaySummary:
    date: Date
    statuses: list[ClassDayStatus] = field(default_factory=list)

    @property
    def submitted(self) -> list[ClassDayStatus]:
        return [s for s in self.statuses if s.submitted]

    @property
    def missing(self) -> list[ClassDayStatus]:
        return [s for s in self.statuses if not s.submitted]

    @property
    def total(self) -> int:
        return sum(s.count or 0 for s in self.submitted)

    @property
    def expected(self) -> int:
        return len(self.statuses)


async def active_classes(session: AsyncSession) -> list[SchoolClass]:
    return list(
        await session.scalars(
            select(SchoolClass)
            .where(SchoolClass.is_active.is_(True))
            .order_by(SchoolClass.sort_order, SchoolClass.grade, SchoolClass.letter)
        )
    )


async def get_entry(session: AsyncSession, class_id: int, d: Date) -> MealEntry | None:
    return await session.scalar(
        select(MealEntry).where(MealEntry.class_id == class_id, MealEntry.date == d)
    )


async def upsert_entry(
    session: AsyncSession,
    *,
    class_id: int,
    d: Date,
    eating_count: int,
    teacher_id: int | None,
    source: EntrySource = EntrySource.TEACHER,
    reason: str | None = None,
) -> tuple[MealEntry, bool]:
    """Записати або оновити цифру. Повертає (запис, чи_це_була_правка).

    Кожна правка існуючого значення потрапляє в meal_entry_audit — без цього
    неможливо пояснити перевірці, чому цифра за минулий тиждень змінилася.
    """
    entry = await get_entry(session, class_id, d)
    is_update = entry is not None

    if entry is None:
        entry = MealEntry(
            class_id=class_id,
            date=d,
            eating_count=eating_count,
            entered_by_teacher_id=teacher_id,
            source=source,
        )
        session.add(entry)
        await session.flush()
        session.add(
            MealEntryAudit(
                entry_id=entry.id,
                old_value=None,
                new_value=eating_count,
                changed_by_teacher_id=teacher_id,
                reason=reason,
            )
        )
    elif entry.eating_count != eating_count:
        old = entry.eating_count
        entry.eating_count = eating_count
        entry.entered_by_teacher_id = teacher_id
        entry.source = source
        await session.flush()
        session.add(
            MealEntryAudit(
                entry_id=entry.id,
                old_value=old,
                new_value=eating_count,
                changed_by_teacher_id=teacher_id,
                reason=reason,
            )
        )

    await session.flush()
    return entry, is_update


async def day_summary(session: AsyncSession, d: Date) -> DaySummary:
    """Хто здав, хто ні, і скільки всього за конкретний день."""
    classes = await active_classes(session)
    entries = {
        e.class_id: e
        for e in await session.scalars(select(MealEntry).where(MealEntry.date == d))
    }
    return DaySummary(
        date=d,
        statuses=[ClassDayStatus(school_class=c, entry=entries.get(c.id)) for c in classes],
    )


async def last_known_count(session: AsyncSession, class_id: int, before: Date) -> int | None:
    """Значення за попередній навчальний день — для підказки «Як вчора».

    Спершу шукаємо саме попередній навчальний день; якщо запису за нього немає,
    беремо найсвіжіший наявний, щоб клавіатура все одно центрувалася осмислено.
    """
    prev = await previous_school_day(session, before)
    if prev is not None:
        entry = await get_entry(session, class_id, prev)
        if entry is not None:
            return entry.eating_count

    return await session.scalar(
        select(MealEntry.eating_count)
        .where(MealEntry.class_id == class_id, MealEntry.date < before)
        .order_by(MealEntry.date.desc())
        .limit(1)
    )


async def classes_for_teacher(session: AsyncSession, teacher_id: int) -> list[SchoolClass]:
    """Класи, закріплені за вчителем."""
    rows = await session.scalars(
        select(ClassAssignment)
        .where(ClassAssignment.teacher_id == teacher_id)
        .options(selectinload(ClassAssignment.school_class))
    )
    classes = [r.school_class for r in rows if r.school_class.is_active]
    classes.sort(key=lambda c: (c.sort_order, c.grade, c.letter))
    return classes


async def primary_teacher_ids(session: AsyncSession, class_id: int) -> list[int]:
    """Кому слати запит по цьому класу."""
    return list(
        await session.scalars(
            select(ClassAssignment.teacher_id).where(
                ClassAssignment.class_id == class_id,
                ClassAssignment.is_primary.is_(True),
            )
        )
    )
