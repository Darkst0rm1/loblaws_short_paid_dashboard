"""Reference normalization, column detection, and short-paid matching."""
import pandas as pd

from src import reference_matcher as rm


# (1) Leading-zero removal from Vendor Reference Number.
def test_normalize_removes_only_leading_zeroes():
    assert rm.normalize_reference("0090091172") == "90091172"
    assert rm.normalize_reference("  0090091172 ") == "90091172"
    assert rm.normalize_reference(90091172) == "90091172"
    assert rm.normalize_reference(90091172.0) == "90091172"  # excel float


def test_normalize_keeps_non_zero_prefixes():
    # Letters such as D / OI are NOT stripped — only leading zeroes.
    assert rm.normalize_reference("D90157331") == "D90157331"
    assert rm.normalize_reference("OI12345") == "OI12345"
    assert rm.normalize_reference("0D123") == "D123"


def test_detect_reference_column_priority():
    cols = ["Reference", "FULL DEBIT DESCRIPTION", "Debit Description Number"]
    # Debit Description Number outranks FULL DEBIT DESCRIPTION.
    assert rm.detect_reference_column(cols) == "Debit Description Number"
    assert rm.detect_reference_column(["a", "Vendor Reference Number", "Debit Description Number"]) \
        == "Vendor Reference Number"
    assert rm.detect_reference_column(["FULL DEBIT DESCRIPTION"]) == "FULL DEBIT DESCRIPTION"
    assert rm.detect_reference_column(["x", "y"]) is None


def _sp(values):
    return pd.DataFrame({"FULL DEBIT DESCRIPTION": values})


# (2) Exact short-paid match.
def test_exact_match_after_normalization():
    df = _sp([90157331, 90091172, 11111111])
    status, rows = rm.match_reference(df, "FULL DEBIT DESCRIPTION", "0090091172")
    assert status == rm.MATCH_FOUND
    assert rows == [1]


# (3) Missing short-paid match.
def test_no_match():
    df = _sp([90157331, 11111111])
    status, rows = rm.match_reference(df, "FULL DEBIT DESCRIPTION", "0090091172")
    assert status == rm.MATCH_NOT_FOUND
    assert rows == []


# (4) Duplicate short-paid match.
def test_duplicate_match():
    df = _sp([90091172, 90091172, 22222222])
    status, rows = rm.match_reference(df, "FULL DEBIT DESCRIPTION", "90091172")
    assert status == rm.MATCH_DUPLICATE
    assert rows == [0, 1]


def test_blank_cells_never_match():
    df = _sp([None, "", 90091172])
    status, rows = rm.match_reference(df, "FULL DEBIT DESCRIPTION", "90091172")
    assert status == rm.MATCH_FOUND and rows == [2]
