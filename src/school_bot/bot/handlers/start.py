"""Реєстрація по одноразовому інвайт-посиланню."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot import keyboards, texts
from school_bot.bot.callbacks import PickClass, PickDone
from school_bot.bot.commands import set_personal_commands
from school_bot.clock import today
from school_bot.db.models import SchoolClass, Teacher
from school_bot.domain.classes import set_teacher_classes
from school_bot.domain.meals import active_classes, classes_for_teacher, get_entry
from school_bot.domain.teachers import clean_name, register_by_phone

log = logging.getLogger(__name__)
router = Router(name="start")


class SelfRegister(StatesGroup):
    full_name = State()
    classes = State()

INVITE_PREFIX = "inv_"


async def _still_needs_name(
    message: Message, state: FSMContext, teacher: Teacher | None
) -> bool:
    """Чи бот саме зараз чекає ПІБ.

    Точок входу, які інакше завершили б реєстрацію під ніком з Telegram,
    кілька: /start, повторний контакт, інвайт-посилання. Кожна з них має
    спершу спитати про це.
    """
    if await state.get_state() != SelfRegister.full_name:
        return False

    # Доступ вимкнули просто під час реєстрації. Далі просити ПІБ не можна:
    # звичайний текст від вимкненого користувача middleware вже не пропустить,
    # і людина ходитиме по колу — бот просить те, чого сам не прийме.
    if teacher is not None and not teacher.is_active:
        await state.clear()
        await message.answer(texts.ACCOUNT_DISABLED, reply_markup=ReplyKeyboardRemove())
        return True

    await message.answer(texts.ASK_FULL_NAME, reply_markup=ReplyKeyboardRemove())
    return True


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
    if await _still_needs_name(message, state, teacher):
        return

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
    if await _still_needs_name(message, state, teacher):
        return

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
    # Контакт, надісланий повторно, поки бот чекає ПІБ: запис уже створено,
    # тож інакше спрацювало б звичайне привітання — під ніком з Telegram
    # і без жодної згадки, що ПІБ так і не вказане.
    if await _still_needs_name(message, state, teacher):
        return

    await state.clear()

    if teacher is not None and teacher.is_active:
        await _greet(message, session, teacher)
        return

    # Доступ вимкнув адміністратор — повторний контакт не має його повертати,
    # інакше /off_teacher нічого не значить.
    if teacher is not None and not teacher.is_active:
        await message.answer(texts.ACCOUNT_DISABLED, reply_markup=ReplyKeyboardRemove())
        return

    contact = message.contact
    # Telegram дозволяє надіслати чужий контакт із адресної книги — без цієї
    # перевірки будь-хто зміг би зайти під обліковим записом вчителя.
    if contact.user_id != message.from_user.id:
        await message.answer(texts.CONTACT_NOT_YOURS, reply_markup=keyboards.share_contact())
        return

    found, is_new = await register_by_phone(
        session,
        contact.phone_number,
        message.from_user.id,
        fallback_name=message.from_user.full_name,
    )

    if not found.is_active:
        await message.answer(texts.ACCOUNT_DISABLED, reply_markup=ReplyKeyboardRemove())
        return

    if not is_new:
        log.info("Вчитель %s привʼязався за номером", found.full_name)
        await _greet(message, session, found)
        return

    # Номера не було у списку. Імʼя з Telegram часто нік, тож одразу просимо
    # ПІБ: саме воно піде у списки й звіти.
    log.info("Самореєстрація: %s (tg=%s)", found.full_name, message.from_user.id)
    await state.set_state(SelfRegister.full_name)
    await message.answer(texts.ASK_FULL_NAME, reply_markup=ReplyKeyboardRemove())


# [^/\s], а не [^/]: інакше «\s*» бектрекає й " /help" проходить як ПІБ.
@router.message(SelfRegister.classes)
async def skip_class_picking(
    message: Message, session: AsyncSession, teacher: Teacher, state: FSMContext
) -> None:
    """Текст замість натискання кнопки виводить зі стану вибору класів.

    Симетрично до skip_full_name: інакше повідомлення провалюється у
    fallback, стан лишається, і вчитель зависає в ньому до /start.
    """
    await state.clear()
    await message.answer(texts.CLASS_PICKING_STOPPED)
    await _greet(message, session, teacher)


@router.message(Command("name"))
async def change_name_start(message: Message, state: FSMContext) -> None:
    """Змінити власне ПІБ.

    Зареєстровано ДО хендлерів стану: інакше skip_full_name перехоплював
    /name разом з усім іншим, і команда спрацьовувала лише з другого разу.
    """
    await state.set_state(SelfRegister.full_name)
    await message.answer(texts.ASK_NAME_AGAIN)


@router.message(SelfRegister.full_name, F.text.regexp(r"^\s*[^/\s]"))
async def receive_full_name(
    message: Message, session: AsyncSession, teacher: Teacher, state: FSMContext
) -> None:
    name, why = clean_name(message.text)
    if why:
        await message.answer(texts.name_rejected(why))
        return

    teacher.full_name = name
    await session.flush()
    log.info("Вчитель %s назвав ПІБ", name)
    await message.answer(texts.NAME_ACCEPTED)

    # Вчитель, який уже має класи, змінював ПІБ через /name — питати
    # класи вдруге не треба.
    if await classes_for_teacher(session, teacher.id):
        await state.clear()
        await _greet(message, session, teacher)
        return

    await _offer_classes(message, session, teacher, state, texts.ASK_FIRST_CLASS)


async def _offer_classes(
    message: Message,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
    prompt: str,
) -> None:
    """Показати класи, яких у вчителя ще немає."""
    mine = {c.id for c in await classes_for_teacher(session, teacher.id)}
    available = [(c.id, c.name) for c in await active_classes(session) if c.id not in mine]

    if not available:
        await state.clear()
        await message.answer(
            texts.ALL_CLASSES_PICKED if mine else texts.NO_CLASSES_CONFIGURED
        )
        await _greet(message, session, teacher)
        return

    await state.set_state(SelfRegister.classes)
    await message.answer(prompt, reply_markup=keyboards.pick_class(available))


@router.callback_query(SelfRegister.classes, PickClass.filter())
async def picked_class(
    query: CallbackQuery,
    callback_data: PickClass,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    school_class = await session.get(SchoolClass, callback_data.class_id)
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    mine = {c.id for c in await classes_for_teacher(session, teacher.id)}
    await set_teacher_classes(session, teacher.id, mine | {school_class.id})
    chosen = [c.name for c in await classes_for_teacher(session, teacher.id)]
    log.info("Вчитель %s обрав клас %s", teacher.full_name, school_class.name)

    await query.message.edit_text(
        texts.class_picked(school_class.name, chosen), reply_markup=keyboards.pick_more()
    )
    await query.answer()


@router.callback_query(SelfRegister.classes, PickDone.filter())
async def picked_done(
    query: CallbackQuery,
    callback_data: PickDone,
    session: AsyncSession,
    teacher: Teacher,
    state: FSMContext,
) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()

    if callback_data.more:
        await _offer_classes(query.message, session, teacher, state, texts.ASK_NEXT_CLASS)
        return

    await state.clear()
    await _greet(query.message, session, teacher)


@router.message(SelfRegister.full_name)
async def skip_full_name(
    message: Message, state: FSMContext, teacher: Teacher | None
) -> None:
    """Будь-що, крім тексту з ПІБ, виводить зі стану очікування.

    Інакше хендлер стану ковтав команди: /help зберігався як ПІБ, сама
    команда не спрацьовувала, а вийти зі стану було нічим.
    """
    await state.clear()
    current = teacher.full_name if teacher is not None else message.from_user.full_name
    await message.answer(texts.name_postponed(current))


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

