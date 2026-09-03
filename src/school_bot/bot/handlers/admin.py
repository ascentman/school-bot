"""Адмін-меню: зведення, звіти, вчителі, класи, неробочі дні, налаштування."""

from __future__ import annotations

import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.bot import keyboards, texts
from school_bot.bot.callbacks import AdminAction, ClassToggle, MonthPick
from school_bot.bot.filters import IsAdmin
from school_bot.clock import today
from school_bot.config import settings
from school_bot.db.models import (
    UA_DAY_KIND,
    ClassAssignment,
    DayKind,
    NonSchoolDay,
    SchoolClass,
    Teacher,
)
from school_bot.domain.calendar import mark_range, unmark_range
from school_bot.domain.classes import create_classes, parse_date_range, set_teacher_classes
from school_bot.domain.dates import format_date
from school_bot.domain.meals import active_classes, classes_for_teacher, day_summary
from school_bot.domain.phones import format_phone
from school_bot.domain.teachers import clean_name, free_number, import_teachers
from school_bot.reports.matrix import available_months, build_month_matrix
from school_bot.reports.pdf import render_pdf
from school_bot.reports.xlsx import render_xlsx
from school_bot.scheduler.jobs import day_pdf_attachment, sync_all_months

log = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AddTeacher(StatesGroup):
    name = State()
    classes = State()


class AddClass(StatesGroup):
    names = State()


class ImportTeachers(StatesGroup):
    waiting_for_list = State()


class EditTeacherClasses(StatesGroup):
    picking = State()


class DaysOff(StatesGroup):
    range_input = State()


# --- 📊 Сьогодні ----------------------------------------------------------


@router.message(F.text == texts.BTN_TODAY)
async def today_summary(message: Message, session: AsyncSession) -> None:
    d = today()
    summary = await day_summary(session, d)
    if summary.expected == 0:
        await message.answer(texts.NO_ACTIVE_CLASSES)
        return

    missing = [(s.school_class.id, s.school_class.name) for s in summary.missing]
    lines = [
        texts.digest(
            d,
            submitted=len(summary.submitted),
            expected=summary.expected,
            total=summary.total,
            missing=[n for _, n in missing],
        )
    ]
    if summary.submitted:
        lines += ["", "<b>Подано:</b>"]
        lines += [
            f"  {texts.esc(s.school_class.name)} — {s.count}" for s in summary.submitted
        ]

    await message.answer(
        "\n".join(lines),
        reply_markup=keyboards.missing_classes(d, missing) if missing else None,
    )

    # Той самий summary, що й у тексті вище, і та сама безпечна побудова, що в
    # розсилці: збій рендеру має коштувати файл, а не всю відповідь.
    document = day_pdf_attachment(summary)
    if document is not None:
        await message.answer_document(document)


# --- 📅 Звіт за місяць ----------------------------------------------------


@router.message(F.text == texts.BTN_REPORT)
async def report_menu(message: Message, session: AsyncSession) -> None:
    months = await available_months(session)
    if not months:
        await message.answer(texts.NOTHING_TO_REPORT)
        return
    await message.answer(texts.PICK_MONTH, reply_markup=keyboards.month_picker(months))


@router.callback_query(MonthPick.filter())
async def send_report(
    query: CallbackQuery, callback_data: MonthPick, session: AsyncSession
) -> None:
    await query.answer(texts.REPORT_BUILDING)
    matrix = await build_month_matrix(
        session,
        callback_data.year,
        callback_data.month,
        school_name=settings.school_name,
        today=today(),
    )
    stem = f"harchuvannia_{callback_data.year}-{callback_data.month:02d}"
    caption = texts.report_caption(
        matrix.title, matrix.grand_total, len(matrix.elapsed_school_days), matrix.missing_total
    )

    await query.message.answer_document(
        BufferedInputFile(render_xlsx(matrix), filename=f"{stem}.xlsx"), caption=caption
    )
    await query.message.answer_document(
        BufferedInputFile(render_pdf(matrix), filename=f"{stem}.pdf")
    )


# --- 👩‍🏫 Вчителі ----------------------------------------------------------


@router.message(F.text == texts.BTN_TEACHERS)
async def teachers_list(message: Message, session: AsyncSession) -> None:
    rows = list(
        await session.scalars(
            select(Teacher)
            .where(Teacher.is_active.is_(True))
            .options(selectinload(Teacher.assignments).selectinload(ClassAssignment.school_class))
            .order_by(Teacher.full_name)
        )
    )
    lines = ["👩‍🏫 <b>Вчителі</b>", ""]
    waiting = 0
    for t in rows:
        names = [a.school_class.name for a in t.assignments if a.school_class.is_active]
        crown = "👑 " if t.is_admin else ""
        if t.is_linked:
            pending = ""
        else:
            pending = "  ⏳ <i>ще не відкрив бота</i>"
            waiting += 1
        joined = ", ".join(texts.esc(n) for n in names)
        lines.append(f"{crown}<b>{texts.esc(t.full_name)}</b> — {joined or '—'}{pending}")
        if t.phone:
            lines.append(f"    <code>{format_phone(t.phone)}</code>")

    if waiting:
        lines += [
            "",
            f"⏳ Чекають активації: <b>{waiting}</b>. Поки вчитель не відкриє бота, "
            "запити йому не надходять.",
        ]
    lines += [
        "",
        "📥 Завантажити список: /import_teachers",
        "🏫 Змінити класи: /edit_teacher",
        "➕ Додати одного: /add_teacher",
        "🚫 Вимкнути: /off_teacher",
        "📵 Звільнити номер: /free_number",
    ]
    await message.answer("\n".join(lines))


# --- масовий імпорт ---


@router.message(Command("import_teachers"))
async def import_start(message: Message, state: FSMContext) -> None:
    await state.set_state(ImportTeachers.waiting_for_list)
    await message.answer(texts.IMPORT_ASK_LIST)


@router.message(ImportTeachers.waiting_for_list)
async def import_apply(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    result = await import_teachers(session, message.text or "")

    await message.answer(
        texts.import_preview(
            created=len(result.created),
            updated=len(result.updated),
            failed=[p.raw for p in result.failed],
            classes=result.created_classes,
        )
    )
    if result.total_ok:
        me = await message.bot.get_me()
        await message.answer(texts.import_done(result.total_ok, f"https://t.me/{me.username}"))


# --- редагування класів наявного вчителя ---


@router.message(Command("edit_teacher"))
async def edit_teacher_start(message: Message, session: AsyncSession) -> None:
    rows = list(
        await session.scalars(
            select(Teacher).where(Teacher.is_active.is_(True)).order_by(Teacher.full_name)
        )
    )
    if not rows:
        await message.answer(texts.NO_ACTIVE_TEACHERS)
        return
    await message.answer(
        texts.TEACHER_PICK_TO_EDIT,
        reply_markup=keyboards.picker([(t.id, t.full_name) for t in rows], "teacher_edit"),
    )


@router.callback_query(AdminAction.filter(F.action == "teacher_edit"))
async def edit_teacher_pick(
    query: CallbackQuery,
    callback_data: AdminAction,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    teacher = await session.get(Teacher, int(callback_data.arg))
    if teacher is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    classes = await active_classes(session)
    if not classes:
        await query.answer(texts.NO_ACTIVE_CLASSES, show_alert=True)
        return

    current = {c.id for c in await classes_for_teacher(session, teacher.id)}
    await state.set_state(EditTeacherClasses.picking)
    await state.update_data(teacher_id=teacher.id, selected=sorted(current))
    await query.message.edit_text(
        texts.teacher_edit_classes(teacher.full_name),
        reply_markup=keyboards.class_multiselect(
            [(c.id, c.name) for c in classes], current, done_action="classes_saved"
        ),
    )
    await query.answer()


@router.callback_query(
    EditTeacherClasses.picking, AdminAction.filter(F.action == "classes_saved")
)
async def edit_teacher_save(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()

    teacher = await session.get(Teacher, data["teacher_id"])
    if teacher is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return

    await set_teacher_classes(session, teacher.id, set(data.get("selected", [])))
    names = [c.name for c in await classes_for_teacher(session, teacher.id)]
    await query.message.edit_text(texts.teacher_classes_saved(teacher.full_name, names))
    await query.answer(texts.TOAST_STORED)


@router.message(Command("add_teacher"))
async def add_teacher_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddTeacher.name)
    await message.answer(texts.TEACHER_ASK_NAME)


@router.message(AddTeacher.name)
async def add_teacher_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name, why = clean_name(message.text)
    if why:
        await message.answer(texts.name_rejected(why))
        return

    classes = await active_classes(session)
    if not classes:
        await _create_teacher(message, session, state, name, set())
        return

    await state.set_state(AddTeacher.classes)
    await state.update_data(name=name, selected=[])
    await message.answer(
        texts.TEACHER_ASK_CLASSES,
        reply_markup=keyboards.class_multiselect([(c.id, c.name) for c in classes], set()),
    )


@router.callback_query(
    StateFilter(AddTeacher.classes, EditTeacherClasses.picking), ClassToggle.filter()
)
async def toggle_class(
    query: CallbackQuery,
    callback_data: ClassToggle,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Перемкнути клас у виборі. Спільний для створення й редагування вчителя —
    відрізняється лише кнопка «Готово», яку показувати."""
    data = await state.get_data()
    selected = set(data.get("selected", []))
    selected ^= {callback_data.class_id}
    await state.update_data(selected=sorted(selected))

    editing = await state.get_state() == EditTeacherClasses.picking
    done_action = "classes_saved" if editing else "teacher_done"
    classes = await active_classes(session)
    await query.message.edit_reply_markup(
        reply_markup=keyboards.class_multiselect(
            [(c.id, c.name) for c in classes], selected, done_action=done_action
        )
    )
    await query.answer()


@router.callback_query(AddTeacher.classes, AdminAction.filter(F.action == "teacher_done"))
async def add_teacher_done(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    await query.message.edit_reply_markup(reply_markup=None)
    await _create_teacher(
        query.message, session, state, data["name"], set(data.get("selected", []))
    )
    await query.answer()


async def _create_teacher(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    name: str,
    class_ids: set[int],
) -> None:
    await state.clear()
    code = secrets.token_urlsafe(12)
    teacher = Teacher(full_name=name, invite_code=code)
    session.add(teacher)
    await session.flush()

    if class_ids:
        await set_teacher_classes(session, teacher.id, class_ids)

    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start=inv_{code}"
    await message.answer(texts.invite_created(name, link))
    log.info("Створено запрошення для %s", name)


@router.message(Command("off_teacher"))
async def off_teacher_menu(message: Message, session: AsyncSession) -> None:
    rows = list(
        await session.scalars(
            select(Teacher).where(Teacher.is_active.is_(True)).order_by(Teacher.full_name)
        )
    )
    if not rows:
        await message.answer(texts.NO_ACTIVE_TEACHERS)
        return
    await message.answer(
        texts.PICK_TEACHER_TO_DISABLE,
        reply_markup=keyboards.picker([(t.id, t.full_name) for t in rows], "teacher_off"),
    )


@router.callback_query(AdminAction.filter(F.action == "teacher_off"))
async def off_teacher(
    query: CallbackQuery, callback_data: AdminAction, session: AsyncSession
) -> None:
    teacher = await session.get(Teacher, int(callback_data.arg))
    if teacher is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return
    teacher.is_active = False
    await session.flush()
    await query.message.edit_text(texts.teacher_disabled(teacher.full_name))
    await query.answer()


# --- 🏫 Класи -------------------------------------------------------------


@router.message(Command("free_number"))
async def free_number_menu(message: Message, session: AsyncSession) -> None:
    """Звільнити номер вимкненого вчителя для нової людини."""
    rows = list(
        await session.scalars(
            select(Teacher)
            .where(Teacher.is_active.is_(False), Teacher.phone.is_not(None))
            .order_by(Teacher.full_name)
        )
    )
    if not rows:
        await message.answer(texts.NO_DISABLED_TEACHERS)
        return
    await message.answer(
        texts.PICK_TEACHER_TO_FREE,
        reply_markup=keyboards.picker(
            [(t.id, f"{t.full_name} · {format_phone(t.phone)}") for t in rows], "free_number"
        ),
    )


@router.callback_query(AdminAction.filter(F.action == "free_number"))
async def free_number_apply(
    query: CallbackQuery, callback_data: AdminAction, session: AsyncSession
) -> None:
    teacher = await free_number(session, int(callback_data.arg))
    if teacher is None:
        # Або запис зник, або його встигли повернути в дію, поки меню висіло.
        await query.answer(texts.CANNOT_FREE_ACTIVE, show_alert=True)
        return
    await query.message.edit_text(texts.number_freed(teacher.full_name))
    await query.answer()


@router.message(F.text == texts.BTN_CLASSES)
async def classes_list(message: Message, session: AsyncSession) -> None:
    classes = await active_classes(session)
    lines = ["🏫 <b>Класи</b>", ""]
    if classes:
        for c in classes:
            teacher_names = list(
                await session.scalars(
                    select(Teacher.full_name)
                    .join(ClassAssignment, ClassAssignment.teacher_id == Teacher.id)
                    .where(ClassAssignment.class_id == c.id, Teacher.is_active.is_(True))
                )
            )
            warn = "" if teacher_names else "  ⚠️ <i>без класного керівника</i>"
            joined = ", ".join(texts.esc(n) for n in teacher_names)
            lines.append(f"<b>{texts.esc(c.name)}</b> — {joined or '—'}{warn}")
    else:
        lines.append("<i>Поки жодного.</i>")
    lines += ["", "Додати: /add_class", "Прибрати: /off_class"]
    await message.answer("\n".join(lines))


@router.message(Command("add_class"))
async def add_class_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddClass.names)
    await message.answer(texts.CLASS_ASK_NAME)


@router.message(AddClass.names)
async def add_class_finish(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    created, rejected = await create_classes(session, message.text or "")
    await message.answer(texts.classes_added(created, rejected))


@router.message(Command("off_class"))
async def off_class_menu(message: Message, session: AsyncSession) -> None:
    classes = await active_classes(session)
    if not classes:
        await message.answer(texts.NO_ACTIVE_CLASSES)
        return
    await message.answer(
        texts.PICK_CLASS_TO_DISABLE,
        reply_markup=keyboards.picker([(c.id, c.name) for c in classes], "class_off", per_row=4),
    )


@router.callback_query(AdminAction.filter(F.action == "class_off"))
async def off_class(
    query: CallbackQuery, callback_data: AdminAction, session: AsyncSession
) -> None:
    school_class = await session.get(SchoolClass, int(callback_data.arg))
    if school_class is None:
        await query.answer(texts.NOTHING_TO_EDIT, show_alert=True)
        return
    school_class.is_active = False
    await session.flush()
    await query.message.edit_text(texts.class_disabled(school_class.name))
    await query.answer()


# --- 🗓 Неробочі дні ------------------------------------------------------


@router.message(F.text == texts.BTN_DAYS_OFF)
async def days_off_list(message: Message, session: AsyncSession) -> None:
    rows = list(
        await session.scalars(
            select(NonSchoolDay)
            .where(NonSchoolDay.date >= today())
            .order_by(NonSchoolDay.date)
            .limit(30)
        )
    )
    lines = ["🗓 <b>Неробочі дні</b> (найближчі)", ""]
    if rows:
        for r in rows:
            note = f" — {r.note}" if r.note else ""
            lines.append(f"{format_date(r.date)} · {UA_DAY_KIND[r.kind]}{note}")
    else:
        lines.append("<i>Не позначено жодного.</i>")
    lines += ["", "Додати: /days_off", "Прибрати: /days_on"]
    await message.answer("\n".join(lines))


@router.message(Command("days_off", "days_on"))
async def days_off_start(message: Message, state: FSMContext) -> None:
    await state.set_state(DaysOff.range_input)
    await state.update_data(mode="off" if "/days_off" in (message.text or "") else "on")
    await message.answer(texts.DAYS_OFF_ASK_RANGE)


@router.message(DaysOff.range_input)
async def days_off_apply(message: Message, session: AsyncSession, state: FSMContext) -> None:
    parsed = parse_date_range(message.text or "")
    if parsed is None:
        await message.answer(texts.DATE_RANGE_INVALID)
        return

    data = await state.get_data()
    await state.clear()
    start, end = parsed

    if data.get("mode") == "on":
        removed = await unmark_range(session, start, end)
        await message.answer(
            texts.days_off_cleared(removed) if removed else texts.NO_DAYS_OFF_IN_RANGE
        )
        return

    added = await mark_range(session, start, end, DayKind.VACATION, note=None)
    await message.answer(texts.days_off_marked(added, start, end))


# --- ⚙️ Налаштування ------------------------------------------------------


@router.message(F.text == texts.BTN_SETTINGS)
async def settings_view(message: Message, session: AsyncSession) -> None:
    classes = await active_classes(session)
    has_any_teacher = await session.scalar(
        select(Teacher.id).where(Teacher.is_active.is_(True)).limit(1)
    )
    lines = [
        "⚙️ <b>Налаштування</b>",
        "",
        f"Школа: {settings.school_name}",
        f"Часовий пояс: {settings.timezone}",
        f"Запит: <b>{settings.prompt_time:%H:%M}</b> (Пн–Пт)",
        "Нагадування: <b>"
        + ", ".join(f"{t:%H:%M}" for t in settings.remind_times)
        + "</b>",
        f"Зведення адміну: <b>{settings.digest_time:%H:%M}</b>",
        f"Активних класів: {len(classes)}",
        "",
    ]
    if settings.sheets_enabled:
        lines += [
            "📗 Google Sheets: <b>увімкнено</b>",
            texts.sheet_url(settings.google_sheet_id or ""),
            "",
            "Оновити зараз: /sync",
        ]
    else:
        lines.append("📗 Google Sheets: <b>вимкнено</b>")

    if not has_any_teacher:
        lines.append(texts.NO_TEACHERS_HINT)
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("sync"))
async def sync_now(message: Message, session: AsyncSession) -> None:
    if not settings.sheets_enabled:
        await message.answer(texts.SHEETS_DISABLED)
        return

    status = await message.answer(texts.SYNC_IN_PROGRESS)
    synced, total = await sync_all_months(session)
    await status.edit_text(texts.sync_done(synced, total), disable_web_page_preview=True)


@router.callback_query(AdminAction.filter(F.action == "cancel"))
async def cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.edit_text(texts.MANUAL_CANCELLED)
    await query.answer()
