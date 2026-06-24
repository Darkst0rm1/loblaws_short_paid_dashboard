"""Typed data models for the Loblaws Short Paid dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class DebitMemoLine:
    """A single product line extracted from a debit memo PDF."""

    source_file: str
    page_number: int
    debit_memo_number: str | None = None
    invoice_number: str | None = None
    invoice_reference: str | None = None
    po_number: str | None = None
    upc: str | None = None
    description: str | None = None
    qty_received: Decimal | None = None
    qty_invoiced: Decimal | None = None
    short_quantity: Decimal | None = None
    unit: str | None = None
    po_price: Decimal | None = None
    invoice_price: Decimal | None = None
    net_amount: Decimal | None = None
    taxes: Decimal | None = None
    total_amount: Decimal | None = None
    ocr_used: bool = False
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)


@dataclass
class DebitMemoDocument:
    """Document-level identifiers parsed from a debit memo PDF header."""

    source_file: str
    page_count: int = 0
    debit_memo_number: str | None = None
    invoice_number: str | None = None
    invoice_reference: str | None = None
    vendor_reference: str | None = None
    vendor_number: str | None = None
    store_number: str | None = None
    document_date: str | None = None
    ocr_used: bool = False
    file_hash: str | None = None
    lines: list[DebitMemoLine] = field(default_factory=list)
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)


@dataclass
class MaterialMatchResult:
    """Result of matching a debit memo UPC against the LCL database."""

    upc: str
    status: str  # "matched" | "not_found" | "ambiguous"
    material_number: str | None = None
    match_source: str | None = None  # "unit" | "case"
    material_description: str | None = None
    candidates: list[str] = field(default_factory=list)


@dataclass
class ShortPaidMatchResult:
    """Result of matching a debit memo to a Short Paid List row."""

    status: str  # "matched" | "not_found" | "ambiguous"
    row_index: int | None = None
    candidate_rows: list[int] = field(default_factory=list)
    match_method: str | None = None
    match_score: float | None = None


@dataclass
class ProcessingException:
    """A record routed to the exception reports."""

    category: str
    reason: str
    source_file: str | None = None
    page_number: int | None = None
    debit_memo_number: str | None = None
    invoice_reference: str | None = None
    upc: str | None = None
    detail: str | None = None


@dataclass
class ValidationResult:
    """Outcome of the reconciliation / validation step."""

    ok: bool = True
    critical_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
