"""Apply the debit-memo business rules and build the result objects.

One :class:`DebitMemoResult` is produced per debit memo. Every item of a memo is
rendered into a single combined cell (one line each). A memo becomes
``Manual Review`` if ANY item needs review, the reference does not match exactly
one short-paid row, the PDF failed to parse, or the memo is a duplicate upload.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from . import material_matcher as mm
from . import reference_matcher as rm
from .formatting import format_item_line, format_money, format_quantity
from .models import (
    STATUS_PROCESSED,
    STATUS_REVIEW,
    DebitMemo,
    DebitMemoResult,
    ItemResult,
    ProcessingSummary,
)


def _dedup_key(memo: DebitMemo) -> tuple[str, str, str]:
    return (
        rm.normalize_reference(memo.vendor_reference),
        (memo.debit_number or "").strip(),
        (memo.po_number or "").strip(),
    )


def _process_item(item, index: mm.MaterialIndex) -> ItemResult:
    """Match one item's UPC, compute the quantity difference, build its line."""
    upc = (item.upc or "").strip() or None
    total = item.item_total
    reasons: list[str] = []

    # Quantity difference = Quantity Invoiced - Quantity Received.
    qty_diff: Decimal | None = None
    if item.qty_invoiced is None or item.qty_received is None:
        reasons.append("Missing quantity received or invoiced")
    else:
        qty_diff = item.qty_invoiced - item.qty_received

    if total is None:
        reasons.append(f"Missing item Total for UPC {upc or '(missing)'}")

    # UPC -> material.
    label = None
    material_number = None
    if not upc:
        reasons.append("Missing UPC")
        label = "UPC (missing) not found"
    else:
        status, material_number, candidates = index.lookup(upc)
        if status == mm.UPC_FOUND:
            label = material_number
        elif status == mm.UPC_NOT_FOUND:
            label = f"UPC {upc} not found"
            reasons.append(f"UPC {upc} not found in material list")
        else:  # multiple
            label = f"UPC {upc} matched multiple materials"
            reasons.append(f"UPC matched multiple material rows: {upc}")

    # Negative quantity difference -> review (never auto-approve a negative).
    if qty_diff is not None and qty_diff < 0:
        reasons.append(f"Negative quantity difference for UPC {upc or '(missing)'}")

    display_line = format_item_line(label, qty_diff, total)
    return ItemResult(
        upc=upc,
        material_number=material_number,
        quantity_difference=qty_diff,
        item_total=total,
        display_line=display_line,
        review_reason="; ".join(reasons) if reasons else None,
    )


def _result_for_memo(memo: DebitMemo, sp_df, ref_column, index) -> DebitMemoResult:
    ref_norm = rm.normalize_reference(memo.vendor_reference)
    result = DebitMemoResult(
        source_file=memo.source_file,
        vendor_reference=ref_norm or None,
        debit_number=memo.debit_number,
        po_number=memo.po_number,
    )
    reasons: list[str] = []

    # Failed parse: keep whatever header values were found, route to review.
    if memo.parse_error:
        result.status = STATUS_REVIEW
        result.review_reason = f"PDF parsing error: {memo.parse_error}"
        return result

    # Reference -> short-paid row.
    if not ref_norm:
        reasons.append("Missing Vendor Reference Number")
    else:
        status, _rows = rm.match_reference(sp_df, ref_column, memo.vendor_reference)
        if status == rm.MATCH_NOT_FOUND:
            reasons.append("Vendor Reference Number not found in short-paid list")
        elif status == rm.MATCH_DUPLICATE:
            reasons.append("Duplicate Vendor Reference Number in short-paid list")

    # Items.
    if not memo.items:
        reasons.append("No item rows extracted from PDF")
    item_results = [_process_item(it, index) for it in memo.items]
    result.item_results = item_results
    result.cell_text = "\n".join(ir.display_line for ir in item_results)
    for ir in item_results:
        if ir.review_reason:
            reasons.append(ir.review_reason)

    result.status = STATUS_REVIEW if reasons else STATUS_PROCESSED
    result.review_reason = "; ".join(reasons)
    return result


def process(
    memos: list[DebitMemo],
    sp_df: pd.DataFrame,
    ref_column,
    index: mm.MaterialIndex,
) -> tuple[list[DebitMemoResult], ProcessingSummary]:
    """Process all memos into result rows plus a KPI summary.

    Duplicate uploads (same normalized reference + debit number + PO number)
    collapse to a single ``Manual Review`` row instead of repeated rows.
    """
    # Group by dedup key, preserving first-seen order.
    order: list[tuple[str, str, str]] = []
    groups: dict[tuple[str, str, str], list[DebitMemo]] = {}
    for memo in memos:
        key = _dedup_key(memo)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(memo)

    results: list[DebitMemoResult] = []
    for key in order:
        members = groups[key]
        primary = members[0]
        result = _result_for_memo(primary, sp_df, ref_column, index)
        if len(members) > 1:
            # Same memo uploaded more than once -> one Manual Review row.
            result.status = STATUS_REVIEW
            dup_reason = "Duplicate debit memo uploaded"
            result.review_reason = (
                f"{dup_reason}; {result.review_reason}" if result.review_reason else dup_reason
            )
        results.append(result)

    summary = ProcessingSummary(
        pdfs_uploaded=len(memos),
        processed=sum(1 for r in results if r.status == STATUS_PROCESSED),
        manual_review=sum(1 for r in results if r.status == STATUS_REVIEW),
        items_extracted=sum(len(r.item_results) for r in results),
        upcs_not_found=sum(
            1
            for r in results
            for ir in r.item_results
            if ir.upc and ir.material_number is None and "not found in material list" in (ir.review_reason or "")
        ),
    )
    return results, summary
