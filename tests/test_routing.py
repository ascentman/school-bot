"""Структурні перевірки маршрутизації.

Порядок роутерів у aiogram визначає, хто перехопить повідомлення першим.
Помилка тут не падає — вона тихо ламає інші сценарії, тому перевіряється окремо.
"""

from __future__ import annotations

from school_bot.bot.commands import ADMIN_COMMANDS, TEACHER_COMMANDS

TEACHER_ALLOWED = {"start", "today", "help"}

# Диспетчер приходить із conftest: роутери — модульні синглтони, тож він
# збирається один раз на всю сесію тестів.


def test_fallback_router_is_last(dispatcher):
    """Перехоплювач усього має бути останнім, інакше він зʼїсть ввід у FSM."""
    names = [r.name for r in dispatcher.sub_routers]
    assert names[-1] == "fallback"
    assert "start" in names and "admin" in names and "daily" in names


def test_only_fallback_catches_all_text(dispatcher):
    """Жоден роутер, окрім fallback, не має ловити будь-який текст без умов."""
    for router in dispatcher.sub_routers:
        if router.name == "fallback":
            continue
        for handler in router.message.handlers:
            assert handler.filters, (
                f"Роутер {router.name} має хендлер без фільтрів — "
                "він перехопить усі повідомлення"
            )


def test_teacher_menu_has_no_admin_commands():
    """Вчитель не повинен бачити команди, які для нього мовчать."""
    assert {c.command for c in TEACHER_COMMANDS} == TEACHER_ALLOWED


def test_admin_menu_extends_teacher_menu():
    teacher = [c.command for c in TEACHER_COMMANDS]
    admin = [c.command for c in ADMIN_COMMANDS]
    assert admin[: len(teacher)] == teacher
    assert "import_teachers" in admin and "sync" in admin


def test_every_admin_command_has_a_handler(dispatcher):
    """Команда в меню без хендлера — мовчання у відповідь на дотик."""
    registered: set[str] = set()
    for router in dispatcher.sub_routers:
        for handler in router.message.handlers:
            for flt in handler.filters:
                registered.update(_commands_in(flt.callback))

    missing = {c.command for c in ADMIN_COMMANDS} - registered - {"start"}
    assert not missing, f"Команди без обробника: {sorted(missing)}"


def _commands_in(callback: object) -> set[str]:
    """Витягти імена команд із фільтра: Command("x"), F.text == "/x", F.text.in_({...})."""
    found: set[str] = set()

    # aiogram.filters.Command
    for name in getattr(callback, "commands", ()) or ():
        found.add(str(name))

    # MagicFilter: значення лежать у слотах операцій, не в repr
    magic = getattr(callback, "__self__", None)
    for operation in getattr(magic, "_operations", ()) or ():
        for slot in getattr(type(operation), "__slots__", ()):
            found.update(_strings_in(getattr(operation, slot, None)))
    return found


def _strings_in(value: object, depth: int = 0) -> set[str]:
    """Зібрати всі '/команди' зі значення — воно буває вкладеним (F.text.in_({...}))."""
    if depth > 3:
        return set()
    if isinstance(value, str):
        return {value.lstrip("/")} if value.startswith("/") else set()
    if isinstance(value, (set, frozenset, list, tuple)):
        return {s for item in value for s in _strings_in(item, depth + 1)}
    if isinstance(value, dict):
        return {s for item in value.values() for s in _strings_in(item, depth + 1)}
    return set()


# --- планувальник ---------------------------------------------------------
#
# Регресія: після рефакторингу джоби отримали обовʼязковий параметр `d`, а
# планувальник передає лише (bot, maker). APScheduler валідує сигнатуру при
# add_job, тож бот падав на старті — і жоден тест цього не бачив, бо
# планувальник ніхто не збирав.


def _scheduler():
    from unittest.mock import MagicMock

    from school_bot.bot.main import build_scheduler

    return build_scheduler(MagicMock())


def test_scheduler_builds():
    """add_job валідує аргументи — сама збірка вже є перевіркою."""
    assert _scheduler().get_jobs()


def test_scheduler_registers_whole_daily_plan():
    from school_bot.scheduler.jobs import daily_plan

    ids = {j.id for j in _scheduler().get_jobs()}
    assert {key for key, _, _ in daily_plan()} <= ids
    assert "sheets" in ids


def test_scheduler_jobs_survive_a_missing_date():
    """Кожен джоб має запускатися без явної дати — саме так його кличе cron."""
    import inspect

    from school_bot.scheduler.jobs import daily_plan

    for key, _, run in daily_plan():
        params = inspect.signature(run).parameters
        assert params["d"].default is not inspect.Parameter.empty, (
            f"джоб {key}: параметр d обовʼязковий, планувальник його не передає"
        )


def test_scheduler_defaults_survive_blackouts():
    sch = _scheduler()
    assert sch._job_defaults["misfire_grace_time"] >= 3600
    assert sch._job_defaults["coalesce"] is True
