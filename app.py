"""Loblaws Short Paid Dashboard - Streamlit application."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from src import column_detector as cd
from src import excel_exporter, file_loader, validators
from src import material_matcher as mm
from src import short_paid_processor as spp
from src.debit_memo_parser import parse_pdf_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("loblaws_dashboard")

st.set_page_config(page_title="Loblaws Short Paid Dashboard", layout="wide")

RESULT_COLUMN = cd.RESULT_COLUMN


def _safe_index(columns, value) -> int:
    """Return the index of ``value`` in ``columns`` (0 if not found)."""
    cols = list(columns)
    return cols.index(value) if value in cols else 0


def _init_state():
    st.session_state.setdefault("processed", None)
    st.session_state.setdefault("seen_hashes", {})


_init_state()


@st.cache_data(show_spinner=False)
def _list_sheets(file_bytes: bytes):
    return file_loader.list_sheets(file_bytes)


@st.cache_data(show_spinner=False)
def _read_sheet(file_bytes: bytes, sheet_name: str):
    return file_loader.read_sheet(file_bytes, sheet_name)


@st.cache_data(show_spinner=False)
def _build_index(file_bytes: bytes, sheet_name: str, unit_col, case_col, material_col, desc_col):
    df = file_loader.read_sheet(file_bytes, sheet_name)
    return mm.build_upc_index(df, unit_col, case_col, material_col, desc_col)


st.title("Loblaws Short Paid Dashboard")
st.caption(
    "Match Loblaws debit memo PDFs to the LCL Material Database and write "
    "results into the Short Paid List column **MATERIAL#, QTY & AMOUNT$**."
)

# ---------------------------------------------------------------- Step 1: Upload
st.header("Step 1 - Upload Files")

col1, col2, col3 = st.columns(3)
with col1:
    lcl_file = st.file_uploader("LCL Material Database", type=["xlsx", "xlsm"], key="lcl")
with col2:
    sp_file = st.file_uploader("Loblaws Short Paid List", type=["xlsx", "xlsm"], key="sp")
with col3:
    debit_memo_files = st.file_uploader(
        "Upload Debit Memo PDFs", type=["pdf"], accept_multiple_files=True, key="pdfs"
    )

if st.button("Reset session"):
    st.session_state["processed"] = None
    st.session_state["seen_hashes"] = {}
    st.cache_data.clear()
    st.rerun()

if not (lcl_file and sp_file and debit_memo_files):
    st.info("Upload all three inputs (LCL database, Short Paid list, and at least one PDF) to continue.")
    st.stop()

lcl_bytes = lcl_file.getvalue()
sp_bytes = sp_file.getvalue()

total_pdf_size = sum(len(f.getvalue()) for f in debit_memo_files)
st.success(
    f"LCL: **{lcl_file.name}**  |  Short Paid: **{sp_file.name}**  |  "
    f"PDFs: **{len(debit_memo_files)}** ({total_pdf_size/1024:.1f} KB total)"
)

# ---------------------------------------------------------- Step 2: Validation
st.header("Step 2 - Workbook & Column Validation")

lcl_sheets = _list_sheets(lcl_bytes)
sp_sheets = _list_sheets(sp_bytes)

c1, c2 = st.columns(2)
with c1:
    default_lcl = file_loader.choose_lcl_sheet(lcl_sheets) or lcl_sheets[0]
    lcl_sheet = st.selectbox("LCL worksheet", lcl_sheets, index=lcl_sheets.index(default_lcl))
with c2:
    sp_sheet = st.selectbox("Short Paid worksheet", sp_sheets, index=0)

lcl_df = _read_sheet(lcl_bytes, lcl_sheet)
sp_df = _read_sheet(sp_bytes, sp_sheet)

lcl_cols = cd.detect_columns(lcl_df, cd.LCL_COLUMN_SPEC)
sp_cols = cd.detect_columns(sp_df, cd.SHORT_PAID_COLUMN_SPEC)

with st.expander("Detected LCL columns", expanded=False):
    st.json(lcl_cols)
with st.expander("Detected Short Paid columns", expanded=False):
    st.json(sp_cols)

# Manual mapping fallback for the critical LCL columns.
st.subheader("LCL column mapping")
m1, m2, m3, m4 = st.columns(4)
with m1:
    material_col = st.selectbox("Material #", lcl_df.columns,
                                index=_safe_index(lcl_df.columns, lcl_cols.get("material")))
with m2:
    unit_col = st.selectbox("Unit UPC", lcl_df.columns,
                            index=_safe_index(lcl_df.columns, lcl_cols.get("unit_upc")))
with m3:
    case_options = ["(none)"] + list(lcl_df.columns)
    case_default = lcl_cols.get("case_upc")
    case_col = st.selectbox("Case UPC", case_options,
                            index=case_options.index(case_default) if case_default in case_options else 0)
    case_col = None if case_col == "(none)" else case_col
with m4:
    desc_options = ["(none)"] + list(lcl_df.columns)
    desc_default = lcl_cols.get("material_description")
    desc_col = st.selectbox("Material Description", desc_options,
                            index=desc_options.index(desc_default) if desc_default in desc_options else 0)
    desc_col = None if desc_col == "(none)" else desc_col

missing = cd.missing_required({"material": material_col, "unit_upc": unit_col}, cd.LCL_REQUIRED)
if missing:
    st.error(f"Missing required LCL columns: {missing}")
    st.stop()

upc_index = _build_index(lcl_bytes, lcl_sheet, unit_col, case_col, material_col, desc_col)
dup_stats = mm.duplicate_upc_stats(upc_index)

mcols = st.columns(5)
mcols[0].metric("LCL material rows", len(lcl_df))
mcols[1].metric("Short Paid rows", len(sp_df))
mcols[2].metric("Debit memo PDFs", len(debit_memo_files))
mcols[3].metric("Unit UPC -> multi material", dup_stats["unit_ambiguous"])
mcols[4].metric("Case UPC -> multi material", dup_stats["case_ambiguous"])

# ------------------------------------------------------- Step 3: Parse PDFs
st.header("Step 3 - Parse Debit Memo PDFs")

if st.button("Process Debit Memo PDFs", type="primary"):
    documents = []
    progress = st.progress(0.0)
    seen_hashes = {}
    for i, f in enumerate(debit_memo_files, start=1):
        raw = f.getvalue()
        h = file_loader.file_hash(raw)
        if h in seen_hashes:
            st.warning(f"Duplicate PDF skipped: {f.name} (identical to {seen_hashes[h]})")
            progress.progress(i / len(debit_memo_files))
            continue
        seen_hashes[h] = f.name
        try:
            doc = parse_pdf_bytes(raw, f.name)
            doc.file_hash = h
        except Exception as exc:  # never crash the whole run
            logger.exception("Failed to parse %s", f.name)
            from src.models import DebitMemoDocument

            doc = DebitMemoDocument(source_file=f.name, status="error")
            doc.warnings.append(f"Exception: {exc}")
        documents.append(doc)

        st.write(
            f"**{f.name}** - pages: {doc.page_count}, "
            f"{'OCR' if doc.ocr_used else 'text'}, DM#: {doc.debit_memo_number}, "
            f"inv ref: {doc.invoice_reference}, lines: {len(doc.lines)}, status: {doc.status}"
        )
        progress.progress(i / len(debit_memo_files))

    result = spp.process(sp_df, documents, upc_index, sp_cols)
    row_cells = spp.assemble_cells(result.row_results)
    validation = validators.validate(sp_df, row_cells, result.matched_lines, result.invalid_lines)

    st.session_state["processed"] = {
        "documents": documents,
        "result": result,
        "row_cells": row_cells,
        "validation": validation,
        "sp_sheet": sp_sheet,
    }

processed = st.session_state.get("processed")
if not processed:
    st.info("Click **Process Debit Memo PDFs** to run matching.")
    st.stop()

result = processed["result"]
row_cells = processed["row_cells"]
validation = processed["validation"]
documents = processed["documents"]

# ------------------------------------------------------ Step 4: Summary cards
st.header("Step 4 - Summary")
s = st.columns(6)
parsed_ok = sum(1 for d in documents if d.status != "error")
ocr_count = sum(1 for d in documents if d.ocr_used)
err_count = sum(1 for d in documents if d.status == "error")
n_lines = sum(len(d.lines) for d in documents)
s[0].metric("PDFs parsed", parsed_ok)
s[1].metric("PDFs needing OCR", ocr_count)
s[2].metric("PDF errors", err_count)
s[3].metric("Product lines", n_lines)
s[4].metric("Rows updated", result.totals["rows_updated"])
s[5].metric("UPCs not in LCL", len(result.invalid_lines))

s2 = st.columns(4)
s2[0].metric("Materials found", len(result.matched_lines))
s2[1].metric("Total short qty", f"{result.totals['short_qty']:.0f}")
s2[2].metric("Total net amount", f"{result.totals['net']:.2f}")
s2[3].metric("Exceptions", len(result.exceptions))

if validation.critical_errors:
    st.error("Critical validation errors - download blocked:")
    for e in validation.critical_errors:
        st.write(f"- {e}")

# ------------------------------------------------------------- Tabs
tabs = st.tabs([
    "Updated Short Paid List",
    "Parsed Debit Memo Lines",
    "Matched Product Lines",
    "Invalid LCL Materials",
    "Review Required",
])

with tabs[0]:
    display = sp_df.copy()
    display[RESULT_COLUMN] = ""
    for idx, val in row_cells.items():
        if 0 <= idx < len(display):
            display.iat[idx, display.columns.get_loc(RESULT_COLUMN)] = val
    st.dataframe(display, use_container_width=True)

with tabs[1]:
    rows = []
    for d in documents:
        for ln in d.lines:
            rows.append({
                "Source PDF": ln.source_file, "Page": ln.page_number,
                "DM#": d.debit_memo_number, "Inv Ref": d.invoice_reference,
                "UPC": ln.upc, "Description": ln.description,
                "Qty Rec": ln.qty_received, "Qty Inv": ln.qty_invoiced,
                "Short Qty": ln.short_quantity, "Unit": ln.unit,
                "Invoice Price": ln.invoice_price, "Net": ln.net_amount,
                "Taxes": ln.taxes, "Total": ln.total_amount,
                "OCR": ln.ocr_used, "Warnings": "; ".join(ln.warnings),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

with tabs[2]:
    st.dataframe(pd.DataFrame(result.matched_lines), use_container_width=True)

with tabs[3]:
    st.dataframe(pd.DataFrame(result.invalid_lines), use_container_width=True)

with tabs[4]:
    rev = [{
        "Category": e.category, "Reason": e.reason, "Source PDF": e.source_file,
        "Page": e.page_number, "DM#": e.debit_memo_number,
        "Inv Ref": e.invoice_reference, "UPC": e.upc, "Detail": e.detail,
    } for e in result.exceptions]
    st.dataframe(pd.DataFrame(rev), use_container_width=True)

# ---------------------------------------------------------- Downloads
st.header("Downloads")
d1, d2 = st.columns(2)

with d1:
    if validation.critical_errors:
        st.button("Download Updated Short Paid List", disabled=True)
    else:
        updated = excel_exporter.write_results(sp_bytes, processed["sp_sheet"], row_cells)
        st.download_button(
            "Download Updated Short Paid List",
            data=updated,
            file_name=f"updated_{sp_file.name.rsplit('.', 1)[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with d2:
    exc_bytes = excel_exporter.build_exceptions_workbook(result.exceptions)
    st.download_button(
        "Download Processing Exceptions",
        data=exc_bytes,
        file_name="processing_exceptions.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
