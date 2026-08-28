"""Експорт місячного табеля в PDF (альбомна орієнтація, A4)."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from school_bot.reports.matrix import MonthMatrix

# Кирилиця: вбудовані шрифти reportlab її не мають, тому шукаємо системний.
_FONT_CANDIDATES = [
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("DejaVuSans", "/opt/homebrew/share/fonts/DejaVuSans.ttf"),
    ("ArialUnicode", "/Library/Fonts/Arial Unicode.ttf"),
    ("Helvetica-Sys", "/System/Library/Fonts/Helvetica.ttc"),
    ("Supplemental-Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"),
]

_font_name: str | None = None


def _cyrillic_font() -> str:
    """Зареєструвати перший знайдений шрифт з кирилицею."""
    global _font_name
    if _font_name is not None:
        return _font_name

    from pathlib import Path

    for name, path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _font_name = name
                return name
            except Exception:  # noqa: BLE001 — шрифт може бути .ttc з кількома гранями
                continue

    _font_name = "Helvetica"  # запасний варіант: латиниця відрендериться, кирилиця — ні
    return _font_name


def render_pdf(matrix: MonthMatrix) -> bytes:
    font = _cyrillic_font()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Облік харчування — {matrix.title}",
    )

    title_style = ParagraphStyle("t", fontName=font, fontSize=13, alignment=1, spaceAfter=2)
    sub_style = ParagraphStyle("s", fontName=font, fontSize=9, alignment=1, spaceAfter=6)
    foot_style = ParagraphStyle("f", fontName=font, fontSize=9, spaceBefore=10)

    story = [Paragraph(f"Облік харчування учнів — {matrix.title}", title_style)]
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
        v = matrix.day_total(col.date) if col.is_school_day else None
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

    doc.build(story)
    return buf.getvalue()
