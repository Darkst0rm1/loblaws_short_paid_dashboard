"""Loblaws Short Paid Dashboard — debit-memo processing (Streamlit app).

Upload the LCL Material List, the LCL Short-Paid List, and one or more debit
memo PDFs. Each memo is matched to a short-paid row by its Vendor Reference
Number, every item UPC is resolved to an LCL Material number, and a new
``Debit Memo Results`` sheet is written into a downloadable copy of the
short-paid workbook (originals untouched).
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from src import excel_exporter, file_validation, processor
from src import material_matcher as mm
from src import reference_matcher as rm
from src.models import STATUS_REVIEW
from src.pdf_parser import parse_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("loblaws_dashboard")

st.set_page_config(page_title="Loblaws Short Paid Dashboard", layout="wide")


@st.cache_data(show_spinner=False)
def _list_sheets(file_bytes: bytes):
    return file_validation.list_sheets(file_bytes)


@st.cache_data(show_spinner=False)
def _read_sheet(file_bytes: bytes, sheet_name: str):
    return file_validation.read_sheet(file_bytes, sheet_name)


st.title("Loblaws Short Paid Dashboard")
st.caption(
    "Match Loblaws debit memo PDFs to the short-paid list by Vendor Reference "
    "Number, resolve each item UPC to its LCL Material number, and export a new "
    "**Debit Memo Results** sheet."
)

# ----------------------------------------------------------------- 1. Uploads
st.header("1. Upload files")
c1, c2, c3 = st.columns(3)
with c1:
    lcl_file = st.file_uploader("LCL Material List (.xlsx / .xlsm)", type=["xlsx", "xlsm"], key="lcl")
with c2:
    sp_file = st.file_uploader("LCL Short-Paid List (.xlsx)", type=["xlsx"], key="sp")
with c3:
    pdf_files = st.file_uploader(
        "Debit Memo PDFs", type=["pdf"], accept_multiple_files=True, key="pdfs"
    )

all_present = bool(lcl_file and sp_file and pdf_files)
if not all_present:
    st.info("Upload all three inputs (Material List, Short-Paid List, and at least one PDF).")

# ------------------------------------------------------- 2. Sheet selection
lcl_sheet = sp_sheet = None
lcl_bytes = sp_bytes = None
if all_present:
    lcl_bytes = lcl_file.getvalue()
    sp_bytes = sp_file.getvalue()
    lcl_sheets = _list_sheets(lcl_bytes)
    sp_sheets = _list_sheets(sp_bytes)

    st.header("2. Select worksheets")
    s1, s2 = st.columns(2)
    with s1:
        default_lcl = file_validation.choose_material_sheet(lcl_bytes, lcl_sheets)
        lcl_sheet = st.selectbox(
            "Material-list sheet", lcl_sheets,
            index=lcl_sheets.index(default_lcl) if default_lcl in lcl_sheets else 0,
        )
    with s2:
        default_sp = file_validation.choose_short_paid_sheet(sp_bytes, sp_sheets)
        sp_sheet = st.selectbox(
            "Short-paid source sheet", sp_sheets,
            index=sp_sheets.index(default_sp) if default_sp in sp_sheets else 0,
        )

# ------------------------------------------------------------- 3. Process
process_clicked = st.button("Process debit memos", type="primary", disabled=not all_present)

if process_clicked and all_present:
    sp_df = _read_sheet(sp_bytes, sp_sheet)
    ref_column = rm.detect_reference_column(sp_df.columns)
    if ref_column is None:
        st.error(
            "No reference column found in the selected short-paid sheet "
            f"(looked for: {', '.join(rm.REFERENCE_COLUMN_ALIASES)})."
        )
        st.stop()

    lcl_df = _read_sheet(lcl_bytes, lcl_sheet)
    if mm.MATERIAL_COLUMN not in lcl_df.columns:
        st.error(f"The selected material sheet has no '{mm.MATERIAL_COLUMN}' column.")
        st.stop()

    with st.spinner("Building material index and parsing PDFs..."):
        index = mm.build_index(lcl_df)
        memos = []
        for f in pdf_files:
            try:
                memos.append(parse_pdf(f.getvalue(), f.name))
            except Exception as exc:  # defensive — parse_pdf shouldn't raise
                logger.exception("parse_pdf raised for %s", f.name)
                from src.models import DebitMemo
                memos.append(DebitMemo(source_file=f.name, parse_error=f"Unexpected error: {exc}"))
        results, summary = processor.process(memos, sp_df, ref_column, index)
        workbook = excel_exporter.build_workbook(sp_bytes, results)

    st.session_state["run"] = {
        "results": results,
        "summary": summary,
        "ref_column": ref_column,
        "workbook": workbook,
        "sp_name": sp_file.name,
    }

run = st.session_state.get("run")
if not run:
    st.stop()

results = run["results"]
summary = run["summary"]

# --------------------------------------------------------------- 4. KPIs
st.header("3. Results")
st.caption(f"Reference column used: **{run['ref_column']}**")
k = st.columns(5)
k[0].metric("PDFs Uploaded", summary.pdfs_uploaded)
k[1].metric("Successfully Processed", summary.processed)
k[2].metric("Manual Review", summary.manual_review)
k[3].metric("Items Extracted", summary.items_extracted)
k[4].metric("UPCs Not Found", summary.upcs_not_found)

# ----------------------------------------------------------- Results table
table = pd.DataFrame(
    [
        {
            "File Name": r.source_file,
            "Vendor Reference Number": r.vendor_reference or "",
            "Debit Number": r.debit_number or "",
            "PO Number": r.po_number or "",
            "Material, Quantity # and Amount": r.cell_text,
            "Status": r.status,
            "Review Reason": r.review_reason,
        }
        for r in results
    ]
)

f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
with f1:
    status_filter = st.selectbox("Status", ["(all)", "Processed", STATUS_REVIEW])
with f2:
    ref_filter = st.text_input("Vendor Reference Number")
with f3:
    debit_filter = st.text_input("Debit Number")
with f4:
    search = st.text_input("Search UPC or material number")

view = table
if status_filter != "(all)":
    view = view[view["Status"] == status_filter]
if ref_filter.strip():
    view = view[view["Vendor Reference Number"].str.contains(ref_filter.strip(), case=False, na=False)]
if debit_filter.strip():
    view = view[view["Debit Number"].str.contains(debit_filter.strip(), case=False, na=False)]
if search.strip():
    view = view[view["Material, Quantity # and Amount"].str.contains(search.strip(), case=False, na=False)]

st.dataframe(view, use_container_width=True, hide_index=True)

# ------------------------------------------------------------- 5. Download
st.header("4. Download")
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
st.download_button(
    "Download results workbook",
    data=run["workbook"],
    file_name=f"Loblaws_short_paid_with_debit_memo_results_{stamp}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
