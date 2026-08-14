"""VG250/per-Kreis spatial metric computation for the run dashboard.

This module holds the VG250/per-Kreis spatial cluster that ``run_metrics.py``'s
``metrics_matsim`` calls -- ``_ensure_vg250``, ``_load_zgb_kreise``,
``_classify_points``, ``metrics_time_of_day``, ``metrics_per_kreis`` and
``metrics_od_matrix`` -- moved verbatim from ``run_metrics.py`` (which itself
had received them, together with their constants, from ``build_dashboard.py``
during an earlier extraction step; see that module's docstring history).

``ZGB_ARS5`` is defined here because it is used exclusively by this cluster.
``VG250_ZIP`` and ``VG250_CACHE`` used to be built here too (from ``REPO_ROOT``
and ``DASHBOARD_DIR``, imported from the leaf module ``paths.py``); as of
issue #293 they are re-exported from the shared loader module
``braunschweig.analysis.spatial`` instead (see below), so this module no
longer needs ``paths.py`` at all.

``KREIS_NAMES`` is imported from the sibling module ``mid_reference.py`` (its
owner); this module never imports ``run_metrics`` or ``build_dashboard`` back.

``run_metrics.py`` imports ``_load_zgb_kreise``, ``metrics_time_of_day``,
``metrics_per_kreis`` and ``metrics_od_matrix`` from here for its own
``metrics_matsim``. ``build_dashboard.py`` re-exports every name below so
existing callers of ``build_dashboard.<name>`` keep working unchanged.

This module must not import ``run_metrics`` or ``build_dashboard`` -- that
would create an import cycle between the facade/sibling and this module.

**VG250 loading (issue #293).** Locating, extracting and reading the VG250
archive is no longer duplicated here: ``VG250_ZIP``, ``VG250_CACHE`` and
``_ensure_vg250`` now delegate to the single shared loader in
``braunschweig.analysis.spatial`` (the module already documented as the
canonical source of ``REPO_ROOT``/``ZGB8`` for the analysis package; see that
module's docstring for the full strict/tolerant contract). This is a plain
sibling-package import (``braunschweig.analysis.spatial``, not
``braunschweig.analysis.dashboard.*``), so it does not touch the
facade-reexport / no-import-cycle rules above -- ``spatial.py`` is outside
this package and never imports the dashboard facade back.
``_ensure_vg250`` keeps its ``Path | None`` signature and its tolerant
(``strict=False``) failure mode -- a missing archive is logged as a
``warning`` by the shared loader and ``None`` is returned so the rest of the
dashboard still renders without the per-Kreis panel -- but the caching
strategy, the failure-mode decision and the log line all now live in one
place instead of being re-implemented per caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from braunschweig.analysis import spatial
from braunschweig.analysis.dashboard.mid_reference import KREIS_NAMES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ZGB-8 Kreis ARS codes (5-digit) used for spatial joins.
ZGB_ARS5 = list(KREIS_NAMES.keys())

# Re-exported from the shared loader (braunschweig.analysis.spatial) so
# existing importers of these names keep working; see the module docstring.
VG250_ZIP = spatial.VG250_ZIP
VG250_CACHE = spatial.VG250_CACHE

# ---------------------------------------------------------------------------
# VG250 / spatial helpers (per-Kreis + OD matrix)
# ---------------------------------------------------------------------------


def _ensure_vg250() -> Path | None:
    """Extract DE_VG250.gpkg from the shared archive into the shared cache.

    Delegates to :func:`braunschweig.analysis.spatial._resolve_vg250_gpkg`
    with ``strict=False``: the dashboard's per-Kreis panel is one optional
    metric among many, so a missing archive is logged as a ``warning`` (by
    the shared loader) and ``None`` is returned rather than raising -- see
    that module's docstring for why the analysis/validation path instead
    raises.
    """
    return spatial._resolve_vg250_gpkg(strict=False)


def _load_zgb_kreise():
    """Return GeoDataFrame of the 8 ZGB Kreise (CRS EPSG:25832)."""
    p = _ensure_vg250()
    if p is None:
        return None
    try:
        import geopandas as gpd
    except ImportError:
        return None
    krs = gpd.read_file(p, layer="vg250_krs")
    # ARS is the 12-digit Amtlicher Regionalschlüssel; first 5 chars = Kreis code.
    krs["ars5"] = krs["ARS"].astype(str).str[:5]
    krs = krs[krs["ars5"].isin(ZGB_ARS5)].copy()
    krs["name"] = krs["ars5"].map(KREIS_NAMES).fillna(krs.get("GEN", krs["ars5"]))
    krs = krs.to_crs(25832)[["ars5", "name", "geometry"]].reset_index(drop=True)
    return krs


def _classify_points(xs: np.ndarray, ys: np.ndarray, kreise) -> np.ndarray:
    """Spatial-join arrays of x/y (EPSG:25832) to the 8 ZGB Kreise.

    Returns an object array of ARS5 strings (or "external" for points outside).
    """
    import geopandas as gpd
    import pandas as _pd

    pts = gpd.GeoDataFrame(
        {"_idx": np.arange(len(xs))},
        geometry=gpd.points_from_xy(xs, ys),
        crs=25832,
    )
    j = gpd.sjoin(pts, kreise[["ars5", "geometry"]], how="left", predicate="within")
    # Multiple matches per point can occur on borders → keep first.
    j = j.drop_duplicates(subset="_idx").set_index("_idx").sort_index()
    out = j["ars5"].astype("object").fillna("external").to_numpy()
    return out


def metrics_time_of_day(et: pd.DataFrame) -> dict[str, Any]:
    """Hourly trip start histograms broken down by mode and purpose."""
    if et is None or len(et) == 0 or "departure_time" not in et.columns:
        return {}
    hours = (et["departure_time"].astype(float) // 3600).clip(0, 23).astype(int)
    et = et.assign(_h=hours)
    by_mode = (
        et.groupby(["_h", "mode"]).size().unstack(fill_value=0)
        .reindex(range(24), fill_value=0)
    )
    by_pur = (
        et.groupby(["_h", "following_purpose"]).size().unstack(fill_value=0)
        .reindex(range(24), fill_value=0)
    )
    return {
        "hours": list(range(24)),
        "by_mode": {m: [int(v) for v in by_mode[m].tolist()] for m in by_mode.columns},
        "by_purpose": {p: [int(v) for v in by_pur[p].tolist()] for p in by_pur.columns},
        "total_per_hour": [int(v) for v in et.groupby("_h").size().reindex(range(24), fill_value=0).tolist()],
    }


def metrics_per_kreis(et: pd.DataFrame, kreise) -> dict[str, Any]:
    """Per-ZGB-Kreis sim metrics, classified by trip origin (home end of commute)."""
    if et is None or len(et) == 0 or kreise is None:
        return {}
    com = et[et["following_purpose"] == "work"].copy()
    if not len(com):
        return {}
    # Classify origin (home end) of the commute trip.
    ars = _classify_points(com["origin_x"].to_numpy(), com["origin_y"].to_numpy(), kreise)
    com["ars5"] = ars
    out: dict[str, Any] = {}
    for ars5 in ZGB_ARS5:
        sub = com[com["ars5"] == ars5]
        if not len(sub):
            continue
        ms = (sub["mode"].value_counts(normalize=True) * 100).round(1).to_dict()
        out[ars5] = {
            "name": KREIS_NAMES.get(ars5, ars5),
            "n_trips": int(len(sub)),
            "mean_km": float(round(sub["km"].mean(), 2)),
            "median_km": float(round(sub["km"].median(), 2)),
            "mode_share_pct": {k: float(v) for k, v in ms.items()},
        }
    return out


def metrics_od_matrix(et: pd.DataFrame, kreise) -> dict[str, Any]:
    """Origin-Destination matrices at Kreis level, per following_purpose.

    Cells are absolute trip counts; "external" aggregates trips touching
    Kreise outside ZGB-8.
    """
    if et is None or len(et) == 0 or kreise is None:
        return {}
    # Sub-sample to keep the JSON small if there are many trips.
    df = et[["origin_x", "origin_y", "destination_x", "destination_y",
             "following_purpose", "mode"]].dropna()
    o = _classify_points(df["origin_x"].to_numpy(), df["origin_y"].to_numpy(), kreise)
    d = _classify_points(df["destination_x"].to_numpy(), df["destination_y"].to_numpy(), kreise)
    df = df.assign(_o=o, _d=d)
    zones = ZGB_ARS5 + ["external"]
    matrices: dict[str, list[list[int]]] = {}
    purposes = sorted(df["following_purpose"].dropna().unique().tolist())
    for pur in purposes:
        sub = df[df["following_purpose"] == pur]
        # Build matrix counts via pivot
        ct = (
            sub.groupby(["_o", "_d"]).size().unstack(fill_value=0)
            .reindex(index=zones, columns=zones, fill_value=0)
        )
        matrices[pur] = ct.astype(int).values.tolist()
    return {
        "zones": zones,
        "zone_names": [KREIS_NAMES.get(z, "Outside ZGB") for z in zones],
        "purposes": purposes,
        "matrices": matrices,
    }
