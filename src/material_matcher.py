"""Build a searchable index of the complete material table and look up UPCs.

The index is built ONCE per upload: it maps a normalized cell value to the set
of material numbers found in rows containing that value, scanning EVERY column
of the selected material table (not just Unit/Case UPC columns).

UPC normalization (both the memo UPC and every material-table cell):

* convert to text, trim outer spaces
* remove embedded spaces and formatting commas / hyphens / apostrophes
* drop a trailing ``.0`` left by Excel numeric conversion
* preserve all real digits

Matching is exact on the normalized digits. A leading-zero-insensitive
comparison is attempted only as a fallback and only when it yields exactly one
material. Substring / "contains" matching is never used.
"""
from __future__ import annotations

import re

import pandas as pd

MATERIAL_COLUMN = "Material"

# Lookup outcome statuses.
UPC_FOUND = "matched"
UPC_NOT_FOUND = "not_found"
UPC_MULTIPLE = "multiple"

_FORMATTING_CHARS = re.compile(r"[\s,\-']")


def normalize_upc(value) -> str:
    """Normalize a UPC/cell value to its bare digits-and-text comparison form."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Drop a trailing ".0" from Excel numeric conversion (e.g. 41390000956.0).
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    # Remove formatting-only characters; keep every real digit/letter.
    return _FORMATTING_CHARS.sub("", text)


def normalize_material(value) -> str:
    """Render a material number as clean text (drops a trailing ``.0``)."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


class MaterialIndex:
    """Normalized cell value -> set of material numbers it appears with."""

    def __init__(self, exact: dict[str, set[str]], no_zeros: dict[str, set[str]], rows: int):
        self._exact = exact
        self._no_zeros = no_zeros
        self.row_count = rows

    def lookup(self, upc):
        """Return ``(status, material_or_None, candidates)`` for a UPC.

        * exactly one material            -> ``(UPC_FOUND, material, [material])``
        * none (and no single-zero match) -> ``(UPC_NOT_FOUND, None, [])``
        * more than one material          -> ``(UPC_MULTIPLE, None, sorted_list)``
        """
        key = normalize_upc(upc)
        if not key:
            return UPC_NOT_FOUND, None, []

        exact = self._exact.get(key, set())
        if len(exact) == 1:
            return UPC_FOUND, next(iter(exact)), list(exact)
        if len(exact) > 1:
            return UPC_MULTIPLE, None, sorted(exact)

        # Fallback: leading-zero-insensitive, but only if it resolves uniquely.
        relaxed = self._no_zeros.get(key.lstrip("0"), set())
        if len(relaxed) == 1:
            return UPC_FOUND, next(iter(relaxed)), list(relaxed)
        return UPC_NOT_FOUND, None, []


def build_index(df: pd.DataFrame, material_column: str = MATERIAL_COLUMN) -> MaterialIndex:
    """Build a :class:`MaterialIndex` from the entire selected material table."""
    if material_column not in df.columns:
        raise KeyError(f"Material column '{material_column}' not found in the selected sheet")

    exact: dict[str, set[str]] = {}
    no_zeros: dict[str, set[str]] = {}
    columns = list(df.columns)
    mat_pos = columns.index(material_column)

    for row in df.itertuples(index=False, name=None):
        material = normalize_material(row[mat_pos])
        if not material:
            continue  # nothing to return for this row
        for value in row:
            key = normalize_upc(value)
            if not key:
                continue
            exact.setdefault(key, set()).add(material)
            no_zeros.setdefault(key.lstrip("0"), set()).add(material)

    return MaterialIndex(exact, no_zeros, len(df))
