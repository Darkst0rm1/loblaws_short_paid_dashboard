"""Typed data models for the Loblaws Short Paid debit-memo workflow.

These models describe the data as it flows through the pipeline:

    pdf_parser  -> DebitMemo (header fields + DebitMemoItem rows)
    processor   -> DebitMemoResult (one per memo) holding ItemResult rows
    app/export  -> ProcessingSummary (KPI counters)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# Status constants used in the result sheet / dashboard.
STATUS_PROCESSED = "Processed"
STATUS_REVIEW = "Manual Review"


@dataclass
class DebitMemoItem:
    """A single reduced-item row extracted from a debit memo PDF."""

    upc: str | None = None
    description: str | None = None
    qty_received: Decimal | None = None
    qty_invoiced: Decimal | None = None
    po_price: Decimal | None = None
    invoice_price: Decimal | None = None
    item_total: Decimal | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DebitMemo:
    """Header identifiers plus the item rows of one debit memo PDF."""

    source_file: str
    debit_number: str | None = None
    debit_date: str | None = None
    po_number: str | None = None
    vendor_reference: str | None = None
    items: list[DebitMemoItem] = field(default_factory=list)
    page_count: int = 0
    ocr_used: bool = False
    parse_error: str | None = None  # set when the PDF could not be read
    warnings: list[str] = field(default_factory=list)


@dataclass
class ItemResult:
    """The processing outcome for one debit-memo item."""

    upc: str | None = None
    material_number: str | None = None
    quantity_difference: Decimal | None = None
    item_total: Decimal | None = None
    display_line: str = ""        # the line rendered into the combined cell
    review_reason: str | None = None  # None when the item is clean


@dataclass
class DebitMemoResult:
    """One result row: a whole debit memo collapsed into a single record."""

    source_file: str
    vendor_reference: str | None = None      # normalized reference (output value)
    debit_number: str | None = None
    po_number: str | None = None
    cell_text: str = ""                       # multi-line Material/Qty/Amount
    status: str = STATUS_PROCESSED            # STATUS_PROCESSED | STATUS_REVIEW
    review_reason: str = ""                   # semicolon-joined reasons
    item_results: list[ItemResult] = field(default_factory=list)


@dataclass
class ProcessingSummary:
    """KPI counters for the dashboard."""

    pdfs_uploaded: int = 0
    processed: int = 0
    manual_review: int = 0
    items_extracted: int = 0
    upcs_not_found: int = 0
