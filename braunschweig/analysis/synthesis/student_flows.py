"""synpp stage: the acceptance table of the student balance and the diagnostics of the flows (#140, Phase 4, B7).

It COUNTS THE SYNTHESIS (the persons of the cached population and the in-commuter agents), never arrivals in a
simulation, and it needs no final-view trips: it runs on a cached population after the three changed stages. The
definitions are in the design (section 5a, "Acceptance table"; B7); this module owns only how they are coded.

Rows of ``student_acceptance_table.csv`` (:func:`acceptance_table`; ``f(k)``, ``f_eff``, ``e`` and the level table
as in ``braunschweig.data.education.student_flows_inputs``; ``rate`` = ``sampling_rate``; ``L`` = the level table's
total of the region):

* ``site``: per local site the residents of the day (persons with a ``destination_id`` of the site whose
  ``assignment_path`` is not ``absent_*``; balance = the site's share of the region's heads of T x ``L`` x
  ``f_eff`` x ``rate``), the in-commuter agents (balance = T's outside heads of the site x ``f_eff`` x ``rate``) and
  their sum (balance = the site's enrollment x ``f_eff`` x ``rate``), and the same three over all local sites;
* ``incommuters_by_origin``: the agents per origin Kreis (balance = T's outside heads of the Kreis x ``f_eff`` x
  ``rate``) and in total;
* ``out_commuters``: the residents of the day at each surrounding institution (same balance as a site's residents)
  and in total;
* ``students_population``: persons with ``P_TAET == STUDENT_P_TAET`` aged 18 or older per home Kreis (balance =
  level x ``rate``, never the row sums of T; ADR-0210) and in total;
* ``students_of_day``: of those the ones present with an education activity (balance = level x ``f(k)`` x ``rate``);
* ``students_30plus_report``: the students aged 30 or older with an education activity, present or not (their plan
  is what the balance applies ``e`` to; balance = level 30+ x ``e`` x ``rate``), ``rule_applies`` False.

"Present" is the absence stage's ``day_absence_state``; with the day absence off every person is present. An
"education activity" is a row of the ``education_gravity`` output (the persons whose plan has an education trip).

The rule (:func:`acceptance_verdict`, ASSUMPTION A17): a row with ``rule_applies`` whose balance is at least
``braunschweig.students.acceptance_min_persons`` and whose relative deviation exceeds
``braunschweig.students.acceptance_max_deviation_share`` makes the verdict ``FAIL``; with no such row large enough
the verdict is ``REPORT_ONLY`` (the rule needs a larger sample). The stage never raises on a verdict; it raises on
an invalid configuration or invalid input (a refused switch combination, an in-commuter id that repeats or overlaps
a resident's, a missing table). The verdict is written to ``student_flows_report.json`` and logged, a FAIL as an
error.

Diagnostics (measurements of the synthesis, not validation) are listed in :data:`OUTPUT_FILES`. Distance bands of
the residents compare ``straight line x detour factor`` with the SrV table (the ADR-0210 yardstick
``LEGACY_DETOUR_FACTOR``, sensitivity runs at the factors of ``KEY_DETOUR_SENSITIVITY``) and, for all students,
with the MiD table of the students' education legs. No committed Mikrozensus table of the students' education
distance exists, so that comparison is skipped and the skip is logged and written to the report.

Import boundary (Phase 4 global constraints): the project imports below are files unchanged since the run commit
or leaves added after it; the stage package ``braunschweig.popsim`` is never imported.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig import provenance as run_provenance
from braunschweig import synpp_tokens
from braunschweig.analysis import json_output
from braunschweig.calibration import circuity as _circuity
from braunschweig.calibration import srv_distance_targets as _srv_distance_targets
from braunschweig.data.education import student_flows_inputs as _flows_inputs
from braunschweig.data.education import student_mode_reference as _student_mode_reference
from braunschweig.synthesis.locations import university_assignment as _university_assignment

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.student_flows")
_LOG_TAG = "[student_flows]"

STATUS = "measurement of the synthesis, not validation (the acceptance rule is the one exception, A17)"

# --------------------------------------------------------------------------------------------------- keys
KEY_FLOWS_ENABLED = _flows_inputs.KEY_FLOWS_ENABLED
KEY_MIN_PERSONS = "braunschweig.students.acceptance_min_persons"
DEFAULT_MIN_PERSONS = 200          # ASSUMPTION A17: not from a table (design 5a)
KEY_MAX_DEVIATION_SHARE = "braunschweig.students.acceptance_max_deviation_share"
DEFAULT_MAX_DEVIATION_SHARE = 0.05  # ASSUMPTION A17: not from a table (design 5a)
KEY_DETOUR_SENSITIVITY = "braunschweig.students.analysis_detour_sensitivity"
DEFAULT_DETOUR_SENSITIVITY = [1.2, 1.4]   # ASSUMPTION: the ADR-0210 yardstick's sensitivity runs
KEY_MIN_SRV_RESPONDENTS = "braunschweig.students.analysis_min_srv_respondents"
DEFAULT_MIN_SRV_RESPONDENTS = 30   # ASSUMPTION A14: the designer's respondent threshold, not from a table
KEY_FALLBACK_WARN_SHARE = "braunschweig.students.flows_report_fallback_warn_share"
DEFAULT_FALLBACK_WARN_SHARE = 0.25   # ASSUMPTION: warn level of the fallbacks that are a rate, not a defect
#: Repeated from ``student_incommuters`` and ``education_gravity`` (stage modules, not imported here); pinned by
#: ``tests/test_student_flows_analysis.py``.
KEY_OD_TABLE_PATH = "braunschweig.students.od_table_path"
DEFAULT_OD_TABLE_PATH = "braunschweig/schools/student_od_table.csv"
KEY_LEVEL_TABLE_PATH = "braunschweig.students.level_table_path"
DEFAULT_LEVEL_TABLE_PATH = "braunschweig/popsim/student_level_table.csv"
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
KEY_DAY_ABSENCE_STUDENT_CELLS = "day_absence_student_cells_enabled"

# --------------------------------------------------------------------------------------------------- stages
ENRICHED_STAGE = "synthesis.population.enriched"
HOME_ZONES_STAGE = "synthesis.population.spatial.home.zones"
HOME_LOCATIONS_STAGE = "synthesis.population.spatial.home.locations"
TRIPS_STAGE = "synthesis.population.trips"
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"
GRAVITY_STAGE = "braunschweig.synthesis.locations.education_gravity"
FACILITIES_STAGE = "braunschweig.data.schools.university_facilities"
INCOMMUTERS_STAGE = "braunschweig.synthesis.student_incommuters"

# --------------------------------------------------------------------------------------------------- literals
#: Repeated from their owners, pinned by ``tests/test_student_flows_analysis.py``.
ABSENT_PATH_PREFIX = "absent_"                    # education_gravity.ABSENT_PATH_PREFIX
PATH_LEGACY_DECAY = "legacy_decay"                # education_gravity.PATH_LEGACY_DECAY
STATE_PRESENT = "present"                         # day_absence.absence.STATE_PRESENT
COMMUTE_OD_CELL = "od_cell"                       # student_incommuter_counts.COMMUTE_OD_CELL
HOME_BRANCH_GATE = "gate"                         # student_incommuters.HOME_BRANCH_GATE
BRAUNSCHWEIG_KREIS = "03101"                      # srv_student_reference.BRAUNSCHWEIG_KREIS
REGION_HANNOVER_ARS5 = "03241"                    # srv_student_reference.REGION_HANNOVER_ARS5
LANDKREIS_HILDESHEIM_ARS5 = "03254"               # srv_student_reference.LANDKREIS_HILDESHEIM_ARS5
HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_SITE, HOME_GROUP_NO_SITE, HOME_GROUP_ALL = (
    "braunschweig", "other_site_kreis", "no_site_kreis", "region")
HOME_GROUPS = (HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_SITE, HOME_GROUP_NO_SITE, HOME_GROUP_ALL)
DEST_OWN, DEST_SITE_PREFIX, DEST_OTHER_REGION = "own_kreis", "site_kreis_", "other_region_kreis"
DEST_REGION_HANNOVER, DEST_LK_HILDESHEIM, DEST_OTHER_OUTSIDE = "region_hannover", "landkreis_hildesheim", "other_outside"
ALL_GROUP = "all"
SRV_TOTAL_CODE = "03ZGB"

SRV_SUBDIR = ("braunschweig", "srv")
SCHOOLS_SUBDIR = ("braunschweig", "schools")
SRV_DISTANCE_TABLE = "srv2023_student_education_distance_by_kreis.csv"
SRV_DESTINATION_TABLE = "srv2023_student_education_destination.csv"
SRV_LEG_BY_STATUS_TABLE = "srv2023_education_leg_by_student_status.csv"
BALANCE_BY_SITE_TABLE = "student_balance_by_site.csv"
BALANCE_OUT_COMMUTERS_TABLE = "student_balance_out_commuters.csv"
SRV_TABLES = (_flows_inputs.PARAMETERS_TABLE, _flows_inputs.PARAMETERS_BY_AREA_TABLE, SRV_DISTANCE_TABLE,
              SRV_DESTINATION_TABLE, SRV_LEG_BY_STATUS_TABLE)

OUTPUT_SUBDIR = "student_flows"
REPORT_FILE = "student_flows_report.json"
OUTPUT_FILES = {
    "acceptance": "student_acceptance_table.csv",
    "flows": "student_realised_flows.csv",
    "distance_bands": "student_distance_bands.csv",
    "destination_shares": "student_destination_shares.csv",
    "allotment": "student_allotment_by_kreis.csv",
    "leg_by_status": "student_education_leg_by_status.csv",
    "routed_level": "student_routed_level_by_status.csv",
    "non_students": "student_non_students_moved.csv",
    "incommuter_commute": "student_incommuter_commute_length.csv",
    "summary": "student_flows_summary.md",
}

ACCEPTANCE_COLUMNS = ["section", "key", "model", "balance", "deviation", "deviation_share", "rule_applies"]
VERDICT_PASS, VERDICT_FAIL, VERDICT_REPORT_ONLY = "PASS", "FAIL", "REPORT_ONLY"
#: Fixed stream id (a separator only, no value of the model) of the random-allotment baseline of the diagnostics.
BASELINE_STREAM_ID = 140012

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HELPER_MODULES = (
    _flows_inputs,
    _student_mode_reference,
    _university_assignment,
    _srv_distance_targets,
    _circuity,
    run_provenance,
    json_output,
)
#: The import closure of the helpers above and of ``braunschweig.analysis.spatial`` (ADR-0136); one name per line.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.analysis.spatial",
    "braunschweig.data.bbsr.regiostar",
    "braunschweig.data.cordon.mode_reference",
    "braunschweig.data.csv_header",
    "braunschweig.gravity.friction",
)


# =================================================================================== the acceptance table
def _deviation_row(section: str, key: str, model: float, balance: float, rule_applies: bool) -> dict:
    model, balance = float(model), float(balance)
    deviation = model - balance
    return {"section": section, "key": key, "model": model, "balance": balance, "deviation": deviation,
            "deviation_share": deviation / balance if balance != 0 else math.nan, "rule_applies": bool(rule_applies)}


def _count(series: pd.Series, key) -> float:
    return float(series.get(key, 0))


def acceptance_table(*, destinations: pd.DataFrame, enrollment: pd.Series, region_shares: pd.Series,
                     outside_heads: pd.Series, outside_heads_by_origin: pd.Series, level: pd.DataFrame,
                     f_by_kreis: pd.Series, f_eff: float, e: float, rate: float, residents_at: pd.Series,
                     incommuters_at: pd.Series, incommuters_from: pd.Series, students_by_kreis: pd.Series,
                     students_of_day_by_kreis: pd.Series, students_30plus_by_kreis: pd.Series) -> pd.DataFrame:
    """The acceptance table (module docstring) from counts of the model and the quantities of the balance.

    Args:
        destinations: ``[destination_id, scope]`` (``local`` or ``surrounding``), every destination of T.
        enrollment: ``destination_id -> enrollment`` of the local sites (heads).
        region_shares: ``destination_id -> share`` of the region's heads of T (``destination_shares``).
        outside_heads, outside_heads_by_origin: heads of T with ``home_in_region == False`` per destination and per
            origin Kreis.
        level: ``[code, age_range, students_persons]`` of the region (``load_level``).
        f_by_kreis, f_eff, e: the day factor of each Kreis, its level-weighted mean and the education-leg share.
        rate: the sampling rate.
        residents_at, incommuters_at, incommuters_from: model counts per destination / per origin Kreis; a key
            missing from a series counts as 0.
        students_by_kreis, students_of_day_by_kreis, students_30plus_by_kreis: model counts per home Kreis.

    Returns:
        ``ACCEPTANCE_COLUMNS``; ``deviation = model - balance``, ``deviation_share = deviation / balance`` (NaN for a
        balance of 0).
    """
    level_by_kreis = level.groupby("code")["students_persons"].sum().sort_index()
    level_total = float(level_by_kreis.sum())
    level_30plus = level[level["age_range"] == _flows_inputs.AGE_RANGE_OLD].groupby("code")["students_persons"].sum()
    scale = f_eff * rate
    rows: list[dict] = []

    local = sorted(destinations.loc[destinations["scope"] == "local", "destination_id"])
    surrounding = sorted(destinations.loc[destinations["scope"] == "surrounding", "destination_id"])
    totals = {"residents_of_day": [0.0, 0.0], "incommuters": [0.0, 0.0], "total": [0.0, 0.0]}
    for site in local:
        parts = {
            "residents_of_day": (_count(residents_at, site), _count(region_shares, site) * level_total * scale),
            "incommuters": (_count(incommuters_at, site), _count(outside_heads, site) * scale),
            "total": (_count(residents_at, site) + _count(incommuters_at, site), _count(enrollment, site) * scale),
        }
        for name, (model, balance) in parts.items():
            rows.append(_deviation_row("site", f"{site}|{name}", model, balance, True))
            totals[name][0] += model
            totals[name][1] += balance
    for name, (model, balance) in totals.items():
        rows.append(_deviation_row("site", f"all_local_sites|{name}", model, balance, True))

    origins = sorted(set(outside_heads_by_origin.index) | set(incommuters_from.index))
    for origin in origins:
        rows.append(_deviation_row("incommuters_by_origin", origin, _count(incommuters_from, origin),
                                   _count(outside_heads_by_origin, origin) * scale, True))
    rows.append(_deviation_row("incommuters_by_origin", "total", sum(_count(incommuters_from, o) for o in origins),
                               float(outside_heads_by_origin.sum()) * scale, True))

    out_model = out_balance = 0.0
    for institution in surrounding:
        model, balance = _count(residents_at, institution), _count(region_shares, institution) * level_total * scale
        rows.append(_deviation_row("out_commuters", institution, model, balance, True))
        out_model, out_balance = out_model + model, out_balance + balance
    rows.append(_deviation_row("out_commuters", "total", out_model, out_balance, True))

    for section, counts, per_kreis_balance, rule in (
            ("students_population", students_by_kreis, lambda k: float(level_by_kreis[k]) * rate, True),
            ("students_of_day", students_of_day_by_kreis,
             lambda k: float(level_by_kreis[k]) * float(f_by_kreis[k]) * rate, True),
            ("students_30plus_report", students_30plus_by_kreis,
             lambda k: float(level_30plus.get(k, 0.0)) * e * rate, False)):
        model_total = balance_total = 0.0
        for kreis in level_by_kreis.index:
            model, balance = _count(counts, kreis), per_kreis_balance(kreis)
            rows.append(_deviation_row(section, kreis, model, balance, rule))
            model_total, balance_total = model_total + model, balance_total + balance
        rows.append(_deviation_row(section, "total", model_total, balance_total, rule))
    return pd.DataFrame(rows, columns=ACCEPTANCE_COLUMNS)


def acceptance_verdict(table: pd.DataFrame, min_persons: float, max_deviation_share: float) -> str:
    """``PASS``, ``FAIL`` or ``REPORT_ONLY`` (ASSUMPTION A17).

    Judged rows are those with ``rule_applies`` and ``balance >= min_persons``. Any judged row with
    ``|deviation_share| > max_deviation_share`` gives ``FAIL``; no judged row gives ``REPORT_ONLY``; else ``PASS``.
    """
    if not (math.isfinite(max_deviation_share) and max_deviation_share >= 0):
        raise ValueError(f"{_LOG_TAG} max_deviation_share must be finite and >= 0, got {max_deviation_share!r}")
    if not (math.isfinite(min_persons) and min_persons >= 0):
        raise ValueError(f"{_LOG_TAG} min_persons must be finite and >= 0, got {min_persons!r}")
    judged = table[table["rule_applies"].astype(bool) & (table["balance"] >= min_persons)]
    if judged.empty:
        return VERDICT_REPORT_ONLY
    return VERDICT_FAIL if (judged["deviation_share"].abs() > max_deviation_share).any() else VERDICT_PASS


def failing_rows(table: pd.DataFrame, min_persons: float, max_deviation_share: float) -> pd.DataFrame:
    """The judged rows that exceed the bound (the rows that make the verdict FAIL)."""
    judged = table[table["rule_applies"].astype(bool) & (table["balance"] >= min_persons)]
    return judged[judged["deviation_share"].abs() > max_deviation_share]


# =================================================================================== the person frame
def person_table(persons: pd.DataFrame, home_kreis: pd.Series, gravity: pd.DataFrame,
                 day_state: pd.Series | None) -> pd.DataFrame:
    """One row per enriched person: home Kreis, status, education activity, destination, presence.

    Columns: ``person_id, home_kreis, age, p_taet`` (numeric, NaN unreadable), ``is_student`` (``P_TAET ==
    STUDENT_P_TAET``), ``activity`` (the person is in ``gravity``), ``destination_id``, ``assignment_path``,
    ``routed_level`` (from ``gravity``; None without activity), ``present`` (``day_absence_state == present``;
    True for all when ``day_state`` is None).

    Raises ``ValueError`` on a household without a home Kreis, a ``gravity`` person that is no enriched person, a
    duplicated person, a person missing from ``day_state`` or an absent ``P_TAET`` column.
    """
    for column in ("person_id", "household_id", "age", "P_TAET"):
        if column not in persons.columns:
            raise ValueError(f"{_LOG_TAG} the enriched persons lack the column {column!r}; found {list(persons.columns)}")
    if persons["person_id"].duplicated().any() or gravity["person_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} a person occurs twice in the enriched persons or the education locations")
    unknown = set(gravity["person_id"]) - set(persons["person_id"])
    if unknown:
        raise ValueError(f"{_LOG_TAG} {len(unknown)} person(s) of the education locations are no enriched person, "
                         f"first {sorted(unknown)[:5]}")
    kreis = persons["household_id"].map(home_kreis)
    if kreis.isna().any():
        raise ValueError(f"{_LOG_TAG} {int(kreis.isna().sum())} persons live in a household without a home Kreis")
    taet = pd.to_numeric(persons["P_TAET"], errors="coerce")
    frame = pd.DataFrame({"person_id": persons["person_id"].to_numpy(), "home_kreis": kreis.to_numpy(dtype=object),
                          "age": persons["age"].to_numpy(), "p_taet": taet.to_numpy(dtype=float),
                          "is_student": (taet == _flows_inputs.STUDENT_P_TAET).to_numpy()})
    located = gravity.set_index("person_id")
    frame["activity"] = frame["person_id"].isin(located.index).to_numpy()
    for column in ("destination_id", "assignment_path", "routed_level"):
        frame[column] = frame["person_id"].map(located[column]).astype(object)
        frame[column] = frame[column].where(frame[column].notna(), None)
    if day_state is None:
        frame["present"] = True
    else:
        state = frame["person_id"].map(day_state)
        if state.isna().any():
            raise ValueError(f"{_LOG_TAG} {int(state.isna().sum())} persons are missing from the day-absence frame")
        frame["present"] = (state == STATE_PRESENT).to_numpy()
    return frame


def resident_mask(frame: pd.DataFrame) -> pd.Series:
    """The persons placed at a destination whose path is not ``absent_*``: the residents of the day at a site."""
    path = frame["assignment_path"]
    return (frame["destination_id"].notna() & path.notna()
            & ~path.fillna("").astype(str).str.startswith(ABSENT_PATH_PREFIX))


def adult_students(frame: pd.DataFrame) -> pd.Series:
    return frame["is_student"] & (frame["age"] >= _university_student_min_age())


def _university_student_min_age() -> int:
    return 18


def student_counts(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """The three per-home-Kreis counts of the population rows (module docstring)."""
    adult = adult_students(frame)
    sets = {"students_by_kreis": adult,
            "students_of_day_by_kreis": adult & frame["activity"] & frame["present"],
            "students_30plus_by_kreis": adult & (frame["age"] >= 30) & frame["activity"]}
    return {name: frame[mask].groupby("home_kreis").size() for name, mask in sets.items()}


def students_outside_level(frame: pd.DataFrame, level_codes: Sequence[str]) -> int:
    """Adult students whose home Kreis has no row of the level table: no population row counts them."""
    return int((adult_students(frame) & ~frame["home_kreis"].isin(list(level_codes))).sum())


def check_incommuter_ids(incommuters: pd.DataFrame, resident_ids: pd.Series) -> None:
    """Raise ``ValueError`` when the in-commuter ``person_id`` is absent, repeats or overlaps a resident's id.

    An empty frame (the injection is off) passes; an overlap would count one person in two rows of the table.
    """
    if incommuters.empty:
        return
    if "person_id" not in incommuters.columns:
        raise ValueError(f"{_LOG_TAG} the in-commuter frame has no person_id column; found {list(incommuters.columns)}")
    ids = incommuters["person_id"]
    if ids.duplicated().any():
        raise ValueError(f"{_LOG_TAG} {int(ids.duplicated().sum())} in-commuter person_id(s) repeat, first "
                         f"{ids[ids.duplicated()].head(5).tolist()}")
    overlap = set(ids) & set(resident_ids)
    if overlap:
        raise ValueError(f"{_LOG_TAG} {len(overlap)} in-commuter person_id(s) are also residents' ids, first "
                         f"{sorted(overlap)[:5]}")


def usable_distance_rows(bands: pd.DataFrame) -> dict:
    """The SrV distance rows (groups) with enough respondents and those reported only (``n_reference_low``)."""
    srv = bands[bands["reference"] == "srv"].drop_duplicates("group")
    return {"usable": sorted(srv.loc[~srv["n_reference_low"], "group"]),
            "reported_only": sorted(srv.loc[srv["n_reference_low"], "group"])}


# =================================================================================== diagnostics
def realised_flows(frame: pd.DataFrame, incommuters: pd.DataFrame, od: pd.DataFrame) -> pd.DataFrame:
    """``[home_ars5, destination_id, kind, persons, t_heads]``: residents of the day and in-commuters per pair.

    ``t_heads`` is T's heads of the pair (the residents' pair of a region row, the in-commuters' of an outside row);
    pairs of T without a person are kept with 0 persons.
    """
    residents = frame[resident_mask(frame)].groupby(["home_kreis", "destination_id"]).size().rename("persons")
    residents = residents.reset_index().rename(columns={"home_kreis": "home_ars5"})
    commuters = (incommuters.groupby(["orig_ars5", "destination_id"]).size().rename("persons").reset_index()
                 .rename(columns={"orig_ars5": "home_ars5"}) if len(incommuters) else
                 pd.DataFrame({"home_ars5": [], "destination_id": [], "persons": []}))
    parts = []
    for kind, persons, seed in (("resident", residents, _flows_inputs.region_seed(od)),
                                ("incommuter", commuters, _flows_inputs.outside_seed(od))):
        merged = persons.merge(seed.rename(columns={"seed": "t_heads"}), on=["home_ars5", "destination_id"],
                               how="outer")
        merged["persons"] = merged["persons"].fillna(0).astype("int64")
        merged["t_heads"] = merged["t_heads"].fillna(0.0)
        merged.insert(2, "kind", kind)
        parts.append(merged)
    return pd.concat(parts, ignore_index=True).sort_values(["kind", "home_ars5", "destination_id"]).reset_index(
        drop=True)[["home_ars5", "destination_id", "kind", "persons", "t_heads"]]


def band_shares(km: np.ndarray, edges: Sequence[float] = _student_mode_reference.BAND_EDGES_KM) -> np.ndarray:
    """Share of ``km`` in each band of ``edges`` (left-inclusive, last edge infinity); NaN without values."""
    km = np.asarray(km, dtype=float)
    if km.size == 0:
        return np.full(len(edges) - 1, np.nan)
    index = np.searchsorted(np.asarray(edges[1:-1], dtype=float), km, side="right")
    return np.bincount(index, minlength=len(edges) - 1) / km.size


def band_comparison(model_km_by_group: Mapping[str, np.ndarray], references: Sequence[Mapping],
                    detour_factors: Sequence[float], min_reference_persons: int) -> pd.DataFrame:
    """Band shares of ``straight line x detour factor`` against a reference, with the earth mover's distance.

    ``references``: dicts ``group, reference, n_reference, shares`` (a sequence over the bands of
    ``student_mode_reference.BAND_LABELS``), ``emd_noise_95`` (NaN if the table has none). The EMD is
    ``srv_distance_targets.emd_on_shares``, the function that wrote the SrV table's ``emd_noise_95``;
    ``emd_within_noise`` compares the two (missing without a noise level or a reference).
    ``n_reference_low`` is True when the reference row holds fewer than ``min_reference_persons`` respondents
    (``braunschweig.students.analysis_min_srv_respondents``, ASSUMPTION A14): such a row is reported only.
    """
    labels = _student_mode_reference.BAND_LABELS
    rows = []
    for reference in references:
        km = np.asarray(model_km_by_group.get(reference["group"], np.array([])), dtype=float)
        shares_ref = np.asarray(reference["shares"], dtype=float)
        for factor in detour_factors:
            model = band_shares(km * factor)
            have_both = km.size > 0 and np.isfinite(shares_ref).all()
            emd = _srv_distance_targets.emd_on_shares(model, shares_ref) if have_both else math.nan
            noise = float(reference.get("emd_noise_95", math.nan))
            row = {"group": reference["group"], "reference": reference["reference"], "detour_factor": float(factor),
                   "n_model": int(km.size), "n_reference": reference["n_reference"],
                   "n_reference_low": bool(reference["n_reference"] < min_reference_persons)}
            row.update({f"model_share_{label}": share for label, share in zip(labels, model)})
            row.update({f"reference_share_{label}": share for label, share in zip(labels, shares_ref)})
            row.update({"emd": emd, "emd_noise_95": noise,
                        "emd_within_noise": (emd <= noise) if (math.isfinite(emd) and math.isfinite(noise))
                        else None})
            rows.append(row)
    return pd.DataFrame(rows)


def srv_band_references(srv: pd.DataFrame) -> list[dict]:
    """One reference per row of the SrV distance table; the total row (``03ZGB``) is the group ``all``."""
    labels = _student_mode_reference.BAND_LABELS
    columns = [f"share_{label}" for label in labels]
    missing = [c for c in columns + ["code", "n_persons", "emd_noise_95"] if c not in srv.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {SRV_DISTANCE_TABLE} lacks the column(s) {missing}")
    return [{"group": ALL_GROUP if row["code"] == SRV_TOTAL_CODE else str(row["code"]), "reference": "srv",
             "n_reference": int(row["n_persons"]), "shares": [row[c] for c in columns],
             "emd_noise_95": row["emd_noise_95"]} for _, row in srv.iterrows()]


def mid_band_reference(path) -> dict:
    """The MiD students' education-leg band shares: ``share_of_all`` summed over the modes of each band, normalised."""
    table = pd.read_csv(path, comment="#", encoding="utf-8", dtype={"distance_band": str})
    labels = _student_mode_reference.BAND_LABELS
    unknown = sorted(set(table["distance_band"]) - set(labels))
    if unknown:
        raise ValueError(f"{_LOG_TAG} {os.path.basename(str(path))}: unknown distance band(s) {unknown}")
    by_band = table.groupby("distance_band")["share_of_all"].sum().reindex(labels)
    n_by_band = table.groupby("distance_band")["n_unweighted"].sum().reindex(labels).fillna(0)
    if by_band.isna().any() or not by_band.sum() > 0:
        raise ValueError(f"{_LOG_TAG} the MiD table has no share in band(s) {list(by_band.index[by_band.isna()])}")
    return {"group": ALL_GROUP, "reference": "mid", "n_reference": int(n_by_band.sum()),
            "shares": (by_band / by_band.sum()).tolist(), "emd_noise_95": math.nan}


def home_group(kreis: str, site_kreise: frozenset) -> str:
    if kreis == BRAUNSCHWEIG_KREIS:
        return HOME_GROUP_BRAUNSCHWEIG
    return HOME_GROUP_SITE if kreis in site_kreise else HOME_GROUP_NO_SITE


def destination_group(destination_kreis: str, home: str, site_kreise: frozenset) -> str:
    """The destination group of the SrV destination table (same rule as ``srv_student_reference``)."""
    if destination_kreis == home:
        return DEST_OWN
    if destination_kreis in site_kreise:
        return DEST_SITE_PREFIX + destination_kreis
    if destination_kreis in _flows_inputs.ZGB_KREISE:
        return DEST_OTHER_REGION
    if destination_kreis == REGION_HANNOVER_ARS5:
        return DEST_REGION_HANNOVER
    if destination_kreis == LANDKREIS_HILDESHEIM_ARS5:
        return DEST_LK_HILDESHEIM
    return DEST_OTHER_OUTSIDE


def destination_vocabulary(site_kreise: frozenset, group: str) -> list[str]:
    sites = sorted(site_kreise - ({BRAUNSCHWEIG_KREIS} if group == HOME_GROUP_BRAUNSCHWEIG else set()))
    return [DEST_OWN, *(DEST_SITE_PREFIX + kreis for kreis in sites), DEST_OTHER_REGION, DEST_REGION_HANNOVER,
            DEST_LK_HILDESHEIM, DEST_OTHER_OUTSIDE]


def destination_shares_by_home_group(home_kreis: pd.Series, destination_kreis: pd.Series, site_kreise: frozenset,
                                     srv: pd.DataFrame) -> pd.DataFrame:
    """Model destination shares per home group against ``srv2023_student_education_destination.csv``.

    ``home_kreis`` and ``destination_kreis`` are aligned per person (the residents of the day at a destination).
    Columns: ``home_group, destination_group, n_model_group, n_model, model_share, n_srv_group, srv_share,
    srv_se_share, difference_in_se`` and, repeated on every row of a group, ``tvd`` (total-variation distance) and
    ``srv_noise_floor_tvd_95``. Raises ``ValueError`` on a person whose group is outside the vocabulary.
    """
    groups = pd.Series([home_group(k, site_kreise) for k in home_kreis], index=home_kreis.index, dtype=object)
    dest = pd.Series([destination_group(d, h, site_kreise) for d, h in zip(destination_kreis, home_kreis)],
                     index=home_kreis.index, dtype=object)
    srv = srv.set_index(["home_group", "destination_group"])
    rows = []
    for group in HOME_GROUPS:
        selected = dest if group == HOME_GROUP_ALL else dest[groups == group]
        vocabulary = destination_vocabulary(site_kreise, group)
        unexpected = sorted(set(selected) - set(vocabulary))
        if unexpected:
            raise ValueError(f"{_LOG_TAG} destination group(s) {unexpected} are outside the vocabulary of {group}")
        counts = selected.value_counts().reindex(vocabulary, fill_value=0)
        n = int(counts.sum())
        model = counts / n if n else pd.Series(np.nan, index=vocabulary)
        reference = srv.loc[group].reindex(vocabulary)
        srv_share = reference["share"].astype(float)
        tvd = float(0.5 * (model - srv_share).abs().sum()) if n and srv_share.notna().all() else math.nan
        for label in vocabulary:
            se = float(reference.loc[label, "se_share"])
            rows.append({
                "home_group": group, "destination_group": label, "n_model_group": n, "n_model": int(counts[label]),
                "model_share": float(model[label]), "n_srv_group": reference.loc[label, "n_persons_group"],
                "srv_share": float(srv_share[label]), "srv_se_share": se,
                "difference_in_se": (float(model[label]) - float(srv_share[label])) / se if se > 0 else math.nan,
                "tvd": tvd, "srv_noise_floor_tvd_95": float(reference.loc[label, "noise_floor_tvd_95"])})
    return pd.DataFrame(rows)


def education_leg_by_status(frame: pd.DataFrame, srv: pd.DataFrame) -> pd.DataFrame:
    """Share with an education activity among the present adults (18+), by status, against the SrV table.

    ``group`` is ``student`` (``P_TAET == STUDENT_P_TAET``) or ``other_adult``; persons without a readable
    ``P_TAET`` are in neither (counted in the report). Rows per home Kreis and one total (``03ZGB``), age range
    ``18plus``. The SrV universe is persons at home or mobile on the reporting day, i.e. the present persons.
    """
    adults = frame[(frame["age"] >= 18) & frame["present"] & frame["p_taet"].notna()]
    reference = srv[srv["age_range"] == "18plus"].set_index(["code", "group"])
    rows = []
    for code in [*sorted(set(frame["home_kreis"])), SRV_TOTAL_CODE]:
        members = adults if code == SRV_TOTAL_CODE else adults[adults["home_kreis"] == code]
        for group, mask in (("student", members["is_student"]), ("other_adult", ~members["is_student"])):
            selected = members[mask]
            n, n_leg = len(selected), int(selected["activity"].sum())
            ref = reference.loc[(code, group)] if (code, group) in reference.index else None
            share = n_leg / n if n else math.nan
            se = float(ref["se_share"]) if ref is not None else math.nan
            srv_share = float(ref["share"]) if ref is not None else math.nan
            rows.append({"code": code, "group": group, "age_range": "18plus", "n_present": n,
                         "n_with_education_activity": n_leg, "model_share": share, "srv_share": srv_share,
                         "srv_se_share": se,
                         "difference_in_se": (share - srv_share) / se if se > 0 and math.isfinite(share) else math.nan})
    return pd.DataFrame(rows)


def routed_level_by_status(frame: pd.DataFrame) -> pd.DataFrame:
    """``[group, routed_level, persons]``: the present persons with an education activity by status and level."""
    members = frame[frame["activity"] & frame["present"] & frame["routed_level"].notna()].copy()
    members["group"] = np.where(members["p_taet"].isna(), "status_unreadable",
                                np.where(members["is_student"], "student", "not_student"))
    return (members.groupby(["group", "routed_level"]).size().rename("persons").reset_index())


def non_students_moved(frame: pd.DataFrame) -> pd.DataFrame:
    """``[routed_level, persons]`` of the persons aged 20 or older with an education activity who are no student.

    The rule OFF would send all of them to the university level; ON they take ``bbs_adult`` or
    ``adult_education`` and none may sit at a university building (``university`` must be 0 or absent).
    """
    members = frame[frame["activity"] & ~frame["is_student"] & (frame["age"] >= 20) & frame["routed_level"].notna()]
    return members.groupby("routed_level").size().rename("persons").reset_index()


def allotment_by_kreis(assigned: pd.DataFrame, centres: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """The allotment diagnostics of Task 9 per home Kreis, with the random-allotment baseline.

    Args:
        assigned: ``[person_id, home_kreis, destination_id, path, x, y, leg_km]``, the present persons of Kreise
            with a fitted row (every person of such a Kreis).
        centres: ``university_assignment.destination_centres`` of the facilities.
    """
    rows = []
    for kreis, members in assigned.groupby("home_kreis", sort=True):
        diagnostics = _university_assignment.allotment_diagnostics(
            members[["person_id", "destination_id", "path", "home_kreis"]], members[["person_id", "x", "y", "leg_km"]],
            centres, kreis_column="home_kreis")
        totals = members.groupby("destination_id").size()
        random_km = _university_assignment.random_allotment_mean_distance(
            members[["person_id", "x", "y", "leg_km"]], totals, centres, rng)
        row = {"home_kreis": kreis, "n_persons": diagnostics["n_persons"]}
        for path in (_university_assignment.PATH_PLAN_LENGTH, _university_assignment.PATH_FALLBACK_DECAY,
                     _university_assignment.PATH_HOME_DISTANCE):
            row[f"n_{path}"] = diagnostics["n_by_path"].get(path, 0)
        for key in ("n_with_observed_leg", "mean_abs_leg_gap_km", "mean_distance_km"):
            row[key] = diagnostics[key]
        row["random_mean_distance_km"] = random_km
        row["mean_distance_ratio_to_random"] = (diagnostics["mean_distance_km"] / random_km
                                                if random_km and math.isfinite(random_km) else math.nan)
        for key in ("radius_km", "n_near_local_site", "n_near_local_site_sent_elsewhere",
                    "n_near_local_site_with_place", "n_near_local_site_with_place_sent_elsewhere"):
            row[key] = diagnostics[key]
        rows.append(row)
    return pd.DataFrame(rows)


def fallback_entry(tag: str, n: int, n_fallback: int, warn_share: float, meaning: str) -> dict:
    """One fallback rate, logged as ``[tag] primary n/N (x%), fallback m (y%)``; a WARNING above ``warn_share``."""
    primary = n - n_fallback
    share = n_fallback / n if n else 0.0
    message = (f"[{tag}] primary {primary}/{n} ({100.0 * (1 - share) if n else 100.0:.1f}%), fallback {n_fallback} "
               f"({100.0 * share:.1f}%); {meaning}")
    (LOGGER.warning if share > warn_share else LOGGER.info)("%s %s (warn above %g)", _LOG_TAG, message, warn_share)
    return {"tag": tag, "n": int(n), "primary": int(primary), "fallback": int(n_fallback), "fallback_share": share,
            "warn_share": float(warn_share), "meaning": meaning}


def kreis_mismatch(home_kreis: pd.Series, vg250_kreis: pd.Series) -> dict:
    """Households whose Kreis by home zone differs from the VG250 join of ``assign_geographies``.

    Both series are indexed by ``household_id``; a household without a VG250 Kreis is counted apart, a household
    missing from ``vg250_kreis`` raises. Reported, never corrected: production reads the home-zone Kreis only.
    """
    missing = home_kreis.index.difference(vg250_kreis.index)
    if len(missing):
        raise ValueError(f"{_LOG_TAG} {len(missing)} household(s) have no row in the VG250 join, first "
                         f"{list(missing[:5])}")
    other = vg250_kreis.reindex(home_kreis.index)
    unmatched = other.isna()
    different = ~unmatched & (other.astype(object) != home_kreis.astype(object))
    pairs = (pd.DataFrame({"zone": home_kreis[different], "vg250": other[different]}).groupby(["zone", "vg250"])
             .size().sort_values(ascending=False).head(10))
    return {"n_households": int(len(home_kreis)), "n_without_vg250_kreis": int(unmatched.sum()),
            "n_different": int(different.sum()),
            "top_pairs": [{"home_zone_kreis": z, "vg250_kreis": v, "n": int(n)} for (z, v), n in pairs.items()]}


def committed_balance_check(od: pd.DataFrame, by_site: pd.DataFrame, out_commuters: pd.DataFrame,
                            tolerance_persons: float = 1e-3) -> dict:
    """Compare the committed balance tables with T: a mismatch is counted and reported, not raised.

    Per site: total and in-commuters of ``student_balance_by_site.csv`` against T's rows of the site; per surrounding
    institution: ``out_commuters_persons`` against T's region rows.
    """
    by_destination = od.groupby("destination_id")["students_persons"].sum()
    outside = od[~od["home_in_region"]].groupby("destination_id")["students_persons"].sum()
    region = od[od["home_in_region"]].groupby("destination_id")["students_persons"].sum()
    checks = []
    for _, row in by_site.iterrows():
        site = row["destination_id"]
        checks.append((site, "total_persons", float(row["total_persons"]), float(by_destination.get(site, 0.0))))
        checks.append((site, "in_commuters_persons", float(row["in_commuters_persons"]), float(outside.get(site, 0.0))))
    for _, row in out_commuters.iterrows():
        site = row["destination_id"]
        checks.append((site, "out_commuters_persons", float(row["out_commuters_persons"]),
                       float(region.get(site, 0.0))))
    bad = [{"destination_id": s, "quantity": q, "table": a, "t": b} for s, q, a, b in checks
           if abs(a - b) > tolerance_persons]
    return {"n_checked": len(checks), "n_mismatch": len(bad), "mismatches": bad[:10]}


def incommuter_commute_table(incommuters: pd.DataFrame) -> pd.DataFrame:
    """Per origin Kreis (and ``all``): agents, routed commute length statistics, source and home-branch counts."""
    if incommuters.empty:
        return pd.DataFrame(columns=["orig_ars5", "n", "mean_km", "median_km", "p90_km", "n_od_cell_fallback",
                                     "n_gate_home"])
    rows = []
    for origin, members in [*incommuters.groupby("orig_ars5", sort=True), (ALL_GROUP, incommuters)]:
        km = members["commute_length_km"].astype(float)
        rows.append({"orig_ars5": origin, "n": int(len(members)), "mean_km": float(km.mean()),
                     "median_km": float(km.median()), "p90_km": float(km.quantile(0.9)),
                     "n_od_cell_fallback": int((members["commute_length_source"] == COMMUTE_OD_CELL).sum()),
                     "n_gate_home": int((members["home_branch"] == HOME_BRANCH_GATE).sum())})
    return pd.DataFrame(rows)


# =================================================================================== stage
def _srv_dir(data_path) -> str:
    return os.path.join(str(data_path), *SRV_SUBDIR)


def table_paths(context) -> dict:
    """The committed tables the stage reads, ``{label: absolute path}`` (they enter ``validate`` and ``execute``)."""
    data_path = str(context.config("data_path"))
    paths = {name: os.path.join(_srv_dir(data_path), name) for name in SRV_TABLES}
    paths[KEY_OD_TABLE_PATH] = os.path.join(data_path, context.config(KEY_OD_TABLE_PATH))
    paths[KEY_LEVEL_TABLE_PATH] = os.path.join(data_path, context.config(KEY_LEVEL_TABLE_PATH))
    paths[BALANCE_BY_SITE_TABLE] = os.path.join(data_path, *SCHOOLS_SUBDIR, BALANCE_BY_SITE_TABLE)
    paths[BALANCE_OUT_COMMUTERS_TABLE] = os.path.join(data_path, *SCHOOLS_SUBDIR, BALANCE_OUT_COMMUTERS_TABLE)
    paths["mid_student_education_distance_mode"] = os.path.join(
        data_path, _student_mode_reference.TABLE_RELATIVE_PATH)
    return paths


def validate(context):
    token = synpp_tokens.source_token(_HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
                                      stage_label=__name__ + ".validate()")
    from braunschweig.analysis import spatial  # a deferred helper, imported by source_token
    files = spatial.input_file_digests(strict=True)
    for label, path in table_paths(context).items():
        if not os.path.isfile(path):
            raise RuntimeError(f"{_LOG_TAG} input table {label} not found: {path}")
        files[label] = synpp_tokens.text_file_sha256(path)
    token["files"] = files
    return token


def configure(context):
    # The ON-path columns exist only with the flows switch on; refuse at DAG time, before the population is built.
    if not context.config(KEY_FLOWS_ENABLED, True):
        raise ValueError(f"{_LOG_TAG} {KEY_FLOWS_ENABLED} is false: the acceptance table counts the ON path of the "
                         f"student flows; enable it or drop this stage from the run list")
    context.stage(ENRICHED_STAGE)
    context.stage(HOME_ZONES_STAGE)
    context.stage(HOME_LOCATIONS_STAGE)
    context.stage(TRIPS_STAGE)
    context.stage(GRAVITY_STAGE)
    context.stage(FACILITIES_STAGE)
    context.stage(INCOMMUTERS_STAGE)
    absence_enabled = context.config(KEY_DAY_ABSENCE_ENABLED, True)
    if absence_enabled:
        if not context.config(KEY_DAY_ABSENCE_STUDENT_CELLS, True):
            raise ValueError(f"{_LOG_TAG} {KEY_DAY_ABSENCE_ENABLED} is on and {KEY_DAY_ABSENCE_STUDENT_CELLS} is off: "
                             f"the student flows are refused in that configuration (design 5a)")
        context.stage(ABSENCE_STAGE)
    context.config("data_path")
    context.config("output_path")
    context.config("sampling_rate")
    context.config("random_seed")
    context.config(KEY_OD_TABLE_PATH, DEFAULT_OD_TABLE_PATH)
    context.config(KEY_LEVEL_TABLE_PATH, DEFAULT_LEVEL_TABLE_PATH)
    context.config(_flows_inputs.KEY_HOME_AREA_KREISE, _flows_inputs.default_home_area_kreise())
    context.config(KEY_MIN_PERSONS, DEFAULT_MIN_PERSONS)
    context.config(KEY_MAX_DEVIATION_SHARE, DEFAULT_MAX_DEVIATION_SHARE)
    context.config(KEY_DETOUR_SENSITIVITY, DEFAULT_DETOUR_SENSITIVITY)
    context.config(KEY_MIN_SRV_RESPONDENTS, DEFAULT_MIN_SRV_RESPONDENTS)
    context.config(KEY_FALLBACK_WARN_SHARE, DEFAULT_FALLBACK_WARN_SHARE)


def _xy(frame_with_geometry, crs_name: str) -> np.ndarray:
    crs = getattr(frame_with_geometry, "crs", None)
    if crs is None or crs.to_epsg() != _university_assignment.PROJECTED_EPSG:
        raise ValueError(f"{_LOG_TAG} {crs_name} must be in EPSG:{_university_assignment.PROJECTED_EPSG}, got {crs}")
    return np.column_stack([frame_with_geometry.geometry.x.to_numpy(dtype=float),
                            frame_with_geometry.geometry.y.to_numpy(dtype=float)])


def _write_csv(table: pd.DataFrame, directory: str, name: str) -> None:
    table.to_csv(os.path.join(directory, name), index=False, float_format="%.10g", lineterminator="\n",
                 encoding="utf-8")


def summary_markdown(report: dict, acceptance: pd.DataFrame, failing: pd.DataFrame) -> str:
    """A short human-readable account; every number is in a CSV or the report."""
    acc = report["acceptance"]
    lines = ["# Student flows: acceptance table and diagnostics", "",
             f"Status: {STATUS}. Counts the synthesis (cached population and in-commuter agents), not a simulation.", "",
             f"- Verdict: **{acc['verdict']}** (rule ASSUMPTION A17: balance >= {acc['min_persons']:g} persons and "
             f"|deviation| > {acc['max_deviation_share']:.1%}; {acc['n_rows_judged']} row(s) judged)",
             f"- sampling rate {report['parameters']['sampling_rate']}, absence mode {report['day_factor']['absence_mode']}, "
             f"e {report['day_factor']['e']:.4f}, f_eff {report['day_factor']['f_eff']:.4f}"]
    if acc["verdict"] == VERDICT_REPORT_ONLY:
        lines.append("- No row reaches the minimum: this table is a report and the rule needs a larger sample.")
    if acc["verdict"] == VERDICT_FAIL:
        lines.append("- The student flows are not used for results on a population that fails the population rows "
                     f"(population rows failing: {len(acc['population_rows_failing'])}).")
    if len(failing):
        lines += ["", "## Rows over the bound", "", "| section | key | model | balance | deviation share |",
                  "|---|---|---|---|---|"]
        lines += [f"| {r.section} | {r.key} | {r.model:.1f} | {r.balance:.1f} | {r.deviation_share:+.1%} |"
                  for r in failing.itertuples()]
    rows = report["srv_distance_rows"]
    lines += ["", "## SrV distance rows", "",
              f"- Usable (at least {rows['min_respondents']} respondents, ASSUMPTION A14): "
              f"{', '.join(rows['usable']) or 'none'} (`all` is the SrV total row {SRV_TOTAL_CODE})",
              f"- Reported only (fewer respondents, a difference there says little): "
              f"{', '.join(rows['reported_only']) or 'none'}"]
    lines += ["", "## Fallback rates", ""]
    lines += [f"- {e['tag']}: primary {e['primary']}/{e['n']}, fallback {e['fallback']} ({e['fallback_share']:.1%})"
              for e in report["fallback_rates"]]
    lines += ["", "## Skipped", ""] + [f"- {k}: {v}" for k, v in report["skipped"].items()]
    lines += ["", "## Files", ""] + [f"- `{name}`" for name in OUTPUT_FILES.values()]
    return "\n".join(lines) + "\n"


def execute(context):
    from braunschweig.analysis import spatial  # VG250 access only at run time (pulls geopandas and the archive)

    data_path = str(context.config("data_path"))
    out_dir = os.path.join(str(context.config("output_path")), OUTPUT_SUBDIR)
    rate = float(context.config("sampling_rate"))
    min_persons = float(context.config(KEY_MIN_PERSONS))
    max_share = float(context.config(KEY_MAX_DEVIATION_SHARE))
    warn_share = float(context.config(KEY_FALLBACK_WARN_SHARE))
    min_srv_respondents = int(context.config(KEY_MIN_SRV_RESPONDENTS))
    # The yardstick first, then the sensitivity values; a value equal to one already listed is dropped.
    detour_factors = list(dict.fromkeys(
        [float(_circuity.LEGACY_DETOUR_FACTOR), *[float(f) for f in context.config(KEY_DETOUR_SENSITIVITY)]]))
    absence_on = bool(context.config(KEY_DAY_ABSENCE_ENABLED))
    paths = table_paths(context)
    for label, path in paths.items():
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{_LOG_TAG} input table {label} not found: {path}")

    # ---- inputs
    od = _flows_inputs.load_od_table(paths[KEY_OD_TABLE_PATH])
    level = _flows_inputs.load_level(paths[KEY_LEVEL_TABLE_PATH], _flows_inputs.ZGB_KREISE)
    by_site = pd.read_csv(paths[BALANCE_BY_SITE_TABLE], comment="#", encoding="utf-8")
    out_table = pd.read_csv(paths[BALANCE_OUT_COMMUTERS_TABLE], comment="#", encoding="utf-8")
    srv_distance = pd.read_csv(paths[SRV_DISTANCE_TABLE], comment="#", encoding="utf-8", dtype={"code": str})
    srv_destination = pd.read_csv(paths[SRV_DESTINATION_TABLE], comment="#", encoding="utf-8")
    srv_leg = pd.read_csv(paths[SRV_LEG_BY_STATUS_TABLE], comment="#", encoding="utf-8", dtype={"code": str})

    absence_mode = _flows_inputs.ABSENCE_MODE_CELLS if absence_on else _flows_inputs.ABSENCE_MODE_OFF
    e = _flows_inputs.education_leg_share(_srv_dir(data_path))
    away = _flows_inputs.away_share_by_area(_srv_dir(data_path)) if absence_on else {}
    area_of_kreis = _flows_inputs.area_by_kreis(context.config(_flows_inputs.KEY_HOME_AREA_KREISE),
                                                _flows_inputs.ZGB_KREISE)
    level_by_kreis = level.groupby("code")["students_persons"].sum()
    f_by_kreis = _flows_inputs.day_factor_by_kreis(e, away, area_of_kreis, absence_mode)
    f_eff = _flows_inputs.effective_day_factor(level_by_kreis, f_by_kreis)
    LOGGER.info("%s e %.6f, f_eff %.6f (absence mode %s), sampling rate %s", _LOG_TAG, e, f_eff, absence_mode, rate)

    enriched = context.stage(ENRICHED_STAGE)
    home_kreis = _flows_inputs.home_kreis_by_household(context.stage(HOME_ZONES_STAGE))
    homes = context.stage(HOME_LOCATIONS_STAGE)
    gravity = context.stage(GRAVITY_STAGE)
    facilities = context.stage(FACILITIES_STAGE)
    trips = context.stage(TRIPS_STAGE)
    incommuters = context.stage(INCOMMUTERS_STAGE)["persons"]
    day_state, absence_diagnostics = None, None
    if absence_on:
        absence_stage = context.stage(ABSENCE_STAGE)
        absence = absence_stage["absence"]
        if absence["person_id"].duplicated().any():
            raise ValueError(f"{_LOG_TAG} {ABSENCE_STAGE} holds a person more than once")
        day_state = absence.set_index("person_id")["day_absence_state"]
        absence_diagnostics = absence_stage["diagnostics"]
    for column in ("routed_level", "destination_id", "assignment_path"):
        if column not in gravity.columns:
            raise ValueError(f"{_LOG_TAG} {GRAVITY_STAGE} lacks the ON-path column {column!r}: run it with "
                             f"{KEY_FLOWS_ENABLED} on")

    # ---- the person frame and the counts
    frame = person_table(enriched, home_kreis, gravity, day_state)
    residents = frame[resident_mask(frame)]
    state_path_mismatch = int((frame[frame["destination_id"].notna()]["present"]
                               != resident_mask(frame)[frame["destination_id"].notna()]).sum())
    if state_path_mismatch:
        LOGGER.error("%s %d persons whose day_absence_state disagrees with their assignment path (absent_*): the "
                     "location stage and the absence stage do not come from the same population", _LOG_TAG,
                     state_path_mismatch)
    counts = student_counts(frame)
    n_students_outside_level = students_outside_level(frame, level_by_kreis.index)
    (LOGGER.warning if n_students_outside_level else LOGGER.info)(
        "%s adult students whose home Kreis has no row of the level table (excluded from the population rows): %d",
        _LOG_TAG, n_students_outside_level)
    check_incommuter_ids(incommuters, enriched["person_id"])
    has_commuters = len(incommuters) > 0 and "destination_id" in incommuters.columns
    if not has_commuters:
        LOGGER.warning("%s the in-commuter stage holds no agent with a site (cordon or student injection off?): the "
                       "in-commuter rows will deviate by -100%%", _LOG_TAG)
    incommuters_at = incommuters.groupby("destination_id").size() if has_commuters else pd.Series(dtype=int)
    incommuters_from = incommuters.groupby("orig_ars5").size() if has_commuters else pd.Series(dtype=int)

    destinations = (facilities[["destination_id", "scope"]].drop_duplicates("destination_id")
                    .reset_index(drop=True))
    region_shares, dropped = _flows_inputs.destination_shares(od)
    outside = od[~od["home_in_region"]]
    table = acceptance_table(
        destinations=destinations, enrollment=by_site.set_index("destination_id")["enrollment_persons"],
        region_shares=region_shares, outside_heads=outside.groupby("destination_id")["students_persons"].sum(),
        outside_heads_by_origin=outside.groupby("home_ars5")["students_persons"].sum(), level=level,
        f_by_kreis=f_by_kreis, f_eff=f_eff, e=e, rate=rate,
        residents_at=residents.groupby("destination_id").size(), incommuters_at=incommuters_at,
        incommuters_from=incommuters_from, students_by_kreis=counts["students_by_kreis"],
        students_of_day_by_kreis=counts["students_of_day_by_kreis"],
        students_30plus_by_kreis=counts["students_30plus_by_kreis"])
    verdict = acceptance_verdict(table, min_persons, max_share)
    failing = failing_rows(table, min_persons, max_share)
    population_failing = failing[failing["section"].isin(["students_population", "students_of_day"])]
    judged = table[table["rule_applies"].astype(bool) & (table["balance"] >= min_persons)]
    message = (f"{_LOG_TAG} acceptance verdict {verdict}: {len(judged)} row(s) judged (balance >= {min_persons:g}), "
               f"{len(failing)} over {max_share:.1%}")
    if verdict == VERDICT_FAIL:
        LOGGER.error("%s; the student flows are not used for results on a population that fails the population "
                     "rows (%d population row(s) failing); first rows %s", message, len(population_failing),
                     failing[["section", "key", "deviation_share"]].head(5).to_dict("records"))
    elif verdict == VERDICT_REPORT_ONLY:
        LOGGER.warning("%s; no row reaches the minimum, the table is a report and the rule needs a larger sample",
                       message)
    else:
        LOGGER.info(message)

    # ---- diagnostics
    flows = realised_flows(frame, incommuters if has_commuters else incommuters.iloc[0:0], od)

    home_xy = pd.DataFrame(_xy(homes, "the home locations"), columns=["x", "y"], index=homes["household_id"].to_numpy())
    person_home = enriched.set_index("person_id")["household_id"]
    university = residents[residents["routed_level"] == "university"]
    in_gravity = gravity.set_index("person_id")
    building_xy = pd.DataFrame(_xy(gravity, "the education locations"), columns=["bx", "by"],
                               index=gravity["person_id"].to_numpy())
    household = university["person_id"].map(person_home)
    hx = household.map(home_xy["x"]).to_numpy(dtype=float)
    hy = household.map(home_xy["y"]).to_numpy(dtype=float)
    km = np.hypot(hx - university["person_id"].map(building_xy["bx"]).to_numpy(dtype=float),
                  hy - university["person_id"].map(building_xy["by"]).to_numpy(dtype=float)) / 1000.0
    if np.isnan(km).any():
        raise ValueError(f"{_LOG_TAG} {int(np.isnan(km).sum())} resident student(s) without a home or building point")
    km_by_group = {ALL_GROUP: km, **{k: km[(university["home_kreis"] == k).to_numpy()]
                                     for k in sorted(set(university["home_kreis"]))}}
    references = srv_band_references(srv_distance)
    skipped = {"mikrozensus_distance_bands": "no committed Mikrozensus table of the students' education distance: "
                                             "braunschweig/mikrozensus holds the commute distance and mode of the "
                                             "employed only"}
    mid_path = paths["mid_student_education_distance_mode"]
    references.append(mid_band_reference(mid_path))
    bands = band_comparison(km_by_group, references, detour_factors, min_srv_respondents)
    distance_rows = usable_distance_rows(bands)
    LOGGER.info("%s SrV distance rows with at least %d respondents (usable): %s; reported only (too few "
                "respondents): %s", _LOG_TAG, min_srv_respondents, distance_rows["usable"],
                distance_rows["reported_only"])
    LOGGER.info("%s skipped: %s", _LOG_TAG, skipped["mikrozensus_distance_bands"])

    site_kreise = frozenset(od.loc[od["scope"] == "local", "site_kreis_ars5"])
    dest_kreis = od.drop_duplicates("destination_id").set_index("destination_id")["site_kreis_ars5"]
    unknown_destinations = sorted(set(university["destination_id"]) - set(dest_kreis.index))
    if unknown_destinations:
        raise ValueError(f"{_LOG_TAG} destinations of the residents without a row in T: {unknown_destinations[:5]}")
    shares_table = destination_shares_by_home_group(
        university["home_kreis"].reset_index(drop=True),
        university["destination_id"].map(dest_kreis).reset_index(drop=True), site_kreise, srv_destination)

    leg_table = education_leg_by_status(frame, srv_leg)
    level_table = routed_level_by_status(frame)
    moved = non_students_moved(frame)
    n_university_non_students = int(moved.loc[moved["routed_level"] == "university", "persons"].sum()) \
        if len(moved) else 0

    allotted_paths = (_university_assignment.PATH_PLAN_LENGTH, _university_assignment.PATH_FALLBACK_DECAY,
                      _university_assignment.PATH_HOME_DISTANCE)
    allotted = university[university["assignment_path"].isin(allotted_paths)]
    allotted_household = allotted["person_id"].map(person_home)
    leg_km = _university_assignment.observed_education_leg_km(trips)
    assigned = pd.DataFrame({
        "person_id": allotted["person_id"].to_numpy(), "home_kreis": allotted["home_kreis"].to_numpy(),
        "destination_id": allotted["destination_id"].to_numpy(), "path": allotted["assignment_path"].to_numpy(),
        "x": allotted_household.map(home_xy["x"]).to_numpy(dtype=float),
        "y": allotted_household.map(home_xy["y"]).to_numpy(dtype=float),
        "leg_km": allotted["person_id"].map(leg_km).to_numpy(dtype=float)})
    centres = _university_assignment.destination_centres(facilities)
    allotment = allotment_by_kreis(assigned, centres, np.random.default_rng(
        [int(context.config("random_seed")), BASELINE_STREAM_ID])) if len(assigned) else pd.DataFrame()
    commute = incommuter_commute_table(incommuters if has_commuters else incommuters.iloc[0:0])

    # ---- fallback rates of Tasks 5, 6, 9, 10, 11
    activity = frame[frame["activity"]]
    rates = [fallback_entry("student status (Task 5)", len(activity), int(activity["p_taet"].isna().sum()), 0.0,
                            "persons with an education activity whose P_TAET is unreadable (design B1 fallback rule)")]
    if absence_diagnostics and "n_adults_unreadable_p_taet" in absence_diagnostics:
        rates.append(fallback_entry("absence cells P_TAET (Task 6)", int(absence_diagnostics["n_adults"]),
                                    int(absence_diagnostics["n_adults_unreadable_p_taet"]), 0.0,
                                    "adults without a readable P_TAET, drawn at the age-band rate"))
    else:
        skipped["absence_cells_fallback"] = "the absence stage reports no cells diagnostics (day absence off)"
    paths_all = university["assignment_path"].astype(str)
    present_allotted = paths_all.isin(allotted_paths)
    absent_allotted = paths_all.isin([ABSENT_PATH_PREFIX + p for p in allotted_paths])
    rates.append(fallback_entry("university plan rule, present (Task 9)", int(present_allotted.sum()),
                                int((paths_all == _university_assignment.PATH_FALLBACK_DECAY).sum()), warn_share,
                                "present students without an observed education leg, placed by the university decay"))
    rates.append(fallback_entry("university plan rule, absent (Task 9)", int(absent_allotted.sum()),
                                int((paths_all == ABSENT_PATH_PREFIX + _university_assignment.PATH_FALLBACK_DECAY).sum()),
                                warn_share, "absent students, same fallback"))
    legacy = paths_all.isin([PATH_LEGACY_DECAY, ABSENT_PATH_PREFIX + PATH_LEGACY_DECAY])
    rates.append(fallback_entry("legacy decay, Kreis without a row of T (Task 10)", len(university),
                                int(legacy.sum()), 0.0, "students of a home Kreis without a row of T (must be 0)"))
    if has_commuters:
        rates.append(fallback_entry("in-commuter commute length (Task 11)", len(incommuters),
                                    int((incommuters["commute_length_source"] == COMMUTE_OD_CELL).sum()), warn_share,
                                    "commute length on the distance of T's cell instead of a drawn commune"))
        rates.append(fallback_entry("in-commuter home rule (Task 11)", len(incommuters),
                                    int((incommuters["home_branch"] == HOME_BRANCH_GATE).sum()), warn_share,
                                    "agents placed at the nearest cordon gate instead of a point in the ring"))

    # ---- the VG250 cross-check of the home Kreis
    vg250 = spatial.assign_geographies(homes[["household_id", "geometry"]])
    if vg250["household_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} the VG250 join names a household more than once")
    mismatch = kreis_mismatch(home_kreis, vg250.set_index("household_id")["ars5"])
    (LOGGER.warning if mismatch["n_different"] or mismatch["n_without_vg250_kreis"] else LOGGER.info)(
        "%s home Kreis by home zone vs VG250 join: %d of %d households differ, %d without a VG250 Kreis",
        _LOG_TAG, mismatch["n_different"], mismatch["n_households"], mismatch["n_without_vg250_kreis"])
    balance_check = committed_balance_check(od, by_site, out_table)
    if balance_check["n_mismatch"]:
        LOGGER.warning("%s committed balance tables differ from T in %d of %d checks: %s", _LOG_TAG,
                       balance_check["n_mismatch"], balance_check["n_checked"], balance_check["mismatches"])

    report = {
        "status": STATUS, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {"sampling_rate": rate, "detour_factors": detour_factors, "day_absence_enabled": absence_on,
                       "baseline_stream_id": BASELINE_STREAM_ID,
                       "random_seed": int(context.config("random_seed")),
                       KEY_FALLBACK_WARN_SHARE: warn_share},
        "inputs": {label: {"path_relative_to_data_path": os.path.relpath(path, data_path).replace(os.sep, "/"),
                           "sha256": synpp_tokens.text_file_sha256(path)} for label, path in paths.items()},
        "day_factor": {"absence_mode": absence_mode, "e": e, "f_eff": f_eff,
                       "f_by_kreis": {k: float(v) for k, v in f_by_kreis.items()}},
        "acceptance": {"verdict": verdict, "min_persons": min_persons, "max_deviation_share": max_share,
                       "n_rows": int(len(table)), "n_rows_judged": int(len(judged)),
                       "failing_rows": failing[["section", "key", "model", "balance", "deviation_share"]]
                       .to_dict("records"),
                       "population_rows_failing": population_failing[["section", "key", "deviation_share"]]
                       .to_dict("records")},
        "counts": {"n_persons": int(len(frame)), "n_with_education_activity": int(frame["activity"].sum()),
                   "n_residents_of_day_at_a_destination": int(len(residents)),
                   "n_incommuter_agents": int(len(incommuters)) if has_commuters else 0,
                   "n_persons_unreadable_p_taet": int(frame["p_taet"].isna().sum()),
                   "n_adult_students_outside_level_kreise": n_students_outside_level,
                   "n_state_path_mismatch": state_path_mismatch,
                   "n_non_students_at_a_university_building": n_university_non_students,
                   "dropped_zero_share_destinations": list(dropped)},
        "srv_distance_rows": {"min_respondents": min_srv_respondents, **distance_rows},
        "fallback_rates": rates, "home_kreis_vs_vg250": mismatch, "committed_balance_vs_t": balance_check,
        "skipped": skipped,
    }
    os.makedirs(out_dir, exist_ok=True)
    for key, name in (("acceptance", table), ("flows", flows), ("distance_bands", bands),
                      ("destination_shares", shares_table), ("allotment", allotment), ("leg_by_status", leg_table),
                      ("routed_level", level_table), ("non_students", moved), ("incommuter_commute", commute)):
        _write_csv(name, out_dir, OUTPUT_FILES[key])
    json_output.write_json(os.path.join(out_dir, REPORT_FILE), report)
    json_output.write_json(os.path.join(str(context.path()), REPORT_FILE), report)
    with open(os.path.join(out_dir, OUTPUT_FILES["summary"]), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(summary_markdown(report, table, failing))
    LOGGER.info("%s wrote the acceptance table (%s), %d diagnostics files and the report to %s", _LOG_TAG, verdict,
                len(OUTPUT_FILES) - 2, out_dir)
    return {"verdict": verdict, "acceptance_table": table, "report": report}
