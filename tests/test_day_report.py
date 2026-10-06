"""Щоденний звіт про відсутніх і хворих: склад рядків і PDF.

Звіт читають на місці — медсестра й класні керівники, — тому найдорожчі
помилки тут не падіння, а тихі: клас, що зник із переліку, або сума, яка не
сходиться з рядками. Саме їх і ловлять ці тести.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.db.models import MealEntry, SchoolClass
from school_bot.reports.day import (
    build_day_report,
    build_report,
    day_report_filename,
)
from school_bot.reports.pdf import render_day_report

DAY = date(2026, 9, 3)


# --- склад звіту -----------------------------------------------------------


async def _with_absences(session: AsyncSession, rows: dict[str, tuple[int, int | None]]) -> None:
    for name, (absent, sick) in rows.items():
        row = await session.scalar(select(SchoolClass).where(SchoolClass.name == name))
        session.add(MealEntry(class_id=row.id, date=DAY, absent_count=absent, sick_count=sick))
    await session.flush()


@pytest.mark.asyncio
async def test_classes_keep_the_usual_order(session, classes):
    """Порядок рядків — той самий, що й усюди: клас, паралель, літера."""
    await _with_absences(session, {"5-В": (1, 1), "1-А": (3, 2), "3-Б": (0, 0)})
    report = await build_day_report(session, DAY)

    assert [c.name for c in report.cells] == ["1-А", "3-Б", "5-В"]
    assert report.expected == 3


@pytest.mark.asyncio
async def test_totals_sum_the_rows(session, classes):
    """Підсумок не має суперечити рядкам, з яких він складений."""
    await _with_absences(session, {"1-А": (3, 2), "3-Б": (1, 0), "5-В": (0, 0)})
    report = await build_day_report(session, DAY)

    assert report.absent_total == sum(c.absent for c in report.cells)
    assert report.sick_total == sum(c.sick for c in report.cells)
    assert (report.absent_total, report.sick_total) == (4, 2)


@pytest.mark.asyncio
async def test_missing_class_is_not_counted_as_zero(session, classes):
    """Пропуск і справжній нуль — різні речі, і у звіті вони різні."""
    await _with_absences(session, {"1-А": (0, 0)})
    report = await build_day_report(session, DAY)

    cells = {c.name: c for c in report.cells}
    assert cells["1-А"].absent == 0 and cells["1-А"].submitted
    assert cells["3-Б"].absent is None and not cells["3-Б"].submitted
    assert report.submitted == 1
    assert report.missing == ["3-Б", "5-В"]


def test_report_builds_from_a_snapshot_without_touching_the_database():
    """`build_report` — чиста функція над готовим DaySummary.

    Саме завдяки цьому текст зведення і PDF будуються з одного знімка: той,
    хто вже має DaySummary, не мусить перечитувати день.
    """
    from school_bot.domain.meals import ClassDayStatus, DaySummary

    statuses = [
        ClassDayStatus(school_class=SchoolClass(name="1-А", grade=1, letter="А"),
                       entry=MealEntry(date=DAY, absent_count=3, sick_count=1)),
        ClassDayStatus(school_class=SchoolClass(name="3-Б", grade=3, letter="Б"), entry=None),
    ]
    report = build_report(DaySummary(date=DAY, statuses=statuses))

    assert report.date == DAY
    assert (report.absent_total, report.sick_total) == (3, 1)
    assert report.missing == ["3-Б"]
    assert [c.name for c in report.cells] == ["1-А", "3-Б"]


@pytest.mark.asyncio
async def test_day_report_carries_absent_and_sick(session, classes):
    from school_bot.domain.meals import upsert_entry

    await upsert_entry(
        session, class_id=classes[0].id, d=DAY,
        absent_count=3, sick_count=1, teacher_id=None,
    )
    report = await build_day_report(session, DAY)

    cell = next(c for c in report.cells if c.name == "1-А")
    assert (cell.absent, cell.sick) == (3, 1)
    assert (report.absent_total, report.sick_total) == (3, 1)


@pytest.mark.asyncio
async def test_skipping_the_sick_question_does_not_make_a_class_missing(session, classes):
    """Клас подав відсутніх і пропустив хворих — він НЕ боржник."""
    from school_bot.domain.meals import upsert_entry

    await upsert_entry(
        session, class_id=classes[0].id, d=DAY, absent_count=3, teacher_id=None
    )
    report = await build_day_report(session, DAY)

    cell = next(c for c in report.cells if c.name == "1-А")
    assert cell.submitted is True
    assert cell.absent == 3
    assert cell.sick is None
    assert "1-А" not in report.missing


@pytest.mark.asyncio
async def test_totals_stay_empty_when_nobody_reported_anything(session, classes):
    """Порожньо — не нуль: день без жодної цифри не стверджує, що всі на місці."""
    report = await build_day_report(session, DAY)
    assert report.absent_total is None
    assert report.sick_total is None


# --- PDF -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_renders_for_a_real_day(session, classes):
    await _with_absences(session, {"1-А": (3, 2), "3-Б": (1, 0)})
    report = await build_day_report(session, DAY, school_name="44 Школа")
    pdf = render_day_report(report)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


@pytest.mark.asyncio
async def test_pdf_renders_when_nobody_submitted_anything(session, classes):
    """День без жодної цифри — звичайний стан о 09:35, не привід падати."""
    report = await build_day_report(session, DAY)
    assert render_day_report(report).startswith(b"%PDF")


def test_filename_carries_the_date():
    assert day_report_filename(DAY) == "vidsutni_2026-09-03.pdf"


# --- звіт завжди на одному аркуші ------------------------------------------


def _pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def _report_with(n_classes: int) -> object:
    from school_bot.domain.meals import ClassDayStatus, DaySummary

    statuses = [
        ClassDayStatus(
            school_class=SchoolClass(name=f"{i // 3 + 1}-{'АБВ'[i % 3]}",
                                     grade=i // 3 + 1, letter="АБВ"[i % 3]),
            entry=MealEntry(date=DAY, absent_count=i % 9, sick_count=i % 4),
        )
        for i in range(n_classes)
    ]
    return build_report(DaySummary(date=DAY, statuses=statuses), school_name="44 Школа")


@pytest.mark.parametrize("n", [3, 25, 33, 45])
def test_report_always_fits_one_page(n):
    """Головна вимога: аркуш один, скільки б не було класів.

    Кегль підбирається сам, тож зростання школи має зменшувати шрифт, а не
    додавати другу сторінку — її просто не понесуть медсестрі.
    """
    pdf = render_day_report(_report_with(n))
    assert _pages(pdf) == 1, f"{n} класів дали більше однієї сторінки"


@pytest.mark.parametrize("n", [60, 80])
def test_one_page_holds_for_a_very_large_school(n):
    """Межа одного аркуша має триматися далеко за межами реальної школи."""
    assert _pages(render_day_report(_report_with(n))) == 1


@pytest.mark.parametrize("n", [100, 200])
def test_enormous_school_gets_pages_instead_of_nothing(n):
    """За межею аркуша звіт друкується в кілька сторінок, а не зникає.

    Знайдено на рев'ю PR #13: двоколонковий блок нерозривний, тож reportlab
    кидав LayoutError, джоб мовчки його ковтав — і того дня не приходило
    нічого, без жодного натяку чому.
    """
    pdf = render_day_report(_report_with(n))   # не має кидати виняток
    assert pdf.startswith(b"%PDF")
    assert _pages(pdf) > 1


def test_longer_labels_force_a_smaller_font():
    """Кегль має спадати саме від довжини підписів, а не бути сталим.

    Абсолютне число тут пиняти не можна: метрики шрифтів різні на різних
    системах (локально Arial, на сервері DejaVu), тож той самий кегль дає
    різну ширину. Перевіряємо відношення, а не значення — воно й описує логіку.
    """
    from school_bot.reports.pdf import (
        DAY_HEADERS,
        DAY_WIDTHS,
        ONE_PAGE_MAX_FONT,
        _best_font,
        _bold_font,
        _cyrillic_font,
    )

    font, bold = _cyrillic_font(), _bold_font()

    short = _best_font([["1-А", "3", "2"]], DAY_HEADERS, DAY_WIDTHS, font, bold)
    long_label = _best_font(
        [["11-Б (вечірня форма навчання)", "3", "2"]], DAY_HEADERS, DAY_WIDTHS, font, bold
    )

    assert long_label < short, "довгий підпис класу має зменшити кегль"
    assert short <= ONE_PAGE_MAX_FONT
    assert long_label >= 8, "кегль не має падати до нечитабельного"


def test_header_does_not_hold_back_the_numbers():
    """Шапку читають раз, цифри — весь час, тож вона не має тягнути кегль униз.

    «Хворі» у вузькій колонці інакше обмежував би розмір самих цифр, заради
    яких звіт і роблять.
    """
    from school_bot.reports.pdf import (
        DAY_WIDTHS,
        HEADER_FONT_CAP,
        _best_font,
        _bold_font,
        _cyrillic_font,
    )

    font, bold = _cyrillic_font(), _bold_font()
    rows = [["1-А", "3", "2"]]

    # Обидві шапки вміщаються при HEADER_FONT_CAP, тож кегль цифр однаковий —
    # хоча «Хворі» утричі довша за «Х» і без стелі тягнула б таблицю вниз.
    short_header = _best_font(rows, ["К", "В", "Х"], DAY_WIDTHS, font, bold)
    real_header = _best_font(rows, ["Клас", "Відс.", "Хворі"], DAY_WIDTHS, font, bold)

    assert real_header == short_header, "шапка зменшила кегль цифр"
    assert HEADER_FONT_CAP < short_header


def test_table_fits_the_printed_area():
    """Таблиця не має наїжджати на береги.

    Знайдено на рев'ю PR #13: проміжок додавався до кожної половини, і 188 мм
    опинялися в рамці 182 мм. reportlab падає лише по висоті — надто широку
    таблицю він просто центрує поверх берегів, тож на екрані це непомітно,
    а на роздруку видно.
    """
    from school_bot.reports.pdf import (
        COLUMN_GAP,
        DAY_WIDTHS,
        HALF_WIDTH,
        USABLE_WIDTH,
    )

    assert 2 * (HALF_WIDTH + COLUMN_GAP) <= USABLE_WIDTH
    assert sum(DAY_WIDTHS) <= HALF_WIDTH, "колонки ширші за половину аркуша"


def test_split_keeps_both_columns_in_order():
    """Розріз посередині лишає обидві половини впорядкованими.

    Клас шукають очима зверху вниз у лівій колонці, потім у правій, тож
    порядок у кожній половині має лишатися тим самим, що й у звіті.
    """
    from school_bot.reports.pdf import _split_in_two

    rows = [[f"{i}-А", "1", "0"] for i in range(1, 11)]
    left, right = _split_in_two(rows)

    assert len(left) + len(right) == 10
    assert left + right == rows


def test_odd_number_of_classes_splits_without_losing_one():
    """Непарна кількість класів — найлегший спосіб загубити рядок на розрізі."""
    from school_bot.reports.pdf import _split_in_two

    rows = [[f"{i}-А", "1", "0"] for i in range(1, 8)]
    left, right = _split_in_two(rows)
    assert left + right == rows
    assert len(left) == 4 and len(right) == 3
