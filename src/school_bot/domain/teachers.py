"""Масовий імпорт вчителів зі списку.

Список у школі існує в довільному вигляді — з Excel, з Viber, з паперу. Тому
розбір орієнтується не на порядок колонок, а на вміст: номер упізнається за
цифрами, клас — за шаблоном «3-Б», решта вважається іменем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.db.models import SchoolClass, Teacher
from school_bot.domain.classes import (
    CLASS_RE,
    create_classes,
    parse_class_name,
    set_teacher_classes,
)
from school_bot.domain.phones import looks_like_phone, normalize_phone

SEPARATORS = re.compile(r"[,;\t|]+")


@dataclass(slots=True)
class ParsedTeacher:
    raw: str
    name: str = ""
    phone: str | None = None
    class_names: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def parse_line(line: str) -> ParsedTeacher | None:
    """Розібрати один рядок. None — якщо рядок порожній."""
    line = line.strip()
    if not line:
        return None

    parsed = ParsedTeacher(raw=line)
    parts = [p.strip() for p in SEPARATORS.split(line) if p.strip()]

    # Розділювачів може не бути зовсім: «Коваленко Марія 0671234567 1-А».
    if len(parts) == 1:
        parts = _split_unpunctuated(parts[0])

    name_parts: list[str] = []
    for part in parts:
        if parsed.phone is None and looks_like_phone(part):
            parsed.phone = normalize_phone(part)
            continue
        if CLASS_RE.match(part) and (found := parse_class_name(part)):
            parsed.class_names.append(found[0])
            continue
        name_parts.append(part)

    parsed.name = " ".join(name_parts).strip()

    if not parsed.name:
        parsed.error = "не знайдено імені"
    elif len(parsed.name) < 3:
        parsed.error = "надто коротке імʼя"
    elif parsed.phone is None:
        parsed.error = "не знайдено номера"
    return parsed


def _split_unpunctuated(text: str) -> list[str]:
    """Витягти номер і класи з рядка без розділювачів.

    Класи витягуються ПЕРШИМИ: інакше в рядку «Ткаченко Наталія 0501234567 7-В»
    цифра класу прилипає до номера й ламає обидва поля.
    """
    parts: list[str] = []
    rest = text

    for token in re.findall(r"\b\d{1,2}\s*[-–—]\s*[А-ЯЇІЄҐA-Z]\b", rest):
        parts.append(token)
        rest = rest.replace(token, " ", 1)

    phone_match = re.search(r"\+?\d[\d\s()-]{7,16}\d", rest)
    if phone_match:
        parts.append(phone_match.group())
        rest = rest.replace(phone_match.group(), " ", 1)

    parts.append(" ".join(rest.split()))
    return [p for p in parts if p.strip()]


def parse_teacher_list(text: str) -> list[ParsedTeacher]:
    return [p for line in text.splitlines() if (p := parse_line(line)) is not None]


@dataclass(slots=True)
class ImportResult:
    created: list[ParsedTeacher] = field(default_factory=list)
    updated: list[ParsedTeacher] = field(default_factory=list)
    failed: list[ParsedTeacher] = field(default_factory=list)
    created_classes: list[str] = field(default_factory=list)

    @property
    def total_ok(self) -> int:
        return len(self.created) + len(self.updated)


async def import_teachers(
    session: AsyncSession, text: str, *, create_missing_classes: bool = True
) -> ImportResult:
    """Створити або оновити вчителів зі списку.

    Ключ зіставлення — нормалізований номер: повторний імпорт того самого файлу
    оновлює записи, а не плодить дублікати.
    """
    result = ImportResult()
    parsed = parse_teacher_list(text)

    wanted_classes = {name for p in parsed if p.ok for name in p.class_names}
    if wanted_classes and create_missing_classes:
        created, _ = await create_classes(session, ", ".join(sorted(wanted_classes)))
        result.created_classes = created

    existing_classes = await session.scalars(
        select(SchoolClass).where(SchoolClass.name.in_(wanted_classes))
    )
    by_name = {c.name: c for c in existing_classes}

    for item in parsed:
        if not item.ok:
            result.failed.append(item)
            continue

        teacher = await session.scalar(select(Teacher).where(Teacher.phone == item.phone))
        if teacher is None:
            teacher = Teacher(full_name=item.name, phone=item.phone)
            session.add(teacher)
            await session.flush()
            result.created.append(item)
        else:
            teacher.full_name = item.name
            teacher.is_active = True
            result.updated.append(item)

        if item.class_names:
            ids = {by_name[n].id for n in item.class_names if n in by_name}
            await set_teacher_classes(session, teacher.id, ids)

    await session.flush()
    return result


async def link_by_phone(session: AsyncSession, phone: str, tg_user_id: int) -> Teacher | None:
    """Привʼязати Telegram-акаунт до запису вчителя за номером.

    Так вчителю не потрібне персональне запрошення: він відкриває спільне
    посилання, ділиться контактом — і бот сам знаходить його у списку.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    teacher = await session.scalar(
        select(Teacher).where(Teacher.phone == normalized, Teacher.is_active.is_(True))
    )
    if teacher is None:
        return None

    # Номер уже привʼязаний до іншого акаунта — не перехоплюємо мовчки.
    if teacher.tg_user_id is not None and teacher.tg_user_id != tg_user_id:
        return None

    teacher.tg_user_id = tg_user_id
    teacher.invite_code = None
    await session.flush()
    return teacher
