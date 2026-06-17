# tests/test_preprocess_lod2_heights.py
import pandas as pd
from shapely.geometry import box
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "pp_lod2", pathlib.Path("scripts/preprocess_lod2_heights.py"))
pp = importlib.util.module_from_spec(spec); spec.loader.exec_module(pp)


def test_build_heights_parquet(tmp_path):
    index = {"features": [
        {"properties": {"tile_id": "T1", "shp": "http://x/T1"}, "geometry": box(0, 0, 1, 1).__geo_interface__},
        {"properties": {"tile_id": "T2", "shp": "http://x/T2"}, "geometry": box(9, 9, 10, 10).__geo_interface__},
    ]}
    def fake_dl(url, dest): open(dest, "w").write("z")
    def fake_reader(zip_path):  # returns a DBF-like attribute frame per tile
        return pd.DataFrame({"gml_id": ["DENILDa", "DENILDa", "DENILDb"],
                             "externRef": ["q$$$DENIALa", "q$$$DENIALa", "q$$$DENIALb"],
                             "measHeight": [9.0, 9.0, 3.0], "roofType": ["1000"] * 3})
    out = str(tmp_path / "lod2_heights.parquet")
    meta = pp.build_heights_parquet(index, box(0.5, 0.5, 2, 2), str(tmp_path / "cache"), out,
                                    downloader=fake_dl, reader=fake_reader, max_workers=1)
    df = pd.read_parquet(out)
    assert meta["n_tiles"] == 1 and meta["n_failed"] == 0       # only T1 intersects
    assert set(df["OI"]) == {"DENIALa", "DENIALb"}
    assert list(df.columns) == ["OI", "height_m", "roofType"]
