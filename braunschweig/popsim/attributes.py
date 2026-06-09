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

from braunschweig.ipf.attributed import derive_socioprofessional_class
from braunschweig.popsim import missing

# MiD P_BKAT (Berufskategorie) -> eqasim/INSEE CS1 occupation class.
# P_BKAT has 6 substantive categories; the CS1 detail is collapsed to this coarse
# crosswalk (documented assumption: no finer occupation distinction is available in
# the standard MiD respondent table). MiD codebook codes:
#   1 Angestellte/Arbeiter (White+Blue collar workers, largest group) -> 6 Worker
#   2 Beamte (Civil servants)                                         -> 5 Employee
#   3 Selbststaendige/Freiberufler (Self-employed, liberal professions)-> 3 Science
#   4 mithelfende Familienangehoerige (Contributing family member)    -> 4 Intermediate
#   5 in Ausbildung (Trainee/apprenticeship, employed)                -> 4 Intermediate
#   6 geringfuegig Beschaeftigte (Marginal employment)               -> 6 Worker
#   7 Nicht berufstaetig (not employed)   -- NOT in map -> fallback
#  95 Nicht zuzuordnen (unclassifiable)   -- NOT in map -> fallback
SPC_BY_P_BKAT = {1: 6, 2: 5, 3: 3, 4: 4, 5: 4, 6: 6}

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


def map_employed(
    persons: pd.DataFrame, *, taet_col: str = "P_TAET", rng=None
) -> pd.DataFrame:
    """Add a boolean ``employed`` from MiD ``P_TAET`` via the uniform missing policy.

    MiD codebook mapping: codes 1..7 (Angestellte/Arbeiter, Beamte, Selbststaendige,
    geringfuegig, Elternzeit, mithelfende Angehoerige, Wehrdienst) -> True; 8..16
    (Ausbildung, Schueler, Student, Rentner, Haushalter, arbeitslos, etc.) -> False.
    No structural design-missing codes apply (employment is asked of all respondents
    above interviewing age). 99 (keine Angabe, item non-response) -> imputed from the
    valid pool within the same age group (alter_gr1) when present, else global pool;
    ``default=False`` (conservative: unknown employment treated as not employed).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    value_map = {code: (code in EMPLOYED_TAET) for code in range(1, 17)}
    spec = missing.AttributeSpec(
        name="employed",
        source_col=taet_col,
        value_map=value_map,
        structural={},
        group_cols=("alter_gr1",) if "alter_gr1" in persons.columns else (),
        default=False,
    )
    out = persons.copy()
    out["employed"], _ = missing.resolve(out, spec, rng=rng)
    out["employed"] = out["employed"].astype(bool)
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
    households: pd.DataFrame, *, status_col: str = "oek_status", rng=None
) -> pd.DataFrame:
    """Add ``economic_status`` (very_low..very_high) from MiD ``oek_status`` via the uniform missing policy.

    MiD codebook: 1 very_low, 2 low, 3 medium, 4 high, 5 very_high. No structural
    design-missing codes apply to household economic status (it is derived by the MiD
    from household income, so it is always either valid or item-nonresponse). 9 (keine
    Angabe) -> imputed from the valid pool within the same household-size group
    (hhgr_gr) when present, else global pool; ``default=None`` (households that cannot
    be classified receive no status; downstream must handle None).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="economic_status",
        source_col=status_col,
        value_map=ECONOMIC_STATUS_BY_OEK_STATUS,
        structural={},
        group_cols=("hhgr_gr",) if "hhgr_gr" in households.columns else (),
        default=None,
    )
    out = households.copy()
    out["economic_status"], _ = missing.resolve(out, spec, rng=rng)
    return out


def map_household_income(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1", rng=None
) -> pd.DataFrame:
    """Add the categorical ``household_income`` class from the MiD income group via the uniform missing policy.

    MiD codebook: hheink_gr1 groups 1..15 map to income class labels (under_500 ..
    over_7000). 99 (keine Angabe) -> imputed from the valid pool within the same
    household-size group (hhgr_gr) when present, else global pool; ``default=None``
    (households that cannot be classified receive no income class).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="household_income",
        source_col=group_col,
        value_map=INCOME_CLASS_BY_GROUP,
        structural={},
        group_cols=("hhgr_gr",) if "hhgr_gr" in households.columns else (),
        default=None,
    )
    out = households.copy()
    out["household_income"], _ = missing.resolve(out, spec, rng=rng)
    return out


def map_household_income_eur(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1", rng=None
) -> pd.DataFrame:
    """Add ``household_income_eur`` from the MiD ``hheink_gr1`` group midpoints via the uniform missing policy.

    MiD codebook: hheink_gr1 groups 1..15 map to EUR midpoints of the codebook range;
    group 15 (>7000 EUR) uses a conservative estimate of 8000.0 EUR. 99 (keine Angabe)
    -> imputed from the valid pool within the same household-size group (hhgr_gr) when
    present, else global pool; ``default=None`` (households that cannot be classified
    receive no EUR value).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="household_income_eur",
        source_col=group_col,
        value_map=INCOME_GROUP_MIDPOINT_EUR,
        structural={},
        group_cols=("hhgr_gr",) if "hhgr_gr" in households.columns else (),
        default=None,
    )
    out = households.copy()
    out["household_income_eur"], _ = missing.resolve(out, spec, rng=rng)
    # Cast to float only where non-None; None remains as NaN in the object series
    out["household_income_eur"] = pd.to_numeric(out["household_income_eur"], errors="coerce")
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
    persons: pd.DataFrame, *, fkarte_col: str = "P_FKARTE", rng=None
) -> pd.DataFrame:
    """Add a boolean ``has_pt_subscription`` from MiD ``P_FKARTE`` via the uniform missing policy.

    MiD codebook mapping: 1 (Einzelfahrschein) -> False, 2 (Mehrfahrtenkarte) ->
    False, 3 (Deutschlandticket) -> True, 4 (Wochen-/Monatskarte ohne Abo) -> True,
    5 (Monatskarte im Abo / Jahreskarte) -> True, 6 (Jobticket / Semesterticket) ->
    True, 7 (sonstiges) -> False, 8 (fahre nie mit OEPNV) -> False.

    Structural design-missing codes (Handbuch Tab. 3, first-digit conventions):
    202 (PAPI Interviewart, fragebogen-bedingt) -> False; 206 (Proxy interview) ->
    False; 402 (Kind unter 14 Jahre, nicht befragt) -> False. These persons genuinely
    have no PT ticket of their own.

    99 (keine Angabe, item non-response) -> imputed from the valid pool within the
    same age group (alter_gr1) when present, else global pool; ``default=False``
    (conservative: unknown PT use treated as no subscription).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    value_map = {code: (code in PT_SUBSCRIPTION_FKARTE) for code in range(1, 9)}
    spec = missing.AttributeSpec(
        name="has_pt_subscription",
        source_col=fkarte_col,
        value_map=value_map,
        structural={202: False, 206: False, 402: False},
        group_cols=("alter_gr1",) if "alter_gr1" in persons.columns else (),
        default=False,
    )
    out = persons.copy()
    out["has_pt_subscription"], _ = missing.resolve(out, spec, rng=rng)
    out["has_pt_subscription"] = out["has_pt_subscription"].astype(bool)
    return out


def map_number_of_bicycles(
    households: pd.DataFrame, *, bikes_col: str = "H_ANZRAD", rng=None
) -> pd.DataFrame:
    """Add ``number_of_bicycles`` from MiD ``H_ANZRAD`` via the uniform missing policy.

    MiD codebook: 0..10 valid bicycle counts. 99 (keine Angabe, item non-response) ->
    imputed from the valid pool within the same household-size group (hhgr_gr) when
    present, else from the global valid pool. Previously, 99 was silently mapped to 0
    (``BIKES_MISSING_CODE``); now it is imputed to avoid a systematic bias toward
    zero-bicycle households. ``BIKES_MISSING_CODE`` is retained as a module constant
    for any downstream code that may still reference it.

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="number_of_bicycles",
        source_col=bikes_col,
        value_map={i: i for i in range(0, 11)},
        structural={},
        group_cols=("hhgr_gr",) if "hhgr_gr" in households.columns else (),
        default=0,
    )
    out = households.copy()
    out["number_of_bicycles"], _ = missing.resolve(out, spec, rng=rng)
    out["number_of_bicycles"] = out["number_of_bicycles"].astype(int)
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


def map_socioprofessional_class(
    persons: pd.DataFrame, *, bkat_col: str = "P_BKAT"
) -> pd.DataFrame:
    """Add ``socioprofessional_class`` (CS1 1-8) from MiD occupation, with fallback.

    Primary path: MiD ``P_BKAT`` (Berufskategorie) is mapped to the eqasim/INSEE CS1
    code via ``SPC_BY_P_BKAT`` (codes 1..6 -> CS1 codes 3..6; ~0 % structural
    missing in the MiD). Codes 7 (nicht berufstaetig) and 95 (nicht zuzuordnen) are
    NOT in the map and fall through to the broad-activity fallback.

    Fallback path: ``derive_socioprofessional_class(employed, age, studies)`` from
    ``braunschweig.ipf.attributed`` is used when (a) the ``P_BKAT`` column is absent
    entirely, or (b) the code for a given person does not resolve via ``SPC_BY_P_BKAT``
    (i.e. code 7 or 95). This is consistent with the IPF path and re-uses the same
    function, so both paths produce values in the same CS1 code space.

    The fallback rate is printed for transparency (CLAUDE.md: no silent fallbacks).
    ``derive_socioprofessional_class`` resets its index to 0-based internally, so the
    returned Series is realigned to the input DataFrame's index before ``.fillna`` to
    avoid a misaligned join.

    Args:
        persons: DataFrame with at least ``employed`` (bool) and ``age`` (int).
            ``studies`` (bool) is used by the fallback if present; defaults to False.
            ``bkat_col`` is optional (absent column -> all fallback).
        bkat_col: name of the MiD Berufskategorie column (default ``P_BKAT``).

    Returns:
        A copy of ``persons`` with the integer ``socioprofessional_class`` column added.
    """
    out = persons.copy()
    studies = out["studies"] if "studies" in out.columns else pd.Series(False, index=out.index)

    # derive_socioprofessional_class resets the index to 0-based internally
    # (pd.Series(...).reset_index(drop=True) at every input), so the returned Series
    # has index 0..N-1. Realign to out.index before .fillna to prevent a silent
    # misaligned join when the input has a non-default index.
    fallback = derive_socioprofessional_class(out["employed"], out["age"], studies)
    fallback = pd.Series(fallback.to_numpy(), index=out.index)

    if bkat_col in out.columns:
        mapped = out[bkat_col].map(SPC_BY_P_BKAT)
        n_primary = int(mapped.notna().sum())
        n_fallback = int(mapped.isna().sum())
        n_total = len(out)
        print(
            f"[braunschweig.popsim.attributes] socioprofessional_class: "
            f"primary (P_BKAT) {n_primary}/{n_total} "
            f"({100.0 * n_primary / max(n_total, 1):.1f}%), "
            f"fallback (broad-activity) {n_fallback}/{n_total} "
            f"({100.0 * n_fallback / max(n_total, 1):.1f}%)."
        )
        out["socioprofessional_class"] = mapped.fillna(fallback).astype(int)
    else:
        print(
            f"[braunschweig.popsim.attributes] socioprofessional_class: "
            f"P_BKAT column absent -> all {len(out)} persons use the broad-activity fallback."
        )
        out["socioprofessional_class"] = fallback.astype(int)
    return out
