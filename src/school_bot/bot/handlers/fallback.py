"""Останній роутер: відповідь на все, що не розпізнали інші.

Підключається НАЙОСТАННІШИМ. Без нього вчитель, який натиснув адмінську команду
або просто написав щось своє, отримує повну тишу й не розуміє, чи бот живий.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from school_bot.bot import keyboards, texts
from school_bot.db.models import Teacher

router = Router(name="fallback")


@router.message(F.text)
async def unknown_input(message: Message, teacher: Teacher) -> None:
    await message.answer(
        texts.UNKNOWN_INPUT,
        reply_markup=keyboards.main_menu(is_admin=teacher.is_admin),
    )
