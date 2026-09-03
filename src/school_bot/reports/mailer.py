"""Надсилання щоденного звіту поштою.

Другий канал доставки поруч із Telegram: перевірка частіше просить «скиньте на
пошту», ніж заходить у месенджер. Канал необовʼязковий — без SMTP-налаштувань
бот працює як раніше, лист просто не йде.

Блокуючий smtplib загорнутий у `asyncio.to_thread` — так само, як gspread у
`sheets.py`: тягнути ще одну залежність заради одного листа на добу немає сенсу.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from collections.abc import Sequence
from email.message import EmailMessage

from school_bot.config import settings
from school_bot.domain.dates import format_date, plural_children
from school_bot.reports.day import DayReport, day_report_filename

log = logging.getLogger(__name__)


def subject_for(report: DayReport) -> str:
    day = f"{format_date(report.date)} {report.date.year}"
    school = f" — {report.school_name}" if report.school_name else ""
    return f"Облік харчування за {day}{school}"


def body_for(report: DayReport) -> str:
    """Головні цифри — у тілі листа.

    Щоб відповісти «скільки сьогодні?», не має бути потреби відкривати вкладення
    з телефона: сам PDF потрібен уже тоді, коли його треба роздрукувати.
    """
    def num(v: int | None) -> str:
        return "—" if v is None else str(v)

    lines = [
        f"{format_date(report.date, with_weekday=True)} {report.date.year} р.",
        "",
        f"Разом на харчуванні: {plural_children(report.total)}",
        f"Всього відсутніх: {num(report.absent_total)}"
        f" · з них по хворобі: {num(report.sick_total)}",
        f"Подали дані: {report.submitted} з {report.expected} класів",
    ]
    if report.missing:
        lines += ["", "Не подали: " + ", ".join(report.missing)]

    lines += ["", "Деталі за змінами роздачі — у PDF у вкладенні.", ""]
    for group in report.groups:
        if group.label:
            total = group.total if group.has_data else "—"
            lines.append(f"{group.label}   {total}")
        for cell in group.cells:
            # Дописуємо до наявного рядка, а не перебудовуємо його: «1-А: 17»
            # лишається на місці, тож і звичка читача, і тести не ламаються.
            lines.append(
                f"    {cell.name}: {num(cell.count)}"
                f" · відсутні {num(cell.absent)} · хворі {num(cell.sick)}"
            )

    lines += ["", "—", "Надіслано ботом обліку харчування автоматично."]
    return "\n".join(lines)


def build_message(
    report: DayReport,
    pdf: bytes,
    *,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    """Зібрати лист із PDF у вкладенні. Без мережі — тому легко тестується."""
    msg = EmailMessage()
    msg["Subject"] = subject_for(report)
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_for(report))
    msg.add_attachment(
        pdf,
        maintype="application",
        subtype="pdf",
        filename=day_report_filename(report.date),
    )
    return msg


def _send_sync(msg: EmailMessage) -> None:
    """Власне відправка. Виконується в окремому потоці."""
    # Таймаут обовʼязковий: за замовчуванням smtplib чекає вічно, і зависла
    # відправка тримала б потік до перезапуску процесу.
    if settings.smtp_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
        )
    else:
        server = smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
        )
    with server:
        if not settings.smtp_ssl:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


async def send_day_report(report: DayReport, pdf: bytes) -> bool:
    """Надіслати звіт за день на REPORT_EMAILS. Повертає, чи вийшло."""
    if not settings.email_enabled:
        log.debug("Пошта не налаштована — лист не надсилаю")
        return False

    msg = build_message(
        report,
        pdf,
        sender=settings.mail_from,
        recipients=settings.report_emails,
    )
    await asyncio.to_thread(_send_sync, msg)
    log.info(
        "Звіт за %s надіслано на %s", report.date, ", ".join(settings.report_emails)
    )
    return True


async def safe_send_day_report(report: DayReport, pdf: bytes) -> bool:
    """Те саме, але без винятків назовні.

    Пошта — третій канал після тексту в Telegram і файлу там же. Недоступний
    SMTP не має коштувати зведення: інакше збій у Gmail лишав би адмінів без
    повідомлення, хоча всі дані на місці.
    """
    try:
        return await send_day_report(report, pdf)
    except Exception:
        log.exception("Не вдалося надіслати звіт за %s поштою", report.date)
        return False
