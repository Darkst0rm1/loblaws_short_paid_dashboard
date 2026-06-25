"""Business-rule processing: quantities, amounts, combined cells, dedup, errors."""
from decimal import Decimal

import pandas as pd

from src import material_matcher as mm
from src import processor
from src.models import STATUS_PROCESSED, STATUS_REVIEW, DebitMemo, DebitMemoItem


def _index():
    df = pd.DataFrame(
        {
            "Material": [10057258, 10068421],
            "Unit UPC Code": ["10041390000956", "20000000000001"],
            "Case UPC Code": ["c1", "c2"],
        }
    )
    return mm.build_index(df)


def _sp():
    return pd.DataFrame({"FULL DEBIT DESCRIPTION": [90091172, 90157331]})


def _item(upc="10041390000956", qrec=350, qinv=420, total="6375.60"):
    return DebitMemoItem(
        upc=upc,
        qty_received=Decimal(str(qrec)) if qrec is not None else None,
        qty_invoiced=Decimal(str(qinv)) if qinv is not None else None,
        item_total=Decimal(str(total)) if total is not None else None,
    )


def _memo(ref="0090091172", debit="1709032065", po="4878948624", items=None):
    return DebitMemo(
        source_file="memo.pdf",
        vendor_reference=ref,
        debit_number=debit,
        po_number=po,
        items=items if items is not None else [_item()],
    )


def _run(memos):
    return processor.process(memos, _sp(), "FULL DEBIT DESCRIPTION", _index())


# Acceptance test (synthetic, mirrors the required PDF values).
def test_acceptance_row():
    results, summary = _run([_memo()])
    r = results[0]
    assert r.vendor_reference == "90091172"
    assert r.debit_number == "1709032065"
    assert r.po_number == "4878948624"
    assert r.cell_text == "10057258, 70, $6,375.60"
    assert r.status == STATUS_PROCESSED
    assert r.review_reason == ""


# (9) Positive quantity difference.
def test_positive_quantity_difference():
    r = _run([_memo(items=[_item(qrec=350, qinv=420)])])[0][0]
    assert "10057258, 70, $6,375.60" == r.cell_text


# (10) Zero quantity difference is valid and still included.
def test_zero_quantity_difference():
    r = _run([_memo(items=[_item(qrec=420, qinv=420, total="150.00")])])[0][0]
    assert r.cell_text == "10057258, 0, $150.00"
    assert r.status == STATUS_PROCESSED


# (11) Negative quantity difference -> manual review, still included.
def test_negative_quantity_difference():
    r = _run([_memo(items=[_item(qrec=420, qinv=350, total="100.00")])])[0][0]
    assert r.status == STATUS_REVIEW
    assert "Negative quantity difference" in r.review_reason
    assert "10057258, -70, $100.00" == r.cell_text


# (12) Item Total used directly (not qty * price).
def test_item_total_used_directly():
    # qty diff 70, price would imply a different number; amount must equal Total.
    r = _run([_memo(items=[_item(qrec=350, qinv=420, total="6375.60")])])[0][0]
    assert r.cell_text.endswith("$6,375.60")


# (13) Multiple items combined into one cell with newlines.
def test_multiple_items_one_cell_with_newlines():
    items = [
        _item(upc="10041390000956", qrec=350, qinv=420, total="6375.60"),
        _item(upc="20000000000001", qrec=8, qinv=20, total="425.40"),
    ]
    r = _run([_memo(items=items)])[0][0]
    lines = r.cell_text.split("\n")
    assert lines == ["10057258, 70, $6,375.60", "10068421, 12, $425.40"]


# (14) Mixed matched and unmatched items -> review, both lines present.
def test_mixed_matched_and_unmatched_items():
    items = [
        _item(upc="10041390000956", qrec=350, qinv=420, total="6375.60"),
        _item(upc="123456789", qrec=5, qinv=10, total="118.25"),  # not in material list
    ]
    r = _run([_memo(items=items)])[0][0]
    assert r.status == STATUS_REVIEW
    assert "10057258, 70, $6,375.60" in r.cell_text
    assert "UPC 123456789 not found, 5, $118.25" in r.cell_text
    assert "123456789" in r.review_reason


# (18) Multiple PDF processing.
def test_multiple_pdfs_produce_multiple_rows():
    m1 = _memo(ref="0090091172", debit="1709032065", po="4878948624")
    m2 = _memo(ref="90157331", debit="1709032066", po="4878948625",
               items=[_item(upc="20000000000001", qrec=8, qinv=20, total="425.40")])
    results, summary = _run([m1, m2])
    assert len(results) == 2
    assert summary.pdfs_uploaded == 2
    assert {r.debit_number for r in results} == {"1709032065", "1709032066"}


# (19) Duplicate PDF detection -> one Manual Review row.
def test_duplicate_pdf_detection():
    dup = _memo()
    dup2 = _memo()  # identical ref + debit + PO
    results, summary = _run([dup, dup2])
    assert len(results) == 1
    assert results[0].status == STATUS_REVIEW
    assert "Duplicate debit memo uploaded" in results[0].review_reason


# (20) One failed PDF does not stop the remaining files.
def test_failed_pdf_does_not_stop_others():
    bad = DebitMemo(source_file="bad.pdf", parse_error="Password-protected PDF; cannot read")
    good = _memo()
    results, summary = _run([bad, good])
    assert len(results) == 2
    statuses = {r.source_file: r.status for r in results}
    assert statuses["bad.pdf"] == STATUS_REVIEW
    assert statuses["memo.pdf"] == STATUS_PROCESSED
    bad_row = next(r for r in results if r.source_file == "bad.pdf")
    assert "parsing error" in bad_row.review_reason.lower()


def test_missing_reference_goes_to_review():
    r = _run([_memo(ref=None)])[0][0]
    assert r.status == STATUS_REVIEW
    assert "Missing Vendor Reference Number" in r.review_reason


def test_reference_not_in_short_paid_goes_to_review():
    r = _run([_memo(ref="55555555")])[0][0]
    assert r.status == STATUS_REVIEW
    assert "not found in short-paid list" in r.review_reason


def test_duplicate_short_paid_reference_goes_to_review():
    sp = pd.DataFrame({"FULL DEBIT DESCRIPTION": [90091172, 90091172]})
    results, _ = processor.process([_memo()], sp, "FULL DEBIT DESCRIPTION", _index())
    assert results[0].status == STATUS_REVIEW
    assert "Duplicate Vendor Reference Number" in results[0].review_reason
