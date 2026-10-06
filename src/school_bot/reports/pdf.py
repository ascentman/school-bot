"""Експорт у PDF: місячний табель (альбомна A4) і звіт за день (книжкова A4)."""

from __future__ import annotations

import logging
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
from school_bot.reports.day import REPORT_TITLE, DayReport
from school_bot.reports.matrix import MonthMatrix

log = logging.getLogger(__name__)

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
        title=f"Облік відсутніх — {first.title}",
    )

    story: list = []
    for i, matrix in enumerate(matrices):
        if i:
            story.append(PageBreak())
        story += _month_story(matrix, doc, font)

    doc.build(story)
    return buf.getvalue()


# Щоденний звіт читають зблизька й часто роздрукованим, нерідко люди старшого
# віку. Тому правило просте: рівно один аркуш А4 і найбільший кегль, який на
# ньому вміщається. Дві колонки поруч — бо 25 рядків в одну таким шрифтом не
# лягають. Межі між класами — суцільні лінії, а не відтінки: на ксероксі
# відтінки зникають.
ONE_PAGE_MAX_FONT = 26
ONE_PAGE_MIN_FONT = 8
# Шапку читають один раз, тож вона не має тягнути вниз кегль усієї таблиці:
# «Хворі» у вузькій колонці інакше обмежував би цифри, заради яких усе й є.
HEADER_FONT_CAP = 13

PAGE_MARGIN = 14 * mm
USABLE_WIDTH = A4[0] - 2 * PAGE_MARGIN      # 182 мм
COLUMN_GAP = 2 * mm                          # проміжок між двома половинами
HALF_WIDTH = (USABLE_WIDTH - 2 * COLUMN_GAP) / 2

# Ширини комірок у половині аркуша: (підпис, значення…). Сума має вкладатися в
# HALF_WIDTH — інакше таблиця мовчки наїде на поля: reportlab падає лише по
# висоті, а надто широку просто центрує поверх берегів.
DAY_HEADERS = ["Клас", "Відс.", "Хворі"]
DAY_WIDTHS = [46 * mm, 21 * mm, 21 * mm]


def _big_doc(buf: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )


def _dash(v: int | None) -> str:
    return "—" if v is None else str(v)


def _report_rows(report: DayReport) -> list[list[str]]:
    """Плаский список рядків: клас, відсутні, хворі."""
    return [[c.name, _dash(c.absent), _dash(c.sick)] for c in report.cells]


def _split_in_two(rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """Розрізати список навпіл: ліва колонка аркуша й права.

    Класи йдуть у звичайному порядку (1-А, 1-Б, … 11-Б), тож рівний розріз
    посередині лишає обидві половини впорядкованими — око шукає клас зверху
    вниз у лівій колонці, потім у правій.
    """
    middle = (len(rows) + 1) // 2
    return rows[:middle], rows[middle:]


def _fits_width(
    rows: list[list[str]],
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
    checks = [(headers, bold, min(size, HEADER_FONT_CAP))]
    checks += [(cells, font, size) for cells in rows]
    for cells, face, cell_size in checks:
        for text, width in zip(cells, widths, strict=True):
            if pdfmetrics.stringWidth(text, face, cell_size) > width - 12:
                return False
    return True


def _best_font(
    rows: list[list[str]],
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
    rows: list[list[str]],
    headers: list[str],
    widths: list[float],
    font: str,
    bold: str,
    size: float,
) -> Table:
    """Одна з двох колонок аркуша."""
    data = [headers] + rows
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("FONTSIZE", (0, 0), (-1, 0), min(size, HEADER_FONT_CAP)),
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
    ]))
    return table


def _draw_one_page(report: DayReport, size: float) -> bytes:
    font = _cyrillic_font()
    bold = _bold_font()
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    doc = _big_doc(buf, f"{REPORT_TITLE} — {day_title}")

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

    story = [Paragraph(REPORT_TITLE, title_style)]
    if report.school_name:
        story.append(Paragraph(f"{report.school_name} · {day_title}", sub_style))
    story.append(
        Paragraph(
            f"ВІДСУТНІХ: {_dash(report.absent_total)}"
            f" · З НИХ ХВОРІ: {_dash(report.sick_total)}",
            total_style,
        )
    )

    left, right = _split_in_two(_report_rows(report))
    # Проміжок додається до кожної половини рівно один раз, і сума не має
    # перевищувати USABLE_WIDTH — це стереже тест test_table_fits_the_page.
    columns = Table(
        [[
            _half_table(left, DAY_HEADERS, DAY_WIDTHS, font, bold, size),
            _half_table(right, DAY_HEADERS, DAY_WIDTHS, font, bold, size),
        ]],
        colWidths=[HALF_WIDTH + COLUMN_GAP, HALF_WIDTH + COLUMN_GAP],
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

    # Клас, що не подав нічого, у суму не входить, тож «Відсутніх: 15» без
    # цього рядка виглядало б повною цифрою, а насправді може бути більшою.
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


def _draw_flowing(report: DayReport, size: float) -> bytes:
    """Запасний рендер для дуже великої школи: одна колонка, кілька сторінок.

    Дві колонки — нерозривний блок, і коли він вищий за аркуш, reportlab не
    ділить його, а падає. Звичайна одноколонкова таблиця ділиться штатно, тож
    надто велика школа отримає багатосторінковий звіт замість жодного.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    buf = BytesIO()
    day_title = f"{format_date(report.date, with_weekday=True)} {report.date.year} р."
    doc = _big_doc(buf, f"{REPORT_TITLE} — {day_title}")

    title_style = ParagraphStyle(
        "ft", fontName=bold, fontSize=20, alignment=1, spaceAfter=2, leading=24
    )
    sub_style = ParagraphStyle(
        "fs", fontName=font, fontSize=11, alignment=1, spaceAfter=8,
        textColor=colors.HexColor("#444444"),
    )
    story = [
        Paragraph(REPORT_TITLE, title_style),
        Paragraph(f"{report.school_name} · {day_title}", sub_style),
        _half_table(_report_rows(report), DAY_HEADERS, DAY_WIDTHS, font, bold, size),
    ]
    doc.build(story)
    return buf.getvalue()


def render_day_report(report: DayReport) -> bytes:
    """Щоденний звіт: один аркуш А4 і найбільший кегль, який на ньому вміщається.

    Ширину перевіряємо самі, а висоту — єдиним надійним способом: будуємо
    документ і дивимося, скільки вийшло сторінок. Тому кегль спускаємо, поки
    не влізе; більша школа отримує менший шрифт, а не другу сторінку — її
    просто не понесуть медсестрі.

    Межа все ж існує: приблизно від ста класів на аркуш не лягає навіть
    найдрібніший кегль. Там звіт друкується в кілька сторінок — це гірше, але
    незрівнянно краще, ніж не отримати його взагалі.
    """
    font = _cyrillic_font()
    bold = _bold_font()
    rows = _report_rows(report)
    size = _best_font(rows, DAY_HEADERS, DAY_WIDTHS, font, bold)

    while size >= ONE_PAGE_MIN_FONT:
        try:
            data = _draw_one_page(report, size)
        except LayoutError:
            # Дві колонки — один нерозривний блок. Коли він вищий за сторінку,
            # reportlab не ділить його, а падає; для нас це те саме «не влізло».
            size -= 0.5
            continue
        if data.count(b"/Type /Page") - data.count(b"/Type /Pages") <= 1:
            return data
        size -= 0.5

    # Школа завелика, щоб влізти на аркуш навіть найдрібнішим кеглем. Краще
    # багатосторінковий звіт, ніж жодного: інакше виняток дійшов би до джоба,
    # той мовчки проковтнув би його, і того дня не прийшло б ні PDF, ні тексту.
    log.warning(
        "Звіт за %s не вміщається на аркуш — друкую в кілька сторінок", report.date
    )
    return _draw_flowing(report, ONE_PAGE_MIN_FONT)
