"""Per-Gemeinde external commuter points (population-weighted split of a Kreis flow).

Turns a per-Kreis external commuter flow (BA Pendler, Kreis-level by privacy) into one
point per Gemeinde of each external Kreis above a flow threshold, splitting the Kreis
flow across its Gemeinden in proportion to population (EWZ) with a largest-remainder
round so the per-Gemeinde counts sum EXACTLY to the Kreis flow.

ASSUMPTION (CLAUDE.md no-invented-reference): Gemeinde population is the proxy for where
commute endpoints (jobs for out-commuters / residences for in-commuters) concentrate
within an external Kreis -- BA publishes no sub-Kreis OD. Population co-locates with
employment for the urban centres that dominate the flow. No sub-Kreis target exists to
validate against; this is a documented assumption, not a calibrated value.

Shared by ``braunschweig.data.external_workplaces`` (out-commuter workplaces) and
``braunschweig.synthesis.incommuters`` (in-commuter origins). Region-neutral; EPSG:25832.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd


def _largest_remainder(total: int, weights) -> np.ndarray:
    """Apportion integer ``total`` across ``weights`` (largest-remainder / Hare).

    Returns an integer array summing exactly to ``total``; ties broken by descending
    fractional remainder then input order. ``weights`` need not be normalised.
    """
    weights = np.asarray(weights, dtype=float)
    if weights.sum() <= 0 or total <= 0:
        return np.zeros(len(weights), dtype=int)
    exact = total * weights / weights.sum()
    floor = np.floor(exact).astype(int)
    remainder = int(total - floor.sum())
    if remainder > 0:
        order = np.argsort(-(exact - floor))
        floor[order[:remainder]] += 1
    return floor


def gemeinde_external_points(
    kreis_flow: pd.DataFrame,
    gemeinden: gpd.GeoDataFrame,
    min_flow: int = 50,
) -> gpd.GeoDataFrame:
    """One point per Gemeinde of each external Kreis with flow >= ``min_flow``.

    Args:
        kreis_flow: DataFrame [ars5, flow] -- external commuter flow per Kreis (already
            aggregated for the direction). Kreise with flow < ``min_flow`` are dropped.
        gemeinden: GeoDataFrame [ars5, gem_ags, ewz, geometry] -- external Gemeinden with
            8-digit AGS (``gem_ags[:5] == ars5``), population (ewz>0), polygon geometry.
        min_flow: minimum per-Kreis flow to keep the Kreis (inclusive).

    Returns:
        GeoDataFrame [ars5, gem_ags, commune_id, flow, ewz, geometry(Point)] in
        ``gemeinden.crs``. ``commune_id == "EXT" + gem_ags`` (so ``commune_id[3:8]`` is the
        Kreis ars5, preserving the legacy ``EXT<ars5>`` convention). ``flow`` per Gemeinde
        sums exactly to the Kreis flow; Gemeinden with ewz<=0 are excluded.
    """
    kf = kreis_flow[kreis_flow["flow"] >= int(min_flow)].copy()
    # Drop Gemeinden with zero or negative population before joining.
    gem = gemeinden[gemeinden["ewz"].astype(float) > 0].copy()
    gem = gem[gem["ars5"].isin(set(kf["ars5"]))].copy()

    flow_by_kreis = {str(k): int(v) for k, v in zip(kf["ars5"], kf["flow"])}

    rows = []
    for ars5, sub in gem.groupby("ars5"):
        total = flow_by_kreis[str(ars5)]
        # Reset index so positional alignment between alloc array and rows is safe.
        sub = sub.reset_index(drop=True)
        alloc = _largest_remainder(total, sub["ewz"].to_numpy(dtype=float))
        pts = sub.geometry.representative_point()
        for i in range(len(sub)):
            f = int(alloc[i])
            if f <= 0:
                continue
            g = sub.iloc[i]
            rows.append({
                "ars5": str(ars5),
                "gem_ags": str(g["gem_ags"]),
                "commune_id": "EXT" + str(g["gem_ags"]),
                "flow": f,
                "ewz": float(g["ewz"]),
                "geometry": pts.iloc[i],
            })

    return gpd.GeoDataFrame(
        rows,
        columns=["ars5", "gem_ags", "commune_id", "flow", "ewz", "geometry"],
        geometry="geometry",
        crs=gemeinden.crs,
    )
