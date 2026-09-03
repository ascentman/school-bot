"""Дані для щоденного звіту: дата, загальна цифра, класи по змінах.

Окремо від MonthMatrix свідомо: місячний табель відповідає на питання «як було
протягом місяця», а цей — на питання «кого й скільки годувати сьогодні», і
групується не по класах, а по змінах роздачі.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as Date

from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.domain.meals import day_summary
from school_bot.domain.slots import MealSlot

log = logging.getLogger(__name__)

# Класи, яких немає в жодній зміні MEAL_SLOTS. Мовчки викидати їх не можна:
# новий клас інакше зникне зі звіту, а сума перестане сходитися.
UNSCHEDULED_LABEL = "Поза розкладом"


def day_report_filename(d: Date) -> str:
    """Ім'я файлу звіту за день.

    Спільне для розсилки й кнопки: інакше той самий звіт приходив би
    під двома різними назвами.
    """
    return f"harchuvannia_{d:%Y-%m-%d}.pdf"


@dataclass(slots=True)
class ClassCell:
    name: str
    count: int | None

    @property
    def submitted(self) -> bool:
        return self.count is not None


@dataclass(slots=True)
class SlotGroup:
    """Одна зміна у звіті. `label` порожній, якщо розклад не заданий."""

    label: str
    cells: list[ClassCell]

    @property
    def total(self) -> int:
        return sum(c.count or 0 for c in self.cells)

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.cells if not c.submitted]

    @property
    def has_data(self) -> bool:
        return any(c.submitted for c in self.cells)


@dataclass(slots=True)
class DayReport:
    date: Date
    school_name: str
    groups: list[SlotGroup]

    @property
    def cells(self) -> list[ClassCell]:
        return [c for g in self.groups for c in g.cells]

    @property
    def total(self) -> int:
        return sum(g.total for g in self.groups)

    @property
    def expected(self) -> int:
        return len(self.cells)

    @property
    def submitted(self) -> int:
        return sum(1 for c in self.cells if c.submitted)

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.cells if not c.submitted]


async def build_day_report(
    session: AsyncSession,
    d: Date,
    *,
    school_name: str = "",
    slots: Sequence[MealSlot] = (),
) -> DayReport:
    """Зібрати звіт за день, розклавши класи по змінах роздачі."""
    summary = await day_summary(session, d)
    by_name = {s.school_class.name: s for s in summary.statuses}

    groups: list[SlotGroup] = []
    placed: set[str] = set()

    for slot in slots:
        cells: list[ClassCell] = []
        for name in slot.class_names:
            status = by_name.get(name)
            if status is None:
                # Клас є в розкладі, але вимкнений або не заведений у школі.
                log.warning("MEAL_SLOTS: класу %s немає серед активних", name)
                continue
            cells.append(ClassCell(name=name, count=status.count))
            placed.add(name)
        if cells:
            groups.append(SlotGroup(label=slot.label, cells=cells))

    rest = [
        ClassCell(name=s.school_class.name, count=s.count)
        for s in summary.statuses
        if s.school_class.name not in placed
    ]
    if rest:
        groups.append(SlotGroup(label=UNSCHEDULED_LABEL if slots else "", cells=rest))

    return DayReport(date=d, school_name=school_name, groups=groups)
