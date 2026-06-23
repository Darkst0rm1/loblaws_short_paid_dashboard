from decimal import Decimal

import pandas as pd

from src.material_matcher import build_upc_index
from src.models import DebitMemoDocument, DebitMemoLine
from src.short_paid_processor import assemble_cells, process

SP_COLUMNS = {
    "invoice_reference": "Invoice Reference",
    "reference": "Reference",
    "item_text": "Item Text",
    "full_debit_description": "FULL DEBIT DESCRIPTION",
}


def _sp_df():
    return pd.DataFrame({
        "Invoice Reference": ["INV-100", "INV-200"],
        "Reference": ["", ""],
        "Item Text": ["", ""],
        "FULL DEBIT DESCRIPTION": ["", ""],
    })


def _index():
    df = pd.DataFrame({
        "Material": ["10057258", "10058421"],
        "Unit UPC Code": ["10724923451967", "20011112222"],
        "Case UPC Code": ["", ""],
        "Material Description": ["A", "B"],
    })
    return build_upc_index(df, "Unit UPC Code", "Case UPC Code", "Material", "Material Description")


def _line(upc, qrec, qinv, net):
    return DebitMemoLine(
        source_file="dm.pdf", page_number=1, upc=upc,
        qty_received=Decimal(str(qrec)), qty_invoiced=Decimal(str(qinv)),
        short_quantity=Decimal(str(qinv)) - Decimal(str(qrec)),
        net_amount=Decimal(str(net)), status="ok",
    )


def test_valid_and_invalid_lines_in_one_cell():
    doc = DebitMemoDocument(source_file="dm.pdf", invoice_reference="INV-100")
    doc.lines = [
        _line("10724923451967", 0, 7, "173.74"),     # valid -> material 10057258
        _line("99999999999", 0, 23, "567.64"),       # invalid -> not in LCL
    ]
    res = process(_sp_df(), [doc], _index(), SP_COLUMNS)
    cells = assemble_cells(res.row_results)
    assert 0 in cells
    text = cells[0]
    assert "10057258, 7, 173.74" in text
    assert "99999999999, 23, 567.64 - Material not valid in LCL" in text


def test_unlinked_debit_memo_not_written():
    doc = DebitMemoDocument(source_file="dm.pdf", invoice_reference="INV-DOES-NOT-EXIST")
    doc.lines = [_line("10724923451967", 0, 7, "173.74")]
    res = process(_sp_df(), [doc], _index(), SP_COLUMNS)
    assert res.row_results == {}
    assert any(e.category == "Unlinked Debit Memos" for e in res.exceptions)


def test_negative_short_quantity_flagged_not_written():
    doc = DebitMemoDocument(source_file="dm.pdf", invoice_reference="INV-100")
    doc.lines = [_line("10724923451967", 5, 3, "100.00")]  # negative short qty
    res = process(_sp_df(), [doc], _index(), SP_COLUMNS)
    assert res.row_results == {}
    assert any("Negative" in e.reason for e in res.exceptions)


def test_duplicate_line_deduplicated():
    doc1 = DebitMemoDocument(source_file="a.pdf", invoice_reference="INV-100", debit_memo_number="DM1")
    doc1.lines = [_line("10724923451967", 0, 7, "173.74")]
    doc2 = DebitMemoDocument(source_file="b.pdf", invoice_reference="INV-100", debit_memo_number="DM1")
    doc2.lines = [_line("10724923451967", 0, 7, "173.74")]  # identical
    res = process(_sp_df(), [doc1, doc2], _index(), SP_COLUMNS)
    cells = assemble_cells(res.row_results)
    assert cells[0].count("10057258, 7, 173.74") == 1


def test_same_upc_different_amounts_preserved():
    doc = DebitMemoDocument(source_file="dm.pdf", invoice_reference="INV-100", debit_memo_number="DM1")
    doc.lines = [
        _line("10724923451967", 0, 7, "173.74"),
        _line("10724923451967", 0, 3, "75.00"),
    ]
    res = process(_sp_df(), [doc], _index(), SP_COLUMNS)
    cells = assemble_cells(res.row_results)
    assert "10057258, 7, 173.74" in cells[0]
    assert "10057258, 3, 75.00" in cells[0]
