"""Конфігурація застосунку (.env → typed settings)."""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from school_bot.domain.slots import MealSlot, parse_meal_slots

BASE_DIR = Path(__file__).resolve().parents[2]

# Навмисно приблизна: завдання — спіймати одрук на кшталт пропущеної @ чи
# крапки, а не відтворити RFC 5322.
EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[a-zA-Z]{2,}$")

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
    # Два окремі звіти замість одного спільного: харчування читає кухня, а
    # відсутніх — медсестра й класні керівники. Різні читачі, різні аркуші.
    meals_report_time: time = time(9, 40)
    absence_report_time: time = time(9, 50)
    sheets_rebuild_time: time = time(20, 0)

    # Розклад роздачі: у якому порядку класи йдуть до їдальні. Впливає лише на
    # щоденний PDF — саме так його читає перевірка. Порожній список означає
    # простий перелік класів без розбивки на зміни.
    meal_slots: Annotated[list[MealSlot], NoDecode] = Field(default_factory=list)

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

    # Пошта — необовʼязково, як і Google Sheets: без неї бот працює, лист просто
    # не йде. Дефолти під Gmail; SMTP_PASSWORD там — «пароль додатка» (App
    # Password), а не звичайний пароль від скриньки.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = False        # true → SMTPS (465); інакше STARTTLS (587)
    smtp_from: str = ""           # порожнє → SMTP_USER
    smtp_timeout: int = 30

    # Кому надсилати щоденний звіт. Порожньо → розсилка вимкнена.
    report_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

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

    @field_validator("smtp_password", mode="before")
    @classmethod
    def _strip_password(cls, v: object) -> object:
        """Прибрати пробіли з «пароля додатка».

        Google показує його чотирма групами по чотири символи й
        розділяє нерозривними пробілами (\xa0). Скопійований як є, він валить
        авторизацію в smtplib з UnicodeEncodeError замість зрозумілого «невірний
        пароль». Сам пароль пробілів не містить, тож прибрати їх безпечно.
        """
        if isinstance(v, str):
            return "".join(v.split())
        return v

    @field_validator("report_emails", mode="before")
    @classmethod
    def _split_emails(cls, v: object) -> object:
        """Дозволяє записати REPORT_EMAILS=a@b.ua, c@d.ua у .env."""
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("report_emails")
    @classmethod
    def _check_emails(cls, v: list[str]) -> list[str]:
        """Одрук в адресі має впасти на старті, а не мовчки з'їсти розсилку."""
        bad = [a for a in v if not EMAIL_RE.match(a)]
        if bad:
            raise ValueError(f"REPORT_EMAILS: не схоже на адресу — {', '.join(bad)}")
        return v

    @field_validator("meal_slots", mode="before")
    @classmethod
    def _parse_meal_slots(cls, v: object) -> object:
        """Дозволяє записати MEAL_SLOTS=08:45-09:00 = 3-А,3-Б; ... у .env."""
        if isinstance(v, str):
            return parse_meal_slots(v)
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

    @property
    def email_enabled(self) -> bool:
        """Чи достатньо налаштувань, щоб надіслати лист."""
        return bool(
            self.smtp_host and self.smtp_user and self.smtp_password and self.report_emails
        )

    @property
    def mail_from(self) -> str:
        return self.smtp_from or self.smtp_user


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
