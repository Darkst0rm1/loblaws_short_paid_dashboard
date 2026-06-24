from decimal import Decimal

import pandas as pd

from src.material_matcher import (
    INVALID_MESSAGE,
    build_upc_index,
    format_amount,
    format_quantity,
    format_result_line,
    match_upc,
    normalize_upc,
)


# --- UPC normalization -------------------------------------------------------

def test_normalize_removes_trailing_dot_zero():
    assert normalize_upc("10724923451967.0") == "10724923451967"


def test_normalize_removes_spaces():
    assert normalize_upc("10 724923 451967") == "10724923451967"


def test_normalize_removes_hyphens():
    assert normalize_upc("107-24923-451967") == "10724923451967"


def test_normalize_preserves_leading_zeros():
    assert normalize_upc("001234567890") == "001234567890"


def test_normalize_no_scientific_notation_from_float():
    assert normalize_upc(1.0724923451967e13) == "10724923451967"


def test_normalize_returns_string():
    assert isinstance(normalize_upc(123456), str)


# --- Material matching -------------------------------------------------------

def _sample_df():
    return pd.DataFrame({
        "Material": ["10057258", "10058421", "20001111"],
        "Material Description": ["Widget A", "Widget B", "Widget C"],
        "Unit UPC Code": ["10724923451967", "0061010101010", "777"],
        "Case UPC Code": ["555", "0061010101011", "888"],
    })


def _index():
    return build_upc_index(_sample_df(), "Unit UPC Code", "Case UPC Code", "Material", "Material Description")


def test_exact_unit_upc_match():
    res = match_upc(_index(), "10724923451967")
    assert res.status == "matched"
    assert res.material_number == "10057258"
    assert res.match_source == "unit"


def test_exact_case_upc_match():
    res = match_upc(_index(), "555")
    assert res.status == "matched"
    assert res.material_number == "10057258"
    assert res.match_source == "case"


def test_unit_upc_priority_over_case():
    # 777 is a Unit UPC, 888 is its case; searching 777 must come from unit.
    res = match_upc(_index(), "777")
    assert res.match_source == "unit"


def test_upc_not_found():
    res = match_upc(_index(), "99999999")
    assert res.status == "not_found"
    assert res.material_number is None


def test_upc_mapped_to_multiple_materials_is_ambiguous():
    df = pd.DataFrame({
        "Material": ["111", "222"],
        "Unit UPC Code": ["123456", "123456"],
        "Case UPC Code": ["", ""],
        "Material Description": ["a", "b"],
    })
    idx = build_upc_index(df, "Unit UPC Code", "Case UPC Code", "Material", "Material Description")
    res = match_upc(idx, "123456")
    assert res.status == "ambiguous"
    assert sorted(res.candidates) == ["111", "222"]


def test_no_fuzzy_for_upc():
    # A near-miss UPC must NOT match.
    res = match_upc(_index(), "10724923451968")
    assert res.status == "not_found"


# --- Result formatting -------------------------------------------------------

def test_valid_material_output():
    assert format_result_line("10057258", Decimal("7"), Decimal("173.74"), valid=True) == "10057258, 7, 173.74"


def test_invalid_lcl_material_output():
    out = format_result_line("10724923451967", Decimal("7"), Decimal("173.74"), valid=False)
    assert out == "UPC# 10724923451967, 7, 173.74 - not valid on LCL material list"


def test_two_decimal_amount_formatting():
    assert format_amount(Decimal("173.7")) == "173.70"
    assert format_amount(Decimal("173")) == "173.00"


def test_whole_number_quantity_formatting():
    assert format_quantity(Decimal("7.0")) == "7"
    assert format_quantity(Decimal("7")) == "7"


def test_fractional_quantity_kept():
    assert format_quantity(Decimal("2.5")) == "2.5"


def test_exact_invalid_message():
    assert INVALID_MESSAGE == "not valid on LCL material list"
