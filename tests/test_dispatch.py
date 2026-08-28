"""Наскрізні перевірки: справжній Update → справжній Dispatcher → відповідь.

Ці тести існують тому, що структурні перевірки маршрутизації пропустили
реальну поломку: middleware було зареєстроване як inner, а фільтри рівня
роутера перевіряються ДО inner-middleware. Через це `IsAdmin` бачив
teacher=None і адмінське меню мовчки не працювало взагалі.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from school_bot.bot import texts
from school_bot.db.models import Role, SchoolClass, Teacher

ADMIN_ID = 100
TEACHER_ID = 200
STRANGER_ID = 300


@pytest_asyncio.fixture
async def people(maker):
    async with maker() as s:
        klass = SchoolClass(name="1-А", grade=1, letter="А", sort_order=1)
        admin = Teacher(full_name="Адмін", tg_user_id=ADMIN_ID, role=Role.ADMIN)
        teacher = Teacher(full_name="Вчителька", tg_user_id=TEACHER_ID, role=Role.TEACHER)
        s.add_all([klass, admin, teacher])
        await s.commit()
        return {"class_id": klass.id}


ADMIN_BUTTONS = [
    texts.BTN_TODAY,
    texts.BTN_REPORT,
    texts.BTN_TEACHERS,
    texts.BTN_CLASSES,
    texts.BTN_DAYS_OFF,
    texts.BTN_SETTINGS,
]


@pytest.mark.parametrize("button", ADMIN_BUTTONS)
async def test_admin_buttons_reach_their_handlers(send, people, button):
    """Кожна кнопка адмінського меню має дати змістовну відповідь, не фолбек."""
    replies = await send(button, tg_id=ADMIN_ID)
    assert replies, f"кнопка «{button}» лишилася без відповіді"
    assert texts.UNKNOWN_INPUT not in replies, f"кнопка «{button}» провалилася у фолбек"


@pytest.mark.parametrize(
    "command",
    ["/import_teachers", "/edit_teacher", "/add_teacher", "/add_class", "/days_off"],
)
async def test_admin_commands_reach_their_handlers(send, people, command):
    replies = await send(command, tg_id=ADMIN_ID)
    assert replies and texts.UNKNOWN_INPUT not in replies


async def test_teacher_sees_own_classes(send, people):
    replies = await send(texts.BTN_MY_CLASSES, tg_id=TEACHER_ID)
    assert replies
    assert texts.UNKNOWN_INPUT not in replies


async def test_teacher_cannot_use_admin_buttons(send, people):
    """Кнопка адміна від вчителя не має спрацювати — але й не мовчати."""
    replies = await send(texts.BTN_TEACHERS, tg_id=TEACHER_ID)
    assert replies == [texts.UNKNOWN_INPUT]


async def test_teacher_help(send, people):
    replies = await send("/help", tg_id=TEACHER_ID)
    assert any("Як це працює" in r for r in replies)


async def test_unknown_text_gets_a_reply(send, people):
    assert await send("привіт", tg_id=TEACHER_ID) == [texts.UNKNOWN_INPUT]


async def test_stranger_is_refused(send, people):
    replies = await send("привіт", tg_id=STRANGER_ID)
    assert replies == [texts.NOT_REGISTERED]


async def test_stranger_start_is_asked_for_contact(send, people):
    replies = await send("/start", tg_id=STRANGER_ID)
    assert any("Поділитися номером" in r or "номер" in r.lower() for r in replies)


# --- перше відкриття бота -------------------------------------------------


async def test_first_open_asks_for_contact(send, people):
    """Невідомий користувач має отримати кнопку «Поділитися номером»."""
    replies = await send("/start", tg_id=STRANGER_ID)
    assert any("натисніть кнопку" in r for r in replies)


async def test_known_teacher_start_does_not_ask_contact(send, people):
    """Той, хто вже привʼязаний, більше номер не надсилає."""
    replies = await send("/start", tg_id=TEACHER_ID)
    assert any("Вітаю" in r for r in replies)
    assert not any("номер" in r.lower() for r in replies)


async def test_welcome_warns_when_no_classes(send, maker):
    """Без класів вчитель не отримає запитів — про це має бути сказано."""
    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(Teacher(full_name="Без класів", tg_user_id=404, role=Role.TEACHER))
        await s.commit()

    replies = await send("/start", tg_id=404)
    assert any("не закріплено жодного класу" in r for r in replies)
