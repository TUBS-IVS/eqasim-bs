"""Fit metrics for the distance-fit diagnostic.

Two families:
  - band_share_fit: realised distribution vs a band-share reference (EMD per key).
  - mean_distance_fit: realised mean km vs a mean-km reference (abs/rel error per key).
honesty_summary collapses a per-key fit frame into the three honest numbers
(aggregate, subpopulation-weighted mean, worst subpopulation) and flags whether
the comparison is genuine validation (all references out_of_sample).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.metrics import band_shares, emd_on_bands


def band_share_fit(df_dist, key_col, targets_by_key, band_edges, *, reference_tag):
    edges = np.asarray(band_edges, dtype=float)
    n_bands = len(edges) - 1
    rows = []
    for key, grp in df_dist.groupby(key_col):
        target = targets_by_key.get(str(key))
        if target is None:
            target = targets_by_key.get(key)
        if target is None:
            continue
        target = np.asarray(target, dtype=float)
        shares = band_shares(np.asarray(grp["distance_km"].values, dtype=float), edges)
        emd = emd_on_bands(shares, target)
        for b in range(n_bands):
            rows.append({
                "key_type": key_col, "key": key, "band": b,
                "band_lo_km": float(edges[b]),
                "band_hi_km": float(edges[b + 1]) if np.isfinite(edges[b + 1]) else 999.0,
                "model_share": float(shares[b]), "target_share": float(target[b]),
                "diff": float(shares[b] - target[b]), "emd": float(emd),
                "n": int(len(grp)), "reference_tag": reference_tag,
            })
    return pd.DataFrame(rows)


def mean_distance_fit(df_dist, key_cols, targets_by_key, *, reference_tag):
    rows = []
    for key_vals, grp in df_dist.groupby(key_cols):
        key = "|".join(str(v) for v in (key_vals if isinstance(key_vals, tuple) else (key_vals,)))
        target = targets_by_key.get(key)
        if target is None:
            continue
        model_mean = float(np.mean(grp["distance_km"].values))
        abs_err = abs(model_mean - float(target))
        rel_err = abs_err / float(target) if target else float("nan")
        rows.append({
            "key_type": "|".join(key_cols), "key": key,
            "model_mean_km": model_mean, "target_mean_km": float(target),
            "abs_err_km": abs_err, "rel_err": rel_err,
            "n": int(len(grp)), "reference_tag": reference_tag,
        })
    return pd.DataFrame(rows)


def honesty_summary(fit_df, *, metric):
    if fit_df.empty:
        return {"aggregate": None, "subpop_weighted_mean": None, "worst_key": None,
                "worst_value": None, "is_validation": False}
    per_key = fit_df.drop_duplicates("key")
    w = per_key["n"].astype(float).values
    v = per_key[metric].astype(float).values
    worst_idx = int(np.argmax(v))
    return {
        "aggregate": None,
        "subpop_weighted_mean": float(np.average(v, weights=w)) if w.sum() else float(np.mean(v)),
        "worst_key": per_key.iloc[worst_idx]["key"],
        "worst_value": float(v[worst_idx]),
        "is_validation": bool((per_key["reference_tag"] == "out_of_sample").all()),
    }
