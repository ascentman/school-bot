"""Щоденні джоби: запит, нагадування, зведення адміну, нічний синк.

Час кожного задається в конфігу (PROMPT_TIME, REMIND_TIMES, DIGEST_TIME).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date as Date
from datetime import time as Time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from school_bot.bot import keyboards, texts
from school_bot.clock import now, today
from school_bot.config import settings
from school_bot.db.models import AppSetting, Role, SchoolClass, Teacher
from school_bot.domain.calendar import is_school_day
from school_bot.domain.meals import (
    DaySummary,
    active_classes,
    day_summary,
    get_entry,
    last_known_count,
    primary_teacher_ids,
)
from school_bot.reports import mailer, sheets
from school_bot.reports.day import (
    ReportKind,
    build_report,
    day_report_filename,
)
from school_bot.reports.matrix import available_months, build_month_matrices
from school_bot.reports.pdf import render_day_report

log = logging.getLogger(__name__)

# Джоб денного розкладу: (bot, session_maker, дата) -> скільки надіслано.
DailyJob = Callable[..., Awaitable[int]]

# Пауза між повідомленнями: ~20 за секунду — у межах ліміту Telegram.
SEND_PAUSE = 0.05

# Пауза між вкладками Google Sheets: квота ~60 записів на хвилину.
SHEETS_PAUSE = 1.2


# --- журнал запусків -------------------------------------------------------
#
# Потрібен, щоб після простою (блекаут, перезапуск) догнати пропущену розсилку
# рівно один раз, а не щоразу, коли процес піднімається.


def _run_key(job: str) -> str:
    return f"last_run:{job}"


async def has_run(session: AsyncSession, job: str, d: Date) -> bool:
    value = await session.scalar(select(AppSetting.value).where(AppSetting.key == _run_key(job)))
    return value == d.isoformat()


def _should_mark(attempted: int, sent: int) -> bool:
    """Чи вважати джоб виконаним.

    Позначаємо, якщо роботи не було (нема кому слати) або хоч одне повідомлення
    дійшло. Якщо ж спроби були і всі провалилися — маркер НЕ ставимо, щоб
    наступний старт спробував ще раз. Інакше мережевий збій під час догоняння
    тихо зʼїдав би розсилку на цілий день.
    """
    return attempted == 0 or sent > 0


async def mark_run(session: AsyncSession, job: str, d: Date) -> None:
    key = _run_key(job)
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=d.isoformat()))
    else:
        row.value = d.isoformat()
    await session.flush()


async def _send(
    bot: Bot, chat_id: int, text: str, who: str = "", _retries: int = 2, **kwargs
) -> bool:
    """Надіслати з обробкою типових помилок Telegram.

    Заблокований бот, ненатиснутий /start чи флуд-контроль не повинні зривати
    всю розсилку — вони стосуються одного отримувача, а не решти класів.
    """
    label = f"{who} ({chat_id})" if who else str(chat_id)
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramRetryAfter as e:
        # Обмежена кількість спроб: без неї стійкий флуд-контроль зациклив би
        # розсилку й затримав усі інші класи.
        if _retries <= 0:
            log.error("%s: флуд-контроль не минув, здаюся", label)
            return False
        log.warning("Флуд-контроль, чекаю %s с", e.retry_after)
        await asyncio.sleep(e.retry_after)
        return await _send(bot, chat_id, text, who, _retries - 1, **kwargs)
    except TelegramForbiddenError:
        log.warning("%s заблокував бота", label)
        return False
    except TelegramBadRequest as e:
        # Telegram не дозволяє боту писати першим: поки людина не натисне Start,
        # чату не існує. Це нормальний стан нового вчителя, а не збій, тому
        # ніякого traceback — лише зрозуміла підказка, що робити.
        if "chat not found" in str(e).lower():
            log.warning("%s ще не відкрив бота — попросіть натиснути Start", label)
        else:
            log.error("Не вдалося надіслати до %s: %s", label, e)
        return False
    except Exception:
        log.exception("Не вдалося надіслати повідомлення до %s", label)
        return False


async def _send_document(bot: Bot, chat_id: int, document: BufferedInputFile) -> bool:
    """Надіслати файл. Невдача не має зривати зведення.

    Текст зведення вже дійшов і містить головні цифри; PDF — зручність поверх
    нього. Тому помилку логуємо, але джоб вважається виконаним.
    """
    try:
        await bot.send_document(chat_id, document)
        return True
    except TelegramRetryAfter as e:
        log.warning("Флуд-контроль на файлі, чекаю %s с", e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            await bot.send_document(chat_id, document)
            return True
        except Exception:
            log.exception("Не вдалося надіслати PDF до %s", chat_id)
            return False
    except Exception:
        log.exception("Не вдалося надіслати PDF до %s", chat_id)
        return False


def day_report_attachments(summary: DaySummary) -> list[BufferedInputFile]:
    """Обидва щоденні звіти як вкладення Telegram.

    Кожен рендериться окремо: збій одного має коштувати саме той файл, а не
    обидва. Інакше зламаний звіт про відсутніх забирав би з собою вже готовий
    звіт про харчування — а його чекає кухня.
    """
    if not summary.statuses:
        return []

    try:
        report = build_report(
            summary, school_name=settings.school_name, slots=settings.meal_slots
        )
    except Exception:
        log.exception("Не вдалося зібрати звіт за %s", summary.date)
        return []

    documents: list[BufferedInputFile] = []
    for kind in (ReportKind.MEALS, ReportKind.ABSENCE):
        try:
            documents.append(
                BufferedInputFile(
                    render_day_report(report, kind),
                    filename=day_report_filename(summary.date, kind),
                )
            )
        except Exception:
            log.exception("Не вдалося намалювати звіт %s за %s", kind.value, summary.date)
    return documents


async def _admin_chat_ids(session: AsyncSession) -> list[int]:
    return list(
        await session.scalars(
            select(Teacher.tg_user_id).where(
                Teacher.role == Role.ADMIN,
                Teacher.is_active.is_(True),
                Teacher.tg_user_id.is_not(None),
            )
        )
    )


async def _ask_classes(
    bot: Bot,
    session: AsyncSession,
    classes: list[SchoolClass],
    d: Date,
    compose: Callable[[str, Date], str],
) -> tuple[int, int]:
    """Розіслати класним керівникам повідомлення з цифровою клавіатурою.

    Спільне тіло для ранкового запиту й нагадувань: відрізняється лише набір
    класів і текст, решта — вибір керівника, підказка «як минулого разу»,
    обробка недоступних отримувачів — однакова.

    Повертає (скільки спроб, скільки доставлено).
    """
    attempted = sent = 0

    for school_class in classes:
        teacher_ids = await primary_teacher_ids(session, school_class.id)
        if not teacher_ids:
            log.warning("Клас %s без класного керівника", school_class.name)
            continue

        hint = await last_known_count(session, school_class.id, d)
        markup = keyboards.number_pad(
            school_class.id, d, last_known=hint, max_children=settings.max_children
        )
        for teacher_id in teacher_ids:
            teacher = await session.get(Teacher, teacher_id)
            if teacher is None or not teacher.is_active or teacher.tg_user_id is None:
                continue
            attempted += 1
            if await _send(
                bot,
                teacher.tg_user_id,
                compose(school_class.name, d),
                who=teacher.full_name,
                reply_markup=markup,
            ):
                sent += 1
            await asyncio.sleep(SEND_PAUSE)

    return attempted, sent


async def daily_prompt(
    bot: Bot,
    maker: async_sessionmaker[AsyncSession],
    d: Date | None = None,
    *,
    force: bool = False,
) -> int:
    """Розіслати запит класним керівникам. Повертає кількість надісланих повідомлень."""
    d = d or today()

    async with maker() as session:
        if not force and not await is_school_day(session, d):
            log.info("%s — не навчальний день, запит не надсилаю", d)
            return 0

        classes = await active_classes(session)
        if not classes:
            log.warning("Немає активних класів")
            return 0

        # Клас, який уже подав дані, не турбуємо повторно.
        pending = [c for c in classes if await get_entry(session, c.id, d) is None]
        attempted, sent = await _ask_classes(bot, session, pending, d, texts.prompt)

        if _should_mark(attempted, sent):
            await mark_run(session, "prompt", d)
        await session.commit()

    log.info("Запит на %s: надіслано %s з %s", d, sent, attempted)
    if attempted and not sent:
        log.error("Жодне повідомлення не доставлено — спробую ще раз при наступному старті")
    return sent


async def remind(
    bot: Bot,
    maker: async_sessionmaker[AsyncSession],
    d: Date | None = None,
    *,
    force: bool = False,
    slot: str = "",
) -> int:
    """Нагадування тим, хто не відповів.

    `slot` — мітка часу (напр. "09:30") для журналу запусків: нагадувань за день
    кілька, і догоняюча логіка має розрізняти, яке з них уже відпрацювало.
    """
    d = d or today()

    async with maker() as session:
        if not force and not await is_school_day(session, d):
            return 0

        summary = await day_summary(session, d)
        silent = [status.school_class for status in summary.missing]
        attempted, sent = await _ask_classes(bot, session, silent, d, texts.reminder)

        if _should_mark(attempted, sent):
            await mark_run(session, f"remind:{slot}" if slot else "remind", d)
        await session.commit()

    log.info("Нагадування %s на %s: надіслано %s з %s", slot or "—", d, sent, attempted)
    return sent


async def _day_report_job(
    bot: Bot,
    maker: async_sessionmaker[AsyncSession],
    d: Date | None,
    kind: ReportKind,
    *,
    force: bool = False,
) -> int:
    """Спільне тіло обох щоденних звітів.

    Різниця між ними лише в тому, які цифри друкуються й о котрій вони йдуть,
    тож розсилка, обробка збоїв і журнал запусків спільні.
    """
    d = d or today()
    sent = attempted = 0
    job_key = f"report:{kind.value}"

    async with maker() as session:
        if not force and not await is_school_day(session, d):
            return 0

        summary = await day_summary(session, d)
        if not summary.statuses:
            # Мовчати тут не можна: жодного активного класу — це не «нема
            # роботи», а помилка налаштування, і побачити її має людина,
            # а не лише лог. Маркер ставимо, щоб не слати те саме щостарту.
            log.warning("%s: немає активних класів, звіт не будую", d)
            for chat_id in await _admin_chat_ids(session):
                await _send(bot, chat_id, texts.NO_ACTIVE_CLASSES)
            await mark_run(session, job_key, d)
            await session.commit()
            return 0

        report = build_report(
            summary, school_name=settings.school_name, slots=settings.meal_slots
        )
        try:
            pdf = render_day_report(report, kind)
        except Exception:
            # Збій рендеру коштує звіт, а не джоб: інакше він лишився б
            # непозначеним і наступний старт розіслав би все вдруге.
            log.exception("Не вдалося побудувати звіт %s за %s", kind.value, d)
            pdf = None

        text = texts.report_ready(kind, report)
        document = (
            BufferedInputFile(pdf, filename=day_report_filename(d, kind))
            if pdf is not None
            else None
        )

        for chat_id in await _admin_chat_ids(session):
            attempted += 1
            if await _send(bot, chat_id, text):
                sent += 1
                if document is not None:
                    await _send_document(bot, chat_id, document)

        # Пошта — після Telegram і байдужа до його результату: у адміністратора
        # бот міг бути заблокований, а директор усе одно чекає лист.
        if pdf is not None:
            await mailer.safe_send_day_report(report, pdf, kind=kind)

        if _should_mark(attempted, sent):
            await mark_run(session, job_key, d)
        await session.commit()

    log.info("Звіт %s за %s: надіслано %s з %s", kind.value, d, sent, attempted)
    return sent


async def meals_report(
    bot: Bot,
    maker: async_sessionmaker[AsyncSession],
    d: Date | None = None,
    *,
    force: bool = False,
) -> int:
    """Звіт про харчування — той, що йде на кухню."""
    return await _day_report_job(bot, maker, d, ReportKind.MEALS, force=force)


async def absence_report(
    bot: Bot,
    maker: async_sessionmaker[AsyncSession],
    d: Date | None = None,
    *,
    force: bool = False,
) -> int:
    """Звіт про відсутніх і хворих — той, що йде медсестрі."""
    return await _day_report_job(bot, maker, d, ReportKind.ABSENCE, force=force)


async def sync_all_months(session: AsyncSession, limit: int = 12) -> tuple[int, int]:
    """Перебудувати вкладки Google Sheets із БД. Повертає (оновлено, усього).

    Спільна для нічного джоба й ручного /sync — інакше два шляхи синхронізації
    непомітно розходяться.
    """
    months = await available_months(session, limit=limit)
    moment = today()
    eating: list = []
    tabs: list = []
    for y, m in months:
        eat, absent, sick = await build_month_matrices(
            session, y, m, school_name=settings.school_name, today=moment
        )
        eating.append(eat)
        tabs.append(eat)
        # Вкладки відсутніх і хворих створюємо лише за місяці, де ці цифри
        # справді є. Уся історія до появи фічі їх не має, тож інакше ми
        # щоночі перебудовували б два десятки порожніх вкладок — і без потреби
        # впиралися б у квоту Google на записи.
        tabs.extend(extra for extra in (absent, sick) if extra.has_any_data)

    synced = 0
    for i, matrix in enumerate(tabs):
        if i:
            # Вкладок тепер утричі більше (три метрики на місяць), а Sheets
            # дозволяє ~60 записів на хвилину. Без паузи нічний синк упирався б
            # у 429, і safe_rebuild_month тихо проковтнув би це — вкладки
            # лишилися б застарілими, і ніхто б не помітив.
            await asyncio.sleep(SHEETS_PAUSE)
        if await sheets.safe_rebuild_month(matrix):
            synced += 1

    # «Зведення» лишається про харчування: це відповідь на питання
    # «скільки годували», а не «скільки хворіли».
    if eating:
        try:
            await sheets.sync_summary(eating)
        except Exception:
            log.exception("Не вдалося оновити вкладку «Зведення»")

    return synced, len(tabs)


async def nightly_sheets_sync(maker: async_sessionmaker[AsyncSession]) -> int:
    """Повна перебудова вкладок у Google Sheets.

    Дебаунснутий синк після кожного запису може щось пропустити (мережа, ліміти),
    тому раз на добу таблиця перебудовується з БД повністю.
    """
    if not settings.sheets_enabled:
        return 0

    async with maker() as session:
        synced, total = await sync_all_months(session)

    log.info("Нічний синк: оновлено %s з %s вкладок", synced, total)
    return synced


def daily_plan() -> list[tuple[str, Time, DailyJob]]:
    """Розклад дня: (ключ у журналі, час, що виконати).

    Одне джерело правди для планувальника й догоняючої логіки — інакше вони
    розходяться, і догоняння або пропускає джоб, або запускає не той.
    """
    plan: list[tuple[str, Time, DailyJob]] = [
        ("prompt", settings.prompt_time, daily_prompt),
    ]
    for t in settings.remind_times:
        slot = f"{t:%H:%M}"

        async def run(
            bot: Bot,
            maker: async_sessionmaker[AsyncSession],
            d: Date | None = None,
            _slot: str = slot,
        ) -> int:
            # d необовʼязковий: планувальник передає лише (bot, maker) і чекає
            # на «сьогодні», а догоняюча логіка задає конкретну дату.
            return await remind(bot, maker, d, slot=_slot)

        plan.append((f"remind:{slot}", t, run))

    plan.append(("report:meals", settings.meals_report_time, meals_report))
    plan.append(("report:absence", settings.absence_report_time, absence_report))
    return plan


async def catch_up(bot: Bot, maker: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Догнати розсилки, пропущені через простій.

    Потрібно тому, що cron не відтворює минуле: якщо сервер лежав о 09:05 і
    піднявся о 10:20, запланований запит того дня просто не відбудеться — мовчки,
    поки хтось не помітить дірку в звіті. Для хостингу в Україні з блекаутами
    це не крайній випадок, а звичайний вівторок.

    Викликається один раз при старті. Захист від повторів — журнал запусків,
    тому перезапуск процесу десять разів поспіль нічого не продублює.
    """
    moment = now()
    d = moment.date()
    done: dict[str, int] = {}

    async with maker() as session:
        if not await is_school_day(session, d):
            log.info("Старт: %s не навчальний день, догоняти нічого", d)
            return done

    # Після кінця навчального дня запит уже безпредметний.
    if moment.time() > settings.catch_up_deadline:
        log.info(
            "Старт: вже %s — пізніше за дедлайн %s, догоняти не буду",
            moment.strftime("%H:%M"),
            settings.catch_up_deadline.strftime("%H:%M"),
        )
        return done

    for job_key, scheduled, run in daily_plan():
        if moment.time() < scheduled:
            continue  # час ще не настав — спрацює за розкладом

        async with maker() as session:
            if await has_run(session, job_key, d):
                continue

        log.warning("Догоняю пропущений джоб %s за %s", job_key, d)
        done[job_key] = await run(bot, maker, d)

    if done:
        log.warning("Догнали після простою: %s", done)
    else:
        log.info("Старт: пропущених розсилок немає")
    return done
