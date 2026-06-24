"""Diagnose why a debit memo PDF parses to nothing / lags.

Usage:
    .venv\\Scripts\\python.exe diagnose_pdf.py "C:\\path\\to\\debit_memo.pdf"

Reports, per page: extracted-text length (pdfplumber + PyMuPDF), image count
(to detect scanned/image-only PDFs), timing (to locate lag), a preview of the
extracted text, and how many product lines the current parser recognizes.
No customer data leaves the machine -- everything prints to your terminal.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main(path_str: str) -> None:
    path = Path(path_str)
    if not path.exists():
        print(f"FILE NOT FOUND: {path}")
        return

    data = path.read_bytes()
    print(f"File: {path.name}")
    print(f"Size: {len(data)/1024:.1f} KB")
    print("=" * 70)

    # --- pdfplumber ---------------------------------------------------------
    pp_pages, pp_lens, pp_images = [], [], []
    t0 = time.time()
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                pp_pages.append(txt)
                pp_lens.append(len(txt))
                pp_images.append(len(page.images))
        print(f"pdfplumber: {len(pp_pages)} page(s) in {time.time()-t0:.2f}s")
        print(f"  text length per page : {pp_lens}")
        print(f"  image count per page : {pp_images}")
    except Exception as exc:
        print(f"pdfplumber FAILED: {exc!r}")

    # --- PyMuPDF ------------------------------------------------------------
    mu_pages, mu_lens = [], []
    t0 = time.time()
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        for page in doc:
            txt = page.get_text() or ""
            mu_pages.append(txt)
            mu_lens.append(len(txt))
        print(f"PyMuPDF: {doc.page_count} page(s) in {time.time()-t0:.2f}s")
        print(f"  text length per page : {mu_lens}")
        doc.close()
    except Exception as exc:
        print(f"PyMuPDF FAILED: {exc!r}")

    # --- Scanned? -----------------------------------------------------------
    best = pp_pages if sum(pp_lens) >= sum(mu_lens) else mu_pages
    total_text = sum(len(p) for p in best)
    print("=" * 70)
    if total_text < 20:
        print("VERDICT: No embedded text -> this is almost certainly a SCANNED /")
        print("         image-only PDF. It needs OCR (Tesseract) to be read.")
    else:
        print(f"VERDICT: Embedded text found ({total_text} chars). Not scanned.")

    # --- Text preview -------------------------------------------------------
    print("-" * 70)
    print("TEXT PREVIEW (first 800 chars of best extraction):")
    print("-" * 70)
    joined = "\n".join(best)
    print(joined[:800] if joined.strip() else "<empty>")

    # --- Parser result ------------------------------------------------------
    print("-" * 70)
    try:
        from src.debit_memo_parser import parse_debit_memo_text

        total_lines = 0
        for i, page_text in enumerate(best, start=1):
            doc = parse_debit_memo_text(page_text, path.name, i)
            total_lines += len(doc.lines)
            if i == 1:
                print(f"Header parsed: DM#={doc.debit_memo_number}, "
                      f"inv_ref={doc.invoice_reference}, inv#={doc.invoice_number}")
        print(f"PRODUCT LINES RECOGNIZED BY CURRENT PARSER: {total_lines}")
        if total_lines == 0 and total_text >= 20:
            print(">> Text exists but the parser matched 0 rows -> the row layout")
            print("   differs from what the regex expects. Paste the preview above")
            print("   so the parser can be adjusted to this PDF's format.")
    except Exception as exc:
        print(f"Parser step failed: {exc!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python diagnose_pdf.py "C:\\path\\to\\debit_memo.pdf"')
    else:
        main(sys.argv[1])
