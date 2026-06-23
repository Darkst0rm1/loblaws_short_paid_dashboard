import io

from openpyxl import Workbook, load_workbook

from src.column_detector import RESULT_COLUMN
from src.excel_exporter import build_exceptions_workbook, write_results
from src.models import ProcessingException


def _make_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "06.08.26"
    ws.append(["Invoice Reference", "Amount", "Dispute Status"])
    ws.append(["INV-100", 173.74, "Open"])
    ws.append(["INV-200", 316.94, "Open"])
    ws.append(["INV-300", 50.00, "Closed"])
    # A second worksheet that must be preserved.
    other = wb.create_sheet("QTY")
    other.append(["keep", "me"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_result_column_added_and_written():
    data = _make_workbook()
    row_cells = {0: "10057258, 7, 173.74"}
    new_bytes = write_results(data, "06.08.26", row_cells)

    wb = load_workbook(io.BytesIO(new_bytes))
    ws = wb["06.08.26"]
    headers = [c.value for c in ws[1]]
    assert RESULT_COLUMN in headers
    col = headers.index(RESULT_COLUMN) + 1
    # Data row 0 -> worksheet row 2.
    assert ws.cell(row=2, column=col).value == "10057258, 7, 173.74"


def test_original_columns_and_rows_preserved():
    data = _make_workbook()
    new_bytes = write_results(data, "06.08.26", {0: "x, 1, 1.00"})
    wb = load_workbook(io.BytesIO(new_bytes))
    ws = wb["06.08.26"]
    headers = [c.value for c in ws[1]]
    assert headers[:3] == ["Invoice Reference", "Amount", "Dispute Status"]
    # Original data still present and in order.
    assert ws.cell(row=2, column=1).value == "INV-100"
    assert ws.cell(row=3, column=1).value == "INV-200"
    assert ws.cell(row=4, column=1).value == "INV-300"


def test_other_worksheets_preserved():
    data = _make_workbook()
    new_bytes = write_results(data, "06.08.26", {0: "x, 1, 1.00"})
    wb = load_workbook(io.BytesIO(new_bytes))
    assert "QTY" in wb.sheetnames
    assert wb["QTY"].cell(row=1, column=1).value == "keep"


def test_multiline_cell_and_wrap():
    data = _make_workbook()
    value = "10057258, 7, 173.74\n99999999, 23, 567.64 - Material not valid in LCL"
    new_bytes = write_results(data, "06.08.26", {0: value})
    wb = load_workbook(io.BytesIO(new_bytes))
    ws = wb["06.08.26"]
    headers = [c.value for c in ws[1]]
    col = headers.index(RESULT_COLUMN) + 1
    cell = ws.cell(row=2, column=col)
    assert "\n" in cell.value
    assert cell.alignment.wrap_text is True


def test_existing_result_column_updated():
    # Build a workbook that already has the result column.
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws.append(["Invoice Reference", RESULT_COLUMN])
    ws.append(["INV-100", "old value"])
    out = io.BytesIO()
    wb.save(out)
    data = out.getvalue()

    new_bytes = write_results(data, "S", {0: "10057258, 7, 173.74"})
    wb2 = load_workbook(io.BytesIO(new_bytes))
    ws2 = wb2["S"]
    headers = [c.value for c in ws2[1]]
    # Result column not duplicated.
    assert headers.count(RESULT_COLUMN) == 1
    col = headers.index(RESULT_COLUMN) + 1
    assert ws2.cell(row=2, column=col).value == "10057258, 7, 173.74"


def test_unmatched_rows_left_blank():
    data = _make_workbook()
    new_bytes = write_results(data, "06.08.26", {0: "x, 1, 1.00"})
    wb = load_workbook(io.BytesIO(new_bytes))
    ws = wb["06.08.26"]
    headers = [c.value for c in ws[1]]
    col = headers.index(RESULT_COLUMN) + 1
    # Rows 1 and 2 (df index) were not written -> blank.
    assert ws.cell(row=3, column=col).value in (None, "")
    assert ws.cell(row=4, column=col).value in (None, "")


def test_exceptions_workbook_has_all_sheets():
    exc = [
        ProcessingException(category="Invalid LCL Materials", reason="Material not valid in LCL", upc="999"),
        ProcessingException(category="Unlinked Debit Memos", reason="Short Paid row not found"),
    ]
    data = build_exceptions_workbook(exc)
    wb = load_workbook(io.BytesIO(data))
    for sheet in ["Invalid LCL Materials", "Review Required", "Unlinked Debit Memos"]:
        assert sheet in wb.sheetnames
