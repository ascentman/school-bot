"""Реєстрація по одноразовому інвайт-посиланню."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot import keyboards, texts
from school_bot.bot.commands import set_personal_commands
from school_bot.clock import today
from school_bot.db.models import Teacher
from school_bot.domain.meals import classes_for_teacher, get_entry
from school_bot.domain.teachers import link_by_phone

log = logging.getLogger(__name__)
router = Router(name="start")

INVITE_PREFIX = "inv_"


async def _greet(message: Message, session: AsyncSession, teacher: Teacher) -> None:
    # Меню команд виставляється персонально: вчитель не має бачити адмінські
    # пункти, які для нього все одно не спрацюють.
    try:
        await set_personal_commands(message.bot, message.chat.id, is_admin=teacher.is_admin)
    except Exception:
        log.warning("Не вдалося оновити меню команд", exc_info=True)

    classes = await classes_for_teacher(session, teacher.id)
    await message.answer(
        texts.welcome(teacher.full_name, [c.name for c in classes], teacher.is_admin),
        reply_markup=keyboards.main_menu(is_admin=teacher.is_admin),
    )


@router.message(CommandStart(deep_link=True))
async def start_with_invite(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    teacher: Teacher | None,
    state: FSMContext,
) -> None:
    await state.clear()
    payload = (command.args or "").strip()

    if teacher is not None and teacher.is_linked:
        await message.answer(texts.INVITE_ALREADY_LINKED)
        await _greet(message, session, teacher)
        return

    if not payload.startswith(INVITE_PREFIX):
        await message.answer(texts.INVITE_INVALID)
        return

    code = payload[len(INVITE_PREFIX) :]
    invited = await session.scalar(
        select(Teacher).where(Teacher.invite_code == code, Teacher.tg_user_id.is_(None))
    )
    if invited is None:
        await message.answer(texts.INVITE_INVALID)
        return

    invited.tg_user_id = message.from_user.id
    invited.invite_code = None  # одноразове
    await session.flush()
    log.info("Вчитель %s (%s) звʼязав акаунт", invited.full_name, message.from_user.id)

    await _greet(message, session, invited)


@router.message(CommandStart())
async def start_plain(
    message: Message,
    session: AsyncSession,
    teacher: Teacher | None,
    state: FSMContext,
) -> None:
    await state.clear()
    if teacher is not None and teacher.is_active:
        await _greet(message, session, teacher)
        return

    # Невідомого користувача просимо поділитися номером: якщо адміністратор уже
    # завантажив список працівників, бот знайде людину сам — персональне
    # запрошення для кожного стає непотрібним.
    await message.answer(texts.ASK_CONTACT, reply_markup=keyboards.share_contact())


@router.message(F.contact)
async def receive_contact(
    message: Message,
    session: AsyncSession,
    teacher: Teacher | None,
    state: FSMContext,
) -> None:
    """Привʼязати акаунт за номером із надісланого контакту."""
    await state.clear()

    if teacher is not None and teacher.is_active:
        await _greet(message, session, teacher)
        return

    contact = message.contact
    # Telegram дозволяє надіслати чужий контакт із адресної книги — без цієї
    # перевірки будь-хто зміг би зайти під обліковим записом вчителя.
    if contact.user_id != message.from_user.id:
        await message.answer(texts.CONTACT_NOT_YOURS, reply_markup=keyboards.share_contact())
        return

    found = await link_by_phone(session, contact.phone_number, message.from_user.id)
    if found is None:
        log.info("Невідомий номер при реєстрації: tg_id=%s", message.from_user.id)
        await message.answer(texts.CONTACT_NOT_FOUND, reply_markup=ReplyKeyboardRemove())
        return

    log.info("Вчитель %s привʼязався за номером", found.full_name)
    await _greet(message, session, found)


@router.message(F.text == texts.BTN_MY_CLASSES)
@router.message(Command("today"))
async def my_classes(
    message: Message, session: AsyncSession, teacher: Teacher, state: FSMContext
) -> None:
    """Класи вчителя з можливістю ввести дані без початкового запиту.

    Потрібно тому, що інакше єдиний шлях подати цифру — кнопки в ранковому
    повідомленні. Видалив його або не помітив — і ввести нічим.
    """
    await state.clear()
    classes = await classes_for_teacher(session, teacher.id)
    if not classes:
        await message.answer(texts.NO_CLASSES_ASSIGNED)
        return

    d = today()
    rows = []
    for school_class in classes:
        entry = await get_entry(session, school_class.id, d)
        rows.append((school_class.id, school_class.name, entry.eating_count if entry else None))

    await message.answer(
        texts.my_classes_header(d),
        reply_markup=keyboards.my_classes(d, rows),
    )


@router.message(Command("help"))
async def help_command(message: Message, teacher: Teacher) -> None:
    await message.answer(texts.TEACHER_HELP)

