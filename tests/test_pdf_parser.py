"""PDF extraction: header fields, item rows, and error handling.

The coordinate parser needs a real PDF, so the rich extraction assertion runs
against the supplied sample when present (skipped otherwise). The text-fallback
parser and the error paths are exercised without any PDF file.
"""
import os
from decimal import Decimal

import pytest

from src import pdf_parser
from src.pdf_parser import _parse_text_items, parse_pdf

SAMPLE_PDF = r"C:\Users\mohamed\Downloads\5119001568 2025.pdf"


def test_empty_pdf_returns_parse_error():
    memo = parse_pdf(b"", "empty.pdf")
    assert memo.parse_error and "Empty" in memo.parse_error
    assert memo.items == []


def test_garbage_bytes_do_not_raise():
    memo = parse_pdf(b"not a real pdf", "junk.pdf")
    assert memo.parse_error is not None  # routed to review, never raised


def test_text_fallback_parses_single_line_item():
    # UPC desc qrec qinv unit po inv net taxes total
    line = "10041390000956 KIKKOMAN SOY 350 420 CS 91.08 91.08 6375.60 0.00 6375.60"
    items = _parse_text_items(line)
    assert len(items) == 1
    it = items[0]
    assert it.upc == "10041390000956"
    assert it.qty_received == Decimal("350")
    assert it.qty_invoiced == Decimal("420")
    assert it.item_total == Decimal("6375.60")


def test_header_extraction_label_then_value():
    text = (
        "Debit Number, Debit Date\n"
        "1709032065, 2025/10/17\n"
        "PO Number\n"
        "4878948624\n"
        "Vendor Reference Number, Vendor Reference Date\n"
        "0090091172, 2025/10/17\n"
    )
    fields = pdf_parser._extract_header_fields(text)
    assert fields["debit_number"] == "1709032065"
    assert fields["debit_date"] == "2025/10/17"
    assert fields["po_number"] == "4878948624"
    assert fields["vendor_reference"] == "0090091172"


@pytest.mark.skipif(not os.path.exists(SAMPLE_PDF), reason="sample debit memo PDF not available")
def test_real_sample_pdf_extraction():
    with open(SAMPLE_PDF, "rb") as fh:
        memo = parse_pdf(fh.read(), "5119001568 2025.pdf")
    assert memo.parse_error is None
    assert memo.debit_number == "1709032065"
    assert memo.vendor_reference == "0090091172"
    assert memo.po_number == "4878948624"
    assert len(memo.items) == 1
    item = memo.items[0]
    assert item.upc == "10041390000956"
    assert item.qty_received == Decimal("350")
    assert item.qty_invoiced == Decimal("420")
    assert item.item_total == Decimal("6375.60")
