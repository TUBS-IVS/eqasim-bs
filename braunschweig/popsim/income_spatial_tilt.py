"""Within-Kreis spatial income tilt from per-cell net cold rent (GAMMA layer).

Literature-grounded (see docs/superpowers/specs/2026-06-14-nettokaltmiete-income-tilt-design.md):
rent carries a weak income signal (area->individual Spearman ~0.32) and housing demand is
income-inelastic, so the rent->income map is concave (beta<1) and bounded. The index is
normalized to a household-weighted mean of 1 within each Kreis, so applying it preserves the
per-Kreis income mean exactly (pure within-Kreis redistribution).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_BETA = 0.3
DEFAULT_CLIP = 0.30


def _normalize_mean_one(index: pd.Series, kreis: pd.Series, weight: pd.Series) -> pd.Series:
    """Scale ``index`` within each Kreis so the weighted mean is exactly 1.0."""
    df = pd.DataFrame({"index": index.to_numpy(float), "kreis": kreis.to_numpy(),
                       "weight": weight.to_numpy(float)})
    means = df.groupby("kreis").apply(
        lambda g: np.average(g["index"], weights=g["weight"]) if g["weight"].sum() > 0 else 1.0
    )
    scale = df["kreis"].map(means).to_numpy()
    scale = np.where(scale == 0, 1.0, scale)
    return pd.Series(df["index"].to_numpy() / scale, index=index.index)


def build_renter_rent_index(cells: pd.DataFrame, *, rent_col: str, kreis_col: str,
                            weight_col: str, beta: float = DEFAULT_BETA) -> pd.DataFrame:
    """Per-cell renter income index = (rent/median_Kreis(rent))**beta, Kreis-mean-1 normalized.

    Cells with missing/non-positive rent get a neutral raw index of 1.0 (no tilt). The
    returned frame is ``cells`` plus a ``renter_income_index`` column.
    """
    out = cells.copy()
    rent = pd.to_numeric(out[rent_col], errors="coerce")
    valid = rent > 0
    # Kreis median over valid rents only.
    med = (rent.where(valid).groupby(out[kreis_col]).transform("median"))
    raw = np.where(valid & (med > 0), (rent / med) ** float(beta), 1.0)
    raw = pd.Series(raw, index=out.index).fillna(1.0)
    missing_rate = float((~valid).mean())
    if missing_rate > 0:
        logger.info("[income_spatial_tilt] renter rent missing/zero in %.1f%% of cells "
                    "-> neutral index", 100 * missing_rate)
    out["renter_income_index"] = _normalize_mean_one(raw, out[kreis_col], out[weight_col])
    return out


def build_owner_income_index(cells: pd.DataFrame, *, quote_col: str, kreis_col: str,
                             weight_col: str, beta: float = DEFAULT_BETA) -> pd.DataFrame:
    """Per-cell owner income index = (quote/median_Kreis(quote))**beta, Kreis-mean-1 normalized.

    Cells with missing/non-positive Eigentuemerquote get a neutral raw index of 1.0 (no tilt).
    Higher ownership share -> more affluent area -> index > 1. The returned frame is ``cells``
    plus an ``owner_income_index`` column.
    """
    out = cells.copy()
    q = pd.to_numeric(out[quote_col], errors="coerce")
    valid = q > 0
    med = q.where(valid).groupby(out[kreis_col]).transform("median")
    raw = np.where(valid & (med > 0), (q / med) ** float(beta), 1.0)
    raw = pd.Series(raw, index=out.index).fillna(1.0)
    out["owner_income_index"] = _normalize_mean_one(raw, out[kreis_col], out[weight_col])
    return out
