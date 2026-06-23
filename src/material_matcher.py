"""UPC normalization, LCL material indexing, and exact UPC matching.

No fuzzy matching is ever used for UPC values.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from .models import MaterialMatchResult

# Columns we look for in the LCL material database.
UNIT_UPC_CANDIDATES = ["Unit UPC Code", "Unit UPC", "UnitUPC"]
CASE_UPC_CANDIDATES = ["Case UPC Code", "Case UPC", "CaseUPC"]
MATERIAL_CANDIDATES = ["Material"]
DESCRIPTION_CANDIDATES = ["Material Description", "Description"]


def normalize_upc(value) -> str:
    """Normalize a UPC to a clean digit string.

    Rules:
      * Always returns a string (never int / float).
      * Trims surrounding whitespace and removes internal spaces.
      * Removes hyphens and commas.
      * Removes a trailing ``.0`` (a common artifact of float parsing).
      * Preserves leading zeros.
      * Never uses scientific notation.
    """
    if value is None:
        return ""

    # If a float sneaks in, render it without scientific notation.
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        text = f"{value:.0f}" if value == int(value) else repr(value)
    elif isinstance(value, int):
        text = str(value)
    else:
        text = str(value)

    text = text.strip()
    if not text:
        return ""

    # Strip surrounding spaces, internal spaces, hyphens, commas.
    text = text.replace(" ", "").replace("-", "").replace(",", "")

    # Remove a single trailing ".0" (float artifact). Keep other content intact.
    if text.endswith(".0"):
        text = text[:-2]

    # Remove any remaining non-numeric formatting characters, but keep digits.
    text = re.sub(r"[^0-9]", "", text)

    return text


def _normalize_material(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:
            return None
        if value == int(value):
            return str(int(value))
        return repr(value)
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text or None


def build_upc_index(
    df: pd.DataFrame,
    unit_col: str,
    case_col: str | None,
    material_col: str,
    description_col: str | None = None,
) -> dict:
    """Build a lookup index mapping normalized UPC -> material info.

    Returns a dict with two sub-indexes (``unit`` and ``case``). Each maps a
    normalized UPC to a dict ``{materials: set[str], description: str|None}``.
    """
    index: dict[str, dict[str, dict]] = {"unit": {}, "case": {}}

    def add(kind: str, upc_value, material, description):
        upc = normalize_upc(upc_value)
        material = _normalize_material(material)
        if not upc or not material:
            return
        bucket = index[kind].setdefault(upc, {"materials": set(), "description": None})
        bucket["materials"].add(material)
        if description and not bucket["description"]:
            bucket["description"] = str(description).strip()

    for _, row in df.iterrows():
        material = row.get(material_col)
        description = row.get(description_col) if description_col else None
        if unit_col and unit_col in df.columns:
            add("unit", row.get(unit_col), material, description)
        if case_col and case_col in df.columns:
            add("case", row.get(case_col), material, description)

    return index


def match_upc(index: dict, upc_value) -> MaterialMatchResult:
    """Match a single UPC against the LCL index using exact matching only.

    Priority: Unit UPC Code first, then Case UPC Code.
    """
    upc = normalize_upc(upc_value)
    if not upc:
        return MaterialMatchResult(upc=upc, status="not_found")

    for source in ("unit", "case"):
        bucket = index.get(source, {}).get(upc)
        if not bucket:
            continue
        materials = sorted(bucket["materials"])
        if len(materials) == 1:
            return MaterialMatchResult(
                upc=upc,
                status="matched",
                material_number=materials[0],
                match_source=source,
                material_description=bucket["description"],
                candidates=materials,
            )
        # Multiple different materials for the same UPC -> ambiguous.
        return MaterialMatchResult(
            upc=upc,
            status="ambiguous",
            match_source=source,
            material_description=bucket["description"],
            candidates=materials,
        )

    return MaterialMatchResult(upc=upc, status="not_found")


def duplicate_upc_stats(index: dict) -> dict:
    """Return counts of UPCs that map to more than one material."""
    stats = {"unit_ambiguous": 0, "case_ambiguous": 0}
    for upc, bucket in index.get("unit", {}).items():
        if len(bucket["materials"]) > 1:
            stats["unit_ambiguous"] += 1
    for upc, bucket in index.get("case", {}).items():
        if len(bucket["materials"]) > 1:
            stats["case_ambiguous"] += 1
    return stats


# --- Result formatting helpers ------------------------------------------------

INVALID_MESSAGE = "Material not valid in LCL"


def format_quantity(qty) -> str:
    """Format a quantity: whole numbers without ``.0``; keep decimals otherwise."""
    if qty is None:
        return ""
    if not isinstance(qty, Decimal):
        try:
            qty = Decimal(str(qty))
        except (InvalidOperation, ValueError):
            return str(qty)
    if qty == qty.to_integral_value():
        return str(int(qty))
    return format(qty.normalize(), "f")


def format_amount(amount) -> str:
    """Format a monetary amount with exactly two decimals, no dollar sign."""
    if amount is None:
        return ""
    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return str(amount)
    return f"{amount:.2f}"


def format_result_line(material_or_upc: str, qty, net, valid: bool) -> str:
    """Build a single result line for the result cell.

    Valid:   ``MATERIAL#, QTY, AMOUNT``
    Invalid: ``UPC, QTY, AMOUNT - Material not valid in LCL``
    """
    base = f"{material_or_upc}, {format_quantity(qty)}, {format_amount(net)}"
    if valid:
        return base
    return f"{base} - {INVALID_MESSAGE}"
