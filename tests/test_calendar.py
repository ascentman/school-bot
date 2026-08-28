from __future__ import annotations

from datetime import date

from school_bot.db.models import DayKind
from school_bot.domain.calendar import (
    is_school_day,
    is_weekend,
    mark_range,
    previous_school_day,
    school_days_in_month,
    unmark_range,
)


def test_weekend_detection():
    assert is_weekend(date(2026, 9, 5))      # субота
    assert is_weekend(date(2026, 9, 6))      # неділя
    assert not is_weekend(date(2026, 9, 4))  # пʼятниця


async def test_weekend_is_not_school_day(session):
    assert not await is_school_day(session, date(2026, 9, 5))
    assert await is_school_day(session, date(2026, 9, 4))


async def test_marked_day_is_not_school_day(session):
    await mark_range(session, date(2026, 10, 28), date(2026, 11, 3), DayKind.VACATION, "Осінні")
    assert not await is_school_day(session, date(2026, 10, 28))
    assert not await is_school_day(session, date(2026, 11, 3))
    assert await is_school_day(session, date(2026, 11, 4))


async def test_mark_range_skips_weekends(session):
    # 28.10.2026 (ср) – 03.11.2026 (вт): 7 днів, з них 31.10 і 01.11 — вихідні.
    added = await mark_range(session, date(2026, 10, 28), date(2026, 11, 3), DayKind.VACATION)
    assert added == 5


async def test_mark_range_is_idempotent(session):
    first = await mark_range(session, date(2026, 10, 28), date(2026, 10, 30), DayKind.VACATION)
    second = await mark_range(session, date(2026, 10, 28), date(2026, 10, 30), DayKind.VACATION)
    assert first == 3
    assert second == 0


async def test_mark_range_accepts_reversed_bounds(session):
    added = await mark_range(session, date(2026, 10, 30), date(2026, 10, 28), DayKind.VACATION)
    assert added == 3


async def test_unmark_range(session):
    await mark_range(session, date(2026, 10, 28), date(2026, 10, 30), DayKind.VACATION)
    removed = await unmark_range(session, date(2026, 10, 28), date(2026, 10, 30))
    assert removed == 3
    assert await is_school_day(session, date(2026, 10, 28))


async def test_school_days_in_month_september(session):
    days = await school_days_in_month(session, 2026, 9)
    assert len(days) == 22          # вересень 2026: 22 будні
    assert days[0] == date(2026, 9, 1)
    assert days[-1] == date(2026, 9, 30)


async def test_school_days_in_february_leap_year(session):
    days = await school_days_in_month(session, 2028, 2)
    assert days[-1] == date(2028, 2, 29)


async def test_school_days_in_december_crosses_year(session):
    days = await school_days_in_month(session, 2026, 12)
    assert days[-1] == date(2026, 12, 31)


async def test_school_days_excludes_vacation(session):
    await mark_range(session, date(2026, 10, 28), date(2026, 10, 30), DayKind.VACATION)
    days = await school_days_in_month(session, 2026, 10)
    assert date(2026, 10, 28) not in days
    assert date(2026, 10, 27) in days


async def test_previous_school_day_skips_weekend(session):
    # понеділок 07.09.2026 → пʼятниця 04.09.2026
    assert await previous_school_day(session, date(2026, 9, 7)) == date(2026, 9, 4)


async def test_previous_school_day_skips_vacation(session):
    await mark_range(session, date(2026, 11, 2), date(2026, 11, 6), DayKind.VACATION)
    assert await previous_school_day(session, date(2026, 11, 9)) == date(2026, 10, 30)


async def test_previous_school_day_gives_up_after_limit(session):
    await mark_range(session, date(2026, 1, 1), date(2026, 3, 1), DayKind.VACATION)
    assert await previous_school_day(session, date(2026, 3, 2)) is None
