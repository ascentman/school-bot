"""Синхронізація з Google Sheets.

БД — джерело правди, таблиця — дзеркало для перегляду. Будь-яку вкладку можна
перебудувати з нуля, тому зіпсована вручну таблиця не є втратою даних.

gspread синхронний, тому кожен виклик іде в окремий тред.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from school_bot.config import settings
from school_bot.domain.dates import month_name
from school_bot.reports.matrix import MonthMatrix

log = logging.getLogger(__name__)

SUMMARY_TAB = "Зведення"

GREY = {"red": 0.937, "green": 0.937, "blue": 0.937}
YELLOW = {"red": 1.0, "green": 0.949, "blue": 0.8}
RED = {"red": 0.984, "green": 0.835, "blue": 0.835}
BLUE = {"red": 0.867, "green": 0.898, "blue": 0.941}
GREEN = {"red": 0.909, "green": 0.941, "blue": 0.894}


class SheetsDisabledError(RuntimeError):
    pass


def tab_name(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _client() -> Any:
    if not settings.sheets_enabled:
        raise SheetsDisabledError("GOOGLE_CREDENTIALS_FILE / GOOGLE_SHEET_ID не задані")

    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        str(settings.google_credentials_file),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
        ],
    )
    return gspread.authorize(creds)


def _matrix_to_grid(matrix: MonthMatrix) -> list[list[Any]]:
    """Матриця → двовимірний масив клітинок, точно як у XLSX."""
    header_days = ["Клас"] + [c.label for c in matrix.columns] + ["Разом"]
    header_wd = [""] + [(c.off_marker or c.weekday_short) for c in matrix.columns] + [""]

    grid: list[list[Any]] = [
        [f"Облік харчування учнів — {matrix.title}"],
        [matrix.school_name or ""],
        [],
        header_days,
        header_wd,
    ]
    for row in matrix.rows:
        line: list[Any] = [row.name]
        for col in matrix.columns:
            v = row.value(col.date)
            line.append("" if v is None else v)
        line.append(row.total)
        grid.append(line)

    totals: list[Any] = ["Разом"]
    for col in matrix.columns:
        v = matrix.day_total(col.date) if col.is_school_day else None
        totals.append("" if v is None else v)
    totals.append(matrix.grand_total)
    grid.append(totals)
    return grid


def _format_requests(matrix: MonthMatrix, sheet_id: int) -> list[dict[str, Any]]:
    """Заливка колонок і пропущених днів. Кольори ті самі, що в XLSX і PDF."""
    n_rows = len(matrix.rows)
    header_row = 3          # 0-based: рядок 4 таблиці
    first_data_row = 5      # 0-based: рядок 6
    total_row = first_data_row + n_rows

    def cell_fmt(r0: int, r1: int, c0: int, c1: int, color: dict[str, float]) -> dict[str, Any]:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": r0,
                    "endRowIndex": r1,
                    "startColumnIndex": c0,
                    "endColumnIndex": c1,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }

    n_cols = len(matrix.columns) + 2
    reqs: list[dict[str, Any]] = [
        cell_fmt(header_row, header_row + 2, 0, n_cols, BLUE),
        cell_fmt(total_row, total_row + 1, 0, n_cols, GREEN),
        cell_fmt(header_row, total_row + 1, n_cols - 1, n_cols, GREEN),
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 5, "frozenColumnCount": 1},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
    ]

    for i, col in enumerate(matrix.columns):
        c = i + 1
        if col.is_weekend:
            reqs.append(cell_fmt(header_row, total_row + 1, c, c + 1, GREY))
        elif col.off_kind is not None:
            reqs.append(cell_fmt(header_row, total_row + 1, c, c + 1, YELLOW))
        else:
            for r, row in enumerate(matrix.rows):
                if matrix.is_gap(row, col):
                    rr = first_data_row + r
                    reqs.append(cell_fmt(rr, rr + 1, c, c + 1, RED))
    return reqs


def _rebuild_month_sync(matrix: MonthMatrix) -> str:
    gc = _client()
    book = gc.open_by_key(settings.google_sheet_id)
    name = tab_name(matrix.year, matrix.month)

    grid = _matrix_to_grid(matrix)
    n_cols = len(matrix.columns) + 2

    try:
        ws = book.worksheet(name)
        ws.clear()
        ws.resize(rows=max(len(grid) + 5, 20), cols=n_cols)
    except Exception:  # вкладки ще немає
        ws = book.add_worksheet(title=name, rows=max(len(grid) + 5, 20), cols=n_cols)

    ws.update(values=grid, range_name="A1", value_input_option="RAW")
    book.batch_update({"requests": _format_requests(matrix, ws.id)})

    # Свіжий місяць — першою вкладкою, щоб перевірка бачила його одразу.
    try:
        book.reorder_worksheets([ws] + [w for w in book.worksheets() if w.id != ws.id])
    except Exception:
        log.debug("Не вдалося переставити вкладки", exc_info=True)

    return ws.url


def _sync_summary_sync(matrices: list[MonthMatrix]) -> None:
    """Вкладка «Зведення»: місяці × класи."""
    gc = _client()
    book = gc.open_by_key(settings.google_sheet_id)

    class_names: list[str] = []
    for m in matrices:
        for row in m.rows:
            if row.name not in class_names:
                class_names.append(row.name)

    grid: list[list[Any]] = [["Місяць", *class_names, "Разом"]]
    for m in sorted(matrices, key=lambda x: (x.year, x.month)):
        totals = {row.name: row.total for row in m.rows}
        grid.append(
            [f"{month_name(m.month)} {m.year}"]
            + [totals.get(name, "") for name in class_names]
            + [m.grand_total]
        )

    n_cols = len(class_names) + 2
    try:
        ws = book.worksheet(SUMMARY_TAB)
        ws.clear()
        ws.resize(rows=max(len(grid) + 5, 20), cols=n_cols)
    except Exception:
        ws = book.add_worksheet(title=SUMMARY_TAB, rows=max(len(grid) + 5, 20), cols=n_cols)

    ws.update(values=grid, range_name="A1", value_input_option="RAW")


async def rebuild_month(matrix: MonthMatrix) -> str:
    return await asyncio.to_thread(_rebuild_month_sync, matrix)


async def sync_summary(matrices: list[MonthMatrix]) -> None:
    await asyncio.to_thread(_sync_summary_sync, matrices)


async def safe_rebuild_month(matrix: MonthMatrix) -> str | None:
    """Синк, який ніколи не валить виклик. Google — не критичний шлях."""
    if not settings.sheets_enabled:
        return None
    try:
        return await rebuild_month(matrix)
    except Exception:
        log.exception("Не вдалося синхронізувати %s-%02d", matrix.year, matrix.month)
        return None
