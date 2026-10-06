"""Тести щоденних джобів із фейковим ботом — без мережі й без Telegram."""

from __future__ import annotations

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


async def test_prompt_asks_about_absences(bot, maker, school):
    """Єдине питання запиту — скільки відсутніх; харчування не згадується."""
    await jobs.daily_prompt(bot, maker, MONDAY)
    text = bot.to(1001)[0].text
    assert "відсутн" in text.lower()
    assert "харчу" not in text.lower()


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
            s, class_id=school["classes"][0], d=MONDAY, absent_count=2,
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


async def test_prompt_keyboard_starts_at_zero_without_a_hint(bot, maker, school):
    """Кількість відсутніх скаче день у день, тож «як минулого разу» тут немає.

    Натомість 0 стоїть першим: це найчастіша відповідь, і вона має бути
    доступна одним дотиком.
    """
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY.replace(day=4), absent_count=7,
            teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.daily_prompt(bot, maker, MONDAY)
    kb = bot.to(1001)[0].markup
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels[0] == "0"
    assert not any("минулого разу" in label for label in labels)
    assert "✏️ Інша цифра" in labels


async def test_prompt_keyboard_has_no_skip_button(bot, maker, school):
    """Пропустити перше питання означало б не подати нічого."""
    await jobs.daily_prompt(bot, maker, MONDAY)
    kb = bot.to(1001)[0].markup
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Пропустити" in label for label in labels)


# --- remind ---------------------------------------------------------------
#
# Нагадування вимкнені в конфігу (REMIND_TIMES порожній), але сам механізм
# лишається: щоб повернути їх, досить вписати час у .env.


async def test_remind_only_to_those_who_did_not_answer(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY, absent_count=2,
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
                s, class_id=class_id, d=MONDAY, absent_count=1, teacher_id=school["maria"]
            )
        await s.commit()
    assert await jobs.remind(bot, maker, MONDAY) == 0


async def test_remind_silent_on_weekend(bot, maker, school):
    assert await jobs.remind(bot, maker, SATURDAY) == 0


async def test_no_reminders_are_scheduled_by_default(bot, maker, school):
    """Школа просила рівно одне повідомлення на день."""
    keys = [k for k, _, _ in jobs.daily_plan()]
    assert not any(k.startswith("remind") for k in keys)


# --- щоденний звіт --------------------------------------------------------


async def test_report_goes_only_to_admins(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY,
            absent_count=2, teacher_id=school["maria"],
        )
        await s.commit()

    assert await jobs.day_report(bot, maker, MONDAY) == 1
    assert bot.to(1001) == []          # вчителю звіти не йдуть
    assert bot.to(2002)


async def test_report_shows_absent_and_sick(bot, maker, school):
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY,
            absent_count=3, sick_count=1, teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.day_report(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "Відсутніх" in text and "3" in text
    assert "харчу" not in text.lower()


async def test_report_names_the_classes_that_did_not_submit(bot, maker, school):
    """Медсестра має бачити, що дані неповні, а не лише підсумкову цифру."""
    async with maker() as s:
        await upsert_entry(
            s, class_id=school["classes"][0], d=MONDAY,
            absent_count=2, sick_count=1, teacher_id=school["maria"],
        )
        await s.commit()

    await jobs.day_report(bot, maker, MONDAY)
    text = bot.to(2002)[0].text
    assert "Не подали" in text and "3-Б" in text and "5-В" in text


async def test_report_attaches_the_pdf(bot, maker, school):
    await jobs.day_report(bot, maker, MONDAY)
    assert [d.filename for d in bot.docs_to(2002)] == ["vidsutni_2026-09-07.pdf"]
    assert bot.docs_to(1001) == []


async def test_report_survives_a_failing_render(bot, maker, school, monkeypatch):
    """Збій рендеру коштує файл, а не весь джоб."""
    def boom(report):
        raise RuntimeError("ReportLab не зміг")

    monkeypatch.setattr(jobs, "render_day_report", boom)

    assert await jobs.day_report(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text          # текст усе одно дійшов
    assert bot.documents == []


async def test_report_survives_a_failing_send(maker, school):
    class NoDocumentsBot(FakeBot):
        async def send_document(self, chat_id, document, **kwargs):
            raise RuntimeError("Telegram відмовив")

    bot = NoDocumentsBot()
    assert await jobs.day_report(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text


async def test_report_is_marked_as_run(bot, maker, school):
    await jobs.day_report(bot, maker, MONDAY)
    async with maker() as s:
        assert await jobs.has_run(s, "report", MONDAY)


async def test_report_is_in_the_daily_plan_after_the_prompt():
    plan = jobs.daily_plan()
    keys = [k for k, _, _ in plan]
    assert keys == ["prompt", "report"]

    times = {k: t for k, t, _ in plan}
    assert times["prompt"] < times["report"], "звіт не має йти раніше запиту"


async def test_broken_render_costs_the_attachment_not_the_digest(maker, school, monkeypatch):
    """Кнопка «Сьогодні»: зламаний рендер не має зривати саме зведення."""
    from school_bot.domain.meals import day_summary

    def boom(report):
        raise RuntimeError("ReportLab не зміг")

    monkeypatch.setattr(jobs, "render_day_report", boom)

    async with maker() as s:
        summary = await day_summary(s, MONDAY)
    assert jobs.day_report_attachment(summary) is None


async def test_attachment_is_the_same_file_as_the_morning_report(maker, school):
    """Кнопка має показувати те, що людина вже звикла бачити вранці."""
    from school_bot.domain.meals import day_summary

    async with maker() as s:
        summary = await day_summary(s, MONDAY)
    document = jobs.day_report_attachment(summary)
    assert document is not None
    assert document.filename == "vidsutni_2026-09-07.pdf"


async def test_report_tells_admins_when_there_are_no_classes(bot, maker, school):
    """Жодного активного класу — це помилка налаштування, і її має бути видно."""
    from sqlalchemy import update

    from school_bot.db.models import SchoolClass

    async with maker() as s:
        await s.execute(update(SchoolClass).values(is_active=False))
        await s.commit()

    assert await jobs.day_report(bot, maker, MONDAY) == 0
    assert bot.to(2002), "адмін мав отримати попередження, а не тишу"

    async with maker() as s:
        assert await jobs.has_run(s, "report", MONDAY)


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
