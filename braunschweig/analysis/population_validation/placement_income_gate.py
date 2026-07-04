"""Phase-0 gate for issue #108: does the EXISTING placement already reproduce the
per-Kreis income/status geography (so the post-hoc income overwrite can simply be
dropped), or is a status × Kreis control needed?

Pure functions over a per-household frame; the real inputs are assembled on the
server (see the server-run recipe in the task report). No model change.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.data.mid.status_by_kreis import STATUS_KEYS


def realised_status_by_kreis(households: pd.DataFrame) -> pd.DataFrame:
    """Per-Kreis realised economic_status shares (one row per ars5).

    Households with a missing (NaN) or out-of-vocabulary economic_status are excluded
    from the share numerator/denominator, and the fallback rate is logged. A Kreis whose
    households are ALL NaN/invalid still appears as a row (with NaN shares) instead of
    silently vanishing -- a silent drop would otherwise hide a broken upstream
    economic_status assignment (see CLAUDE.md "Fallback transparency").
    """
    required = {"ars5", "economic_status"}
    missing = required - set(households.columns)
    if missing:
        raise ValueError(f"placement_income_gate.realised_status_by_kreis requires columns {sorted(missing)}.")
    s = households["economic_status"].astype("object")
    n_total = len(households)
    n_nan = int(s.isna().sum())
    invalid_labels = sorted(set(s.dropna().astype(str).unique()) - set(STATUS_KEYS))
    n_invalid = int(s.dropna().astype(str).isin(invalid_labels).sum())
    if n_nan or n_invalid:
        print(f"WARNING: [placement_income_gate] economic_status has {n_nan}/{n_total} NaN "
              f"({100*n_nan/max(n_total,1):.2f}%) and {n_invalid} out-of-vocabulary "
              f"({100*n_invalid/max(n_total,1):.2f}%) labels {invalid_labels}; these are excluded "
              f"from realised shares -- investigate the upstream economic_status assignment.")
    counts = (households.groupby(["ars5", "economic_status"], dropna=False).size()
              .unstack("economic_status").reindex(columns=list(STATUS_KEYS)).fillna(0.0))
    shares = counts.div(counts.sum(axis=1), axis=0)
    return shares.reset_index()


def status_srmse_vs_h4(realised: pd.DataFrame, h4: pd.DataFrame) -> dict[str, float]:
    """Per-ars5 standardised RMSE of realised status shares vs the H4 target. Both sides are share
    vectors summing to 1 over the 5 STATUS_KEYS, so the standardising mean is a constant 0.2 ->
    SRMSE == 5 * RMSE(shares). This fixed-scale SRMSE is NOT comparable to the count-based SRMSE in
    quality_assessment.py; use it only to rank/compare Kreise within this diagnostic.
    """
    tgt = h4.set_index("ars5")
    out = {}
    skipped = [str(row["ars5"]) for _, row in realised.iterrows() if str(row["ars5"]) not in tgt.index]
    if skipped:
        print(f"WARNING: [placement_income_gate] {len(skipped)} ars5 present in realised but absent "
              f"from the H4 target, skipped from SRMSE: {skipped}")
    for _, row in realised.iterrows():
        ars5 = str(row["ars5"])
        if ars5 not in tgt.index:
            continue
        r = row[list(STATUS_KEYS)].to_numpy(dtype=float)
        t = tgt.loc[ars5, list(STATUS_KEYS)].to_numpy(dtype=float)
        t = t / t.sum() if t.sum() > 0 else t
        mean_t = t.mean()
        out[ars5] = float(np.sqrt(np.mean((r - t) ** 2)) / mean_t) if mean_t > 0 else float("nan")
    return out


def donor_replication(households: pd.DataFrame) -> pd.DataFrame:
    """Per-Kreis donor over-replication (clone-effect signal for the pool check)."""
    if "source_household_id" not in households.columns:
        raise ValueError("placement_income_gate.donor_replication requires 'source_household_id'; it is "
                          "populated by the popsim_mid/popsim_open producers, not simple_ipf_open -- assemble "
                          "the per-household frame from a popsim_mid run.")
    rows = []
    for ars5, grp in households.groupby("ars5"):
        clones = grp["source_household_id"].value_counts()
        rows.append({
            "ars5": ars5,
            "n_households": int(len(grp)),
            "n_unique_donors": int(clones.size),
            "max_clones": int(clones.max()),
            "p95_clones": float(np.percentile(clones.to_numpy(), 95)),
        })
    return pd.DataFrame(rows)
