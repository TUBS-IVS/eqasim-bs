"""Synthetic mini-fixtures for the VerBindungen data layer tests.

Geometry layout (EPSG:4326 in the shapefile, as upstream; the loader
reprojects to EPSG:25832). Four cells on a 2x2 unit-ish grid near 10.5E/52.2N:

    stadtteil-1  (03101000)          | vg250-3 (03151001,03151002)
    stadtteil-2  (03101000)          | vg250-9 (09999999)   <- out of scope

Municipalities (EPSG:25832): three in-scope communes; commune 031510029999
("renamed" since 2019 -- its AGS 03151002 is deliberately ABSENT from the
municipalities frame under that key) exercises the geometric fallback of the
AGS->commune mapping.
"""
from __future__ import annotations

import os
import zipfile

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

# WGS84 boxes (lon/lat) -- roughly 0.02 deg tall/wide, non-overlapping.
_CELLS_4326 = [
    ("stadtteil-1", "03101000", 10.50, 52.20),
    ("stadtteil-2", "03101000", 10.50, 52.22),
    ("vg250-3", "03151001,03151002", 10.53, 52.20),
    ("vg250-9", "09999999", 10.53, 52.26),
]
_W, _H = 0.02, 0.02


def write_cells_shapefile_zip(dirpath) -> str:
    """Write the 4-cell shapefile as a zip; return the zip path."""
    dirpath = str(dirpath)
    shp_dir = os.path.join(dirpath, "shp")
    os.makedirs(shp_dir, exist_ok=True)
    gdf = gpd.GeoDataFrame(
        {
            "ags_0": [ags for _, ags, _, _ in _CELLS_4326],
            "ewz": [None, None, 5000.0, 7000.0],
            "zell_id": [cid for cid, _, _, _ in _CELLS_4326],
            "rs7": [72, 72, 74, 77],
        },
        geometry=[box(x, y, x + _W, y + _H) for _, _, x, y in _CELLS_4326],
        crs="EPSG:4326",
    )
    shp_path = os.path.join(shp_dir, "verbindungen-verkehrszellen.shp")
    gdf.to_file(shp_path)
    zip_path = os.path.join(dirpath, "Shapefiles_VerBindungen_Zellen.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in os.listdir(shp_dir):
            if name.startswith("verbindungen-verkehrszellen."):
                zf.write(os.path.join(shp_dir, name), arcname=name)
    return zip_path


def make_municipalities_gdf() -> gpd.GeoDataFrame:
    """In-scope municipalities in EPSG:25832 covering the fixture cells.

    031010001000 covers BOTH stadtteil cells (parent city). 031510000001
    matches AGS 03151001 directly (ars_to_ags8('031510000001') =
    '03151' + '001' = '03151001'). 031510029999 has an ARS whose AGS-8
    (ars_to_ags8('031510029999') = '03151' + '999' = '03151999') does NOT
    equal 03151002, so AGS 03151002 is unmatched by the dict and must be
    recovered geometrically (its centroid lies in vg250-3).
    """
    cells = gpd.GeoDataFrame(
        {"zell_id": [c for c, _, _, _ in _CELLS_4326]},
        geometry=[box(x, y, x + _W, y + _H) for _, _, x, y in _CELLS_4326],
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")
    g = dict(zip(cells["zell_id"], cells.geometry))
    return gpd.GeoDataFrame(
        {
            "commune_id": ["031010001000", "031510000001", "031510029999"],
        },
        geometry=[
            g["stadtteil-1"].union(g["stadtteil-2"]),
            # split vg250-3 into west/east halves for the two communes
            _west_half(g["vg250-3"]),
            _east_half(g["vg250-3"]),
        ],
        crs="EPSG:25832",
    )


def _west_half(geom):
    minx, miny, maxx, maxy = geom.bounds
    return box(minx, miny, (minx + maxx) / 2.0, maxy)


def _east_half(geom):
    minx, miny, maxx, maxy = geom.bounds
    return box((minx + maxx) / 2.0, miny, maxx, maxy)


def write_qzm_csv(path, rows) -> None:
    """rows: list of (wo_zell_id, ao_zell_id, gesamtpendler)."""
    df = pd.DataFrame(rows, columns=["wo_zell_id", "ao_zell_id", "gesamtpendler"])
    df.to_csv(path, index=False, quoting=1)  # QUOTE_ALL like upstream


def write_statisch_csv(path, rows, relation: bool = False) -> None:
    """Write a BA Statisch/Relationen CSV (semicolon, leading index column).

    rows: list of dicts with keys ``WO_verb_zell_id`` (+ ``AO_verb_zell_id``
    when relation=True), ``SvB_aGeB`` and optionally the breakdown columns;
    missing breakdowns are filled with 0. Values may be the string ``"*"``
    (Dominanz suppression).
    """
    breakdown = ["Ges_M", "Ges_W", "Alt_u25", "Alt_25bu45", "Alt_45bu67",
                 "Alt_67um", "AZ_V", "AZ_T", "AZ_KA", "WB_PGL", "WB_DL",
                 "WB_KA", "Azubi"]
    id_cols = ["WO_verb_zell_id"] + (["AO_verb_zell_id"] if relation else [])
    out = []
    for i, r in enumerate(rows, start=1):
        row = {"": str(i)}
        for c in id_cols:
            row[c] = r[c]
        row["SvB_aGeB"] = r.get("SvB_aGeB", 0)
        for c in breakdown:
            row[c] = r.get(c, 0)
        out.append(row)
    pd.DataFrame(out).to_csv(path, sep=";", index=False, quoting=1)
