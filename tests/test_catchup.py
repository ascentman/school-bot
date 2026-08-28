"""Догоняюча розсилка після простою — сценарій блекауту на українському хостингу."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from school_bot.config import settings
from school_bot.db.models import DayKind
from school_bot.domain.calendar import mark_range
from school_bot.domain.meals import upsert_entry
from school_bot.scheduler import jobs
from tests.conftest import MONDAY, SATURDAY, FakeBot

TZ = ZoneInfo("Europe/Kyiv")


@pytest.fixture
def at(monkeypatch):
    """Заморозити «зараз» на вказаній годині понеділка 07.09.2026."""

    def _freeze(hour: int, minute: int = 0, day=MONDAY):
        monkeypatch.setattr(
            jobs, "now", lambda: datetime.combine(day, datetime.min.time(), TZ).replace(
                hour=hour, minute=minute
            )
        )

    return _freeze


async def test_nothing_to_catch_before_first_job(bot, maker, school, at):
    at(8, 30)   # ще до 09:05
    assert await jobs.catch_up(bot, maker) == {}
    assert bot.sent == []


async def test_catches_missed_prompt(bot, maker, school, at):
    """Сервер піднявся о 09:20 — запланований о 09:05 запит cron уже не відтворить."""
    at(9, 20)
    result = await jobs.catch_up(bot, maker)
    assert result == {"prompt": 3}
    assert len(bot.sent) == 3


async def test_restart_does_not_duplicate(bot, maker, school, at):
    at(9, 20)
    await jobs.catch_up(bot, maker)
    sent_after_first = len(bot.sent)

    # Процес перезапустили ще двічі — повторів бути не має.
    await jobs.catch_up(bot, maker)
    await jobs.catch_up(bot, maker)
    assert len(bot.sent) == sent_after_first


async def test_catches_everything_after_long_blackout(bot, maker, school, at):
    """Світло зникло зранку і зʼявилося об 11:00 — догнати всі чотири розсилки."""
    at(11, 0)
    result = await jobs.catch_up(bot, maker)
    assert result == {"prompt": 3, "remind:09:30": 3, "remind:09:45": 3, "digest": 1}


async def test_partial_catch_up(bot, maker, school, at):
    """О 09:35 минули запит і перше нагадування, друге — ще ні."""
    at(9, 35)
    result = await jobs.catch_up(bot, maker)
    assert set(result) == {"prompt", "remind:09:30"}


async def test_skips_jobs_that_already_ran(bot, maker, school, at):
    at(9, 5)
    await jobs.daily_prompt(bot, maker, MONDAY)
    bot.sent.clear()

    at(11, 0)
    result = await jobs.catch_up(bot, maker)
    assert "prompt" not in result
    assert set(result) == {"remind:09:30", "remind:09:45", "digest"}


async def test_silent_on_weekend(bot, maker, school, at):
    at(11, 0, SATURDAY)
    assert await jobs.catch_up(bot, maker) == {}
    assert bot.sent == []


async def test_silent_on_vacation(bot, maker, school, at):
    async with maker() as s:
        await mark_range(s, MONDAY, MONDAY, DayKind.VACATION)
        await s.commit()
    at(11, 0)
    assert await jobs.catch_up(bot, maker) == {}


async def test_silent_after_deadline(bot, maker, school, at):
    """О 19:00 запит про сьогоднішнє харчування вже безпредметний."""
    at(19, 0)
    assert await jobs.catch_up(bot, maker) == {}
    assert bot.sent == []


async def test_deadline_boundary(bot, maker, school, at):
    at(settings.catch_up_deadline.hour, settings.catch_up_deadline.minute)
    assert await jobs.catch_up(bot, maker) != {}   # рівно о 15:00 ще працює


async def test_catch_up_respects_submitted_classes(bot, maker, school, at):
    """Клас, який устиг подати дані до відновлення, повторно не турбуємо."""
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            teacher_id=school["maria"],
        )
        await s.commit()

    at(9, 20)
    result = await jobs.catch_up(bot, maker)
    assert result == {"prompt": 2}
    assert not any("1-А" in m.text for m in bot.sent)


# --- поведінка при повній невдачі доставки --------------------------------


class DeadBot(FakeBot):
    """Жодне повідомлення не доходить — наприклад, вчитель не натиснув Start."""

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        from aiogram.exceptions import TelegramBadRequest

        raise TelegramBadRequest(method=None, message="chat not found")


async def test_total_delivery_failure_is_retried(maker, school, at):
    """Якщо не доставлено нічого, джоб не вважається виконаним.

    Інакше мережевий збій під час догоняння тихо зʼїв би розсилку на цілий день.
    """
    at(9, 20)
    dead = DeadBot()
    assert await jobs.catch_up(dead, maker) == {"prompt": 0}

    # Причину усунено (вчитель натиснув Start) — наступний старт має спрацювати.
    alive = FakeBot()
    at(9, 20)
    assert await jobs.catch_up(alive, maker) == {"prompt": 3}
    assert len(alive.sent) == 3


async def test_partial_delivery_is_marked_done(maker, school, at):
    """Один недоступний отримувач не змушує повторювати всю розсилку."""

    class FlakyBot(FakeBot):
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            from aiogram.exceptions import TelegramBadRequest

            if chat_id == 1001:
                raise TelegramBadRequest(method=None, message="chat not found")
            return await super().send_message(chat_id, text, reply_markup, **kwargs)

    at(9, 20)
    assert await jobs.catch_up(FlakyBot(), maker) == {"prompt": 1}

    at(9, 20)
    second = FakeBot()
    assert "prompt" not in await jobs.catch_up(second, maker)


async def test_no_recipients_still_marks_done(maker, school, at):
    """Немає кому слати — це виконана робота, а не невдача."""
    from school_bot.db.models import Teacher

    async with maker() as s:
        for tid in (school["maria"], school["oksana"]):
            (await s.get(Teacher, tid)).is_active = False
        await s.commit()

    at(9, 20)
    bot = FakeBot()
    assert await jobs.catch_up(bot, maker) == {"prompt": 0}

    at(9, 20)
    assert "prompt" not in await jobs.catch_up(FakeBot(), maker)
