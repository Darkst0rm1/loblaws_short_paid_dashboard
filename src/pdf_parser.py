"""Extract debit-memo header fields and item rows from a PDF.

This module is responsible ONLY for extraction (no business rules). It returns a
:class:`~src.models.DebitMemo` and never raises for a bad PDF — parsing problems
are reported via ``DebitMemo.parse_error`` so the caller can route the memo to
manual review and keep processing the remaining files.

Loblaws debit memos lay their item table out in columns; extracted linearly the
text collapses to one value per line. The primary parser therefore works on word
**coordinates** (cluster words into visual rows by vertical position, assign to
columns by horizontal position relative to the table header). A regex line
parser is kept as a fallback for memos that extract as single-line rows and for
the OCR-text path. OCR is used only when a page yields no usable text/words; no
external AI/API/internet service is used.
"""
from __future__ import annotations

import io
import logging
import re
from decimal import Decimal, InvalidOperation

from .models import DebitMemo, DebitMemoItem

logger = logging.getLogger(__name__)

_MONEY = r"-?\(?\$?[\d,]+\.\d{2}\)?"
_INT = r"-?\d+"

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

# Label-then-value header patterns (value sits on the next non-empty line).
_LABEL_VALUE = [
    ("debit_number", re.compile(r"debit\s*number", re.IGNORECASE)),
    ("vendor_reference", re.compile(r"vendor\s*reference\s*number", re.IGNORECASE)),
    ("po_number", re.compile(r"\bp\.?\s*o\.?\s*number", re.IGNORECASE)),
]
# Labels that carry a second comma-separated value which is a date.
_DATE_BEARING = {"debit_number": "debit_date"}


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


# --- header field extraction --------------------------------------------------

def _extract_header_fields(text: str) -> dict:
    """Extract Debit Number, Debit Date, PO Number and Vendor Reference Number.

    Loblaws uses a label-then-value layout, e.g. a line ``Debit Number, Debit
    Date`` followed by ``1709032065, 2025/10/17``.
    """
    fields: dict[str, str] = {}
    lines = [ln.strip() for ln in text.splitlines()]
    nonempty = [i for i, ln in enumerate(lines) if ln]

    for pos, i in enumerate(nonempty):
        line = lines[i]
        if re.search(r"\d", line):
            continue  # value lines are handled when we reach their label
        for field, rx in _LABEL_VALUE:
            if field in fields or not rx.search(line):
                continue
            if pos + 1 < len(nonempty):
                value_line = lines[nonempty[pos + 1]]
                parts = [p.strip() for p in value_line.split(",")]
                if parts and parts[0]:
                    fields[field] = parts[0]
                    date_key = _DATE_BEARING.get(field)
                    if date_key and len(parts) > 1 and parts[1]:
                        fields[date_key] = parts[1]
            break
    return fields


# --- coordinate-based table parser --------------------------------------------

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
_COLUMN_SLACK = 3.0


def _cluster_rows(words: list[dict], tol: float = 4.0) -> list[list[dict]]:
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


def _item_from_cells(cells: dict) -> DebitMemoItem:
    qrec = parse_int(cells.get("qty_rec"))
    qinv = parse_int(cells.get("qty_inv"))
    upc = re.sub(r"\D", "", cells.get("upc", ""))
    warnings: list[str] = []
    total = parse_money(cells.get("total"))
    if total is None:
        # Fall back to Net if a separate Total column is absent.
        total = parse_money(cells.get("net"))
    return DebitMemoItem(
        upc=upc or None,
        description=(cells.get("description") or "").strip() or None,
        qty_received=qrec,
        qty_invoiced=qinv,
        po_price=parse_money(cells.get("po_price")),
        invoice_price=parse_money(cells.get("invoice_price")),
        item_total=total,
        warnings=warnings,
    )


def _parse_words(words: list[dict]) -> list[DebitMemoItem]:
    rows = _cluster_rows(words)
    header_idx = next((i for i, r in enumerate(rows) if _is_header_row(r)), None)
    if header_idx is None:
        return []
    anchors = _build_column_anchors(rows[header_idx])
    if not any(name == "upc" for name, _ in anchors):
        return []

    items: list[DebitMemoItem] = []
    current: DebitMemoItem | None = None
    for row in rows[header_idx + 1:]:
        cells = _assign_to_columns(row, anchors)
        upc = re.sub(r"\D", "", cells.get("upc", ""))
        if len(upc) >= 6:
            current = _item_from_cells(cells)
            items.append(current)
            continue
        alpha = re.sub(r"[^a-z]", "", " ".join(w["text"] for w in row).lower())
        if items and any(k in alpha for k in _TOTAL_KEYWORDS):
            break
        if current is not None:
            nonempty = {k: v for k, v in cells.items() if v and v.strip()}
            if nonempty and set(nonempty) <= {"description"}:
                current.description = ((current.description or "") + " " + nonempty["description"]).strip()
    return items


# --- regex line parser (fallback path) ---------------------------------------

def _net_from_nums(nums: list[Decimal]):
    inv = net = taxes = total = None
    n = len(nums)
    if n >= 4:
        total, taxes, net, inv = nums[-1], nums[-2], nums[-3], nums[-4]
    elif n == 3:
        total, net, inv = nums[-1], nums[-2], nums[-3]
    elif n == 2:
        total, net = nums[-1], nums[-2]
    elif n == 1:
        net = nums[-1]
    return inv, net, taxes, total


def _parse_line(text: str) -> DebitMemoItem | None:
    raw = text.strip()
    if not raw:
        return None
    m = _LINE_RE.match(raw)
    if not m:
        return None
    nums = [parse_money(t) for t in re.findall(_MONEY, m.group("nums"))]
    nums = [x for x in nums if x is not None]
    inv_price, net, _taxes, total = _net_from_nums(nums)
    return DebitMemoItem(
        upc=m.group("upc"),
        description=m.group("desc").strip() or None,
        qty_received=parse_int(m.group("qrec")),
        qty_invoiced=parse_int(m.group("qinv")),
        invoice_price=inv_price,
        item_total=total if total is not None else net,
    )


def _parse_text_items(text: str) -> list[DebitMemoItem]:
    items: list[DebitMemoItem] = []
    last: DebitMemoItem | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        item = _parse_line(stripped)
        if item is not None:
            items.append(item)
            last = item
            continue
        if last is not None and not re.search(r"\d", stripped):
            last.description = f"{last.description or ''} {stripped}".strip()
    return items


# --- PDF extraction -----------------------------------------------------------

def _extract_pages(file_bytes: bytes) -> list[dict]:
    """Extract per-page ``{words, text, ocr_used}`` from a PDF."""
    plumber_pages: list[tuple[str, list[dict]]] = []
    try:
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
    try:
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


def _is_encrypted(file_bytes: bytes) -> bool:
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        needs = bool(doc.needs_pass)
        doc.close()
        return needs
    except Exception:
        return False


def parse_pdf(file_bytes: bytes, filename: str) -> DebitMemo:
    """Parse a PDF into a :class:`DebitMemo`. Never raises; sets ``parse_error``."""
    memo = DebitMemo(source_file=filename)

    if not file_bytes:
        memo.parse_error = "Empty PDF (no content)"
        return memo
    if _is_encrypted(file_bytes):
        memo.parse_error = "Password-protected PDF; cannot read"
        return memo

    try:
        pages = _extract_pages(file_bytes)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to extract %s", filename)
        memo.parse_error = f"Could not read PDF: {exc}"
        return memo

    if not pages:
        memo.parse_error = "Could not extract any text from PDF"
        return memo

    memo.page_count = len(pages)
    header = _extract_header_fields("\n".join(p["text"] for p in pages))
    memo.debit_number = header.get("debit_number")
    memo.debit_date = header.get("debit_date")
    memo.po_number = header.get("po_number")
    memo.vendor_reference = header.get("vendor_reference")

    ocr_used = False
    for page in pages:
        ocr_used = ocr_used or page["ocr_used"]
        page_items = _parse_words(page["words"]) if page["words"] else []
        if not page_items:
            page_items = _parse_text_items(page["text"])
        memo.items.extend(page_items)
    memo.ocr_used = ocr_used

    if not memo.items:
        memo.warnings.append("No item rows recognized in this PDF")
        if not any([memo.debit_number, memo.vendor_reference, memo.po_number]):
            memo.parse_error = "Unreadable item table and no header fields found"
    return memo
