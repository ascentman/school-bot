"""Ланцюжок із двох питань: відсутні → з них хворі.

`bot/handlers/daily.py` — та єдина взаємодія, яку 25 вчителів виконують
щоранку. Тут перевіряється саме поведінка ланцюжка, а не рендер: що
зберігається, коли друге питання не ставиться і що переживає повторний прохід.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from school_bot.bot.callbacks import MealAbsent, MealSick
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


async def test_the_prompt_asks_about_absentees_first(dispatcher, api_bot, maker, school):
    """Перший тап по цифрі створює запис дня — саме з цього починається звіт."""
    class_id = school["classes"][0]
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        tg_id=1001,
    )
    assert any("по хворобі" in t for t in texts_out)
    entry = await _entry(maker, class_id)
    assert entry.absent_count == 3 and entry.sick_count is None


async def test_a_new_entry_leaves_the_meal_count_empty(dispatcher, api_bot, maker, school):
    """Харчування знято з обліку: ланцюжок його не торкається."""
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(), tg_id=1001,
    )
    assert (await _entry(maker, class_id)).eating_count is None


async def test_zero_absentees_skips_the_sickness_question(dispatcher, api_bot, maker, school):
    """Нема відсутніх — нема кого питати про хворих, і хворих рівно нуль."""
    class_id = school["classes"][0]
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=0).pack(), tg_id=1001,
    )

    assert not any("по хворобі" in t for t in texts_out)
    entry = await _entry(maker, class_id)
    assert entry.absent_count == 0
    assert entry.sick_count == 0        # очевидна відповідь, а не дірка у звіті


async def test_skipping_the_sickness_question_keeps_the_absent_count(
    dispatcher, api_bot, maker, school
):
    """Головна обіцянка кнопки «Пропустити» на другому кроці."""
    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=None).pack(), tg_id=1001,
    )

    entry = await _entry(maker, class_id)
    assert entry.absent_count == 3      # цифра, заради якої все й робиться
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
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=5).pack(), tg_id=1001,
    )

    entry = await _entry(maker, class_id)
    assert entry.sick_count is None, "5 хворих при 2 відсутніх не має записатися"


async def test_correcting_absent_keeps_a_smaller_sick(dispatcher, api_bot, maker, school):
    """Повторний прохід ланцюжком не стирає вже подану цифру хворих."""
    class_id = school["classes"][0]
    for data in (
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(),
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=5).pack(),
    ):
        await _press(dispatcher, api_bot, maker, data, tg_id=1001)

    entry = await _entry(maker, class_id)
    assert (entry.absent_count, entry.sick_count) == (5, 2)


async def test_every_step_is_journalled_with_its_field(dispatcher, api_bot, maker, school):
    class_id = school["classes"][0]
    for data in (
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(),
    ):
        await _press(dispatcher, api_bot, maker, data, tg_id=1001)

    async with maker() as s:
        rows = list(await s.scalars(select(MealEntryAudit)))
    assert [r.changed_field for r in rows] == [MealField.ABSENT, MealField.SICK]


async def test_callback_format_did_not_change(dispatcher, api_bot, maker, school):
    """НЕ ВИДАЛЯТИ. Бот у проді: у чатах вчителів висять надіслані кнопки.

    Якби до MealAbsent додали поле, кожна з них перестала б розпаковуватися й
    мовчки провалювалася б у fallback. Тест фіксує саме сумісність формату.
    """
    class_id = school["classes"][0]
    button = MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack()
    assert button.count(":") == 3, "формат callback_data змінився"

    texts_out = await _press(dispatcher, api_bot, maker, button, tg_id=1001)

    assert not any("не зрозумів" in t.lower() for t in texts_out)
    assert (await _entry(maker, class_id)).absent_count == 3


async def test_buttons_of_the_dropped_meal_question_say_so(
    dispatcher, api_bot, maker, school
):
    """НЕ ВИДАЛЯТИ. У чатах вчителів висять ранкові запити «скільки харчуються».

    Telegram не дає їх прибрати, тож тап по такій кнопці має дати зрозумілу
    підказку, а не мовчання бота й порожній запис у БД.
    """
    from school_bot.bot.callbacks import MealSet

    class_id = school["classes"][0]
    old_button = MealSet(class_id=class_id, d=MONDAY.toordinal(), value=24).pack()

    await _press(dispatcher, api_bot, maker, old_button, tg_id=1001)

    answers = [c for c in api_bot.calls if type(c).__name__ == "AnswerCallbackQuery"]
    assert answers, "тап по застарілій кнопці лишився без відповіді"
    assert "застарів" in (answers[0].text or "")
    assert await _entry(maker, class_id) is None, "застаріла кнопка не має писати в БД"


async def test_absent_above_the_pad_can_be_entered_by_hand(
    dispatcher, api_bot, maker, school, send
):
    """Карантин: відсутніх більше, ніж кнопок на сітці.

    Знайдено на рев'ю PR #11 — без «Іншої цифри» цифра мовчки обрізалася б до
    найбільшої кнопки, і звіт занизив би відсутність без жодного попередження.
    """
    from school_bot.bot.callbacks import MealManualAbsent

    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealManualAbsent(class_id=class_id, d=MONDAY.toordinal()).pack(), tg_id=1001,
    )
    texts_out = await send("23", tg_id=1001)

    assert (await _entry(maker, class_id)).absent_count == 23
    assert any("по хворобі" in t for t in texts_out)   # ланцюжок триває


async def test_manual_sick_still_respects_the_cap(dispatcher, api_bot, maker, school, send):
    """Ручний ввід — другий шлях, а не обхідний повз стелю."""
    from school_bot.bot.callbacks import MealManualSick

    class_id = school["classes"][0]
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(), tg_id=1001,
    )
    await _press(
        dispatcher, api_bot, maker,
        MealManualSick(class_id=class_id, d=MONDAY.toordinal()).pack(), tg_id=1001,
    )
    await send("9", tg_id=1001)

    assert (await _entry(maker, class_id)).sick_count is None, "9 хворих при 3 відсутніх"


async def test_lowering_absent_later_clips_sick(dispatcher, api_bot, maker, school):
    """Сценарій із рев'ю: зменшили відсутніх і пропустили другий крок."""
    class_id = school["classes"][0]
    for data in (
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=5).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=3).pack(),
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(),
        MealSick(class_id=class_id, d=MONDAY.toordinal(), value=None).pack(),
    ):
        await _press(dispatcher, api_bot, maker, data, tg_id=1001)

    entry = await _entry(maker, class_id)
    assert (entry.absent_count, entry.sick_count) == (2, 2)


async def test_admin_goes_through_the_same_chain(dispatcher, api_bot, maker, school):
    """Адмін вводить за клас, який не подав, — і теж проходить усі кроки."""
    class_id = school["classes"][2]      # клас Оксани-адміна
    texts_out = await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=MONDAY.toordinal(), value=2).pack(), tg_id=2002,
    )
    assert any("по хворобі" in t for t in texts_out)


async def test_a_past_day_is_untouched_by_the_chain(dispatcher, api_bot, maker, school):
    """Ланцюжок працює за конкретну дату з кнопки, а не за «сьогодні»."""
    class_id = school["classes"][0]
    past = date(2026, 9, 1)
    await _press(
        dispatcher, api_bot, maker,
        MealAbsent(class_id=class_id, d=past.toordinal(), value=1).pack(), tg_id=2002,
    )

    entry = await _entry(maker, class_id, past)
    assert entry is not None and entry.absent_count == 1
    assert await _entry(maker, class_id, MONDAY) is None
