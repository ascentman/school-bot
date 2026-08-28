"""Схема БД має створюватися так, щоб Alembic про неї знав.

Регресія: раніше CLI створював таблиці через Base.metadata.create_all, не
записуючи версію в alembic_version. Після цього штатний `alembic upgrade head`
(перша команда в Docker CMD) падав з «table already exists» — і деплой ламався
назавжди.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from school_bot.config import BASE_DIR


def _run(cmd: list[str], db: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "SCHOOL_BOT_NO_DOTENV": "1",
        "HOME": str(Path.home()),
    }
    return subprocess.run(
        cmd, cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120
    )


@pytest.mark.slow
def test_cli_first_then_alembic(tmp_path: Path):
    """CLI на чистій машині, потім штатний деплой — не має бути конфлікту."""
    db = tmp_path / "a.db"

    first = _run([sys.executable, "-m", "school_bot.cli", "status"], db)
    assert first.returncode == 0, first.stderr

    upgrade = _run([sys.executable, "-m", "alembic", "upgrade", "head"], db)
    assert upgrade.returncode == 0, upgrade.stderr
    assert "already exists" not in upgrade.stderr


@pytest.mark.slow
def test_alembic_first_then_cli(tmp_path: Path):
    """Штатний порядок: міграції, потім застосунок."""
    db = tmp_path / "b.db"

    upgrade = _run([sys.executable, "-m", "alembic", "upgrade", "head"], db)
    assert upgrade.returncode == 0, upgrade.stderr

    after = _run([sys.executable, "-m", "school_bot.cli", "status"], db)
    assert after.returncode == 0, after.stderr
    assert "Класів" in after.stdout


@pytest.mark.slow
def test_schema_is_stamped(tmp_path: Path):
    """Після старту застосунку Alembic має бачити актуальну версію."""
    db = tmp_path / "c.db"
    _run([sys.executable, "-m", "school_bot.cli", "status"], db)

    current = _run([sys.executable, "-m", "alembic", "current"], db)
    assert current.returncode == 0, current.stderr
    assert "head" in current.stdout, f"alembic_version не проставлено: {current.stdout!r}"
