"""Дані для щоденного звіту: дата й класи з цифрами відсутніх і хворих.

Окремо від MonthMatrix свідомо: місячний табель відповідає на питання «як було
протягом місяця», а цей — на питання «кого немає сьогодні», і друкується на
одному аркуші, щоб його можна було віднести медсестрі одразу.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date

from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.domain.meals import DaySummary, day_summary, optional_sum

REPORT_TITLE = "Відсутні та хворі"


def day_report_filename(d: Date) -> str:
    """Ім'я файлу звіту за день.

    Спільне для розсилки й кнопки: інакше той самий звіт приходив би
    під двома різними назвами.
    """
    return f"vidsutni_{d:%Y-%m-%d}.pdf"


@dataclass(slots=True)
class ClassCell:
    """Клас у звіті за день.

    `submitted` — це НАЯВНІСТЬ ЗАПИСУ, а не наявність цифри: клас може подати
    відсутніх і пропустити питання про хворих, тож виводити «подав» зі значення
    означало б рахувати його в боржники.
    """

    name: str
    absent: int | None = None
    sick: int | None = None
    submitted: bool = True


@dataclass(slots=True)
class DayReport:
    date: Date
    school_name: str
    cells: list[ClassCell]

    @property
    def expected(self) -> int:
        return len(self.cells)

    @property
    def submitted(self) -> int:
        return sum(1 for c in self.cells if c.submitted)

    @property
    def absent_total(self) -> int | None:
        return optional_sum(c.absent for c in self.cells)

    @property
    def sick_total(self) -> int | None:
        return optional_sum(c.sick for c in self.cells)

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.cells if not c.submitted]


def build_report(summary: DaySummary, *, school_name: str = "") -> DayReport:
    """Перекласти вже зібраний підсумок дня у рядки звіту.

    Приймає готовий DaySummary, а не сесію, щоб текст зведення і прикріплений
    до нього PDF будувалися з одного знімка даних. Інакше між двома запитами
    вчитель встигає надіслати цифру — і два документи за той самий день
    показують різні числа.

    Порядок класів — той самий, що й усюди: клас, паралель, літера.
    """
    return DayReport(
        date=summary.date,
        school_name=school_name,
        cells=[
            ClassCell(
                name=s.school_class.name,
                absent=s.absent,
                sick=s.sick,
                submitted=s.submitted,
            )
            for s in summary.statuses
        ],
    )


async def build_day_report(
    session: AsyncSession, d: Date, *, school_name: str = ""
) -> DayReport:
    """Зібрати звіт за день. Для тих, у кого ще немає готового DaySummary."""
    summary = await day_summary(session, d)
    return build_report(summary, school_name=school_name)
