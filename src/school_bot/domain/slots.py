"""Розклад роздачі: о котрій годині який клас іде до їдальні.

Потрібен лише звітності: цифри збираються однаково для всіх класів, але
перевірка читає їх по змінах — саме в тому порядку, в якому діти сідають за
столи. Тому розклад живе в конфігу (MEAL_SLOTS), а не в базі: він змінюється
разом із розкладом уроків, раз на півріччя, і правити його має бути так само
просто, як список класів.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time as Time

from school_bot.domain.classes import parse_class_name

# "08:45-09:00", "08:45 – 09:00"
SLOT_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True, slots=True)
class MealSlot:
    """Одна зміна: проміжок часу і класи, що харчуються в ньому."""

    start: Time
    end: Time
    class_names: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M} – {self.end:%H:%M}"


def _parse_time(hour: str, minute: str, chunk: str) -> Time:
    try:
        return Time(int(hour), int(minute))
    except ValueError as e:
        raise ValueError(f"MEAL_SLOTS: недійсний час у «{chunk.strip()}»") from e


def parse_meal_slots(raw: str) -> list[MealSlot]:
    """Розібрати MEAL_SLOTS: «08:45-09:00 = 3-А, 3-Б; 09:15-09:30 = 6-А, 10-Б».

    Роздільник між змінами — «;» або перенос рядка, тож розклад можна записати
    і в один рядок (безпечно для docker compose env_file), і стовпчиком.

    Помилку не ковтаємо: мовчазно проігнорований рядок означав би клас, який
    зник зі звіту, — а помітили б це вже на перевірці.
    """
    slots: list[MealSlot] = []
    seen: dict[str, str] = {}

    for chunk in re.split(r"[;\n]+", raw):
        if not chunk.strip():
            continue
        head, sep, tail = chunk.partition("=")
        if not sep:
            raise ValueError(
                f"MEAL_SLOTS: очікую «ЧЧ:ХХ-ЧЧ:ХХ = класи», отримав «{chunk.strip()}»"
            )

        m = SLOT_RE.match(head)
        if not m:
            raise ValueError(f"MEAL_SLOTS: не розумію час «{head.strip()}»")
        start = _parse_time(m.group(1), m.group(2), chunk)
        end = _parse_time(m.group(3), m.group(4), chunk)
        if end <= start:
            raise ValueError(f"MEAL_SLOTS: кінець не пізніше початку в «{head.strip()}»")

        names: list[str] = []
        for part in tail.split(","):
            if not part.strip():
                continue
            parsed = parse_class_name(part)
            if parsed is None:
                raise ValueError(f"MEAL_SLOTS: не розпізнано клас «{part.strip()}»")
            name = parsed[0]
            # Клас у двох змінах — це або одрук, або справжня двозначність;
            # у звіті він однаково дав би подвоєну суму.
            if name in seen:
                raise ValueError(f"MEAL_SLOTS: клас {name} вказано двічі ({seen[name]})")
            seen[name] = head.strip()
            names.append(name)

        if not names:
            raise ValueError(f"MEAL_SLOTS: зміна «{head.strip()}» без жодного класу")
        slots.append(MealSlot(start=start, end=end, class_names=tuple(names)))

    slots.sort(key=lambda s: (s.start, s.end))
    return slots
