"""ORM-моделі (SQLAlchemy 2.0, typed)."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Довжина ПІБ. Відколи зʼявилася самореєстрація, його задає будь-хто, а SQLite
# довжину VARCHAR не перевіряє — тож межа потрібна на вході, і саме тут, поруч
# з колонкою, щоб вони не розʼїхалися.
MAX_NAME_LEN = 200
MIN_NAME_LEN = 3


class Role(StrEnum):
    TEACHER = "teacher"
    ADMIN = "admin"


class EntrySource(StrEnum):
    TEACHER = "teacher"
    ADMIN = "admin"
    IMPORT = "import"


class MealField(StrEnum):
    """Яку саме цифру запису змінили. Потрібне журналу правок.

    До появи відсутніх/хворих цифра була одна, тож журнал її не називав.
    Тепер без назви поля неможливо відповісти, що саме виправили.
    """

    EATING = "eating"
    ABSENT = "absent"
    SICK = "sick"


UA_MEAL_FIELD = {
    MealField.EATING: "харчування",
    MealField.ABSENT: "відсутні",
    MealField.SICK: "хворі",
}


class DayKind(StrEnum):
    HOLIDAY = "holiday"      # державне свято
    VACATION = "vacation"    # канікули
    REMOTE = "remote"        # дистанційне навчання без харчування
    OTHER = "other"


UA_DAY_KIND = {
    DayKind.HOLIDAY: "свято",
    DayKind.VACATION: "канікули",
    DayKind.REMOTE: "дистанційно",
    DayKind.OTHER: "інше",
}


class Teacher(Base):
    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(MAX_NAME_LEN))
    # Нормалізований номер (380671234567) — ключ, за яким вчитель привʼязує
    # свій Telegram, поділившись контактом. Unique не дає двом записам
    # претендувати на один номер.
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    role: Mapped[Role] = mapped_column(sa.Enum(Role, native_enum=False), default=Role.TEACHER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    invite_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    assignments: Mapped[list[ClassAssignment]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_linked(self) -> bool:
        return self.tg_user_id is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Teacher {self.id} {self.full_name!r} {self.role.value}>"


class SchoolClass(Base):
    __tablename__ = "school_class"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(16), unique=True)  # "3-Б"
    grade: Mapped[int] = mapped_column(Integer)                 # 3
    letter: Mapped[str] = mapped_column(String(4), default="")  # "Б"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    assignments: Mapped[list[ClassAssignment]] = relationship(
        back_populates="school_class", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SchoolClass {self.name}>"


class ClassAssignment(Base):
    """Звʼязок вчитель↔клас. Many-to-many — один вчитель може вести кілька класів."""

    __tablename__ = "class_assignment"
    __table_args__ = (UniqueConstraint("class_id", "teacher_id", name="uq_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_class.id", ondelete="CASCADE"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id", ondelete="CASCADE"))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)

    school_class: Mapped[SchoolClass] = relationship(back_populates="assignments")
    teacher: Mapped[Teacher] = relationship(back_populates="assignments")


class MealEntry(Base):
    """Одна цифра на клас на день. UNIQUE гарантує відсутність дублів."""

    __tablename__ = "meal_entry"
    __table_args__ = (UniqueConstraint("class_id", "date", name="uq_entry_class_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("school_class.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[Date] = mapped_column(sa.Date, index=True)
    eating_count: Mapped[int] = mapped_column(Integer)
    present_count: Mapped[int | None] = mapped_column(Integer)
    # NULL — не питали або вчитель пропустив питання; 0 — відсутніх справді
    # немає. Зливати ці два стани не можна: до цієї фічі даних не було взагалі,
    # і звіт за травень не має стверджувати, що тоді ніхто не хворів.
    absent_count: Mapped[int | None] = mapped_column(Integer)
    sick_count: Mapped[int | None] = mapped_column(Integer)
    entered_by_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL")
    )
    source: Mapped[EntrySource] = mapped_column(
        sa.Enum(EntrySource, native_enum=False), default=EntrySource.TEACHER
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    school_class: Mapped[SchoolClass] = relationship()
    entered_by: Mapped[Teacher | None] = relationship()


class MealEntryAudit(Base):
    """Лог правок. Критично для перевірки: видно хто, коли й що змінив."""

    __tablename__ = "meal_entry_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("meal_entry.id", ondelete="CASCADE"), index=True
    )
    # Назва саме changed_field, а не field: у domain/meals.py уже імпортовано
    # dataclasses.field, і поруч там будуються ці ж рядки журналу.
    #
    # server_default — рядок "EATING", а не "eating": sa.Enum(native_enum=False)
    # зберігає ІМʼЯ члена enum, а не значення (у проді source лежить як
    # "TEACHER"). З малими літерами наявні рядки не читалися б назад.
    changed_field: Mapped[MealField] = mapped_column(
        sa.Enum(MealField, native_enum=False),
        default=MealField.EATING,
        server_default=MealField.EATING.name,
    )
    old_value: Mapped[int | None] = mapped_column(Integer)
    new_value: Mapped[int] = mapped_column(Integer)
    changed_by_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL")
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reason: Mapped[str | None] = mapped_column(Text)


class NonSchoolDay(Base):
    """Дні, коли бот мовчить: свята, канікули, дистанційка."""

    __tablename__ = "non_school_day"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[Date] = mapped_column(sa.Date, unique=True, index=True)
    kind: Mapped[DayKind] = mapped_column(
        sa.Enum(DayKind, native_enum=False), default=DayKind.HOLIDAY
    )
    note: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    """Key/value для налаштувань, які змінюються з бота без перезапуску."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
