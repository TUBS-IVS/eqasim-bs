"""German Gemeinde centroids OUTSIDE ZGB as long-distance secondary candidates.

Mirrors braunschweig.data.external_workplaces (which does the same for WORK
out-commuters), but WITHOUT the BA-Pendleratlas flow restriction: every German
Gemeinde outside the ZGB political prefix is a candidate, Germany-wide, so the
full MiD secondary desired-distance tail (up to ~700 km) is coverable. The points
are anchored at the Gemeinde representative point and weighted by population (ewz).

commune_id = "EXT" + gem_ags (8-digit AGS) -- the same collision-free convention
as external_workplaces, so existing EXT-prefix analysis filters classify them.

Consumed by braunschweig.synthesis.locations.secondary_chainsolvers
(build_secondary_candidates) when secondary_external_candidates is ON. Only
meaningful with cordon_enabled (RunScenarioCutter then turns the boundary-crossing
secondary trip into a fixed "outside" activity); see the spec.
"""
from __future__ import annotations

import geopandas as gpd

from braunschweig.data.external_workplaces import _load_gemeinden


def build_external_secondary_points(gemeinden, zgb_prefixes):
    """Gemeinde centroids outside ZGB, population-weighted.

    Parameters
    ----------
    gemeinden : GeoDataFrame [ars5, gem_ags, ewz, geometry]
        All German Gemeinden (vg250), ewz>0, any UTM CRS.
    zgb_prefixes : list[str]
        The ZGB Kreis ars5 codes (braunschweig.political_prefix). Gemeinden whose
        ars5 is in this list are EXCLUDED (already covered by the in-area set).

    Returns
    -------
    GeoDataFrame [commune_id, ars5, gem_ags, ewz, geometry], one row per external
    Gemeinde, commune_id = "EXT" + gem_ags.
    """
    zgb = set(str(p) for p in zgb_prefixes)
    out = gemeinden[~gemeinden["ars5"].astype(str).isin(zgb)].copy()
    out["commune_id"] = "EXT" + out["gem_ags"].astype(str)
    return gpd.GeoDataFrame(
        out[["commune_id", "ars5", "gem_ags", "ewz", "geometry"]],
        geometry="geometry", crs=gemeinden.crs,
    )


def configure(context):
    # Reuse the external_workplaces vg250 config keys + the ZGB prefix.
    context.config("data_path")
    context.config(
        "germany.population_path",
        "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
    )
    context.config(
        "germany.population_source",
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg",
    )
    context.config("braunschweig.political_prefix")


def execute(context):
    gemeinden = _load_gemeinden(context)
    zgb = [str(p) for p in context.config("braunschweig.political_prefix")]
    df = build_external_secondary_points(gemeinden, zgb)
    print(
        "[braunschweig.data.external_secondary_points] "
        f"{len(df)} external Gemeinde centroids (outside ZGB); "
        f"total ewz = {int(df['ewz'].sum()):,}"
    )
    return df


def validate(context):
    import os
    path = os.path.join(
        context.config("data_path"),
        context.config("germany.population_path"),
    )
    if not os.path.exists(path):
        raise RuntimeError(f"VG250 archive not found: {path}")
    return os.path.getsize(path)
