"""Усі українські рядки в одному місці — щоб правити формулювання, не чіпаючи логіку."""

from __future__ import annotations

from datetime import date as Date
from html import escape

from school_bot.config import settings
from school_bot.db.models import MAX_NAME_LEN
from school_bot.domain.dates import format_date, plural_children


def esc(value: str) -> str:
    """Екранувати те, що прийшло від людини.

    Бот працює в parse_mode=HTML, а ПІБ відтоді, як зʼявилася самореєстрація,
    задає будь-хто. Символ «<» ламає розбір сутностей і валить відповідь
    необробленим TelegramBadRequest; навмисний тег перетворює список вчителів
    на клікабельне посилання в чаті адміністратора.
    """
    return escape(value or "", quote=False)

# --- Доступ ---------------------------------------------------------------

NOT_REGISTERED = (
    "👋 Вітаю!\n\n"
    "Цей бот призначений для працівників школи. Ваш обліковий запис не знайдено.\n"
    "Зверніться до адміністратора, щоб отримати запрошення."
)
ASK_CONTACT = (
    "👋 Вітаю! Це бот обліку харчування учнів.\n\n"
    "Щоб я знайшов вас у списку працівників, натисніть кнопку нижче — "
    "Telegram надішле мені ваш номер телефону.\n\n"
    "<i>Номер потрібен лише для впізнання. Нікуди більше не передається.</i>"
)
BTN_SHARE_CONTACT = "📱 Поділитися номером"
ASK_FULL_NAME = (
    "Дякую! Тепер напишіть, будь ласка, своє <b>ПІБ</b> — воно потрапить "
    "у списки й звіти.\n\n"
    "Наприклад: <code>Коваленко Марія Іванівна</code>"
)
NAME_ACCEPTED = "✅ Записав."
ACCOUNT_DISABLED = (
    "⛔ Ваш доступ вимкнено адміністратором.\n"
    "Якщо це помилка — зверніться до нього."
)
NAME_TOO_LONG = f"❗ Задовге ПІБ. Максимум — {MAX_NAME_LEN} символів."
NAME_NEEDS_LETTERS = (
    "❗ Схоже, це не ПІБ. Напишіть прізвище, імʼя та по батькові словами."
)
def name_postponed(current: str) -> str:
    """Показати, як людина записана зараз.

    Раніше тут було два повідомлення й прапорець, який намагався вгадати,
    первинна це реєстрація чи зміна. Будь-яка спроба вивести це з історії
    (стану FSM, ніку відправника) виявлялася ламкою — тому просто називаємо
    факт: він правдивий в обох випадках.
    """
    return (
        f"Гаразд. Зараз ви записані як <b>{esc(current)}</b>.\n"
        "Вказати або змінити ПІБ — команда /name."
    )
ASK_NAME_AGAIN = "Напишіть своє ПІБ одним повідомленням."
CONTACT_NOT_YOURS = (
    "❗ Це контакт іншої людини. Натисніть кнопку «📱 Поділитися номером», "
    "щоб надіслати свій."
)
INVITE_INVALID = (
    "🔗 Це запрошення вже використане або недійсне.\n"
    "Попросіть адміністратора надіслати нове."
)
INVITE_ALREADY_LINKED = "✅ Ви вже зареєстровані. Скористайтеся меню нижче."
ACCESS_DENIED = "⛔ Ця дія доступна лише адміністратору."


def welcome(name: str, class_names: list[str], is_admin: bool) -> str:
    lines = [f"✅ Вітаю, {esc(name)}!", ""]
    if class_names:
        lines.append("Ваші класи: " + ", ".join(esc(n) for n in class_names))
    else:
        # Без класів вчитель не отримає жодного запиту. Якщо про це не сказати,
        # він просто чекатиме й вважатиме, що бот не працює.
        lines.append(
            "⚠️ За вами ще не закріплено жодного класу, тому запити поки не "
            "надходитимуть. Зверніться до адміністратора."
        )
    if is_admin:
        lines.append("Роль: адміністратор")
    lines += [
        "",
        # Час береться з конфігу: зашитий у текст, він розходиться з розкладом
        # щойно адміністратор змінить PROMPT_TIME, і вчитель отримує хибну інструкцію.
        f"Щобудня о {settings.prompt_time:%H:%M} я надсилатиму запит "
        "про кількість дітей на харчуванні.",
        "Відповідь — один дотик по потрібній цифрі.",
    ]
    return "\n".join(lines)


# --- Щоденний запит -------------------------------------------------------


def prompt(class_name: str, d: Date) -> str:
    return (
        f"📋 <b>{esc(class_name)}</b> · {format_date(d, with_weekday=True)}\n\n"
        "Скільки дітей сьогодні харчуються?"
    )


def prompt_answered(class_name: str, d: Date, count: int, at: str, *, edited: bool = False) -> str:
    head = "✏️ Виправлено" if edited else "✅ Записано"
    return (
        f"✅ <b>{esc(class_name)}</b> · {format_date(d)} — <b>{plural_children(count)}</b>\n"
        f"<i>{head} о {esc(at)}.</i>"
    )


def reminder(class_name: str, d: Date) -> str:
    return (
        f"⏰ Нагадування: <b>{esc(class_name)}</b> · {format_date(d)}\n\n"
        "Дані про харчування ще не подані."
    )


TEACHER_HELP = (
    "ℹ️ <b>Як це працює</b>\n\n"
    "Щобудня вранці я надсилаю запит по кожному вашому класу. Треба лише "
    "натиснути потрібну цифру — писати нічого не потрібно.\n\n"
    "• Цифра над сіткою — скільки було минулого разу.\n"
    "• «✏️ Інша цифра» — якщо потрібного числа немає на екрані.\n"
    "• «✏️ Виправити» — змінити вже подану цифру за сьогодні.\n\n"
    "Якщо ви видалили повідомлення або хочете подати дані раніше — "
    "натисніть <b>📋 Мої класи</b> або команду /today.\n\n"
    "Змінити своє ПІБ — команда /name.\n\n"
    "У вихідні та на канікулах я мовчу."
)


def my_classes_header(d: Date) -> str:
    return (
        f"📋 <b>{format_date(d, with_weekday=True)}</b>\n"
        "Оберіть клас, щоб ввести або змінити дані:"
    )


NO_CLASSES_ASSIGNED = (
    "За вами не закріплено жодного класу.\n"
    "Зверніться до адміністратора — він призначить."
)
UNKNOWN_INPUT = (
    "Не зрозумів. Скористайтеся кнопками меню внизу "
    "або натисніть /help."
)

MANUAL_ASK = "Надішліть кількість дітей числом (наприклад: <code>27</code>)."
MANUAL_CANCELLED = "Скасовано."


def manual_invalid(max_children: int) -> str:
    return f"❗ Потрібне ціле число від 0 до {max_children}. Спробуйте ще раз."


TOAST_SAVED = "Записано ✅"
TOAST_STORED = "Збережено ✅"
NOTHING_TO_EDIT = "Запис не знайдено — можливо, його вже видалили."
DAY_IS_OFF = "Цей день позначено як неробочий, тому запит не надсилався."


# --- Зведення й звіти -----------------------------------------------------


def digest(d: Date, submitted: int, expected: int, total: int, missing: list[str]) -> str:
    lines = [
        f"📊 <b>{format_date(d, with_weekday=True)}</b>",
        f"Подали: <b>{submitted}</b> з {expected}",
        f"Разом на харчуванні: <b>{plural_children(total)}</b>",
    ]
    if missing:
        lines += ["", "❗ Не подали: " + ", ".join(esc(n) for n in missing)]
    else:
        lines += ["", "✅ Усі класи подали дані."]
    return "\n".join(lines)


NO_ACTIVE_CLASSES = "Немає жодного активного класу. Додайте класи в меню «🏫 Класи»."
NO_ACTIVE_TEACHERS = "Немає активних вчителів."
NOTHING_TO_REPORT = "Ще немає жодного запису, тож звітувати нема про що."
PICK_MONTH = "Оберіть місяць — надішлю XLSX і PDF:"
PICK_TEACHER_TO_DISABLE = "Кого вимкнути? Дані вчителя зберігаються."
PICK_CLASS_TO_DISABLE = (
    "Який клас прибрати з опитування?\n<i>Історія записів зберігається.</i>"
)
NAME_TOO_SHORT = "Надто коротко. Введіть ПІБ повністю:"
NOTHING_ADDED = "Нічого не додано."
NO_DAYS_OFF_IN_RANGE = "У цьому діапазоні позначок не було."
SYNC_IN_PROGRESS = "⏳ Синхронізую…"
NO_TEACHERS_HINT = "\n⚠️ Немає жодного вчителя — почніть з /add_teacher"


def sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{esc(sheet_id)}"


def sync_done(synced: int, total: int) -> str:
    if total == 0:
        return "Немає даних для синхронізації."
    return (
        f"✅ Синхронізовано вкладок: {synced} з {total}\n"
        f"{sheet_url(settings.google_sheet_id or '')}"
    )


def teacher_disabled(name: str) -> str:
    return f"✅ {esc(name)} — доступ вимкнено."


def class_disabled(name: str) -> str:
    return f"✅ {esc(name)} прибрано з опитування."


def days_off_cleared(count: int) -> str:
    return f"✅ Знято позначку з {count} дн."


def classes_added(created: list[str], rejected: list[str]) -> str:
    parts = []
    if created:
        parts.append("✅ Додано: " + ", ".join(esc(c) for c in created))
    if rejected:
        # rejected — сирі шматки того, що набрав адміністратор: одрук на
        # кшталт «1<3» інакше валить відповідь розбором сутностей.
        parts.append("⚠️ Пропущено: " + ", ".join(esc(r) for r in rejected))
    return "\n".join(parts) or NOTHING_ADDED


def report_caption(title: str, total: int, school_days: int, missing: int) -> str:
    caption = (
        f"📅 <b>{esc(title)}</b>\n"
        f"Разом: <b>{total}</b> порцій за {school_days} навч. дн."
    )
    if missing:
        caption += f"\n⚠️ Незаповнених клітинок: {missing}"
    return caption
REPORT_BUILDING = "⏳ Формую звіт…"
SHEETS_DISABLED = (
    "Синхронізація з Google Sheets вимкнена.\n"
    "Щоб увімкнути, задайте GOOGLE_CREDENTIALS_FILE і GOOGLE_SHEET_ID у .env."
)


# --- Адмін-меню -----------------------------------------------------------

ADMIN_MENU = "⚙️ <b>Меню адміністратора</b>"
BTN_TODAY = "📊 Сьогодні"
BTN_REPORT = "📅 Звіт за місяць"
BTN_TEACHERS = "👩‍🏫 Вчителі"
BTN_CLASSES = "🏫 Класи"
BTN_DAYS_OFF = "🗓 Неробочі дні"
BTN_SETTINGS = "⚙️ Налаштування"
BTN_MY_CLASSES = "📋 Мої класи"

TEACHER_ASK_NAME = "Введіть ПІБ вчителя:"
TEACHER_ASK_CLASSES = (
    "Оберіть класи цього вчителя, потім натисніть «Готово».\n"
    "Можна не обирати жодного — тоді призначите пізніше."
)
CLASS_ASK_NAME = (
    "Введіть назву класу (наприклад: <code>3-Б</code>).\n"
    "Можна кілька через кому: <code>1-А, 1-Б, 2-А</code>"
)
DAYS_OFF_ASK_RANGE = (
    "Введіть діапазон неробочих днів у форматі <code>ДД.ММ.РРРР - ДД.ММ.РРРР</code>\n"
    "або одну дату: <code>28.10.2026</code>\n\n"
    "Приклад: <code>28.10.2026 - 03.11.2026</code>"
)
DATE_RANGE_INVALID = (
    "❗ Не вдалося розпізнати дату. Формат: <code>ДД.ММ.РРРР</code> "
    "або <code>ДД.ММ.РРРР - ДД.ММ.РРРР</code>."
)


IMPORT_ASK_LIST = (
    "📋 Надішліть список вчителів — по одному в рядку.\n\n"
    "Приклад:\n"
    "<code>Коваленко Марія Іванівна, 0671234567, 1-А\n"
    "Шевчук Оксана Петрівна, 0509876543, 3-Б, 5-В\n"
    "Мельник Ігор Богданович, 0631112233</code>\n\n"
    "Порядок колонок не важливий — номер і клас я впізнаю сам. Розділювач: "
    "кома, крапка з комою або табуляція. Клас можна не вказувати.\n\n"
    "Список можна просто скопіювати з Excel."
)


def import_preview(created: int, updated: int, failed: list[str], classes: list[str]) -> str:
    lines = ["📋 <b>Результат розбору</b>", ""]
    if created:
        lines.append(f"➕ Нових: <b>{created}</b>")
    if updated:
        lines.append(f"🔄 Оновлених: <b>{updated}</b>")
    if classes:
        lines.append(f"🏫 Створено класів: {', '.join(esc(c) for c in classes)}")
    if failed:
        lines += ["", "⚠️ <b>Не вдалося розібрати:</b>"]
        lines += [f"  • <code>{esc(line)}</code>" for line in failed[:10]]
        if len(failed) > 10:
            lines.append(f"  …та ще {len(failed) - 10}")
    return "\n".join(lines)


def import_done(count: int, link: str) -> str:
    return (
        f"✅ Додано вчителів: <b>{count}</b>\n\n"
        "Тепер надішліть їм це посилання — одне на всіх:\n"
        f"{esc(link)}\n\n"
        "Вони натиснуть «Почати», поділяться номером — і я сам знайду кожного "
        "у списку разом з його класами.\n\n"
        "<i>Поки вчитель не відкриє бота, запити йому не надходитимуть — "
        "Telegram не дозволяє ботам писати першими.</i>"
    )


TEACHER_PICK_TO_EDIT = "Оберіть вчителя, щоб змінити його класи:"


def teacher_edit_classes(name: str) -> str:
    return f"🏫 Класи вчителя <b>{esc(name)}</b>.\nПозначте потрібні та натисніть «Готово»."


def teacher_classes_saved(name: str, class_names: list[str]) -> str:
    joined = ", ".join(esc(n) for n in class_names)
    return f"✅ <b>{esc(name)}</b> — класи: {joined or '—'}"


def invite_created(name: str, link: str) -> str:
    return (
        f"✅ Вчителя <b>{esc(name)}</b> додано.\n\n"
        f"Надішліть це посилання для реєстрації:\n{esc(link)}\n\n"
        "<i>Посилання одноразове.</i>"
    )


def days_off_marked(added: int, start: Date, end: Date) -> str:
    if added == 0:
        return "Ці дні вже позначені як неробочі."
    return (
        f"✅ Позначено неробочими: <b>{added}</b> дн.\n"
        f"{format_date(start)} — {format_date(end)}\n\n"
        "<i>Вихідні пропущено автоматично.</i>"
    )
