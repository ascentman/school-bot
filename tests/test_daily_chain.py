"""Ланцюжок із трьох питань: харчування → відсутні → з них хворі.

`bot/handlers/daily.py` донедавна не мав жодного тесту, хоча це та єдина
взаємодія, яку 25 вчителів виконують щоранку. Тут перевіряється саме поведінка
ланцюжка, а не рендер: що зберігається, коли третє питання не ставиться і що
переживає повторний прохід.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from school_bot.bot.callbacks import MealAbsent, MealSet, MealSick
from school_bot.db.models import MealEntry, MealEntryAudit, MealField
from tests.conftest import MONDAY


async def _entry(maker, class_id: int, d=MONDAY) -> MealEntry:
    async with maker() as s:
        return await s.scalar(
            select(MealEntry).where(MealEntry.class_id == class_id, MealEntry.date == d)
        )


async def _press(dispatcher, api_bot, maker, data: str, *, tg_id: int) -> list[str]:
    """Натиснути кнопку за сирим callback_data — як це робить Telegram."""
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    import tests.conftest as c

    c._ACTIVE_MAKER["maker"] = maker
    api_bot.calls.clear()
    message = Message(
        message_id=5,
        date=datetime.now(UTC),
        chat=Chat(id=tg_id, type="private"),
        from_user=User(id=api_bot.id, is_bot=True, first_name="bot"),
        text="…",
    ).as_(api_bot)
    query = CallbackQuery(
        id="1",
        from_user=User(id=tg_id, is_bot=False, first_name="Тест"),
        chat_instance="1",
        message=message,
        data=data,
    ).as_(api_bot)
    await dispatcher.feed_update(api_bot, Update(update_id=99, callback_query=query))
    return api_bot.texts


async def test_answering_the_meal_count_asks_about_absentees(
    dispatcher, api_bot, maker, school
):
    class_id = school["classes"][0]
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(),
        tg_id=1001,
    )
    assert any("Всього відсутніх" in t for t in texts_out)
    assert (await _entry(maker, class_id)).eating_count == 24


async def test_absentees_above_zero_ask_about_sickness(dispatcher, api_bot, maker, school):
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(), tg_id=1001,
    )
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(), tg_id=1001,
    )

    assert any("по хворобі" in t for t in texts_out)
    entry = await _entry(maker, class_id)
    assert entry.absent_count == 3 and entry.sick_count is None


async def test_zero_absentees_skips_the_sickness_question(dispatcher, api_bot, maker, school):
    """Нема відсутніх — нема кого питати про хворих, і хворих рівно нуль."""
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(), tg_id=1001,
    )
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=0).pack(), tg_id=1001,
    )

    assert not any("по хворобі" in t for t in texts_out)
    entry = await _entry(maker, class_id)
    assert entry.absent_count == 0
    assert entry.sick_count == 0        # очевидна відповідь, а не дірка у звіті


async def test_skipping_keeps_the_meal_count_and_leaves_absent_empty(
    dispatcher, api_bot, maker, school
):
    """Головна обіцянка кнопки «Пропустити»."""
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=None).pack(), tg_id=1001,
    )

    entry = await _entry(maker, class_id)
    assert entry.eating_count == 24     # цифра, заради якої все й робиться
    assert entry.absent_count is None
    assert entry.sick_count is None


async def test_sick_cannot_exceed_absent(dispatcher, api_bot, maker, school):
    """Стара кнопка з більшої сітки не має записати хворих більше за відсутніх.

    Кнопки живуть у чаті вічно: вчитель відповів «5 відсутніх», дістав пад
    0..5, потім повернувся й зменшив відсутніх до 2 — стара «5» досі
    натискається.
    """
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=5).pack(), tg_id=1001,
    )

    entry = await _entry(maker, class_id)
    assert entry.sick_count is None, "5 хворих при 2 відсутніх не має записатися"


async def test_correcting_the_meal_count_keeps_absent_and_sick(
    dispatcher, api_bot, maker, school
):
    """Повторний прохід ланцюжком не стирає вже подані цифри."""
    class_id = school["classes"][0]
    for data in (
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(),
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(),
    ):
        await _press(dispatcher, api_bot, maker, data, tg_id=1001)

    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=26).pack(), tg_id=1001,
    )

    entry = await _entry(maker, class_id)
    assert (entry.eating_count, entry.absent_count, entry.sick_count) == (26, 3, 2)


async def test_every_step_is_journalled_with_its_field(dispatcher, api_bot, maker, school):
    class_id = school["classes"][0]
    for data in (
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack(),
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(),
    ):
        await _press(dispatcher, api_bot, maker, data, tg_id=1001)

    async with maker() as s:
        rows = list(await s.scalars(select(MealEntryAudit)))
    assert [r.changed_field for r in rows] == [
        MealField.EATING, MealField.ABSENT, MealField.SICK
    ]


async def test_buttons_from_before_this_release_still_work(
    dispatcher, api_bot, maker, school
):
    """НЕ ВИДАЛЯТИ. Бот у проді: у чатах вчителів висять старі кнопки «ms:».

    Якби до MealSet додали поле, кожна з них перестала б розпаковуватися й
    мовчки провалювалася б у fallback. Тест фіксує саме сумісність формату.
    """
    class_id = school["classes"][0]
    old_button = MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack()
    assert old_button.count(":") == 3, "формат callback_data змінився"

    texts_out = await _press(dispatcher, api_bot, maker, old_button, tg_id=1001)

    assert not any("не зрозумів" in t.lower() for t in texts_out)
    assert (await _entry(maker, class_id)).eating_count == 24


async def test_manual_entry_also_enters_the_chain(dispatcher, api_bot, maker, school, send):
    """«✏️ Інша цифра» не має бути тихим обхідним шляхом повз відсутніх."""
    from school_bot.bot.callbacks import MealManual

    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealManual(class_id=class_id, d=MONDAY.toordinal()).pack(), tg_id=1001,
    )
    texts_out = await send("27", tg_id=1001)

    assert any("Всього відсутніх" in t for t in texts_out)
    entry = await _entry(maker, class_id)
    assert entry.eating_count == 27


async def test_admin_goes_through_the_same_chain(dispatcher, api_bot, maker, school):
    """Адмін вводить за клас, який не подав, — і теж проходить усі кроки."""
    class_id = school["classes"][2]      # клас Оксани-адміна
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=MONDAY.toordinal(), value=18).pack(), tg_id=2002,
    )
    assert any("Всього відсутніх" in t for t in texts_out)


async def test_a_past_day_is_untouched_by_the_chain(dispatcher, api_bot, maker, school):
    """Ланцюжок працює за конкретну дату з кнопки, а не за «сьогодні»."""
    class_id = school["classes"][0]
    past = date(2026, 9, 1)
    await _press(
        dispatcher, api_bot, maker,
        MealSet(class_id=class_id, d=past.toordinal(), value=20).pack(), tg_id=2002,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=past.toordinal(), value=1).pack(), tg_id=2002,
    )

    entry = await _entry(maker, class_id, past)
    assert entry is not None and entry.absent_count == 1
    assert await _entry(maker, class_id, MONDAY) is None
