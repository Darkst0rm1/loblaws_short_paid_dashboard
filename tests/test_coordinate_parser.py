"""Tests for the coordinate-based debit-memo parser.

Word positions mirror the real Loblaws layout observed in
``5119001568 2025.pdf`` (KIKKOMAN SOY SAUCE line).
"""

from decimal import Decimal

from src.debit_memo_parser import _extract_header_fields, parse_words


def w(text, x0, top):
    return {"text": text, "x0": float(x0), "top": float(top)}


# Header row x-positions taken from the real PDF.
HEADER = [
    w("UPC", 65, 10), w("#", 89, 10), w("Description", 123, 10),
    w("Qty", 204, 10), w("Qty", 244, 10), w("Unit", 277, 10),
    w("PO", 316, 10), w("Invoice", 351, 10), w("Net", 406, 10),
    w("Taxes", 445, 10), w("Total", 493, 10),
]


def _kikkoman_row(top=30):
    return [
        w("10041390000956", 54, top), w("KIKKOMAN", 122, top), w("SOY", 166, top),
        w("S", 185, top), w("350", 208, top), w("420", 250, top), w("C12", 280, top),
        w("91.08", 320, top), w("91.08", 365, top), w("6,375.60", 411, top),
        w("0.00", 463, top), w("6,375.60", 502, top),
    ]


def test_parses_real_loblaws_layout():
    words = HEADER + _kikkoman_row() + [w("AUCE", 122, 42)] + [w("Subtotal", 365, 60), w("6,375.60", 430, 60)]
    lines = parse_words(words, "dm.pdf", 1)
    assert len(lines) == 1
    line = lines[0]
    assert line.upc == "10041390000956"
    assert line.qty_received == Decimal("350")
    assert line.qty_invoiced == Decimal("420")
    assert line.short_quantity == Decimal("70")
    assert line.unit == "C12"
    assert line.net_amount == Decimal("6375.60")
    assert line.taxes == Decimal("0.00")
    assert line.total_amount == Decimal("6375.60")


def test_wrapped_description_joined():
    words = HEADER + _kikkoman_row() + [w("AUCE", 122, 42)]
    line = parse_words(words, "dm.pdf", 1)[0]
    # "S" stays in the description column (not pulled into Qty), and "AUCE" joins.
    assert "KIKKOMAN SOY S" in line.description
    assert "AUCE" in line.description


def test_totals_block_not_treated_as_product():
    words = HEADER + _kikkoman_row() + [
        w("Subtotal", 365, 60), w("6,375.60", 430, 60),
        w("TOTAL", 365, 80), w("(CAD)", 402, 80), w("6,375.60", 502, 80),
    ]
    lines = parse_words(words, "dm.pdf", 1)
    assert len(lines) == 1  # totals rows ignored


def test_multiple_products():
    second = [
        w("10058421000000", 54, 50), w("WIDGET", 122, 50), w("0", 208, 50),
        w("13", 250, 50), w("CS", 280, 50), w("24.38", 320, 50), w("24.38", 365, 50),
        w("316.94", 411, 50), w("0.00", 463, 50), w("316.94", 502, 50),
    ]
    words = HEADER + _kikkoman_row() + second
    lines = parse_words(words, "dm.pdf", 1)
    assert len(lines) == 2
    assert lines[1].upc == "10058421000000"
    assert lines[1].qty_invoiced == Decimal("13")
    assert lines[1].net_amount == Decimal("316.94")


def test_no_header_returns_empty():
    # Without a recognizable header row, the coordinate parser yields nothing
    # (the caller then falls back to the text parser).
    assert parse_words(_kikkoman_row(), "dm.pdf", 1) == []


# --- header label/value extraction -------------------------------------------

HEADER_TEXT = """DEBIT MEMO ( REDUCTION )
Debit Number, Debit Date
1709032065, 2025/10/17
PO Number
4878948624
Vendor Reference Number, Vendor Reference Date
0090091172, 2025/10/17
Vendor Number
1029176
"""


def test_header_label_value_extraction():
    fields = _extract_header_fields(HEADER_TEXT)
    assert fields["debit_memo_number"] == "1709032065"
    assert fields["vendor_reference"] == "0090091172"
    assert fields["po_number"] == "4878948624"
    assert fields["vendor_number"] == "1029176"
