"""Detect relevant columns in the LCL and Short Paid workbooks."""

from __future__ import annotations

import re

import pandas as pd


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first DataFrame column matching any candidate (loose match)."""
    norm_map = {_norm(c): c for c in df.columns}
    for candidate in candidates:
        key = _norm(candidate)
        if key in norm_map:
            return norm_map[key]
    # Substring fallback.
    for candidate in candidates:
        key = _norm(candidate)
        for norm_col, original in norm_map.items():
            if key and key in norm_col:
                return original
    return None


LCL_COLUMN_SPEC = {
    "customer_material": ["Customer Material"],
    "material": ["Material"],
    "material_description": ["Material Description", "Description"],
    "unit_upc": ["Unit UPC Code", "Unit UPC"],
    "case_upc": ["Case UPC Code", "Case UPC"],
    "customer_price": ["Customer Price"],
    "invoice_price": ["Invoice Price"],
    "case_size": ["Case Size"],
}

SHORT_PAID_COLUMN_SPEC = {
    "invoice_reference": ["Invoice Reference"],
    "reference": ["Reference"],
    "item_text": ["Item Text"],
    "full_debit_description": ["FULL DEBIT DESCRIPTION", "Full Debit Description"],
    "amount": ["Amount (Tran Cur.)", "Amount"],
    "dispute_id": ["Dispute ID"],
    "dispute_status": ["Dispute Status"],
}

LCL_REQUIRED = ["material", "unit_upc"]
RESULT_COLUMN = "MATERIAL#, QTY & AMOUNT$"


def detect_columns(df: pd.DataFrame, spec: dict) -> dict:
    """Map logical roles -> actual column names using ``spec``."""
    return {role: find_column(df, candidates) for role, candidates in spec.items()}


def find_lcl_sheet_by_columns(sheets: dict[str, pd.DataFrame]) -> str | None:
    """Given {sheet_name: df}, return the first sheet containing material + a UPC column."""
    for name, df in sheets.items():
        cols = detect_columns(df, LCL_COLUMN_SPEC)
        if cols.get("material") and (cols.get("unit_upc") or cols.get("case_upc")):
            return name
    return None


def missing_required(detected: dict, required_roles: list[str]) -> list[str]:
    return [role for role in required_roles if not detected.get(role)]
