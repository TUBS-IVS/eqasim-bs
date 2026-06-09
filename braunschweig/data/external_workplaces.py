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

This stage produces **one synthetic workplace entry per Gemeinde** of
each external Kreis that has an outbound flow from ZGB above a
configurable threshold (default 50 SvB).  The Kreis-level outbound SvB
is split across the Kreis' Gemeinden in proportion to population (EWZ)
using the largest-remainder method (exact integer apportionment), so
each Gemeinde receives an integer employee count and the sum equals the
Kreis flow exactly.  Each Gemeinde point is anchored at the Gemeinde
polygon representative point (which tends to fall on the urban core,
not farmland for large sparse Gemeinden).

Population is used as an employment proxy (ASSUMPTION: commute endpoints
concentrate at population centres; no sub-Kreis BA OD data is available
to validate against).  This places jobs in Hannover city (not the rural
fringe of Region Hannover), Hildesheim city, etc.

Each workplace gets a fabricated ``commune_id`` (``EXT<gem_ags8>``),
where ``gem_ags8`` is the 8-digit AGS of the Gemeinde.  The prefix
``EXT`` guarantees no collision with real AGS codes (all-numeric).
``commune_id[3:8]`` recovers the Kreis ARS5 (legacy convention).
``employees`` is the population-apportioned share of the Kreis outbound.

Downstream, ``braunschweig.locations.work`` concatenates these rows onto
the ZGB work-location stage (row-count-agnostic), and
``braunschweig.gravity.model`` emits outbound flows so the sampling code
picks them up.

Output schema (GeoDataFrame, UTM32N):
    ars5            str     5-digit Kreis ARS (e.g. ``03241``)
    gem_ags         str     8-digit Gemeinde AGS (e.g. ``03241001``)
    commune_id      str     ``"EXT" + gem_ags`` (downstream id)
    iris_id         str     ``commune_id + "0000"`` (fake IRIS convention)
    employees       int     Population-apportioned Kreis outbound share
    ewz             float   Gemeinde population (EWZ)
    geometry        Point   Gemeinde representative point (UTM32N)
"""

from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

from braunschweig.data.cordon.external_points import gemeinde_external_points

# Default lower bound: Kreise that attract fewer than this many SvB
# commuters from the whole ZGB are dropped.  Keeps the synthetic
# workplace list short enough to inspect manually.
DEFAULT_MIN_FLOW = 50


def build_external_workplaces(
    outbound: pd.DataFrame,
    gemeinden: "gpd.GeoDataFrame",
    min_flow: int = DEFAULT_MIN_FLOW,
) -> "gpd.GeoDataFrame":
    """Split per-Kreis outbound flows into per-Gemeinde synthetic workplaces.

    This is a pure function (no I/O, no context): it reuses the Task-1
    helper :func:`braunschweig.data.cordon.external_points.gemeinde_external_points`
    to avoid duplicating the population-weighted apportionment logic.

    Parameters
    ----------
    outbound:
        DataFrame ``[ars5, employees]`` -- BA Pendler outbound SvB per
        external Kreis (ZGB -> external), already aggregated across ZGB
        origins.
    gemeinden:
        GeoDataFrame ``[ars5, gem_ags, ewz, geometry]`` -- external Gemeinden
        with 8-digit AGS (``gem_ags``), population (``ewz`` > 0), polygon
        geometry (any UTM CRS).
    min_flow:
        Minimum outbound SvB for a Kreis to be kept (inclusive).

    Returns
    -------
    GeoDataFrame with columns:
        ``ars5``, ``gem_ags``, ``commune_id``, ``iris_id``,
        ``employees``, ``ewz``, ``geometry``

    ``commune_id = "EXT" + gem_ags`` (8-digit AGS), so ``commune_id[3:8]``
    recovers the Kreis ARS5 (legacy convention).
    ``iris_id = commune_id + "0000"`` (fake IRIS id, matches convention in
    ``data.spatial.municipalities``).
    ``employees`` per Gemeinde sums exactly to the Kreis outbound (largest-
    remainder apportionment).
    """
    # Rename employees -> flow so gemeinde_external_points sees the expected schema.
    kreis_flow = outbound.rename(columns={"employees": "flow"})[["ars5", "flow"]].copy()

    result = gemeinde_external_points(kreis_flow, gemeinden, min_flow=min_flow)

    # Rename flow back to employees and add iris_id.
    result = result.rename(columns={"flow": "employees"}).copy()
    result["iris_id"] = result["commune_id"] + "0000"

    return result[["ars5", "gem_ags", "commune_id", "iris_id", "employees", "ewz", "geometry"]]


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


def _load_gemeinden(context) -> gpd.GeoDataFrame:
    """Gemeinde polygons with EWZ (population) from VG250-EW ``vg250_gem``.

    Returns one row per Gemeinde with columns:
        ars5      str    5-digit Kreis ARS (first 5 chars of ARS field)
        gem_ags   str    8-digit Gemeinde AGS
        GEN       str    Gemeinde name (human-readable, from VG250 GEN field)
        ewz       float  Population headcount (EWZ), always > 0
        geometry        Gemeinde polygon (UTM32N)

    ``gem_ags`` is derived preferentially from the ``AGS`` field in
    ``vg250_gem`` (if present and 8 digits).  When ``AGS`` is absent or
    malformed the 12-digit ``ARS`` is used as a fallback:
    ``gem_ags = ARS[:5] + ARS[9:12]``  (Kreis part + Gemeinde suffix).
    An assertion verifies that ``gem_ags`` is always 8 chars and that
    ``gem_ags[:5] == ars5`` (Kreis prefix consistency).

    Rows with missing or non-positive EWZ are dropped because they are
    excluded from the population-weighted apportionment anyway.
    """
    # Read both ARS and AGS so we can pick whichever is available.
    df = gpd.read_file(
        _vsi_path(context), layer="vg250_gem",
        columns=["ARS", "AGS", "GEN", "GF", "EWZ"],
        engine="pyogrio",
    )
    # Keep only land geometries (GF=4) — water Gemeinden have EWZ=0.
    df = df[df["GF"] == 4].copy()
    df["ars5"] = df["ARS"].astype(str).str[:5]
    df["ewz"] = pd.to_numeric(df["EWZ"], errors="coerce").fillna(0.0)
    df = df[df["ewz"] > 0].copy()

    # Derive 8-digit Gemeinde AGS: prefer the native AGS field when it looks
    # valid (8 alphanumeric chars), otherwise fall back to ARS slicing.
    ags_native = df["AGS"].astype(str).str.strip()
    ags_from_ars = df["ARS"].astype(str).str[:5] + df["ARS"].astype(str).str[9:12]
    use_native = ags_native.str.len() == 8
    df["gem_ags"] = ags_native.where(use_native, ags_from_ars)

    # Sanity: every gem_ags must be 8 chars and share the Kreis prefix.
    assert (df["gem_ags"].str.len() == 8).all(), \
        "[_load_gemeinden] derived gem_ags contains entries with length != 8"
    assert (df["gem_ags"].str[:5] == df["ars5"]).all(), \
        "[_load_gemeinden] gem_ags[:5] != ars5 — Kreis prefix mismatch"

    return df[["ars5", "gem_ags", "GEN", "ewz", "geometry"]]


def execute(context) -> gpd.GeoDataFrame:
    """Build per-Gemeinde synthetic workplaces for external Kreise.

    Computes outbound BA Pendler flows (ZGB -> external Kreise), splits
    each Kreis flow across its Gemeinden proportional to EWZ (population),
    and returns one row per Gemeinde above the ``braunschweig.external_work_min_flow``
    threshold.  Kreise with no matching Gemeinde rows (data gap) trigger a
    WARNING log line (no-silent-fallback rule, CLAUDE.md).

    Returns a GeoDataFrame with at least:
        ``employees``, ``commune_id``, ``iris_id``, ``geometry``
    which is all that ``braunschweig.locations.work`` requires.
    """
    zgb = [str(p) for p in context.config("braunschweig.political_prefix")]
    min_flow = int(context.config("braunschweig.external_work_min_flow"))

    df_pendler = context.stage("braunschweig.data.census.pendler")

    # Outbound flows ZGB -> external Kreis (union over all 8 ZGB origins).
    outbound = (
        df_pendler[
            df_pendler["orig_ars"].isin(zgb)
            & ~df_pendler["dest_ars"].isin(zgb)
        ]
        .groupby("dest_ars", as_index=False)["flow"]
        .sum()
        .rename(columns={"dest_ars": "ars5", "flow": "employees"})
    )
    # Apply threshold before passing to build_external_workplaces so the
    # Kreis list is consistent for the zero-Gemeinde check below.
    outbound = outbound[outbound["employees"] >= min_flow].copy()

    if outbound.empty:
        raise RuntimeError(
            "[braunschweig.data.external_workplaces] no outbound flows "
            "above threshold — check pendler data or threshold."
        )

    # Load Gemeinden with gem_ags column.
    gemeinden = _load_gemeinden(context)

    # Build per-Gemeinde rows (population-weighted split).
    df = build_external_workplaces(outbound, gemeinden, min_flow=min_flow)

    # No-silent-fallback check (CLAUDE.md): warn if any Kreis above threshold
    # yielded zero Gemeinde points (data gap in vg250_gem).
    kreise_with_output = set(df["ars5"])
    for ars5, svb in zip(outbound["ars5"], outbound["employees"]):
        if str(ars5) not in kreise_with_output:
            print(
                f"WARNING: [braunschweig.data.external_workplaces] "
                f"Kreis {ars5} has outbound {svb:,} SvB (>= threshold {min_flow}) "
                f"but produced ZERO Gemeinde points — check vg250_gem coverage."
            )

    n_kreise = outbound["ars5"].nunique()
    n_points = len(df)
    total_svb = int(df["employees"].sum())
    print(
        f"[braunschweig.data.external_workplaces] "
        f"{n_kreise} external Kreise above threshold {min_flow} SvB; "
        f"{n_points} Gemeinde points emitted; "
        f"total outbound SvB = {total_svb:,}"
    )

    return df


def validate(context):
    path = os.path.join(
        context.config("data_path"),
        context.config("germany.population_path"),
    )
    if not os.path.exists(path):
        raise RuntimeError(f"VG250 archive not found: {path}")
    return os.path.getsize(path)
