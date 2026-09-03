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


async def test_digest_attaches_the_day_pdf(bot, maker, school):
    """Зведення о 09:35 приходить разом із PDF за той самий день."""
    await jobs.admin_digest(bot, maker, MONDAY)

    docs = bot.docs_to(2002)
    assert [d.filename for d in docs] == ["harchuvannia_2026-09-07.pdf"]
    assert bot.docs_to(1001) == []      # вчителю звіт не йде


async def test_digest_survives_a_failing_pdf_send(maker, school):
    """Файл не дійшов — зведення все одно вважається виконаним.

    Головні цифри вже в тексті; якщо через збій на файлі джоб лишався б
    непозначеним, наступний старт розіслав би зведення вдруге.
    """
    class NoDocumentsBot(FakeBot):
        async def send_document(self, chat_id, document, **kwargs):
            raise RuntimeError("Telegram відмовив")

    bot = NoDocumentsBot()
    assert await jobs.admin_digest(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text

    async with maker() as s:
        assert await jobs.has_run(s, "digest", MONDAY)


async def test_digest_survives_a_failing_pdf_render(bot, maker, school, monkeypatch):
    """Збій рендеру коштує файл, а не все зведення.

    До появи PDF текстове зведення від нього не залежало взагалі. Якщо виняток
    з ReportLab вилетить до розсилки, адміни того дня не отримають нічого —
    саме цього й не має статися.
    """
    def boom(report):
        raise RuntimeError("ReportLab не зміг")

    monkeypatch.setattr(jobs, "render_day_pdf", boom)

    assert await jobs.admin_digest(bot, maker, MONDAY) == 1
    assert "Подали:" in bot.to(2002)[0].text     # текст усе одно дійшов
    assert bot.documents == []                   # а файла просто немає

    async with maker() as s:
        assert await jobs.has_run(s, "digest", MONDAY)


async def test_digest_reads_the_day_only_once(bot, maker, school, monkeypatch):
    """Текст і вкладення будуються з одного знімка даних.

    Два окремі запити до БД лишали вікно: вчитель встигає надіслати цифру між
    ними, і повідомлення та файл за той самий день показують різні числа.
    """
    from school_bot.reports import day as day_report

    calls: list[object] = []
    real = jobs.day_summary

    async def counting(session, d):
        summary = await real(session, d)
        calls.append(summary)
        return summary

    # Обидва модулі імпортували day_summary поіменно, тож рахувати треба в
    # кожному: підміна лише в одному пропустила б звернення з іншого.
    monkeypatch.setattr(jobs, "day_summary", counting)
    monkeypatch.setattr(day_report, "day_summary", counting)

    await jobs.admin_digest(bot, maker, MONDAY)

    assert len(calls) == 1, "день має читатися рівно раз на зведення"
    assert bot.docs_to(2002)                     # і файл усе одно надіслано


async def test_digest_emails_the_same_pdf_it_sends_to_telegram(bot, maker, school, monkeypatch):
    """Один рендер на обидва канали — інакше вони можуть розійтися."""
    from school_bot.reports import mailer

    posted: list[tuple] = []

    async def fake_send(report, pdf):
        posted.append((report, pdf))
        return True

    monkeypatch.setattr(mailer, "safe_send_day_report", fake_send)
    await jobs.admin_digest(bot, maker, MONDAY)

    assert len(posted) == 1
    report, pdf = posted[0]
    assert report.date == MONDAY
    assert pdf.startswith(b"%PDF")


async def test_digest_survives_a_dead_smtp(bot, maker, school, monkeypatch):
    """Недоступна пошта не має забирати з собою зведення в Telegram.

    Канал увімкнено, але сервер не відповідає — саме той стан, у якому опиниться
    прод, якщо Gmail відхилить пароль або впаде мережа.
    """
    from school_bot.reports import mailer

    def dead(msg):
        raise OSError("SMTP недоступний")

    monkeypatch.setattr(mailer.settings, "smtp_user", "bot@school.ua")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "report_emails", ["dyrektor@school.ua"])
    monkeypatch.setattr(mailer, "_send_sync", dead)

    assert await jobs.admin_digest(bot, maker, MONDAY) == 1
    assert bot.to(2002)[0].text      # текст дійшов
    assert bot.docs_to(2002)         # і файл у Telegram теж

    async with maker() as s:
        assert await jobs.has_run(s, "digest", MONDAY)


async def test_digest_does_not_email_when_channel_is_off(bot, maker, school):
    """У тестах SMTP не налаштований — і жодних спроб надсилати немає."""
    from school_bot.config import settings

    assert not settings.email_enabled
    assert await jobs.admin_digest(bot, maker, MONDAY) == 1


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
