"""Клавіатури. Головне тут — number_pad: відповідь має вимагати одного дотику."""

from __future__ import annotations

from datetime import date as Date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from school_bot.bot import texts
from school_bot.bot.callbacks import (
    AdminAction,
    ClassToggle,
    MealAbsent,
    MealEdit,
    MealManual,
    MealSet,
    MealSick,
    MonthPick,
    PickClass,
    PickDone,
)
from school_bot.domain.dates import month_name

PAD_WIDTH = 4      # кнопок у рядку
PAD_SPAN = 12      # скільки чисел показуємо
DEFAULT_CENTER = 20

# Другий і третій крок: цифри малі, тож рядок ширший, а сітка коротша.
SMALL_PAD_WIDTH = 5
ABSENT_SPAN = 20   # 0..19 — вистачає навіть на грип у класі
SKIP_LABEL = "⏭ Пропустити"


def number_pad(
    class_id: int,
    d: Date,
    *,
    last_known: int | None,
    max_children: int,
) -> InlineKeyboardMarkup:
    """Сітка чисел, центрована на значенні попереднього навчального дня.

    Сенс: у переважній більшості днів кількість дітей майже не змінюється,
    тому потрібна цифра вже на екрані й вчителю досить одного дотику.
    """
    kb = InlineKeyboardBuilder()

    if last_known is not None:
        kb.row(
            InlineKeyboardButton(
                text=f"↩︎ Як минулого разу: {last_known}",
                callback_data=MealSet(class_id=class_id, d=d.toordinal(), value=last_known).pack(),
            )
        )

    center = last_known if last_known is not None else DEFAULT_CENTER
    start = max(0, center - PAD_SPAN // 2 - 1)
    end = min(max_children, start + PAD_SPAN - 1)
    start = max(0, end - PAD_SPAN + 1)  # не даємо сітці схлопнутися біля верхньої межі

    buttons = [
        InlineKeyboardButton(
            text=str(n),
            callback_data=MealSet(class_id=class_id, d=d.toordinal(), value=n).pack(),
        )
        for n in range(start, end + 1)
    ]
    for i in range(0, len(buttons), PAD_WIDTH):
        kb.row(*buttons[i : i + PAD_WIDTH])

    kb.row(
        InlineKeyboardButton(
            text="0 — немає",
            callback_data=MealSet(class_id=class_id, d=d.toordinal(), value=0).pack(),
        ),
        InlineKeyboardButton(
            text="✏️ Інша цифра",
            callback_data=MealManual(class_id=class_id, d=d.toordinal()).pack(),
        ),
    )
    return kb.as_markup()


def _small_pad(
    buttons: list[InlineKeyboardButton], skip: InlineKeyboardButton
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i in range(0, len(buttons), SMALL_PAD_WIDTH):
        kb.row(*buttons[i : i + SMALL_PAD_WIDTH])
    kb.row(skip)
    return kb.as_markup()


def _mark(n: int, current: int | None) -> str:
    """Позначити вже записану цифру — щоб повторний прохід не був наосліп."""
    return f"· {n}" if current is not None and n == current else str(n)


def absent_pad(
    class_id: int, d: Date, *, current: int | None, max_children: int
) -> InlineKeyboardMarkup:
    """Крок 2. Свідомо БЕЗ підказки «як минулого разу».

    Кількість відсутніх скаче день у день, тому вчорашня цифра нічого не
    підказує — на відміну від харчування. Натомість 0 стоїть першим.

    «Іншої цифри» тут немає навмисно: вона тягне за собою FSM, а стан у боті
    памʼятається лише до перезапуску й лише один на вчителя — класний керівник
    двох класів не зміг би відповідати по обох. Тому сітка одразу покриває
    весь реалістичний діапазон.
    """
    top = min(max_children, ABSENT_SPAN - 1)
    buttons = [
        InlineKeyboardButton(
            text=_mark(n, current),
            callback_data=MealAbsent(class_id=class_id, d=d.toordinal(), value=n).pack(),
        )
        for n in range(0, top + 1)
    ]
    skip = InlineKeyboardButton(
        text=SKIP_LABEL,
        callback_data=MealAbsent(class_id=class_id, d=d.toordinal(), value=None).pack(),
    )
    return _small_pad(buttons, skip)


def sick_pad(
    class_id: int, d: Date, *, current: int | None, max_absent: int
) -> InlineKeyboardMarkup:
    """Крок 3. Стеля — кількість відсутніх: хворих не буває більше за відсутніх."""
    buttons = [
        InlineKeyboardButton(
            text=_mark(n, current),
            callback_data=MealSick(class_id=class_id, d=d.toordinal(), value=n).pack(),
        )
        for n in range(0, max_absent + 1)
    ]
    skip = InlineKeyboardButton(
        text=SKIP_LABEL,
        callback_data=MealSick(class_id=class_id, d=d.toordinal(), value=None).pack(),
    )
    return _small_pad(buttons, skip)


def edit_button(class_id: int, d: Date) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✏️ Виправити",
            callback_data=MealEdit(class_id=class_id, d=d.toordinal()).pack(),
        )
    )
    return kb.as_markup()


def share_contact() -> ReplyKeyboardMarkup:
    """Кнопка, якою Telegram надсилає боту номер користувача.

    Це єдиний спосіб дізнатися номер: Bot API не дозволяє шукати людей за
    телефоном, тож саме користувач ініціює обмін.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_SHARE_CONTACT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def picker(items: list[tuple[int, str]], action: str, per_row: int = 1) -> InlineKeyboardMarkup:
    """Список кнопок «обрати щось» — вчителя, клас, будь-що з id та назвою."""
    kb = InlineKeyboardBuilder()
    for item_id, name in items:
        kb.button(text=name, callback_data=AdminAction(action=action, arg=str(item_id)).pack())
    kb.adjust(per_row)
    return kb.as_markup()


def my_classes(d: Date, rows: list[tuple[int, str, int | None]]) -> InlineKeyboardMarkup:
    """Класи вчителя з поточним станом. Дає ввести дані без початкового запиту."""
    kb = InlineKeyboardBuilder()
    for class_id, name, value in rows:
        label = f"{name} — {value}" if value is not None else f"{name} — ще не подано"
        mark = "✅" if value is not None else "▫️"
        kb.button(
            text=f"{mark} {label}",
            callback_data=MealEdit(class_id=class_id, d=d.toordinal()).pack(),
        )
    kb.adjust(1)
    return kb.as_markup()


def pick_class(classes: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Сітка класів школи для вибору при реєстрації."""
    kb = InlineKeyboardBuilder()
    for class_id, name in classes:
        kb.button(text=name, callback_data=PickClass(class_id=class_id).pack())
    kb.adjust(4)
    return kb.as_markup()


def pick_more() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➕ Ще один клас", callback_data=PickDone(more=True).pack()),
        InlineKeyboardButton(text="✅ Це все", callback_data=PickDone(more=False).pack()),
    )
    return kb.as_markup()


def main_menu(*, is_admin: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=texts.BTN_MY_CLASSES)
    if is_admin:
        kb.button(text=texts.BTN_TODAY)
        kb.button(text=texts.BTN_REPORT)
        kb.button(text=texts.BTN_TEACHERS)
        kb.button(text=texts.BTN_CLASSES)
        kb.button(text=texts.BTN_DAYS_OFF)
        kb.button(text=texts.BTN_SETTINGS)
        kb.adjust(1, 2, 2, 2)
    else:
        kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)


def missing_classes(d: Date, missing: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Кнопки під зведенням — ввести за клас, який не подав."""
    kb = InlineKeyboardBuilder()
    for class_id, name in missing:
        kb.button(text=name, callback_data=MealEdit(class_id=class_id, d=d.toordinal()).pack())
    kb.adjust(3)
    return kb.as_markup()


def month_picker(months: list[tuple[int, int]], fmt: str = "xlsx") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for year, month in months:
        kb.button(
            text=f"{month_name(month)} {year}",
            callback_data=MonthPick(year=year, month=month, fmt=fmt).pack(),
        )
    kb.adjust(2)
    return kb.as_markup()


def class_multiselect(
    all_classes: list[tuple[int, str]],
    selected: set[int],
    done_action: str = "teacher_done",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for class_id, name in all_classes:
        mark = "☑️ " if class_id in selected else ""
        kb.button(text=f"{mark}{name}", callback_data=ClassToggle(class_id=class_id).pack())
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(
            text="✅ Готово", callback_data=AdminAction(action=done_action).pack()
        ),
        InlineKeyboardButton(
            text="✖️ Скасувати", callback_data=AdminAction(action="cancel").pack()
        ),
    )
    return kb.as_markup()


def confirm(action: str, arg: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Так", callback_data=AdminAction(action=action, arg=arg).pack()
        ),
        InlineKeyboardButton(text="✖️ Ні", callback_data=AdminAction(action="cancel").pack()),
    )
    return kb.as_markup()
