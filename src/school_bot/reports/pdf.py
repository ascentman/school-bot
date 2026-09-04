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

from school_bot.domain.dates import format_date
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
# Щоденні звіти друкують і читають зблизька, часто люди старшого віку. Тому
# правило одне для обох: рівно один аркуш А4 і найбільший кегль, який на ньому
# вміщається. Дві колонки поруч — бо 35 рядків в одну таким шрифтом не лягають.
ONE_PAGE_MAX_FONT = 26
ONE_PAGE_MIN_FONT = 8
COLUMN_GAP = 4 * mm

# Ширини комірок у половині аркуша: (підпис, значення…). Разом ≤ 90 мм.
KIND_COLUMNS: dict[ReportKind, tuple[list[str], list[float]]] = {
    ReportKind.MEALS: (["Клас", "Харч."], [58 * mm, 32 * mm]),
    ReportKind.ABSENCE: (["Клас", "Відс.", "Хворі"], [44 * mm, 23 * mm, 23 * mm]),
}


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


def _dash(v: int | None) -> str:
    return "—" if v is None else str(v)


def _report_rows(report: DayReport, kind: ReportKind) -> list[tuple[list[str], bool]]:
    """Плаский список рядків: (комірки, чи це заголовок зміни)."""
    meals = kind is ReportKind.MEALS
    rows: list[tuple[list[str], bool]] = []
    for group in report.groups:
        if group.label:
            if meals:
                cells = [group.label, str(group.total) if group.has_data else "—"]
            else:
                cells = [group.label, _dash(group.absent_total), _dash(group.sick_total)]
            rows.append((cells, True))
        for c in group.cells:
            if meals:
                cells = [c.name, _dash(c.count)]
            else:
                cells = [c.name, _dash(c.absent), _dash(c.sick)]
            rows.append((cells, False))
    return rows


def _split_in_two(
    rows: list[tuple[list[str], bool]],
) -> tuple[list[tuple[list[str], bool]], list[tuple[list[str], bool]]]:
    """Розрізати список навпіл, але тільки по межі зміни.

    Розрив усередині зміни означав би, що частина класів однієї роздачі
    опинилася в іншій колонці — саме те, чого шукає око на аркуші.
    """
    middle = (len(rows) + 1) // 2
    candidates = [i for i, (_, is_group) in enumerate(rows) if is_group and i > 0]
    if not candidates:
        return rows[:middle], rows[middle:]
    cut = min(candidates, key=lambda i: abs(i - middle))
    return rows[:cut], rows[cut:]


def _fits_width(
    rows: list[tuple[list[str], bool]],
    headers: list[str],
    widths: list[float],
    font: str,
    bold: str,
    size: float,
) -> bool:
    """Чи вміщається кожна комірка у свою колонку.

    Міряти доводиться самим: reportlab не переносить і не стискає текст у
    комірці — задовгий підпис просто виповзає за рамку, і сторінок при цьому
    не більшає. Тобто переповнення по ширині не видно ні звідки, крім оцього.
    """
    for cells, is_group in [(headers, True), *rows]:
        face = bold if is_group else font
        for text, width in zip(cells, widths, strict=True):
            if pdfmetrics.stringWidth(text, face, size) > width - 12:
                return False
    return True


def _best_font(
    rows: list[tuple[list[str], bool]],
    headers: list[str],
    widths: list[float],
    font: str,
    bold: str,
) -> float:
    size = ONE_PAGE_MAX_FONT
    while size > ONE_PAGE_MIN_FONT and not _fits_width(
        rows, headers, widths, font, bold, size
    ):
        size -= 0.5
    return size


def _half_table(
    rows: list[tuple[list[str], bool]],
    headers: list[str],
    widths: list[float],
    font: str,
    bold: str,
    size: float,
) -> Table:
    """Одна з двох колонок аркуша."""
    data = [headers] + [cells for cells, _ in rows]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        # Без явного leading reportlab лишає висоту рядка від стилю за
        # замовчуванням, і при великому кеглі текст налазить сам на себе.
        ("LEADING", (0, 0), (-1, -1), size * 1.15),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Суцільні межі, а не відтінки: на ксероксі відтінки зникають.
        ("GRID", (0, 0), (-1, -1), 0.9, colors.HexColor("#333333")),
        ("BOX", (0, 0), (-1, -1), 1.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D6E0EE")),
        ("LEFTPADDING", (0, 0), (0, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i, (_, is_group) in enumerate(rows, start=1):
        if is_group:
            style += [
                ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#E4EBF5")),
                ("FONTNAME", (0, i), (-1, i), bold),
                ("LINEABOVE", (0, i), (-1, i), 1.8, colors.black),
            ]
    table.setStyle(TableStyle(style))
    return table


def _draw_one_page(report: DayReport, kind: ReportKind, size: float) -> bytes:
    font = _cyrillic_font()
    bold = _bold_font()
    headers, widths = KIND_COLUMNS[kind]
    meals = kind is ReportKind.MEALS
    heading = UA_REPORT_KIND[kind]
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    doc = _big_doc(buf, f"{heading} — {day_title}")

    title_style = ParagraphStyle(
        "ot", fontName=bold, fontSize=20, alignment=1, spaceAfter=2, leading=24
    )
    sub_style = ParagraphStyle(
        "os", fontName=font, fontSize=11, alignment=1, spaceAfter=6,
        textColor=colors.HexColor("#444444"),
    )
    total_style = ParagraphStyle(
        "ox", fontName=bold, fontSize=17, alignment=1, spaceAfter=8, leading=21
    )
    note_style = ParagraphStyle("on", fontName=font, fontSize=10, spaceBefore=6)
    foot_style = ParagraphStyle("of", fontName=font, fontSize=10, spaceBefore=10)

    story = [Paragraph(heading, title_style)]
    if report.school_name:
        story.append(Paragraph(f"{report.school_name} · {day_title}", sub_style))
    if meals:
        story.append(Paragraph(f"РАЗОМ: {report.total}", total_style))
    else:
        story.append(
            Paragraph(
                f"ВІДСУТНІХ: {_dash(report.absent_total)}"
                f" · З НИХ ХВОРІ: {_dash(report.sick_total)}",
                total_style,
            )
        )

    left, right = _split_in_two(_report_rows(report, kind))
    half = sum(widths)
    columns = Table(
        [[
            _half_table(left, headers, widths, font, bold, size),
            _half_table(right, headers, widths, font, bold, size),
        ]],
        colWidths=[half + COLUMN_GAP, half + COLUMN_GAP],
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

    if meals and report.missing:
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


def render_day_report(report: DayReport, kind: ReportKind) -> bytes:
    """Щоденний звіт: гарантовано один аркуш А4, найбільшим можливим кеглем.

    Ширину перевіряємо самі, а висоту — єдиним надійним способом: будуємо
    документ і дивимося, скільки вийшло сторінок. Тому кегль спускаємо, поки
    не влізе; більша школа має отримати менший шрифт, а не другу сторінку —
    її просто не понесуть на роздачу.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    headers, widths = KIND_COLUMNS[kind]
    rows = _report_rows(report, kind)
    size = _best_font(rows, headers, widths, font, bold)

    while size >= ONE_PAGE_MIN_FONT:
        try:
            data = _draw_one_page(report, kind, size)
        except LayoutError:
            # Дві колонки — один нерозривний блок. Коли він вищий за сторінку,
            # reportlab не ділить його, а падає; для нас це те саме «не влізло».
            size -= 0.5
            continue
        if data.count(b"/Type /Page") - data.count(b"/Type /Pages") <= 1:
            return data
        size -= 0.5
    return _draw_one_page(report, kind, ONE_PAGE_MIN_FONT)
