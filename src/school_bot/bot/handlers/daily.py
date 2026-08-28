"""Обробка відповіді на щоденний запит: тап по цифрі, ручний ввід, виправлення."""

from __future__ import annotations

import logging
from datetime import date as Date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot import keyboards, texts
from school_bot.bot.callbacks import MealEdit, MealManual, MealSet
from school_bot.clock import hhmm, today
from school_bot.config import settings
from school_bot.db.models import EntrySource, SchoolClass, Teacher
from school_bot.domain.meals import get_entry, last_known_count, upsert_entry

log = logging.getLogger(__name__)
router = Router(name="daily")


class ManualEntry(StatesGroup):
    waiting_for_number = State()


def _source_for(teacher: Teacher) -> EntrySource:
    return EntrySource.ADMIN if teacher.is_admin else EntrySource.TEACHER


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
    now = hhmm()
    await query.message.edit_text(
        texts.prompt_answered(school_class.name, d, value, now, edited=was_update),
        reply_markup=keyboards.edit_button(class_id, d),
    )
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


@router.callback_query(MealManual.filter())
async def ask_manual(
    query: CallbackQuery,
    callback_data: MealManual,
    state: FSMContext,
) -> None:
    await state.set_state(ManualEntry.waiting_for_number)
    await state.update_data(
        class_id=callback_data.class_id,
        d=callback_data.d,
        message_id=query.message.message_id,
    )
    await query.message.answer(texts.MANUAL_ASK)
    await query.answer()


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

    _, was_update = await upsert_entry(
        session,
        class_id=class_id,
        d=d,
        eating_count=value,
        teacher_id=teacher.id,
        source=_source_for(teacher),
    )
    now = hhmm()
    await message.answer(
        texts.prompt_answered(school_class.name, d, value, now, edited=was_update),
        reply_markup=keyboards.edit_button(class_id, d),
    )


@router.message(ManualEntry.waiting_for_number)
async def reject_manual(message: Message) -> None:
    await message.answer(texts.manual_invalid(settings.max_children))
