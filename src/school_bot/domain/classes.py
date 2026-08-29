"""Створення класів з тексту та розбір діапазонів дат."""

from __future__ import annotations

import logging
import re
from datetime import date as Date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.db.models import ClassAssignment, SchoolClass

log = logging.getLogger(__name__)

# "3-Б", "3 Б", "3б", "10-А"
CLASS_RE = re.compile(r"^\s*(\d{1,2})\s*[-–—\s]?\s*([А-ЯЇІЄҐа-яїієґA-Za-z]?)\s*$")

DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")


def parse_class_name(raw: str) -> tuple[str, int, str] | None:
    """'3б' → ('3-Б', 3, 'Б'). Повертає None, якщо не розпізнано."""
    m = CLASS_RE.match(raw)
    if not m:
        return None
    grade = int(m.group(1))
    if not 1 <= grade <= 12:
        return None
    letter = m.group(2).upper()
    name = f"{grade}-{letter}" if letter else str(grade)
    return name, grade, letter


def parse_date_range(raw: str) -> tuple[Date, Date] | None:
    """'28.10.2026 - 03.11.2026' або '28.10.2026' → (start, end)."""
    found = DATE_RE.findall(raw)
    if not found:
        return None
    try:
        dates = [Date(int(y), int(mo), int(d)) for d, mo, y in found[:2]]
    except ValueError:
        return None
    start = dates[0]
    end = dates[1] if len(dates) > 1 else start
    return (start, end) if start <= end else (end, start)


async def create_classes(session: AsyncSession, raw: str) -> tuple[list[str], list[str]]:
    """Створити класи зі списку через кому. Повертає (створені, відхилені)."""
    created: list[str] = []
    rejected: list[str] = []

    existing = set(await session.scalars(select(SchoolClass.name)))

    for chunk in re.split(r"[,;\n]+", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        parsed = parse_class_name(chunk)
        if parsed is None:
            rejected.append(chunk)
            continue
        name, grade, letter = parsed
        if name in existing:
            rejected.append(f"{name} (вже є)")
            continue
        session.add(
            SchoolClass(
                name=name,
                grade=grade,
                letter=letter,
                sort_order=grade * 100 + (ord(letter) if letter else 0),
            )
        )
        existing.add(name)
        created.append(name)

    await session.flush()
    return created, rejected


async def ensure_classes(session: AsyncSession, names: list[str]) -> list[str]:
    """Створити класи зі списку, яких ще немає.

    Викликається при старті з SCHOOL_CLASSES. Наявних не чіпає й нічого не
    видаляє: за класом, прибраним зі списку, лишається історія записів,
    а сховати його з опитування можна через /off_class.
    """
    if not names:
        return []
    created, rejected = await create_classes(session, ", ".join(names))
    if created:
        log.info("Створено класи з конфігу: %s", ", ".join(created))

    # Нерозпізнане не мовчимо: одрук у SCHOOL_CLASSES інакше просто зникає,
    # і адміністратор дізнається про це, лише коли вчитель не знайде свій клас.
    unknown = [r for r in rejected if "вже є" not in r]
    if unknown:
        log.warning("SCHOOL_CLASSES: не розпізнано — %s", ", ".join(unknown))
    return created


async def add_teacher_class(session: AsyncSession, teacher_id: int, class_id: int) -> None:
    """Додати один клас, не чіпаючи решти.

    Саме додати, а не перезаписати набір: set_teacher_classes видаляє все,
    чого немає в переданому списку. Два майже одночасні тапи по різних
    класах читали б набір до коміту сусіда й затирали одне одного.
    """
    try:
        # UNIQUE(class_id, teacher_id) робить повтор безпечним; savepoint
        # не дає конфлікту завалити всю сесію.
        async with session.begin_nested():
            session.add(
                ClassAssignment(class_id=class_id, teacher_id=teacher_id, is_primary=True)
            )
    except IntegrityError:
        log.debug("Клас %s уже закріплений за вчителем %s", class_id, teacher_id)


async def set_teacher_classes(
    session: AsyncSession, teacher_id: int, class_ids: set[int]
) -> None:
    """Замінити набір класів вчителя на заданий."""
    current = list(
        await session.scalars(
            select(ClassAssignment).where(ClassAssignment.teacher_id == teacher_id)
        )
    )
    for row in current:
        if row.class_id not in class_ids:
            await session.delete(row)
    have = {row.class_id for row in current}
    for class_id in class_ids - have:
        session.add(
            ClassAssignment(class_id=class_id, teacher_id=teacher_id, is_primary=True)
        )
    await session.flush()
