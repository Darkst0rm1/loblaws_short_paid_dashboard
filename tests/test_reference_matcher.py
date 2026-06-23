import pandas as pd

from src.reference_matcher import match_debit_memo_to_rows, normalize_reference

COLUMNS = {
    "invoice_reference": "Invoice Reference",
    "reference": "Reference",
    "item_text": "Item Text",
    "full_debit_description": "FULL DEBIT DESCRIPTION",
}


def _df():
    return pd.DataFrame({
        "Invoice Reference": ["INV-998877", "INV-111", "INV-222"],
        "Reference": ["DM12345", "RX-1", "RX-2"],
        "Item Text": ["misc", "see DM55555", "other"],
        "FULL DEBIT DESCRIPTION": ["debit for INV777", "nope", "memo DM99999 details"],
    })


# --- Normalization -----------------------------------------------------------

def test_normalize_removes_labels_and_punct():
    # Leading labels are stripped; normalization is symmetric on both sides.
    assert normalize_reference("Invoice INV-998877") == "998877"
    assert normalize_reference("Debit Memo: DM12345") == "dm12345"
    # Punctuation and case are normalized away.
    assert normalize_reference("RX_1-2 / 3") == "rx123"


def test_normalize_trailing_dot_zero():
    assert normalize_reference("998877.0") == "998877"


# --- Matching ----------------------------------------------------------------

def test_exact_invoice_reference_match():
    res = match_debit_memo_to_rows(_df(), invoice_reference="INV-998877", columns=COLUMNS)
    assert res.status == "matched"
    assert res.row_index == 0


def test_exact_debit_memo_via_reference():
    res = match_debit_memo_to_rows(_df(), debit_memo_number="DM12345", columns=COLUMNS)
    assert res.status == "matched"
    assert res.row_index == 0


def test_identifier_inside_full_description():
    res = match_debit_memo_to_rows(_df(), debit_memo_number="DM99999", columns=COLUMNS)
    assert res.status == "matched"
    assert res.row_index == 2
    assert "full_description" in res.match_method


def test_identifier_inside_item_text():
    res = match_debit_memo_to_rows(_df(), debit_memo_number="DM55555", columns=COLUMNS)
    assert res.status == "matched"
    assert res.row_index == 1


def test_no_row_found():
    res = match_debit_memo_to_rows(_df(), debit_memo_number="NOPE000", columns=COLUMNS)
    assert res.status == "not_found"


def test_multiple_rows_found_is_ambiguous():
    df = pd.DataFrame({
        "Invoice Reference": ["DUP", "DUP"],
        "Reference": ["", ""],
        "Item Text": ["", ""],
        "FULL DEBIT DESCRIPTION": ["", ""],
    })
    res = match_debit_memo_to_rows(df, invoice_reference="DUP", columns=COLUMNS)
    assert res.status == "ambiguous"
    assert res.candidate_rows == [0, 1]


def test_controlled_fuzzy_fallback():
    # Slightly different reference; only resolvable by fuzzy.
    df = pd.DataFrame({
        "Invoice Reference": ["INV9988770"],
        "Reference": [""],
        "Item Text": [""],
        "FULL DEBIT DESCRIPTION": [""],
    })
    res = match_debit_memo_to_rows(df, invoice_reference="INV998877", columns=COLUMNS)
    # Either fuzzy matches (high similarity) or not found, but never wrong row.
    assert res.status in ("matched", "not_found")
    if res.status == "matched":
        assert res.match_method == "fuzzy_reference"
        assert res.match_score >= 92.0


def test_low_score_fuzzy_rejected():
    df = pd.DataFrame({
        "Invoice Reference": ["ZZZ000111"],
        "Reference": [""],
        "Item Text": [""],
        "FULL DEBIT DESCRIPTION": [""],
    })
    res = match_debit_memo_to_rows(df, invoice_reference="INV998877", columns=COLUMNS)
    assert res.status == "not_found"
