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


class PickClass(CallbackData, prefix="pc"):
    """Вибір свого класу під час реєстрації."""

    class_id: int


class PickDone(CallbackData, prefix="pd"):
    """«Готово» або «Додати ще» у виборі класів."""

    more: bool


# Кроки 2 і 3 отримали ВЛАСНІ префікси, а не нове поле в MealSet. Причина
# практична: бот працює в проді, і в чатах вчителів висять старі повідомлення
# з кнопками формату "ms:...". aiogram вимагає точного збігу кількості полів
# при розпакуванні, тож будь-яке нове поле в MealSet — навіть зі значенням за
# замовчуванням — зробило б кожну ту кнопку мертвою.


class MealAbsent(CallbackData, _HasDate, prefix="mab"):
    """Крок 2: «Всього відсутніх». value=None — «Пропустити»."""

    class_id: int
    d: int
    value: int | None


class MealSick(CallbackData, _HasDate, prefix="msk"):
    """Крок 3: «З них по хворобі». value=None — «Пропустити»."""

    class_id: int
    d: int
    value: int | None


class MealManualAbsent(CallbackData, _HasDate, prefix="mma"):
    """«Інша цифра» на кроці відсутніх — коли їх більше, ніж є на сітці."""

    class_id: int
    d: int


class MealManualSick(CallbackData, _HasDate, prefix="mms"):
    """«Інша цифра» на кроці хворих."""

    class_id: int
    d: int
