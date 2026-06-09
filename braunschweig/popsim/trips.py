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


def mid_time_seconds(wege: pd.DataFrame, hour_col: str, minute_col: str) -> pd.Series:
    """Seconds since midnight from MiD hour + minute columns."""
    return wege[hour_col].astype(float) * 3600.0 + wege[minute_col].astype(float) * 60.0


# Ordered tuple of columns that constitute the eqasim trip schema subset produced by
# build_trip_table.  Downstream stages (data/hts/hts.py fix/validate and
# synthesis/population/activities.py) expect exactly these columns; all other MiD
# Wege columns are carried through as extras.
EQASIM_TRIP_COLUMNS = (
    "person_id",
    "trip_id",
    "departure_time",
    "arrival_time",
    "trip_duration",
    "activity_duration",
    "preceding_purpose",
    "following_purpose",
    "is_first_trip",
    "is_last_trip",
    "mode",
)


def build_trip_table(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
) -> pd.DataFrame:
    """Map MiD Wege onto synthetic persons into the eqasim trip schema (+ extras).

    Produces one row per (synthetic person, MiD trip) with the columns listed in
    ``EQASIM_TRIP_COLUMNS`` so that the eqasim trip-time fix/validation layer
    (``data/hts/hts.py``) and activity-chain construction
    (``synthesis/population/activities.py``) apply unchanged to popsim_mid trips.
    All other MiD Wege columns (``wegkm``, ``W_ANZBEGL``, ``W_BEGL_HH``,
    ``W_ZWDF``, ...) are carried through unchanged as extra columns for later use.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
        One row per unique synthetic person is expected; duplicates on
        ``person_id`` are dropped before the join so that each unique synthetic
        person gets exactly one copy of the donor trip chain (avoids a
        person x wege cross-join that would produce duplicate trip_id values).
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.  All columns are preserved.
    household_col:
        Name of the household-ID column shared by ``persons`` and ``mid_wege``.
    person_col:
        Name of the within-household person-ID column shared by both frames.
    trip_col:
        Name of the within-person trip-sequence column in ``mid_wege`` (used to
        build the unique ``trip_id``).

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, MiD trip) sorted by ``(person_id, trip_col)``,
        containing the full eqasim trip schema (see ``EQASIM_TRIP_COLUMNS``) plus
        all original MiD Wege columns.
    """
    # One trip chain per unique synthetic person; avoids a person x wege cross-join
    # that would produce duplicate trip_id values when the caller passes a persons
    # frame that has already been exploded (e.g. one row per household member).
    persons = persons.drop_duplicates(subset="person_id")

    df = expand_persons_to_trips(
        persons,
        mid_wege,
        household_col=household_col,
        person_col=person_col,
        trip_col=trip_col,
    )
    df = df.sort_values(["person_id", trip_col]).reset_index(drop=True)

    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM").to_numpy()
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM").to_numpy()
    df["trip_duration"] = df["arrival_time"] - df["departure_time"]

    grp = df.groupby("person_id", sort=False)
    df["is_first_trip"] = grp.cumcount() == 0
    df["is_last_trip"] = grp.cumcount(ascending=False) == 0

    # activity_duration: time between arrival of this trip and departure of the next.
    # NaN for the last trip of each person (no subsequent departure).
    next_dep = grp["departure_time"].shift(-1)
    df["activity_duration"] = next_dep - df["arrival_time"]

    # purpose (destination activity) was mapped by expand_persons_to_trips.
    df["following_purpose"] = df["purpose"]
    df["preceding_purpose"] = grp["following_purpose"].shift(1)
    # The first trip of each person departs from home.
    df.loc[df["is_first_trip"], "preceding_purpose"] = "home"

    return df


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
