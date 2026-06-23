"""Write results back into the original Short Paid workbook (non-destructive)."""

from __future__ import annotations

import io
import logging

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from .column_detector import RESULT_COLUMN
from .models import ProcessingException

logger = logging.getLogger(__name__)


def _find_header_row_and_col(ws, result_column: str, max_scan_rows: int = 5):
    """Locate the header row and the result column index (1-based).

    Returns ``(header_row_idx, result_col_idx, header_cells_by_name)``.
    The result column is created at the end if it does not exist.
    """
    header_row_idx = 1
    headers = {}
    for r in range(1, min(max_scan_rows, ws.max_row) + 1):
        row_headers = {}
        for c in range(1, ws.max_column + 1):
            value = ws.cell(row=r, column=c).value
            if value is not None:
                row_headers[str(value).strip()] = c
        if row_headers:
            header_row_idx = r
            headers = row_headers
            break

    if result_column in headers:
        result_col_idx = headers[result_column]
    else:
        result_col_idx = ws.max_column + 1
        ws.cell(row=header_row_idx, column=result_col_idx, value=result_column)

    return header_row_idx, result_col_idx, headers


def write_results(
    file_bytes: bytes,
    sheet_name: str,
    row_cells: dict[int, str],
    *,
    result_column: str = RESULT_COLUMN,
    header_row: int = 0,
) -> bytes:
    """Load the original workbook, write result cells into the chosen sheet,
    and return new .xlsx bytes. The original file is never modified.

    ``row_cells`` keys are 0-based DataFrame row indices (data rows). They are
    offset by the header position to reach the right worksheet row.
    """
    from openpyxl.styles import Alignment

    wb = load_workbook(io.BytesIO(file_bytes))
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Worksheet '{sheet_name}' not found in workbook")
    ws = wb[sheet_name]

    header_row_idx, result_col_idx, _ = _find_header_row_and_col(ws, result_column)
    col_letter = get_column_letter(result_col_idx)

    wrap_top = Alignment(wrap_text=True, vertical="top")

    for df_row_index, value in row_cells.items():
        # Worksheet data starts the row after the header.
        ws_row = header_row_idx + 1 + int(df_row_index)
        cell = ws.cell(row=ws_row, column=result_col_idx, value=value)
        cell.alignment = wrap_top
        # Increase row height for multiline content.
        line_count = value.count("\n") + 1
        if line_count > 1:
            ws.row_dimensions[ws_row].height = max(15 * line_count, 30)

    # Reasonable width for the result column.
    ws.column_dimensions[col_letter].width = 42

    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def build_exceptions_workbook(exceptions: list[ProcessingException]) -> bytes:
    """Build an .xlsx with one worksheet per exception category."""
    categories = [
        "Invalid LCL Materials",
        "Review Required",
        "Unlinked Debit Memos",
        "Ambiguous UPC Matches",
        "Parsing Errors",
        "Duplicate PDFs",
        "Validation Errors",
    ]
    wb = Workbook()
    wb.remove(wb.active)

    headers = [
        "Category", "Reason", "Source PDF", "Page", "Debit Memo #",
        "Invoice Reference", "UPC", "Detail",
    ]

    for category in categories:
        ws = wb.create_sheet(title=category[:31])
        ws.append(headers)
        for exc in exceptions:
            if exc.category != category:
                continue
            ws.append([
                exc.category, exc.reason, exc.source_file, exc.page_number,
                exc.debit_memo_number, exc.invoice_reference, exc.upc, exc.detail,
            ])

    if not wb.sheetnames:  # safety
        wb.create_sheet(title="Exceptions")

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
