"""Download + parse LGLN LoD2 3D-Shape tiles for the ZGB into an OI->height parquet.

One-off preprocessing (like preprocess_alkis_landuse.py). Robust bulk download
(resume/retry/parallel), tile-by-tile parse (no memory blowup). The 3D-Shape DBF
carries measHeight + externRef(OI); we never read its (3D, WKB-type-16) geometry.
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
import pandas as pd, geopandas as gpd
from shapely.geometry import box
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from braunschweig.data import lod2_heights as L

INDEX_URL = ("https://arcgis-geojson.s3.eu-de.cloud-object-storage.appdomain.cloud/"
             "lod2/lgln-opengeodata-lod2.geojson")


def _pyogrio_reader(zip_path: str) -> pd.DataFrame:
    import pyogrio, zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    shps = [n for n in names if n.endswith(".shp")]
    if not shps:
        raise ValueError(f"no .shp in {zip_path}")
    return pyogrio.read_dataframe(f"/vsizip/{zip_path}/{shps[0]}",
                                  columns=["gml_id", "externRef", "measHeight", "roofType"],
                                  read_geometry=False)


def build_heights_parquet(index, region_4326, cache_dir, out_path, *,
                          downloader=urllib.request.urlretrieve, reader=_pyogrio_reader,
                          max_workers=8):
    tiles = L.tiles_for_region(index, region_4326)
    ok, failed = L.download_tiles(tiles, cache_dir, downloader=downloader, max_workers=max_workers)
    parts = []
    for zip_path in ok:
        try:
            parts.append(L.extract_heights(reader(zip_path)))
        except Exception as exc:  # a corrupt tile must not kill the run
            print(f"  ! parse failed {zip_path}: {exc}", flush=True)
    heights = (pd.concat(parts, ignore_index=True).drop_duplicates("OI")
               if parts else pd.DataFrame(columns=["OI", "height_m", "roofType"]))
    heights.to_parquet(out_path, index=False)
    meta = {"n_tiles": len(tiles), "n_failed": len(failed), "n_buildings": int(len(heights))}
    with open(os.path.splitext(out_path)[0] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({**meta, "failed_tiles": failed[:50]}, fh, indent=2)
    return meta


def _region_from_alkis(alkis_parquet):
    # Scope = the (already-ZGB-clipped) ALKIS footprint extent; bbox is slightly
    # over-inclusive at corners — harmless, those tiles join to no footprints.
    g = gpd.read_parquet(alkis_parquet)
    return gpd.GeoSeries([box(*g.total_bounds)], crs="EPSG:25832").to_crs("EPSG:4326").iloc[0]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index-url", default=INDEX_URL)
    p.add_argument("--alkis", default="eqasim-data/data/braunschweig/preprocessed/alkis_buildings.parquet",
                   help="ALKIS parquet whose extent defines the ZGB region to fetch")
    p.add_argument("--cache-dir", default="eqasim-data/data/braunschweig/lod2/tiles")
    p.add_argument("--out", default="eqasim-data/data/braunschweig/preprocessed/lod2_heights.parquet")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args(argv)
    import logging; logging.basicConfig(level=logging.INFO)
    idx_local = a.index_url
    if idx_local.startswith("http"):
        dst = os.path.join(a.cache_dir, "_index.geojson"); os.makedirs(a.cache_dir, exist_ok=True)
        urllib.request.urlretrieve(a.index_url, dst); idx_local = dst
    with open(idx_local, encoding="utf-8") as fh:
        index = json.load(fh)
    region = _region_from_alkis(a.alkis)
    meta = build_heights_parquet(index, region, a.cache_dir, a.out, max_workers=a.workers)
    print(f"LoD2 heights: {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
