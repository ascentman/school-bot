"""Перевірки конфігу, які ловлять помилки без запуску бота."""

from __future__ import annotations

import ast
from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from school_bot.config import BASE_DIR, Settings, settings

SRC = BASE_DIR / "src" / "school_bot"


def _settings_attrs_used() -> set[tuple[str, str, int]]:
    """Усі звернення виду `settings.X` у вихідному коді."""
    found: set[tuple[str, str, int]] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
            ):
                found.add((node.attr, str(path.relative_to(BASE_DIR)), node.lineno))
    return found


def test_every_settings_attribute_exists():
    """Ловить осиротілі посилання після перейменування полів конфігу.

    Меню «Налаштування» відкривають раз на місяць, тож звернення до неіснуючого
    поля може прожити в коді довго й впасти в найгірший момент.
    """
    known = set(Settings.model_fields) | {
        name for name in dir(Settings) if not name.startswith("_")
    }
    broken = [
        f"{path}:{line} — settings.{attr}"
        for attr, path, line in sorted(_settings_attrs_used())
        if attr not in known
    ]
    assert not broken, "Звернення до неіснуючих полів конфігу:\n" + "\n".join(broken)


def test_defaults_match_agreed_schedule():
    assert settings.prompt_time == time(9, 5)
    assert settings.remind_times == [time(9, 30), time(9, 45)]
    assert settings.digest_time == time(10, 0)


def test_schedule_is_chronological():
    """Нагадування мають іти після запиту, зведення — після нагадувань."""
    assert all(t > settings.prompt_time for t in settings.remind_times)
    assert settings.digest_time >= max(settings.remind_times)
    assert settings.catch_up_deadline > settings.digest_time


def test_remind_times_parsed_from_env_string():
    s = Settings(remind_times="09:45, 09:30")
    assert s.remind_times == [time(9, 30), time(9, 45)]


def test_remind_times_deduplicated():
    assert Settings(remind_times="09:30,09:30").remind_times == [time(9, 30)]


def test_remind_times_can_be_empty():
    assert Settings(remind_times="").remind_times == []


def test_bootstrap_admins_parsed_from_env_string():
    assert Settings(bootstrap_admins="111, 222").bootstrap_admins == [111, 222]


def test_env_example_parses(tmp_path: Path):
    """`.env.example` має бути робочим файлом, а не лише документацією.

    Inline-коментарі в ньому не повинні потрапляти у значення.
    """
    example = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(example, encoding="utf-8")

    s = Settings(_env_file=env)
    assert s.prompt_time == time(9, 5)
    assert s.remind_times == [time(9, 30), time(9, 45)]
    assert s.catch_up_deadline == time(15, 0)
    assert s.misfire_grace_seconds == 7200
    assert s.school_name and "#" not in s.school_name


@pytest.mark.parametrize("bad", ["не час", "25:00"])
def test_invalid_time_is_rejected(bad: str):
    with pytest.raises(ValidationError):
        Settings(remind_times=bad)


def test_no_hardcoded_schedule_in_user_texts():
    """Час у текстах має братися з конфігу, інакше він розійдеться з розкладом.

    Вчитель прочитає «о 08:00», а запит прийде о 09:05 — і повірить тексту.
    """
    import re

    texts_src = (SRC / "bot" / "texts.py").read_text(encoding="utf-8")
    # Прибираємо f-рядки з підстановкою — вони саме те, що треба.
    without_interpolation = re.sub(r"\{[^}]*\}", "", texts_src)
    hardcoded = re.findall(r"\b([01]?\d|2[0-3]):[0-5]\d\b", without_interpolation)
    assert not hardcoded, f"У текстах зашитий час: {hardcoded}"
