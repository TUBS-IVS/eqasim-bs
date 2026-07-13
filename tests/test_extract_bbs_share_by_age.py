# tests/test_extract_bbs_share_by_age.py
"""Tests for scripts/extract_bbs_share_by_age.py (issue #139).

Covers the SpreadsheetML parsing (numerator = BBS 16-20 total minus dual-system
Berufsschule Teilzeit; single-year Oberstufe extraction), the pure share
construction (flat BBS/4), and that the committed CSV activates the primary
(age-resolved) path of the bbs_share loader.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extract_bbs_share_by_age import (
    build_rows,
    extract_beruflich_numerator,
    extract_oberstufe_by_age,
)
from braunschweig.data.schools.bbs_share import load_bbs_share_by_age

_REPO = Path(__file__).resolve().parents[1]
_COMMITTED_CSV = _REPO / "eqasim-data" / "data" / "braunschweig" / "schools" / "nds_bbs_share_by_age.csv"

# Minimal SpreadsheetML mimicking the LSN export layout. Columns of a Schulform /
# age row: [label, Insges, "unter 16", "16 - 20", "20 - 25", "25+"].
_HEAD = (
    '<?xml version="1.0"?>\n'
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
    'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n<Worksheet ss:Name="s"><Table>\n'
)
_TAIL = "</Table></Worksheet></Workbook>\n"


def _row(*cells):
    out = ["<Row>"]
    for c in cells:
        if c is None:
            continue
        typ = "Number" if isinstance(c, (int, float)) else "String"
        out.append(f'<Cell><Data ss:Type="{typ}">{c}</Data></Cell>')
    out.append("</Row>\n")
    return "".join(out)


def _beruflich_xml():
    return _HEAD + (
        _row("0       Niedersachsen")
        + _row("Schulformen insgesamt", 1000, 50, 400, 300, 250)
        + _row("  Berufsschule", 200, 10, 150, 30, 10)
        + _row("    Berufsschule (Teilzeit)", 160, 5, 120, 25, 10)
        + _row("  Berufsfachschule", 300, 20, 180, 80, 20)
    ) + _TAIL


def _allgemein_xml():
    # Ages 16..19 -> declining Oberstufe "insgesamt" (first numeric cell).
    return _HEAD + (
        _row("0       Niedersachsen")
        + _row("Altersjahre insgesamt", 90000)
        + _row("15 - 16", 20000, 1, 2)
        + _row("16 - 17", 16000, 1, 2)
        + _row("17 - 18", 12000, 1, 2)
        + _row("18 - 19", 8000, 1, 2)
        + _row("19 - 20", 3000, 1, 2)
    ) + _TAIL


def test_beruflich_numerator_subtracts_dual_teilzeit(tmp_path):
    p = tmp_path / "beruflich.xml"
    p.write_text(_beruflich_xml(), encoding="utf-8")
    total, teilzeit, numerator = extract_beruflich_numerator(str(p))
    assert total == 400          # "16 - 20" group of Schulformen insgesamt
    assert teilzeit == 120       # "16 - 20" group of Berufsschule (Teilzeit)
    assert numerator == 280      # full-time vocational = total - dual teilzeit


def test_oberstufe_by_age_reads_single_years_insgesamt(tmp_path):
    p = tmp_path / "allgemein.xml"
    p.write_text(_allgemein_xml(), encoding="utf-8")
    ob = extract_oberstufe_by_age(str(p))
    assert ob == {16: 16000, 17: 12000, 18: 8000, 19: 3000}


def test_parser_picks_first_niedersachsen_occurrence_not_subregion(tmp_path):
    # The Niedersachsen state block is the FIRST region; later sub-region rows with
    # the same labels must be ignored (first occurrence wins).
    xml = _HEAD + (
        _row("0       Niedersachsen")
        + _row("Schulformen insgesamt", 1000, 50, 400, 300, 250)
        + _row("    Berufsschule (Teilzeit)", 160, 5, 120, 25, 10)
        + _row("1       Statistische Region Braunschweig")
        + _row("Schulformen insgesamt", 100, 5, 40, 30, 25)          # sub-region, must be ignored
        + _row("    Berufsschule (Teilzeit)", 16, 1, 12, 2, 1)
    ) + _TAIL
    p = tmp_path / "multi.xml"
    p.write_text(xml, encoding="utf-8")
    total, teilzeit, numerator = extract_beruflich_numerator(str(p))
    assert (total, teilzeit, numerator) == (400, 120, 280)  # Niedersachsen, not the region


def test_build_rows_flat_bbs_and_rising_share():
    ob = {16: 16000, 17: 12000, 18: 8000, 19: 3000}
    rows = build_rows(280 * 4, ob)  # numerator so bbs/yr = 280
    ages = [r[0] for r in rows]
    bbs = [r[1] for r in rows]
    shares = [r[3] for r in rows]
    assert ages == [16, 17, 18, 19]
    assert bbs == [280.0, 280.0, 280.0, 280.0]          # flat BBS/4
    assert shares == sorted(shares)                      # rising with declining Oberstufe
    assert shares[0] == pytest.approx(280 / (280 + 16000))
    assert shares[-1] == pytest.approx(280 / (280 + 3000))


def test_build_rows_rejects_nonpositive_oberstufe():
    with pytest.raises(ValueError, match="non-positive Oberstufe"):
        build_rows(1000, {16: 100, 17: 100, 18: 100, 19: 0})


def test_committed_csv_activates_primary_path():
    """The committed reference CSV must load (primary path taken) with monotonic shares."""
    assert _COMMITTED_CSV.exists(), f"committed CSV missing at {_COMMITTED_CSV}"
    shares = load_bbs_share_by_age(str(_COMMITTED_CSV))
    assert shares is not None, "loader returned None -> primary path NOT taken"
    assert sorted(shares) == [16, 17, 18, 19]
    vals = [shares[a] for a in (16, 17, 18, 19)]
    assert vals == sorted(vals), f"shares not rising with age: {vals}"
    # All well below the old scalar 0.681 (the scalar over-allocated to BBS).
    assert max(vals) < 0.681
