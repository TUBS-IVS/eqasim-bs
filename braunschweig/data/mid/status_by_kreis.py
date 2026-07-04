"""Loader for the MiD 2023 H4 economic-status distribution per Kreis.

Backs the economic_status x Kreis PopulationSim control (issue #108). The CSV is
produced by scripts/extract_mid_h4_status_by_kreis.py from the regional-study PDF
(Tabelle H4). Row-% per Kreis over the 5 BMDV status classes.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

# Canonical low->high status order (identical to
# braunschweig.synthesis.population.enriched.ECONOMIC_STATUS_CATEGORIES).
STATUS_KEYS = ("very_low", "low", "medium", "high", "very_high")

_FILENAME = os.path.join("braunschweig", "mid", "mid2023_H4_status_by_kreis.csv")


def load_status_by_kreis(data_path: str) -> pd.DataFrame:
    """Load the per-Kreis H4 status table; fail-fast on schema drift."""
    path = os.path.join(data_path, _FILENAME)
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
    expected = {"kreis", "ars5", "n_weighted", "n_unweighted", *STATUS_KEYS}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(f"{path}: missing columns {sorted(missing)}")
    return df


def status_pmf_by_kreis(df: pd.DataFrame, ars5: str) -> np.ndarray | None:
    """Return P(status | Kreis) as a 5-vector in STATUS_KEYS order, or None.

    Row-% are renormalised to sum to 1 (printed as integer percent; may sum to
    99-101 after rounding)."""
    sub = df[df["ars5"].astype(str) == str(ars5)]
    if sub.empty:
        return None
    vec = sub.iloc[0][list(STATUS_KEYS)].to_numpy(dtype=float)
    total = vec.sum()
    return (vec / total) if total > 0 else None
