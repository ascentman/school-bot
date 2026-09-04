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
from school_bot.reports.day import (
    UA_REPORT_KIND,
    DayReport,
    ReportKind,
    day_report_filename,
)

log = logging.getLogger(__name__)


def subject_for(report: DayReport, kind: ReportKind = ReportKind.MEALS) -> str:
    day = f"{format_date(report.date)} {report.date.year}"
    school = f" — {report.school_name}" if report.school_name else ""
    return f"{UA_REPORT_KIND[kind]} за {day}{school}"


def body_for(report: DayReport, kind: ReportKind = ReportKind.MEALS) -> str:
    """Головні цифри — у тілі листа.

    Щоб відповісти «скільки сьогодні?», не має бути потреби відкривати вкладення
    з телефона: сам PDF потрібен уже тоді, коли його треба роздрукувати.
    """
    def num(v: int | None) -> str:
        return "—" if v is None else str(v)

    meals = kind is ReportKind.MEALS
    lines = [
        f"{format_date(report.date, with_weekday=True)} {report.date.year} р.",
        "",
    ]
    if meals:
        lines.append(f"Разом на харчуванні: {plural_children(report.total)}")
    else:
        lines.append(f"Всього відсутніх: {num(report.absent_total)}")
        lines.append(f"З них по хворобі: {num(report.sick_total)}")
    lines.append(f"Подали дані: {report.submitted} з {report.expected} класів")
    # Однаково для обох звітів: клас, що не подав нічого, у сумі не врахований.
    if report.missing:
        lines += ["", "Не подали: " + ", ".join(report.missing)]

    lines += ["", "Деталі за змінами роздачі — у PDF у вкладенні.", ""]
    for group in report.groups:
        if group.label:
            if meals:
                total = group.total if group.has_data else "—"
            else:
                total = f"{num(group.absent_total)} / {num(group.sick_total)}"
            lines.append(f"{group.label}   {total}")
        for cell in group.cells:
            if meals:
                lines.append(f"    {cell.name}: {num(cell.count)}")
            else:
                lines.append(
                    f"    {cell.name}: відсутні {num(cell.absent)}"
                    f" · хворі {num(cell.sick)}"
                )

    lines += ["", "—", "Надіслано ботом обліку харчування автоматично."]
    return "\n".join(lines)


def build_message(
    report: DayReport,
    pdf: bytes,
    *,
    sender: str,
    recipients: Sequence[str],
    kind: ReportKind = ReportKind.MEALS,
) -> EmailMessage:
    """Зібрати лист із PDF у вкладенні. Без мережі — тому легко тестується."""
    msg = EmailMessage()
    msg["Subject"] = subject_for(report, kind)
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_for(report, kind))
    msg.add_attachment(
        pdf,
        maintype="application",
        subtype="pdf",
        filename=day_report_filename(report.date, kind),
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


async def send_day_report(
    report: DayReport, pdf: bytes, *, kind: ReportKind = ReportKind.MEALS
) -> bool:
    """Надіслати звіт за день на REPORT_EMAILS. Повертає, чи вийшло."""
    if not settings.email_enabled:
        log.debug("Пошта не налаштована — лист не надсилаю")
        return False

    msg = build_message(
        report,
        pdf,
        sender=settings.mail_from,
        recipients=settings.report_emails,
        kind=kind,
    )
    await asyncio.to_thread(_send_sync, msg)
    log.info(
        "Звіт за %s надіслано на %s", report.date, ", ".join(settings.report_emails)
    )
    return True


async def safe_send_day_report(
    report: DayReport, pdf: bytes, *, kind: ReportKind = ReportKind.MEALS
) -> bool:
    """Те саме, але без винятків назовні.

    Пошта — третій канал після тексту в Telegram і файлу там же. Недоступний
    SMTP не має коштувати зведення: інакше збій у Gmail лишав би адмінів без
    повідомлення, хоча всі дані на місці.
    """
    try:
        return await send_day_report(report, pdf, kind=kind)
    except Exception:
        log.exception("Не вдалося надіслати звіт за %s поштою", report.date)
        return False
