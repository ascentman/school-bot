"""Запис і агрегація даних про відсутніх та хворих."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.db.models import (
    ClassAssignment,
    EntrySource,
    MealEntry,
    MealEntryAudit,
    MealField,
    SchoolClass,
)


def optional_sum(values) -> int | None:
    """Сума наявних цифр, або None, якщо не подано жодної.

    Порожньо — це не нуль: до появи обліку відсутніх даних не було взагалі, і
    звіт за той час не має стверджувати, що тоді ніхто не хворів.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None


@dataclass(slots=True)
class ClassDayStatus:
    school_class: SchoolClass
    entry: MealEntry | None

    @property
    def submitted(self) -> bool:
        return self.entry is not None

    # Хворі можуть бути None навіть за наявного запису — вчитель пропустив
    # питання. Це не те саме, що «клас не подав нічого».
    @property
    def absent(self) -> int | None:
        return self.entry.absent_count if self.entry else None

    @property
    def sick(self) -> int | None:
        return self.entry.sick_count if self.entry else None


@dataclass(slots=True)
class DaySummary:
    date: Date
    statuses: list[ClassDayStatus] = field(default_factory=list)

    @property
    def submitted(self) -> list[ClassDayStatus]:
        return [s for s in self.statuses if s.submitted]

    @property
    def missing(self) -> list[ClassDayStatus]:
        return [s for s in self.statuses if not s.submitted]

    @property
    def absent_total(self) -> int | None:
        return optional_sum(s.absent for s in self.submitted)

    @property
    def sick_total(self) -> int | None:
        return optional_sum(s.sick for s in self.submitted)

    @property
    def expected(self) -> int:
        return len(self.statuses)


async def active_classes(session: AsyncSession) -> list[SchoolClass]:
    return list(
        await session.scalars(
            select(SchoolClass)
            .where(SchoolClass.is_active.is_(True))
            .order_by(SchoolClass.sort_order, SchoolClass.grade, SchoolClass.letter)
        )
    )


async def get_entry(session: AsyncSession, class_id: int, d: Date) -> MealEntry | None:
    return await session.scalar(
        select(MealEntry).where(MealEntry.class_id == class_id, MealEntry.date == d)
    )


# Поля, за якими ведеться журнал правок. Порядок визначає порядок рядків
# у журналі, коли за один виклик змінилося кілька цифр.
#
# eating_count тут немає навмисно: харчування більше не обліковується, тож
# нових рядків EATING не зʼявляється. Старі в журналі лишаються.
_AUDITED: tuple[tuple[str, MealField], ...] = (
    ("absent_count", MealField.ABSENT),
    ("sick_count", MealField.SICK),
)


def _audit_change(
    entry: MealEntry,
    attr: str,
    changed_field: MealField,
    new: int | None,
    *,
    teacher_id: int | None,
    reason: str | None,
) -> MealEntryAudit | None:
    """Застосувати одну цифру. None у `new` означає «не чіпати», а не «стерти».

    Саме тому пропуск питання нічого не записує: цифра, подана раніше,
    переживає і повторний прохід ланцюжком, і правку сусіднього поля.
    """
    if new is None:
        return None
    old = getattr(entry, attr)
    if old == new:
        return None            # повторний тап по тій самій цифрі — не правка
    setattr(entry, attr, new)
    return MealEntryAudit(
        entry_id=entry.id,
        changed_field=changed_field,
        old_value=old,
        new_value=new,
        changed_by_teacher_id=teacher_id,
        reason=reason,
    )


async def upsert_entry(
    session: AsyncSession,
    *,
    class_id: int,
    d: Date,
    absent_count: int | None = None,
    sick_count: int | None = None,
    teacher_id: int | None,
    source: EntrySource = EntrySource.TEACHER,
    reason: str | None = None,
) -> tuple[MealEntry, bool]:
    """Записати або оновити цифри дня. Повертає (запис, чи_запис_уже_існував).

    Кожне поле необовʼязкове: None — «не чіпати», а не «стерти». Кожна фактична
    зміна потрапляє в meal_entry_audit окремим рядком із назвою поля — без цього
    неможливо пояснити перевірці, яку саме цифру за минулий тиждень виправили.
    """
    values: dict[str, int | None] = {
        "absent_count": absent_count,
        "sick_count": sick_count,
    }

    entry = await get_entry(session, class_id, d)
    is_update = entry is not None
    audits: list[MealEntryAudit] = []

    if entry is None:
        if absent_count is None:
            # Запис із самих хворих не має сенсу: саме кількість відсутніх —
            # перше питання й основа звіту. Краще зрозуміла помилка, ніж
            # напівпорожній рядок, який потім нічим не пояснити.
            raise ValueError("Новий запис потребує absent_count")
        entry = MealEntry(
            class_id=class_id,
            date=d,
            absent_count=absent_count,
            entered_by_teacher_id=teacher_id,
            source=source,
        )
        session.add(entry)
        await session.flush()          # потрібен entry.id для журналу
        audits.append(
            MealEntryAudit(
                entry_id=entry.id,
                changed_field=MealField.ABSENT,
                old_value=None,
                new_value=absent_count,
                changed_by_teacher_id=teacher_id,
                reason=reason,
            )
        )
        # Відсутні вже застосовані й зажурнальовані вище; лишилися хворі,
        # яких адмін чи той самий виклик може передати разом з ними.
        pending = _AUDITED[1:]
    else:
        pending = _AUDITED

    for attr, meal_field in pending:
        row = _audit_change(
            entry, attr, meal_field, values[attr], teacher_id=teacher_id, reason=reason
        )
        if row is not None:
            audits.append(row)

    # Хворих не буває більше за відсутніх. Стеля в клавіатурі цього не
    # гарантує: вчитель може повернутися ланцюжком і ЗМЕНШИТИ відсутніх уже
    # після того, як указав хворих, а тоді пропустити другий крок — і запис
    # лишився б із «відсутні 2 · хворі 3». Тому інваріант тримаємо тут, де
    # пишуться дані, а не в кожному хендлері окремо.
    if entry.absent_count is not None and (entry.sick_count or 0) > entry.absent_count:
        clipped = _audit_change(
            entry, "sick_count", MealField.SICK, entry.absent_count,
            teacher_id=teacher_id, reason=reason or "хворих підрізано до кількості відсутніх",
        )
        if clipped is not None:
            audits.append(clipped)

    if audits:
        # Автора й джерело оновлюємо лише разом зі справжньою зміною: повторний
        # тап по тій самій цифрі не має переписувати, хто ввів запис.
        entry.entered_by_teacher_id = teacher_id
        entry.source = source
        session.add_all(audits)

    await session.flush()
    return entry, is_update


async def was_corrected(session: AsyncSession, entry_id: int) -> bool:
    """Чи цифру відсутніх уже правили після першого запису.

    Потрібне ланцюжку: на другому кроці запис уже існує, тож «запис існував»
    більше не означає «це правка». Джерело правди — журнал.
    """
    n = await session.scalar(
        select(func.count())
        .select_from(MealEntryAudit)
        .where(
            MealEntryAudit.entry_id == entry_id,
            MealEntryAudit.changed_field == MealField.ABSENT,
        )
    )
    return (n or 0) > 1


async def day_summary(session: AsyncSession, d: Date) -> DaySummary:
    """Хто здав, хто ні, і скільки всього за конкретний день."""
    classes = await active_classes(session)
    entries = {
        e.class_id: e
        for e in await session.scalars(select(MealEntry).where(MealEntry.date == d))
    }
    return DaySummary(
        date=d,
        statuses=[ClassDayStatus(school_class=c, entry=entries.get(c.id)) for c in classes],
    )


async def classes_for_teacher(session: AsyncSession, teacher_id: int) -> list[SchoolClass]:
    """Класи, закріплені за вчителем."""
    rows = await session.scalars(
        select(ClassAssignment)
        .where(ClassAssignment.teacher_id == teacher_id)
        .options(selectinload(ClassAssignment.school_class))
    )
    classes = [r.school_class for r in rows if r.school_class.is_active]
    classes.sort(key=lambda c: (c.sort_order, c.grade, c.letter))
    return classes


async def primary_teacher_ids(session: AsyncSession, class_id: int) -> list[int]:
    """Кому слати запит по цьому класу."""
    return list(
        await session.scalars(
            select(ClassAssignment.teacher_id).where(
                ClassAssignment.class_id == class_id,
                ClassAssignment.is_primary.is_(True),
            )
        )
    )
