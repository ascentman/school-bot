from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from school_bot.db.models import ClassAssignment, EntrySource, MealEntry, MealEntryAudit
from school_bot.domain.dates import plural_children
from school_bot.domain.meals import (
    classes_for_teacher,
    day_summary,
    last_known_count,
    primary_teacher_ids,
    upsert_entry,
)

D = date(2026, 9, 2)


async def test_insert_creates_entry_and_audit(session, classes, teacher):
    entry, updated = await upsert_entry(
        session, class_id=classes[0].id, d=D, eating_count=24, teacher_id=teacher.id
    )
    assert not updated
    assert entry.eating_count == 24

    audits = list(await session.scalars(select(MealEntryAudit)))
    assert len(audits) == 1
    assert audits[0].old_value is None
    assert audits[0].new_value == 24


async def test_repeat_same_value_does_not_duplicate_audit(session, classes, teacher):
    for _ in range(3):
        await upsert_entry(
            session, class_id=classes[0].id, d=D, eating_count=24, teacher_id=teacher.id
        )
    assert await session.scalar(select(func.count()).select_from(MealEntry)) == 1
    assert await session.scalar(select(func.count()).select_from(MealEntryAudit)) == 1


async def test_correction_updates_in_place_and_logs(session, classes, teacher):
    await upsert_entry(session, class_id=classes[0].id, d=D, eating_count=24, teacher_id=teacher.id)
    entry, updated = await upsert_entry(
        session,
        class_id=classes[0].id,
        d=D,
        eating_count=26,
        teacher_id=teacher.id,
        source=EntrySource.ADMIN,
        reason="перерахували після 2 уроку",
    )
    assert updated
    assert entry.eating_count == 26
    assert await session.scalar(select(func.count()).select_from(MealEntry)) == 1

    audits = list(await session.scalars(select(MealEntryAudit).order_by(MealEntryAudit.id)))
    assert len(audits) == 2
    assert (audits[1].old_value, audits[1].new_value) == (24, 26)
    assert audits[1].reason == "перерахували після 2 уроку"


async def test_unique_per_class_per_date(session, classes, teacher):
    await upsert_entry(session, class_id=classes[0].id, d=D, eating_count=24, teacher_id=teacher.id)
    await upsert_entry(session, class_id=classes[1].id, d=D, eating_count=18, teacher_id=teacher.id)
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 3), eating_count=25, teacher_id=teacher.id
    )
    assert await session.scalar(select(func.count()).select_from(MealEntry)) == 3


async def test_zero_is_a_valid_answer(session, classes, teacher):
    entry, _ = await upsert_entry(
        session, class_id=classes[0].id, d=D, eating_count=0, teacher_id=teacher.id
    )
    assert entry.eating_count == 0
    summary = await day_summary(session, D)
    assert summary.statuses[0].submitted   # 0 — це відповідь, а не її відсутність
    assert summary.total == 0


async def test_day_summary_counts_missing(session, classes, teacher):
    await upsert_entry(session, class_id=classes[0].id, d=D, eating_count=24, teacher_id=teacher.id)
    await upsert_entry(session, class_id=classes[1].id, d=D, eating_count=18, teacher_id=teacher.id)

    summary = await day_summary(session, D)
    assert summary.expected == 3
    assert len(summary.submitted) == 2
    assert [s.school_class.name for s in summary.missing] == ["5-В"]
    assert summary.total == 42


async def test_day_summary_empty_day(session, classes):
    summary = await day_summary(session, D)
    assert summary.total == 0
    assert len(summary.missing) == 3


async def test_last_known_count_uses_previous_school_day(session, classes, teacher):
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 4), eating_count=24, teacher_id=teacher.id
    )
    # понеділок 07.09 → має підтягнути пʼятницю 04.09
    assert await last_known_count(session, classes[0].id, date(2026, 9, 7)) == 24


async def test_last_known_count_falls_back_to_latest(session, classes, teacher):
    # запис давній, попереднього навчального дня в базі немає
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=21, teacher_id=teacher.id
    )
    assert await last_known_count(session, classes[0].id, date(2026, 9, 30)) == 21


async def test_last_known_count_none_for_new_class(session, classes):
    assert await last_known_count(session, classes[0].id, D) is None


async def test_teacher_class_binding(session, classes, teacher):
    session.add_all([
        ClassAssignment(class_id=classes[0].id, teacher_id=teacher.id, is_primary=True),
        ClassAssignment(class_id=classes[2].id, teacher_id=teacher.id, is_primary=True),
    ])
    await session.flush()

    mine = await classes_for_teacher(session, teacher.id)
    assert [c.name for c in mine] == ["1-А", "5-В"]
    assert await primary_teacher_ids(session, classes[0].id) == [teacher.id]
    assert await primary_teacher_ids(session, classes[1].id) == []


async def test_inactive_class_excluded(session, classes, teacher):
    session.add(ClassAssignment(class_id=classes[0].id, teacher_id=teacher.id, is_primary=True))
    classes[0].is_active = False
    await session.flush()
    assert await classes_for_teacher(session, teacher.id) == []


def test_plural_children():
    assert plural_children(1) == "1 дитина"
    assert plural_children(2) == "2 дитини"
    assert plural_children(5) == "5 дітей"
    assert plural_children(11) == "11 дітей"
    assert plural_children(21) == "21 дитина"
    assert plural_children(24) == "24 дитини"
    assert plural_children(0) == "0 дітей"


# --- розбір назв класів і діапазонів дат ---------------------------------


def test_parse_class_name_variants():
    from school_bot.domain.classes import parse_class_name

    assert parse_class_name("3-Б") == ("3-Б", 3, "Б")
    assert parse_class_name("3б") == ("3-Б", 3, "Б")
    assert parse_class_name(" 10 А ") == ("10-А", 10, "А")
    assert parse_class_name("7") == ("7", 7, "")
    assert parse_class_name("13-А") is None
    assert parse_class_name("абв") is None
    assert parse_class_name("") is None


def test_parse_date_range():
    from school_bot.domain.classes import parse_date_range

    assert parse_date_range("28.10.2026 - 03.11.2026") == (date(2026, 10, 28), date(2026, 11, 3))
    assert parse_date_range("28.10.2026") == (date(2026, 10, 28), date(2026, 10, 28))
    assert parse_date_range("03.11.2026 - 28.10.2026") == (date(2026, 10, 28), date(2026, 11, 3))
    assert parse_date_range("не дата") is None
    assert parse_date_range("32.13.2026") is None


async def test_create_classes(session):
    from school_bot.domain.classes import create_classes

    created, rejected = await create_classes(session, "1-А, 1-Б, 2а, хтозна")
    assert created == ["1-А", "1-Б", "2-А"]
    assert rejected == ["хтозна"]

    created2, rejected2 = await create_classes(session, "1-А")
    assert created2 == []
    assert rejected2 == ["1-А (вже є)"]


async def test_create_classes_sort_order(session):
    from school_bot.domain.classes import create_classes
    from school_bot.domain.meals import active_classes

    await create_classes(session, "10-А, 2-Б, 2-А, 1-В")
    assert [c.name for c in await active_classes(session)] == ["1-В", "2-А", "2-Б", "10-А"]


async def test_set_teacher_classes_replaces(session, classes, teacher):
    from school_bot.domain.classes import set_teacher_classes
    from school_bot.domain.meals import classes_for_teacher

    await set_teacher_classes(session, teacher.id, {classes[0].id, classes[1].id})
    assert [c.name for c in await classes_for_teacher(session, teacher.id)] == ["1-А", "3-Б"]

    await set_teacher_classes(session, teacher.id, {classes[2].id})
    assert [c.name for c in await classes_for_teacher(session, teacher.id)] == ["5-В"]

    await set_teacher_classes(session, teacher.id, set())
    assert await classes_for_teacher(session, teacher.id) == []


async def test_unrecognised_classes_from_config_are_reported(session, caplog):
    """Одрук у SCHOOL_CLASSES не має зникати мовчки."""
    import logging

    from school_bot.domain.classes import ensure_classes

    with caplog.at_level(logging.WARNING):
        created = await ensure_classes(session, ["1-А", "13-Я", "хтозна"])

    assert created == ["1-А"]
    assert "13-Я" in caplog.text and "хтозна" in caplog.text


async def test_existing_classes_are_not_reported_as_errors(session, caplog):
    """Повторний старт із тим самим списком не має сипати попередженнями."""
    import logging

    from school_bot.domain.classes import ensure_classes

    await ensure_classes(session, ["1-А", "2-Б"])
    with caplog.at_level(logging.WARNING):
        assert await ensure_classes(session, ["1-А", "2-Б"]) == []
    assert "не розпізнано" not in caplog.text
