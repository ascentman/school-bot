"""Розсилка звіту поштою — без мережі.

Мережеву частину (`_send_sync`) свідомо не тестуємо: там нема нашої логіки,
лише виклики smtplib. Тестуємо те, що ламається насправді, — склад листа,
умови вмикання каналу і те, що недоступний SMTP не забирає з собою зведення.
"""

from __future__ import annotations

from datetime import date

import pytest

from school_bot.config import Settings
from school_bot.db.models import MealEntry, SchoolClass
from school_bot.domain.meals import ClassDayStatus, DaySummary
from school_bot.domain.slots import parse_meal_slots
from school_bot.reports import mailer
from school_bot.reports.day import build_report

DAY = date(2026, 9, 3)
SCHEDULE = "08:45-09:00 = 3-Б; 09:45-10:00 = 1-А"


def _report():
    statuses = [
        ClassDayStatus(
            school_class=SchoolClass(name="1-А", grade=1, letter="А"),
            entry=MealEntry(date=DAY, eating_count=17),
        ),
        ClassDayStatus(school_class=SchoolClass(name="3-Б", grade=3, letter="Б"), entry=None),
    ]
    return build_report(
        DaySummary(date=DAY, statuses=statuses),
        school_name="44 Школа",
        slots=parse_meal_slots(SCHEDULE),
    )


# --- склад листа -----------------------------------------------------------


def test_message_carries_the_pdf_as_an_attachment():
    msg = mailer.build_message(
        _report(), b"%PDF-1.4 fake", sender="bot@school.ua", recipients=["a@b.ua", "c@d.ua"]
    )

    assert msg["To"] == "a@b.ua, c@d.ua"
    assert msg["From"] == "bot@school.ua"

    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "harchuvannia_2026-09-03.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_subject_names_the_day_and_the_school():
    subject = mailer.subject_for(_report())
    assert "3 вересня" in subject and "2026" in subject
    assert "44 Школа" in subject


def test_body_answers_the_question_without_opening_the_pdf():
    """Головні цифри — у тілі: відкривати вкладення з телефона незручно."""
    body = mailer.body_for(_report())

    assert "Разом на харчуванні: 17 дітей" in body
    assert "Подали дані: 1 з 2" in body
    assert "Не подали: 3-Б" in body
    assert "08:45 – 09:00" in body      # групування збережене й у тексті
    assert "1-А: 17" in body


def test_absence_body_reports_absences():
    """Лист про відсутніх говорить про відсутніх, а не про порції."""
    from school_bot.reports.day import ReportKind

    body = mailer.body_for(_report(), ReportKind.ABSENCE)
    assert "Всього відсутніх:" in body
    assert "З них по хворобі:" in body
    assert "Разом на харчуванні" not in body


def test_meals_body_stays_about_meals():
    from school_bot.reports.day import ReportKind

    body = mailer.body_for(_report(), ReportKind.MEALS)
    assert "Разом на харчуванні: 17 дітей" in body
    assert "Всього відсутніх:" not in body


def test_subject_and_filename_follow_the_kind():
    from school_bot.reports.day import ReportKind

    rep = _report()
    assert "Харчування" in mailer.subject_for(rep, ReportKind.MEALS)
    assert "Відсутні" in mailer.subject_for(rep, ReportKind.ABSENCE)

    msg = mailer.build_message(rep, b"%PDF", sender="a@b.ua", recipients=["c@d.ua"],
                               kind=ReportKind.ABSENCE)
    assert next(msg.iter_attachments()).get_filename() == "vidsutni_2026-09-03.pdf"


def test_body_shows_a_dash_for_a_class_that_did_not_submit():
    """Пропуск і нуль лишаються різними речами й у листі."""
    assert "3-Б: —" in mailer.body_for(_report())


# --- умови вмикання --------------------------------------------------------


def test_channel_is_off_until_fully_configured():
    assert not Settings().email_enabled
    assert not Settings(smtp_user="a@b.ua", smtp_password="p").email_enabled  # нема адресатів
    assert not Settings(report_emails="a@b.ua").email_enabled                 # нема логіна
    assert Settings(
        smtp_user="a@b.ua", smtp_password="p", report_emails="c@d.ua"
    ).email_enabled


def test_app_password_survives_being_copied_with_spaces():
    """Google показує «пароль додатка» групами по чотири, через НЕРОЗРИВНІ
    пробіли. Скопійований як є, він валив авторизацію з UnicodeEncodeError
    ('ascii' codec can't encode character '\xa0') — тобто помилка виглядала як
    завгодно, тільки не як «приберіть пробіли». Спіймано на реальному .env.

    Значення тут навмисно НЕ у форматі справжнього пароля Google (16 малих
    літер): інакше сканер секретів справедливо чіплявся б до кожного коміту.
    Для перевірки очистки важлива лише наявність пробілів, а не форма.
    """
    expected = "TEST0pass1word2"
    assert Settings(smtp_password="TEST0 pass1 word2").smtp_password == expected
    assert Settings(smtp_password="TEST0\xa0pass1\xa0word2").smtp_password == expected
    assert Settings(smtp_password="  TEST0pass1word2\n").smtp_password == expected


def test_from_falls_back_to_the_login():
    assert Settings(smtp_user="a@b.ua").mail_from == "a@b.ua"
    assert Settings(smtp_user="a@b.ua", smtp_from="zvit@school.ua").mail_from == "zvit@school.ua"


@pytest.mark.asyncio
async def test_nothing_is_sent_when_the_channel_is_off(monkeypatch):
    """Без налаштувань лист не йде — і це не помилка."""
    sent: list[object] = []
    monkeypatch.setattr(mailer, "_send_sync", lambda msg: sent.append(msg))

    assert await mailer.send_day_report(_report(), b"pdf") is False
    assert sent == []


@pytest.mark.asyncio
async def test_smtp_failure_does_not_escape_the_safe_wrapper(monkeypatch):
    """Недоступний SMTP не має коштувати зведення в Telegram."""
    def boom(msg):
        raise OSError("SMTP недоступний")

    monkeypatch.setattr(mailer.settings, "smtp_user", "a@b.ua")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "report_emails", ["c@d.ua"])
    monkeypatch.setattr(mailer, "_send_sync", boom)

    assert await mailer.safe_send_day_report(_report(), b"pdf") is False


@pytest.mark.asyncio
async def test_configured_channel_actually_sends(monkeypatch):
    sent: list[object] = []
    monkeypatch.setattr(mailer.settings, "smtp_user", "bot@school.ua")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "report_emails", ["dyrektor@school.ua"])
    monkeypatch.setattr(mailer, "_send_sync", lambda msg: sent.append(msg))

    assert await mailer.send_day_report(_report(), b"%PDF") is True
    assert len(sent) == 1
    assert sent[0]["To"] == "dyrektor@school.ua"


# --- сам виклик smtplib ----------------------------------------------------
#
# Мережі тут немає, але гілка STARTTLS/SSL і порядок викликів — наша логіка,
# і помилка в ній виявиться лише в проді, коли лист не піде.


class _RecordingSMTP:
    instances: list[_RecordingSMTP] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        _RecordingSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")

    def send_message(self, msg):
        self.calls.append("send")


@pytest.fixture
def smtp_double(monkeypatch):
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _RecordingSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _RecordingSMTP)
    monkeypatch.setattr(mailer.settings, "smtp_user", "bot@school.ua")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "report_emails", ["dyrektor@school.ua"])
    return _RecordingSMTP


def test_starttls_path_upgrades_before_logging_in(smtp_double, monkeypatch):
    """Пароль не має піти у відкритий канал — STARTTLS строго до login."""
    monkeypatch.setattr(mailer.settings, "smtp_ssl", False)
    mailer._send_sync(mailer.build_message(_report(), b"%PDF", sender="a@b.ua",
                                           recipients=["c@d.ua"]))

    server = smtp_double.instances[0]
    assert server.calls == ["starttls", "login:bot@school.ua", "send", "quit"]


def test_ssl_path_does_not_call_starttls(smtp_double, monkeypatch):
    """На 465 канал уже шифрований — STARTTLS там помилка, а не зайвий крок."""
    monkeypatch.setattr(mailer.settings, "smtp_ssl", True)
    mailer._send_sync(mailer.build_message(_report(), b"%PDF", sender="a@b.ua",
                                           recipients=["c@d.ua"]))

    assert "starttls" not in smtp_double.instances[0].calls


def test_connection_always_has_a_timeout(smtp_double, monkeypatch):
    """Без таймауту зависла відправка тримала б потік до перезапуску процесу."""
    monkeypatch.setattr(mailer.settings, "smtp_ssl", False)
    mailer._send_sync(mailer.build_message(_report(), b"%PDF", sender="a@b.ua",
                                           recipients=["c@d.ua"]))

    assert smtp_double.instances[0].timeout == mailer.settings.smtp_timeout


def test_both_reports_name_who_did_not_submit():
    """Клас без запису не входить у суму — про це має сказати кожен звіт.

    Знайдено на рев'ю PR #13: звіт про відсутніх мовчки показував «Відсутніх: 2»,
    хоч один клас не подав нічого й реальна цифра могла бути більшою.
    """
    from school_bot.reports.day import ReportKind

    for kind in (ReportKind.MEALS, ReportKind.ABSENCE):
        body = mailer.body_for(_report(), kind)
        assert "Не подали: 3-Б" in body, f"{kind.value}: немає списку боржників"
        assert "Подали дані: 1 з 2" in body
