"""Headline metrics for the typed home matcher: type-match share + size assortativity."""
from __future__ import annotations
import pandas as pd
from scipy.stats import spearmanr

_BTYPE_MAP = {"ein_zweifamilienhaus": "efh_zfh", "mehrfamilienhaus": "mfh", "sonstiges": "sonst"}


def home_match_metrics(placed: pd.DataFrame, buildings_btype: pd.DataFrame) -> dict:
    df = placed.merge(buildings_btype, left_on="home_location_id", right_on="building_id", how="inner")
    hh_btype = df["building_type_3class"].map(_BTYPE_MAP)
    match = (hh_btype == df["btype"]).mean() if len(df) else float("nan")
    if len(df) >= 3 and df["size"].nunique() > 1 and df["household_size"].nunique() > 1:
        rho = spearmanr(df["household_size"], df["size"]).correlation
    else:
        rho = float("nan")
    return {"type_match_share": float(match), "size_assortativity": float(rho),
            "n_households": int(len(placed))}
