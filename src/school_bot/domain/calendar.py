"""Чи є дата навчальним днем."""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.db.models import DayKind, NonSchoolDay

WEEKEND = (5, 6)  # субота, неділя


def is_weekend(d: Date) -> bool:
    return d.weekday() in WEEKEND


async def non_school_day(session: AsyncSession, d: Date) -> NonSchoolDay | None:
    return await session.scalar(select(NonSchoolDay).where(NonSchoolDay.date == d))


async def is_school_day(session: AsyncSession, d: Date) -> bool:
    """Навчальний день = будній і не позначений як неробочий."""
    if is_weekend(d):
        return False
    return await non_school_day(session, d) is None


async def school_days_in_month(session: AsyncSession, year: int, month: int) -> list[Date]:
    """Усі навчальні дні місяця, по порядку."""
    first = Date(year, month, 1)
    last = Date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)

    marked = set(
        await session.scalars(
            select(NonSchoolDay.date).where(NonSchoolDay.date.between(first, last))
        )
    )
    days: list[Date] = []
    cursor = first
    while cursor <= last:
        if not is_weekend(cursor) and cursor not in marked:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


async def previous_school_day(session: AsyncSession, d: Date, *, limit: int = 14) -> Date | None:
    """Найближчий навчальний день до `d`. Потрібен для підказки «Як вчора»."""
    cursor = d - timedelta(days=1)
    for _ in range(limit):
        if await is_school_day(session, cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


async def mark_range(
    session: AsyncSession,
    start: Date,
    end: Date,
    kind: DayKind,
    note: str | None = None,
) -> int:
    """Позначити діапазон неробочим. Вихідні пропускаються — вони й так неробочі.

    Повертає кількість реально доданих днів.
    """
    if end < start:
        start, end = end, start

    existing = set(
        await session.scalars(
            select(NonSchoolDay.date).where(NonSchoolDay.date.between(start, end))
        )
    )
    added = 0
    cursor = start
    while cursor <= end:
        if not is_weekend(cursor) and cursor not in existing:
            session.add(NonSchoolDay(date=cursor, kind=kind, note=note))
            added += 1
        cursor += timedelta(days=1)
    await session.flush()
    return added


async def unmark_range(session: AsyncSession, start: Date, end: Date) -> int:
    """Прибрати позначки неробочих днів у діапазоні."""
    if end < start:
        start, end = end, start
    rows = list(
        await session.scalars(select(NonSchoolDay).where(NonSchoolDay.date.between(start, end)))
    )
    for row in rows:
        await session.delete(row)
    await session.flush()
    return len(rows)
