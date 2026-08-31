"""Конфігурація застосунку (.env → typed settings)."""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

# Тести мають бачити дефолти з коду, а не .env розробника — інакше вони
# проходять чи падають залежно від чийогось особистого конфігу.
_ENV_FILE = None if os.getenv("SCHOOL_BOT_NO_DOTENV") else BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(default="", description="Токен від @BotFather")
    school_name: str = "Загальноосвітня школа"

    # Усі класи школи. Створюються при старті, якщо їх ще немає, і саме з них
    # вчитель обирає свої під час реєстрації. Наявні класи не видаляються:
    # за ними лишається історія записів.
    school_classes: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Telegram ID тих, хто отримує права адміна при першому /start.
    # NoDecode: без нього pydantic-settings намагається розібрати значення з .env
    # як JSON ще до валідаторів, і звичайний список через кому падає.
    bootstrap_admins: Annotated[list[int], NoDecode] = Field(default_factory=list)

    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'school.db'}"
    timezone: str = "Europe/Kyiv"

    prompt_time: time = time(9, 5)
    remind_times: Annotated[list[time], NoDecode] = Field(
        default_factory=lambda: [time(9, 15), time(9, 30)]
    )
    digest_time: time = time(9, 45)
    sheets_rebuild_time: time = time(20, 0)

    # Після цієї години догоняюча розсилка вже не має сенсу: навчальний день
    # закінчився, і запит о 19:00 лише дратуватиме вчителя.
    catch_up_deadline: time = time(15, 0)

    # Скільки часу джоб лишається дійсним, якщо не спрацював вчасно.
    # За замовчуванням APScheduler дає 1 секунду — при блекауті цього замало.
    misfire_grace_seconds: int = 2 * 60 * 60

    # Межі валідації кількості дітей у класі.
    max_children: int = 40

    # Google Sheets (необовʼязково — без них бот працює, синк просто вимкнений).
    google_credentials_file: Path | None = None
    google_sheet_id: str | None = None

    log_level: str = "INFO"

    @field_validator("bootstrap_admins", mode="before")
    @classmethod
    def _split_admins(cls, v: object) -> object:
        """Дозволяє записати BOOTSTRAP_ADMINS=111,222 у .env."""
        if isinstance(v, str):
            return [int(part) for part in v.replace(" ", "").split(",") if part]
        return v

    @field_validator("school_classes", mode="before")
    @classmethod
    def _split_classes(cls, v: object) -> object:
        """Дозволяє записати SCHOOL_CLASSES=1-А,1-Б,2-А у .env."""
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("remind_times", mode="before")
    @classmethod
    def _split_times(cls, v: object) -> object:
        """Дозволяє записати REMIND_TIMES=09:30,09:45 у .env."""
        if isinstance(v, str):
            return [part for part in v.replace(" ", "").split(",") if part]
        return v

    @field_validator("remind_times")
    @classmethod
    def _sort_times(cls, v: list[time]) -> list[time]:
        return sorted(set(v))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.google_credentials_file and self.google_sheet_id)


    def ensure_storage(self) -> None:
        """Створити директорію під SQLite-файл.

        Викликається при завантаженні конфігу, а не в db/base.py: alembic імпортує
        лише config, тож інакше `alembic upgrade head` на чистому томі падає з
        «unable to open database file».
        """
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            Path(self.database_url[len(prefix) :]).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_storage()
