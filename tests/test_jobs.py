"""Тести щоденних джобів із фейковим ботом — без мережі й без Telegram."""

from __future__ import annotations

from datetime import date

from school_bot.db.models import DayKind, SchoolClass, Teacher
from school_bot.domain.calendar import mark_range
from school_bot.domain.meals import upsert_entry
from school_bot.scheduler import jobs
from tests.conftest import MONDAY, SATURDAY, FakeBot

# --- daily_prompt ---------------------------------------------------------


async def test_prompt_sends_one_message_per_class(bot, maker, school):
    assert await jobs.daily_prompt(bot, maker, MONDAY) == 3
    assert len(bot.to(1001)) == 2      # Марія веде два класи — два окремі запити
    assert len(bot.to(2002)) == 1
    assert "1-А" in bot.to(1001)[0].text
    assert "3-Б" in bot.to(1001)[1].text


async def test_prompt_silent_on_weekend(bot, maker, school):
    assert await jobs.daily_prompt(bot, maker, SATURDAY) == 0
    assert bot.sent == []


async def test_prompt_silent_on_vacation(bot, maker, school):
    async with maker() as s:
        await mark_range(s, MONDAY, MONDAY, DayKind.VACATION)
        await s.commit()
    assert await jobs.daily_prompt(bot, maker, MONDAY) == 0


async def test_force_overrides_calendar(bot, maker, school):
    assert await jobs.daily_prompt(bot, maker, SATURDAY, force=True) == 3


async def test_prompt_skips_class_that_already_submitted(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            teacher_id=school["maria"],
        )
        await s.commit()

    assert await jobs.daily_prompt(bot, maker, MONDAY) == 2
    assert not any("1-А" in m.text for m in bot.sent)


async def test_prompt_skips_class_without_teacher(bot, maker, school):
    async with maker() as s:
        s.add(SchoolClass(name="9-А", grade=9, letter="А", sort_order=9))
        await s.commit()
    assert await jobs.daily_prompt(bot, maker, MONDAY) == 3   # 9-А без керівника — пропущено


async def test_prompt_skips_inactive_teacher(bot, maker, school):
    async with maker() as s:
        teacher = await s.get(Teacher, school["maria"])
        teacher.is_active = False
        await s.commit()
    assert await jobs.daily_prompt(bot, maker, MONDAY) == 1
    assert bot.to(1001) == []


async def test_prompt_keyboard_centres_on_last_value(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=date(2026, 9, 4), eating_count=27,
            teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.daily_prompt(bot, maker, MONDAY)
    kb = bot.to(1001)[0].markup
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "↩︎ Як минулого разу: 27" in labels
    assert "27" in labels


# --- remind ---------------------------------------------------------------


async def test_remind_only_to_those_who_did_not_answer(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            teacher_id=school["maria"],
        )
        await s.commit()

    assert await jobs.remind(bot, maker, MONDAY) == 2
    texts_sent = [m.text for m in bot.sent]
    assert all("Нагадування" in t for t in texts_sent)
    assert not any("1-А" in t for t in texts_sent)


async def test_remind_silent_when_everyone_answered(bot, maker, school):
    async with maker() as s:
        for class_id in school["classes"]:
            await upsert_entry(
                s, class_id=class_id, d=MONDAY, eating_count=20, teacher_id=school["maria"]
            )
        await s.commit()
    assert await jobs.remind(bot, maker, MONDAY) == 0


async def test_remind_silent_on_weekend(bot, maker, school):
    assert await jobs.remind(bot, maker, SATURDAY) == 0


# --- admin_digest ---------------------------------------------------------


async def test_digest_goes_only_to_admins(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            teacher_id=school["maria"],
        )
        await s.commit()

    assert await jobs.admin_digest(bot, maker, MONDAY) == 1
    assert bot.to(1001) == []
    text = bot.to(2002)[0].text
    assert "Подали: <b>1</b> з 3" in text
    assert "3-Б" in text and "5-В" in text


async def test_digest_reports_total(bot, maker, school):
    async with maker() as s:
        for class_id, n in zip(school["classes"], (24, 18, 20), strict=True):
            await upsert_entry(
                s, class_id=class_id, d=MONDAY, eating_count=n, teacher_id=school["maria"]
            )
        await s.commit()

    await jobs.admin_digest(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "62" in text
    assert "Усі класи подали дані" in text
    assert bot.to(2002)[0].markup is None      # немає боржників — немає кнопок


async def test_digest_offers_buttons_for_missing_classes(bot, maker, school):
    await jobs.admin_digest(bot, maker, MONDAY)
    kb = bot.to(2002)[0].markup
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == ["1-А", "3-Б", "5-В"]


# --- стійкість ------------------------------------------------------------


async def test_blocked_user_does_not_break_broadcast(maker, school):
    from aiogram.exceptions import TelegramForbiddenError

    class BlockingBot(FakeBot):
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            if chat_id == 1001:
                raise TelegramForbiddenError(method=None, message="bot was blocked")
            return await super().send_message(chat_id, text, reply_markup, **kwargs)

    bot = BlockingBot()
    # Марія заблокувала бота, але Оксана має отримати свій запит.
    assert await jobs.daily_prompt(bot, maker, MONDAY) == 1
    assert len(bot.to(2002)) == 1
