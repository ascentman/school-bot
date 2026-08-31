"""Пам'ятка для вчителів має збігатися з тим, що бот справді показує.

Вона намальована з реальними написами кнопок. Якщо перейменувати кнопку
в боті й забути про docs/pamyatka.html, вчитель шукатиме на екрані те,
чого там немає, — а помітить це не розробник, а він.
"""

from __future__ import annotations

from datetime import date

import pytest

from school_bot.bot import keyboards
from school_bot.config import BASE_DIR, settings

GUIDE = BASE_DIR / "docs" / "pamyatka.html"


@pytest.fixture(scope="module")
def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _labels(markup) -> list[str]:
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", [])
    return [b.text for row in rows for b in row]


def test_guide_exists():
    assert GUIDE.exists(), "пам'ятку видалено — оновіть цей тест або поверніть файл"


def test_every_button_the_guide_shows_still_exists(guide: str):
    """Кожна кнопка з пам'ятки має існувати в боті під тією самою назвою."""
    shown = {
        *_labels(keyboards.share_contact()),
        *_labels(keyboards.pick_more()),
        *_labels(keyboards.edit_button(1, date(2026, 9, 1))),
        *_labels(keyboards.main_menu(is_admin=False)),
        *_labels(keyboards.number_pad(1, date(2026, 9, 1), last_known=24, max_children=40)),
    }
    # Ті, що намальовані в пам'ятці явно.
    drawn = {
        "📱 Поділитися номером",
        "➕ Ще один клас",
        "✅ Це все",
        "✏️ Виправити",
        "📋 Мої класи",
        "0 — немає",
        "✏️ Інша цифра",
    }
    missing_from_bot = drawn - shown
    assert not missing_from_bot, f"пам'ятка показує неіснуючі кнопки: {missing_from_bot}"

    missing_from_guide = [d for d in drawn if d not in guide]
    assert not missing_from_guide, f"кнопки зникли з пам'ятки: {missing_from_guide}"


def test_guide_shows_the_configured_prompt_time(guide: str):
    """Час у пам'ятці має збігатися з розкладом, а не бути вписаним колись."""
    assert f"{settings.prompt_time:%-H:%M}" in guide, (
        "у пам'ятці не той час ранкового запиту"
    )
    for t in settings.remind_times:
        assert f"{t:%-H:%M}" in guide, f"нагадування о {t:%H:%M} не згадане в пам'ятці"


def test_guide_links_to_the_bot(guide: str):
    """Посилання має бути натисним — з телефона це головний шлях у бота."""
    assert 'href="https://t.me/' in guide


def test_guide_is_a_fragment(guide: str):
    """Хостинг артефактів сам додає каркас; власні <html>/<body> ламають сторінку."""
    for tag in ("<!doctype", "<html", "<body"):
        assert tag not in guide.lower(), f"зайвий {tag} — обгортку додає хостинг"
