"""Тести щоденних джобів із фейковим ботом — без мережі й без Telegram."""

from __future__ import annotations

from datetime import date

import pytest

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


# --- щоденні звіти --------------------------------------------------------
#
# Два окремі звіти замість одного зведення: харчування о 09:40 (його чекає
# кухня), відсутні о 09:50. Тіло в них спільне, тож більшість перевірок
# ганяємо по обох одразу.


REPORTS = [jobs.meals_report, jobs.absence_report]


@pytest.mark.parametrize("job", REPORTS)
async def test_report_goes_only_to_admins(bot, maker, school, job):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            absent_count=2, teacher_id=school["maria"],
        )
        await s.commit()

    assert await job(bot, maker, MONDAY) == 1
    assert bot.to(1001) == []          # вчителю звіти не йдуть
    assert bot.to(2002)


async def test_meals_report_names_the_classes_that_did_not_submit(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.meals_report(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "3-Б" in text and "5-В" in text


async def test_absence_report_shows_absent_and_sick(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            absent_count=3, sick_count=1, teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.absence_report(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "Відсутніх" in text and "3" in text


@pytest.mark.parametrize(
    "job,fname",
    [(jobs.meals_report, "harchuvannia_2026-09-07.pdf"),
     (jobs.absence_report, "vidsutni_2026-09-07.pdf")],
)
async def test_each_report_attaches_its_own_file(bot, maker, school, job, fname):
    await job(bot, maker, MONDAY)
    assert [d.filename for d in bot.docs_to(2002)] == [fname]
    assert bot.docs_to(1001) == []


@pytest.mark.parametrize("job", REPORTS)
async def test_report_survives_a_failing_render(bot, maker, school, monkeypatch, job):
    """Збій рендеру коштує файл, а не весь джоб."""
    def boom(report, kind):
        raise RuntimeError("ReportLab не зміг")

    monkeypatch.setattr(jobs, "render_day_report", boom)

    assert await job(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text          # текст усе одно дійшов
    assert bot.documents == []


@pytest.mark.parametrize("job", REPORTS)
async def test_report_survives_a_failing_send(maker, school, job):
    class NoDocumentsBot(FakeBot):
        async def send_document(self, chat_id, document, **kwargs):
            raise RuntimeError("Telegram відмовив")

    bot = NoDocumentsBot()
    assert await job(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text


async def test_reports_are_marked_separately(bot, maker, school):
    """Два джоби — два маркери, інакше догоняння пропустило б один зі звітів."""
    await jobs.meals_report(bot, maker, MONDAY)

    async with maker() as s:
        assert await jobs.has_run(s, "report:meals", MONDAY)
        assert not await jobs.has_run(s, "report:absence", MONDAY)

    await jobs.absence_report(bot, maker, MONDAY)
    async with maker() as s:
        assert await jobs.has_run(s, "report:absence", MONDAY)


async def test_both_reports_are_in_the_daily_plan():
    keys = [k for k, _, _ in jobs.daily_plan()]
    assert "report:meals" in keys and "report:absence" in keys
    times = {k: t for k, t, _ in jobs.daily_plan()}
    assert times["report:meals"] < times["report:absence"]


async def test_reports_email_their_own_kind(bot, maker, school, monkeypatch):
    """Кожен звіт іде поштою під своїм видом, а не обидва як харчування."""
    from school_bot.reports import mailer
    from school_bot.reports.day import ReportKind

    posted: list = []

    async def fake_send(report, pdf, *, kind=ReportKind.MEALS):
        posted.append(kind)
        return True

    monkeypatch.setattr(mailer, "safe_send_day_report", fake_send)
    await jobs.meals_report(bot, maker, MONDAY)
    await jobs.absence_report(bot, maker, MONDAY)

    assert posted == [ReportKind.MEALS, ReportKind.ABSENCE]


async def test_reports_do_not_email_when_channel_is_off(bot, maker, school):
    from school_bot.config import settings

    assert not settings.email_enabled
    assert await jobs.meals_report(bot, maker, MONDAY) == 1


async def test_one_broken_render_does_not_lose_the_other_report(maker, school, monkeypatch):
    """Кнопка «Сьогодні»: зламаний звіт має коштувати свій файл, а не обидва.

    Знайдено на рев'ю PR #13 — обидва рендери стояли під одним try/except,
    тож збій другого забирав з собою вже готовий перший.
    """
    from school_bot.domain.meals import day_summary
    from school_bot.reports.day import ReportKind

    real = jobs.render_day_report

    def half_broken(report, kind):
        if kind is ReportKind.ABSENCE:
            raise RuntimeError("ReportLab не зміг")
        return real(report, kind)

    monkeypatch.setattr(jobs, "render_day_report", half_broken)

    async with maker() as s:
        summary = await day_summary(s, MONDAY)
    docs = jobs.day_report_attachments(summary)

    assert [d.filename for d in docs] == ["harchuvannia_2026-09-07.pdf"]


async def test_report_tells_admins_when_there_are_no_classes(bot, maker, school):
    """Жодного активного класу — це помилка налаштування, і її має бути видно."""
    from sqlalchemy import update

    from school_bot.db.models import SchoolClass

    async with maker() as s:
        await s.execute(update(SchoolClass).values(is_active=False))
        await s.commit()

    assert await jobs.meals_report(bot, maker, MONDAY) == 0
    assert bot.to(2002), "адмін мав отримати попередження, а не тишу"

    async with maker() as s:
        assert await jobs.has_run(s, "report:meals", MONDAY)


async def test_absence_report_also_names_missing_classes(bot, maker, school):
    """Медсестра має бачити, що дані неповні, а не лише підсумкову цифру."""
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, eating_count=24,
            absent_count=2, sick_count=1, teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.absence_report(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "Не подали" in text and "3-Б" in text


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
