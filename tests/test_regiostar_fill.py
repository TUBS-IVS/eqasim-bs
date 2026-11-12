from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.bbsr import regiostar  # noqa: E402
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


class _ValidateContext:
    """Declared-options-only config, like synpp's ValidateContext (``config(key)`` takes the key alone)."""

    def __init__(self, data_path):
        self._values = {"data_path": str(data_path), "regiostar.path": "regiostar/workbook.xlsx"}

    def config(self, key):
        return self._values[key]


def _validate_inputs(data_path):
    workbook = data_path / "regiostar" / "workbook.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"workbook-bytes")
    vg250 = data_path / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    vg250.parent.mkdir(parents=True)
    vg250.write_bytes(b"vg250-archive-A")
    return vg250


def test_validate_token_follows_the_vg250_archive_content_not_only_the_workbook(tmp_path):
    # execute() reads the VG250 archive for the Gemeinde universe and centroids; a replaced archive of
    # the same size must not serve the cached table (audit C3).
    vg250 = _validate_inputs(tmp_path)
    context = _ValidateContext(tmp_path)
    before = regiostar.validate(context)
    vg250.write_bytes(b"vg250-archive-B")          # same length, other content
    after = regiostar.validate(context)
    assert after["vg250_sha256"] != before["vg250_sha256"]
    assert after["workbook_size"] == before["workbook_size"] == len(b"workbook-bytes")


def test_validate_raises_naming_the_path_when_the_vg250_archive_is_missing(tmp_path):
    vg250 = _validate_inputs(tmp_path)
    vg250.unlink()
    with pytest.raises(RuntimeError, match="vg250-ew_12-31"):
        regiostar.validate(_ValidateContext(tmp_path))


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
