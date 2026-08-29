from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from school_bot.db.models import Base, ClassAssignment, Role, SchoolClass, Teacher

MONDAY = date(2026, 9, 7)
SATURDAY = date(2026, 9, 5)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Чиста in-memory БД на кожен тест."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def classes(session: AsyncSession) -> list[SchoolClass]:
    rows = [
        SchoolClass(name="1-А", grade=1, letter="А", sort_order=1),
        SchoolClass(name="3-Б", grade=3, letter="Б", sort_order=2),
        SchoolClass(name="5-В", grade=5, letter="В", sort_order=3),
    ]
    session.add_all(rows)
    await session.flush()
    return rows


@pytest_asyncio.fixture
async def teacher(session: AsyncSession) -> Teacher:
    t = Teacher(full_name="Марія Коваленко", tg_user_id=1001, role=Role.TEACHER)
    session.add(t)
    await session.flush()
    return t


@dataclass
class SentMessage:
    chat_id: int
    text: str
    markup: object | None = None


@dataclass
class FakeBot:
    """Мінімальний двійник Bot: запамʼятовує все, що йому передали."""

    sent: list[SentMessage] = field(default_factory=list)

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent.append(SentMessage(chat_id, text, reply_markup))
        return SentMessage(chat_id, text, reply_markup)

    def to(self, chat_id: int) -> list[SentMessage]:
        return [m for m in self.sent if m.chat_id == chat_id]


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def school(maker):
    """2 класи у Марії (tg 1001), 1 клас у Оксани-адміна (tg 2002)."""
    async with maker() as s:
        c1 = SchoolClass(name="1-А", grade=1, letter="А", sort_order=1)
        c2 = SchoolClass(name="3-Б", grade=3, letter="Б", sort_order=2)
        c3 = SchoolClass(name="5-В", grade=5, letter="В", sort_order=3)
        maria = Teacher(full_name="Марія", tg_user_id=1001, role=Role.TEACHER)
        oksana = Teacher(full_name="Оксана", tg_user_id=2002, role=Role.ADMIN)
        s.add_all([c1, c2, c3, maria, oksana])
        await s.flush()
        s.add_all([
            ClassAssignment(class_id=c1.id, teacher_id=maria.id, is_primary=True),
            ClassAssignment(class_id=c2.id, teacher_id=maria.id, is_primary=True),
            ClassAssignment(class_id=c3.id, teacher_id=oksana.id, is_primary=True),
        ])
        await s.commit()
        return {"classes": [c1.id, c2.id, c3.id], "maria": maria.id, "oksana": oksana.id}


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- наскрізний прогін апдейтів через диспетчер ---------------------------
#
# Структурні тести маршрутизації не ловлять помилок у middleware й фільтрах:
# роутер може бути на місці, але мовчки відхиляти все. Тому окремо ганяємо
# справжні Update через справжній Dispatcher.


class RecordingBot:
    """Двійник Bot: замість мережі записує викликані методи API."""

    id = 1
    username = "school44_bot"

    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        return _canned_response(method)

    async def me(self):
        return User(id=self.id, is_bot=True, first_name="school44", username=self.username)

    get_me = me

    @property
    def texts(self) -> list[str]:
        return [getattr(c, "text", "") or "" for c in self.calls]

    @property
    def buttons(self) -> list[str]:
        """Підписи inline-кнопок останньої відповіді."""
        return [
            b.text
            for call in self.calls
            for row in getattr(getattr(call, "reply_markup", None), "inline_keyboard", [])
            for b in row
        ]

    def said(self, fragment: str) -> bool:
        return any(fragment.lower() in t.lower() for t in self.texts)


def _canned_response(method):
    from aiogram.methods import EditMessageText, SendMessage

    if isinstance(method, EditMessageText):
        return Message(
            message_id=5,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            text=method.text,
        )
    if isinstance(method, SendMessage):
        return Message(
            message_id=999,
            date=datetime.now(UTC),
            chat=Chat(id=method.chat_id, type="private"),
            text=method.text,
        )
    return True


# Диспетчер будується один раз: роутери — модульні синглтони й не переприкріплюються.
_ACTIVE_MAKER: dict[str, object] = {}


class _MakerProxy:
    def __call__(self):
        return _ACTIVE_MAKER["maker"]()


@pytest.fixture(scope="session")
def dispatcher():
    from school_bot.bot.main import build_dispatcher

    return build_dispatcher(session_maker=_MakerProxy())


@pytest.fixture
def api_bot() -> RecordingBot:
    return RecordingBot()


@pytest.fixture
def send(dispatcher, api_bot, maker):
    """Надіслати текст від імені користувача й повернути відповіді бота."""
    _ACTIVE_MAKER["maker"] = maker

    async def _send(
        text: str | None = None,
        *,
        tg_id: int,
        contact: Contact | None = None,
        name: str = "Тест",
    ) -> list[str]:
        api_bot.calls.clear()
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=tg_id, type="private"),
            from_user=User(id=tg_id, is_bot=False, first_name=name),
            text=text,
            contact=contact,
        ).as_(api_bot)
        await dispatcher.feed_update(api_bot, Update(update_id=1, message=message))
        return api_bot.texts

    return _send


@pytest.fixture
def tap(dispatcher, api_bot, maker):
    """Натиснути inline-кнопку з останньої відповіді бота."""
    _ACTIVE_MAKER["maker"] = maker

    async def _tap(label: str, *, tg_id: int) -> list[str]:
        buttons = [
            b
            for call in api_bot.calls
            for row in getattr(getattr(call, "reply_markup", None), "inline_keyboard", [])
            for b in row
        ]
        match = next((b for b in buttons if label in b.text), None)
        assert match is not None, f"кнопки «{label}» немає серед {[b.text for b in buttons]}"

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
            data=match.callback_data,
        ).as_(api_bot)
        await dispatcher.feed_update(api_bot, Update(update_id=2, callback_query=query))
        return api_bot.texts

    return _tap
