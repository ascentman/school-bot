"""Нормалізація телефонних номерів.

Потрібна, бо той самий номер записують як «067 123-45-67», «+380671234567»,
«0671234567», а Telegram віддає «380671234567». Без зведення до єдиного вигляду
зіставити вчителя зі списком неможливо.
"""

from __future__ import annotations

import re

UA_CODE = "380"
UA_OPERATOR_LEN = 9  # 67 123 45 67 без ведучого нуля


def normalize_phone(raw: str | None) -> str | None:
    """Звести до вигляду 380671234567. Повертає None, якщо це не схоже на номер.

    Іноземні номери приймаються як є (10–15 цифр), бо серед працівників школи
    можуть бути люди з не-українськими номерами.
    """
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    # 0671234567 → 380671234567
    if len(digits) == UA_OPERATOR_LEN + 1 and digits.startswith("0"):
        return UA_CODE + digits[1:]

    # 671234567 → 380671234567
    if len(digits) == UA_OPERATOR_LEN:
        return UA_CODE + digits

    # 380671234567 / 00380671234567
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == len(UA_CODE) + UA_OPERATOR_LEN and digits.startswith(UA_CODE):
        return digits

    # Іноземний номер — лишаємо як є, якщо довжина правдоподібна.
    if 10 <= len(digits) <= 15:
        return digits

    return None


def format_phone(normalized: str | None) -> str:
    """380671234567 → +38 (067) 123-45-67. Для показу людині."""
    if not normalized:
        return "—"
    if normalized.startswith(UA_CODE) and len(normalized) == len(UA_CODE) + UA_OPERATOR_LEN:
        n = normalized[len(UA_CODE) :]          # 671234567
        return f"+38 (0{n[:2]}) {n[2:5]}-{n[5:7]}-{n[7:]}"
    return "+" + normalized


def looks_like_phone(raw: str) -> bool:
    """Чи схожий фрагмент рядка на номер — для розбору списку вчителів."""
    digits = re.sub(r"\D", "", raw)
    letters = re.sub(r"[^А-Яа-яЇїІіЄєҐґA-Za-z]", "", raw)
    return len(digits) >= UA_OPERATOR_LEN and len(digits) > len(letters)
