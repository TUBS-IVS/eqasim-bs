"""External-Kreis workplace stage for Braunschweig.

Rationale
---------
``braunschweig.political_prefix`` restricts the whole upstream pipeline
(population, distance matrix, gravity model, ALKIS/OSM work locations)
to the 8 ZGB Kreise. But the BA Pendleratlas 2025 shows that roughly
28 % of all employed ZGB residents have their workplace outside the
ZGB (61 160 SvB outbound of ~220 000 total).  Without an external work
pool the synthetic population keeps every commuter inside ZGB-8, which
is the main driver of the observed 8.7 km vs 20.7 km shortfall on
MiD P13.

This stage produces **one synthetic workplace entry per external Kreis**
that has an outbound flow from ZGB above a configurable threshold
(default 50 SvB).  Each entry is anchored at the *population-weighted*
centroid of the Kreis: we load the VG250-EW ``vg250_gem`` layer (one
polygon per Gemeinde with the official EWZ head-count), weight the
Gemeinde representative points by EWZ, and reduce to a single Point
per Kreis.  Population is used as an employment proxy — it places the
synthetic workplace at the Kreis' urban centre (e.g. Hannover city
inside Region Hannover, Hildesheim city inside LK Hildesheim) instead
of the land-centroid that may fall into uninhabited forest or farmland
for large, sparse Kreise.  If Gemeinde data is unavailable for a Kreis
we fall back to the dissolved Kreis polygon centroid.

Each workplace gets a fabricated ``commune_id`` (``EXT<ARS5>``) so it
cannot collide with real 8-digit German AGS codes, and carries
``employees`` equal to the total BA outbound SvB (ZGB → this Kreis).
Downstream, ``braunschweig.locations.work`` concatenates these rows
onto the Bavaria work-location stage, and ``braunschweig.gravity.model``
emits outbound flows so the sampling code picks them up.

Output schema (GeoDataFrame, UTM32N):
    ars5            str     5-digit Kreis ARS (e.g. ``03241``)
    kreis_name      str     Human-readable name from VG250 (GEN field)
    commune_id      str     Synthetic id ``EXT<ARS5>`` used downstream
    iris_id         str     ``commune_id + '0000'`` (fake IRIS convention)
    employees       int     BA Pendler outbound flow ZGB → this Kreis
    ewz_total       int     Sum of Gemeinde EWZ used as placement weight
    placement       str     ``"emp_weighted"`` or ``"kreis_centroid"``
    geometry        Point   Population-weighted Kreis location (UTM32N)
"""

from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

# Default lower bound: Kreise that attract fewer than this many SvB
# commuters from the whole ZGB are dropped.  Keeps the synthetic
# workplace list short enough to inspect manually.
DEFAULT_MIN_FLOW = 50

# Share of external Kreise placed at the (cruder) dissolved Kreis-polygon
# centroid instead of the population-weighted centroid above which the
# placement log is emitted with a ``WARNING:`` prefix.  The Kreis-centroid
# fallback is expected to be rare (only Kreise with no usable VG250-EW
# Gemeinde EWZ); a high share signals a data-quality problem in the
# Gemeinde population layer that systematically biases external-workplace
# locations toward land centroids (possibly uninhabited terrain).
KREIS_CENTROID_FALLBACK_WARN_SHARE = 0.10


def summarise_placement_fallback(placement, total_employees, min_flow):
    """Build the external-workplace placement log line.

    Counts how many external Kreise were anchored at the
    population-weighted Gemeinde centroid (``emp_weighted``) versus the
    Kreis-polygon-centroid fallback (``kreis_centroid``), reports the
    fallback as both an absolute count and a share, and prefixes the line
    with ``WARNING:`` when the fallback share exceeds
    :data:`KREIS_CENTROID_FALLBACK_WARN_SHARE`.

    This function is pure (no side effects, no I/O): it only derives the
    log message from the already-computed ``placement`` labels, so it does
    not change any output value, geometry, or RNG draw.

    Parameters
    ----------
    placement
        Iterable / pandas Series of per-Kreis placement labels, each one of
        ``"emp_weighted"`` or ``"kreis_centroid"``.
    total_employees : int
        Total outbound SvB across all external Kreise (for the log line).
    min_flow : int
        Configured minimum outbound flow threshold (for the log line).

    Returns
    -------
    str
        The formatted log line (caller is responsible for printing it).
    """
    placement = pd.Series(list(placement), dtype=object)
    n_total = int(len(placement))
    n_weighted = int((placement == "emp_weighted").sum())
    n_fallback = int((placement == "kreis_centroid").sum())
    fallback_share = (n_fallback / n_total) if n_total > 0 else 0.0
    prefix = "WARNING: " if fallback_share > KREIS_CENTROID_FALLBACK_WARN_SHARE else ""
    return (
        f"{prefix}[braunschweig.data.external_workplaces] "
        f"{n_total} external Kreise, {total_employees:,} SvB (outbound from ZGB), "
        f"threshold >= {min_flow}; placement: {n_weighted} emp-weighted, "
        f"{n_fallback} Kreis-centroid fallback ({fallback_share:.1%})"
    )


def configure(context):
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
    context.config("braunschweig.external_work_min_flow", DEFAULT_MIN_FLOW)
    context.stage("braunschweig.data.census.pendler")


def _vsi_path(context) -> str:
    """Return the GDAL ``/vsizip/`` URI for the VG250-EW GeoPackage."""
    archive = "{}/{}".format(
        context.config("data_path"),
        context.config("germany.population_path"),
    )
    inner = context.config("germany.population_source")
    return f"/vsizip/{archive}/{inner}"


def _load_kreise(context) -> gpd.GeoDataFrame:
    """Kreis polygons from the VG250-EW ``vg250_krs`` layer (UTM32N).

    ``vg250_krs`` ships one row per Kreis × geometry fragment, so we
    dissolve by the 5-digit Kreis ARS and pick the ``GF = 4`` land row
    name (GF=4 = "Landfläche mit Strukturen").  Result: 401 Kreise.
    """
    df = gpd.read_file(
        _vsi_path(context), layer="vg250_krs",
        columns=["ARS", "GEN", "BEZ", "GF"],
        engine="pyogrio",
    )
    df["ars5"] = df["ARS"].astype(str).str[:5]

    # Keep land geometry (GF=4) first when selecting the Kreis name.
    df = df.sort_values(["ars5", "GF"], ascending=[True, False])
    names = df.groupby("ars5").agg(
        kreis_name=("GEN", "first"),
        bez=("BEZ", "first"),
    ).reset_index()

    df_geom = df.dissolve(by="ars5", as_index=False)[["ars5", "geometry"]]
    return df_geom.merge(names, on="ars5", how="left")


def _load_gemeinden(context) -> gpd.GeoDataFrame:
    """Gemeinde polygons with EWZ (population) from VG250-EW ``vg250_gem``.

    Returns one row per Gemeinde (ars5 = first 5 chars of ARS) with
    ``ewz`` coerced to a non-negative integer.  Rows with missing or
    non-positive EWZ are dropped because they are excluded from the
    population-weighted centroid anyway.
    """
    df = gpd.read_file(
        _vsi_path(context), layer="vg250_gem",
        columns=["ARS", "GEN", "GF", "EWZ"],
        engine="pyogrio",
    )
    # Keep only land geometries (GF=4) — water Gemeinden have EWZ=0.
    df = df[df["GF"] == 4].copy()
    df["ars5"] = df["ARS"].astype(str).str[:5]
    df["ewz"] = pd.to_numeric(df["EWZ"], errors="coerce").fillna(0.0)
    df = df[df["ewz"] > 0].copy()
    return df[["ars5", "GEN", "ewz", "geometry"]]


def _weighted_centroids(kreise: gpd.GeoDataFrame,
                        gemeinden: gpd.GeoDataFrame) -> pd.DataFrame:
    """Compute population-weighted Kreis centroids from Gemeinden.

    For every Kreis present in ``kreise`` we aggregate its Gemeinden'
    representative points (polygon centroids) weighted by EWZ:

        x̂(K) = Σ ewz_g · x_g / Σ ewz_g

    Kreise without any matching Gemeinde end up with NaN coordinates —
    the caller is expected to fall back to the Kreis-polygon centroid.

    Returns a plain ``DataFrame`` with columns ``ars5, ewz_total, x, y``.
    """
    gem = gemeinden.copy()
    pts = gem.geometry.representative_point()
    gem["x"] = pts.x
    gem["y"] = pts.y

    # Weighted sums per Kreis — groupby-apply avoids an outer merge.
    gem["wx"] = gem["x"] * gem["ewz"]
    gem["wy"] = gem["y"] * gem["ewz"]

    agg = gem.groupby("ars5", as_index=False).agg(
        ewz_total=("ewz", "sum"),
        wx=("wx", "sum"),
        wy=("wy", "sum"),
    )
    agg["x"] = agg["wx"] / agg["ewz_total"]
    agg["y"] = agg["wy"] / agg["ewz_total"]

    # Restrict to Kreise actually on the output list.
    return agg[agg["ars5"].isin(kreise["ars5"])][
        ["ars5", "ewz_total", "x", "y"]
    ].copy()


def execute(context) -> gpd.GeoDataFrame:
    zgb = [str(p) for p in context.config("braunschweig.political_prefix")]
    min_flow = int(context.config("braunschweig.external_work_min_flow"))

    df_pendler = context.stage("braunschweig.data.census.pendler")

    # Outbound flows ZGB → external Kreis (union over all 8 ZGB origins).
    outbound = (
        df_pendler[
            df_pendler["orig_ars"].isin(zgb)
            & ~df_pendler["dest_ars"].isin(zgb)
        ]
        .groupby("dest_ars", as_index=False)["flow"]
        .sum()
        .rename(columns={"dest_ars": "ars5", "flow": "employees"})
    )
    outbound = outbound[outbound["employees"] >= min_flow].copy()

    if outbound.empty:
        raise RuntimeError(
            "[braunschweig.data.external_workplaces] no outbound flows "
            "above threshold — check pendler data or threshold."
        )

    # Kreis-level polygons (fallback geometry + names).
    kreise = _load_kreise(context)
    df = outbound.merge(kreise, on="ars5", how="left")

    missing = df[df["geometry"].isna()]
    if len(missing):
        print(
            "[braunschweig.data.external_workplaces] "
            f"{len(missing)} destination Kreise without VG250 match: "
            f"{missing['ars5'].tolist()}"
        )
        df = df.dropna(subset=["geometry"]).copy()

    df = gpd.GeoDataFrame(df, geometry="geometry", crs=kreise.crs)

    # Gemeinde-level centroids weighted by EWZ.  Any Kreis that has no
    # matching Gemeinde (very rare; e.g. pure water-body AGS) keeps the
    # Kreis-polygon centroid computed further down.
    gemeinden = _load_gemeinden(context)
    weighted = _weighted_centroids(df, gemeinden)
    df = df.merge(weighted, on="ars5", how="left")

    # Kreis-centroid fallback (used when ewz_total is NaN or 0).
    kreis_centroid = df.geometry.centroid
    has_weight = df["ewz_total"].fillna(0.0) > 0
    coords_x = np.where(has_weight, df["x"], kreis_centroid.x)
    coords_y = np.where(has_weight, df["y"], kreis_centroid.y)
    df["geometry"] = [Point(x, y) for x, y in zip(coords_x, coords_y)]
    df["placement"] = np.where(
        has_weight, "emp_weighted", "kreis_centroid"
    )
    df["ewz_total"] = df["ewz_total"].fillna(0.0).astype(int)

    # Fabricated commune_id / iris_id.  The ``EXT`` prefix guarantees no
    # collision with real 8-digit German AGS codes, which are all numeric.
    df["commune_id"] = "EXT" + df["ars5"].astype(str)
    df["iris_id"] = df["commune_id"] + "0000"
    df["employees"] = df["employees"].astype(int)

    total = int(df["employees"].sum())
    print(summarise_placement_fallback(df["placement"], total, min_flow))

    return df[[
        "ars5", "kreis_name", "commune_id", "iris_id",
        "employees", "ewz_total", "placement", "geometry",
    ]]


def validate(context):
    path = os.path.join(
        context.config("data_path"),
        context.config("germany.population_path"),
    )
    if not os.path.exists(path):
        raise RuntimeError(f"VG250 archive not found: {path}")
    return os.path.getsize(path)
