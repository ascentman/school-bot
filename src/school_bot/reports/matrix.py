"""Спільна структура даних для всіх експортів.

XLSX, PDF і Google Sheet будуються з одного MonthMatrix — тому вони не можуть
розійтися між собою. Будь-який новий формат додається тут же, без дублювання логіки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.db.models import DayKind, MealEntry, MealField, NonSchoolDay
from school_bot.domain.dates import format_month, weekday_name
from school_bot.domain.meals import active_classes

# Підпис метрики у назві аркуша й у заголовку. Харчування лишається без
# суфікса: це основна таблиця, і вона має виглядати як раніше.
UA_METRIC = {
    MealField.EATING: "",
    MealField.ABSENT: "відсутні",
    MealField.SICK: "хворі",
}

_METRIC_COLUMN = {
    MealField.EATING: "eating_count",
    MealField.ABSENT: "absent_count",
    MealField.SICK: "sick_count",
}

UA_DAY_KIND_SHORT = {
    DayKind.HOLIDAY: "св",
    DayKind.VACATION: "кн",
    DayKind.REMOTE: "дс",
    DayKind.OTHER: "—",
}


@dataclass(slots=True)
class DayColumn:
    date: Date
    is_weekend: bool
    off_kind: DayKind | None

    @property
    def is_school_day(self) -> bool:
        return not self.is_weekend and self.off_kind is None

    @property
    def label(self) -> str:
        return str(self.date.day)

    @property
    def weekday_short(self) -> str:
        return weekday_name(self.date, short=True)

    @property
    def off_marker(self) -> str:
        if self.is_weekend:
            return ""
        return UA_DAY_KIND_SHORT.get(self.off_kind, "") if self.off_kind else ""


@dataclass(slots=True)
class ClassRow:
    """Рядок класу за місяць.

    `values` тримає лише НЕПОРОЖНІ цифри обраної метрики, а `submitted` — дні,
    коли запис узагалі існує. Для харчування ці множини збігаються (колонка
    NOT NULL), а для відсутніх і хворих — ні: клас міг подати харчування й
    пропустити друге питання. Тому «дірка» рахується по `submitted`, інакше
    аркуш «Відсутні» був би суцільно червоний.
    """

    class_id: int
    name: str
    values: dict[Date, int]
    submitted: set[Date] = field(default_factory=set)

    def value(self, d: Date) -> int | None:
        return self.values.get(d)

    @property
    def total(self) -> int:
        return sum(self.values.values())

    def missing_days(self, columns: list[DayColumn], until: Date | None = None) -> list[Date]:
        """Навчальні дні без запису — саме те, що першим питає перевірка.

        `until` відсікає майбутні дати: день, який ще не настав, не є пропуском.
        """
        return [
            c.date
            for c in columns
            if c.is_school_day
            and c.date not in self.submitted
            and (until is None or c.date <= until)
        ]


@dataclass(slots=True)
class MonthMatrix:
    year: int
    month: int
    columns: list[DayColumn]
    rows: list[ClassRow]
    school_name: str
    today: Date | None = None
    metric: MealField = MealField.EATING

    @property
    def title(self) -> str:
        return format_month(self.year, self.month)

    @property
    def heading(self) -> str:
        """Заголовок таблиці. Для харчування — рівно такий, як був завжди."""
        base = "Облік харчування учнів"
        if self.metric is not MealField.EATING:
            base = f"{base} — {UA_METRIC[self.metric]}"
        return f"{base} — {self.title}"

    @property
    def school_days(self) -> list[DayColumn]:
        return [c for c in self.columns if c.is_school_day]

    @property
    def elapsed_school_days(self) -> list[DayColumn]:
        """Навчальні дні, які вже минули. Майбутні не є пропусками."""
        if self.today is None:
            return self.school_days
        return [c for c in self.school_days if c.date <= self.today]

    def is_future(self, d: Date) -> bool:
        return self.today is not None and d > self.today

    def is_gap(self, row: ClassRow, col: DayColumn) -> bool:
        """Пропущений навчальний день — те, що підсвічується червоним.

        Червоне означає «клас того дня не подав нічого», однаково на всіх
        трьох аркушах. Пропущене друге питання дірою не є: запис поданий,
        просто цієї цифри в ньому немає.
        """
        return (
            col.is_school_day
            and not self.is_future(col.date)
            and col.date not in row.submitted
        )

    def has_data(self, d: Date) -> bool:
        return any(d in r.values for r in self.rows)

    def day_total(self, d: Date) -> int | None:
        """Сума за день, або None якщо жоден клас ще не подав.

        Порожня клітинка і справжній нуль — різні речі: «0» у звіті означає,
        що того дня ніхто не харчувався, а не що дані ще не зібрані.
        """
        if not self.has_data(d):
            return None
        return sum(r.values.get(d, 0) for r in self.rows)

    @property
    def grand_total(self) -> int:
        return sum(r.total for r in self.rows)

    @property
    def has_any_data(self) -> bool:
        """Чи є в цій метриці бодай одна цифра за місяць."""
        return any(r.values for r in self.rows)

    @property
    def missing_total(self) -> int:
        return sum(len(r.missing_days(self.columns, self.today)) for r in self.rows)


def _month_bounds(year: int, month: int) -> tuple[Date, Date]:
    first = Date(year, month, 1)
    last = Date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return first, last


def _project(
    year: int,
    month: int,
    columns: list[DayColumn],
    classes: list,
    entries: list[MealEntry],
    *,
    metric: MealField,
    school_name: str,
    today: Date | None,
) -> MonthMatrix:
    """Розкласти вже прочитані записи в матрицю однієї метрики.

    Без запитів до БД: три аркуші місячного звіту будуються з одних і тих
    самих рядків, тож читати їх тричі немає потреби.
    """
    column = _METRIC_COLUMN[metric]
    by_class: dict[int, dict[Date, int]] = {c.id: {} for c in classes}
    submitted: dict[int, set[Date]] = {c.id: set() for c in classes}
    for e in entries:
        if e.class_id not in by_class:
            continue
        submitted[e.class_id].add(e.date)
        value = getattr(e, column)
        if value is not None:
            by_class[e.class_id][e.date] = value

    rows = [
        ClassRow(
            class_id=c.id,
            name=c.name,
            values=by_class.get(c.id, {}),
            submitted=submitted.get(c.id, set()),
        )
        for c in classes
    ]
    return MonthMatrix(
        year=year,
        month=month,
        columns=columns,
        rows=rows,
        school_name=school_name,
        today=today,
        metric=metric,
    )


async def _read_month(session: AsyncSession, year: int, month: int):
    """Прочитати все потрібне за місяць: календар, класи, записи."""
    first, last = _month_bounds(year, month)

    off_days = {
        row.date: row.kind
        for row in await session.scalars(
            select(NonSchoolDay).where(NonSchoolDay.date.between(first, last))
        )
    }

    columns: list[DayColumn] = []
    cursor = first
    while cursor <= last:
        columns.append(
            DayColumn(
                date=cursor,
                is_weekend=cursor.weekday() >= 5,
                off_kind=off_days.get(cursor),
            )
        )
        cursor += timedelta(days=1)

    classes = await active_classes(session)
    entries = list(
        await session.scalars(select(MealEntry).where(MealEntry.date.between(first, last)))
    )
    return columns, classes, entries


async def build_month_matrix(
    session: AsyncSession,
    year: int,
    month: int,
    *,
    school_name: str = "",
    today: Date | None = None,
    metric: MealField = MealField.EATING,
) -> MonthMatrix:
    columns, classes, entries = await _read_month(session, year, month)
    return _project(
        year, month, columns, classes, entries,
        metric=metric, school_name=school_name, today=today,
    )


async def build_month_matrices(
    session: AsyncSession,
    year: int,
    month: int,
    *,
    school_name: str = "",
    today: Date | None = None,
) -> list[MonthMatrix]:
    """Усі три метрики за місяць — за одне читання БД.

    Порядок фіксований: харчування першим, бо саме воно лишається основною
    таблицею (перший аркуш, перша сторінка, перша вкладка).
    """
    columns, classes, entries = await _read_month(session, year, month)
    return [
        _project(
            year, month, columns, classes, entries,
            metric=m, school_name=school_name, today=today,
        )
        for m in (MealField.EATING, MealField.ABSENT, MealField.SICK)
    ]


async def available_months(session: AsyncSession, limit: int = 12) -> list[tuple[int, int]]:
    """Місяці, за які є хоч якісь дані — щоб не пропонувати порожні."""
    dates = await session.scalars(select(MealEntry.date).distinct().order_by(MealEntry.date.desc()))
    seen: list[tuple[int, int]] = []
    for d in dates:
        key = (d.year, d.month)
        if key not in seen:
            seen.append(key)
        if len(seen) >= limit:
            break
    return seen
