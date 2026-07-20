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

import logging

import numpy as np
import pandas as pd

from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
from braunschweig.ipf.attributed import derive_socioprofessional_class
from braunschweig.popsim import missing

logger = logging.getLogger(__name__)

# NOTE (issue #167): there is NO occupation ("Berufskategorie") variable in the
# standard MiD respondent table. P_BKAT is "Umfang der Erwerbstaetigkeit"
# (employment EXTENT; see EMPLOYMENT_STATUS_BY_P_BKAT below), NOT an occupation
# code. The former SPC_BY_P_BKAT crosswalk mis-read P_BKAT as an occupation and is
# removed; socioprofessional_class is derived from broad activity status via
# braunschweig.ipf.attributed.derive_socioprofessional_class (see
# map_socioprofessional_class), which is the eqasim/IPF path and documents that no
# occupation data exists upstream of the HTS in this fork.

# MiD P_TAET (Taetigkeit der Person): MiD official `erwerb` definition (Erwerbstätigkeit
# ja/nein per MiD methodology). Source: MiD 2023 Codeplan B1 (Personen sheet, P_TAET):
#   1  Angestellte/r, Arbeiter/in (auch Zeit-/Berufssoldat/in)       -> employed
#   2  Beamtin/Beamter                                               -> employed
#   3  Selbststaendige/r, Freiberufler/in                            -> employed
#   4  geringfuegig erwerbstaetig (auch 520-Euro-Job)                -> employed
#   5  erwerbstaetig, aber momentan in Elternzeit/Pflegezeit/etc.    -> NOT employed (Elternzeit)
#   6  unbezahlt mithelfende/r Familienangehoerige/r im Betrieb      -> employed
#   7  freiwilliger Wehrdienst / Bundesfreiwilligendienst (FSJ/FOEJ) -> NOT employed (FSJ)
#   8  in Ausbildung (Auszubildende/r)                               -> employed (Azubi)
#   9  Schueler/in        10 Student/in                              -> not employed
#  11  Rentner/in/Pensionaer/in 12 arbeitslos         13 Hausfrau/-mann
#  14  dauerhaft erwerbsunfaehig 15 Kind (zu Hause)    16 Kind (Kindergarten/Tagesbetreuung)
#  17  sonstiges (other/miscellaneous activity)                      -> not employed
#  99  keine Angabe (item non-response)                              -> imputed
# MiD official `erwerb` = Erwerbstätigkeit ja/nein (inkl. Auszubildende). P_TAET 1,2,3,4,6,8.
# (5 Elternzeit and 7 FSJ/Wehrdienst are NOT erwerbstätig per the MiD `erwerb` variable.)
EMPLOYED_TAET = frozenset({1, 2, 3, 4, 6, 8})

# MiD P_BKAT (Umfang der Erwerbstaetigkeit; MiD 2023 Codeplan B1, Personen col
# 121; VERIFIED against the raw MiD2023_Personen.csv cross-tab with `erwerb`:
# codes 1-6 all erwerb=1, code 7 erwerb=0). P9's seven reference columns are the
# P_BKAT value labels 1..7 verbatim, so this is an exact apples-to-apples match
# with NO P_TAET overlay needed -- code 6 IS in Ausbildung:
#   1 Vollzeit erwerbstaetig                      -> vollzeit
#   2 Teilzeit (18-<35 h/week)                    -> teilzeit
#   3 geringfuegig (11-<18 h/week)                -> geringfuegig
#   4 sonstiger Erwerbsumfang                     -> sonstiges
#   5 erwerbstaetig ohne Angabe zum Umfang        -> erwerbstaetig_unspec
#   6 in Ausbildung                               -> in_ausbildung
#   7 nicht erwerbstaetig                         -> nicht_erwerbstaetig
#   9 keine Angabe (item non-response)            -> imputed (missing policy)
EMPLOYMENT_STATUS_BY_P_BKAT = {
    1: "vollzeit", 2: "teilzeit", 3: "geringfuegig", 4: "sonstiges",
    5: "erwerbstaetig_unspec", 6: "in_ausbildung", 7: "nicht_erwerbstaetig",
}
# Derived from EMPLOYMENT_STATUS_BY_P_BKAT (not re-listed literally) so the two
# stay in sync by construction; dict insertion order (Python 3.7+) preserves the
# codebook code order 1..7 above.
EMPLOYMENT_STATUS_CATEGORIES = tuple(EMPLOYMENT_STATUS_BY_P_BKAT.values())

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


def imputation_group_cols(
    frame: pd.DataFrame, base_col: str, *, rs7_conditioning: bool = True,
    rs7_col: str = "RegioStaR7",
) -> tuple:
    """Conditioning-group columns for item-nonresponse imputation (issue #131).

    Returns ``(base_col,)`` as before, extended by ``RegioStaR7`` when the frame
    carries it and ``rs7_conditioning`` is on (default, project rule): licence /
    PT subscription / cars / income of a rural household are then imputed from a
    pool of the SAME urban-rural region type instead of a national pool including
    big-city respondents. On donor household frames RS7 is the survey home
    region; on expanded synthetic persons it is the PLACED home cell's RS7
    (``stage.join_cell_attributes``). RS7 is only appended IN ADDITION to the
    base column -- frames without the base column keep the old empty grouping
    (minimal deviation from the previous per-mapper guards). Thin (base, RS7)
    cells fall back to the global pool inside ``missing.resolve``, which counts
    and logs that rate (``MissingReport.n_group_fallback``).
    """
    if base_col not in frame.columns:
        return ()
    cols = [base_col]
    if rs7_conditioning and rs7_col in frame.columns:
        cols.append(rs7_col)
    return tuple(cols)


def map_employed(
    persons: pd.DataFrame, *, taet_col: str = "P_TAET", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add a boolean ``employed`` from MiD ``P_TAET`` via the uniform missing policy.

    MiD `erwerb` definition (Erwerbstätigkeit ja/nein): codes {1, 2, 3, 4, 6, 8}
    (Angestellte/Arbeiter, Beamte, Selbststaendige, geringfuegig, mithelfende
    Angehoerige, Auszubildende) -> True. Codes 5 (Elternzeit) and 7 (FSJ/Wehrdienst)
    are NOT erwerbstätig per the MiD ``erwerb`` variable and map to False. Codes
    9..17 (Schueler, Student, Rentner, arbeitslos, Hausfrau/-mann, erwerbsunfaehig,
    Kind, sonstiges) -> False. The full substantive code range 1..17 is enumerated
    (the real MiD Personen table carries P_TAET=17 "sonstiges" for ~4,043 persons;
    MiD 2023 Codeplan B1, Personen, P_TAET).
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
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default=False,
    )
    out = persons.copy()
    out["employed"], _ = missing.resolve(out, spec, rng=rng)
    out["employed"] = out["employed"].astype(bool)
    return out


def map_employment_status(
    persons: pd.DataFrame, *, bkat_col: str = "P_BKAT", taet_col: str = "P_TAET",
    rng=None, rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add a categorical ``employment_status`` (P9 taxonomy) from MiD ``P_BKAT``.

    P_BKAT (Umfang der Erwerbstaetigkeit) maps 1:1 onto the P9 columns via
    ``EMPLOYMENT_STATUS_BY_P_BKAT`` -- code 6 IS ``in_ausbildung`` directly, no
    overlay from another column is needed (verified against the MiD 2023
    Codeplan B1 and the raw MiD2023_Personen.csv cross-tab with `erwerb`).
    Missing / code-9 P_BKAT (keine Angabe) is imputed from the valid pool within
    the same age group via the uniform missing policy (rate logged; no silent
    fallback). Additive: the boolean ``employed`` is untouched. This is now the
    seed column for the per-Kreis ``employment_status`` popsim control (issue
    #172), still also written for analysis/validation.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    value_map = dict(EMPLOYMENT_STATUS_BY_P_BKAT)
    spec = missing.AttributeSpec(
        name="employment_status",
        source_col=bkat_col,
        value_map=value_map,
        structural={},
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default="nicht_erwerbstaetig",
    )
    out = persons.copy()
    # taet_col is kept only for API symmetry with map_employed; P_BKAT code 6 IS
    # in_ausbildung directly, so this function does not read P_TAET at all.
    out["employment_status"], _ = missing.resolve(out, spec, rng=rng)
    out["employment_status"] = out["employment_status"].astype(str)

    # Observability (no silent fallback): the boolean `employed` (from P_TAET)
    # and `employment_status` (from P_BKAT) are DISTINCT MiD variables; log how
    # often they agree so a low rate surfaces as a data-quality signal.
    if "employed" in out.columns:
        employed_side = out["employment_status"].isin(
            [c for c in EMPLOYMENT_STATUS_CATEGORIES if c != "nicht_erwerbstaetig"])
        agree = float((employed_side == out["employed"].astype(bool)).mean())
        _log = logger.warning if agree < 0.9 else logger.info
        _log("[attributes] employment_status vs employed agreement: %.1f%% "
             "(distinct MiD vars P_BKAT vs P_TAET)", 100.0 * agree)

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
    persons: pd.DataFrame, *, license_col: str = "P_FSCHEIN", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default=False,
    )
    out = persons.copy()
    out["has_license"], _ = missing.resolve(out, spec, rng=rng)
    out["has_license"] = out["has_license"].astype(bool)
    return out


def map_economic_status(
    households: pd.DataFrame, *, status_col: str = "oek_status", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning),
        default=None,
    )
    out = households.copy()
    out["economic_status"], _ = missing.resolve(out, spec, rng=rng)
    return out


def map_household_income(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning),
        default=None,
    )
    out = households.copy()
    out["household_income"], _ = missing.resolve(out, spec, rng=rng)
    return out


def map_household_income_eur(
    households: pd.DataFrame, *, group_col: str = "hheink_gr1", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning),
        default=None,
    )
    out = households.copy()
    out["household_income_eur"], _ = missing.resolve(out, spec, rng=rng)
    # Cast to float only where non-None; None remains as NaN in the object series
    out["household_income_eur"] = pd.to_numeric(out["household_income_eur"], errors="coerce")
    return out


def map_number_of_cars(
    households: pd.DataFrame, *, cars_col: str = "H_ANZAUTO", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning),
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
    persons: pd.DataFrame, *, fkarte_col: str = "P_FKARTE", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default=False,
    )
    out = persons.copy()
    out["has_pt_subscription"], _ = missing.resolve(out, spec, rng=rng)
    out["has_pt_subscription"] = out["has_pt_subscription"].astype(bool)
    return out


def map_pt_subscription_type(
    persons: pd.DataFrame, *, fkarte_col: str = "P_FKARTE", rng=None,
    rs7_conditioning: bool = True,
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
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
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
    households: pd.DataFrame, *, bikes_col: str = "anzpedrad", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add ``number_of_bicycles`` from the MiD combined bicycle column via the uniform missing policy.

    CONSTRUCT (server-verified 2026-07-08 on the MiD B1 household microdata, 218,039
    valid rows): the committed target construct is bicycles INCLUDING pedelecs/e-bikes
    -- MiD codebook table H12.3 "Anzahl Fahrraeder/Pedelecs/E-Bikes im Haushalt", and the
    matching SrV side (``E_ANZ_RAD_ALLE_6``, "alle Raeder", table
    ``srv2023_bikes_incl_ebikes_by_kreis.csv``). The MiD household file provides this
    combined count directly as ``anzpedrad`` (default ``bikes_col``), verified to equal
    ``min(H_ANZRAD + H_ANZPED, 10)`` on ALL 218,039 valid rows (0 mismatches; the 99
    missing code propagates unchanged). DELIBERATE CONSTRUCT CHANGE: the previous default
    (``H_ANZRAD``, conventional bicycles EXCLUDING pedelecs) systematically understated
    household bicycle ownership against the incl-pedelec target (~31 % of households own
    >= 1 pedelec); this default now matches the popsim control, the written
    ``number_of_bicycles`` attribute, and the H12.3 reference to ONE construct.

    MiD codebook: 0..10 valid bicycle counts (``anzpedrad`` is top-coded at 10, same as
    the source columns; irrelevant for the 4+ control clip). 99 (keine Angabe, item
    non-response) -> imputed from the valid pool within the same household-size group
    (hhgr_gr) when present, else from the global valid pool. Previously, 99 was silently
    mapped to 0 (``BIKES_MISSING_CODE``); now it is imputed to avoid a systematic bias
    toward zero-bicycle households. ``BIKES_MISSING_CODE`` is retained as a module
    constant for any downstream code that may still reference it.

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="number_of_bicycles",
        source_col=bikes_col,
        value_map={i: i for i in range(0, 11)},
        structural={},
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning),
        default=0,
    )
    out = households.copy()
    out["number_of_bicycles"], _ = missing.resolve(out, spec, rng=rng)
    out["number_of_bicycles"] = out["number_of_bicycles"].astype(int)
    return out


def map_has_ebike(
    households: pd.DataFrame, *, ebike_col: str = "H_ANZPED", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add a 0/1 ``has_ebike`` household flag from the MiD household e-bike column.

    VERIFIED (2026-07-08, MiD B1 household microdata, 218,039 rows): the household
    e-bike column is ``H_ANZPED`` (Anzahl Pedelecs; values 0..10, missing code 99, the
    same code schema as ``H_ANZAUTO`` / ``H_ANZRAD``). Any value >= 1 means the
    household owns at least one operational pedelec, which is treated as the
    ``has_ebike`` control per the SrV target construct (``V_ANZ_ERAD``, household owns
    >= 1 operational e-bike). Missing code 99 is imputed within ``hhgr_gr`` (else global
    pool) before binarisation. Raises KeyError if ``ebike_col`` is absent (no silent
    fallback).

    Remaining ASSUMPTION (documented, not server-verifiable from the MiD codebook
    alone): MiD "Pedelec" is treated as equivalent to SrV "E-Rad" for the purpose of
    this control; the SrV construct may additionally include S-Pedelecs (higher-power
    e-bikes classed as mofas), which MiD's Pedelec question may not capture. This is a
    minor construct edge case and is not expected to materially bias the control.

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    if ebike_col not in households.columns:
        raise KeyError(
            f"map_has_ebike: source column {ebike_col!r} absent from the household frame "
            f"(has {list(households.columns)}); cannot seed the has_ebike control.")
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name="_ebike_count", source_col=ebike_col,
        value_map={i: i for i in range(0, 11)}, structural={},
        group_cols=imputation_group_cols(households, "hhgr_gr", rs7_conditioning=rs7_conditioning), default=0)
    out = households.copy()
    counts, _ = missing.resolve(out, spec, rng=rng)
    out["has_ebike"] = (counts.astype(int) >= 1).astype(int)
    return out


def map_trip_class(
    persons: pd.DataFrame, *, trips_col: str = "anzwege1", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add an int-coded ``trip_class`` (0..3) from MiD ``anzwege1`` via the uniform missing policy.

    Class scheme (matches the SrV 2023 trip-class target, ``target2026_trip_class_by_kreis.csv``,
    built by ``scripts/build_trip_class_target.py`` from ``E_ANZ_WEGE``): 0 trips -> class 0;
    1-2 trips -> class 1; 3-4 trips -> class 2; 5+ trips -> class 3.

    MiD codebook: ``anzwege1`` (Anzahl Wege am Stichtag) is valid over 0..50. The codes
    803 (30,041 weekday persons, server-verified 2026-07-08 on MiD B1) and 804 (2,714)
    mark persons whose trip module is not covered (no diary / rueckwirkende Wegeerhebung
    only) -- item non-response, NOT zero trips. Diary non-response correlates with
    mobility (persons who could not be surveyed on their trips are not a random subset
    of the mobile/immobile population), so these codes must never be dropped or forced to
    a class; they are declared in ``impute_codes`` and imputed from the valid pool within
    the same age band (``alter_gr1``) when present, else the global valid pool.
    ``default=1`` (the modal SrV class, 1-2 trips) is used only if the valid pool is empty.

    Raises ``KeyError`` if ``trips_col`` is absent (no silent fallback to a guessed
    column name).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    if trips_col not in persons.columns:
        raise KeyError(
            f"map_trip_class: source column {trips_col!r} absent from the person frame "
            f"(has {list(persons.columns)}); cannot seed the trip_class control.")
    rng = rng if rng is not None else np.random.RandomState(0)
    value_map = {n: (0 if n == 0 else 1 if n <= 2 else 2 if n <= 4 else 3) for n in range(0, 51)}
    spec = missing.AttributeSpec(
        name="trip_class",
        source_col=trips_col,
        value_map=value_map,
        structural={},
        impute_codes=(803, 804),          # trip module not covered (no diary): impute
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default=1,
    )
    out = persons.copy()
    out["trip_class"], _ = missing.resolve(out, spec, rng=rng)
    out["trip_class"] = out["trip_class"].astype(int)
    return out


def map_participation(
    persons: pd.DataFrame, name: str, *, source_col: str, rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add an int-coded ``<name>`` (0/1) "participation" control from a per-person
    purpose-trip flag via the uniform missing policy (mirrors ``map_trip_class``
    precisely). Generic core behind ``map_work_participation`` / ``map_leisure_
    participation`` / ``map_education_participation`` (feature #224 task 5) --
    parametrized by ``name`` (the output column / ``missing.AttributeSpec`` name) rather
    than duplicated per purpose.

    ``source_col`` is expected to already carry the per-person purpose-trip flag {0, 1},
    or one of the MiD 803/804 diary non-response codes (trip module not covered -- see
    ``mid.compute_has_purpose_trip``, which derives this column from the person's Wege).
    As with ``trip_class``, 803/804 are item non-response, NOT "no trip" -- diary
    non-response correlates with mobility, so these codes must never be dropped or
    forced to 0; they are declared in ``impute_codes`` and imputed from the valid {0, 1}
    pool within the same age band (``alter_gr1``) when present, else the global valid
    pool. ``default=0`` (no trip) is used only if the valid pool is empty.

    Raises ``KeyError`` if ``source_col`` is absent (no silent fallback to a guessed
    column name).

    ``rng`` defaults to ``np.random.RandomState(0)`` for backward compatibility;
    callers should pass the pipeline's seeded rng to ensure reproducibility.
    """
    if source_col not in persons.columns:
        raise KeyError(
            f"map_participation: source column {source_col!r} absent from the person "
            f"frame (has {list(persons.columns)}); cannot seed the {name} control.")
    rng = rng if rng is not None else np.random.RandomState(0)
    spec = missing.AttributeSpec(
        name=name,
        source_col=source_col,
        value_map={0: 0, 1: 1},
        structural={},
        impute_codes=(803, 804),          # trip module not covered (no diary): impute
        group_cols=imputation_group_cols(persons, "alter_gr1", rs7_conditioning=rs7_conditioning),
        default=0,
    )
    out = persons.copy()
    out[name], _ = missing.resolve(out, spec, rng=rng)
    out[name] = out[name].astype(int)
    return out


def map_work_participation(
    persons: pd.DataFrame, *, source_col: str = "work_participation_src", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add an int-coded ``work_participation`` (0/1) from a per-person work-trip flag.

    Thin wrapper over :func:`map_participation` (name="work_participation"); kept as a
    named entry point so existing callers/tests stay unchanged. See that function's
    docstring for the full 803/804 non-response imputation policy.
    """
    return map_participation(
        persons, "work_participation", source_col=source_col, rng=rng,
        rs7_conditioning=rs7_conditioning)


def map_leisure_participation(
    persons: pd.DataFrame, *, source_col: str = "leisure_participation_src", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add an int-coded ``leisure_participation`` (0/1) from a per-person leisure-trip
    flag (W_ZWECK=7; see ``mid.PARTICIPATION_W_ZWECK``).

    Thin wrapper over :func:`map_participation` (name="leisure_participation"); mirrors
    ``map_work_participation`` exactly (feature #224 task 5).
    """
    return map_participation(
        persons, "leisure_participation", source_col=source_col, rng=rng,
        rs7_conditioning=rs7_conditioning)


def map_education_participation(
    persons: pd.DataFrame, *, source_col: str = "education_participation_src", rng=None,
    rs7_conditioning: bool = True,
) -> pd.DataFrame:
    """Add an int-coded ``education_participation`` (0/1) from a per-person
    education-trip flag (W_ZWECK in {3, 11, 12}; see ``mid.PARTICIPATION_W_ZWECK``).

    Thin wrapper over :func:`map_participation` (name="education_participation");
    mirrors ``map_work_participation`` exactly (feature #224 task 5).
    """
    return map_participation(
        persons, "education_participation", source_col=source_col, rng=rng,
        rs7_conditioning=rs7_conditioning)


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


# MiD bildung1 (Schulabschluss zusammengefasst) -> 3-class {low, mid, high}. Codes
# confirmed vs MiD 2023 Codeplan B1, sheet Personen: 1=(noch) ohne Abschluss,
# 2=niedrig, 3=mittel, 4=hoch, 5=anderer Abschluss, 9=k.A. Only the completed
# allgemeinbildender Abschluss levels map: 2->low, 3->mid, 4->high. Code 1
# ((noch) ohne; carries ALL <15 children + current pupils -- bildung1 has no 402
# structural code), code 5 (anderer Abschluss; no Zensus pendant) and 9 (k.A.)
# -> NaN (excluded, mirrors Zensus __1 sitting outside the 3 classes).
SCHULABS_BY_BILDUNG1 = {2: "low", 3: "mid", 4: "high"}


def map_schulabschluss(persons: pd.DataFrame) -> pd.DataFrame:
    """Add a 3-class ``schulabschluss`` {low, mid, high} from MiD ``bildung1``.

    bildung1 1 ((noch) ohne Abschluss), 5 (anderer Abschluss) and 9 (k.A.) -> NaN
    (excluded from the completed-qualification universe; floated/imputed downstream).
    """
    import logging
    logger = logging.getLogger(__name__)
    out = persons.copy()
    out["schulabschluss"] = out["bildung1"].map(SCHULABS_BY_BILDUNG1)
    n_unmapped = int(out["schulabschluss"].isna().sum())
    if n_unmapped:
        logger.info(
            "[popsim.attributes] schulabschluss: %d/%d persons unmapped (k.A.) -> imputed downstream",
            n_unmapped, len(out),
        )
    return out


# MiD bildung2 (Berufs- oder Hochschulabschluss) -> 3-class {none, vocational,
# tertiary}. Codes confirmed vs MiD 2023 Codeplan B1, sheet Personen: 1=ja Berufs-
# abschluss, 2=ja Hochschulabschluss, 3=ja Berufs- UND Hochschulabschluss, 4=ja
# anderer, 5=nein, 9=k.A., 206=Proxy, 402=Kind<14. 1 -> vocational; 2 and 3 (both
# carry a Hochschulabschluss) -> tertiary; 5 -> none. Code 4 (anderer; no Zensus
# pendant -- census berufl. partitions fully into __11-13/voc, __14-17/tert, __2/none)
# and 9/206/402 (k.A./structural) -> NaN (excluded / imputed downstream).
BERUFABS_BY_BILDUNG2 = {1: "vocational", 2: "tertiary", 3: "tertiary", 5: "none"}


def map_beruflabschluss(persons: pd.DataFrame) -> pd.DataFrame:
    """Add a 3-class ``beruflabschluss`` {none, vocational, tertiary} from MiD ``bildung2``.

    bildung2 4 (anderer), 9 (k.A.) and structural 206/402 -> NaN (excluded from the
    15+ control universe / imputed downstream).
    """
    import logging
    logger = logging.getLogger(__name__)
    out = persons.copy()
    out["beruflabschluss"] = out["bildung2"].map(BERUFABS_BY_BILDUNG2)
    n_unmapped = int(out["beruflabschluss"].isna().sum())
    if n_unmapped:
        logger.info(
            "[popsim.attributes] beruflabschluss: %d/%d unmapped (k.A./structural 206/402) -> excluded/imputed",
            n_unmapped, len(out),
        )
    return out


def map_socioprofessional_class(persons: pd.DataFrame) -> pd.DataFrame:
    """Add ``socioprofessional_class`` (eqasim/INSEE CS1 1-8) from broad activity status.

    There is NO occupation variable in the standard MiD respondent table, so CS1 is
    derived from the broad activity status the synthesis DOES carry --
    ``derive_socioprofessional_class(employed, age, studies)`` from
    ``braunschweig.ipf.attributed`` -- exactly the eqasim/IPF path, so the popsim and
    IPF populations share one CS1 code space. Age is a documented coarse seniority
    proxy for the active classes (NOT measured occupation).

    Issue #167: the former ``SPC_BY_P_BKAT`` primary path mis-read MiD ``P_BKAT``
    ("Umfang der Erwerbstaetigkeit" = employment EXTENT; see
    ``EMPLOYMENT_STATUS_BY_P_BKAT``) as an occupation "Berufskategorie" crosswalk. That
    crosswalk was semantically invalid and is removed; ``P_BKAT`` no longer influences
    ``socioprofessional_class`` at all (it feeds only ``map_employment_status``).

    Args:
        persons: DataFrame with at least ``employed`` (bool) and ``age`` (int).
            ``studies`` (bool) is used if present; defaults to False.

    Returns:
        A copy of ``persons`` with the integer ``socioprofessional_class`` column added.
    """
    out = persons.copy()
    studies = out["studies"] if "studies" in out.columns else pd.Series(False, index=out.index)

    # derive_socioprofessional_class resets the index to 0-based internally, so realign
    # the returned Series to out.index before assignment to avoid a misaligned join
    # when the input has a non-default index.
    spc = derive_socioprofessional_class(out["employed"], out["age"], studies)
    out["socioprofessional_class"] = pd.Series(spc.to_numpy(), index=out.index).astype(int)
    return out
