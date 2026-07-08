"""Derive the economic_status x Kreis PopulationSim control targets from MiD H4.

Level 1 of the income-weighted placement design (issue #109). The committed
mid2023_H4_status_by_kreis.csv gives the 5-class economic-status ROW-% per Kreis.
This module turns those shares (optionally shrunk toward the ZGB aggregate for small
per-Kreis n) into absolute per-Kreis household counts that sum to the household total
PopulationSim already controls per Kreis, plus the CatalogControl factory. Pure module:
no I/O, no real-data reads (the H4 frame + hh totals are passed in).
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from braunschweig.data.mid.status_by_kreis import STATUS_KEYS

# The KREIS control / census-source column names (one per status class), in STATUS_KEYS order.
STATUS_CONTROL_COLUMNS: tuple[str, ...] = tuple(f"economic_status_{k}" for k in STATUS_KEYS)

# The ZGB aggregate row acts as the shrinkage prior mean (the finest committed
# aggregate above the per-Kreis rows on the same H4 page).
_ZGB_ARS5 = "03ZGB"


def shrunk_status_shares(h4: pd.DataFrame, *, prior_n: float = 0.0) -> pd.DataFrame:
    """Per-Kreis status shares (rows sum to 1), Dirichlet-shrunk toward the ZGB row.

    Each Kreis's raw H4 counts (row-% treated as counts) are combined with a prior of
    ``prior_n`` pseudo-households distributed by the ZGB share vector:
        share_k = (row_k + prior_n * zgb_share) / (sum(row_k) + prior_n).
    ``prior_n = 0`` returns the raw per-Kreis H4 row renormalised (no shrinkage). Higher
    ``prior_n`` pulls small-n Kreise toward the regional mean. The ZGB row itself is
    returned raw. The applied ``prior_n`` MUST be logged by the caller (no silent tuning).
    """
    keys = list(STATUS_KEYS)
    zgb = h4[h4["ars5"].astype(str) == _ZGB_ARS5]
    if zgb.empty:
        raise ValueError(f"H4 frame has no {_ZGB_ARS5} aggregate row for shrinkage prior.")
    zgb_vec = zgb.iloc[0][keys].to_numpy(dtype=float)
    zgb_share = zgb_vec / zgb_vec.sum()

    rows = []
    for _, r in h4.iterrows():
        raw = r[keys].to_numpy(dtype=float)
        total = raw.sum()
        if str(r["ars5"]) == _ZGB_ARS5 or prior_n <= 0.0:
            share = raw / total if total > 0 else zgb_share.copy()
        else:
            share = (raw + prior_n * zgb_share) / (total + prior_n)
        rows.append({"ars5": str(r["ars5"]), **dict(zip(keys, share))})
    return pd.DataFrame(rows)


def _largest_remainder(shares: np.ndarray, total: int) -> np.ndarray:
    """Integer partition of ``total`` proportional to ``shares`` (sums to exactly total)."""
    if total <= 0:
        return np.zeros(len(shares), dtype=int)
    exact = shares * total
    floor = np.floor(exact).astype(int)
    remainder = int(total - floor.sum())
    if remainder > 0:
        order = np.argsort(-(exact - floor))  # largest fractional part first
        floor[order[:remainder]] += 1
    return floor


def status_kreis_count_table(
    h4: pd.DataFrame,
    hh_total_by_ars5: Mapping[str, float],
    *,
    prior_n: float = 0.0,
) -> pd.DataFrame:
    """Per-Kreis integer household counts per status class, summing to round(hh_total[k]).

    Columns: ``ARS_kreis`` + STATUS_CONTROL_COLUMNS. One row per Kreis in
    ``hh_total_by_ars5``. Fail-fast if a Kreis is absent from the H4 frame (a silently
    under-constrained status control would defeat the whole point of the target).
    """
    shares = shrunk_status_shares(h4, prior_n=prior_n).set_index("ars5")
    keys = list(STATUS_KEYS)
    out = []
    for ars5, hh_total in hh_total_by_ars5.items():
        key = str(ars5)
        if key not in shares.index:
            raise ValueError(f"status_kreis_count_table: Kreis {key} absent from the H4 target frame.")
        counts = _largest_remainder(shares.loc[key, keys].to_numpy(dtype=float), int(round(float(hh_total))))
        out.append({"ARS_kreis": key, **dict(zip(STATUS_CONTROL_COLUMNS, counts))})
    return pd.DataFrame(out)
