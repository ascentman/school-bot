"""Поточний час у часовому поясі школи.

Окремим модулем, щоб «сьогодні» рахувалося одним способом: розкидані по коду
datetime.now(settings.tz).date() легко розходяться, а в тестах їх не підмінити.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from school_bot.config import settings


def now() -> datetime:
    return datetime.now(settings.tz)


def today() -> Date:
    return now().date()


def hhmm() -> str:
    """Поточний час як 09:05 — для підтверджень у чаті."""
    return now().strftime("%H:%M")
