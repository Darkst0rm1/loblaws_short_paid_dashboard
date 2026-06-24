"""Parse debit memo PDFs into structured product lines.

Design notes
------------
* Loblaws debit memos lay their product table out in columns. When the PDF
  text is extracted linearly it collapses to one value per line, so a plain
  regex line parser sees no complete rows. The primary parser therefore works
  on word **coordinates**: words are clustered into visual rows by their
  vertical position (``top``) and assigned to columns by their horizontal
  position (``x0``) relative to the table header.
* A regex line parser (``parse_line`` / ``parse_debit_memo_text``) is kept as a
  fallback for debit memos that *do* extract as single-line rows, and for the
  OCR text path. Both are exercised by the test-suite without real PDFs.
* OCR is used ONLY for pages that yield no usable text or words.
* Document identifiers (Debit Number, Vendor Reference, PO, Vendor Number) are
  read from the header text, which uses a vertical *label-then-value* layout
  (e.g. a line ``Debit Number, Debit Date`` followed by ``1709032065, ...``).
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

# Regex single-line product row (fallback path).
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

# Inline "Label: value" header patterns (fallback for other memo formats).
_DM_HEADER = re.compile(r"(?:debit\s*memo|dm)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_INV_HEADER = re.compile(r"invoice\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_INVREF_HEADER = re.compile(r"invoice\s*ref(?:erence)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_VENDOR_HEADER = re.compile(r"vendor\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_STORE_HEADER = re.compile(r"store\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
_DATE_HEADER = re.compile(r"date\s*[:#]?\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})", re.IGNORECASE)

# Label-then-value header patterns (value sits on the next line).
_LABEL_VALUE = [
    ("debit_memo_number", re.compile(r"debit\s*number", re.IGNORECASE)),
    ("vendor_reference", re.compile(r"vendor\s*reference\s*number", re.IGNORECASE)),
    ("po_number", re.compile(r"\bp\.?\s*o\.?\s*number", re.IGNORECASE)),
    ("vendor_number", re.compile(r"vendor\s*number", re.IGNORECASE)),
    ("invoice_number", re.compile(r"invoice\s*number", re.IGNORECASE)),
    ("invoice_reference", re.compile(r"invoice\s*reference", re.IGNORECASE)),
]


# --- numeric helpers ----------------------------------------------------------

def parse_money(token) -> Decimal | None:
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


def parse_int(token) -> Decimal | None:
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


# --- header field extraction --------------------------------------------------

def _extract_header_fields(text: str) -> dict:
    """Extract document identifiers from header text.

    Handles two layouts:
      1. Label-then-value: a label line with no digits (e.g. "Debit Number,
         Debit Date") followed by a value line ("1709032065, 2025/10/17").
      2. Inline "Label: value" (e.g. "Debit Memo No: DM123").
    """
    fields: dict[str, str] = {}

    lines = [ln.strip() for ln in text.splitlines()]
    nonempty = [i for i, ln in enumerate(lines) if ln]

    # Pass 1: label-then-value pairs.
    for pos, i in enumerate(nonempty):
        line = lines[i]
        if re.search(r"\d", line):
            # The value is on this same line -> handled by the inline pass.
            continue
        for field, rx in _LABEL_VALUE:
            if field in fields:
                continue
            if rx.search(line):
                if pos + 1 < len(nonempty):
                    value = lines[nonempty[pos + 1]].split(",")[0].strip()
                    if value:
                        fields[field] = value
                break

    # Pass 2: inline "Label: value" fallbacks for anything still missing.
    for key, rx in (
        ("invoice_reference", _INVREF_HEADER),
        ("debit_memo_number", _DM_HEADER),
        ("invoice_number", _INV_HEADER),
        ("vendor_number", _VENDOR_HEADER),
        ("store_number", _STORE_HEADER),
        ("document_date", _DATE_HEADER),
    ):
        if fields.get(key):
            continue
        match = rx.search(text)
        if match:
            val = match.group(1).strip()
            # Require a digit so we never capture stray words like "Price".
            if val and re.search(r"\d", val):
                fields[key] = val

    return fields


# --- coordinate-based table parser --------------------------------------------

# Header label text -> canonical column key. "qty" is handled separately because
# it appears twice (Qty Rec. / Qty Inv.).
_HEADER_LABELS = {
    "upc": "upc",
    "description": "description",
    "unit": "unit",
    "po": "po_price",
    "invoice": "invoice_price",
    "net": "net",
    "taxes": "taxes",
    "tax": "taxes",
    "total": "total",
}

_TOTAL_KEYWORDS = ("subtotal", "grandtotal", "totalcad", "gsthst", "gst", "pst", "qst")

# Words starting up to this many points left of a column's header still belong
# to that column (handles values that nudge slightly left of the header).
_COLUMN_SLACK = 3.0


def _cluster_rows(words: list[dict], tol: float = 4.0) -> list[list[dict]]:
    """Group words into visual rows by their vertical position (``top``)."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    current = [ordered[0]]
    anchor_top = ordered[0]["top"]
    for w in ordered[1:]:
        if abs(w["top"] - anchor_top) <= tol:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
            anchor_top = w["top"]
    rows.append(sorted(current, key=lambda x: x["x0"]))
    return rows


def _is_header_row(row: list[dict]) -> bool:
    texts = {w["text"].strip().lower().strip("#.,:") for w in row}
    return "upc" in texts and "description" in texts and ("net" in texts or "total" in texts)


def _build_column_anchors(header_row: list[dict]) -> list[tuple[str, float]]:
    """Build ``[(column_name, left_x)]`` anchors from a header row."""
    anchors: list[tuple[str, float]] = []
    qty_x: list[float] = []
    for w in sorted(header_row, key=lambda x: x["x0"]):
        token = w["text"].strip().lower().strip("#.,:")
        if token == "qty":
            qty_x.append(w["x0"])
        elif token in _HEADER_LABELS:
            anchors.append((_HEADER_LABELS[token], w["x0"]))
    if len(qty_x) >= 2:
        anchors += [("qty_rec", qty_x[0]), ("qty_inv", qty_x[1])]
    elif len(qty_x) == 1:
        anchors.append(("qty_rec", qty_x[0]))
    anchors.sort(key=lambda a: a[1])
    return anchors


def _assign_to_columns(row: list[dict], anchors: list[tuple[str, float]]) -> dict:
    """Assign each word in ``row`` to the column whose left edge it falls under."""
    names = [a[0] for a in anchors]
    lefts = [a[1] for a in anchors]
    cells = {n: "" for n in names}
    for w in sorted(row, key=lambda x: x["x0"]):
        x = w["x0"]
        idx = 0
        for i in range(len(lefts)):
            if x >= lefts[i] - _COLUMN_SLACK:
                idx = i
            else:
                break
        cells[names[idx]] = (cells[names[idx]] + " " + w["text"]).strip()
    return cells


def _line_from_cells(cells: dict, upc: str, source_file: str, page_number: int) -> DebitMemoLine:
    qrec = parse_int(cells.get("qty_rec"))
    qinv = parse_int(cells.get("qty_inv"))
    short, status, warnings = compute_short_quantity(qinv, qrec)
    net = parse_money(cells.get("net"))
    if net is None:
        warnings = list(warnings) + ["Net amount not found"]
        status = "Review required"
    return DebitMemoLine(
        source_file=source_file,
        page_number=page_number,
        upc=upc,
        description=(cells.get("description") or "").strip() or None,
        qty_received=qrec,
        qty_invoiced=qinv,
        short_quantity=short,
        unit=(cells.get("unit") or "").strip() or None,
        po_price=parse_money(cells.get("po_price")),
        invoice_price=parse_money(cells.get("invoice_price")),
        net_amount=net,
        taxes=parse_money(cells.get("taxes")),
        total_amount=parse_money(cells.get("total")),
        status=status,
        warnings=warnings,
    )


def parse_words(words: list[dict], source_file: str, page_number: int) -> list[DebitMemoLine]:
    """Parse product lines from positioned words (the primary path).

    ``words`` is a list of dicts with at least ``text``, ``x0`` and ``top``.
    Returns ``[]`` if no table header is found (caller falls back to text).
    """
    rows = _cluster_rows(words)
    header_idx = next((i for i, r in enumerate(rows) if _is_header_row(r)), None)
    if header_idx is None:
        return []
    anchors = _build_column_anchors(rows[header_idx])
    if not any(name == "upc" for name, _ in anchors):
        return []

    lines: list[DebitMemoLine] = []
    current: DebitMemoLine | None = None
    for row in rows[header_idx + 1:]:
        cells = _assign_to_columns(row, anchors)
        upc = re.sub(r"\D", "", cells.get("upc", ""))
        if len(upc) >= 6:
            current = _line_from_cells(cells, upc, source_file, page_number)
            lines.append(current)
            continue
        # Stop once we reach the totals / tax summary block.
        alpha = re.sub(r"[^a-z]", "", " ".join(w["text"] for w in row).lower())
        if lines and any(k in alpha for k in _TOTAL_KEYWORDS):
            break
        # Wrapped description: a row with content only in the description column.
        if current is not None:
            nonempty = {k: v for k, v in cells.items() if v and v.strip()}
            if nonempty and set(nonempty) <= {"description"}:
                current.description = ((current.description or "") + " " + nonempty["description"]).strip()
    return lines


# --- regex line parser (fallback path) ---------------------------------------

def _net_from_nums(nums: list[Decimal]):
    """Map a trailing price block to (invoice_price, net, taxes, total)."""
    warnings: list[str] = []
    inv = net = taxes = total = None
    n = len(nums)
    if n >= 4:
        total, taxes, net, inv = nums[-1], nums[-2], nums[-3], nums[-4]
    elif n == 3:
        total, net, inv = nums[-1], nums[-2], nums[-3]
        warnings.append("Taxes column not found; assumed absent")
    elif n == 2:
        total, net = nums[-1], nums[-2]
        warnings.append("Sparse price block; mapped Net and Total only")
    elif n == 1:
        net = nums[-1]
        warnings.append("Single amount only; treated as Net")
    return inv, net, taxes, total, warnings


def parse_line(text: str, source_file: str, page_number: int) -> DebitMemoLine | None:
    """Parse a single-line product row (fallback path). None if not a product."""
    raw = text.strip()
    if not raw:
        return None
    m = _LINE_RE.match(raw)
    if not m:
        return None

    nums = [parse_money(t) for t in re.findall(_MONEY, m.group("nums"))]
    nums = [x for x in nums if x is not None]
    qrec = parse_int(m.group("qrec"))
    qinv = parse_int(m.group("qinv"))
    inv_price, net, taxes, total, num_warnings = _net_from_nums(nums)
    short, status, qty_warnings = compute_short_quantity(qinv, qrec)
    warnings = list(num_warnings) + list(qty_warnings)
    if net is None:
        warnings.append("Net amount not found")
        status = "Review required"

    return DebitMemoLine(
        source_file=source_file,
        page_number=page_number,
        upc=m.group("upc"),
        description=m.group("desc").strip(),
        qty_received=qrec,
        qty_invoiced=qinv,
        short_quantity=short,
        unit=m.group("unit").strip(),
        invoice_price=inv_price,
        net_amount=net,
        taxes=taxes,
        total_amount=total,
        status=status,
        warnings=warnings,
    )


_NOISE_RE = re.compile(
    r"^(page\b|subtotal|grand\s*total|total\b|tax\b|taxes\b|upc\b|description\b|qty\b|"
    r"vendor\b|store\b|invoice\b|debit\s*memo\b|date\b|p\.?o\.?\b)",
    re.IGNORECASE,
)


def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.match(line.strip()))


def parse_debit_memo_text(text: str, source_file: str, page_number: int = 1) -> DebitMemoDocument:
    """Parse a page of debit-memo *text* (fallback path / OCR text)."""
    doc = DebitMemoDocument(source_file=source_file, page_count=1)
    header = _extract_header_fields(text)
    doc.debit_memo_number = header.get("debit_memo_number")
    doc.invoice_number = header.get("invoice_number")
    doc.invoice_reference = header.get("invoice_reference")
    doc.vendor_reference = header.get("vendor_reference")
    doc.vendor_number = header.get("vendor_number")
    doc.store_number = header.get("store_number")
    doc.document_date = header.get("document_date")

    last_line: DebitMemoLine | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        line = parse_line(stripped, source_file, page_number)
        if line is not None:
            line.debit_memo_number = doc.debit_memo_number
            line.invoice_reference = doc.invoice_reference
            doc.lines.append(line)
            last_line = line
            continue
        if _is_noise(stripped):
            continue
        if last_line is not None and not re.search(r"\d", stripped):
            last_line.description = f"{last_line.description} {stripped}".strip()

    if not doc.lines:
        doc.status = "no_product_lines"
        doc.warnings.append("No product lines recognized in this document")
    return doc


# --- PDF extraction -----------------------------------------------------------

def _extract_pages_detailed(file_bytes: bytes) -> list[dict]:
    """Extract per-page ``{words, text, ocr_used}`` from a PDF.

    Words come from pdfplumber (best for the positioned table). Page text is
    preferred from PyMuPDF, which keeps multi-column header blocks in clean
    reading order (pdfplumber interleaves the left/right columns, which breaks
    the label-then-value header parsing). OCR is used only for pages with
    neither text nor words.
    """
    plumber_pages: list[tuple[str, list[dict]]] = []
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                words = [
                    {"text": w["text"], "x0": float(w["x0"]), "top": float(w["top"])}
                    for w in page.extract_words(use_text_flow=False, keep_blank_chars=False)
                ]
                plumber_pages.append((text, words))
    except Exception as exc:  # pragma: no cover - depends on runtime libs
        logger.warning("pdfplumber failed: %s", exc)

    mupdf_texts: list[str] = []
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        mupdf_texts = [page.get_text() or "" for page in doc]
        doc.close()
    except Exception as exc:  # pragma: no cover
        logger.warning("PyMuPDF failed: %s", exc)

    out: list[dict] = []
    n = max(len(plumber_pages), len(mupdf_texts))
    for i in range(n):
        text_pl, words = plumber_pages[i] if i < len(plumber_pages) else ("", [])
        text_mu = mupdf_texts[i] if i < len(mupdf_texts) else ""
        text = text_mu if text_mu.strip() else text_pl  # prefer clean PyMuPDF text
        if not text.strip() and not words:
            out.append({"words": [], "text": _ocr_page(file_bytes, i), "ocr_used": True})
        else:
            out.append({"words": words, "text": text, "ocr_used": False})
    return out


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
    pages = _extract_pages_detailed(file_bytes)
    if not pages:
        doc = DebitMemoDocument(source_file=filename, status="error")
        doc.warnings.append("Could not extract any text from PDF")
        return doc

    full = DebitMemoDocument(source_file=filename, page_count=len(pages))
    header = _extract_header_fields("\n".join(p["text"] for p in pages))
    full.debit_memo_number = header.get("debit_memo_number")
    full.invoice_number = header.get("invoice_number")
    full.invoice_reference = header.get("invoice_reference")
    full.vendor_reference = header.get("vendor_reference")
    full.vendor_number = header.get("vendor_number")
    full.store_number = header.get("store_number")
    full.document_date = header.get("document_date")

    ocr_used = False
    for page_no, page in enumerate(pages, start=1):
        ocr_used = ocr_used or page["ocr_used"]
        page_lines: list[DebitMemoLine] = []
        if page["words"]:
            page_lines = parse_words(page["words"], filename, page_no)
        if not page_lines:  # fallback to text line parser (and OCR text)
            page_lines = parse_debit_memo_text(page["text"], filename, page_no).lines
        for line in page_lines:
            line.ocr_used = page["ocr_used"]
            line.debit_memo_number = full.debit_memo_number
            line.invoice_number = full.invoice_number
            line.invoice_reference = full.invoice_reference
            full.lines.append(line)

    full.ocr_used = ocr_used
    if not full.lines:
        full.status = "no_product_lines"
        full.warnings.append("No product lines recognized in this PDF")
    return full
