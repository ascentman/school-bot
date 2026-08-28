from __future__ import annotations

import pytest

from school_bot.domain.phones import format_phone, looks_like_phone, normalize_phone


@pytest.mark.parametrize(
    "raw",
    [
        "0671234567",
        "+380671234567",
        "380671234567",
        "067 123 45 67",
        "067-123-45-67",
        "(067) 123-45-67",
        "+38 067 123 45 67",
        "00380671234567",
        "671234567",
        " 067 123 45 67 ",
    ],
)
def test_all_ukrainian_forms_normalize_identically(raw: str):
    assert normalize_phone(raw) == "380671234567"


def test_foreign_number_kept_as_is():
    assert normalize_phone("+48 123 456 789") == "48123456789"


@pytest.mark.parametrize("bad", ["", None, "не номер", "12", "абв", "1234567890123456789"])
def test_rejects_non_numbers(bad):
    assert normalize_phone(bad) is None


def test_format_for_display():
    assert format_phone("380671234567") == "+38 (067) 123-45-67"
    assert format_phone(None) == "—"
    assert format_phone("48123456789") == "+48123456789"


@pytest.mark.parametrize("raw", ["0671234567", "+380 67 123 45 67", "067-123-45-67"])
def test_looks_like_phone_true(raw: str):
    assert looks_like_phone(raw)


@pytest.mark.parametrize("raw", ["Коваленко Марія Іванівна", "1-А", "3-Б", ""])
def test_looks_like_phone_false(raw: str):
    assert not looks_like_phone(raw)
