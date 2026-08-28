"""Меню команд Telegram, окремо для вчителя й адміністратора.

set_my_commands без scope виставляє список глобально — тоді вчитель бачить
адмінські команди, які для нього мовчать. Тому default — вчительський набір,
а адмінам він перезаписується персонально при /start.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

TEACHER_COMMANDS = [
    BotCommand(command="start", description="Головне меню"),
    BotCommand(command="today", description="Ввести або виправити дані за сьогодні"),
    BotCommand(command="name", description="Змінити своє ПІБ"),
    BotCommand(command="help", description="Як користуватися ботом"),
]

ADMIN_COMMANDS = TEACHER_COMMANDS + [
    BotCommand(command="import_teachers", description="Завантажити список вчителів"),
    BotCommand(command="edit_teacher", description="Змінити класи вчителя"),
    BotCommand(command="add_teacher", description="Додати одного вчителя"),
    BotCommand(command="off_teacher", description="Вимкнути вчителя"),
    BotCommand(command="free_number", description="Звільнити номер для нової людини"),
    BotCommand(command="add_class", description="Додати клас"),
    BotCommand(command="off_class", description="Прибрати клас"),
    BotCommand(command="days_off", description="Позначити канікули"),
    BotCommand(command="days_on", description="Скасувати канікули"),
    BotCommand(command="sync", description="Оновити Google-таблицю"),
]


async def set_default_commands(bot: Bot) -> None:
    await bot.set_my_commands(TEACHER_COMMANDS, scope=BotCommandScopeDefault())


async def set_personal_commands(bot: Bot, chat_id: int, *, is_admin: bool) -> None:
    await bot.set_my_commands(
        ADMIN_COMMANDS if is_admin else TEACHER_COMMANDS,
        scope=BotCommandScopeChat(chat_id=chat_id),
    )
