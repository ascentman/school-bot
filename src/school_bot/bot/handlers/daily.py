"""Обробка відповіді на щоденний запит: тап по цифрі, ручний ввід, виправлення."""

from __future__ import annotations

import logging
from datetime import date as Date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot import keyboards, texts
from school_bot.bot.callbacks import (
    MealAbsent,
    MealEdit,
    MealManual,
    MealManualAbsent,
    MealManualSick,
    MealSet,
    MealSick,
)
from school_bot.clock import hhmm, today
from school_bot.config import settings
from school_bot.db.models import EntrySource, MealField, SchoolClass, Teacher
from school_bot.domain.meals import (
    get_entry,
    last_known_count,
    upsert_entry,
    was_corrected,
)

log = logging.getLogger(__name__)
router = Router(name="daily")


class ManualEntry(StatesGroup):
    waiting_for_number = State()


def _source_for(teacher: Teacher) -> EntrySource:
    return EntrySource.ADMIN if teacher.is_admin else EntrySource.TEACHER


async def _show(message: Message, text: str, markup, *, edit: bool = True) -> None:
    """Показати наступний крок.

    `edit=False` — надіслати новим повідомленням: так робить ручний ввід, де
    відповідь вчителя вже розірвала ланцюжок власним повідомленням у чаті.

    Ковтаємо «message is not modified»: hhmm() має точність до хвилини, тож
    два тапи по тій самій цифрі за одну хвилину дають байт у байт той самий
    текст, і Telegram відповідає помилкою. Роутера помилок у диспетчері немає,
    тож інакше це був би трейсбек у логах на рівному місці.
    """
    if not edit:
        await message.answer(text, reply_markup=markup)
        return
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _ask_absent(
    message: Message,
    session: AsyncSession,
    *,
    school_class: SchoolClass,
    d: Date,
    edit: bool = True,
) -> None:
    entry = await get_entry(session, school_class.id, d)
    await _show(
        message,
        texts.prompt_absent(school_class.name, d, entry.eating_count),
        keyboards.absent_pad(
            school_class.id, d,
            current=entry.absent_count,
            max_children=settings.max_children,
        ),
        edit=edit,
    )


async def _ask_sick(
    message: Message,
    session: AsyncSession,
    *,
    school_class: SchoolClass,
    d: Date,
    absent: int,
    edit: bool = True,
) -> None:
    entry = await get_entry(session, school_class.id, d)
    await _show(
        message,
        texts.prompt_sick(school_class.name, d, absent),
        keyboards.sick_pad(school_class.id, d, current=entry.sick_count, max_absent=absent),
        edit=edit,
    )


async def _finish(
    message: Message,
    session: AsyncSession,
    *,
    school_class: SchoolClass,
    d: Date,
    edit: bool = True,
) -> None:
    """Підсумок дня з усіма трьома цифрами."""
    entry = await get_entry(session, school_class.id, d)
    await _show(
        message,
        texts.prompt_answered(
            school_class.name, d, entry.eating_count, hhmm(),
            edited=await was_corrected(session, entry.id),
            absent=entry.absent_count,
            sick=entry.sick_count,
        ),
        keyboards.edit_button(school_class.id, d),
        edit=edit,
    )


async def _save_and_confirm(
    query: CallbackQuery,
    session: AsyncSession,
    teacher: Teacher,
    *,
    class_id: int,
    d: Date,
    value: int,
) -> None:
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    _, was_update = await upsert_entry(
        session,
        class_id=class_id,
        d=d,
        eating_count=value,
        teacher_id=teacher.id,
        source=_source_for(teacher),
    )
    await _ask_absent(query.message, session, school_class=school_class, d=d)
    await query.answer(texts.TOAST_SAVED)
    log.info(
        "%s: %s = %s (%s)",
        d, school_class.name, value, "правка" if was_update else "новий запис",
    )


@router.callback_query(MealSet.filter())
async def set_value(
    query: CallbackQuery,
    callback_data: MealSet,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    await state.clear()
    await _save_and_confirm(
        query,
        session,
        teacher,
        class_id=callback_data.class_id,
        d=callback_data.date,
        value=callback_data.value,
    )


@router.callback_query(MealAbsent.filter())
async def set_absent(
    query: CallbackQuery,
    callback_data: MealAbsent,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    """Крок 2. value=None — «Пропустити»: нічого не пишемо, цифра їжі лишається."""
    await state.clear()
    school_class = await session.get(SchoolClass, callback_data.class_id)
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return
    d = callback_data.date

    if await get_entry(session, school_class.id, d) is None:
        # Запис зник між кроками (адмін видалив клас, гонка) — не створюємо
        # порожній запис із самих відсутніх: харчування NOT NULL.
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    if callback_data.value is None:
        await _finish(query.message, session, school_class=school_class, d=d)
        await query.answer(texts.TOAST_SKIPPED)
        return

    # Відсутніх немає — отже й хворих нуль. Питати про це окремо безглуздо, а
    # лишати NULL означало б дірку у звіті там, де відповідь очевидна. Пишемо
    # обидві цифри одним викликом: це одна дія вчителя, а не дві.
    await upsert_entry(
        session,
        class_id=school_class.id,
        d=d,
        absent_count=callback_data.value,
        sick_count=0 if callback_data.value == 0 else None,
        teacher_id=teacher.id,
        source=_source_for(teacher),
    )

    if callback_data.value > 0:
        await _ask_sick(
            query.message, session, school_class=school_class, d=d, absent=callback_data.value
        )
    else:
        await _finish(query.message, session, school_class=school_class, d=d)
    await query.answer(texts.TOAST_SAVED)


@router.callback_query(MealSick.filter())
async def set_sick(
    query: CallbackQuery,
    callback_data: MealSick,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    """Крок 3. Стелю перевіряємо ще раз на сервері, а не лише в клавіатурі."""
    await state.clear()
    school_class = await session.get(SchoolClass, callback_data.class_id)
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return
    d = callback_data.date

    entry = await get_entry(session, school_class.id, d)
    if entry is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    if callback_data.value is None:
        await _finish(query.message, session, school_class=school_class, d=d)
        await query.answer(texts.TOAST_SKIPPED)
        return

    # Кнопки живуть у чаті вічно: вчитель міг відповісти «5 відсутніх», дістати
    # пад 0..5, потім повернутися й зменшити відсутніх до 2. Стара «5» досі
    # натискається, і без цієї перевірки записала б хворих більше за відсутніх.
    if entry.absent_count is not None and callback_data.value > entry.absent_count:
        await query.answer(texts.SICK_EXCEEDS_ABSENT, show_alert=True)
        await _ask_sick(
            query.message, session, school_class=school_class, d=d, absent=entry.absent_count
        )
        return

    await upsert_entry(
        session,
        class_id=school_class.id,
        d=d,
        sick_count=callback_data.value,
        teacher_id=teacher.id,
        source=_source_for(teacher),
    )
    await _finish(query.message, session, school_class=school_class, d=d)
    await query.answer(texts.TOAST_SAVED)


@router.callback_query(MealEdit.filter())
async def edit_value(
    query: CallbackQuery,
    callback_data: MealEdit,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    """Повернути клавіатуру. Вчителю — лише за сьогодні, адміну — за будь-яку дату."""
    await state.clear()
    d = callback_data.date
    school_class = await session.get(SchoolClass, callback_data.class_id)
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    if not teacher.is_admin and d != today():
        await query.answer(
            "Виправляти минулі дні може лише адміністратор.\n"
            "Зверніться до нього — правка буде зафіксована в журналі.",
            show_alert=True,
        )
        return

    existing = await get_entry(session, callback_data.class_id, d)
    hint = existing.eating_count if existing else await last_known_count(
        session, callback_data.class_id, d
    )
    await query.message.edit_text(
        texts.prompt(school_class.name, d),
        reply_markup=keyboards.number_pad(
            callback_data.class_id, d, last_known=hint, max_children=settings.max_children
        ),
    )
    await query.answer()


async def _start_manual(
    query: CallbackQuery, state: FSMContext, *, class_id: int, d: int,
    meal_field: MealField, ask: str,
) -> None:
    """Спільний вхід у ручний ввід. Поле кладемо в стан, а не в callback_data.

    Додати поле в MealManual не можна: у чатах вчителів висять старі кнопки
    цього префікса, і зайвий сегмент зробив би їх нечитними.
    """
    await state.set_state(ManualEntry.waiting_for_number)
    await state.update_data(class_id=class_id, d=d, field=meal_field.value)
    await query.message.answer(ask)
    await query.answer()


@router.callback_query(MealManual.filter())
async def ask_manual(
    query: CallbackQuery, callback_data: MealManual, state: FSMContext
) -> None:
    await _start_manual(
        query, state, class_id=callback_data.class_id, d=callback_data.d,
        meal_field=MealField.EATING, ask=texts.MANUAL_ASK,
    )


@router.callback_query(MealManualAbsent.filter())
async def ask_manual_absent(
    query: CallbackQuery, callback_data: MealManualAbsent, state: FSMContext
) -> None:
    await _start_manual(
        query, state, class_id=callback_data.class_id, d=callback_data.d,
        meal_field=MealField.ABSENT, ask=texts.MANUAL_ASK_ABSENT,
    )


@router.callback_query(MealManualSick.filter())
async def ask_manual_sick(
    query: CallbackQuery, callback_data: MealManualSick, state: FSMContext
) -> None:
    await _start_manual(
        query, state, class_id=callback_data.class_id, d=callback_data.d,
        meal_field=MealField.SICK, ask=texts.MANUAL_ASK_SICK,
    )


@router.message(ManualEntry.waiting_for_number, F.text.regexp(r"^\s*\d{1,3}\s*$"))
async def receive_manual(
    message: Message,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    value = int(message.text.strip())
    if value > settings.max_children:
        await message.answer(texts.manual_invalid(settings.max_children))
        return

    data = await state.get_data()
    await state.clear()

    d = Date.fromordinal(data["d"])
    class_id = data["class_id"]
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        await message.answer(texts.NOTHING_TO_EDIT)
        return

    meal_field = MealField(data.get("field", MealField.EATING.value))
    entry = await get_entry(session, class_id, d)

    if meal_field is MealField.EATING:
        await upsert_entry(
            session, class_id=class_id, d=d, eating_count=value,
            teacher_id=teacher.id, source=_source_for(teacher),
        )
        # Ручний ввід теж веде в ланцюжок: інакше «Інша цифра» була б тихим
        # обхідним шляхом повз питання про відсутніх.
        await _ask_absent(message, session, school_class=school_class, d=d, edit=False)
    elif entry is None:
        await message.answer(texts.NOTHING_TO_EDIT)
        return
    elif meal_field is MealField.ABSENT:
        await upsert_entry(
            session, class_id=class_id, d=d, absent_count=value,
            sick_count=0 if value == 0 else None,
            teacher_id=teacher.id, source=_source_for(teacher),
        )
        if value > 0:
            await _ask_sick(
                message, session, school_class=school_class, d=d, absent=value, edit=False
            )
        else:
            await _finish(message, session, school_class=school_class, d=d, edit=False)
    else:
        # Та сама стеля, що й у клавіатурі: пад показує не всі числа, тож
        # ручний ввід — це другий шлях, а не обхідний.
        if entry.absent_count is not None and value > entry.absent_count:
            await message.answer(texts.SICK_EXCEEDS_ABSENT)
            return
        await upsert_entry(
            session, class_id=class_id, d=d, sick_count=value,
            teacher_id=teacher.id, source=_source_for(teacher),
        )
        await _finish(message, session, school_class=school_class, d=d, edit=False)

    log.info("%s: %s %s = %s (ручний ввід)", d, school_class.name, meal_field.value, value)


@router.message(ManualEntry.waiting_for_number)
async def reject_manual(message: Message) -> None:
    await message.answer(texts.manual_invalid(settings.max_children))
