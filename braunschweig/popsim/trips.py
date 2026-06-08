"""Map MiD Wege (trips) onto the synthetic persons -> eqasim activity chains.

Each synthetic popsim_mid person is a copy of a MiD donor person ``(H_ID, P_ID)``;
the donor's MiD Wege (trips) become that person's trip chain. This module maps the
MiD trip purpose and mode to the eqasim vocabulary and joins the donor Wege onto
the synthetic persons. Codes are grounded in the MiD 2023 codebook (Wege sheet),
documented inline, not invented.

The activity-chain construction proper (building the home/work/... activity
sequence with times and coordinates between consecutive trips) builds on the
trip records produced here.
"""

from __future__ import annotations

import pandas as pd

# MiD W_ZWECK (Wegezweck) -> eqasim activity type at the trip destination.
# 1 Arbeit, 2 dienstlich -> work; 3 Ausbildung/Schule, 11 Schule, 12 Kita -> education;
# 4 Einkauf -> shop; 7 Freizeit -> leisure; 8 nach Hause, 9 Rueckweg -> home;
# 5 private Erledigungen, 6 Bringen/Holen, 10 anderer Zweck -> other.
PURPOSE_BY_W_ZWECK = {
    1: "work",
    2: "work",
    3: "education",
    4: "shop",
    5: "other",
    6: "other",
    7: "leisure",
    8: "home",
    9: "home",
    10: "other",
    11: "education",
    12: "education",
}
DEFAULT_PURPOSE = "other"

# MiD hvm (Hauptverkehrsmittel) -> eqasim mode.
# 1 zu Fuss -> walk; 2 Fahrrad -> bike; 3 MIV-Mitfahrer -> car_passenger;
# 4 MIV-Fahrer -> car; 5 OEPV -> pt; 9 keine Angabe -> walk (fallback).
MODE_BY_HVM = {
    1: "walk",
    2: "bike",
    3: "car_passenger",
    4: "car",
    5: "pt",
    9: "walk",
}
DEFAULT_MODE = "walk"


def map_purpose(wege: pd.DataFrame, *, zweck_col: str = "W_ZWECK") -> pd.DataFrame:
    """Add the eqasim activity ``purpose`` from MiD ``W_ZWECK``."""
    out = wege.copy()
    out["purpose"] = out[zweck_col].map(PURPOSE_BY_W_ZWECK).fillna(DEFAULT_PURPOSE)
    return out


def map_mode(wege: pd.DataFrame, *, hvm_col: str = "hvm") -> pd.DataFrame:
    """Add the eqasim ``mode`` from MiD ``hvm``."""
    out = wege.copy()
    out["mode"] = out[hvm_col].map(MODE_BY_HVM).fillna(DEFAULT_MODE)
    return out


def expand_persons_to_trips(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
) -> pd.DataFrame:
    """Join the donor MiD Wege onto the synthetic persons -> one row per trip.

    Each synthetic person (``person_id``, referencing donor ``(H_ID, P_ID)``) gets
    the donor person's trips, with the purpose and mode mapped to the eqasim
    vocabulary and a unique ``trip_id`` (``<person_id>_<W_ID>``). Persons whose
    donor has no Wege are dropped (they make no trips).

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    """
    wege = map_mode(map_purpose(mid_wege))
    merged = persons.merge(
        wege, on=[household_col, person_col], how="inner", suffixes=("", "_weg")
    )
    merged["trip_id"] = (
        merged["person_id"].astype(str) + "_" + merged[trip_col].astype(str)
    )
    return merged.reset_index(drop=True)
