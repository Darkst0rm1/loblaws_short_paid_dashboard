# Loblaws Short Paid Dashboard

A Streamlit dashboard that processes Loblaws **debit memo PDFs**, matches their
product UPCs against an uploaded **LCL Material Database**, and writes the final
product information directly into the correct rows of an uploaded **Loblaws
Short Paid List** under the column:

```
MATERIAL#, QTY & AMOUNT$
```

## Features

- Three upload sections: LCL Material Database, Short Paid List, and one or
  many Debit Memo PDFs.
- Text-based PDF extraction (pdfplumber / PyMuPDF) with OCR fallback only for
  scanned pages that contain no extractable text.
- Right-to-left numeric parsing of debit memo tables (Net is taken from the
  line, never the document total).
- Exact UPC matching against `Unit UPC Code` first, then `Case UPC Code`.
  **No fuzzy matching is ever used for UPCs.**
- Invalid UPCs are kept in the result with the exact text
  `Material not valid in LCL`.
- Controlled, high-threshold fuzzy matching only for invoice/document
  references when exact matching fails.
- Non-destructive Excel export: original rows, columns, worksheets, values and
  formatting are preserved; only the result column is added/updated.
- Full exception reporting workbook.

## Required upload files

| Upload | Accepts | Example |
| ------ | ------- | ------- |
| LCL Material Database | `.xlsx`, `.xlsm` | `LCL Material, Agreement, Slicer Database Feb 25 2026 copy.xlsm` |
| Loblaws Short Paid List | `.xlsx`, `.xlsm` | `Loblaws short paid as of 06.08.26.xlsx` |
| Debit Memo PDFs | `.pdf` (multiple) | one or more debit memo PDFs |

The `Material` column in the LCL database is the Tree of Life material number
returned in the result.

## Installation

```bash
python -m venv .venv
```

Activate on Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the dashboard

```bash
streamlit run app.py
```

## Run tests

```bash
pytest
```

## Debit memo PDF parsing behavior

- Normal text extraction is attempted first (pdfplumber, then PyMuPDF).
- A product row is recognized by a UPC (>= 6 digits) at the start of the line.
- Numbers are parsed from the right because spacing is inconsistent. Within the
  trailing price block the order from the right is: Total, Taxes, Net, Invoice
  Price, [PO Price]. Net is therefore the 3rd value from the right when taxes
  are present.
- Wrapped descriptions (continuation lines with no digits) are appended to the
  preceding product line.
- Repeated headers, footers, page numbers, subtotal/total/tax rows are ignored.

## OCR fallback behavior

OCR runs only on pages that produce no usable text. It requires **Tesseract**
to be installed on the machine (the `pytesseract` Python package is only a
wrapper). On Windows, install Tesseract from
<https://github.com/UB-Mannheim/tesseract/wiki> and ensure `tesseract.exe` is on
your `PATH`. If Tesseract is not installed, scanned PDFs are reported as
parsing errors instead of crashing the app.

## Material matching rules

1. Normalize the debit memo UPC.
2. Look up `Unit UPC Code` (exact).
3. If not found, look up `Case UPC Code` (exact).
4. Unique match -> return the `Material` number.
5. Not found -> keep the UPC and append `Material not valid in LCL`.
6. UPC mapped to multiple materials -> flagged `Review required`, not written.

## Short Paid row matching rules

Identifiers are normalized (strings, trimmed, `.0` removed, labels like
`Invoice`/`DM` removed, punctuation/spaces removed, case-insensitive). Matching
order: exact debit memo number, invoice reference, invoice number, reference,
then identifiers found inside `FULL DEBIT DESCRIPTION` / `Item Text`, then a
controlled fuzzy fallback (threshold 92). Ambiguous or missing matches are
never written automatically.

## Excel export behavior

The original workbook is loaded with openpyxl, the selected worksheet is
updated in place (result column added/updated, wrap text on, top-aligned, row
heights increased for multiline cells), and a **new** `.xlsx` file is returned.
The uploaded file is never modified.

## Exception export behavior

`Download Processing Exceptions` produces an `.xlsx` with one worksheet per
category: Invalid LCL Materials, Review Required, Unlinked Debit Memos,
Ambiguous UPC Matches, Parsing Errors, Duplicate PDFs, Validation Errors.

## Troubleshooting

- **Wrong worksheet detected** - use the worksheet selectors in Step 2.
- **Columns not detected** - use the manual LCL column mapping in Step 2.
- **Scanned PDF not parsed** - install Tesseract (see OCR section).
- **UPC turned into scientific notation in Excel** - the app reads cells as
  text (`dtype=object`) to preserve leading zeros; re-upload the original file.

## Testing instructions

```bash
pytest -q
```

Tests cover UPC normalization, material matching, quantity logic, result
formatting, reference matching, PDF text parsing, Excel export, and duplicate
detection.
