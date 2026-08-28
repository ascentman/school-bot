"""Middleware: сесія БД + автентифікація вчителя для кожного апдейту."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from school_bot.bot import texts
from school_bot.config import settings
from school_bot.db.models import Role, Teacher

log = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class AuthMiddleware(BaseMiddleware):
    """Кладе `teacher` у data. Незареєстрованих не пускає далі /start.

    Бот закритий: усе, що не має запису в БД, отримує лише повідомлення
    «зверніться до адміністратора».
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        session: AsyncSession = data["session"]
        if user is None:
            return await handler(event, data)

        teacher = await session.scalar(select(Teacher).where(Teacher.tg_user_id == user.id))

        # Bootstrap: перші адміни з .env отримують доступ автоматично,
        # інакше нікому було б видати перше запрошення.
        if teacher is None and user.id in settings.bootstrap_admins:
            teacher = Teacher(
                tg_user_id=user.id,
                full_name=user.full_name,
                role=Role.ADMIN,
            )
            session.add(teacher)
            await session.flush()
            log.info("Створено bootstrap-адміна %s (%s)", user.full_name, user.id)

        data["teacher"] = teacher

        if teacher is not None and teacher.is_active:
            return await handler(event, data)

        # Пропускаємо два випадки, з яких починається реєстрація:
        # /start (інвайт-посилання) і надісланий контакт (привʼязка за номером).
        # Без другого повідомлення з контактом не має тексту й було б відкинуте.
        if isinstance(event, Message) and (
            (event.text or "").startswith("/start") or event.contact is not None
        ):
            return await handler(event, data)

        # Вимкнений запис — не те саме, що невідомий: «облікового запису
        # не знайдено» прямо суперечить дійсності й збиває людину з пантелику.
        refusal = (
            texts.ACCOUNT_DISABLED if teacher is not None else texts.NOT_REGISTERED
        )
        if isinstance(event, CallbackQuery):
            await event.answer(refusal, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(refusal)
        return None
