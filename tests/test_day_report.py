"""Щоденний звіт: розклад роздачі, групування класів і PDF.

Звіт читають на місці, звіряючи зі зміною на роздачі, тому найдорожчі помилки
тут — не падіння, а тихі: клас, що зник із групування, або сума, яка не
сходиться з рядками. Саме їх і ловлять ці тести.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.config import BASE_DIR, Settings
from school_bot.db.models import MealEntry, SchoolClass
from school_bot.domain.classes import parse_class_name
from school_bot.domain.slots import parse_meal_slots
from school_bot.reports.day import (
    UNSCHEDULED_LABEL,
    ReportKind,
    build_day_report,
    build_report,
    day_report_filename,
)
from school_bot.reports.pdf import render_day_report

DAY = date(2026, 9, 3)

SCHEDULE = "09:45-10:00 = 1-А, 5-В; 08:45-09:00 = 3-Б"


# --- розбір MEAL_SLOTS -----------------------------------------------------


def test_slots_parsed_and_sorted_by_time():
    slots = parse_meal_slots(SCHEDULE)
    assert [s.label for s in slots] == ["08:45 – 09:00", "09:45 – 10:00"]
    assert slots[0].class_names == ("3-Б",)
    assert slots[1].class_names == ("1-А", "5-В")


def test_slots_accept_newlines_as_separator():
    """Розклад можна записати стовпчиком — так його зручніше правити."""
    slots = parse_meal_slots("08:45-09:00 = 3-Б\n09:45-10:00 = 1-А")
    assert len(slots) == 2


def test_slot_class_names_are_normalised():
    """«3б» і «3-Б» — той самий клас, інакше він не знайдеться в базі."""
    assert parse_meal_slots("08:45-09:00 = 3б, 10 а")[0].class_names == ("3-Б", "10-А")


def test_latin_lookalikes_fold_to_cyrillic():
    """«10-A» з латинською A виглядає як «10-А», але це інший рядок.

    Список класів часто копіюють із чужого документа, де розкладка змішана;
    без згортання такий клас мовчки випав би зі звіту.
    """
    latin = "10-A"  # саме латинська A — так її копіюють із чужих документів
    assert parse_class_name(latin)[0] == "10-А"
    assert parse_meal_slots(f"08:45-09:00 = {latin}")[0].class_names == ("10-А",)


@pytest.mark.parametrize(
    "bad",
    [
        "08:45-09:00",                      # без класів
        "3-А, 3-Б",                         # без часу
        "08:45-09:00 = ",                   # порожня зміна
        "25:00-26:00 = 3-А",                # неможливий час
        "09:00-08:45 = 3-А",                # кінець раніше початку
        "08:45-09:00 = абв",                # не клас
        "08:45-09:00 = 3-А; 09:15-09:30 = 3-А",  # клас у двох змінах
    ],
)
def test_broken_schedule_is_rejected_loudly(bad: str):
    """Мовчазно проігнорований рядок = клас, що зник зі звіту непомітно."""
    with pytest.raises(ValueError):
        parse_meal_slots(bad)


def test_settings_parse_schedule_from_env_string():
    s = Settings(meal_slots=SCHEDULE)
    assert [x.start for x in s.meal_slots] == [time(8, 45), time(9, 45)]


def test_empty_schedule_is_allowed():
    assert Settings(meal_slots="").meal_slots == []


# --- групування ------------------------------------------------------------


async def _with_counts(session: AsyncSession, counts: dict[str, int]) -> None:
    for name, value in counts.items():
        row = await session.scalar(select(SchoolClass).where(SchoolClass.name == name))
        session.add(MealEntry(class_id=row.id, date=DAY, eating_count=value))
    await session.flush()


@pytest.mark.asyncio
async def test_classes_are_grouped_in_serving_order(session, classes):
    """Порядок рядків — порядок роздачі, а не алфавіт."""
    await _with_counts(session, {"1-А": 10, "3-Б": 20, "5-В": 5})
    report = await build_day_report(session, DAY, slots=parse_meal_slots(SCHEDULE))

    assert [g.label for g in report.groups] == ["08:45 – 09:00", "09:45 – 10:00"]
    assert [c.name for c in report.groups[0].cells] == ["3-Б"]
    assert [c.name for c in report.groups[1].cells] == ["1-А", "5-В"]
    assert report.groups[1].total == 15
    assert report.total == 35


@pytest.mark.asyncio
async def test_group_totals_sum_to_the_grand_total(session, classes):
    """Підсумок не має суперечити рядкам, з яких він складений."""
    await _with_counts(session, {"1-А": 10, "3-Б": 20, "5-В": 5})
    report = await build_day_report(session, DAY, slots=parse_meal_slots(SCHEDULE))
    assert sum(g.total for g in report.groups) == report.total


@pytest.mark.asyncio
async def test_class_outside_the_schedule_is_not_lost(session, classes):
    """Новий клас, якого ще немає в MEAL_SLOTS, має лишитися у звіті.

    Інакше він зникає мовчки, і сума перестає сходитися з реальністю —
    помітили б це вже на перевірці.
    """
    await _with_counts(session, {"1-А": 10, "3-Б": 20, "5-В": 5})
    report = await build_day_report(session, DAY, slots=parse_meal_slots("08:45-09:00 = 3-Б"))

    assert [g.label for g in report.groups] == ["08:45 – 09:00", UNSCHEDULED_LABEL]
    assert {c.name for c in report.groups[1].cells} == {"1-А", "5-В"}
    assert report.total == 35


@pytest.mark.asyncio
async def test_without_schedule_all_classes_go_into_one_plain_group(session, classes):
    await _with_counts(session, {"1-А": 10})
    report = await build_day_report(session, DAY)
    assert len(report.groups) == 1
    assert report.groups[0].label == ""
    assert report.expected == 3


@pytest.mark.asyncio
async def test_schedule_may_mention_a_class_the_school_no_longer_has(session, classes):
    """Прибраний клас у MEAL_SLOTS не має валити звіт."""
    report = await build_day_report(session, DAY, slots=parse_meal_slots("08:45-09:00 = 7-Я"))
    assert [g.label for g in report.groups] == [UNSCHEDULED_LABEL]


@pytest.mark.asyncio
async def test_missing_class_is_not_counted_as_zero(session, classes):
    """Пропуск і справжній нуль — різні речі, і у звіті вони різні."""
    await _with_counts(session, {"1-А": 0})
    report = await build_day_report(session, DAY)

    cells = {c.name: c for c in report.cells}
    assert cells["1-А"].count == 0 and cells["1-А"].submitted
    assert cells["3-Б"].count is None and not cells["3-Б"].submitted
    assert report.submitted == 1
    assert report.missing == ["3-Б", "5-В"]


def test_report_builds_from_a_snapshot_without_touching_the_database():
    """`build_report` — чиста функція над готовим DaySummary.

    Саме завдяки цьому текст зведення і PDF будуються з одного знімка: той,
    хто вже має DaySummary, не мусить перечитувати день.
    """
    from school_bot.domain.meals import ClassDayStatus, DaySummary

    statuses = [
        ClassDayStatus(school_class=SchoolClass(name="3-Б", grade=3, letter="Б"), entry=None),
        ClassDayStatus(
            school_class=SchoolClass(name="1-А", grade=1, letter="А"),
            entry=MealEntry(date=DAY, eating_count=17),
        ),
    ]
    report = build_report(
        DaySummary(date=DAY, statuses=statuses), slots=parse_meal_slots(SCHEDULE)
    )

    assert report.date == DAY
    assert report.total == 17
    assert report.missing == ["3-Б"]
    assert [g.label for g in report.groups] == ["08:45 – 09:00", "09:45 – 10:00"]


# --- PDF -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_renders_for_a_real_day(session, classes):
    await _with_counts(session, {"1-А": 10, "3-Б": 20})
    report = await build_day_report(
        session, DAY, school_name="44 Школа", slots=parse_meal_slots(SCHEDULE)
    )
    pdf = render_day_report(report, ReportKind.MEALS)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


@pytest.mark.asyncio
async def test_pdf_renders_when_nobody_submitted_anything(session, classes):
    """День без жодної цифри — звичайний стан о 09:35, не привід падати."""
    report = await build_day_report(session, DAY, slots=parse_meal_slots(SCHEDULE))
    assert render_day_report(report, ReportKind.ABSENCE).startswith(b"%PDF")


def test_filename_carries_the_date():
    assert day_report_filename(DAY) == "harchuvannia_2026-09-03.pdf"


# --- узгодженість .env.example --------------------------------------------


def test_example_schedule_only_mentions_example_classes():
    """`.env.example` має бути робочим цілим, а не двома незалежними списками.

    Клас у MEAL_SLOTS, якого немає в SCHOOL_CLASSES, дає лише рядок у логах —
    того, хто копіює приклад під свою школу, це збиває з пантелику.
    """
    s = Settings(_env_file=BASE_DIR / ".env.example")
    scheduled = {name for slot in s.meal_slots for name in slot.class_names}
    known = {parse_class_name(n)[0] for n in s.school_classes}
    assert scheduled <= known, f"немає серед SCHOOL_CLASSES: {sorted(scheduled - known)}"


@pytest.mark.asyncio
async def test_day_report_carries_absent_and_sick(session, classes):
    from school_bot.domain.meals import upsert_entry

    await upsert_entry(
        session, class_id=classes[0].id, d=DAY, eating_count=17,
        absent_count=3, sick_count=1, teacher_id=None,
    )
    report = await build_day_report(session, DAY)

    cell = next(c for c in report.cells if c.name == "1-А")
    assert (cell.count, cell.absent, cell.sick) == (17, 3, 1)
    assert (report.absent_total, report.sick_total) == (3, 1)


@pytest.mark.asyncio
async def test_skipping_the_second_question_does_not_make_a_class_missing(session, classes):
    """Клас подав харчування й пропустив відсутніх — він НЕ боржник."""
    from school_bot.domain.meals import upsert_entry

    await upsert_entry(
        session, class_id=classes[0].id, d=DAY, eating_count=17, teacher_id=None
    )
    report = await build_day_report(session, DAY)

    cell = next(c for c in report.cells if c.name == "1-А")
    assert cell.submitted is True
    assert cell.absent is None
    assert "1-А" not in report.missing


@pytest.mark.asyncio
async def test_totals_stay_empty_when_nobody_reported_absences(session, classes):
    """Порожньо — не нуль: старі дні не мають тверджувати, що ніхто не хворів."""
    from school_bot.domain.meals import upsert_entry

    await upsert_entry(
        session, class_id=classes[0].id, d=DAY, eating_count=17, teacher_id=None
    )
    report = await build_day_report(session, DAY)
    assert report.absent_total is None
    assert report.sick_total is None


# --- звіт про харчування завжди на одному аркуші ---------------------------


def _pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def _report_with(n_classes: int) -> object:
    from school_bot.domain.meals import ClassDayStatus, DaySummary

    statuses = [
        ClassDayStatus(
            school_class=SchoolClass(name=f"{i // 3 + 1}-{'АБВ'[i % 3]}",
                                     grade=i // 3 + 1, letter="АБВ"[i % 3]),
            entry=MealEntry(date=DAY, eating_count=20 + i % 9),
        )
        for i in range(n_classes)
    ]
    return build_report(DaySummary(date=DAY, statuses=statuses), school_name="44 Школа")


@pytest.mark.parametrize("n", [3, 25, 33, 45])
def test_meals_report_always_fits_one_page(n):
    """Головна вимога: аркуш один, скільки б не було класів.

    Кегль підбирається сам, тож зростання школи має зменшувати шрифт, а не
    додавати другу сторінку — її просто не понесуть на роздачу.
    """
    pdf = render_day_report(_report_with(n), ReportKind.MEALS)
    assert _pages(pdf) == 1, f"{n} класів дали більше однієї сторінки"


def test_font_shrinks_only_when_it_has_to():
    """Маленька школа має отримати найбільший кегль, а не той самий, що велика."""
    from school_bot.reports.pdf import (
        KIND_COLUMNS,
        ONE_PAGE_MAX_FONT,
        _best_font,
        _bold_font,
        _cyrillic_font,
        _report_rows,
    )

    headers, widths = KIND_COLUMNS[ReportKind.MEALS]
    rows = _report_rows(_report_with(3), ReportKind.MEALS)
    size = _best_font(rows, headers, widths, _cyrillic_font(), _bold_font())
    assert size == ONE_PAGE_MAX_FONT


def test_columns_never_split_a_serving_slot():
    """Зміну не можна розірвати між колонками — око шукає її цілою."""
    from school_bot.reports.pdf import _split_in_two

    rows = [
        (["08:45 – 09:00", "5"], True),
        (["1-А", "5"], False),
        (["09:15 – 09:30", "7"], True),
        (["2-А", "7"], False),
    ]
    left, right = _split_in_two(rows)
    assert right[0][1] is True, "друга колонка має починатися зі зміни"
    assert left and right


@pytest.mark.parametrize("n", [3, 25, 33, 45])
def test_absence_report_also_fits_one_page(n):
    """Той самий аркуш і для відсутніх: його теж друкують."""
    pdf = render_day_report(_report_with(n), ReportKind.ABSENCE)
    assert _pages(pdf) == 1, f"{n} класів дали більше однієї сторінки"


def test_table_fits_the_printed_area():
    """Таблиця не має наїжджати на береги.

    Знайдено на рев'ю PR #13: проміжок додавався до кожної половини, і 188 мм
    опинялися в рамці 182 мм. reportlab падає лише по висоті — надто широку
    таблицю він просто центрує поверх берегів, тож на екрані це непомітно,
    а на роздруку видно.
    """
    from school_bot.reports.pdf import (
        COLUMN_GAP,
        HALF_WIDTH,
        KIND_COLUMNS,
        USABLE_WIDTH,
    )

    assert 2 * (HALF_WIDTH + COLUMN_GAP) <= USABLE_WIDTH
    for kind, (_, widths) in KIND_COLUMNS.items():
        assert sum(widths) <= HALF_WIDTH, f"{kind.value}: колонки ширші за половину"


def test_single_serving_slot_keeps_a_header_in_both_columns():
    """Школа з однією зміною: права колонка не має лишитися без підпису.

    Знайдено на рев'ю PR #13 — межі груп не було, і код падав у наївний розріз
    посередині, лишаючи половину класів без заголовка.
    """
    from school_bot.reports.pdf import _split_in_two

    rows = [(["08:45 – 09:00", "50"], True)]
    rows += [([f"{i}-А", "10"], False) for i in range(1, 11)]

    left, right = _split_in_two(rows)
    assert left[0][1] is True
    assert right[0][1] is True, "друга колонка без заголовка зміни"
    assert right[0][0][0] == "08:45 – 09:00"


def test_no_slots_at_all_still_splits():
    """Без MEAL_SLOTS заголовків немає взагалі — розріз має просто спрацювати."""
    from school_bot.reports.pdf import _split_in_two

    rows = [([f"{i}-А", "10"], False) for i in range(1, 11)]
    left, right = _split_in_two(rows)
    assert len(left) + len(right) == 10
    assert left and right
