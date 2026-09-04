"""Експорт у PDF: місячний табель (альбомна A4) і звіт за день (книжкова A4)."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

from school_bot.domain.dates import format_date, plural_children
from school_bot.reports.day import UA_REPORT_KIND, DayReport, ReportKind
from school_bot.reports.matrix import MonthMatrix

# Кирилиця: вбудовані шрифти reportlab її не мають, тому шукаємо системний.
# Жирне накреслення йде парою до звичайного: підмішувати жирний з іншої
# гарнітури не можна — у таблиці це видно як стрибок ширини літер.
_FONT_CANDIDATES = [
    (
        "DejaVuSans",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "DejaVuSans",
        "/opt/homebrew/share/fonts/DejaVuSans.ttf",
        "/opt/homebrew/share/fonts/DejaVuSans-Bold.ttf",
    ),
    (
        "Supplemental-Arial",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    ("ArialUnicode", "/Library/Fonts/Arial Unicode.ttf", None),
    ("Helvetica-Sys", "/System/Library/Fonts/Helvetica.ttc", None),
]

_font_name: str | None = None
_bold_name: str | None = None


def _register() -> None:
    """Зареєструвати перший знайдений шрифт з кирилицею (і його жирну пару)."""
    global _font_name, _bold_name

    from pathlib import Path

    for name, regular, bold in _FONT_CANDIDATES:
        if not Path(regular).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, regular))
        except Exception:  # noqa: BLE001 — шрифт може бути .ttc з кількома гранями
            continue

        _font_name = name
        _bold_name = name  # запасний варіант, якщо жирного немає поруч
        if bold and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
                _bold_name = f"{name}-Bold"
            except Exception:  # noqa: BLE001
                pass
        return

    # Латиниця відрендериться, кирилиця — ні. Краще, ніж падіння звіту.
    _font_name = _bold_name = "Helvetica"


def _cyrillic_font() -> str:
    if _font_name is None:
        _register()
    assert _font_name is not None
    return _font_name


def _bold_font() -> str:
    if _bold_name is None:
        _register()
    assert _bold_name is not None
    return _bold_name


def _month_story(matrix: MonthMatrix, doc: SimpleDocTemplate, font: str) -> list:
    """Одна сторінка місячного табеля."""

    title_style = ParagraphStyle("t", fontName=font, fontSize=13, alignment=1, spaceAfter=2)
    sub_style = ParagraphStyle("s", fontName=font, fontSize=9, alignment=1, spaceAfter=6)
    foot_style = ParagraphStyle("f", fontName=font, fontSize=9, spaceBefore=10)

    story = [Paragraph(matrix.heading, title_style)]
    if matrix.school_name:
        story.append(Paragraph(matrix.school_name, sub_style))
    story.append(Spacer(1, 2 * mm))

    header_days = ["Клас"] + [c.label for c in matrix.columns] + ["Разом"]
    header_wd = [""] + [(c.off_marker or c.weekday_short) for c in matrix.columns] + [""]

    data: list[list[str]] = [header_days, header_wd]
    for row in matrix.rows:
        line = [row.name]
        for col in matrix.columns:
            v = row.value(col.date)
            line.append("" if v is None else str(v))
        line.append(str(row.total))
        data.append(line)

    totals = ["Разом"]
    for col in matrix.columns:
        v = matrix.day_total(col.date)
        totals.append("" if v is None else str(v))
    totals.append(str(matrix.grand_total))
    data.append(totals)

    n_cols = len(matrix.columns)
    avail = doc.width - 18 * mm - 14 * mm
    col_widths = [18 * mm] + [avail / n_cols] * n_cols + [14 * mm]

    table = Table(data, colWidths=col_widths, repeatRows=2)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0B0B0")),
        ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#DDE5F0")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8F0E4")),
        ("BACKGROUND", (-1, 0), (-1, -1), colors.HexColor("#E8F0E4")),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]

    # Заливка вихідних, канікул і пропущених днів — узгоджена з XLSX.
    for i, col in enumerate(matrix.columns, start=1):
        if col.is_weekend:
            style.append(("BACKGROUND", (i, 0), (i, -1), colors.HexColor("#EFEFEF")))
        elif col.off_kind is not None:
            style.append(("BACKGROUND", (i, 0), (i, -1), colors.HexColor("#FFF2CC")))
        else:
            for r, row in enumerate(matrix.rows, start=2):
                if matrix.is_gap(row, col):
                    style.append(("BACKGROUND", (i, r), (i, r), colors.HexColor("#FBD5D5")))

    table.setStyle(TableStyle(style))
    story.append(table)
    story.append(
        Paragraph("Відповідальна особа: ______________________&nbsp;&nbsp;&nbsp;&nbsp;"
                  "Дата: ______________", foot_style)
    )

    return story


def render_pdf(*matrices: MonthMatrix) -> bytes:
    """Місячний табель: по сторінці на метрику.

    Саме сторінки, а не додаткові колонки: у ландшафтній таблиці на 31 день
    колонка вже ~8 мм і вміщає дві цифри, тож третьої вкласти нікуди.
    """
    font = _cyrillic_font()
    buf = BytesIO()
    first = matrices[0]
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Облік харчування — {first.title}",
    )

    story: list = []
    for i, matrix in enumerate(matrices):
        if i:
            story.append(PageBreak())
        story += _month_story(matrix, doc, font)

    doc.build(story)
    return buf.getvalue()


# Великий звіт читає людина старшого віку, часто роздрукований і без окулярів
# під рукою. Тому кегль удвічі більший за компактний варіант, а межі між
# класами — суцільні лінії, а не відтінки: на ксероксі відтінки зникають.
BIG_FONT = 15
BIG_HEAD = 17
BIG_TITLE = 22


def _big_doc(buf: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )


def render_meals_pdf(report: DayReport) -> bytes:
    """Звіт про харчування — гарантовано один аркуш А4, найбільшим можливим кеглем.

    Ширину перевіряємо самі (reportlab не переносить текст у комірці), а висоту —
    єдиним надійним способом: збудувати документ і подивитися, скільки вийшло
    сторінок. Тому кегль спускаємо, поки не влізе.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    size = _best_meals_font(_meal_rows(report), font, bold)

    while size >= MEALS_MIN_FONT:
        try:
            data = _draw_meals(report, font_size=size)
        except LayoutError:
            # Дві колонки — один нерозривний блок. Коли він вищий за сторінку,
            # reportlab не ділить його, а падає; для нас це те саме «не влізло».
            size -= 0.5
            continue
        if data.count(b"/Type /Page") - data.count(b"/Type /Pages") <= 1:
            return data
        size -= 0.5
    return _draw_meals(report, font_size=MEALS_MIN_FONT)


def render_day_report(report: DayReport, kind: ReportKind) -> bytes:
    """Потрібний звіт за видом — єдина точка входу для джобів, кнопки й CLI."""
    if kind is ReportKind.MEALS:
        return render_meals_pdf(report)
    return render_big_day_pdf(report, kind)


def _meal_rows(report: DayReport) -> list[tuple[str, str, bool]]:
    """Плаский список рядків: (клас або зміна, цифра, чи це заголовок зміни)."""
    rows: list[tuple[str, str, bool]] = []
    for group in report.groups:
        if group.label:
            rows.append((group.label, str(group.total) if group.has_data else "—", True))
        for c in group.cells:
            rows.append((c.name, "—" if c.count is None else str(c.count), False))
    return rows


def _split_in_two(rows: list[tuple[str, str, bool]]) -> tuple[list, list]:
    """Розрізати список навпіл, але тільки по межі зміни.

    Розрив усередині зміни означав би, що частина класів однієї роздачі
    опинилася в іншій колонці — саме те, чого шукає око на аркуші.
    """
    middle = (len(rows) + 1) // 2
    candidates = [i for i, (_, _, is_group) in enumerate(rows) if is_group and i > 0]
    if not candidates:
        return rows[:middle], rows[middle:]
    cut = min(candidates, key=lambda i: abs(i - middle))
    return rows[:cut], rows[cut:]


def _meals_column(rows: list[tuple[str, str, bool]], font: str, bold: str, size: float):
    """Одна колонка таблиці харчування."""
    data = [["Клас", "Харч."]] + [[name, value] for name, value, _ in rows]
    table = Table(data, colWidths=[MEALS_COL_NAME, MEALS_COL_VALUE], repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        # Без явного leading reportlab лишає висоту рядка від стилю за
        # замовчуванням, і при великому кеглі текст налазить сам на себе.
        ("LEADING", (0, 0), (-1, -1), size * 1.15),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.9, colors.HexColor("#333333")),
        ("BOX", (0, 0), (-1, -1), 1.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D6E0EE")),
        ("LEFTPADDING", (0, 0), (0, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i, (_, _, is_group) in enumerate(rows, start=1):
        if is_group:
            style += [
                ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#E4EBF5")),
                ("FONTNAME", (0, i), (-1, i), bold),
                ("LINEABOVE", (0, i), (-1, i), 1.8, colors.black),
            ]
    table.setStyle(TableStyle(style))
    return table


# Ширина колонок таблиці харчування (назва класу / цифра).
MEALS_COL_NAME = 58 * mm
MEALS_COL_VALUE = 32 * mm
MEALS_MAX_FONT = 26
MEALS_MIN_FONT = 8


def _fits_width(rows: list[tuple[str, str, bool]], font: str, bold: str, size: float) -> bool:
    """Чи вміщаються всі підписи в колонку без виповзання.

    reportlab не переносить і не стискає текст у комірці — задовгий рядок
    просто вилазить за рамку, і сторінок при цьому не більшає. Тому ширину
    доводитьсяміряти самим.
    """
    room = MEALS_COL_NAME - 14           # мінус відступи зліва й справа
    value_room = MEALS_COL_VALUE - 10
    for name, value, is_group in rows:
        face = bold if is_group else font
        if pdfmetrics.stringWidth(name, face, size) > room:
            return False
        if pdfmetrics.stringWidth(value, face, size) > value_room:
            return False
    return pdfmetrics.stringWidth("Клас", bold, size) <= room


def _best_meals_font(rows: list[tuple[str, str, bool]], font: str, bold: str) -> float:
    """Найбільший кегль, за якого підписи ще не виповзають за колонку."""
    size = MEALS_MAX_FONT
    while size > MEALS_MIN_FONT and not _fits_width(rows, font, bold, size):
        size -= 0.5
    return size


def _draw_meals(report: DayReport, *, font_size: float | None = None) -> bytes:
    """Звіт про харчування — рівно один аркуш А4, максимально великим шрифтом.

    Дві колонки поруч, а не одна довга: 25 класів плюс десять змін — це 35
    рядків, і в один стовпчик вони на аркуші поміщаються лише дрібним кеглем.
    Розрізаємо по межі зміни, щоб роздача не розʼїжджалася між колонками.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    doc = _big_doc(buf, f"Харчування — {day_title}")

    title_style = ParagraphStyle(
        "mt", fontName=bold, fontSize=20, alignment=1, spaceAfter=2, leading=24
    )
    sub_style = ParagraphStyle(
        "ms", fontName=font, fontSize=11, alignment=1, spaceAfter=6,
        textColor=colors.HexColor("#444444"),
    )
    total_style = ParagraphStyle(
        "mx", fontName=bold, fontSize=17, alignment=1, spaceAfter=8, leading=21
    )
    note_style = ParagraphStyle("mn", fontName=font, fontSize=10, spaceBefore=6)
    foot_style = ParagraphStyle("mf", fontName=font, fontSize=10, spaceBefore=10)

    story = [Paragraph("Харчування", title_style)]
    if report.school_name:
        story.append(Paragraph(f"{report.school_name} · {day_title}", sub_style))
    story.append(Paragraph(f"РАЗОМ: {report.total}", total_style))

    rows = _meal_rows(report)
    size = font_size if font_size is not None else _best_meals_font(rows, font, bold)
    left, right = _split_in_two(rows)
    columns = Table(
        [[
            _meals_column(left, font, bold, size),
            _meals_column(right, font, bold, size),
        ]],
        colWidths=[90 * mm, 90 * mm],
        hAlign="CENTER",
    )
    columns.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(columns)

    if report.missing:
        story.append(
            Paragraph(
                f"Не подали ({len(report.missing)}): " + ", ".join(report.missing),
                note_style,
            )
        )
    story.append(
        Paragraph("Відповідальна особа: ______________________&nbsp;&nbsp;&nbsp;&nbsp;"
                  "Дата: ______________", foot_style)
    )

    doc.build(story)
    return buf.getvalue()


def render_big_day_pdf(report: DayReport, kind: ReportKind) -> bytes:
    """Звіт за день великим шрифтом: або харчування, або відсутні.

    Два окремі документи, а не дві половини одного: харчування несуть на кухню,
    відсутніх — медсестрі, і кожному потрібен свій аркуш, а не чужа половина.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    meals = kind is ReportKind.MEALS
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    heading = UA_REPORT_KIND[kind]
    doc = _big_doc(buf, f"{heading} — {day_title}")

    title_style = ParagraphStyle(
        "bt", fontName=bold, fontSize=BIG_TITLE, alignment=1, spaceAfter=6, leading=26
    )
    sub_style = ParagraphStyle(
        "bs", fontName=font, fontSize=12, alignment=1, spaceAfter=10,
        textColor=colors.HexColor("#444444"),
    )
    date_style = ParagraphStyle(
        "bd", fontName=bold, fontSize=15, alignment=1, spaceAfter=4, leading=19
    )
    total_style = ParagraphStyle(
        "bx", fontName=bold, fontSize=16, alignment=1, spaceAfter=12, leading=20
    )
    note_style = ParagraphStyle("bn", fontName=font, fontSize=11, spaceBefore=10)
    foot_style = ParagraphStyle("bf", fontName=font, fontSize=11, spaceBefore=16)

    story = [Paragraph(heading, title_style)]
    if report.school_name:
        story.append(Paragraph(report.school_name, sub_style))
    story.append(Paragraph(day_title, date_style))

    if meals:
        story.append(
            Paragraph(f"Разом: {plural_children(report.total)}", total_style)
        )
        header = ["Клас", "Харчуються"]
        widths = [90 * mm, 50 * mm]
    else:
        absent = report.absent_total
        sick = report.sick_total
        story.append(
            Paragraph(
                f"Відсутніх: {absent if absent is not None else '—'}"
                f" · з них хворі: {sick if sick is not None else '—'}",
                total_style,
            )
        )
        header = ["Клас", "Відсутні", "З них хворі"]
        widths = [78 * mm, 42 * mm, 42 * mm]

    def cell(v: int | None) -> str:
        return "—" if v is None else str(v)

    data: list[list[str]] = [header]
    group_rows: list[int] = []
    class_rows: list[int] = []
    for group in report.groups:
        if group.label:
            group_rows.append(len(data))
            if meals:
                data.append([group.label, str(group.total) if group.has_data else "—"])
            else:
                data.append([group.label, cell(group.absent_total), cell(group.sick_total)])
        for c in group.cells:
            class_rows.append(len(data))
            if meals:
                data.append([c.name, cell(c.count)])
            else:
                data.append([c.name, cell(c.absent), cell(c.sick)])

    if meals:
        data.append(["РАЗОМ", str(report.total)])
    else:
        data.append(["РАЗОМ", cell(report.absent_total), cell(report.sick_total)])

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="CENTER")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTNAME", (0, -1), (-1, -1), bold),
        ("FONTSIZE", (0, 0), (-1, -1), BIG_FONT),
        ("FONTSIZE", (0, 0), (-1, 0), BIG_HEAD),
        ("FONTSIZE", (0, -1), (-1, -1), BIG_HEAD),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Чіткі суцільні межі: клас має бути видно з відстані витягнутої руки.
        ("GRID", (0, 0), (-1, -1), 0.9, colors.HexColor("#333333")),
        ("BOX", (0, 0), (-1, -1), 1.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D6E0EE")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCE9D5")),
        ("LEFTPADDING", (0, 0), (0, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    for r in group_rows:
        style += [
            ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#EDF1F7")),
            ("FONTNAME", (0, r), (-1, r), bold),
            ("FONTSIZE", (0, r), (-1, r), BIG_HEAD),
            ("LINEABOVE", (0, r), (-1, r), 1.8, colors.black),
        ]
    table.setStyle(TableStyle(style))
    story.append(table)

    if meals and report.missing:
        story.append(
            Paragraph(
                f"Не подали дані ({len(report.missing)}): " + ", ".join(report.missing),
                note_style,
            )
        )
    story.append(
        Paragraph("Відповідальна особа: ______________________&nbsp;&nbsp;&nbsp;&nbsp;"
                  "Дата: ______________", foot_style)
    )

    doc.build(story)
    return buf.getvalue()
