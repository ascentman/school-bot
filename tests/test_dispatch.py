"""Наскрізні перевірки: справжній Update → справжній Dispatcher → відповідь.

Ці тести існують тому, що структурні перевірки маршрутизації пропустили
реальну поломку: middleware було зареєстроване як inner, а фільтри рівня
роутера перевіряються ДО inner-middleware. Через це `IsAdmin` бачив
teacher=None і адмінське меню мовчки не працювало взагалі.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from aiogram.types import Contact

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


# --- самореєстрація за номером -------------------------------------------


def _own_contact(tg_id: int, phone: str) -> Contact:
    return Contact(phone_number=phone, first_name="Хтось", user_id=tg_id)


async def test_unknown_number_is_registered_not_refused(send, people, maker):
    """Перевірку за списком прибрано: людина не впирається у відмову."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    replies = await send(tg_id=900, contact=_own_contact(900, "+380991112233"))
    assert any("ПІБ" in r for r in replies), replies
    assert not any("немає в списку" in r for r in replies)

    async with maker() as s:
        created = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 900))
    assert created is not None
    assert created.phone == "380991112233"


async def test_name_is_saved_after_self_registration(send, people, maker):
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=901, contact=_own_contact(901, "+380991110001"), name="Вова 🌻")
    replies = await send("Коваленко Марія Іванівна", tg_id=901)

    assert any("Вітаю, Коваленко Марія Іванівна" in r for r in replies), replies
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 901))
    assert saved.full_name == "Коваленко Марія Іванівна"


async def test_self_registered_teacher_is_warned_about_no_classes(send, people):
    await send(tg_id=902, contact=_own_contact(902, "+380991110002"))
    replies = await send("Мельник Ігор Богданович", tg_id=902)
    assert any("не закріплено жодного класу" in r for r in replies)


async def test_too_short_name_is_rejected(send, people, maker):
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=903, contact=_own_contact(903, "+380991110003"))
    replies = await send("Ок", tg_id=903)
    assert any("Надто коротко" in r for r in replies)

    # Стан не скинуто — наступне повідомлення все ще чекає ПІБ.
    await send("Гнатюк Леся Андріївна", tg_id=903)
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 903))
    assert saved.full_name == "Гнатюк Леся Андріївна"


async def test_someone_elses_contact_is_still_refused(send, people, maker):
    """Прибрали перевірку за списком, але не захист від чужого контакту."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    foreign = _own_contact(999, "+380991110004")     # user_id ≠ відправник
    replies = await send(tg_id=904, contact=foreign)

    assert any("контакт іншої людини" in r for r in replies)
    async with maker() as s:
        assert await s.scalar(select(Teacher).where(Teacher.tg_user_id == 904)) is None


async def test_deactivated_teacher_resharing_contact_does_not_crash(send, maker):
    """Вимкнений вчитель ділиться контактом — не має бути краху.

    link_by_phone шукає лише серед активних, тож повертає None. Спроба
    створити новий запис з тим самим tg_user_id падала на UNIQUE.
    """
    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(
            Teacher(
                full_name="Вимкнений",
                tg_user_id=905,
                phone="380991110905",
                role=Role.TEACHER,
                is_active=False,
            )
        )
        await s.commit()

    replies = await send(tg_id=905, contact=_own_contact(905, "+380991110905"))
    assert replies, "бот має щось відповісти, а не впасти"
    assert any("вимкнено" in r for r in replies), replies


async def test_command_is_not_swallowed_as_a_name(send, people, maker):
    """У стані очікування ПІБ команда не має записуватися як імʼя."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=906, contact=_own_contact(906, "+380991110906"))
    await send("/help", tg_id=906)

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 906))
    assert saved.full_name != "/help", "команда потрапила в ПІБ"


async def test_deactivated_teacher_is_not_reactivated(send, maker):
    """/off_teacher має щось означати: повторна реєстрація не повертає доступ."""
    from sqlalchemy import select

    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(
            Teacher(
                full_name="Вимкнений",
                tg_user_id=907,
                phone="380991110907",
                role=Role.TEACHER,
                is_active=False,
            )
        )
        await s.commit()

    await send(tg_id=907, contact=_own_contact(907, "+380991110907"))
    async with maker() as s:
        after = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 907))
    assert not after.is_active


async def test_command_in_name_state_leaves_the_state(send, people, maker):
    """Після команди стан скидається, і бот знову слухає звичайні команди."""
    await send(tg_id=908, contact=_own_contact(908, "+380991110908"))
    postponed = await send("/help", tg_id=908)
    assert any("можна вказати згодом" in r for r in postponed)

    # Тепер /help має спрацювати нормально.
    assert any("Як це працює" in r for r in await send("/help", tg_id=908))


async def test_name_command_lets_teacher_fix_it_later(send, people, maker):
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=909, contact=_own_contact(909, "+380991110909"), name="Вова 🌻")
    await send("/help", tg_id=909)                       # відклали

    assert any("ПІБ" in r for r in await send("/name", tg_id=909))
    await send("Кравець Юрій Миколайович", tg_id=909)

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 909))
    assert saved.full_name == "Кравець Юрій Миколайович"


async def test_name_without_letters_is_rejected(send, people, maker):
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=910, contact=_own_contact(910, "+380991110910"))
    assert any("не ПІБ" in r for r in await send("12345", tg_id=910))

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 910))
    assert saved.full_name != "12345"
