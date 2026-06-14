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

from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
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

# MiD P_TAET (Taetigkeit der Person): codes 1..7 are forms of employment, 8..17 are
# not employed. Source: MiD 2023 Codeplan B1 (Personen sheet, variable P_TAET):
#   1  Angestellte/r, Arbeiter/in (auch Zeit-/Berufssoldat/in)       -> employed
#   2  Beamtin/Beamter                                               -> employed
#   3  Selbststaendige/r, Freiberufler/in                            -> employed
#   4  geringfuegig erwerbstaetig (auch 520-Euro-Job)                -> employed
#   5  erwerbstaetig, aber momentan in Elternzeit/Pflegezeit/etc.    -> employed
#   6  unbezahlt mithelfende/r Familienangehoerige/r im Betrieb      -> employed
#   7  freiwilliger Wehrdienst / Bundesfreiwilligendienst (FSJ/FOEJ) -> employed
#   8  in Ausbildung            9  Schueler/in        10 Student/in   -> not employed
#  11  Rentner/in/Pensionaer/in 12 arbeitslos         13 Hausfrau/-mann
#  14  dauerhaft erwerbsunfaehig 15 Kind (zu Hause)    16 Kind (Kindergarten/Tagesbetreuung)
#  17  sonstiges (other/miscellaneous activity)                      -> not employed
#  99  keine Angabe (item non-response)                              -> imputed
EMPLOYED_TAET = frozenset({1, 2, 3, 4, 5, 6, 7})

# MiD P_TAET codes that indicate the person is in education (Ausbildung, Schueler,
# Student): 8 = in Ausbildung, 9 = Schueler/in (einschl. Vorschule), 10 = Student/in.
# These map to studies=True; all other codes (employment 1-7, Rentner/arbeitslos/
# Hausfrau 11-16, 17 sonstiges, 99 k.A.) map to studies=False (conservative: unknown
# treated as not in education). Source: MiD 2023 Codeplan B1 (Personen, P_TAET).
STUDIES_TAET = frozenset({8, 9, 10})

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

# MiD P_FKARTE code -> categorical ticket-type string (matches PT_TICKET_CATEGORIES
# order in braunschweig.data.mid.reference_tables). Codes 1-8 are exhaustive;
# code 8 (fahre nie) is the never-travels category used for structural missings.
FKARTE_TO_CATEGORY: dict[int, str] = {
    1: "einzelfahrschein",       # single ticket
    2: "mehrfachkarte",          # multi-ride card
    3: "deutschlandticket",      # Deutschlandticket
    4: "wochen_monat_ohne_abo",  # weekly/monthly without subscription
    5: "monat_abo_jahreskarte",  # monthly subscription / annual pass
    6: "jobticket_semesterticket",  # job ticket / semester ticket
    7: "anderes",                # other
    8: "fahre_nie",              # never travels by PT
}

# The never-travels category is used for the structural under-14 floor
# (code 402, children under the MiD PT-subscription basis age, not interviewed)
# and as the default for persons whose code cannot be resolved. Adult
# interview-mode / proxy coverage codes (202/206) are imputed, not forced here.
PT_TICKET_NEVER = "fahre_nie"


def map_employed(
    persons: pd.DataFrame, *, taet_col: str = "P_TAET", rng=None
) -> pd.DataFrame:
    """Add a boolean ``employed`` from MiD ``P_TAET`` via the uniform missing policy.

    MiD codebook mapping: codes 1..7 (Angestellte/Arbeiter, Beamte, Selbststaendige,
    geringfuegig, Elternzeit, mithelfende Angehoerige, freiwilliger Wehr-/Bundes-
    freiwilligendienst) -> True; 8..17 (Ausbildung, Schueler, Student, Rentner,
    arbeitslos, Hausfrau/-mann, erwerbsunfaehig, Kind, sonstiges) -> False. The full
    substantive code range 1..17 is enumerated (the real MiD Personen table carries
    P_TAET=17 "sonstiges" for ~4,043 persons; MiD 2023 Codeplan B1, Personen, P_TAET).
    No structural design-missing codes apply (employment is asked of all respondents
    above interviewing age). 99 (keine Angabe, item non-response) -> imputed from the
    valid pool within the same age group (alter_gr1) when present, else global pool;
    ``default=False`` (conservative: unknown employment treated as not employed).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    # Enumerate the full substantive P_TAET range 1..17 (range(1, 18)) so that code 17
    # "sonstiges" is mapped explicitly to False instead of raising as unenumerated.
    value_map = {code: (code in EMPLOYED_TAET) for code in range(1, 18)}
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


def map_studies(
    persons: pd.DataFrame, *, taet_col: str = "P_TAET"
) -> pd.DataFrame:
    """Add a boolean ``studies`` column derived from MiD ``P_TAET``.

    MiD codebook mapping (P_TAET = Taetigkeit der Person):
      8  in Ausbildung               -> True
      9  Schueler/in (einschl. Vorschule) -> True
      10 Student/in                  -> True
      All other codes (1-7 employment, 11-16 Rentner/arbeitslos/Hausfrau/Kind,
      17 sonstiges, 99 k.A.) -> False.

    ``map_studies`` uses ``Series.isin(STUDIES_TAET)`` directly (not the
    ``missing.resolve`` policy), so unenumerated codes such as 17 "sonstiges"
    cannot raise here -- they simply evaluate to ``False``.

    99 (keine Angabe, item non-response) is conservatively treated as False
    (not in education). P_TAET has near-complete coverage in the MiD
    (structural design-missing codes apply only to children not interviewed,
    who are not in the MiD respondent table).

    The ``studies`` flag is used by ``map_socioprofessional_class`` as a
    secondary signal in the broad-activity fallback path
    (``derive_socioprofessional_class``). It must be derived before
    ``map_socioprofessional_class`` is called.

    Parameters
    ----------
    persons:
        DataFrame with a ``P_TAET`` column (MiD Taetigkeit der Person).
    taet_col:
        Name of the MiD P_TAET column (default ``P_TAET``).

    Returns
    -------
    pandas.DataFrame
        A copy of ``persons`` with a boolean ``studies`` column added.
    """
    out = persons.copy()
    taet = out[taet_col]
    out["studies"] = taet.isin(STUDIES_TAET).astype(bool)
    return out


def map_has_license(
    persons: pd.DataFrame, *, license_col: str = "P_FSCHEIN", rng=None
) -> pd.DataFrame:
    """Add a boolean ``has_license`` from MiD ``P_FSCHEIN`` via the uniform missing policy.

    MiD codebook mapping: 1 = ja -> True, 2 = nein -> False. Only 403 (under legal
    driving age) is a legitimate deterministic structural False. The codes 202
    (Interviewart / interview mode) and 404 (coverage / non-applicable on adults)
    are NOT "no licence" -- they are coverage / interview-mode design-missings that
    must be IMPUTED from comparable adult respondents, not forced to False. Forcing
    them to False (the previous behaviour) put ~42.9 % of donor persons on the
    deterministic-False path and depressed the licence share to ~52 % vs the ~73 %
    P17.1 reference. They are therefore declared in ``impute_codes`` (treated as
    item non-response). 9 (keine Angabe, item non-response) and 202/404 are all
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
        structural={403: False},          # under legal age: deterministic False
        impute_codes=(202, 404),          # interview-mode / coverage on adults: impute
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
    only 402 (Kind unter 14 Jahre, nicht befragt) is a legitimate deterministic
    structural False -- it is the under-14 / PT-subscription basis-age floor (mirrors
    the legacy ``braunschweig.minimum_age.pt_subscription`` rule): children below the
    MiD PT-subscription basis age genuinely have no PT ticket of their own. The codes
    202 (PAPI Interviewart, fragebogen-bedingt) and 206 (Erwachsener ab 14,
    Proxy/Stellvertreter) are first-digit-2 interview-mode / coverage design-missings
    on persons of subscription age; they are NOT "no ticket" and must be IMPUTED from
    comparable adult respondents, not forced to False. Forcing them to False (the
    previous behaviour) put adult proxy persons (P_FKARTE=206: ~24.6k MiD donors) on
    the deterministic-False path and biased the subscription share downward. They are
    therefore declared in ``impute_codes`` (treated as item non-response).

    99 (keine Angabe, item non-response) and 202/206 are all imputed from the valid
    pool within the same age group (alter_gr1) when present, else global pool;
    ``default=False`` (conservative: unknown PT use treated as no subscription).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    value_map = {code: (code in PT_SUBSCRIPTION_FKARTE) for code in range(1, 9)}
    spec = missing.AttributeSpec(
        name="has_pt_subscription",
        source_col=fkarte_col,
        value_map=value_map,
        structural={402: False},          # Kind unter 14: deterministic under-14 floor
        impute_codes=(202, 206),          # interview-mode / adult proxy coverage: impute
        group_cols=("alter_gr1",) if "alter_gr1" in persons.columns else (),
        default=False,
    )
    out = persons.copy()
    out["has_pt_subscription"], _ = missing.resolve(out, spec, rng=rng)
    out["has_pt_subscription"] = out["has_pt_subscription"].astype(bool)
    return out


def map_pt_subscription_type(
    persons: pd.DataFrame, *, fkarte_col: str = "P_FKARTE", rng=None
) -> pd.DataFrame:
    """Add a categorical ``pt_subscription_type`` from MiD ``P_FKARTE`` via the uniform missing policy.

    MiD codebook mapping (``FKARTE_TO_CATEGORY``):
    1 -> ``einzelfahrschein``, 2 -> ``mehrfachkarte``, 3 -> ``deutschlandticket``,
    4 -> ``wochen_monat_ohne_abo``, 5 -> ``monat_abo_jahreskarte``,
    6 -> ``jobticket_semesterticket``, 7 -> ``anderes``, 8 -> ``fahre_nie``.

    Structural design-missing codes: only 402 (Kind unter 14, nicht befragt) is the
    legitimate deterministic ``"fahre_nie"`` -- the under-14 / PT-subscription basis-age
    floor (mirrors the legacy ``braunschweig.minimum_age.pt_subscription`` rule).
    Children below the MiD PT-subscription basis age genuinely have no PT ticket of
    their own. The codes 202 (PAPI interview mode, form-dependent) and 206 (Erwachsener
    ab 14, Proxy/Stellvertreter) are first-digit-2 interview-mode / coverage
    design-missings on persons of subscription age; they are NOT "never travels" and
    must be IMPUTED from comparable adult respondents, not forced to ``"fahre_nie"``.
    They are therefore declared in ``impute_codes`` (treated as item non-response).

    99 (keine Angabe, item non-response) and 202/206 are all imputed from the valid
    pool within the same age group (``alter_gr1``) when present, else global pool;
    categorical default ``PT_TICKET_NEVER`` = ``"fahre_nie"`` (conservative: unknown
    PT use treated as never travelling by PT).

    The output category is constrained to ``PT_TICKET_CATEGORIES`` from
    ``braunschweig.data.mid.reference_tables`` (import validated at module load).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="pt_subscription_type",
        source_col=fkarte_col,
        value_map=FKARTE_TO_CATEGORY,
        structural={402: PT_TICKET_NEVER},   # Kind unter 14: deterministic under-14 floor
        impute_codes=(202, 206),             # interview-mode / adult proxy coverage: impute
        group_cols=("alter_gr1",) if "alter_gr1" in persons.columns else (),
        default=PT_TICKET_NEVER,
    )
    out = persons.copy()
    out["pt_subscription_type"], _ = missing.resolve(out, spec, rng=rng)
    # Ensure the column contains only valid PT_TICKET_CATEGORIES values.
    invalid = ~out["pt_subscription_type"].isin(PT_TICKET_CATEGORIES)
    if invalid.any():
        n_invalid = int(invalid.sum())
        raise ValueError(
            f"[braunschweig.popsim.attributes] map_pt_subscription_type: "
            f"{n_invalid} persons have a pt_subscription_type not in PT_TICKET_CATEGORIES: "
            f"{sorted(out.loc[invalid, 'pt_subscription_type'].unique())}. "
            f"Check that FKARTE_TO_CATEGORY is consistent with PT_TICKET_CATEGORIES."
        )
    out["pt_subscription_type"] = out["pt_subscription_type"].astype("string")
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


def map_housing_tenure(
    households: pd.DataFrame, *, miete_col: str = "H_MIETE"
) -> pd.DataFrame:
    """Add ``housing_tenure`` (owner/renter/unknown) from MiD ``H_MIETE``.

    MiD H_MIETE codebook (MiD 2023, Codeplan B1, Haushalte):
      1 -> Mieter   (renter, incl. Untermiete, genossenschaftliches Wohnen)
      2 -> Eigentuemer (owner-occupier)
      3, 9, 309 -> ambiguous / keine Angabe -> "unknown" (not assigned to either
                   control; consistent with the Tier-2 control expressions which
                   match only H_MIETE == 1 and H_MIETE == 2)
    The column is absent for ENTD frames (no tenure flag); the function then
    skips derivation and returns the frame unchanged with no column added.
    Missing rate is logged for transparency (no silent fallback).

    Parameters
    ----------
    households:
        MiD donor households frame with at least ``miete_col`` when available.
    miete_col:
        Name of the MiD tenure column (default ``H_MIETE``).

    Returns
    -------
    pandas.DataFrame
        A copy with ``housing_tenure`` column added if ``miete_col`` is present.
        Absent column -> returned unchanged (ENTD path; no column added).
    """
    import logging
    logger = logging.getLogger(__name__)

    out = households.copy()
    if miete_col not in out.columns:
        logger.info(
            "[popsim.attributes] map_housing_tenure: %r absent from households "
            "(ENTD path?); housing_tenure not derived.", miete_col,
        )
        return out

    val = pd.to_numeric(out[miete_col], errors="coerce")
    tenure = pd.Series("unknown", index=out.index, dtype=object)
    tenure[val == 2] = "owner"
    tenure[val == 1] = "renter"
    out["housing_tenure"] = tenure

    n_unknown = int((tenure == "unknown").sum())
    n_total = len(out)
    if n_unknown:
        logger.info(
            "[popsim.attributes] map_housing_tenure: %d/%d households have "
            "housing_tenure='unknown' (H_MIETE not in {1, 2}; excluded from "
            "tenure control but present on frame for reference).",
            n_unknown, n_total,
        )
    return out


# MiD haustyp -> building_type_3class label.
# Coding (MiD 2023 Codeplan B1, Haushalte, haustyp):
#   1  Ein-/Zweifamilienhaus (EFH/ZFH, freistehend/DHH/Reihenhaus) -> ein_zweifamilienhaus
#   2  Mehrfamilienhaus (3-12 Wohnungen)                            -> mehrfamilienhaus
#   3  Geschosswohnungsbau (13+ Wohnungen, grouped with MFH)        -> mehrfamilienhaus
#   4  Sonstiges / gemischte Nutzung                                -> sonstiges
#  95  nicht zutreffend (n.z.)                                      -> None/NaN (excluded)
BUILDING_TYPE_3CLASS_BY_HAUSTYP = {
    1: "ein_zweifamilienhaus",
    2: "mehrfamilienhaus",
    3: "mehrfamilienhaus",
    4: "sonstiges",
    # 95 intentionally absent -> NaN in map() -> excluded from all three classes
}


def map_building_type_3class(
    households: pd.DataFrame, *, haustyp_col: str = "haustyp"
) -> pd.DataFrame:
    """Add ``building_type_3class`` (3-class Zensus Gebaeudetyp) from MiD ``haustyp``.

    Collapses the MiD 4-code building-type flag to the 3 popsim control classes:
      ein_zweifamilienhaus, mehrfamilienhaus, sonstiges.
    Code 95 (nicht zutreffend) yields NaN and is excluded from all three classes
    (consistent with the Tier-2 control expressions: no haustyp==95 row matches
    any building_type expression).
    Missing rate is logged for transparency (no silent fallback).
    The column is absent for ENTD frames; the function then returns the frame
    unchanged.

    Parameters
    ----------
    households:
        MiD donor households frame with at least ``haustyp_col`` when available.
    haustyp_col:
        Name of the MiD building-type column (default ``haustyp``).

    Returns
    -------
    pandas.DataFrame
        A copy with ``building_type_3class`` column added if ``haustyp_col`` is present.
        Absent column -> returned unchanged (ENTD path; no column added).
    """
    import logging
    logger = logging.getLogger(__name__)

    out = households.copy()
    if haustyp_col not in out.columns:
        logger.info(
            "[popsim.attributes] map_building_type_3class: %r absent from households "
            "(ENTD path?); building_type_3class not derived.", haustyp_col,
        )
        return out

    out["building_type_3class"] = out[haustyp_col].map(BUILDING_TYPE_3CLASS_BY_HAUSTYP)
    n_nan = int(out["building_type_3class"].isna().sum())
    n_total = len(out)
    if n_nan:
        logger.info(
            "[popsim.attributes] map_building_type_3class: %d/%d households have "
            "building_type_3class=NaN (haustyp=95 'nicht zutreffend'; excluded from "
            "all three building_type control classes).",
            n_nan, n_total,
        )
    return out


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
