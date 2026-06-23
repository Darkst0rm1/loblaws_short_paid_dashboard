from decimal import Decimal

from src.debit_memo_parser import (
    compute_short_quantity,
    parse_debit_memo_text,
    parse_line,
)


# --- Quantity logic ----------------------------------------------------------

def test_short_qty_basic():
    short, status, _ = compute_short_quantity(Decimal("7"), Decimal("0"))
    assert short == Decimal("7")
    assert status == "ok"


def test_short_qty_zero_received():
    short, status, _ = compute_short_quantity(Decimal("7"), Decimal("0"))
    assert short == Decimal("7")


def test_short_qty_partial_received():
    short, status, _ = compute_short_quantity(Decimal("10"), Decimal("3"))
    assert short == Decimal("7")


def test_short_qty_negative_is_flagged_not_zeroed():
    short, status, warnings = compute_short_quantity(Decimal("3"), Decimal("5"))
    assert short == Decimal("-2")
    assert status == "Review required"
    assert any("Negative" in w for w in warnings)


def test_short_qty_missing_invoiced():
    short, status, _ = compute_short_quantity(None, Decimal("3"))
    assert short is None
    assert status == "Review required"


def test_short_qty_missing_received():
    short, status, _ = compute_short_quantity(Decimal("3"), None)
    assert short is None
    assert status == "Review required"


# --- Single line parsing (right-to-left numerics) ----------------------------

def test_parse_single_product_line():
    # UPC desc       QtyRec QtyInv Unit PO    Inv    Net    Taxes Total
    text = "10724923451967 SOME PRODUCT 0 7 EA 24.82 24.82 173.74 0.00 173.74"
    line = parse_line(text, "dm.pdf", 1)
    assert line is not None
    assert line.upc == "10724923451967"
    assert line.qty_received == Decimal("0")
    assert line.qty_invoiced == Decimal("7")
    assert line.net_amount == Decimal("173.74")
    assert line.total_amount == Decimal("173.74")
    assert line.short_quantity == Decimal("7")


def test_parse_line_missing_taxes():
    # Only Inv, Net, Total (3 numbers) -> taxes absent.
    text = "10057258 WIDGET 0 7 EA 24.82 173.74 173.74"
    line = parse_line(text, "dm.pdf", 1)
    assert line.net_amount == Decimal("173.74")
    assert any("Taxes" in w for w in line.warnings)


def test_non_product_line_returns_none():
    assert parse_line("Page 1 of 2", "dm.pdf", 1) is None
    assert parse_line("UPC # Description Qty", "dm.pdf", 1) is None


def test_negative_and_comma_amounts():
    text = "10058421 BIG ITEM 0 13 CS 24.38 24.38 316.94 0.00 316.94"
    line = parse_line(text, "dm.pdf", 1)
    assert line.net_amount == Decimal("316.94")


# --- Document parsing --------------------------------------------------------

DOC_TEXT = """Debit Memo No: DM12345
Invoice Reference: INV-998877
Vendor No: 55012
UPC # Description Qty Rec. Qty Inv. Unit PO Price Invoice Price Net Taxes Total
10724923451967 PRODUCT ONE 0 7 EA 24.82 24.82 173.74 0.00 173.74
10058421 PRODUCT TWO 0 13 CS 24.38 24.38 316.94 0.00 316.94
Subtotal 490.68
Grand Total 490.68
Page 1 of 1
"""


def test_parse_multiple_products():
    doc = parse_debit_memo_text(DOC_TEXT, "dm.pdf")
    assert len(doc.lines) == 2
    assert doc.debit_memo_number == "DM12345"
    assert doc.invoice_reference == "INV-998877"
    assert doc.lines[0].net_amount == Decimal("173.74")
    assert doc.lines[1].net_amount == Decimal("316.94")


def test_repeated_headers_and_footers_ignored():
    text = DOC_TEXT + "UPC # Description Qty Rec. Qty Inv. Unit PO Price Invoice Price Net Taxes Total\n"
    doc = parse_debit_memo_text(text, "dm.pdf")
    assert len(doc.lines) == 2


def test_wrapped_description_appended():
    text = """Debit Memo No: DM1
UPC # Description Qty Rec. Qty Inv. Unit PO Price Invoice Price Net Taxes Total
10724923451967 PRODUCT ONE 0 7 EA 24.82 24.82 173.74 0.00 173.74
EXTRA LONG DESCRIPTION
"""
    doc = parse_debit_memo_text(text, "dm.pdf")
    assert len(doc.lines) == 1
    assert "EXTRA LONG DESCRIPTION" in doc.lines[0].description


def test_no_product_rows():
    doc = parse_debit_memo_text("Just a header\nPage 1 of 1\n", "dm.pdf")
    assert len(doc.lines) == 0
    assert doc.status == "no_product_lines"
