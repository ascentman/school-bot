"""Engine та фабрика сесій."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from school_bot.config import BASE_DIR, settings

engine = create_async_engine(settings.database_url, echo=False)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сесія з автоматичним commit/rollback."""
    async with SessionMaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _upgrade_to_head() -> None:
    # Приглушуємо ДО імпорту alembic: частина його INFO-логів друкується вже
    # під час завантаження модуля й інакше ховає власний вивід CLI.
    # Явний `alembic upgrade head` (Docker CMD) логування не втрачає — він
    # читає alembic.ini і налаштовує його сам.
    logging.getLogger("alembic").setLevel(logging.WARNING)

    from alembic import command
    from alembic.config import Config

    # Config без шляху до alembic.ini: інакше env.py викликає fileConfig() і
    # перевизначає логування застосунку — CLI тоне в INFO-повідомленнях Alembic.
    cfg = Config()
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    command.upgrade(cfg, "head")


async def ensure_schema() -> None:
    """Привести БД до актуальної схеми.

    Саме через Alembic, а не Base.metadata.create_all: create_all будує таблиці,
    не записуючи версію в alembic_version, після чого штатний `alembic upgrade
    head` падає з «table already exists» — і деплой ламається назавжди.

    Alembic синхронний і сам відкриває event loop, тому виконується в окремому
    потоці: у вже запущеному циклі asyncio.run() всередині впав би.
    """
    await asyncio.to_thread(_upgrade_to_head)
