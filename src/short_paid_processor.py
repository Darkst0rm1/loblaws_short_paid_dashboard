"""Orchestrate: match debit memos to rows, build result cell content."""

from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

from . import material_matcher as mm
from .column_detector import RESULT_COLUMN
from .models import DebitMemoDocument, MaterialMatchResult, ProcessingException
from .reference_matcher import match_debit_memo_to_rows

logger = logging.getLogger(__name__)


def _line_dedup_key(line, dm_number):
    return (
        dm_number,
        line.upc,
        str(line.qty_received),
        str(line.qty_invoiced),
        str(line.net_amount),
    )


def build_result_line(line, match: MaterialMatchResult) -> str:
    """Produce the formatted result line for one product."""
    if match.status == "matched":
        return mm.format_result_line(match.material_number, line.short_quantity, line.net_amount, valid=True)
    # not_found -> invalid LCL material (keep the UPC in the result).
    return mm.format_result_line(line.upc, line.short_quantity, line.net_amount, valid=False)


class ProcessResult:
    def __init__(self):
        self.row_results: dict[int, list[str]] = defaultdict(list)
        self.matched_lines: list[dict] = []
        self.invalid_lines: list[dict] = []
        self.exceptions: list[ProcessingException] = []
        self.totals = {"short_qty": 0, "net": 0.0, "rows_updated": 0}


def process(
    short_paid_df: pd.DataFrame,
    documents: list[DebitMemoDocument],
    upc_index: dict,
    sp_columns: dict,
) -> ProcessResult:
    """Match each debit memo to a Short Paid row and assemble result cells."""
    result = ProcessResult()
    seen_keys: set = set()

    for doc in documents:
        if doc.status == "error":
            result.exceptions.append(
                ProcessingException(
                    category="Parsing Errors",
                    reason="; ".join(doc.warnings) or "Parsing failed",
                    source_file=doc.source_file,
                )
            )
            continue

        # Find the Short Paid row for this document.
        sp_match = match_debit_memo_to_rows(
            short_paid_df,
            debit_memo_number=doc.debit_memo_number,
            invoice_number=doc.invoice_number,
            invoice_reference=doc.invoice_reference,
            columns=sp_columns,
        )

        if sp_match.status == "not_found":
            result.exceptions.append(
                ProcessingException(
                    category="Unlinked Debit Memos",
                    reason="Short Paid row not found",
                    source_file=doc.source_file,
                    debit_memo_number=doc.debit_memo_number,
                    invoice_reference=doc.invoice_reference,
                )
            )
            continue
        if sp_match.status == "ambiguous":
            result.exceptions.append(
                ProcessingException(
                    category="Review Required",
                    reason="Debit memo matches multiple Short Paid rows",
                    source_file=doc.source_file,
                    debit_memo_number=doc.debit_memo_number,
                    invoice_reference=doc.invoice_reference,
                    detail=f"candidate rows: {sp_match.candidate_rows}",
                )
            )
            continue

        row_index = sp_match.row_index

        for line in doc.lines:
            key = _line_dedup_key(line, doc.debit_memo_number)
            if key in seen_keys:
                result.exceptions.append(
                    ProcessingException(
                        category="Duplicate PDFs",
                        reason="Duplicate product line skipped",
                        source_file=doc.source_file,
                        upc=line.upc,
                        debit_memo_number=doc.debit_memo_number,
                    )
                )
                continue
            seen_keys.add(key)

            match = mm.match_upc(upc_index, line.upc)

            review_reason = None
            if line.short_quantity is None:
                review_reason = "Missing / unparseable quantity"
            elif line.net_amount is None:
                review_reason = "Missing Net amount"
            elif line.short_quantity < 0:
                review_reason = "Negative short quantity"
            elif match.status == "ambiguous":
                review_reason = "UPC maps to multiple LCL materials"

            base_record = {
                "source_file": line.source_file,
                "page": line.page_number,
                "debit_memo_number": doc.debit_memo_number,
                "invoice_reference": doc.invoice_reference,
                "upc": line.upc,
                "description": line.description,
                "qty_received": line.qty_received,
                "qty_invoiced": line.qty_invoiced,
                "short_quantity": line.short_quantity,
                "unit": line.unit,
                "invoice_price": line.invoice_price,
                "net": line.net_amount,
                "taxes": line.taxes,
                "total": line.total_amount,
                "row_index": row_index,
                "match_method": sp_match.match_method,
                "match_score": sp_match.match_score,
                "ocr_used": line.ocr_used,
                "warnings": "; ".join(line.warnings),
            }

            if review_reason:
                result.exceptions.append(
                    ProcessingException(
                        category="Ambiguous UPC Matches" if match.status == "ambiguous" else "Review Required",
                        reason=review_reason,
                        source_file=line.source_file,
                        page_number=line.page_number,
                        debit_memo_number=doc.debit_memo_number,
                        invoice_reference=doc.invoice_reference,
                        upc=line.upc,
                        detail=f"candidates: {match.candidates}" if match.candidates else None,
                    )
                )
                # Do not write uncertain matches into the Short Paid List.
                continue

            result_line = build_result_line(line, match)
            result.row_results[row_index].append(result_line)

            record = dict(base_record)
            record["lcl_material"] = match.material_number
            record["material_description"] = match.material_description
            record["upc_match_source"] = match.match_source
            record["status"] = "matched" if match.status == "matched" else "Material not valid in LCL"

            if match.status == "matched":
                result.matched_lines.append(record)
            else:  # not_found -> invalid material, still written
                result.invalid_lines.append(record)
                result.exceptions.append(
                    ProcessingException(
                        category="Invalid LCL Materials",
                        reason=mm.INVALID_MESSAGE,
                        source_file=line.source_file,
                        page_number=line.page_number,
                        debit_memo_number=doc.debit_memo_number,
                        invoice_reference=doc.invoice_reference,
                        upc=line.upc,
                    )
                )

            if line.short_quantity is not None:
                result.totals["short_qty"] += float(line.short_quantity)
            if line.net_amount is not None:
                result.totals["net"] += float(line.net_amount)

    result.totals["rows_updated"] = len(result.row_results)
    return result


def assemble_cells(row_results: dict[int, list[str]]) -> dict[int, str]:
    """Join each row's product lines into a single multiline cell value."""
    cells = {}
    for row_index, lines in row_results.items():
        # De-duplicate identical lines while preserving order.
        seen = set()
        ordered = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                ordered.append(line)
        cells[row_index] = "\n".join(ordered)
    return cells
