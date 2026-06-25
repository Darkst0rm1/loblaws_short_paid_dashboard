# Loblaws Short Paid Dashboard

A Streamlit dashboard that processes Loblaws **debit memo PDFs**, matches each
memo to a row in the uploaded **LCL Short-Paid List** by its **Vendor Reference
Number**, resolves every item UPC to its **LCL Material number** using the
uploaded **LCL Material List**, and writes a new **`Debit Memo Results`**
worksheet into a downloadable copy of the short-paid workbook.

The original worksheets and their values are never modified.

## Workflow

1. Upload three inputs: LCL Material List, LCL Short-Paid List, and one or more
   debit memo PDFs. The **Process** button stays disabled until all three are
   provided.
2. Select the material-list sheet (default `Material listing 04.29.26`) and the
   short-paid source sheet (default `06.08.26`).
3. Each PDF is parsed, matched, and combined into one result row.
4. Review the KPI cards and the filterable results table.
5. Download the new workbook (originals untouched + the new results sheet).

## Required upload files

| Upload | Accepts | Example |
| ------ | ------- | ------- |
| LCL Material List | `.xlsx`, `.xlsm` | `LCL Material, Agreement, Slicer Database Feb 25 2026 copy.xlsm` |
| LCL Short-Paid List | `.xlsx` | `Loblaws short paid as of 06.08.26.xlsx` |
| Debit Memo PDFs | `.pdf` (multiple) | one or more debit memo PDFs |

## Debit memo extraction

Text and table extraction first (pdfplumber for positioned words, PyMuPDF for
clean header text); OCR is used only for pages with no extractable text. No
external AI/API/internet service is used. Extracted per memo: Debit Number,
Debit Date (when present), PO Number, Vendor Reference Number, and one or more
item rows (UPC, Description, Qty Received, Qty Invoiced, PO Price, Invoice
Price, Item Total).

## Matching rules

**Reference → short-paid row.** Normalize both sides (text, trimmed, leading
zeroes removed only — e.g. `0090091172` → `90091172`) then match exactly. The
reference column is detected by alias priority: `Vendor Reference Number`,
`Debit Description Number`, `FULL DEBIT DESCRIPTION`. No match → Manual Review;
more than one match → Manual Review (duplicates are never auto-resolved).

**UPC → material.** The whole selected material table is indexed once (every
column searched, not just Unit/Case UPC). UPC normalization removes formatting
spaces/commas/hyphens/apostrophes and a trailing `.0`. Exact normalized match
first; a leading-zero-insensitive match is used only if it resolves to exactly
one material. No match → the item is kept as `UPC {n} not found, {qty}, ${total}`
and flagged. Multiple materials → flagged for review (never auto-picked).

**Quantities & amounts.** `Quantity Difference = Quantity Invoiced − Quantity
Received` (zero is valid; negative is flagged for review). The amount is each
item's own extracted **Total** (never quantity × price, never the document
subtotal).

**One row per memo.** All items go in a single `Material, Quantity # and Amount`
cell, one line each (wrap text on). A memo is `Manual Review` if any item or the
reference needs review; all reasons are listed (`;`-separated).

## Result sheet

A new `Debit Memo Results` sheet (or `Debit Memo Results 2`, `3`, … if the name
exists) with columns: Vendor Reference Number, Debit Number, PO Number,
`Material, Quantity # and Amount`, Status, Review Reason. Bold frozen header,
autofilter, wrapped top-aligned cells, practical column widths, and conditional
highlighting of `Manual Review` rows.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

## Tests

```bash
pytest -q
```

Tests cover reference normalization/matching, whole-table UPC matching,
quantity logic, item-total usage, multi-item cells, mixed matched/unmatched
items, multiple-PDF and duplicate-PDF handling, error resilience, original-sheet
preservation, and result-sheet creation/collision.

## OCR fallback

OCR runs only on pages with no usable text and requires **Tesseract** installed
on the machine (`pytesseract` is just a wrapper). On Windows, install from
<https://github.com/UB-Mannheim/tesseract/wiki> and ensure `tesseract.exe` is on
your `PATH`. Without it, scanned PDFs are reported as parsing errors (Manual
Review) instead of crashing.

## Note on numeric precision

The workbook is round-tripped with openpyxl (the workbook-preserving library;
`pandas.ExcelWriter` is deliberately avoided). openpyxl conforms stored numbers
to ~15 significant digits — which is Excel's own storage precision — so a
pre-computed cell that happened to carry more than 15 significant digits may
change in its 16th digit after export. The effect is invisible: such cells are
currency-formatted, so the displayed value is identical to the cent, and
formulas, text, and formatting are preserved exactly. No source data is altered.
