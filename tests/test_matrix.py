from __future__ import annotations

from datetime import date

from school_bot.db.models import DayKind
from school_bot.domain.calendar import mark_range
from school_bot.domain.meals import upsert_entry
from school_bot.reports.matrix import available_months, build_month_matrix


async def test_matrix_shapes_september(session, classes, teacher):
    m = await build_month_matrix(session, 2026, 9, school_name="Тест")
    assert len(m.columns) == 30
    assert len(m.school_days) == 22
    assert len(m.rows) == 3
    assert m.title == "вересень 2026"


async def test_matrix_31_day_month(session, classes):
    m = await build_month_matrix(session, 2026, 10)
    assert len(m.columns) == 31


async def test_matrix_february_leap(session, classes):
    m = await build_month_matrix(session, 2028, 2)
    assert len(m.columns) == 29


async def test_matrix_december_crosses_year(session, classes):
    m = await build_month_matrix(session, 2026, 12)
    assert len(m.columns) == 31
    assert m.columns[-1].date == date(2026, 12, 31)


async def test_matrix_totals(session, classes, teacher):
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24, teacher_id=teacher.id
    )
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 2), eating_count=22, teacher_id=teacher.id
    )
    await upsert_entry(
        session, class_id=classes[1].id, d=date(2026, 9, 1), eating_count=18, teacher_id=teacher.id
    )

    m = await build_month_matrix(session, 2026, 9)
    row = next(r for r in m.rows if r.name == "1-А")
    assert row.value(date(2026, 9, 1)) == 24
    assert row.total == 46
    assert m.day_total(date(2026, 9, 1)) == 42
    assert m.grand_total == 64
    # день без жодного запису — None, а не 0
    assert m.day_total(date(2026, 9, 3)) is None
    assert not m.has_data(date(2026, 9, 3))


async def test_vacation_days_marked_and_excluded(session, classes):
    await mark_range(session, date(2026, 10, 28), date(2026, 10, 30), DayKind.VACATION)
    m = await build_month_matrix(session, 2026, 10)

    col = next(c for c in m.columns if c.date == date(2026, 10, 28))
    assert not col.is_school_day
    assert col.off_marker == "кн"
    # жовтень 2026: 22 будні мінус 3 дні канікул
    assert len(m.school_days) == 19


async def test_weekend_columns_are_not_school_days(session, classes):
    m = await build_month_matrix(session, 2026, 9)
    sat = next(c for c in m.columns if c.date == date(2026, 9, 5))
    assert sat.is_weekend and not sat.is_school_day
    assert sat.off_marker == ""


async def test_missing_days_detection(session, classes, teacher):
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24, teacher_id=teacher.id
    )
    m = await build_month_matrix(session, 2026, 9)
    row = next(r for r in m.rows if r.name == "1-А")
    missing = row.missing_days(m.columns)
    assert date(2026, 9, 1) not in missing
    assert date(2026, 9, 2) in missing
    assert date(2026, 9, 5) not in missing   # субота не рахується пропуском
    assert len(missing) == 21


async def test_class_without_any_entry(session, classes):
    m = await build_month_matrix(session, 2026, 9)
    row = next(r for r in m.rows if r.name == "5-В")
    assert row.total == 0
    assert len(row.missing_days(m.columns)) == 22
    assert m.missing_total == 66


async def test_available_months(session, classes, teacher):
    for d in (date(2026, 9, 1), date(2026, 10, 1), date(2026, 10, 2)):
        await upsert_entry(
            session, class_id=classes[0].id, d=d, eating_count=20, teacher_id=teacher.id
        )
    assert await available_months(session) == [(2026, 10), (2026, 9)]


async def test_available_months_empty(session, classes):
    assert await available_months(session) == []


async def test_future_days_are_not_gaps(session, classes, teacher):
    """Майбутній навчальний день — не пропуск, а просто ще не настав."""
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24, teacher_id=teacher.id
    )
    m = await build_month_matrix(session, 2026, 9, today=date(2026, 9, 3))
    row = next(r for r in m.rows if r.name == "1-А")

    missing = row.missing_days(m.columns, m.today)
    assert missing == [date(2026, 9, 2), date(2026, 9, 3)]
    assert len(m.elapsed_school_days) == 3

    col_future = next(c for c in m.columns if c.date == date(2026, 9, 4))
    col_past = next(c for c in m.columns if c.date == date(2026, 9, 2))
    assert not m.is_gap(row, col_future)
    assert m.is_gap(row, col_past)
    assert m.is_future(date(2026, 9, 4))


async def test_missing_total_respects_today(session, classes, teacher):
    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24, teacher_id=teacher.id
    )
    m = await build_month_matrix(session, 2026, 9, today=date(2026, 9, 2))
    # 3 класи × 2 навч. дні, що минули = 6 клітинок, з них 1 заповнена
    assert m.missing_total == 5


async def test_without_today_all_school_days_count(session, classes):
    m = await build_month_matrix(session, 2026, 9)
    assert m.missing_total == 66      # 3 класи × 22 дні
    assert len(m.elapsed_school_days) == 22


# --- три метрики ------------------------------------------------------------


async def test_matrix_projects_the_requested_metric(session, classes):
    from school_bot.db.models import MealField
    from school_bot.reports.matrix import build_month_matrix

    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24,
        absent_count=3, sick_count=1, teacher_id=None,
    )

    eating = await build_month_matrix(session, 2026, 9)
    absent = await build_month_matrix(session, 2026, 9, metric=MealField.ABSENT)
    sick = await build_month_matrix(session, 2026, 9, metric=MealField.SICK)

    assert eating.rows[0].value(date(2026, 9, 1)) == 24
    assert absent.rows[0].value(date(2026, 9, 1)) == 3
    assert sick.rows[0].value(date(2026, 9, 1)) == 1
    assert (eating.grand_total, absent.grand_total, sick.grand_total) == (24, 3, 1)


async def test_a_skipped_metric_is_not_a_gap(session, classes):
    """Клас подав харчування й пропустив відсутніх — це не пропущений день.

    Інакше аркуш «Відсутні» був би суцільно червоний: усі дні до появи цієї
    фічі й кожен пропуск вчителя рахувалися б дірами.
    """
    from school_bot.db.models import MealField
    from school_bot.reports.matrix import build_month_matrix

    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24,
        teacher_id=None,
    )
    m = await build_month_matrix(
        session, 2026, 9, metric=MealField.ABSENT, today=date(2026, 9, 1)
    )
    col = next(c for c in m.columns if c.date == date(2026, 9, 1))

    assert m.rows[0].value(date(2026, 9, 1)) is None    # цифри немає
    assert not m.is_gap(m.rows[0], col)                 # але дірою це не є


async def test_a_day_without_any_entry_is_still_a_gap(session, classes):
    """А ось справжній пропуск лишається червоним на всіх трьох аркушах."""
    from school_bot.db.models import MealField
    from school_bot.reports.matrix import build_month_matrix

    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24,
        teacher_id=None,
    )
    m = await build_month_matrix(
        session, 2026, 9, metric=MealField.ABSENT, today=date(2026, 9, 2)
    )
    col = next(c for c in m.columns if c.date == date(2026, 9, 2))
    assert m.is_gap(m.rows[0], col)


async def test_eating_heading_did_not_change(session, classes):
    """Основна таблиця має виглядати так само, як до появи двох інших."""
    from school_bot.reports.matrix import build_month_matrix

    m = await build_month_matrix(session, 2026, 9, school_name="Ліцей №1")
    assert m.heading == "Облік харчування учнів — вересень 2026"


async def test_build_month_matrices_returns_eating_first(session, classes):
    from school_bot.db.models import MealField
    from school_bot.reports.matrix import build_month_matrices

    ms = await build_month_matrices(session, 2026, 9)
    assert [m.metric for m in ms] == [MealField.EATING, MealField.ABSENT, MealField.SICK]


async def test_has_any_data_marks_empty_metrics(session, classes):
    """Порожню метрику не варто вивантажувати окремою вкладкою.

    Уся історія до появи фічі не має ані відсутніх, ані хворих — інакше
    щоночі перебудовувалися б десятки порожніх вкладок.
    """
    from school_bot.db.models import MealField
    from school_bot.reports.matrix import build_month_matrices

    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), eating_count=24,
        teacher_id=None,
    )
    eating, absent, sick = await build_month_matrices(session, 2026, 9)

    assert eating.has_any_data
    assert not absent.has_any_data
    assert not sick.has_any_data

    await upsert_entry(
        session, class_id=classes[0].id, d=date(2026, 9, 1), absent_count=2,
        teacher_id=None,
    )
    _, absent, sick = await build_month_matrices(session, 2026, 9)
    assert absent.has_any_data
    assert not sick.has_any_data
    assert MealField.ABSENT is absent.metric
