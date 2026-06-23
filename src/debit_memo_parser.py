"""Parse debit memo PDFs into structured product lines.

Design notes
------------
* Text-based extraction (pdfplumber / PyMuPDF) is attempted first.
* OCR is used ONLY for pages that yield no usable text.
* The core line parser works on plain text, so it is fully unit-testable
  without real PDF binaries.
* Numeric columns are parsed from the right because column spacing is
  inconsistent. Within the trailing price block the order from the right is:
  Total, Taxes, Net, Invoice Price, [PO Price]. ``Net`` is therefore the
  3rd value from the right when taxes are present, and the 2nd from the right
  when taxes are absent.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from .models import DebitMemoDocument, DebitMemoLine

logger = logging.getLogger(__name__)

# A money token like 1,234.56  -12.00  (3.50)
_MONEY = r"-?\(?\$?[\d,]+\.\d{2}\)?"
_INT = r"-?\d+"

# Tokens that must never be treated as a product UPC.
_HEADER_WORDS = re.compile(
    r"\b(UPC|Description|Qty|Rec|Inv|Unit|Price|Net|Tax|Taxes|Total|Page|Subtotal|Grand)\b",
    re.IGNORECASE,
)

# Product line: starts with a UPC of >=6 digits.
_LINE_RE = re.compile(
    rf"""^\s*
        (?P<upc>\d{{6,}})\s+
        (?P<desc>.*?)\s+
        (?P<qrec>{_INT})\s+
        (?P<qinv>{_INT})\s+
        (?P<unit>[A-Za-z]{{1,4}})\s+
        (?P<nums>(?:{_MONEY}\s*)+)
        \s*$
    """,
    re.VERBOSE,
)

_DM_HEADER = re.compile(r"(?:debit\s*memo|dm)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_INV_HEADER = re.compile(r"invoice\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_INVREF_HEADER = re.compile(r"invoice\s*ref(?:erence)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_VENDOR_HEADER = re.compile(r"vendor\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_STORE_HEADER = re.compile(r"store\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_DATE_HEADER = re.compile(r"date\s*[:#]?\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})", re.IGNORECASE)
_PO_HEADER = re.compile(r"\bP\.?O\.?\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)


def parse_money(token: str) -> Decimal | None:
    """Parse a money token (handles commas, $, parentheses for negatives)."""
    if token is None:
        return None
    text = str(token).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -value if negative else value


def parse_int(token: str) -> Decimal | None:
    if token is None:
        return None
    text = str(token).strip().replace(",", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def compute_short_quantity(qty_invoiced, qty_received):
    """Compute Short Quantity = Qty Inv. - Qty Rec.

    Returns ``(short_quantity, status, warnings)``.
    * status is ``"ok"`` or ``"Review required"``.
    * If a quantity cannot be parsed, no guess is made -> review required.
    * Negative results are preserved (never silently zeroed) and flagged.
    """
    warnings: list[str] = []

    if qty_invoiced is None or qty_received is None:
        return None, "Review required", ["Missing quantity; cannot compute short quantity"]

    short = qty_invoiced - qty_received
    if short < 0:
        warnings.append("Negative short quantity")
        return short, "Review required", warnings
    return short, "ok", warnings


def _net_from_nums(nums: list[Decimal]) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, list[str]]:
    """Map the trailing price block to (invoice_price, net, taxes, total).

    Layouts (left->right):
        5 nums: PO, Inv, Net, Taxes, Total
        4 nums: Inv, Net, Taxes, Total
        3 nums: Inv, Net, Total           (taxes missing)
        2 nums: Net, Total                (sparse)
    """
    warnings: list[str] = []
    inv = net = taxes = total = None
    n = len(nums)
    if n >= 4:
        total = nums[-1]
        taxes = nums[-2]
        net = nums[-3]
        inv = nums[-4]
    elif n == 3:
        total = nums[-1]
        net = nums[-2]
        inv = nums[-3]
        warnings.append("Taxes column not found; assumed absent")
    elif n == 2:
        total = nums[-1]
        net = nums[-2]
        warnings.append("Sparse price block; mapped Net and Total only")
    elif n == 1:
        net = nums[-1]
        warnings.append("Single amount only; treated as Net")
    return inv, net, taxes, total, warnings


def parse_line(text: str, source_file: str, page_number: int) -> DebitMemoLine | None:
    """Parse a single product row of text into a DebitMemoLine.

    Returns ``None`` when the row is not a product line (header/footer/total).
    """
    raw = text.strip()
    if not raw:
        return None

    m = _LINE_RE.match(raw)
    if not m:
        return None

    upc = m.group("upc")
    desc = m.group("desc").strip()

    nums = [parse_money(t) for t in re.findall(_MONEY, m.group("nums"))]
    nums = [x for x in nums if x is not None]

    qrec = parse_int(m.group("qrec"))
    qinv = parse_int(m.group("qinv"))
    unit = m.group("unit").strip()

    inv_price, net, taxes, total, num_warnings = _net_from_nums(nums)

    short, status, qty_warnings = compute_short_quantity(qinv, qrec)

    warnings = list(num_warnings) + list(qty_warnings)
    if net is None:
        warnings.append("Net amount not found")
        status = "Review required"

    line = DebitMemoLine(
        source_file=source_file,
        page_number=page_number,
        upc=upc,
        description=desc,
        qty_received=qrec,
        qty_invoiced=qinv,
        short_quantity=short,
        unit=unit,
        invoice_price=inv_price,
        net_amount=net,
        taxes=taxes,
        total_amount=total,
        status=status,
        warnings=warnings,
    )
    return line


def _extract_header_fields(text: str) -> dict:
    fields = {}
    for key, regex in (
        ("invoice_reference", _INVREF_HEADER),
        ("debit_memo_number", _DM_HEADER),
        ("invoice_number", _INV_HEADER),
        ("vendor_number", _VENDOR_HEADER),
        ("store_number", _STORE_HEADER),
        ("document_date", _DATE_HEADER),
    ):
        match = regex.search(text)
        if match:
            fields[key] = match.group(1).strip()
    return fields


def parse_debit_memo_text(text: str, source_file: str, page_number: int = 1) -> DebitMemoDocument:
    """Parse the full text of a (single-page) debit memo into a document.

    Handles wrapped descriptions by appending non-product lines that follow a
    product line to that product's description.
    """
    doc = DebitMemoDocument(source_file=source_file, page_count=1)
    header = _extract_header_fields(text)
    doc.debit_memo_number = header.get("debit_memo_number")
    doc.invoice_number = header.get("invoice_number")
    doc.invoice_reference = header.get("invoice_reference")
    doc.vendor_number = header.get("vendor_number")
    doc.store_number = header.get("store_number")
    doc.document_date = header.get("document_date")

    po_match = _PO_HEADER.search(text)
    po_number = po_match.group(1).strip() if po_match else None

    last_line: DebitMemoLine | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        line = parse_line(stripped, source_file, page_number)
        if line is not None:
            line.debit_memo_number = doc.debit_memo_number
            line.invoice_number = doc.invoice_number
            line.invoice_reference = doc.invoice_reference
            line.po_number = po_number
            doc.lines.append(line)
            last_line = line
            continue

        # Not a product line. Skip headers/footers/totals; otherwise treat as a
        # wrapped description continuation for the previous product line.
        if _is_noise(stripped):
            continue
        if last_line is not None and not re.search(r"\d", stripped):
            # Continuation text (no digits) -> append to description.
            last_line.description = f"{last_line.description} {stripped}".strip()

    if not doc.lines:
        doc.status = "no_product_lines"
        doc.warnings.append("No product lines recognized in this document")

    return doc


_NOISE_RE = re.compile(
    r"^(page\b|subtotal|grand\s*total|total\b|tax\b|taxes\b|upc\b|description\b|qty\b|"
    r"vendor\b|store\b|invoice\b|debit\s*memo\b|date\b|p\.?o\.?\b)",
    re.IGNORECASE,
)


def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.match(line.strip()))


# --- PDF extraction (real files) ---------------------------------------------

def extract_pages(file_bytes: bytes, filename: str = "") -> list[tuple[str, bool]]:
    """Extract text from each page of a PDF.

    Returns a list of ``(page_text, ocr_used)`` tuples. Falls back to OCR only
    for pages with no usable extracted text.
    """
    pages: list[tuple[str, bool]] = []
    text_pages = _extract_text_pages(file_bytes)
    for page_text in text_pages:
        if page_text and page_text.strip():
            pages.append((page_text, False))
        else:
            ocr_text = _ocr_page(file_bytes, len(pages))
            pages.append((ocr_text, True))
    return pages


def _extract_text_pages(file_bytes: bytes) -> list[str]:
    """Try pdfplumber, then PyMuPDF. Returns one string per page."""
    # Try pdfplumber.
    try:
        import io

        import pdfplumber

        out: list[str] = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                out.append(page.extract_text() or "")
        return out
    except Exception as exc:  # pragma: no cover - depends on runtime libs
        logger.warning("pdfplumber failed: %s", exc)

    try:
        import fitz  # PyMuPDF

        out = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            out.append(page.get_text() or "")
        doc.close()
        return out
    except Exception as exc:  # pragma: no cover
        logger.warning("PyMuPDF failed: %s", exc)

    return []


def _ocr_page(file_bytes: bytes, page_index: int) -> str:  # pragma: no cover - optional
    """OCR a single page. Requires PyMuPDF, Pillow and pytesseract/Tesseract."""
    try:
        import io

        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[page_index]
        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img)
        doc.close()
        return text
    except Exception as exc:
        logger.warning("OCR failed for page %s: %s", page_index, exc)
        return ""


def parse_pdf_bytes(file_bytes: bytes, filename: str) -> DebitMemoDocument:
    """Parse a real PDF file (bytes) into a DebitMemoDocument."""
    pages = extract_pages(file_bytes, filename)
    if not pages:
        doc = DebitMemoDocument(source_file=filename, status="error")
        doc.warnings.append("Could not extract any text from PDF")
        return doc

    full_doc = DebitMemoDocument(source_file=filename, page_count=len(pages))
    ocr_used = False
    for page_index, (page_text, page_ocr) in enumerate(pages, start=1):
        ocr_used = ocr_used or page_ocr
        page_doc = parse_debit_memo_text(page_text, filename, page_index)
        # Promote document-level header fields from the first page that has them.
        for attr in ("debit_memo_number", "invoice_number", "invoice_reference",
                     "vendor_number", "store_number", "document_date"):
            if getattr(full_doc, attr) is None and getattr(page_doc, attr) is not None:
                setattr(full_doc, attr, getattr(page_doc, attr))
        for line in page_doc.lines:
            line.ocr_used = page_ocr
            full_doc.lines.append(line)

    full_doc.ocr_used = ocr_used
    if not full_doc.lines:
        full_doc.status = "no_product_lines"
        full_doc.warnings.append("No product lines recognized in this PDF")
    return full_doc
