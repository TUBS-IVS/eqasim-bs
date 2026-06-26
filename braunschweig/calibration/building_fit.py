"""Building-potential fit report.

Measures whether the realised within-zone distribution of activities over
buildings follows the building activity potentials (``potential_work`` etc.).

Why within-zone SHARES, not absolute counts: the potentials parquet is a 100%
reference (full building stock, full potential values), but a synthesis run is
sampled (e.g. 25%). Comparing absolute realised counts to the 100% potential
would conflate the sampling rate with the spatial fit. Within-zone shares are
sampling-rate-invariant: a building's realised share inside its zone is compared
to its potential share inside the same zone, so the 25%/100% mismatch cancels.

The zone is the unit at which the upstream gravity model fixes the real total
(Gemeinde via GENESIS SvB for work); the building potential only governs the
WITHIN-zone split (see CLAUDE.md "Building-level activity potentials"). The fit
therefore must be evaluated per zone, never globally.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def within_zone_fit(realised_count, potential_weight):
    """Within-zone fit between realised activity counts and building potentials.

    Parameters
    ----------
    realised_count : array-like, shape (n_buildings,)
        Number of activities placed in each building within ONE zone.
    potential_weight : array-like, shape (n_buildings,)
        The building activity potential (e.g. ``potential_work``) for the SAME
        buildings, in the SAME order.

    Returns
    -------
    dict with keys:
        ``n_buildings`` : int
        ``pearson``     : Pearson r between realised and potential (scale-invariant
                          within a zone, so equivalent to correlating the shares)
        ``spearman``    : Spearman rank correlation (robust to the potential's scale)
        ``tv_distance`` : total-variation distance between the two normalised
                          within-zone share vectors, 0.5 * sum |p_i - q_i| in [0, 1].
                          0 = realised follows potential exactly; 1 = disjoint support.
    """
    realised = np.asarray(realised_count, dtype=float)
    potential = np.asarray(potential_weight, dtype=float)

    if realised.shape != potential.shape:
        raise ValueError(
            "realised_count and potential_weight must have the same shape; "
            f"got {realised.shape} vs {potential.shape}"
        )

    n = realised.shape[0]

    realised_total = realised.sum()
    potential_total = potential.sum()
    realised_share = realised / realised_total if realised_total > 0 else realised
    potential_share = potential / potential_total if potential_total > 0 else potential

    tv_distance = 0.5 * float(np.abs(realised_share - potential_share).sum())

    # Correlations are undefined for a single building or a constant vector.
    if n < 2 or np.ptp(realised) == 0 or np.ptp(potential) == 0:
        pearson = float("nan")
        spearman = float("nan")
    else:
        pearson = float(stats.pearsonr(realised, potential)[0])
        spearman = float(stats.spearmanr(realised, potential)[0])

    return {
        "n_buildings": n,
        "pearson": pearson,
        "spearman": spearman,
        "tv_distance": tv_distance,
    }


def build_fit_report(realised, potential, *, sampling_rate,
                     id_col="building_id", zone_col="zone", value_col="potential"):
    """Per-zone building-potential fit report.

    Parameters
    ----------
    realised : DataFrame
        One row per realised activity, carrying ``id_col`` (the building it was
        placed in). The sampled run produces these.
    potential : DataFrame
        One row per building, carrying ``id_col``, ``zone_col`` (the zone the
        within-zone fit is evaluated in, e.g. commune or TAZ) and ``value_col``
        (the building activity potential, e.g. ``potential_work``). This is the
        100% reference support.
    sampling_rate : float
        The run's sampling rate (e.g. 0.25). Recorded in the output so realised
        counts can be scaled to 100% downstream; the within-zone SHARE fit itself
        is sampling-rate-invariant and needs no scaling.
    id_col, zone_col, value_col : str
        Column names in the two frames.

    Returns
    -------
    dict with keys:
        ``per_zone`` : DataFrame[zone, n_buildings, realised_activities,
                       pearson, spearman, tv_distance] -- one row per zone.
        ``coverage`` : dict with the primary/fallback rate (CLAUDE.md
                       no-silent-fallback): how many realised activities landed on
                       a building that carries a potential vs one that does not.
        ``sampling_rate`` : float (echoed back).
    """
    potential_ids = set(potential[id_col])
    on_potential = realised[id_col].isin(potential_ids)

    realised_total = int(len(realised))
    on_potential_count = int(on_potential.sum())
    primary_rate = on_potential_count / realised_total if realised_total > 0 else float("nan")

    coverage = {
        "realised_total": realised_total,
        "on_potential_building": on_potential_count,
        "off_potential_building": realised_total - on_potential_count,
        "primary_rate": primary_rate,
        "fallback_rate": (1.0 - primary_rate) if realised_total > 0 else float("nan"),
    }

    # Realised counts per building, restricted to the potential support. Buildings
    # with a potential but no realised activity must appear with count 0 (they are
    # part of the within-zone share denominator), so we reindex onto the support.
    counts = (realised[on_potential]
              .groupby(id_col).size()
              .rename("realised_count"))
    support = potential[[id_col, zone_col, value_col]].copy()
    support["realised_count"] = support[id_col].map(counts).fillna(0.0)

    rows = []
    for zone, grp in support.groupby(zone_col):
        m = within_zone_fit(grp["realised_count"].values, grp[value_col].values)
        rows.append({
            "zone": zone,
            "n_buildings": m["n_buildings"],
            "realised_activities": int(grp["realised_count"].sum()),
            "pearson": m["pearson"],
            "spearman": m["spearman"],
            "tv_distance": m["tv_distance"],
        })

    per_zone = pd.DataFrame(rows).sort_values("zone").reset_index(drop=True)

    return {
        "per_zone": per_zone,
        "coverage": coverage,
        "sampling_rate": sampling_rate,
    }


def per_building_residuals(realised, potential, *, id_col="building_id",
                           zone_col="zone", value_col="potential"):
    """Per-building realised vs potential within-zone shares and their residual.

    The residual ``realised_share - potential_share`` (within the building's zone)
    is the per-building fit signal for mapping: positive = the building received a
    larger share of its zone's activities than its potential implies (over-filled),
    negative = under-filled. Buildings with a potential but no realised activity
    appear with ``realised_count == 0`` (under-filled), never dropped.

    Returns a DataFrame with one row per building in ``potential``:
    [id_col, zone_col, value_col, realised_count, potential_share,
     realised_share, share_residual].
    """
    counts = (realised[realised[id_col].isin(set(potential[id_col]))]
              .groupby(id_col).size())
    out = potential[[id_col, zone_col, value_col]].copy()
    out["realised_count"] = out[id_col].map(counts).fillna(0).astype(int)

    zone_pot_total = out.groupby(zone_col)[value_col].transform("sum")
    zone_real_total = out.groupby(zone_col)["realised_count"].transform("sum")
    out["potential_share"] = np.where(
        zone_pot_total > 0, out[value_col] / zone_pot_total, 0.0)
    out["realised_share"] = np.where(
        zone_real_total > 0, out["realised_count"] / zone_real_total, 0.0)
    out["share_residual"] = out["realised_share"] - out["potential_share"]
    return out
