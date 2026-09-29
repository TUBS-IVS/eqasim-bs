"""Synthetic household-car frame shared by the fleet sampling tests.

One definition instead of four identical copies: several fleet test modules draw
from the same frame, and the session fixtures in ``tests/conftest.py`` sample it
once for all of them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.data.kba import fleet_tables as ft


def make_fleet_cars(n_per_kreis: int = 4000, seed: int = 0) -> pd.DataFrame:
    """``n_per_kreis`` cars per ZGB Kreis at Kreis level (no Gemeinde tilt), with the
    economic status and RegioStaR type drawn uniformly from ``seed``."""
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5:
        for _ in range(n_per_kreis):
            rows.append({
                "economic_status": rng.choice(statuses),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,           # Kreis-level (no Gemeinde tilt)
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)
