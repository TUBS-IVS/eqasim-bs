import pandas as pd
from shapely.geometry import box, shape
from braunschweig.data import lod2_heights as L


def test_extract_heights_oi_dedup():
    # DBF rows: one building has 3 surface rows (same gml_id + measHeight), one has 1.
    df = pd.DataFrame({
        "gml_id": ["DENILD0001", "DENILD0001", "DENILD0001", "DENILD0002"],
        "externRef": ["x$$$DENIAL0001", "x$$$DENIAL0001", "x$$$DENIAL0001", "y$$$DENIAL0002"],
        "measHeight": [7.5, 7.5, 7.5, 3.0],
        "roofType": ["1000", "1000", "1000", "1000"],
    })
    out = L.extract_heights(df)
    assert list(out.columns) == ["OI", "height_m", "roofType"]
    assert len(out) == 2                       # deduped by gml_id
    assert set(out["OI"]) == {"DENIAL0001", "DENIAL0002"}
    assert out.set_index("OI").loc["DENIAL0001", "height_m"] == 7.5


def test_tiles_for_region_intersect():
    index = {"features": [
        {"properties": {"tile_id": "A", "shp": "u1"}, "geometry": box(0, 0, 1, 1).__geo_interface__},
        {"properties": {"tile_id": "B", "shp": "u2"}, "geometry": box(10, 10, 11, 11).__geo_interface__},
    ]}
    hit = L.tiles_for_region(index, box(0.5, 0.5, 5, 5))
    assert [t["tile_id"] for t in hit] == ["A"]


def test_download_tiles_skips_existing_and_retries(tmp_path):
    calls = {"n": 0}
    def fake_dl(url, dest):
        calls["n"] += 1
        if "FAILONCE" in url and calls["n"] == 1:
            raise IOError("transient")
        open(dest, "w").write("x")
    # pre-create one tile so it is skipped
    (tmp_path / "A.zip").write_text("already")
    tiles = [{"tile_id": "A", "shp": "http://x/A"},
             {"tile_id": "B", "shp": "http://x/B"},
             {"tile_id": "C", "shp": "http://x/FAILONCE"}]
    ok, failed = L.download_tiles(tiles, str(tmp_path), downloader=fake_dl, max_workers=1, retries=2)
    assert (tmp_path / "A.zip").read_text() == "already"     # not re-downloaded
    assert sorted(p.split("/")[-1] for p in ok) == ["A.zip", "B.zip", "C.zip"]
    assert failed == []                                       # C succeeded on retry
