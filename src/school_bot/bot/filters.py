"""Фільтри доступу."""

from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import TelegramObject

from school_bot.db.models import Teacher


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, teacher: Teacher | None = None) -> bool:
        return teacher is not None and teacher.is_active and teacher.is_admin
