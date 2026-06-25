"""Match a debit memo's Vendor Reference Number to the short-paid worksheet.

Normalization (applied to both the memo value and the short-paid cell):

1. convert to text
2. trim spaces
3. remove leading zeroes only (``0090091172`` -> ``90091172``)

Then an EXACT match is required. No partial matching, and no prefix stripping
(``D``, ``OI`` etc. are kept; only leading zeroes are removed).

The matching column is detected by alias, in priority order:

1. ``Vendor Reference Number``
2. ``Debit Description Number``
3. ``FULL DEBIT DESCRIPTION``
"""
from __future__ import annotations

import pandas as pd

# Alias priority for the short-paid reference column.
REFERENCE_COLUMN_ALIASES = [
    "Vendor Reference Number",
    "Debit Description Number",
    "FULL DEBIT DESCRIPTION",
]

# Match outcome statuses.
MATCH_FOUND = "matched"
MATCH_NOT_FOUND = "not_found"
MATCH_DUPLICATE = "duplicate"


def normalize_reference(value) -> str:
    """Normalize a reference value: text, trimmed, leading zeroes removed."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Excel often stores integers as floats -> drop a single trailing ".0".
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    # Remove leading zeroes ONLY (never other leading characters).
    stripped = text.lstrip("0")
    # Preserve a genuine all-zero value as "0" rather than an empty string.
    if stripped == "" and text != "":
        return "0"
    return stripped


def detect_reference_column(columns) -> str | None:
    """Return the short-paid reference column using the alias priority list."""
    lookup = {str(c).strip().lower(): c for c in columns}
    for alias in REFERENCE_COLUMN_ALIASES:
        col = lookup.get(alias.lower())
        if col is not None:
            return col
    return None


def find_matches(df: pd.DataFrame, ref_column, memo_reference) -> list[int]:
    """Return the positional row indices whose reference equals the memo's.

    Comparison is exact on the normalized values. Blank cells never match.
    """
    target = normalize_reference(memo_reference)
    if not target or ref_column is None or ref_column not in df.columns:
        return []
    matches: list[int] = []
    for pos, value in enumerate(df[ref_column].tolist()):
        if str(value).strip() != "" and normalize_reference(value) == target:
            matches.append(pos)
    return matches


def match_reference(df: pd.DataFrame, ref_column, memo_reference):
    """Match and classify. Returns ``(status, rows)``.

    * exactly one row  -> ``(MATCH_FOUND, [idx])``
    * no rows          -> ``(MATCH_NOT_FOUND, [])``
    * more than one    -> ``(MATCH_DUPLICATE, [idx, idx, ...])``
    """
    rows = find_matches(df, ref_column, memo_reference)
    if len(rows) == 1:
        return MATCH_FOUND, rows
    if len(rows) == 0:
        return MATCH_NOT_FOUND, rows
    return MATCH_DUPLICATE, rows
