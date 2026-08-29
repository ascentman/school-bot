"""Наскрізні перевірки: справжній Update → справжній Dispatcher → відповідь.

Ці тести існують тому, що структурні перевірки маршрутизації пропустили
реальну поломку: middleware було зареєстроване як inner, а фільтри рівня
роутера перевіряються ДО inner-middleware. Через це `IsAdmin` бачив
teacher=None і адмінське меню мовчки не працювало взагалі.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from aiogram.types import Contact
from sqlalchemy import select

from school_bot.bot import texts
from school_bot.db.models import Role, SchoolClass, Teacher
from school_bot.domain.teachers import free_number, link_by_phone

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
    await send(tg_id=901, contact=_own_contact(901, "+380991110001"), name="Вова 🌻")
    replies = await send("Коваленко Марія Іванівна", tg_id=901)

    assert any("Записав" in r for r in replies), replies
    assert any("Оберіть свій клас" in r for r in replies), replies
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 901))
    assert saved.full_name == "Коваленко Марія Іванівна"


async def test_no_configured_classes_falls_back_to_the_warning(send, maker):
    """Якщо класів у школі ще немає, вибирати нічого — лишається попередження."""
    await send(tg_id=902, contact=_own_contact(902, "+380991110002"))
    replies = await send("Мельник Ігор Богданович", tg_id=902)

    assert any("не заведені" in r for r in replies), replies
    assert any("не закріплено жодного класу" in r for r in replies), replies


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
    assert any("/name" in r for r in postponed)

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


async def test_name_with_html_does_not_break_the_bot(send, people, maker):
    """ПІБ тепер задає будь-хто, а бот працює в parse_mode=HTML.

    Символ «<» у ПІБ ламав розбір сутностей Telegram: відповідь падала
    необробленим TelegramBadRequest, ПІБ відкочувалося разом із сесією,
    і людина лишалася без жодної відповіді.
    """
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=911, contact=_own_contact(911, "+380991110911"))
    replies = await send("Іван <Петров> & Сини", tg_id=911)

    assert replies, "бот має відповісти, а не впасти"
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 911))
    assert saved.full_name == "Іван <Петров> & Сини", "ПІБ мало зберегтися як є"


@pytest.mark.parametrize(
    "screen",
    [texts.BTN_TEACHERS, texts.BTN_CLASSES, texts.BTN_TODAY],
)
async def test_injected_html_in_name_is_escaped_on_every_admin_screen(
    send, people, maker, screen
):
    """Самореєстрація не має відкривати шлях HTML у чат адміністратора.

    Перевіряються всі екрани, де зʼявляються ПІБ або назви класів: спершу
    екранували лише «Вчителі», а «Класи» показують ті самі ПІБ і лишалися
    діркою.
    """
    from sqlalchemy import select

    from school_bot.db.models import Teacher
    from school_bot.domain.classes import set_teacher_classes
    from school_bot.domain.meals import upsert_entry

    await send(tg_id=912, contact=_own_contact(912, "+380991110912"))
    await send('<a href="https://example.com">клік</a>', tg_id=912)

    # Привʼязуємо до класу, щоб ПІБ зʼявилося і в списку класів.
    async with maker() as s:
        intruder = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 912))
        await set_teacher_classes(s, intruder.id, {people["class_id"]})
        await upsert_entry(
            s, class_id=people["class_id"], d=date.today(), eating_count=20,
            teacher_id=intruder.id,
        )
        await s.commit()

    joined = "\n".join(await send(screen, tg_id=ADMIN_ID))
    assert "<a href=" not in joined, f"неекранований HTML на екрані «{screen}»"


async def test_disabled_teacher_cannot_return_via_new_telegram_account(send, maker):
    """/off_teacher має триматися й проти нового Telegram-акаунта.

    Людина видаляє акаунт, реєструє новий на той самий номер — tg_user_id
    інший, тож перевірки за ним не спрацьовують, і бот видавав новий
    робочий обліковий запис.
    """
    from sqlalchemy import select

    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(
            Teacher(
                full_name="Звільнений",
                tg_user_id=920,
                phone="380991110920",
                role=Role.TEACHER,
                is_active=False,
            )
        )
        await s.commit()

    # Той самий номер, але вже інший Telegram-акаунт.
    replies = await send(tg_id=921, contact=_own_contact(921, "+380991110920"))

    assert any("вимкнено" in r for r in replies), replies
    async with maker() as s:
        sneaked = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 921))
    assert sneaked is None, "деактивований вчитель отримав новий робочий запис"


async def test_postponing_a_name_change_does_not_claim_a_rename(send, people, maker):
    """Через /name повідомлення не має стверджувати, що ПІБ замінено на нік."""
    await send(tg_id=922, contact=_own_contact(922, "+380991110922"))
    await send("Савченко Ірина Володимирівна", tg_id=922)

    await send("/name", tg_id=922)
    replies = await send("/help", tg_id=922)          # передумав

    assert not any("як ви підписані в Telegram" in r for r in replies), replies


async def test_class_name_with_html_does_not_break_add_class(send, people):
    """Відповідь на /add_class підставляє те, що набрав адміністратор.

    Достатньо одруку «1<3» — і повідомлення падає з TelegramBadRequest,
    той самий баг, який цей PR лікує для ПІБ.
    """
    await send("/add_class", tg_id=ADMIN_ID)
    replies = await send("1<3, 2-А", tg_id=ADMIN_ID)

    joined = "\n".join(replies)
    assert replies
    assert "1<3" not in joined, "неекранований ввід адміністратора"
    assert "1&lt;3" in joined


async def test_second_contact_while_waiting_for_name_still_asks(send, people, maker):
    """Повторний контакт не має мовчки завершувати реєстрацію під ніком."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=930, contact=_own_contact(930, "+380991110930"), name="Вова 🌻")
    replies = await send(tg_id=930, contact=_own_contact(930, "+380991110930"), name="Вова 🌻")

    assert any("ПІБ" in r for r in replies), replies

    # Стан не втрачено: наступне повідомлення все ще приймається як ПІБ.
    await send("Литвин Тетяна Олегівна", tg_id=930)
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 930))
    assert saved.full_name == "Литвин Тетяна Олегівна"


async def test_command_with_leading_space_is_not_a_name(send, people, maker):
    """Один зайвий пробіл не має повертати баг «команда як ПІБ»."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=940, contact=_own_contact(940, "+380991110940"))
    await send(" /help", tg_id=940)

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 940))
    assert saved.full_name != "/help", "команда з пробілом потрапила в ПІБ"


async def test_name_command_works_while_already_waiting_for_a_name(send, people):
    """/name у стані очікування має перепитати, а не зʼїстися."""
    await send(tg_id=941, contact=_own_contact(941, "+380991110941"))
    replies = await send("/name", tg_id=941)
    assert any("ПІБ" in r for r in replies), replies
    assert not any("згодом" in r for r in replies), "команду зʼїв skip_full_name"


async def test_recycled_number_frees_up_after_admin_reimport(send, people, maker):
    """Номер звільненого вчителя згодом дістається новому працівнику.

    Блокування вимкненого запису не має назавжди закривати номер: після
    того як адміністратор внесе його у список під новим ПІБ, нова людина
    має зареєструватися без втручання.
    """
    from sqlalchemy import select

    from school_bot.db.models import Role, Teacher
    from school_bot.domain.teachers import import_teachers

    async with maker() as s:
        s.add(
            Teacher(
                full_name="Звільнений",
                tg_user_id=950,
                phone="380991110950",
                role=Role.TEACHER,
                is_active=False,
            )
        )
        await s.commit()

    # Заблоковано, доки адміністратор не втрутився.
    blocked = await send(tg_id=951, contact=_own_contact(951, "+380991110950"))
    assert any("вимкнено" in r for r in blocked)

    # Адміністратор явно звільняє номер для нового працівника.
    async with maker() as s:
        former = await s.scalar(select(Teacher).where(Teacher.phone == "380991110950"))
        await free_number(s, former.id)
        await import_teachers(s, "Новий Працівник, 0991110950")
        await s.commit()

    replies = await send(tg_id=951, contact=_own_contact(951, "+380991110950"))
    assert not any("вимкнено" in r for r in replies), replies

    async with maker() as s:
        rebound = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 951))
        everyone = list(await s.scalars(select(Teacher).where(Teacher.phone == "380991110950")))

    # Слабка перевірка «просто зареєструвався» тут нічого не варта: людина
    # отримувала порожній дублікат, а реімпортований запис лишався підвішеним
    # під недосяжним tg_user_id. Тому звіряємо саме тотожність запису.
    assert rebound is not None and rebound.is_active
    assert rebound.full_name == "Новий Працівник", "дістався дублікат, а не новий запис"
    assert rebound.phone == "380991110950", "номер лишився за старим записом"
    assert len(everyone) == 1, "номер розʼїхався по двох записах"


async def test_reimport_does_not_unlink_a_working_teacher(session):
    """Звільняти номер можна лише у вимкненого запису.

    Інакше виправлення одруку в ПІБ через повторний імпорт відвʼязувало б
    чинного вчителя від його Telegram.
    """
    from school_bot.domain.teachers import import_teachers

    await import_teachers(session, "Коваленко Марія, 0671234567, 1-А")
    await link_by_phone(session, "0671234567", 500)

    await import_teachers(session, "Коваленко Марія Іванівна, 0671234567, 1-А")

    teacher = await session.scalar(select(Teacher).where(Teacher.phone == "380671234567"))
    assert teacher.tg_user_id == 500, "чинного вчителя відвʼязано від Telegram"
    assert teacher.full_name == "Коваленко Марія Іванівна"


async def test_start_while_waiting_for_name_does_not_finish_silently(send, people, maker):
    """/start — типова дія «почати спочатку», і вона не має лишати нік як ПІБ."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=960, contact=_own_contact(960, "+380991110960"), name="Вова 🌻")
    replies = await send("/start", tg_id=960)

    assert any("ПІБ" in r for r in replies), replies

    # Стан збережено: наступне повідомлення все ще приймається як ПІБ.
    await send("Бондаренко Ольга Василівна", tg_id=960)
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 960))
    assert saved.full_name == "Бондаренко Ольга Василівна"


async def test_multiline_name_is_collapsed(send, people, maker):
    """ПІБ у кілька рядків ламає однорядковий формат списків вчителів і класів."""
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=961, contact=_own_contact(961, "+380991110961"))
    await send("Гнатюк\nЛеся\n\nАндріївна", tg_id=961)

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 961))
    assert "\n" not in saved.full_name
    assert saved.full_name == "Гнатюк Леся Андріївна"


async def test_absurdly_long_name_is_rejected(send, people, maker):
    """Довге ПІБ від самозареєстрованого ламає списки для всіх адміністраторів.

    teachers_list і classes_list зліплюють кількох вчителів в одне
    повідомлення; ліміт Telegram — 4096 символів.
    """
    from sqlalchemy import select

    from school_bot.db.models import Teacher

    await send(tg_id=970, contact=_own_contact(970, "+380991110970"))
    replies = await send("Я" * 3000, tg_id=970)

    assert any("задовге" in r.lower() or "довг" in r.lower() for r in replies), replies
    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 970))
    assert len(saved.full_name) <= 200


async def test_admin_screens_survive_a_long_name(send, people, maker):
    """Навіть якщо довге ПІБ якось потрапило в базу — екрани мають працювати."""
    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(Teacher(full_name="Я" * 200, tg_user_id=971, role=Role.TEACHER))
        await s.commit()

    for screen in (texts.BTN_TEACHERS, texts.BTN_CLASSES):
        replies = await send(screen, tg_id=ADMIN_ID)
        assert replies
        assert all(len(r) <= 4096 for r in replies), f"перевищено ліміт на «{screen}»"


async def test_disabling_during_registration_does_not_loop(send, people, maker):
    """Вимкнення під час очікування ПІБ не має лишати суперечливий глухий кут.

    Бот просив ПІБ на /start, але саме ПІБ уже не приймав: middleware
    відсікала звичайний текст як від невідомого користувача.
    """
    from school_bot.db.models import Teacher

    await send(tg_id=980, contact=_own_contact(980, "+380991110980"))

    async with maker() as s:
        fresh = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 980))
        fresh.is_active = False
        await s.commit()

    replies = await send("/start", tg_id=980)
    assert not any("ПІБ" in r for r in replies), f"бот просить ПІБ у вимкненого: {replies}"
    assert any("вимкнено" in r for r in replies), replies


async def test_name_during_initial_registration_is_not_a_change(send, people):
    """/name під час первинної реєстрації — не зміна вже наявного ПІБ.

    Інакше відмова показує «лишив ПІБ без змін», хоча насправді в записі
    досі нік із Telegram, і людина вирішить, що ПІБ у неї нормальне.
    """
    await send(tg_id=990, contact=_own_contact(990, "+380991110990"), name="Вова 🌻")
    await send("/name", tg_id=990)
    replies = await send("/help", tg_id=990)

    assert any("Вова 🌻" in r for r in replies), replies


async def test_disabled_user_is_told_so_not_that_account_is_unknown(send, maker):
    """«Облікового запису не знайдено» вимкненому — неправда, яка збиває з пантелику."""
    from school_bot.db.models import Role, Teacher

    async with maker() as s:
        s.add(
            Teacher(full_name="Вимкнений", tg_user_id=995, role=Role.TEACHER, is_active=False)
        )
        await s.commit()

    replies = await send("Коваленко Марія Іванівна", tg_id=995)
    assert any("вимкнено" in r for r in replies), replies
    assert not any("не знайдено" in r for r in replies)


async def test_add_teacher_rejects_absurdly_long_name(send, people):
    """/add_teacher — ще один шлях, яким довге ПІБ потрапляло в базу."""
    await send("/add_teacher", tg_id=ADMIN_ID)
    replies = await send("Я" * 3000, tg_id=ADMIN_ID)
    assert any("довг" in r.lower() for r in replies), replies


async def test_repeated_postpone_never_claims_a_name_was_kept(send, people, maker):
    """Цикл «відклав → /name → відклав» не має стверджувати, що ПІБ збережено.

    Прапорець, виведений з FSM-стану, цього не переживав: стан очищується
    при відкладанні, тож другий /name виглядав як зміна вже наявного ПІБ.
    """
    from school_bot.db.models import Teacher

    await send(tg_id=996, contact=_own_contact(996, "+380991110996"), name="Вова 🌻")
    await send("/help", tg_id=996)
    await send("/name", tg_id=996)
    replies = await send("/help", tg_id=996)

    async with maker() as s:
        saved = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 996))
    assert saved.full_name == "Вова 🌻", "ПІБ так і не вводили"
    assert any("Вова 🌻" in r for r in replies), "повідомлення має називати фактичне ПІБ"


# --- вибір класів після ПІБ ----------------------------------------------


async def test_teacher_picks_one_class_then_finishes(send, tap, people, maker):
    from school_bot.domain.meals import classes_for_teacher

    await send(tg_id=1001, contact=_own_contact(1001, "+380991111001"))
    await send("Коваленко Марія Іванівна", tg_id=1001)

    picked = await tap("1-А", tg_id=1001)
    assert any("Додано" in r for r in picked), picked

    done = await tap("Це все", tg_id=1001)
    assert any("Вітаю" in r for r in done), done

    async with maker() as s:
        teacher = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 1001))
        assert [c.name for c in await classes_for_teacher(s, teacher.id)] == ["1-А"]


async def test_teacher_adds_a_second_class(send, tap, maker):
    from school_bot.domain.classes import create_classes
    from school_bot.domain.meals import classes_for_teacher

    async with maker() as s:
        await create_classes(s, "1-А, 3-Б, 5-В")
        await s.commit()

    await send(tg_id=1002, contact=_own_contact(1002, "+380991111002"))
    await send("Шевчук Оксана Петрівна", tg_id=1002)

    await tap("1-А", tg_id=1002)
    again = await tap("Ще один клас", tg_id=1002)
    assert any("Оберіть ще один клас" in r for r in again), again

    await tap("3-Б", tg_id=1002)
    await tap("Це все", tg_id=1002)

    async with maker() as s:
        teacher = await s.scalar(select(Teacher).where(Teacher.tg_user_id == 1002))
        assert [c.name for c in await classes_for_teacher(s, teacher.id)] == ["1-А", "3-Б"]


async def test_already_picked_class_is_not_offered_again(send, tap, api_bot, maker):
    """Перевіряти треба саму сітку, а не текст запрошення над нею."""
    from school_bot.domain.classes import create_classes

    async with maker() as s:
        await create_classes(s, "1-А, 3-Б, 5-В")
        await s.commit()

    await send(tg_id=1003, contact=_own_contact(1003, "+380991111003"))
    await send("Мельник Ігор Богданович", tg_id=1003)
    await tap("1-А", tg_id=1003)
    await tap("Ще один клас", tg_id=1003)

    offered = api_bot.buttons
    assert "1-А" not in offered, f"вже обраний клас пропонується знову: {offered}"
    assert "3-Б" in offered and "5-В" in offered, offered


async def test_picking_stops_when_no_classes_left(send, tap, people, maker):
    """Єдиний клас обрано — далі пропонувати нічого."""
    await send(tg_id=1004, contact=_own_contact(1004, "+380991111004"))
    await send("Бондаренко Ольга", tg_id=1004)
    await tap("1-А", tg_id=1004)

    replies = await tap("Ще один клас", tg_id=1004)
    assert any("всі класи" in r.lower() for r in replies), replies
    assert any("Вітаю" in r for r in replies), replies


async def test_changing_name_later_does_not_ask_for_classes_again(send, tap, people, maker):
    await send(tg_id=1005, contact=_own_contact(1005, "+380991111005"))
    await send("Гнатюк Леся", tg_id=1005)
    await tap("1-А", tg_id=1005)
    await tap("Це все", tg_id=1005)

    await send("/name", tg_id=1005)
    replies = await send("Гнатюк Леся Андріївна", tg_id=1005)

    assert not any("Оберіть свій клас" in r for r in replies), replies
    assert any("Вітаю, Гнатюк Леся Андріївна" in r for r in replies), replies


async def test_text_during_class_picking_does_not_hang(send, tap, people, maker):
    """Текст замість кнопки має вивести зі стану, а не лишити людину в ньому."""
    await send(tg_id=1006, contact=_own_contact(1006, "+380991111006"))
    await send("Ткаченко Наталія", tg_id=1006)

    replies = await send("а можна без класу?", tg_id=1006)
    assert any("вибір класів припинив" in r.lower() for r in replies), replies
    assert any("Вітаю" in r for r in replies), replies

    # Стан очищено: наступне повідомлення обробляється як звичайне.
    assert any("Як це працює" in r for r in await send("/help", tg_id=1006))
