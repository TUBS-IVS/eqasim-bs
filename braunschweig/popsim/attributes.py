"""Map MiD 2023 donor attributes to the eqasim schema (popsim_mid enrichment).

The synthetic popsim_mid persons/households carry their MiD donor columns; this
module maps them to the eqasim attribute schema. All code -> meaning mappings are
grounded in the MiD 2023 codebook (Codeplaene B1), documented inline, not invented.

Covered here (the simulation-relevant attributes available from the standard MiD
household/person tables): employment, driving licence, economic status, household
income (EUR), number of cars, and the derived car availability. PT subscription
and bicycle availability need additional MiD columns and are a follow-on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import missing

# MiD P_TAET (Taetigkeit der Person): codes 1..7 are forms of employment
# (Angestellte/Arbeiter, Beamte, Selbststaendige, geringfuegig, Elternzeit-but-
# employed, mithelfende Angehoerige, Wehr-/Freiwilligendienst); 8+ are not employed
# (Ausbildung, Schueler, Student, Rentner, arbeitslos, ...). 99 = keine Angabe.
EMPLOYED_TAET = frozenset({1, 2, 3, 4, 5, 6, 7})

# MiD P_FSCHEIN (Fuehrerscheinbesitz ja/nein): 1 = ja, 2 = nein, 9 = keine Angabe.
LICENSE_YES = 1

# MiD oek_status (oekonomischer Status, 1..5) -> eqasim 5-class economic status.
ECONOMIC_STATUS_BY_OEK_STATUS = {
    1: "very_low",
    2: "low",
    3: "medium",
    4: "high",
    5: "very_high",
}

# MiD hheink_gr1 (monatliches HH-Nettoeinkommen, 15 Gruppen) -> EUR midpoint of the
# codebook range; the open-ended top group (>7000) uses a conservative estimate.
INCOME_GROUP_MIDPOINT_EUR = {
    1: 250.0,    # unter 500
    2: 700.0,    # 500 - 900
    3: 1200.0,   # 900 - 1500
    4: 1750.0,   # 1500 - 2000
    5: 2300.0,   # 2000 - 2600
    6: 2800.0,   # 2600 - 3000
    7: 3300.0,   # 3000 - 3600
    8: 3800.0,   # 3600 - 4000
    9: 4300.0,   # 4000 - 4600
    10: 4800.0,  # 4600 - 5000
    11: 5300.0,  # 5000 - 5600
    12: 5800.0,  # 5600 - 6000
    13: 6300.0,  # 6000 - 6600
    14: 6800.0,  # 6600 - 7000
    15: 8000.0,  # mehr als 7000 (open-ended estimate)
}

# MiD hheink_gr1 group -> a categorical household income class label (the codebook
# EUR range), so household_income (categorical) accompanies household_income_eur.
INCOME_CLASS_BY_GROUP = {
    1: "under_500", 2: "500_900", 3: "900_1500", 4: "1500_2000", 5: "2000_2600",
    6: "2600_3000", 7: "3000_3600", 8: "3600_4000", 9: "4000_4600", 10: "4600_5000",
    11: "5000_5600", 12: "5600_6000", 13: "6000_6600", 14: "6600_7000", 15: "over_7000",
}

# MiD H_ANZAUTO / H_ANZRAD missing code (keine Angabe) -> treated as 0.
CARS_MISSING_CODE = 99
BIKES_MISSING_CODE = 99

# MiD P_FKARTE (Fahrkartenart) codes that grant unlimited local PT rides during
# their validity (= eqasim flatrate / has_pt_subscription): 3 Deutschlandticket,
# 4 Wochen-/Monatskarte ohne Abo, 5 Monatskarte im Abo / Jahreskarte,
# 6 Jobticket / Firmenabo / Semesterticket. 1/2 single/multi-ride, 7 other, 8 never.
PT_SUBSCRIPTION_FKARTE = frozenset({3, 4, 5, 6})


def map_employed(persons: pd.DataFrame, *, taet_col: str = "P_TAET") -> pd.DataFrame:
    """Add a boolean ``employed`` from MiD ``P_TAET`` (codes 1..7 = erwerbstaetig)."""
    out = persons.copy()
    out["employed"] = out[taet_col].isin(EMPLOYED_TAET)
    return out


def map_has_license(
    persons: pd.DataFrame, *, license_col: str = "P_FSCHEIN", rng=None
) -> pd.DataFrame:
    """Add a boolean ``has_license`` from MiD ``P_FSCHEIN`` via the uniform missing policy.

    MiD codebook mapping: 1 = ja -> True, 2 = nein -> False; 202 (Interviewart /
    structural non-applicable), 403 (under-age, structural), 404 (structural non-
    applicable) -> False deterministically; 9 (keine Angabe, item non-response) ->
    imputed from the valid pool within the same age band (alter_gr1) when present,
    else from the global valid pool. The imputation is seeded via ``rng``.

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="has_license",
        source_col=license_col,
        value_map={1: True, 2: False},
        structural={202: False, 403: False, 404: False},
        group_cols=("alter_gr1",) if "alter_gr1" in persons.columns else (),
        default=False,
    )
    out = persons.copy()
    out["has_license"], _ = missing.resolve(out, spec, rng=rng)
    out["has_license"] = out["has_license"].astype(bool)
    return out


def map_economic_status(
    households: pd.DataFrame, *, status_col: str = "oek_status"
) -> pd.DataFrame:
    """Add ``economic_status`` (very_low..very_high) from MiD ``oek_status`` (1..5)."""
    out = households.copy()
    out["economic_status"] = out[status_col].map(ECONOMIC_STATUS_BY_OEK_STATUS)
    return out


def map_household_income_eur(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1"
) -> pd.DataFrame:
    """Add ``household_income_eur`` from the MiD ``hheink_gr1`` group midpoints."""
    out = households.copy()
    out["household_income_eur"] = out[group_col].map(INCOME_GROUP_MIDPOINT_EUR)
    return out


def map_household_income(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1"
) -> pd.DataFrame:
    """Add the categorical ``household_income`` class from the MiD income group."""
    out = households.copy()
    out["household_income"] = out[group_col].map(INCOME_CLASS_BY_GROUP)
    return out


def map_number_of_cars(
    households: pd.DataFrame, *, cars_col: str = "H_ANZAUTO", rng=None
) -> pd.DataFrame:
    """Add ``number_of_cars`` from MiD ``H_ANZAUTO`` via the uniform missing policy.

    MiD codebook: 0..10 valid counts; 99 (keine Angabe, item non-response) ->
    imputed from the valid pool within the same household-size group (hhgr_gr) when
    present, else from the global valid pool. Previously, 99 was silently mapped to
    0 (``CARS_MISSING_CODE``); now it is imputed to avoid a systematic bias toward
    zero-car households. ``CARS_MISSING_CODE`` is retained as a module constant for
    any downstream code that may still reference it.

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="number_of_cars",
        source_col=cars_col,
        value_map={i: i for i in range(0, 11)},
        structural={},
        group_cols=("hhgr_gr",) if "hhgr_gr" in households.columns else (),
        default=0,
    )
    out = households.copy()
    out["number_of_cars"], _ = missing.resolve(out, spec, rng=rng)
    out["number_of_cars"] = out["number_of_cars"].astype(int)
    return out


def derive_car_availability(n_cars: int, n_adults: int) -> str:
    """Derive eqasim car availability {none, some, all} from cars vs. adults.

    No car -> ``none``; at least as many cars as adults -> ``all`` (every adult can
    drive); otherwise ``some``. With no adults a car still covers the household
    (``all``).
    """
    if n_cars <= 0:
        return "none"
    if n_adults <= 0 or n_cars >= n_adults:
        return "all"
    return "some"


def map_has_pt_subscription(
    persons: pd.DataFrame, *, fkarte_col: str = "P_FKARTE"
) -> pd.DataFrame:
    """Add a boolean ``has_pt_subscription`` from MiD ``P_FKARTE`` (flatrate set)."""
    out = persons.copy()
    out["has_pt_subscription"] = out[fkarte_col].isin(PT_SUBSCRIPTION_FKARTE)
    return out


def map_number_of_bicycles(
    households: pd.DataFrame, *, bikes_col: str = "H_ANZRAD"
) -> pd.DataFrame:
    """Add ``number_of_bicycles`` from MiD ``H_ANZRAD`` (the 99 missing code -> 0)."""
    out = households.copy()
    bikes = out[bikes_col].where(out[bikes_col] != BIKES_MISSING_CODE, 0)
    out["number_of_bicycles"] = bikes.fillna(0).astype(int)
    return out


def derive_bicycle_availability(n_bikes: int, n_persons: int) -> str:
    """Derive bicycle availability {none, some, all} from bikes vs. household size.

    No bike -> ``none``; at least one bike per person -> ``all``; otherwise
    ``some`` (bikes are shared by everyone in the household, children included).
    """
    if n_bikes <= 0:
        return "none"
    if n_persons <= 0 or n_bikes >= n_persons:
        return "all"
    return "some"
