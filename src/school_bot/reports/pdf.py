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

from school_bot.domain.dates import format_date, plural_children
from school_bot.reports.day import DayReport
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


def render_day_pdf(report: DayReport) -> bytes:
    """Звіт за один день: дата, загальна цифра, класи по змінах роздачі.

    Порядок рядків повторює порядок роздачі, а не алфавіт: саме так звіт
    читають на місці — зміна за зміною, звіряючи з тим, що на роздачі.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Облік харчування — {day_title}",
    )

    title_style = ParagraphStyle("dt", fontName=bold, fontSize=15, alignment=1, spaceAfter=9)
    sub_style = ParagraphStyle(
        "ds", fontName=font, fontSize=10.5, alignment=1, spaceAfter=11,
        textColor=colors.HexColor("#555555"),
    )
    date_style = ParagraphStyle("dd", fontName=bold, fontSize=12, alignment=1, spaceAfter=3)
    total_style = ParagraphStyle("dtot", fontName=font, fontSize=12, alignment=1, spaceAfter=10)
    note_style = ParagraphStyle("dn", fontName=font, fontSize=9, spaceBefore=8)
    foot_style = ParagraphStyle("df", fontName=font, fontSize=9, spaceBefore=14)

    story = [Paragraph("Облік харчування учнів", title_style)]
    if report.school_name:
        story.append(Paragraph(report.school_name, sub_style))
    story.append(Paragraph(day_title, date_style))
    story.append(
        Paragraph(f"Разом на харчуванні: <b>{plural_children(report.total)}</b>", total_style)
    )

    def cell(v: int | None) -> str:
        return "—" if v is None else str(v)

    data: list[list[str]] = [["Клас", "Харчуються", "Відсутні", "З них хворі"]]
    # Рядки-заголовки змін фарбуються окремо від рядків класів, тому їхні
    # номери запамʼятовуємо просто під час збирання таблиці.
    group_rows: list[int] = []
    for group in report.groups:
        if group.label:
            group_rows.append(len(data))
            # Зміна без жодної поданої цифри — це не нуль порцій, а брак даних.
            data.append([
                group.label,
                str(group.total) if group.has_data else "—",
                cell(group.absent_total),
                cell(group.sick_total),
            ])
        for c in group.cells:
            data.append([c.name, cell(c.count), cell(c.absent), cell(c.sick)])
    data.append([
        "РАЗОМ",
        str(report.total),
        cell(report.absent_total),
        cell(report.sick_total),
    ])

    table = Table(
        data, colWidths=[62 * mm, 28 * mm, 28 * mm, 28 * mm], repeatRows=1, hAlign="CENTER"
    )
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTNAME", (0, -1), (-1, -1), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0B0B0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE5F0")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8F0E4")),
        # Класи — з відступом, щоб зміна читалася як заголовок над ними.
        ("LEFTPADDING", (0, 1), (0, -1), 16),
        # Щільно свідомо: школа на 25 класів має вміщатися на одну сторінку —
        # підписують і підшивають саме аркуш, а не два.
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    for r in group_rows:
        style += [
            ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#EDF1F7")),
            ("FONTNAME", (0, r), (-1, r), bold),
            ("LEFTPADDING", (0, r), (0, r), 6),
            ("LINEABOVE", (0, r), (-1, r), 0.6, colors.HexColor("#8A9BB4")),
        ]
    table.setStyle(TableStyle(style))
    story.append(table)

    # Пропуск і нуль — різні речі: «—» означає, що клас не подав цифру, і в
    # сумі його немає. Без цього рядка звіт виглядав би повним.
    if report.missing:
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
