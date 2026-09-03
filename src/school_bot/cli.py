"""CLI: ручний запуск джобів, наповнення демо-даними, звіти, бекап.

Потрібен, щоб перевіряти щоденний потік, не чекаючи запланованого часу.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from datetime import date as Date
from datetime import timedelta
from pathlib import Path

import typer
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from school_bot.bot.main import main
from school_bot.clock import now, today
from school_bot.config import settings
from school_bot.db.base import SessionMaker, ensure_schema
from school_bot.db.models import DayKind, MealEntry, Role, SchoolClass, Teacher
from school_bot.domain.calendar import is_school_day, mark_range
from school_bot.domain.classes import create_classes, set_teacher_classes
from school_bot.domain.meals import active_classes, day_summary, upsert_entry
from school_bot.domain.phones import format_phone
from school_bot.domain.teachers import import_teachers
from school_bot.reports import sheets
from school_bot.reports.day import build_day_report, day_report_filename
from school_bot.reports.matrix import available_months, build_month_matrix
from school_bot.reports.pdf import render_day_pdf, render_pdf
from school_bot.reports.xlsx import render_xlsx
from school_bot.scheduler import jobs

BroadcastJob = Callable[..., Awaitable[int]]

app = typer.Typer(add_completion=False, help="Керування ботом обліку харчування")


def _setup_logging() -> None:
    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(name)s — %(message)s")


def _parse_date(value: str | None) -> Date:
    if not value:
        return today()
    return Date.fromisoformat(value)


async def _make_bot() -> Bot:
    if not settings.bot_token:
        raise typer.BadParameter("BOT_TOKEN не задано у .env")
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def _run_broadcast(job: BroadcastJob, date: str | None, force: bool, label: str) -> None:
    """Спільна обгортка для prompt/remind/digest: схема запуску в них однакова."""
    _setup_logging()

    async def _go() -> None:
        await ensure_schema()
        bot = await _make_bot()
        try:
            typer.echo(f"{label}: {await job(bot, SessionMaker, _parse_date(date), force=force)}")
        finally:
            await bot.session.close()

    asyncio.run(_go())


@app.command()
def run() -> None:
    """Запустити бота (long polling)."""

    main()


@app.command()
def prompt(
    date: str = typer.Option(None, help="Дата ISO, напр. 2026-09-02. За замовчуванням — сьогодні."),
    force: bool = typer.Option(False, help="Надіслати навіть у неробочий день."),
) -> None:
    """Розіслати щоденний запит зараз."""
    _run_broadcast(jobs.daily_prompt, date, force, "Надіслано повідомлень")


@app.command()
def remind(
    date: str = typer.Option(None),
    force: bool = typer.Option(False),
) -> None:
    """Надіслати нагадування тим, хто не подав дані."""
    _run_broadcast(jobs.remind, date, force, "Надіслано нагадувань")


@app.command()
def digest(
    date: str = typer.Option(None),
    force: bool = typer.Option(False),
) -> None:
    """Надіслати зведення адміністраторам."""
    _run_broadcast(jobs.admin_digest, date, force, "Надіслано зведень")


@app.command()
def report(
    month: str = typer.Option(..., help="Місяць у форматі РРРР-ММ, напр. 2026-09"),
    fmt: str = typer.Option("xlsx", help="xlsx | pdf | both"),
    out: Path = typer.Option(Path("reports_out"), help="Куди зберегти"),
) -> None:
    """Згенерувати звіт у файл."""
    _setup_logging()
    year, mon = (int(x) for x in month.split("-"))

    async def _go() -> None:

        await ensure_schema()
        out.mkdir(parents=True, exist_ok=True)
        async with SessionMaker() as session:
            matrix = await build_month_matrix(
                session,
                year,
                mon,
                school_name=settings.school_name,
                today=today(),
            )

        stem = f"harchuvannia_{year}-{mon:02d}"
        if fmt in ("xlsx", "both"):
            path = out / f"{stem}.xlsx"
            path.write_bytes(render_xlsx(matrix))
            typer.echo(f"✔ {path}")
        if fmt in ("pdf", "both"):
            path = out / f"{stem}.pdf"
            path.write_bytes(render_pdf(matrix))
            typer.echo(f"✔ {path}")
        typer.echo(
            f"  {matrix.title}: {matrix.grand_total} порцій, "
            f"{len(matrix.elapsed_school_days)} навч. дн. минуло, "
            f"незаповнених — {matrix.missing_total}"
        )

    asyncio.run(_go())


@app.command("day-report")
def day_report(
    date: str = typer.Option(None, help="Дата РРРР-ММ-ДД. Без параметра — сьогодні."),
    out: Path = typer.Option(Path("reports_out"), help="Куди зберегти"),
) -> None:
    """Згенерувати PDF-звіт за день у файл."""
    _setup_logging()
    d = _parse_date(date)      # без параметра — сьогодні

    async def _go() -> None:
        await ensure_schema()
        out.mkdir(parents=True, exist_ok=True)
        async with SessionMaker() as session:
            report = await build_day_report(
                session, d, school_name=settings.school_name, slots=settings.meal_slots
            )

        path = out / day_report_filename(d)
        path.write_bytes(render_day_pdf(report))
        typer.echo(f"✔ {path}")
        typer.echo(
            f"  {d}: {report.total} порцій, подали {report.submitted} з {report.expected}"
            + (f", не подали — {', '.join(report.missing)}" if report.missing else "")
        )

    asyncio.run(_go())


@app.command("sync-sheets")
def sync_sheets(
    month: str = typer.Option(None, help="РРРР-ММ. Без параметра — усі місяці."),
) -> None:
    """Перебудувати вкладки Google Sheets із БД."""
    _setup_logging()

    async def _go() -> None:

        if not settings.sheets_enabled:
            typer.echo("Google Sheets вимкнено: задайте GOOGLE_CREDENTIALS_FILE і GOOGLE_SHEET_ID.")
            raise typer.Exit(1)

        await ensure_schema()
        async with SessionMaker() as session:
            targets = (
                [(int(month[:4]), int(month[5:7]))] if month else await available_months(session)
            )
            now = today()
            matrices = [
                await build_month_matrix(session, y, m, school_name=settings.school_name, today=now)
                for y, m in targets
            ]

        for matrix in matrices:
            url = await sheets.safe_rebuild_month(matrix)
            typer.echo(f"✔ {matrix.title} → {url or 'помилка'}")
        if matrices:
            await sheets.sync_summary(matrices)
            typer.echo("✔ Зведення")

    asyncio.run(_go())


@app.command()
def seed() -> None:
    """Наповнити БД демо-даними для перевірки (3 класи, 2 вчителі, місяць записів)."""
    _setup_logging()

    async def _go() -> None:
        import random

        await ensure_schema()
        async with SessionMaker() as session:
            created, _ = await create_classes(session, "1-А, 3-Б, 5-В")
            typer.echo(f"Класи: {created or 'вже існували'}")

            classes = await active_classes(session)
            teachers = []
            for name, role in [
                ("Коваленко Марія Іванівна", Role.TEACHER),
                ("Шевчук Оксана Петрівна", Role.ADMIN),
            ]:
                t = Teacher(full_name=name, role=role, invite_code=f"demo-{len(teachers)}")
                session.add(t)
                teachers.append(t)
            await session.flush()

            await set_teacher_classes(session, teachers[0].id, {classes[0].id, classes[1].id})
            await set_teacher_classes(session, teachers[1].id, {classes[2].id})

            start_of_today = today()
            first = start_of_today.replace(day=1)
            await mark_range(
                session, first + timedelta(days=13), first + timedelta(days=15),
                DayKind.VACATION, "Демо-канікули",
            )

            rng = random.Random(42)
            base = {c.id: rng.randint(18, 28) for c in classes}
            filled = 0
            cursor = first
            while cursor <= start_of_today:
                if await is_school_day(session, cursor):
                    for c in classes:
                        # один клас навмисне лишаємо з пропусками — щоб було видно
                        # червону заливку в звіті
                        if c.name == "5-В" and cursor.day % 3 == 0:
                            continue
                        await upsert_entry(
                            session,
                            class_id=c.id,
                            d=cursor,
                            eating_count=max(0, base[c.id] + rng.randint(-3, 3)),
                            teacher_id=teachers[0].id,
                        )
                        filled += 1
                cursor += timedelta(days=1)

            await session.commit()
            typer.echo(f"Записів створено: {filled}")
            typer.echo("Запрошення: /start inv_demo-0 (вчитель), inv_demo-1 (адмін)")

    asyncio.run(_go())


@app.command("import-teachers")
def import_teachers_cmd(
    file: Path = typer.Argument(..., help="Текстовий файл або CSV: імʼя, телефон, класи"),
) -> None:
    """Завантажити вчителів зі списку у файлі."""
    _setup_logging()

    async def _go() -> None:

        if not file.exists():
            typer.echo(f"Файл не знайдено: {file}")
            raise typer.Exit(1)

        await ensure_schema()
        async with SessionMaker() as session:
            result = await import_teachers(session, file.read_text(encoding="utf-8"))
            await session.commit()

        for item in result.created:
            typer.echo(f"➕ {item.name} — {format_phone(item.phone)} {item.class_names}")
        for item in result.updated:
            typer.echo(f"🔄 {item.name} — {format_phone(item.phone)} {item.class_names}")
        for item in result.failed:
            typer.echo(f"⚠️ не розібрано ({item.error}): {item.raw}")
        if result.created_classes:
            typer.echo(f"🏫 створено класів: {', '.join(result.created_classes)}")
        typer.echo(f"\nВсього опрацьовано: {result.total_ok}, помилок: {len(result.failed)}")

    asyncio.run(_go())


@app.command()
def link(
    tg_id: int = typer.Option(..., help="Telegram ID (дізнатися у @userinfobot)"),
    name: str = typer.Option(..., help="ПІБ"),
    classes: str = typer.Option("", help="Класи через кому, напр. '1-А,3-Б'"),
    admin: bool = typer.Option(False, help="Дати права адміністратора"),
) -> None:
    """Створити або оновити вчителя напряму, без інвайт-посилання.

    Потрібно у двох випадках: щоб швидко налаштувати себе для тестування, і як
    запасний шлях, якщо вчитель загубив запрошення.
    """
    _setup_logging()

    async def _go() -> None:
        from sqlalchemy import select

        await ensure_schema()
        async with SessionMaker() as session:
            teacher = await session.scalar(select(Teacher).where(Teacher.tg_user_id == tg_id))
            if teacher is None:
                teacher = Teacher(tg_user_id=tg_id, full_name=name)
                session.add(teacher)
                action = "створено"
            else:
                teacher.full_name = name
                action = "оновлено"
            teacher.is_active = True
            teacher.role = Role.ADMIN if admin else Role.TEACHER
            await session.flush()

            wanted = [c.strip() for c in classes.split(",") if c.strip()]
            if wanted:
                # Створити ті класи, яких ще немає, і закріпити всі за вчителем.
                await create_classes(session, classes)
                rows = list(
                    await session.scalars(select(SchoolClass).where(SchoolClass.name.in_(wanted)))
                )
                missing = set(wanted) - {r.name for r in rows}
                if missing:
                    typer.echo(f"⚠️ Не вдалося розпізнати: {', '.join(sorted(missing))}")
                await set_teacher_classes(session, teacher.id, {r.id for r in rows})
                bound = ", ".join(r.name for r in rows)
            else:
                bound = "—"

            await session.commit()
            role = "адміністратор" if admin else "вчитель"
            typer.echo(f"✔ {name} — {action} ({role})")
            typer.echo(f"  Telegram ID: {tg_id}")
            typer.echo(f"  Класи:       {bound}")

    asyncio.run(_go())


@app.command()
def backup(out: Path = typer.Option(Path("data/backups"), help="Куди складати копії")) -> None:
    """Зробити копію SQLite-бази."""
    prefix = "sqlite+aiosqlite:///"
    if not settings.database_url.startswith(prefix):
        typer.echo("Бекап реалізовано лише для SQLite.")
        raise typer.Exit(1)

    src = Path(settings.database_url[len(prefix) :])
    if not src.exists():
        typer.echo(f"База не знайдена: {src}")
        raise typer.Exit(1)

    out.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M")
    dest = out / f"school-{stamp}.db"
    shutil.copy2(src, dest)
    typer.echo(f"✔ {dest} ({dest.stat().st_size // 1024} КБ)")

    # Тримаємо останні 30 копій.
    copies = sorted(out.glob("school-*.db"))
    for old in copies[:-30]:
        old.unlink()


@app.command()
def status() -> None:
    """Показати поточний стан: класи, вчителі, останні записи."""
    _setup_logging()

    async def _go() -> None:
        from sqlalchemy import func, select

        await ensure_schema()
        async with SessionMaker() as session:
            classes = await active_classes(session)
            n_teachers = await session.scalar(
                select(func.count()).select_from(Teacher).where(Teacher.is_active.is_(True))
            )
            n_entries = await session.scalar(select(func.count()).select_from(MealEntry))
            d = today()

            typer.echo(f"База:      {settings.database_url}")
            typer.echo(f"Школа:     {settings.school_name}")
            typer.echo(f"Класів:    {len(classes)} ({', '.join(c.name for c in classes) or '—'})")
            typer.echo(f"Вчителів:  {n_teachers}")
            typer.echo(f"Записів:   {n_entries}")
            typer.echo(f"Sheets:    {'увімкнено' if settings.sheets_enabled else 'вимкнено'}")
            typer.echo("")

            if await is_school_day(session, d):
                summary = await day_summary(session, d)
                typer.echo(
                    f"Сьогодні ({d}): подали {len(summary.submitted)}/{summary.expected}, "
                    f"разом {summary.total}"
                )
                if summary.missing:
                    typer.echo(
                        "  не подали: "
                        + ", ".join(x.school_class.name for x in summary.missing)
                    )
            else:
                typer.echo(f"Сьогодні ({d}) — не навчальний день.")

    asyncio.run(_go())


if __name__ == "__main__":
    app()
