"""VerBindungen Verkehrszellen geometry + AGS mapping, clipped to the ZGB.

Reads ``Shapefiles_VerBindungen_Zellen.zip`` (layer
``verbindungen-verkehrszellen``: 3,189 cells Germany-wide, EPSG:4326, columns
``ags_0`` comma-separated 8-digit AGS at Gebietsstand 31.12.2019, ``ewz``,
``zell_id`` = ``vg250-<n>`` | ``stadtteil-<n>``, ``rs7``), reprojects to
EPSG:25832 and clips to the configured ``braunschweig.political_prefix``
Kreise. Fetch the file with ``scripts/download_verbindungen.py``.

Outputs a tuple ``(df_cells, df_cell_commune)``:

- ``df_cells``: GeoDataFrame [cell_id, kreis_id, is_stadtteil, centroid_x,
  centroid_y, geometry], EPSG:25832 (centroids in meters).
- ``df_cell_commune``: DataFrame [cell_id, commune_id, via_fallback] mapping
  each cell to the 2025 municipality universe (12-digit ARS).

AGS(2019) -> commune(2025) mapping: PRIMARY is the direct AGS-8 dictionary
match against ``data.spatial.municipalities`` (via ``ars_to_ags8``); the
FALLBACK for Gebietsreform cases assigns every in-scope municipality whose
representative point falls inside the cell polygon. The primary/fallback rate
is logged; above ``braunschweig.verbindungen.max_ags_fallback_share`` the
stage raises (a high rate means the AGS join is broken, not reformed).
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8

LAYER = "verbindungen-verkehrszellen"
SHAPE_ZIP = "Shapefiles_VerBindungen_Zellen.zip"
TARGET_CRS = "EPSG:25832"


def parse_ags_list(raw) -> list[str]:
    """Split the comma-separated ``ags_0`` attribute into padded AGS-8 codes.

    Deduplicates while preserving order (Stadtteil rows repeat the parent
    Gemeinde AGS).
    """
    out: list[str] = []
    for part in str(raw).split(","):
        code = part.strip().zfill(8)
        if code and code not in out:
            out.append(code)
    return out


def cell_kreis_id(ags_list: list[str]) -> str:
    """Return the single 5-digit Kreis prefix shared by all AGS of one cell.

    The report guarantees cells aggregate within one NUTS3; a violation means
    the shapefile changed and clipping by Kreis would be wrong -> ValueError.
    """
    prefixes = {a[:5] for a in ags_list}
    if len(prefixes) != 1:
        raise ValueError(f"cell spans multiple Kreise: {sorted(prefixes)}")
    return next(iter(prefixes))


def build_zones_frames(gdf_raw: gpd.GeoDataFrame,
                       df_municipalities: gpd.GeoDataFrame,
                       scope: list[str],
                       max_fallback_share: float):
    """Pure core of the stage; see module docstring for the contract."""
    required = {"ags_0", "zell_id", "geometry"}
    missing = required - set(gdf_raw.columns)
    if missing:
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.zones] expected columns missing "
            f"from the cell shapefile: {sorted(missing)}"
        )

    gdf = gdf_raw.to_crs(TARGET_CRS).copy()
    gdf["ags_list"] = gdf["ags_0"].map(parse_ags_list)
    gdf["kreis_id"] = gdf["ags_list"].map(cell_kreis_id)

    scope = [str(p) for p in scope]
    gdf = gdf[gdf["kreis_id"].isin(scope)].copy()
    if gdf.empty:
        raise RuntimeError(
            "[braunschweig.data.verbindungen.zones] no cells matched scope "
            f"{scope}; check braunschweig.political_prefix and the shapefile."
        )
    if gdf["zell_id"].duplicated().any():
        raise RuntimeError(
            "[braunschweig.data.verbindungen.zones] duplicate zell_id in shapefile"
        )

    gdf["cell_id"] = gdf["zell_id"].astype(str)
    gdf["is_stadtteil"] = gdf["cell_id"].str.startswith("stadtteil-")
    gdf["centroid_x"] = gdf.geometry.centroid.x
    gdf["centroid_y"] = gdf.geometry.centroid.y

    # --- AGS(2019) -> commune(2025) mapping --------------------------------
    df_mun = df_municipalities[["commune_id", "geometry"]].copy()
    df_mun["commune_id"] = df_mun["commune_id"].astype(str)
    df_mun["ags8"] = df_mun["commune_id"].map(ars_to_ags8)
    ags_to_commune = dict(zip(df_mun["ags8"], df_mun["commune_id"]))

    rows, n_primary, n_fallback = [], 0, 0
    unmatched_by_cell: dict[str, list[str]] = {}
    for cell_id, ags_list in zip(gdf["cell_id"], gdf["ags_list"]):
        for ags in ags_list:
            commune = ags_to_commune.get(ags)
            if commune is not None:
                rows.append((cell_id, commune, False))
                n_primary += 1
            else:
                unmatched_by_cell.setdefault(cell_id, []).append(ags)
                n_fallback += 1

    # Geometric fallback: municipalities whose representative point lies in a
    # cell that has unmatched AGS (Gebietsreform since 2019).
    n_fallback_resolved = 0
    if unmatched_by_cell:
        mapped = {(c, m) for c, m, _ in rows}
        cells_fb = gdf[gdf["cell_id"].isin(unmatched_by_cell)][["cell_id", "geometry"]]
        pts = df_mun.copy()
        pts["geometry"] = pts.geometry.representative_point()
        hit = gpd.sjoin(pts, cells_fb.set_geometry("geometry"),
                        how="inner", predicate="within")
        hit_cells = set(hit["cell_id"])
        for commune, cell_id in zip(hit["commune_id"], hit["cell_id"]):
            if (cell_id, commune) not in mapped:
                rows.append((cell_id, commune, True))
        # An unmatched AGS counts as geometrically resolved when the sjoin
        # found at least one commune inside its cell. An unresolved AGS is
        # acceptable ONLY while its cell keeps commune coverage through its
        # other AGS matches AND the commune completeness check below passes;
        # otherwise the coverage check raises.
        n_fallback_resolved = sum(
            len(ags_codes)
            for fb_cell_id, ags_codes in unmatched_by_cell.items()
            if fb_cell_id in hit_cells
        )

    n_total = n_primary + n_fallback
    fallback_share = n_fallback / n_total if n_total else 0.0
    print(
        "[braunschweig.data.verbindungen.zones] AGS->commune mapping: "
        f"primary (direct AGS match) {n_primary}/{n_total} "
        f"({100.0 * n_primary / n_total if n_total else 0.0:.1f}%), "
        f"fallback (geometric) {n_fallback}/{n_total} "
        f"({100.0 * fallback_share:.1f}%)"
    )
    if n_fallback:
        print(
            "[braunschweig.data.verbindungen.zones] geometric fallback outcome: "
            f"{n_fallback_resolved}/{n_fallback} unmatched AGS resolved via "
            f"sjoin, {n_fallback - n_fallback_resolved}/{n_fallback} unresolved"
        )
    if fallback_share > max_fallback_share:
        raise RuntimeError(
            "[braunschweig.data.verbindungen.zones] AGS fallback share "
            f"{fallback_share:.1%} exceeds bound {max_fallback_share:.1%}; "
            "the AGS join is likely broken (format/Gebietsstand mismatch)."
        )

    df_cells = gpd.GeoDataFrame(
        gdf[["cell_id", "kreis_id", "is_stadtteil",
             "centroid_x", "centroid_y", "geometry"]].reset_index(drop=True),
        crs=TARGET_CRS,
    )
    df_cell_commune = pd.DataFrame(
        rows, columns=["cell_id", "commune_id", "via_fallback"]
    ).drop_duplicates(["cell_id", "commune_id"]).reset_index(drop=True)

    # Commune-coverage completeness (fail-early): every in-scope commune must
    # appear in the cell->commune mapping at least once. The 2019 cells tile
    # the scope territory, so a hole means broken geometry or scope input; a
    # commune silently absent from df_cell_commune would otherwise drop its
    # commuters from the OD reference with zero signal.
    in_scope_communes = set(
        df_mun.loc[df_mun["commune_id"].str[:5].isin(scope), "commune_id"]
    )
    uncovered = sorted(in_scope_communes - set(df_cell_commune["commune_id"]))
    if uncovered:
        examples = uncovered[:10]
        print(
            f"[braunschweig.data.verbindungen.zones] {len(uncovered)} in-scope "
            f"commune(s) without cell coverage, e.g. {examples}"
        )
        raise RuntimeError(
            "[braunschweig.data.verbindungen.zones] commune coverage check "
            f"failed: {len(uncovered)} in-scope commune(s) missing from the "
            f"cell->commune mapping (e.g. {examples}); the 2019 cells must "
            "tile the scope territory -- check the cell shapefile and the "
            "municipalities input."
        )

    stats = dict(n_cells=len(df_cells), n_ags_primary=n_primary,
                 n_ags_fallback=n_fallback, fallback_share=fallback_share)
    print(f"[braunschweig.data.verbindungen.zones] {len(df_cells)} ZGB cells "
          f"({int(df_cells['is_stadtteil'].sum())} stadtteil), "
          f"{df_cell_commune['commune_id'].nunique()} communes mapped")
    return df_cells, df_cell_commune, stats


def configure(context):
    context.config("data_path")
    context.config("braunschweig.verbindungen_path", "verbindungen")
    context.config("braunschweig.verbindungen.max_ags_fallback_share", 0.10)
    context.config("braunschweig.political_prefix")
    context.stage("data.spatial.municipalities")


def execute(context):
    path = os.path.join(
        context.config("data_path"),
        context.config("braunschweig.verbindungen_path"),
        SHAPE_ZIP,
    )
    if not os.path.exists(path):
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.zones] missing {path}; "
            "fetch it with: python scripts/download_verbindungen.py"
        )
    gdf_raw = gpd.read_file(f"zip://{path}!{LAYER}.shp")
    if gdf_raw.crs is None or gdf_raw.crs.to_epsg() != 4326:
        raise RuntimeError(
            "[braunschweig.data.verbindungen.zones] expected EPSG:4326 source "
            f"CRS, got {gdf_raw.crs}; upstream file changed?"
        )
    df_municipalities = context.stage("data.spatial.municipalities")
    df_cells, df_cell_commune, _ = build_zones_frames(
        gdf_raw, df_municipalities,
        scope=context.config("braunschweig.political_prefix"),
        max_fallback_share=context.config(
            "braunschweig.verbindungen.max_ags_fallback_share"),
    )
    return df_cells, df_cell_commune
