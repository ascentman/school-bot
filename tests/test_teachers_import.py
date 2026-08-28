from __future__ import annotations

from sqlalchemy import func, select

from school_bot.db.models import Teacher
from school_bot.domain.meals import classes_for_teacher
from school_bot.domain.teachers import import_teachers, link_by_phone, parse_teacher_list

# --- розбір списку --------------------------------------------------------


def test_parses_comma_separated():
    (p,) = parse_teacher_list("Коваленко Марія Іванівна, 0671234567, 1-А")
    assert p.ok
    assert p.name == "Коваленко Марія Іванівна"
    assert p.phone == "380671234567"
    assert p.class_names == ["1-А"]


def test_column_order_does_not_matter():
    a = parse_teacher_list("Мельник Ігор, 0631112233, 2-Б")[0]
    b = parse_teacher_list("0631112233, Мельник Ігор, 2-Б")[0]
    c = parse_teacher_list("2-Б; 0631112233; Мельник Ігор")[0]
    assert a.name == b.name == c.name == "Мельник Ігор"
    assert a.phone == b.phone == c.phone == "380631112233"
    assert a.class_names == b.class_names == c.class_names == ["2-Б"]


def test_handles_tabs_and_semicolons():
    (p,) = parse_teacher_list("Бондаренко Ольга\t068 555 44 33\t4-А")
    assert p.ok and p.phone == "380685554433" and p.class_names == ["4-А"]


def test_handles_line_without_separators():
    """«Ткаченко Наталія 0501234567 7-В» — цифра класу не має злитися з номером."""
    (p,) = parse_teacher_list("Ткаченко Наталія 0501234567 7-В")
    assert p.name == "Ткаченко Наталія"
    assert p.phone == "380501234567"
    assert p.class_names == ["7-В"]


def test_multiple_classes():
    (p,) = parse_teacher_list("Шевчук Оксана; +380509876543; 3-Б; 5-В")
    assert p.class_names == ["3-Б", "5-В"]


def test_teacher_without_classes_is_valid():
    (p,) = parse_teacher_list("Мельник Ігор, 0631112233")
    assert p.ok and p.class_names == []


def test_reports_missing_phone():
    (p,) = parse_teacher_list("Просто Імʼя Без Номера")
    assert not p.ok and p.error == "не знайдено номера"


def test_reports_missing_name():
    (p,) = parse_teacher_list("0991234567")
    assert not p.ok and p.error == "не знайдено імені"


def test_blank_lines_ignored():
    assert len(parse_teacher_list("\n\n  \nМельник Ігор, 0631112233\n\n")) == 1


# --- імпорт ---------------------------------------------------------------

LIST = """Коваленко Марія Іванівна, 0671234567, 1-А
Шевчук Оксана Петрівна, 0509876543, 3-Б, 5-В
Мельник Ігор, 0631112233
поганий рядок без нічого"""


async def test_import_creates_teachers_and_classes(session):
    result = await import_teachers(session, LIST)

    assert len(result.created) == 3
    assert len(result.failed) == 1
    assert sorted(result.created_classes) == ["1-А", "3-Б", "5-В"]

    maria = await session.scalar(select(Teacher).where(Teacher.phone == "380671234567"))
    assert maria.full_name == "Коваленко Марія Іванівна"
    assert [c.name for c in await classes_for_teacher(session, maria.id)] == ["1-А"]

    oksana = await session.scalar(select(Teacher).where(Teacher.phone == "380509876543"))
    assert [c.name for c in await classes_for_teacher(session, oksana.id)] == ["3-Б", "5-В"]


async def test_reimport_updates_instead_of_duplicating(session):
    await import_teachers(session, LIST)
    result = await import_teachers(session, LIST)

    assert len(result.created) == 0
    assert len(result.updated) == 3
    assert await session.scalar(select(func.count()).select_from(Teacher)) == 3


async def test_reimport_applies_changes(session):
    await import_teachers(session, "Коваленко Марія, 0671234567, 1-А")
    await import_teachers(session, "Коваленко Марія Іванівна, 0671234567, 2-Б")

    t = await session.scalar(select(Teacher).where(Teacher.phone == "380671234567"))
    assert t.full_name == "Коваленко Марія Іванівна"
    assert [c.name for c in await classes_for_teacher(session, t.id)] == ["2-Б"]


async def test_same_number_written_differently_is_one_teacher(session):
    await import_teachers(session, "Коваленко Марія, 0671234567, 1-А")
    await import_teachers(session, "Коваленко Марія, +38 (067) 123-45-67, 1-А")
    assert await session.scalar(select(func.count()).select_from(Teacher)) == 1


# --- привʼязка за номером -------------------------------------------------


async def test_link_by_phone(session):
    await import_teachers(session, LIST)

    teacher = await link_by_phone(session, "+380671234567", tg_user_id=555)
    assert teacher is not None
    assert teacher.full_name == "Коваленко Марія Іванівна"
    assert teacher.tg_user_id == 555


async def test_link_accepts_any_phone_format(session):
    await import_teachers(session, LIST)
    assert await link_by_phone(session, "0671234567", 555) is not None


async def test_link_unknown_phone_returns_none(session):
    await import_teachers(session, LIST)
    assert await link_by_phone(session, "0991110000", 555) is None


async def test_link_is_idempotent_for_same_user(session):
    await import_teachers(session, LIST)
    assert await link_by_phone(session, "0671234567", 555) is not None
    assert await link_by_phone(session, "0671234567", 555) is not None


async def test_link_refuses_to_hijack_another_account(session):
    """Чужий номер не має віддавати доступ до чужого запису."""
    await import_teachers(session, LIST)
    await link_by_phone(session, "0671234567", 555)
    assert await link_by_phone(session, "0671234567", 999) is None


async def test_link_ignores_deactivated_teacher(session):
    await import_teachers(session, LIST)
    t = await session.scalar(select(Teacher).where(Teacher.phone == "380671234567"))
    t.is_active = False
    await session.flush()
    assert await link_by_phone(session, "0671234567", 555) is None
