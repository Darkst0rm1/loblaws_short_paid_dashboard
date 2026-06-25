"""Whole-table UPC indexing and exact / leading-zero UPC matching."""
import pandas as pd

from src import material_matcher as mm


def _material_df():
    # Columns deliberately mixed: the UPC may sit in any column.
    return pd.DataFrame(
        {
            "Customer Name": ["LCL", "LCL", "LCL"],
            "Material": [10057258, 20000001, 30000003],
            "Material Description": ["KIKKOMAN SOY", "WIDGET", "GADGET"],
            "Unit UPC Code": ["111111111111", "222222222222", "333333333333"],
            "Case UPC Code": ["10041390000956", "10041390000999", "444444444444"],
        }
    )


def test_normalize_upc_strips_formatting_and_trailing_zero():
    assert mm.normalize_upc(" 10041390000956 ") == "10041390000956"
    assert mm.normalize_upc("100-413,900'00956") == "10041390000956"
    assert mm.normalize_upc("41390000956.0") == "41390000956"


# (5) UPC found anywhere in the selected material table.
def test_upc_found_returns_material():
    idx = mm.build_index(_material_df())
    status, material, _ = idx.lookup("111111111111")
    assert status == mm.UPC_FOUND and material == "10057258"


# (6) UPC found OUTSIDE the Unit UPC column (here in Case UPC Code).
def test_upc_found_outside_unit_upc_column():
    idx = mm.build_index(_material_df())
    status, material, _ = idx.lookup("10041390000956")
    assert status == mm.UPC_FOUND and material == "10057258"


# (7) UPC not found.
def test_upc_not_found():
    idx = mm.build_index(_material_df())
    status, material, _ = idx.lookup("999999999999")
    assert status == mm.UPC_NOT_FOUND and material is None


# (8) UPC matching multiple material rows (different materials).
def test_upc_matches_multiple_materials():
    df = _material_df()
    df.loc[len(df)] = ["LCL", 40000004, "DUP", "111111111111", "x"]  # reuse a UPC
    idx = mm.build_index(df)
    status, material, candidates = idx.lookup("111111111111")
    assert status == mm.UPC_MULTIPLE and material is None
    assert set(candidates) == {"10057258", "40000004"}


def test_same_material_repeated_is_not_ambiguous():
    df = _material_df()
    df.loc[len(df)] = ["LCL", 10057258, "again", "111111111111", "x"]  # same material
    idx = mm.build_index(df)
    status, material, _ = idx.lookup("111111111111")
    assert status == mm.UPC_FOUND and material == "10057258"


def test_leading_zero_fallback_only_when_unique():
    df = pd.DataFrame({"Material": [555], "Unit UPC Code": ["0012345"]})
    idx = mm.build_index(df)
    # No exact match for "12345", but a unique leading-zero match exists.
    status, material, _ = idx.lookup("12345")
    assert status == mm.UPC_FOUND and material == "555"
