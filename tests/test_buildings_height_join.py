# tests/test_buildings_height_join.py
import re

import pytest

import geopandas as gpd, pandas as pd
from shapely.geometry import Point, Polygon
from braunschweig.data import buildings as B


def _b():
    return gpd.GeoDataFrame(
        {"building_id": [0, 1, 2], "area_m2": [80.0, 1000.0, 50.0],
         "OI": ["DENIALa", "DENIALb", "DENIALc"], "commune_id": ["x"] * 3},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)], crs="EPSG:25832")


def test_join_is_non_destructive():
    heights = pd.DataFrame({"OI": ["DENIALb", "DENIALc"], "height_m": [21.0, 6.0], "roofType": ["1000", "1000"]})
    out = B.join_lod2_heights(_b(), heights)
    assert len(out) == 3                                   # no footprint dropped
    assert {"building_id", "area_m2", "OI", "commune_id", "geometry", "height_m"} <= set(out.columns)
    h = out.set_index("OI")["height_m"]
    assert h["DENIALb"] == 21.0 and h["DENIALc"] == 6.0
    assert pd.isna(h["DENIALa"])                           # unmatched -> NaN, kept


# --- audit C3: the heights file is an input of the stage (validate token) and its absence is loud ---

class _Ctx:
    """Declared-options-only config (``config(key)`` takes the key alone) and canned upstream stages."""

    def __init__(self, data_path, stages=None):
        self._data_path = str(data_path)
        self._stages = stages or {}

    def config(self, key):
        assert key == "data_path"
        return self._data_path

    def stage(self, name):
        return self._stages[name]


def _write_heights(data_path, heights_m):
    path = data_path / "braunschweig" / "preprocessed" / "lod2_heights.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"OI": ["DENIALa", "DENIALb"], "height_m": heights_m}).to_parquet(path)
    return path


def test_validate_token_follows_the_heights_file_and_marks_its_absence(tmp_path):
    ctx = _Ctx(tmp_path)
    absent = B.validate(ctx)
    path = _write_heights(tmp_path, [10.0, 20.0])
    first = B.validate(ctx)
    assert B.validate(ctx) == first                        # stable for unchanged bytes
    path.unlink()
    _write_heights(tmp_path, [10.0, 25.0])
    changed = B.validate(ctx)
    assert len({absent, first, changed}) == 3              # absent / content A / content B


def _height_warnings(printed, tmp_path):
    """Warning lines about height_m, with the (digit-bearing) temp path removed so a count can be matched."""
    return [line.replace(str(tmp_path), "<path>") for line in printed.splitlines()
            if "WARNING" in line and "height_m" in line]


def _execute_inputs():
    """Two residential footprints and one garage (dropped by the activity filter) inside one zone."""
    square = lambda x, size: Polygon([(x, 0), (x + size, 0), (x + size, size), (x, size)])
    alkis = gpd.GeoDataFrame(
        {"AGS": ["03101000"] * 3, "activity": ["residential", "residential", "ancillary"],
         "area_m2": [100.0, 400.0, 20.0], "OI": ["DENIALa", "DENIALb", "DENIALc"]},
        geometry=[square(0, 10), square(100, 20), square(200, 4.5)], crs="EPSG:25832")
    zones = gpd.GeoDataFrame({"commune_id": ["031010000000"], "iris_id": ["0310100000000000"]},
                             geometry=[Polygon([(-10, -10), (400, -10), (400, 100), (-10, 100)])], crs="EPSG:25832")
    return {"braunschweig.data.alkis": alkis, "eqasim_common.data.spatial.iris": zones}


def test_execute_joins_heights_when_the_file_exists_and_warns_loudly_when_it_does_not(tmp_path, capsys):
    stages = _execute_inputs()
    missing = B.execute(_Ctx(tmp_path, stages))            # no heights file yet
    assert missing["height_m"].isna().all() and len(missing) == 2
    warnings = _height_warnings(capsys.readouterr().out, tmp_path)
    assert len(warnings) == 1 and re.search(r"\b2\b", warnings[0])   # one warning, naming the footprint count

    _write_heights(tmp_path, [10.0, 20.0])                 # primary path: joined, nothing to warn about
    joined = B.execute(_Ctx(tmp_path, stages))
    assert not _height_warnings(capsys.readouterr().out, tmp_path)
    assert joined.set_index("OI")["height_m"].to_dict() == {"DENIALa": 10.0, "DENIALb": 20.0}


@pytest.mark.parametrize("drop_oi", [True, False], ids=["no_oi_column", "no_oi_matches"])
def test_join_warns_with_the_footprint_count_when_no_footprint_gets_a_height(capsys, tmp_path, drop_oi):
    """Both 100 % fallbacks of the join warn: footprints without an OI column, and OI keys that
    match no LoD2 row (a key format mismatch would otherwise log a plain 0.0 % coverage line)."""
    buildings = _b().drop(columns=["OI"]) if drop_oi else _b()
    out = B.join_lod2_heights(buildings, pd.DataFrame({"OI": ["no-such-oi"], "height_m": [1.0]}))
    assert out["height_m"].isna().all()
    warnings = _height_warnings(capsys.readouterr().out, tmp_path)
    assert len(warnings) == 1 and re.search(r"\b3\b", warnings[0])
