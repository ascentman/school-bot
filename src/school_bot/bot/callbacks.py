"""Типізовані callback_data.

Дата передається як ordinal (ціле число днів), а не ISO-рядок: aiogram не вміє
пакувати `date`, а ліміт callback_data — 64 байти, тож 6 цифр економніші за 10 символів.
"""

from __future__ import annotations

from datetime import date as Date

from aiogram.filters.callback_data import CallbackData


class _HasDate:
    """Домішка: зручний доступ до дати, що зберігається як ordinal."""

    d: int

    @property
    def date(self) -> Date:
        return Date.fromordinal(self.d)


class MealSet(CallbackData, _HasDate, prefix="ms"):
    """Тап по цифрі."""

    class_id: int
    d: int
    value: int


class MealEdit(CallbackData, _HasDate, prefix="me"):
    """«Виправити» — повернути клавіатуру для вже відповіданого запису."""

    class_id: int
    d: int


class MealManual(CallbackData, _HasDate, prefix="mm"):
    """«Інша цифра» — перейти у ввід з клавіатури."""

    class_id: int
    d: int


class AdminAction(CallbackData, prefix="a"):
    action: str
    arg: str = ""


class MonthPick(CallbackData, prefix="mo"):
    year: int
    month: int
    fmt: str = "xlsx"


class ClassToggle(CallbackData, prefix="ct"):
    class_id: int
