"""Reconciliation and validation before allowing the final download."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from .material_matcher import INVALID_MESSAGE
from .models import ValidationResult


def validate(
    original_df: pd.DataFrame,
    row_cells: dict[int, str],
    matched_lines: list[dict],
    invalid_lines: list[dict],
) -> ValidationResult:
    """Run reconciliation checks. Critical errors block download."""
    result = ValidationResult()

    n_rows = len(original_df)

    # Every written result must belong to exactly one valid row.
    for row_index in row_cells:
        if not (0 <= int(row_index) < n_rows):
            result.critical_errors.append(
                f"Result targets row {row_index} which is outside the Short Paid range"
            )

    # Every valid result line must have material, qty, amount.
    for rec in matched_lines:
        if rec.get("lcl_material") in (None, ""):
            result.critical_errors.append(f"Matched line missing material (UPC {rec.get('upc')})")
        if rec.get("short_quantity") is None:
            result.critical_errors.append(f"Matched line missing quantity (UPC {rec.get('upc')})")
        if rec.get("net") is None:
            result.critical_errors.append(f"Matched line missing net amount (UPC {rec.get('upc')})")

    # Every invalid result line must carry UPC, qty, amount.
    for rec in invalid_lines:
        if not rec.get("upc"):
            result.critical_errors.append("Invalid line missing UPC")
        if rec.get("short_quantity") is None or rec.get("net") is None:
            result.critical_errors.append(f"Invalid line missing qty/amount (UPC {rec.get('upc')})")

    # Result cells for invalid materials must carry the exact message.
    for row_index, value in row_cells.items():
        if INVALID_MESSAGE.lower() in value.lower():
            # ensure the exact phrase is present
            if INVALID_MESSAGE not in value:
                result.warnings.append(f"Row {row_index}: invalid-material message casing differs")

    # Reconcile total net written vs eligible parsed net.
    written_net = Decimal("0")
    for rec in matched_lines + invalid_lines:
        net = rec.get("net")
        if net is not None:
            written_net += Decimal(str(net))

    if result.critical_errors:
        result.ok = False
    return result
