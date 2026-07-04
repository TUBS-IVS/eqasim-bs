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
    """Per-Kreis realised economic_status shares (one row per ars5)."""
    counts = (households.groupby(["ars5", "economic_status"]).size()
              .unstack("economic_status").reindex(columns=list(STATUS_KEYS)).fillna(0.0))
    shares = counts.div(counts.sum(axis=1), axis=0)
    return shares.reset_index()


def status_srmse_vs_h4(realised: pd.DataFrame, h4: pd.DataFrame) -> dict[str, float]:
    """Per-ars5 standardised RMSE of realised status shares vs the H4 target (percent)."""
    tgt = h4.set_index("ars5")
    out = {}
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
