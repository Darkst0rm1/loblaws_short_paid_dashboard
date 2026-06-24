"""Match a debit memo to the correct Short Paid List row.

Fuzzy matching is allowed ONLY for invoice / document references as a final
controlled fallback with a high threshold -- never for UPCs.

The primary real-world key is the debit memo's **Vendor Reference Number**
(e.g. ``0090091172``) which, with leading zeros stripped, appears in the Short
Paid ``FULL DEBIT DESCRIPTION`` / ``Item Text`` columns (e.g. ``*D90091172``).
"""

from __future__ import annotations

import re

import pandas as pd

from .models import ShortPaidMatchResult

try:
    from rapidfuzz import fuzz

    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_RAPIDFUZZ = False

FUZZY_THRESHOLD = 92.0

_LABELS = re.compile(r"\b(invoice|inv|debit\s*memo|dm|ref(?:erence)?)\b", re.IGNORECASE)


def normalize_reference(value) -> str:
    """Normalize an invoice / reference / debit-memo identifier for comparison.

    Strips labels, punctuation and case. For all-digit identifiers, leading
    zeros are also stripped so ``0090091172`` matches ``90091172`` (applied
    symmetrically to both sides).
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    text = _LABELS.sub(" ", text)
    text = re.sub(r"[^0-9A-Za-z]", "", text).lower()
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def match_debit_memo_to_rows(
    df: pd.DataFrame,
    *,
    debit_memo_number: str | None = None,
    invoice_number: str | None = None,
    invoice_reference: str | None = None,
    vendor_reference: str | None = None,
    columns: dict | None = None,
) -> ShortPaidMatchResult:
    """Match a debit memo against Short Paid rows.

    ``columns`` maps logical roles to actual column names, e.g.::

        {"invoice_reference": "Invoice Reference", "reference": "Reference",
         "item_text": "Item Text", "full_debit_description": "FULL DEBIT DESCRIPTION"}
    """
    columns = columns or {}
    col_inv_ref = columns.get("invoice_reference")
    col_ref = columns.get("reference")
    col_item_text = columns.get("item_text")
    col_full_desc = columns.get("full_debit_description")

    dm = normalize_reference(debit_memo_number)
    inv = normalize_reference(invoice_number)
    inv_ref = normalize_reference(invoice_reference)
    vref = normalize_reference(vendor_reference)

    def exact_matches(col_name, *needles) -> list[int]:
        if not col_name or col_name not in df.columns:
            return []
        needle_set = {n for n in needles if n}
        if not needle_set:
            return []
        return [idx for idx, raw in df[col_name].items() if normalize_reference(raw) in needle_set]

    def substring_matches(col_name, *needles) -> list[int]:
        if not col_name or col_name not in df.columns:
            return []
        needle_set = {n for n in needles if n and len(n) >= 4}
        if not needle_set:
            return []
        hits = []
        for idx, raw in df[col_name].items():
            norm = normalize_reference(raw)
            if norm and any(n in norm for n in needle_set):
                hits.append(idx)
        return hits

    # Ordered strategies, most specific first.
    strategies = [
        ("exact_invoice_reference", lambda: exact_matches(col_inv_ref, inv_ref, inv, dm)),
        ("exact_reference", lambda: exact_matches(col_ref, dm, inv_ref, inv, vref)),
        ("exact_full_debit_description", lambda: exact_matches(col_full_desc, vref, dm, inv)),
        ("vendor_ref_in_full_description", lambda: substring_matches(col_full_desc, vref)),
        ("vendor_ref_in_item_text", lambda: substring_matches(col_item_text, vref)),
        ("debit_memo_in_full_description", lambda: substring_matches(col_full_desc, dm)),
        ("invoice_in_full_description", lambda: substring_matches(col_full_desc, inv, inv_ref)),
        ("debit_memo_in_item_text", lambda: substring_matches(col_item_text, dm)),
        ("invoice_in_item_text", lambda: substring_matches(col_item_text, inv, inv_ref)),
    ]

    for method, fn in strategies:
        unique = sorted(set(fn()))
        if len(unique) == 1:
            return ShortPaidMatchResult(status="matched", row_index=unique[0], match_method=method, match_score=100.0)
        if len(unique) > 1:
            return ShortPaidMatchResult(status="ambiguous", candidate_rows=unique, match_method=method, match_score=100.0)

    # Controlled fuzzy fallback (references only, high threshold).
    if _HAS_RAPIDFUZZ:
        needles = [n for n in (vref, dm, inv_ref, inv) if n]
        best_idx, best_score = None, 0.0
        search_cols = [c for c in (col_inv_ref, col_ref, col_full_desc, col_item_text) if c and c in df.columns]
        for col_name in search_cols:
            for idx, raw in df[col_name].items():
                target = normalize_reference(raw)
                if not target:
                    continue
                for needle in needles:
                    score = fuzz.ratio(needle, target)
                    if score > best_score:
                        best_score, best_idx = score, idx
        if best_idx is not None and best_score >= FUZZY_THRESHOLD:
            return ShortPaidMatchResult(
                status="matched", row_index=best_idx, match_method="fuzzy_reference", match_score=round(best_score, 2)
            )

    return ShortPaidMatchResult(status="not_found")
