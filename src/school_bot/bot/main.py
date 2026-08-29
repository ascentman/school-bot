"""Збірка бота: диспетчер, роутери, планувальник."""

from __future__ import annotations

import asyncio
import logging
from datetime import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from school_bot.bot.commands import set_default_commands
from school_bot.bot.handlers import admin, daily, fallback, start
from school_bot.bot.middlewares import AuthMiddleware, DbSessionMiddleware
from school_bot.config import settings
from school_bot.db.base import SessionMaker, ensure_schema
from school_bot.domain.classes import ensure_classes
from school_bot.scheduler import jobs

log = logging.getLogger(__name__)


def build_dispatcher(session_maker: async_sessionmaker[AsyncSession] | None = None) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    session_mw = DbSessionMiddleware(session_maker or SessionMaker)
    auth_mw = AuthMiddleware()
    for observer in (dp.message, dp.callback_query):
        # Саме outer_middleware, не middleware: фільтри рівня роутера
        # (router.message.filter(IsAdmin())) перевіряються ДО inner-middleware,
        # тому inner не встигає підставити `teacher` — і адмінський роутер
        # мовчки відхиляє геть усе.
        observer.outer_middleware(session_mw)
        observer.outer_middleware(auth_mw)

    # Порядок важливий: admin перехоплює кнопки меню, daily — callback-и цифр.
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(daily.router)
    # Обовʼязково останнім: перехоплює все, що не розпізнали попередні роутери.
    dp.include_router(fallback.router)
    return dp


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    # misfire_grace_time: скільки джоб лишається дійсним, якщо не спрацював вчасно.
    # Дефолт APScheduler — 1 секунда, тобто найменша затримка (блекаут, перезапуск,
    # завантажений сервер) тихо зʼїдає розсилку. coalesce — якщо пропущено кілька
    # спрацювань поспіль, виконати один раз, а не серією.
    defaults = {
        "misfire_grace_time": settings.misfire_grace_seconds,
        "coalesce": True,
        "max_instances": 1,
    }
    scheduler = AsyncIOScheduler(timezone=settings.tz, job_defaults=defaults)

    def cron(t: time) -> CronTrigger:
        return CronTrigger(
            day_of_week="mon-fri", hour=t.hour, minute=t.minute, timezone=settings.tz
        )

    # Розклад береться з jobs.daily_plan() — того самого джерела, яким
    # користується догоняюча логіка. Два окремі списки неминуче розійшлися б.
    for job_id, at, run in jobs.daily_plan():
        scheduler.add_job(run, cron(at), args=(bot, SessionMaker), id=job_id)

    scheduler.add_job(
        jobs.nightly_sheets_sync,
        cron(settings.sheets_rebuild_time),
        args=(SessionMaker,),
        id="sheets",
    )
    return scheduler


async def run() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )
    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN не задано. Скопіюйте .env.example у .env і вкажіть токен від @BotFather."
        )

    await ensure_schema()
    async with SessionMaker() as session:
        await ensure_classes(session, settings.school_classes)
        await session.commit()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()
    scheduler = build_scheduler(bot)

    me = await bot.get_me()
    await set_default_commands(bot)
    scheduler.start()

    log.info("Бот @%s запущено. Часовий пояс: %s", me.username, settings.timezone)
    for job in scheduler.get_jobs():
        log.info("  джоб %-14s → %s", job.id, job.next_run_time)

    # Після простою (блекаут, перезапуск) догнати те, що cron уже не відтворить.
    try:
        await jobs.catch_up(bot, SessionMaker)
    except Exception:
        log.exception("Догоняюча розсилка не вдалася — продовжую роботу")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
