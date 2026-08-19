"""Per-1km-cell car/bike ownership targets for PopulationSim (issue #240).

SHAPE  = MiD 2023 B1 conditionals P(count | RegioStaR7 x haustyp)
         (committed aggregates mid2023_{cars,bikes}_by_rs7_haustyp.csv),
         mixed per 1km cell by its Zensus dwelling composition.
LEVEL  = the blended target2026_{number_of_cars,number_of_bicycles} KREIS tables
         (the SAME anchors the KREIS ownership controls consume) -- the per-cell
         priors are IPF-raked per Kreis so the 1km layer aggregates exactly to
         the KREIS layer (asserted; no second anchor truth).
OUTPUT = 9 per-100m-cell columns OWN_CARS_{0,1,2,3plus}_agg +
         OWN_BIKES_{0,1,2,3,4plus}_agg, back-distributed from the raked 1km
         targets proportional to the 100m household totals, so the existing
         per-geography aggregation reproduces the 1km targets bit-for-bit.

The per-cell prior is a MODELLED reference (ASSUMPTION: the national MiD
ownership <-> RS7 x building-type relationship holds spatially within the ZGB;
the LEVEL deliberately does not rest on it). See the #240 ADR.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RS7_CLASSES = tuple(range(71, 78))
HAUSTYP_CLASSES = (1, 2, 3, 4)

CARS_CATEGORIES = ("0", "1", "2", "3plus")
BIKES_CATEGORIES = ("0", "1", "2", "3", "4plus")
CARS_COLUMNS = tuple(f"OWN_CARS_{c}_agg" for c in CARS_CATEGORIES)
BIKES_COLUMNS = tuple(f"OWN_BIKES_{c}_agg" for c in BIKES_CATEGORIES)
OWNERSHIP_COLUMNS = CARS_COLUMNS + BIKES_COLUMNS

_CARS_SHARE_COLUMNS = tuple(f"cars_{c}" for c in CARS_CATEGORIES)
_BIKES_SHARE_COLUMNS = tuple(f"bikes_{c}" for c in BIKES_CATEGORIES)

# Cleaned prepared-cell dwelling columns -> MiD haustyp class, per the repo's
# building_type_3class convention (braunschweig.popsim.donor: 1 = EFH/ZFH,
# 2 = MFH 3-12 dwellings, 3 = Geschosswohnungsbau 13+, 4 = sonstiges).
_DW = "_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"
DWELLING_COLUMNS_BY_HAUSTYP: dict[int, tuple[str, ...]] = {
    1: ("FreiEFH" + _DW, "EFH_DHH" + _DW, "EFH_Reihenhaus" + _DW,
        "Freist_ZFH" + _DW, "ZFH_DHH" + _DW, "ZFH_Reihenhaus" + _DW),
    2: ("MFH_3bis6Wohnungen" + _DW, "MFH_7bis12Wohnungen" + _DW),
    3: ("MFH_13undmehrWohnungen" + _DW,),
    4: ("AndererGebaeudetyp" + _DW,),
}
DWELLING_INPUT_COLUMNS: tuple[str, ...] = tuple(
    c for cols in DWELLING_COLUMNS_BY_HAUSTYP.values() for c in cols)


def _load_one_conditional(data_path: str, filename: str, share_columns: tuple[str, ...]) -> pd.DataFrame:
    path = f"{data_path}/braunschweig/mid/{filename}"
    df = pd.read_csv(path, comment="#")
    missing = [c for c in ("rs7", "ht", *share_columns, "n_unweighted") if c not in df.columns]
    if missing:
        raise ValueError(f"{filename}: missing columns {missing}.")
    df = df.astype({"rs7": int, "ht": int}).set_index(["rs7", "ht"]).sort_index()
    expected = [(r, h) for r in RS7_CLASSES for h in HAUSTYP_CLASSES]
    absent = [k for k in expected if k not in df.index]
    if absent:
        raise ValueError(f"{filename}: conditional grid incomplete, missing (rs7, ht) cells {absent}.")
    sums = df[list(share_columns)].sum(axis=1)
    bad = sums[(sums - 1.0).abs() > 1e-6]
    if not bad.empty:
        raise ValueError(
            f"{filename}: share rows must sum to 1 (tolerance 1e-6); offending (rs7, ht): "
            f"{bad.index.tolist()} with sums {bad.round(6).tolist()}.")
    if (df[list(share_columns)] < 0).any().any():
        raise ValueError(f"{filename}: negative share values.")
    return df


def load_ownership_conditionals(data_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load + validate the two committed RS7 x haustyp conditionals, indexed by (rs7, ht)."""
    cars = _load_one_conditional(data_path, "mid2023_cars_by_rs7_haustyp.csv", _CARS_SHARE_COLUMNS)
    bikes = _load_one_conditional(data_path, "mid2023_bikes_by_rs7_haustyp.csv", _BIKES_SHARE_COLUMNS)
    return cars, bikes
