from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.bbsr.regiostar import fill_missing_rs7_nearest_neighbour  # noqa: E402


def test_missing_gemeinde_gets_nearest_neighbour_rs7():
    known = pd.DataFrame({
        "commune_id": ["03153017", "03153016"],
        "ars5": ["03153", "03153"],
        "name": ["Goslar", "Braunlage"],
        "regiostar7": [75, 77],
        "x": [0.0, 100.0],
        "y": [0.0, 0.0],
    })
    expected = pd.DataFrame({
        "commune_id": ["03153017", "03153016", "03153019"],
        "x": [0.0, 100.0, 95.0],
        "y": [0.0, 0.0, 0.0],
    })
    out = fill_missing_rs7_nearest_neighbour(known, expected)
    row = out[out["commune_id"] == "03153019"].iloc[0]
    assert int(row["regiostar7"]) == 77
    assert bool(row["rs7_filled"]) is True
    assert int(out[out["commune_id"] == "03153017"].iloc[0]["regiostar7"]) == 75
    assert bool(out[out["commune_id"] == "03153017"].iloc[0]["rs7_filled"]) is False


def test_ars_to_ags8_drops_verbandsgemeinde_block():
    from braunschweig.data.bbsr.regiostar import ars_to_ags8
    # 12-digit ARS = Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3); AGS8 = ARS[0:5]+ARS[9:12]
    assert ars_to_ags8("031010000000") == "03101000"
    assert ars_to_ags8("031530000019") == "03153019"
    assert ars_to_ags8("03101000") == "03101000"   # idempotent on 8-digit


# --- Fallback transparency (CLAUDE.md): direct match vs nearest-neighbour ----


def _known_frame():
    return pd.DataFrame({
        "commune_id": ["03153017", "03153016"],
        "ars5": ["03153", "03153"],
        "name": ["Goslar", "Braunlage"],
        "regiostar7": [75, 77],
        "x": [0.0, 100.0],
        "y": [0.0, 0.0],
    })


def test_fill_all_match_reports_zero_fallback(capsys):
    """PRIMARY path: every expected Gemeinde matches the reference -> fill 0."""
    known = _known_frame()
    expected = pd.DataFrame({
        "commune_id": ["03153017", "03153016"],
        "x": [0.0, 100.0],
        "y": [0.0, 0.0],
    })
    out = fill_missing_rs7_nearest_neighbour(known, expected)

    assert int(out["rs7_filled"].sum()) == 0
    assert bool((~out["rs7_filled"]).all())

    log = capsys.readouterr().out
    assert "primary (direct match) 2/2 (100.0%)" in log
    assert "fallback (nearest-neighbour) 0/2 (0.0%)" in log
    assert "WARNING" not in log


def test_fill_missing_gemeinde_is_counted_as_fallback(capsys):
    """FALLBACK path: a missing Gemeinde is nearest-neighbour filled + counted.

    1 of 3 Gemeinden is filled (33.3%), above the 10% threshold, so a WARNING
    is emitted.
    """
    known = _known_frame()
    expected = pd.DataFrame({
        "commune_id": ["03153017", "03153016", "03153019"],
        "x": [0.0, 100.0, 95.0],
        "y": [0.0, 0.0, 0.0],
    })
    out = fill_missing_rs7_nearest_neighbour(known, expected)

    filled = out[out["rs7_filled"]]
    assert int(out["rs7_filled"].sum()) == 1
    assert filled.iloc[0]["commune_id"] == "03153019"

    log = capsys.readouterr().out
    assert "primary (direct match) 2/3 (66.7%)" in log
    assert "fallback (nearest-neighbour) 1/3 (33.3%)" in log
    assert "WARNING" in log
