"""Українські назви дат. Потрібні і боту, і звітам."""

from __future__ import annotations

from datetime import date as Date

MONTHS_NOMINATIVE = (
    "січень", "лютий", "березень", "квітень", "травень", "червень",
    "липень", "серпень", "вересень", "жовтень", "листопад", "грудень",
)
MONTHS_GENITIVE = (
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
)
WEEKDAYS = ("понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя")
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")


def month_name(month: int, *, genitive: bool = False) -> str:
    return (MONTHS_GENITIVE if genitive else MONTHS_NOMINATIVE)[month - 1]


def weekday_name(d: Date, *, short: bool = False) -> str:
    return (WEEKDAYS_SHORT if short else WEEKDAYS)[d.weekday()]


def format_date(d: Date, *, with_weekday: bool = False) -> str:
    """26.08 → 'середа, 26 серпня'."""
    text = f"{d.day} {month_name(d.month, genitive=True)}"
    return f"{weekday_name(d)}, {text}" if with_weekday else text


def format_month(year: int, month: int) -> str:
    return f"{month_name(month)} {year}"


def plural_children(n: int) -> str:
    """24 → '24 дитини', 5 → '5 дітей'."""
    if n % 100 in (11, 12, 13, 14):
        word = "дітей"
    elif n % 10 == 1:
        word = "дитина"
    elif n % 10 in (2, 3, 4):
        word = "дитини"
    else:
        word = "дітей"
    return f"{n} {word}"
