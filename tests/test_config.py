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
    assert settings.remind_times == [time(9, 15), time(9, 30)]
    assert settings.digest_time == time(9, 45)


def test_schedule_is_chronological():
    """Нагадування мають іти після запиту, зведення — після нагадувань."""
    assert all(t > settings.prompt_time for t in settings.remind_times)
    assert settings.digest_time >= max(settings.remind_times)
    assert settings.catch_up_deadline > settings.digest_time


def test_remind_times_parsed_from_env_string():
    s = Settings(remind_times="09:30, 09:15")
    assert s.remind_times == [time(9, 15), time(9, 30)]


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
    assert s.remind_times == [time(9, 15), time(9, 30)]
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


PAYLOAD = '<a href="https://example.com">клік</a>'


def _synth(annotation, name: str):
    """Підібрати аргумент за анотацією; рядки — з HTML-payload."""
    from datetime import date

    origin = getattr(annotation, "__origin__", None)
    if annotation is str:
        return PAYLOAD
    if annotation is int:
        return 3
    if annotation is bool:
        return False
    if annotation is date:
        return date(2026, 9, 1)
    if origin is list:
        return [PAYLOAD]
    return PAYLOAD


def test_every_texts_function_escapes_its_input():
    """Прогнати HTML-payload через УСІ функції texts, а не через обраний список.

    Точковий перелік уже тричі пропускав неекрановані місця: спершу
    prompt_answered, потім classes_list, потім classes_added.
    """
    import inspect

    from school_bot.bot import texts as texts_module

    checked = []
    for fname, fn in vars(texts_module).items():
        if fname.startswith("_") or fname == "esc" or not inspect.isfunction(fn):
            continue
        if fn.__module__ != texts_module.__name__:
            continue

        hints = inspect.get_annotations(fn, eval_str=True)
        try:
            kwargs = {
                pname: _synth(hints.get(pname, str), pname)
                for pname in inspect.signature(fn).parameters
            }
            rendered = fn(**kwargs)
        except Exception:  # noqa: BLE001 — функції з нерядковими аргументами пропускаємо
            continue

        if isinstance(rendered, str):
            checked.append(fname)
            assert "<a href=" not in rendered, f"texts.{fname} не екранує ввід"

    assert len(checked) >= 8, f"перевірено замало функцій: {checked}"


def test_user_controlled_text_is_escaped():
    """Кожен текст, у який підставляється введене людиною, має екрануватися.

    Бот працює в parse_mode=HTML, а ПІБ відтоді, як зʼявилася самореєстрація,
    задає будь-хто: «<» валить відповідь, а тег перетворює список вчителів
    на клікабельне посилання в чаті адміністратора.
    """
    from datetime import date

    from school_bot.bot import texts

    payload = '<a href="https://example.com">клік</a>'
    rendered = [
        texts.welcome(payload, ["1-А"], False),
        texts.teacher_disabled(payload),
        texts.class_disabled(payload),
        texts.teacher_edit_classes(payload),
        texts.teacher_classes_saved(payload, ["1-А"]),
        texts.invite_created(payload, "https://t.me/bot"),
        texts.import_preview(created=0, updated=0, failed=[payload], classes=[]),
        texts.prompt_answered(payload, date(2026, 9, 1), 20, "09:05"),
    ]
    for text in rendered:
        assert "<a href=" not in text, f"неекранований HTML у: {text[:70]}"


def test_escape_keeps_ordinary_names_readable():
    from school_bot.bot import texts

    assert "Коваленко Марія Іванівна" in texts.welcome("Коваленко Марія Іванівна", [], False)
    assert texts.esc("Оʼ Коннор") == "Оʼ Коннор"
