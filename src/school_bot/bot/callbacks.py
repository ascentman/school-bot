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


# "ms" і "mm" — кнопки знятого з обліку харчування. Класи лишаються, щоб
# бот міг розпакувати їх і чесно сказати, що запит застарів: у чатах вчителів
# висять ранкові повідомлення з цими кнопками, і без хендлера тап по них
# виглядав би як мовчання бота.


class MealSet(CallbackData, _HasDate, prefix="ms"):
    """Застаріле: тап по цифрі харчування."""

    class_id: int
    d: int
    value: int


class MealManual(CallbackData, _HasDate, prefix="mm"):
    """Застаріле: «Інша цифра» на кроці харчування."""

    class_id: int
    d: int


class MealEdit(CallbackData, _HasDate, prefix="me"):
    """«Виправити» — повернути клавіатуру для вже відповіданого запису."""

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


# Кожен крок має ВЛАСНИЙ префікс, а не спільний клас із полем «крок». Причина
# практична: бот працює в проді, і в чатах вчителів висять старі повідомлення
# з кнопками. aiogram вимагає точного збігу кількості полів при розпакуванні,
# тож будь-яке нове поле — навіть зі значенням за замовчуванням — зробило б
# кожну ту кнопку мертвою.


class MealAbsent(CallbackData, _HasDate, prefix="mab"):
    """Крок 1: «Всього відсутніх».

    value=None лишається читним заради кнопок «Пропустити», які висять у
    чатах з часів, коли відсутні були другим питанням після харчування.
    """

    class_id: int
    d: int
    value: int | None


class MealSick(CallbackData, _HasDate, prefix="msk"):
    """Крок 2: «З них по хворобі». value=None — «Пропустити»."""

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
