"""Per-person education-location assignment for Braunschweig.

School-age pupils (6-19) are placed by the capacity-constrained distance-decay
gravity model on the real NDS school facilities; kindergarten (0-5) uses the
same doubly-constrained capacity gravity model on real Kita facilities
(``braunschweig.data.schools.kita_facilities``); university students (20+) are
routed through the singly-constrained ``assign_by_decay`` on real university
facilities (``braunschweig.data.schools.university_facilities``). Output schema
matches the legacy ``eqasim_common.locations.synthesis.education`` so it is a
drop-in replacement.

With the student flows switch (``braunschweig.students.flows_enabled``, issue #140, Phase 4, design B5) the
persons of level ``university`` are placed by the residents' flows instead: a flow matrix home Kreis x
destination fitted on the persons PRESENT on the simulated day (``university_assignment.fit_flow_matrix``),
rounded without bias (``controlled_round``), allotted inside each home Kreis by the length of the plan's
education leg or the distance from the home (``allot_within_kreis``), then a building drawn by capacity
(``draw_buildings``); see :func:`assign_university_flows`.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import geopandas as gpd

from braunschweig import synpp_tokens
from braunschweig.synthesis.locations import education_gravity_model as _education_gravity_model
from braunschweig.synthesis.locations.education_gravity_model import (
    assign_by_capacity_gravity, assign_by_decay, FALLBACK_WARN_SHARE,
)
from braunschweig.data.bbsr import regiostar as _regiostar
from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.data.education import student_flows_inputs as _student_flows_inputs
from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED
from braunschweig.synthesis.locations import education_routing as _education_routing
from braunschweig.synthesis.locations.education_routing import (
    ADULT_EDUCATION, BBS_ADULT, SCHOOL_BANDS as _SCHOOL_BANDS, age_to_level, route_education_level,
    routing_summary, student_status,
)
from braunschweig.synthesis.locations import university_assignment as _university_assignment
from braunschweig.synthesis.locations.university_assignment import (
    ALLOTMENT_RULES, PATH_FALLBACK_DECAY, RULE_PLAN_LENGTH, allot_within_kreis, allotment_diagnostics,
    controlled_round, destination_centres, draw_buildings, fit_flow_matrix, observed_education_leg_km,
)

logger = logging.getLogger(__name__)

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136). ``bbs_share`` is imported inside
# ``_resolve_bbs_share_by_age``, so it is hashed by name. ``_regiostar`` is the declared stage
# ``braunschweig.data.bbsr.regiostar`` imported as a library (``ars_to_ags8``): the DAG edge covers
# its output, not this code (ADR-0194).
# ``_education_routing`` holds the age bands (every path) and the student routing (ON path);
# ``_student_flows_inputs`` is its only project import (the student status code) and the loader of the table T
# (ON path). ``_university_assignment`` (ON path) imports numpy and pandas only.
_HELPER_MODULES = (
    _education_gravity_model,
    _regiostar,
    _education_routing,
    _student_flows_inputs,
    _university_assignment,
)
# The import closure of ``student_flows_inputs``: a leaf that imports only ``braunschweig.data.csv_header``
# (scripts/audit_synpp_helper_hash.py, ADR-0136). One name per line.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.schools.bbs_share",
    "braunschweig.data.csv_header",
)

#: Share of persons on the student-status fallback above which a WARNING is logged (``education_routing``).
KEY_STATUS_FALLBACK_WARN_SHARE = "braunschweig.students.status_fallback_warn_share"
DEFAULT_STATUS_FALLBACK_WARN_SHARE = 0.0

# ------------------------------------------------------------------ the residents' university flows (ON path, B5)
#: Rule of the allotment inside a home Kreis: ``plan_length`` (the plan's education leg decides, the decay of
#: ``education_university_slope`` the counted fallback) or ``home_distance`` (``university_assignment.ALLOTMENT_RULES``).
#: The default is the owner's choice of 2026-11-27; the measurement against SrV (Task 15) decides it for good.
KEY_KREIS_MATCHING_RULE = "braunschweig.students.kreis_matching_rule"
DEFAULT_KREIS_MATCHING_RULE = RULE_PLAN_LENGTH
#: Largest accepted column deviation of the fit, in persons. A numerical tolerance far below one person (the
#: rounding keeps every column within one person of its fitted sum), not a modelling parameter.
KEY_FIT_TOLERANCE = "braunschweig.students.fit_tolerance"
DEFAULT_FIT_TOLERANCE = 1e-6
#: Largest number of fit iterations before the fit raises. ASSUMPTION: ample for a matrix of eight home Kreise
#: and about twenty destinations; a fit that needs more is near infeasible and should stop the run.
KEY_FIT_MAX_ITERATIONS = "braunschweig.students.fit_max_iterations"
DEFAULT_FIT_MAX_ITERATIONS = 1000
#: Share of the present persons on the fallback of the plan rule (no observed education leg) above which a
#: WARNING is logged. ASSUMPTION, not from a table: to be reset after the 1 % smoke shows the real share.
KEY_MATCHING_FALLBACK_WARN_SHARE = "braunschweig.students.matching_fallback_warn_share"
DEFAULT_MATCHING_FALLBACK_WARN_SHARE = 0.25
#: The table T relative to ``data_path``. The key and its default are those of
#: ``braunschweig.data.schools.university_facilities`` (KEY_OD_TABLE_PATH), repeated because of the import boundary
#: of Phase 4 and pinned by ``tests/test_student_flows_switches.py``.
KEY_OD_TABLE_PATH = "braunschweig.students.od_table_path"
DEFAULT_OD_TABLE_PATH = "braunschweig/schools/student_od_table.csv"
#: The switches of the day-absence stage (owner ``braunschweig.synthesis.day_absence.absence_stage``: KEY_ENABLED,
#: KEY_STUDENT_CELLS and their defaults), repeated because of the import boundary and pinned by the same test.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
KEY_DAY_ABSENCE_STUDENT_CELLS = "day_absence_student_cells_enabled"
DEFAULT_DAY_ABSENCE_STUDENT_CELLS = True
#: The inputs only the ON path declares (owner of the names: ``absence_stage``; pinned).
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"
TRIPS_STAGE = "synthesis.population.trips"
HOME_ZONES_STAGE = "synthesis.population.spatial.home.zones"
#: ``day_absence_state`` of a person present on the simulated day (owner ``day_absence.absence.STATE_PRESENT``).
STATE_PRESENT = "present"
#: The provenance report of the ON path in ``context.path()``; no other stage reads it (Ruling 2).
UNIVERSITY_REPORT_FILE = "education_university_report.json"
#: The university step of the ON path draws from its own generator
#: ``numpy.random.default_rng([random_seed, UNIVERSITY_STREAM_ID])`` (a SeedSequence of the two numbers), so its
#: draws do not depend on how many numbers the other levels took from the stage's ``RandomState(random_seed)``,
#: and the levels drawn after it keep theirs. The id is a fixed constant (the issue number); it only separates
#: this stream from the others of the run.
UNIVERSITY_STREAM_ID = 140
#: ``assignment_path`` of the persons of a home Kreis without a row of T (placed by the legacy decay over the
#: buildings) and the prefix of the paths of the persons absent on the simulated day.
PATH_LEGACY_DECAY = "legacy_decay"
ABSENT_PATH_PREFIX = "absent_"
_UNIVERSITY_TAG = "[education:university]"


@dataclass(frozen=True)
class UniversityFlowInputs:
    """The inputs of the residents' university flows (ON path), keyed by person where they are per person.

    Attributes:
        seed: ``[home_ars5, destination_id, seed]``, the heads of T living in the region
            (``student_flows_inputs.region_seed``).
        shares: ``destination_id -> share`` of the region's heads (``student_flows_inputs.destination_shares``).
        dropped_destinations: the destinations of T without a student of the region (reported only).
        home_kreis: ``person_id -> 5-digit home Kreis`` (``student_flows_inputs.home_kreis_by_household``).
        leg_km: ``person_id -> observed education leg in km`` (``university_assignment.observed_education_leg_km``);
            a person absent from it or with NaN has no observed leg.
        day_state: ``person_id -> day_absence_state`` of the day-absence stage, or None when the day absence is
            switched off (every person present).
        rule: ``braunschweig.students.kreis_matching_rule``.
        fit_tolerance, fit_max_iterations: the fit's settings.
        fallback_warn_share: ``braunschweig.students.matching_fallback_warn_share``.
        rng: the dedicated generator of the university step (:data:`UNIVERSITY_STREAM_ID`).
    """
    seed: pd.DataFrame
    shares: pd.Series
    dropped_destinations: list
    home_kreis: pd.Series
    leg_km: pd.Series
    day_state: pd.Series | None
    rule: str
    fit_tolerance: float
    fit_max_iterations: int
    fallback_warn_share: float
    rng: np.random.Generator


def check_university_flow_settings(rule, day_absence_enabled, day_absence_student_cells_enabled) -> None:
    """Refuse the configurations of the ON path the design rules out (checked at configure time).

    - ``kreis_matching_rule`` must be one of ``university_assignment.ALLOTMENT_RULES``.
    - Day absence ON with its student cells OFF: the students would be thinned at the rate of their age band while
      the balance counts with the away share of their home area (design 5a item 2); the flows are refused.
    """
    if rule not in ALLOTMENT_RULES:
        raise ValueError(f"{KEY_KREIS_MATCHING_RULE} must be one of {list(ALLOTMENT_RULES)}, got {rule!r}")
    if day_absence_enabled and not day_absence_student_cells_enabled:
        raise ValueError(f"{KEY_FLOWS_ENABLED} is true with {KEY_DAY_ABSENCE_ENABLED} true and "
                         f"{KEY_DAY_ABSENCE_STUDENT_CELLS} false: the day absence would thin the students by age "
                         f"band while the flows count with the away share of their home area. Switch the cells on, "
                         f"the day absence off, or the flows off.")
_NDS_LEVELS = ("grundschule", "sekundar_1", "oberstufe", "bbs")
# ``ADULT_EDUCATION`` (the level of the persons aged ``education_adult_min_age`` and older, #465, and of the
# non-student adults on the ON path), ``BBS_ADULT``, ``age_to_level`` and the age bands live in
# ``education_routing`` and are imported above.


def slope_vector_for_level(level, home_rs7, by_level_rs7, scalar_by_level):
    """Per-pupil slope for ``level``.

    ``home_rs7`` is a Series of each pupil's home RegioStaR-7 code.
    If ``by_level_rs7`` (nested dict ``{level: {rs7_code: slope}}``) is given and
    contains an entry for ``level``, use its per-RS7 value for each pupil,
    falling back to ``scalar_by_level[level]`` for RS7 codes without an override.
    If ``by_level_rs7`` is None or does not contain ``level``, every pupil
    receives the scalar, which reproduces the previous uniform-slope behaviour.
    Returns a float64 numpy array of length ``len(home_rs7)``.
    """
    scalar = scalar_by_level[level]
    n = len(home_rs7)
    if not by_level_rs7 or level not in by_level_rs7:
        # No per-RS7 dict for this level: every pupil takes the scalar slope.
        # This is the configured default (slope_by_level_rs7=None), NOT a
        # per-pupil fallback, so it is reported as 0 primary / scalar-by-design.
        print("[education:%s] slope: scalar-by-config for all %d pupils "
              "(no per-RS7 override configured)" % (level, n))
        return np.full(n, float(scalar))
    overrides = {int(k): float(v) for k, v in by_level_rs7[level].items()}
    # PRIMARY: pupil's home RS7 has a per-RS7 slope override.
    # FALLBACK: home RS7 is -1 (unmatched to a Gemeinde) or absent from the
    # override dict -> the pupil silently takes the scalar slope. Make that
    # observable as a PRIMARY-vs-FALLBACK rate (WARNING above the threshold).
    has_override = home_rs7.map(lambda c: int(c) in overrides).to_numpy()
    n_fallback = int((~has_override).sum())
    n_primary = n - n_fallback
    share = (n_fallback / n) if n > 0 else 0.0
    prefix = "WARNING: " if share > FALLBACK_WARN_SHARE else ""
    print("%s[education:%s] slope: primary %d/%d (%.1f%%) per-RS7 override, "
          "scalar fallback %d (%.1f%%)"
          % (prefix, level, n_primary, n, 100.0 * n_primary / n if n else 0.0,
             n_fallback, 100.0 * share))
    return home_rs7.map(lambda c: overrides.get(int(c), scalar)).to_numpy(dtype=float)


def bbs_share_vector(ages, by_age, scalar_share):
    """Per-pupil vocational-BBS share for the age-16-19 upper-secondary cohort.

    ``ages`` is a Series of each upper-secondary pupil's integer age.
    If ``by_age`` (mapping ``age -> bbs_share``) is given, each pupil receives
    its age's share, falling back to ``scalar_share`` for ages without an
    override. If ``by_age`` is None (or empty) every pupil receives
    ``scalar_share``, which reproduces the previous uniform-share behaviour.

    The mechanism only consumes shares supplied via config; the real by-age
    shares come from the NDS Schuljahresstatistik enrollment by age x school
    form (BBS vs. gymnasiale Oberstufe) and are to be supplied there. No
    by-age values are hard-coded here.

    Returns a float64 numpy array of length ``len(ages)``.
    """
    scalar = float(scalar_share)
    if not by_age:
        return np.full(len(ages), scalar)
    overrides = {int(k): float(v) for k, v in by_age.items()}
    return ages.map(lambda a: overrides.get(int(a), scalar)).to_numpy(dtype=float)


def _xy(gdf):
    return np.column_stack([gdf.geometry.x.values, gdf.geometry.y.values])


def _percent(part, whole):
    return 100.0 * part / whole if whole else 0.0


def _largest_remainder(n_persons: int, shares: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Whole cell totals adding up to ``n_persons`` in proportion to ``shares`` (Hamilton's largest remainder).

    Each cell gets the floor of its quota ``n_persons x share``; the persons left go one each to the cells with
    the largest remainders, equal remainders in a seeded order of the cells (no destination favoured by name).
    """
    quotas = n_persons * shares.to_numpy(dtype=float) / float(shares.sum())
    totals = np.floor(quotas).astype(np.int64)
    n_left = n_persons - int(totals.sum())
    tie_order = rng.permutation(len(quotas))
    totals[np.lexsort((tie_order, -(quotas - totals)))[:n_left]] += 1
    return pd.Series(totals, index=shares.index)


def _empty_university_report(inputs: UniversityFlowInputs) -> dict:
    return {"rule": inputs.rule, "n_persons": 0, "n_present": 0,
            "n_absent": 0, "n_by_path": {}, "counts_by_kreis": {},
            "legacy_without_row": {"n_persons": 0, "kreise": []}, "absent_without_fitted_row": {"n_persons": 0,
                                                                                               "kreise": []},
            "dropped_zero_share_destinations": list(inputs.dropped_destinations),
            "fit": {"iterations": 0, "max_deviation_persons": 0.0, "rows_left_out": [], "row_totals": {},
                    "column_targets": {}, "column_rounded": {}},
            "plan_rule": {"n_persons": 0, "n_primary": 0, "n_fallback": 0, "fallback_share": 0.0,
                          "warn_share": inputs.fallback_warn_share},
            "allotment_diagnostics": None, "realised_distance_km_by_home_kreis": {}}


def assign_university_flows(persons, facilities, inputs: UniversityFlowInputs, decay_slope, max_radius_km):
    """Place the persons of level ``university`` by the residents' flows (design B5; ON path of the stage).

    Steps, all drawing from ``inputs.rng`` in this fixed order on persons sorted by ``person_id`` (the result does
    not depend on the row order of ``persons``):

    1. A person whose home Kreis has no row of T (no seed on a destination with a share) is placed by the legacy
       ``assign_by_decay`` over the buildings (capacity times the decay of ``decay_slope``, radius
       ``max_radius_km``); counted, and a WARNING above 0 (design: 0 on the real table).
    2. The present persons of the other Kreise: ``fit_flow_matrix`` (rows: the persons present per home Kreis;
       columns: their sum split by the destinations' shares), ``controlled_round``, then per home Kreis (sorted)
       ``allot_within_kreis`` on the Kreis's row by ``inputs.rule``. The share of the plan rule's fallback is
       logged as a rate, a WARNING above ``inputs.fallback_warn_share``.
    3. The absent persons per home Kreis (sorted): the fitted row shares of their Kreis times their number,
       whole by largest remainder, then the same allotment; paths prefixed ``absent_``. A Kreis with absent persons
       but none present has no fitted row: its absent persons take the shares of its row of T (counted, a WARNING
       above 0).
    4. ``draw_buildings`` for every allotted person (indexed by ``person_id``).

    Args:
        persons: GeoDataFrame ``[person_id, geometry(home)]`` in EPSG:25832, the persons of level ``university``.
        facilities: the university facilities of the ON path (``university_facilities``): ``[location_id,
            capacity, geometry, destination_id, scope]``, one row per building, unique ``location_id``.
        inputs: :class:`UniversityFlowInputs`.
        decay_slope: ``education_university_slope`` (per km, <= 0), the decay of the fallbacks.
        max_radius_km: ``education_university_max_radius_km``, the radius of the legacy decay.

    Returns:
        ``(placed, report)``: ``placed`` is ``[person_id, location_id, geometry(building), destination_id,
        assignment_path]`` in the row order of ``persons``; ``report`` the counts and diagnostics written to
        :data:`UNIVERSITY_REPORT_FILE`.

    Raises:
        ValueError: when a person has no home Kreis or no day-absence state, when a destination with a share has
            no facility, when ``location_id`` is not unique, or on the errors of the pure functions (a fit that does
            not converge, a destination no populated Kreis reaches).
    """
    report = _empty_university_report(inputs)
    columns = ["person_id", "location_id", "geometry", "destination_id", "assignment_path"]
    if len(persons) == 0:
        return pd.DataFrame({column: [] for column in columns}), report
    if not facilities["location_id"].is_unique:
        raise ValueError(f"{_UNIVERSITY_TAG} the university facilities name a location_id more than once")
    rng = inputs.rng
    frame = pd.DataFrame({"person_id": persons["person_id"].to_numpy(), "x": persons.geometry.x.to_numpy(),
                          "y": persons.geometry.y.to_numpy(), "row": np.arange(len(persons))})
    frame["home_ars5"] = frame["person_id"].map(inputs.home_kreis)
    if frame["home_ars5"].isna().any():
        raise ValueError(f"{_UNIVERSITY_TAG} {int(frame['home_ars5'].isna().sum())} university person(s) without a "
                         f"home Kreis; first person_ids {frame.loc[frame['home_ars5'].isna(), 'person_id'].head(5).tolist()}")
    frame["leg_km"] = frame["person_id"].map(inputs.leg_km).astype(float)
    if inputs.day_state is None:
        frame["present"] = True
    else:
        state = frame["person_id"].map(inputs.day_state)
        if state.isna().any():
            raise ValueError(f"{_UNIVERSITY_TAG} {int(state.isna().sum())} university person(s) without a "
                             f"day_absence_state in {ABSENCE_STAGE}")
        frame["present"] = (state == STATE_PRESENT).to_numpy()
    frame = frame.sort_values("person_id", kind="mergesort").reset_index(drop=True)
    frame["destination_id"] = pd.Series([None] * len(frame), dtype=object)
    frame["assignment_path"] = pd.Series([None] * len(frame), dtype=object)
    frame["location_id"] = pd.Series([None] * len(frame), dtype=object)

    centres = destination_centres(facilities)
    without_facility = sorted(str(d) for d in set(inputs.shares.index) - set(centres["destination_id"]))
    if without_facility:
        raise ValueError(f"{_UNIVERSITY_TAG} destinations of T with a share but no university facility: "
                         f"{without_facility}; the facilities and T must come from the same table")
    seed = inputs.seed
    with_row = set(seed.loc[(seed["seed"] > 0) & seed["destination_id"].isin(inputs.shares.index), "home_ars5"])
    no_row = ~frame["home_ars5"].isin(with_row).to_numpy()
    present = frame["present"].to_numpy(dtype=bool)

    # 1. Home Kreise without a row of T: the legacy decay over the buildings.
    if no_row.any():
        legacy = frame[no_row]
        choice = assign_by_decay(legacy[["x", "y"]].to_numpy(), _xy(facilities), facilities["capacity"].to_numpy(),
                                 slope=decay_slope, max_radius_km=max_radius_km, rng=rng,
                                 label="education:university without a row of T")
        picked = facilities.iloc[choice]
        frame.loc[no_row, "location_id"] = picked["location_id"].to_numpy()
        frame.loc[no_row, "destination_id"] = picked["destination_id"].to_numpy()
        frame.loc[no_row, "assignment_path"] = np.where(legacy["present"], PATH_LEGACY_DECAY,
                                                        ABSENT_PATH_PREFIX + PATH_LEGACY_DECAY)
    n_legacy = int(no_row.sum())
    legacy_kreise = sorted(set(frame.loc[no_row, "home_ars5"]))
    (logger.warning if n_legacy else logger.info)(
        "%s rows of T: primary %d/%d (%.1f%%), fallback %d (%.1f%%) placed by the legacy decay because their home "
        "Kreis has no row of T %s (must be 0 on the real table)", _UNIVERSITY_TAG, len(frame) - n_legacy, len(frame),
        _percent(len(frame) - n_legacy, len(frame)), n_legacy, _percent(n_legacy, len(frame)), legacy_kreise)

    # 2. The present persons: fit, rounding, allotment per home Kreis.
    fitted_rows = frame[~no_row & present]
    fitted = pd.DataFrame()
    if len(fitted_rows):
        row_totals = fitted_rows.groupby("home_ars5").size().astype(float)
        fitted, fit_diagnostics = fit_flow_matrix(seed, row_totals, inputs.shares, inputs.fit_tolerance,
                                                  inputs.fit_max_iterations)
        rounded = controlled_round(fitted, rng)
        report["fit"] = {**fit_diagnostics, "row_totals": {k: int(v) for k, v in row_totals.items()},
                         "column_targets": {str(d): float(v) for d, v in (row_totals.sum() * inputs.shares).items()},
                         "column_rounded": {str(d): int(v) for d, v in rounded.sum(axis=0).items()}}
        for kreis in rounded.index:
            members = fitted_rows[fitted_rows["home_ars5"] == kreis]
            allotted = allot_within_kreis(members[["person_id", "x", "y", "leg_km"]], rounded.loc[kreis], centres,
                                          inputs.rule, decay_slope, rng, label=f"home Kreis {kreis}")
            frame.loc[members.index, "destination_id"] = allotted["destination_id"].to_numpy()
            frame.loc[members.index, "assignment_path"] = allotted["path"].to_numpy()
    n_plan = len(fitted_rows)
    n_fallback = int((frame.loc[fitted_rows.index, "assignment_path"] == PATH_FALLBACK_DECAY).sum())
    fallback_share = n_fallback / n_plan if n_plan else 0.0
    (logger.warning if fallback_share > inputs.fallback_warn_share else logger.info)(
        "%s allotment (%s rule) of the present persons: primary %d/%d (%.1f%%), fallback %d (%.1f%%) placed by the "
        "university decay (no observed education leg); warn above %s=%g", _UNIVERSITY_TAG, inputs.rule,
        n_plan - n_fallback, n_plan, _percent(n_plan - n_fallback, n_plan), n_fallback, 100.0 * fallback_share,
        KEY_MATCHING_FALLBACK_WARN_SHARE, inputs.fallback_warn_share)
    report["plan_rule"] = {"n_persons": n_plan, "n_primary": n_plan - n_fallback, "n_fallback": n_fallback,
                           "fallback_share": fallback_share, "warn_share": inputs.fallback_warn_share}

    # 3. The absent persons: the fitted row shares of their Kreis, whole by largest remainder, the same allotment.
    absent_rows = frame[~no_row & ~present]
    seed_rows = seed[seed["destination_id"].isin(inputs.shares.index)]
    without_fitted_row = []
    for kreis, members in absent_rows.groupby("home_ars5", sort=True):
        if kreis in fitted.index:
            row = fitted.loc[kreis]
        else:
            without_fitted_row.append(kreis)
            row = seed_rows[seed_rows["home_ars5"] == kreis].set_index("destination_id")["seed"]
        row = row[row > 0]
        totals = _largest_remainder(len(members), row, rng)
        allotted = allot_within_kreis(members[["person_id", "x", "y", "leg_km"]], totals, centres, inputs.rule,
                                      decay_slope, rng, label=f"home Kreis {kreis}, absent")
        frame.loc[members.index, "destination_id"] = allotted["destination_id"].to_numpy()
        frame.loc[members.index, "assignment_path"] = (ABSENT_PATH_PREFIX + allotted["path"]).to_numpy()
    n_without_fitted = int(absent_rows["home_ars5"].isin(without_fitted_row).sum())
    (logger.warning if n_without_fitted else logger.info)(
        "%s absent persons: %d allotted on their own totals, of them %d (Kreise %s) on the shares of their row of T "
        "because their Kreis has nobody present (no fitted row)", _UNIVERSITY_TAG, len(absent_rows), n_without_fitted,
        without_fitted_row)

    # 4. The buildings of the allotted persons, drawn by capacity; indexed by person_id so the draw does not depend
    # on the row order.
    allotted_mask = ~no_row
    if allotted_mask.any():
        wanted = pd.Series(frame.loc[allotted_mask, "destination_id"].to_numpy(),
                           index=pd.Index(frame.loc[allotted_mask, "person_id"].to_numpy(), name="person_id"))
        frame.loc[allotted_mask, "location_id"] = draw_buildings(wanted, facilities, rng).to_numpy()
    building = facilities.set_index("location_id").geometry
    frame["geometry"] = frame["location_id"].map(building)
    distance_km = np.hypot(frame["x"] - frame["geometry"].map(lambda p: p.x),
                           frame["y"] - frame["geometry"].map(lambda p: p.y)) / 1000.0

    # The report: counts per Kreis and path, the allotment diagnostics of the present persons, realised distances.
    counts = frame.groupby(["home_ars5", "assignment_path"]).size()
    report.update({
        "n_persons": int(len(frame)), "n_present": int(present.sum()),
        "n_absent": int((~present).sum()),
        "n_by_path": {str(p): int(n) for p, n in frame["assignment_path"].value_counts().sort_index().items()},
        "counts_by_kreis": {str(k): {str(p): int(counts[(k, p)]) for p in counts[k].index}
                            for k in counts.index.get_level_values(0).unique()},
        "legacy_without_row": {"n_persons": n_legacy, "kreise": legacy_kreise},
        "absent_without_fitted_row": {"n_persons": n_without_fitted, "kreise": without_fitted_row},
    })
    if len(fitted_rows):
        assigned = frame.loc[fitted_rows.index, ["person_id", "destination_id", "assignment_path", "home_ars5"]]
        report["allotment_diagnostics"] = allotment_diagnostics(
            assigned.rename(columns={"assignment_path": "path"}), frame.loc[fitted_rows.index,
                                                                             ["person_id", "x", "y", "leg_km"]],
            centres, kreis_column="home_ars5")
    present_distance = pd.DataFrame({"home_ars5": frame["home_ars5"], "km": distance_km})[present]
    report["realised_distance_km_by_home_kreis"] = {
        str(kreis): {"n_present": int(len(group)), "mean": float(group["km"].mean()),
                     "median": float(group["km"].median()), "p90": float(group["km"].quantile(0.9))}
        for kreis, group in present_distance.groupby("home_ars5", sort=True)}
    logger.info("%s %d persons (%d present, %d absent) placed; per path %s", _UNIVERSITY_TAG, len(frame),
                report["n_present"], report["n_absent"], report["n_by_path"])
    placed = frame.sort_values("row", kind="mergesort")
    return placed[columns].reset_index(drop=True), report


def _json_safe(value):
    """``value`` with every non-finite float replaced by None (strict JSON), numpy scalars as Python numbers."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def assign_education_locations(df_persons, df_nds, df_universities, df_kita, cfg, rng, university_flows=None,
                               university_report=None):
    """Assign one education location to every person in ``df_persons``.

    df_persons:     GeoDataFrame[person_id, age, home_rs7, geometry(home)]
                    (EPSG:25832).  ``home_rs7`` must be present: it is the
                    RegioStaR-7 code of the person's home Gemeinde (-1 for
                    unmatched), used to build a per-pupil slope when
                    ``cfg["slope_by_level_rs7"]`` is set.
    df_nds:         Facilities GeoDataFrame[school_id, level, capacity,
                    commune_id, geometry] -- NDS school levels only
                    (grundschule/sekundar_1/oberstufe/bbs).
    df_universities: University facilities GeoDataFrame[location_id, capacity,
                    geometry] (EPSG:25832).  University students (age 20+) are
                    routed here via the singly-constrained ``assign_by_decay``
                    using ``cfg["university_slope"]`` and
                    ``cfg["university_max_radius_km"]``.  Institutions outside
                    the ZGB cordon carry no commune_id (filled as empty string).
    df_kita:        Kita facilities GeoDataFrame[location_id, capacity,
                    geometry] (EPSG:25832).  Kindergarten children (age 0-5)
                    are routed here via the doubly-constrained
                    ``assign_by_capacity_gravity`` using the "kindergarten"
                    entry in ``cfg["slope_by_level"]`` and
                    ``cfg["max_radius_km_by_level"]``.
    cfg:            dict with slope_by_level, slope_by_level_rs7 (None or
                    nested dict), max_radius_km_by_level, university_slope,
                    university_max_radius_km, max_iterations, tolerance,
                    bbs_share, and optional bbs_share_by_age (None or a
                    mapping age -> bbs_share for the 16-19 cohort; None
                    reproduces the scalar bbs_share path byte-for-byte).
                    With ``cfg["student_flows_enabled"]`` true (issue #140, Phase 4, design B1) the persons are
                    routed by student status (``braunschweig.synthesis.locations.education_routing``): students
                    aged 18+ to the university level, apprentices and pupils aged 20+ (``P_TAET`` 8 or 9) to
                    ``bbs_adult`` (singly-constrained decay over the ``bbs`` schools, the schools' capacities as
                    weights, slope and radius of the ``bbs`` level), every other adult to ``adult_education``
                    among the ``df_nds`` schools only (no university building; ``adult_education_min_age`` is not
                    read); ``df_persons`` then carries ``P_TAET`` (or ``studies`` and ``employed`` for the
                    counted fallback). ``cfg["student_status_fallback_warn_share"]`` (default 0.0) is the share of
                    persons on the status fallback above which a WARNING is logged. The default (key absent or
                    false) is the code before the switch. ON, the persons of level ``university`` are placed by
                    :func:`assign_university_flows` on ``university_flows`` (required then) instead of
                    ``assign_by_decay``; ``df_universities`` is then the ON output of ``university_facilities``
                    (with ``destination_id`` and ``scope``).
    university_flows: :class:`UniversityFlowInputs` (ON path only; ignored OFF).
    university_report: a dict the ON path fills with the report of :func:`assign_university_flows`, or None.
    Returns DataFrame[person_id, commune_id, location_id, geometry]; with the switch ON also ``routed_level``
    (the level every person was placed on), ``destination_id`` and ``assignment_path`` (the persons of level
    ``university`` only; missing for the others).
    """
    flows = bool(cfg.get("student_flows_enabled", False))
    df = df_persons.copy()
    if flows:
        is_student, _ = student_status(
            df, warn_share=cfg.get("student_status_fallback_warn_share", 0.0))
        df["level"] = route_education_level(
            df["age"], is_student, df["P_TAET"] if "P_TAET" in df.columns else None)
        summary = routing_summary(df["age"], is_student, df["level"])
        print("[education:routing] persons per routed level %s; students under 18 kept on their age band: %d; "
              "non-student adults (aged 20+) whom the switch-OFF rule would send to the university level or to "
              "the adult rule with university buildings: %d (to bbs_adult %d, to adult_education %d)" % (
                  summary["per_level"], summary["students_under_18"], summary["non_student_adults"],
                  summary["to_bbs_adult"], summary["to_adult_education"]))
    else:
        df["level"] = df["age"].apply(age_to_level)

    # Resolve the synthetic "upper_secondary" band into oberstufe (academic)
    # or bbs (vocational) per pupil, drawn from the configured enrollment share.
    # The BBS share is the fraction going to vocational BBS. When
    # cfg["bbs_share_by_age"] is None (the default), every pupil uses the scalar
    # cfg["bbs_share"] (NDS default: 0.681) and the draw is byte-identical to the
    # previous uniform-share code path (same single random_sample call, same
    # elementwise comparison). When an age->share dict is supplied, each pupil is
    # compared against its age-resolved share instead (real BBS enrollment rises
    # steeply with age 16->19), with the scalar as the per-age fallback.
    # Adult education (#465): from the configured minimum age on, an education activity is no longer a university one.
    adult_min_age = None if flows else cfg.get("adult_education_min_age")
    if adult_min_age is not None:
        df.loc[(df["level"] == "university") & (df["age"] >= adult_min_age), "level"] = ADULT_EDUCATION

    us = df["level"] == "upper_secondary"
    if us.any():
        share = bbs_share_vector(
            df.loc[us, "age"], cfg.get("bbs_share_by_age"), cfg["bbs_share"])
        draw = rng.random_sample(size=int(us.sum())) < share
        df.loc[us, "level"] = np.where(draw, "bbs", "oberstufe")

    parts = []

    for level in _NDS_LEVELS:
        sel = df[df["level"] == level]
        if sel.empty:
            continue
        schools = df_nds[df_nds["level"] == level]
        if schools.empty:
            raise RuntimeError(
                f"[education_gravity] no NDS schools for level '{level}'")
        slope = slope_vector_for_level(
            level, sel["home_rs7"], cfg.get("slope_by_level_rs7"),
            cfg["slope_by_level"])
        choice, _ = assign_by_capacity_gravity(
            _xy(sel), _xy(schools), schools["capacity"].values,
            slope=slope,
            max_radius_km=cfg["max_radius_km_by_level"][level],
            max_iterations=cfg["max_iterations"], tolerance=cfg["tolerance"],
            rng=rng, label="education:%s" % level,
        )
        picked = schools.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["school_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    # Kindergarten (age 0-5): doubly-constrained capacity gravity on real Kita
    # facilities. Kitas are local institutions; capacity targets are meaningful
    # (Plaetze ~ local demand), so the same doubly-constrained model used for
    # NDS schools is appropriate here. commune_id is left empty below: it is not
    # consumed downstream (synthesis.population.spatial.locations re-attributes the
    # location geometry to a Gemeinde); the same applies to the university branch.
    sel = df[df["level"] == "kindergarten"]
    if not sel.empty:
        if df_kita.empty:
            raise RuntimeError(
                "[education_gravity] no Kita facilities provided")
        slope = slope_vector_for_level(
            "kindergarten", sel["home_rs7"], cfg.get("slope_by_level_rs7"),
            cfg["slope_by_level"])
        choice, _ = assign_by_capacity_gravity(
            _xy(sel), _xy(df_kita), df_kita["capacity"].values, slope=slope,
            max_radius_km=cfg["max_radius_km_by_level"]["kindergarten"],
            max_iterations=cfg["max_iterations"], tolerance=cfg["tolerance"],
            rng=rng, label="education:kindergarten",
        )
        picked = df_kita.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": "",
            "geometry": picked["geometry"].values,
        }))

    # University, switch OFF (age 20+): singly-constrained decay on real university facilities.
    # Far institutions' large enrollment is largely non-resident; only the
    # distance decay governs how far the local commuter tail reaches.
    # commune_id is empty because many universities are outside the ZGB cordon.
    # Switch ON (students aged 18+): the residents' flows of design B5 (assign_university_flows).
    sel = df[df["level"] == "university"]
    if flows:
        # The residents' flows (B5) on their own generator (UniversityFlowInputs.rng): the stage's ``rng`` is not
        # touched here, so the levels drawn after this one do not depend on the number of students.
        if university_flows is None:
            raise ValueError("[education_gravity] the student flows are on but no university flow inputs were given")
        placed, report = assign_university_flows(sel, df_universities, university_flows,
                                                 cfg["university_slope"], cfg["university_max_radius_km"])
        if university_report is not None:
            university_report.update(report)
        if not placed.empty:
            parts.append(placed.assign(commune_id="")[
                ["person_id", "location_id", "commune_id", "geometry", "destination_id", "assignment_path"]])
    elif not sel.empty:
        if df_universities.empty:
            raise RuntimeError(
                "[education_gravity] no university facilities provided")
        choice = assign_by_decay(
            _xy(sel), _xy(df_universities), df_universities["capacity"].values,
            slope=cfg["university_slope"],
            max_radius_km=cfg["university_max_radius_km"], rng=rng,
            label="education:university",
        )
        picked = df_universities.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": "",   # surrounding universities have no ZGB commune
            "geometry": picked["geometry"].values,
        }))

    # Adult education (#465, age >= cfg["adult_education_min_age"], off when the key is absent or None): every
    # education facility (NDS schools of all levels and the universities) is a candidate with the SAME weight, so
    # neither pupil capacities nor university enrolments decide; the distance decay alone does. Adult courses
    # (Volkshochschule, further education) are not in the facility data and often take place in school buildings.
    # With the student flows ON (B1) the university buildings are NOT candidates: the students of those buildings
    # are placed by the university level, the other adults go to school buildings.
    sel = df[df["level"] == ADULT_EDUCATION]
    if not sel.empty:
        candidate_frames = [
            pd.DataFrame({"location_id": df_nds["school_id"].values, "commune_id": df_nds["commune_id"].values,
                          "geometry": df_nds["geometry"].values, "kind": "school"}),
        ]
        if not flows:
            candidate_frames.append(
                pd.DataFrame({"location_id": df_universities["location_id"].values, "commune_id": "",
                              "geometry": df_universities["geometry"].values, "kind": "university"}))
        facilities = pd.concat(candidate_frames, ignore_index=True)
        facilities = gpd.GeoDataFrame(facilities, geometry="geometry", crs=df_nds.crs)
        choice = assign_by_decay(
            _xy(sel), _xy(facilities), np.ones(len(facilities)),
            slope=cfg["adult_education_slope"],
            max_radius_km=cfg["adult_education_max_radius_km"], rng=rng,
            label="education:adult_education",
        )
        picked = facilities.iloc[choice]
        distance_km = np.hypot(*(_xy(picked) - _xy(sel)).T) / 1000.0
        n_university = int((picked["kind"] == "university").sum())
        print("[education:adult_education] %d persons aged %d+ placed among %d education facilities (schools and "
              "universities, uniform weights, slope %g per km, radius %g km): straight-line distance median %.1f km, "
              "mean %.1f km, p90 %.1f km; at a university %d (%.1f%%)" % (
                  len(sel), _education_routing.ADULT_MIN_AGE if flows else cfg["adult_education_min_age"], len(facilities), cfg["adult_education_slope"],
                  cfg["adult_education_max_radius_km"], np.median(distance_km), distance_km.mean(),
                  np.percentile(distance_km, 90), n_university, 100.0 * n_university / len(sel)))
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    # Adults on a vocational track (ON path only, drawn LAST so that every earlier draw keeps its place in the
    # random stream): singly-constrained decay over the bbs schools with the schools' capacities as weights and the
    # slope and radius of the bbs level, separate from the capacity-balanced placement of the pupils under 20.
    # The scalar slope of the level is used: ``assign_by_decay`` takes one slope for all persons.
    sel = df[df["level"] == BBS_ADULT]
    if not sel.empty:
        schools = df_nds[df_nds["level"] == "bbs"]
        if schools.empty:
            raise RuntimeError(
                "[education_gravity] no NDS schools for level 'bbs' (adult apprentices and pupils)")
        choice = assign_by_decay(
            _xy(sel), _xy(schools), schools["capacity"].values,
            slope=cfg["slope_by_level"]["bbs"], max_radius_km=cfg["max_radius_km_by_level"]["bbs"], rng=rng,
            label="education:bbs_adult",
        )
        picked = schools.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["school_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    out = pd.concat(parts, ignore_index=True)
    columns = ["person_id", "commune_id", "location_id", "geometry"]
    if flows:
        # The analysis stage counts from these columns; they exist on the ON path only. ``destination_id`` and
        # ``assignment_path`` are set for the persons of level ``university`` and missing for every other person.
        out["routed_level"] = out["person_id"].map(df.set_index("person_id")["level"]).values
        for column in ("destination_id", "assignment_path"):
            if column not in out.columns:
                out[column] = pd.Series([None] * len(out), dtype=object)
        columns += ["routed_level", "destination_id", "assignment_path"]
    return out[columns]


def merge_person_attributes(df_persons, df_enriched, flows_enabled):
    """Merge the person attributes the stage needs from ``synthesis.population.enriched`` into ``df_persons``.

    OFF: ``age`` only (the code before the switch). ON: also ``P_TAET`` (student status; present in the popsim_mid
    pipeline, whose persons keep the donor's survey columns), ``studies`` and ``employed`` (derived in
    ``braunschweig.popsim.assembly`` and by the legacy enriched stage; only the counted status fallback reads them),
    each only when the enriched frame has it; ``education_routing.student_status`` raises when none can give a status.

    The merge is on ``person_id`` alone. Raises ``ValueError`` when ``person_id`` is not unique in either frame or
    when ``df_persons`` already carries one of the columns to be merged (the enriched frame owns them; a silent
    overwrite or a suffixed duplicate would hide which one is read), and ``AssertionError`` when the row count
    changes (a person absent from the enriched frame would be dropped silently).
    """
    columns = ["person_id", "age"]
    if flows_enabled:
        columns += [c for c in ("P_TAET", "studies", "employed") if c in df_enriched.columns]
    for name, frame in (("persons", df_persons), ("enriched", df_enriched)):
        if frame["person_id"].duplicated().any():
            raise ValueError("[education_gravity] person_id is not unique in the %s frame (%d duplicates)" % (
                name, int(frame["person_id"].duplicated().sum())))
    clash = [c for c in columns if c != "person_id" and c in df_persons.columns]
    if clash:
        raise ValueError("[education_gravity] the persons already carry column(s) %s that the enriched frame "
                         "provides" % clash)
    n_before = len(df_persons)
    merged = pd.merge(df_persons, df_enriched[columns], on="person_id", validate="one_to_one")
    assert len(merged) == n_before, (
        "[education_gravity] merging the enriched person attributes changed the row count from %d to %d "
        "(persons missing from the enriched frame)" % (n_before, len(merged)))
    return merged


def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("braunschweig.data.schools.facilities")
    context.stage("data.spatial.municipalities")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("random_seed")
    context.config("education_gravity_slope_by_level",
                   {"kindergarten": -0.5,
                    "grundschule": -0.3, "sekundar_1": -0.15,
                    "oberstufe": -0.08, "bbs": -0.05})
    context.config("education_gravity_max_radius_km_by_level",
                   {"kindergarten": 8.0,
                    "grundschule": 15.0, "sekundar_1": 30.0,
                    "oberstufe": 60.0, "bbs": 100.0})
    # Share of upper-secondary (age 16-19) pupils assigned to berufsbildende
    # Schulen (BBS); the remainder go to academic Oberstufe.
    # Source: NDS Kultusministerium Schuljahresstatistik 2023/24:
    # BBS ~68 100 / (BBS 68 100 + gymnasiale Oberstufe 32 000) ~ 0.681.
    context.config("education_bbs_share", 0.681)
    # Optional age-resolved override of the scalar BBS share: a mapping
    # {age -> bbs_share} for the age-16-19 upper-secondary cohort. Real BBS
    # enrollment rises steeply with age (most 16-year-olds are still in the
    # gymnasiale Oberstufe; BBS dominates 17-19), and the two levels have very
    # different trip lengths, so a flat scalar mis-allocates the 16-19 distance
    # distribution. Ages absent from the dict fall back to education_bbs_share.
    # Source for the real shares (to be supplied here): NDS Schuljahresstatistik
    # enrollment by age x school form (BBS vs. gymnasiale Oberstufe). No by-age
    # values are pinned by default. None (not {}) so synpp flatten() keeps the
    # key; this mirrors the education_gravity_slope_by_level_rs7 None-default.
    context.config("education_bbs_share_by_age", None)
    # CSV alternative to the inline dict above: when education_bbs_share_by_age
    # is None, the stage loads the per-age shares from this reference CSV
    # (regionalstatistik 21211 BBS-by-age + 21111 Oberstufe-by-age extract, see
    # braunschweig.data.schools.bbs_share). File absent -> scalar share with an
    # explicit log line; an inline dict config takes precedence over the CSV.
    context.config("data_path")
    context.config("education_bbs_share_by_age_path",
                   "braunschweig/schools/nds_bbs_share_by_age.csv")
    context.stage("braunschweig.data.schools.university_facilities")
    context.stage("braunschweig.data.schools.kita_facilities")
    # Default mirrors the pinned calibration (Destatis MZ 2024 Hochschule mean,
    # ~15.2 km straight-line) so a config that omits the key does not silently
    # run the university model uncalibrated. The active BS configs pin the same
    # value; re-run scripts/calibrate_education_slopes.py to update it.
    context.config("education_university_slope", -0.1415)
    context.config("education_university_max_radius_km", 150.0)
    # Adult education (#465): from this age on an education activity is placed among ALL education facilities
    # (schools and universities, uniform weights) by distance decay instead of at a university. ASSUMPTIONS, not
    # calibrated: the age (30; null switches the band off and every person aged 20+ goes to a university), the slope
    # and the radius (the scalar Sekundarstufe I values: adult courses often take place in school buildings).
    check_adult_education_settings(context.config("education_adult_min_age", 30),
                                   context.config("education_adult_slope", -0.15),
                                   context.config("education_adult_max_radius_km", 30.0))
    context.config("education_gravity_max_iterations", 50)
    context.config("education_gravity_tolerance", 1e-3)
    # None (not {}) so synpp flatten() does not drop this key; per-RS7 dict is
    # set by the calibration script and mirrors gravity_slope_by_regiostar7.
    context.config("education_gravity_slope_by_level_rs7", None)
    # Student status routing (issue #140, Phase 4, design B1); ON by default. The warn share is read on the ON path
    # only and declared only then. ASSUMPTION: any use of the student-status fallback (no P_TAET) in the popsim_mid
    # pipeline is a defect, so every fallback share above 0.0 logs a WARNING.
    if context.config(KEY_FLOWS_ENABLED, True):
        context.config(KEY_STATUS_FALLBACK_WARN_SHARE, DEFAULT_STATUS_FALLBACK_WARN_SHARE)
        _configure_university_flows(context)


def _configure_university_flows(context):
    """The keys and inputs of the residents' university flows (B5), declared ONLY with the switch on.

    The trips (the observed education leg), the home zones (the home Kreis) and, with the day absence on, the
    absence stage (who is present on the simulated day) are new DAG edges of the ON path; the OFF path keeps the
    inputs it had before. The refused configurations raise here (:func:`check_university_flow_settings`).
    """
    rule = context.config(KEY_KREIS_MATCHING_RULE, DEFAULT_KREIS_MATCHING_RULE)
    context.config(KEY_FIT_TOLERANCE, DEFAULT_FIT_TOLERANCE)
    context.config(KEY_FIT_MAX_ITERATIONS, DEFAULT_FIT_MAX_ITERATIONS)
    context.config(KEY_MATCHING_FALLBACK_WARN_SHARE, DEFAULT_MATCHING_FALLBACK_WARN_SHARE)
    context.config(KEY_OD_TABLE_PATH, DEFAULT_OD_TABLE_PATH)
    day_absence_enabled = context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)
    cells_enabled = None
    if day_absence_enabled:
        cells_enabled = context.config(KEY_DAY_ABSENCE_STUDENT_CELLS, DEFAULT_DAY_ABSENCE_STUDENT_CELLS)
    check_university_flow_settings(rule, day_absence_enabled, cells_enabled)
    context.stage(TRIPS_STAGE)
    context.stage(HOME_ZONES_STAGE)
    if day_absence_enabled:
        context.stage(ABSENCE_STAGE)


def check_adult_education_settings(min_age, slope, max_radius_km) -> None:
    """Raise ValueError naming the key for an invalid adult education setting (checked at configure time).

    ``education_adult_min_age``: None (band off) or a whole number of years in [20, 120], no bool;
    ``education_adult_slope``: a finite number < 0 (per km); ``education_adult_max_radius_km``: a finite number > 0.
    """
    if min_age is not None and (isinstance(min_age, bool) or not isinstance(min_age, int) or not 20 <= min_age <= 120):
        raise ValueError(f"education_adult_min_age must be null or a whole number of years in [20, 120], got {min_age!r}")
    for key, value, valid in (("education_adult_slope", slope, lambda v: v < 0),
                              ("education_adult_max_radius_km", max_radius_km, lambda v: v > 0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not valid(value):
            raise ValueError(f"{key} must be a finite number ({'< 0 per km' if key.endswith('slope') else '> 0 km'}), "
                             f"got {value!r}")


def _resolve_bbs_share_by_age(context):
    """Effective {age -> bbs_share} mapping for the 16-19 cohort.

    Precedence: an inline ``education_bbs_share_by_age`` dict wins; otherwise
    the per-age shares are loaded from the reference CSV at
    ``education_bbs_share_by_age_path`` (None when the file is absent, with an
    explicit log -- the scalar ``education_bbs_share`` then applies)."""
    inline = context.config("education_bbs_share_by_age")
    if inline:
        return inline
    from braunschweig.data.schools.bbs_share import load_bbs_share_by_age
    path = os.path.join(context.config("data_path"),
                        context.config("education_bbs_share_by_age_path"))
    return load_bbs_share_by_age(path)


def validate(context):
    """Include the helper modules' source in synpp's stage cache identity (ADR-0136).

    ON path: also the CRLF-neutral digest of the table T it reads (``braunschweig.students.od_table_path``), so a
    rebuilt table devalidates the stage; a missing table stops the run at DAG time.
    """
    helpers = synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")
    if not context.config(KEY_FLOWS_ENABLED):
        return helpers
    path = _od_table_path(context)
    if not os.path.isfile(path):
        raise RuntimeError(f"{_UNIVERSITY_TAG} {KEY_OD_TABLE_PATH} not found: {path}")
    return {"helpers": helpers, "flows_files": {KEY_OD_TABLE_PATH: synpp_tokens.text_file_sha256(path)}}


def _od_table_path(context):
    return os.path.join(str(context.config("data_path")), context.config(KEY_OD_TABLE_PATH))


def _university_flow_inputs(context, df_persons):
    """:class:`UniversityFlowInputs` of the ON path from the declared inputs (persons keyed by ``person_id``).

    The home Kreis comes from the household's home zone; a person whose household has no home zone raises (a
    broken join). The observed education leg is read from the same ``synthesis.population.trips`` frame the
    commute-distance stage reads, in its row order. With the day absence off every person is present.
    """
    od = _student_flows_inputs.load_od_table(_od_table_path(context))
    shares, dropped = _student_flows_inputs.destination_shares(od)
    if dropped:
        logger.info("%s destinations of T without a student of the region, left out (no share, no facility): %s",
                    _UNIVERSITY_TAG, dropped)
    home_kreis = _student_flows_inputs.home_kreis_by_household(context.stage(HOME_ZONES_STAGE))
    kreis = df_persons["household_id"].map(home_kreis)
    if kreis.isna().any():
        raise ValueError(f"{_UNIVERSITY_TAG} {int(kreis.isna().sum())}/{len(df_persons)} persons live in a household "
                         f"without a home zone in {HOME_ZONES_STAGE}; first household_ids "
                         f"{df_persons.loc[kreis.isna(), 'household_id'].unique()[:5].tolist()}")
    day_state = None
    if context.config(KEY_DAY_ABSENCE_ENABLED):
        absence = context.stage(ABSENCE_STAGE)["absence"]
        if absence["person_id"].duplicated().any():
            raise ValueError(f"{_UNIVERSITY_TAG} {ABSENCE_STAGE} holds a person more than once")
        day_state = absence.set_index("person_id")["day_absence_state"]
    else:
        logger.info("%s %s is false: every person counts as present", _UNIVERSITY_TAG, KEY_DAY_ABSENCE_ENABLED)
    random_seed = int(context.config("random_seed"))
    logger.info("%s rule %s, fit tolerance %g persons, at most %d iterations; generator default_rng([%d, %d])",
                _UNIVERSITY_TAG, context.config(KEY_KREIS_MATCHING_RULE), context.config(KEY_FIT_TOLERANCE),
                context.config(KEY_FIT_MAX_ITERATIONS), random_seed, UNIVERSITY_STREAM_ID)
    return UniversityFlowInputs(
        seed=_student_flows_inputs.region_seed(od), shares=shares, dropped_destinations=list(dropped),
        home_kreis=pd.Series(kreis.to_numpy(), index=df_persons["person_id"].to_numpy()),
        leg_km=observed_education_leg_km(context.stage(TRIPS_STAGE)), day_state=day_state,
        rule=context.config(KEY_KREIS_MATCHING_RULE), fit_tolerance=float(context.config(KEY_FIT_TOLERANCE)),
        fit_max_iterations=context.config(KEY_FIT_MAX_ITERATIONS),
        fallback_warn_share=float(context.config(KEY_MATCHING_FALLBACK_WARN_SHARE)),
        rng=np.random.default_rng([random_seed, UNIVERSITY_STREAM_ID]))


def execute(context):
    rng = np.random.RandomState(context.config("random_seed"))

    df_persons = context.stage(
        "synthesis.population.spatial.primary.candidates")["persons"]
    df_persons = df_persons[df_persons["has_education_trip"]].copy()
    households = set(df_persons["household_id"].unique())

    flows_enabled = bool(context.config(KEY_FLOWS_ENABLED))
    df_persons = merge_person_attributes(
        df_persons, context.stage("synthesis.population.enriched"), flows_enabled)

    df_homes = context.stage("synthesis.population.spatial.home.locations")
    df_homes = df_homes[df_homes["household_id"].isin(households)][
        ["household_id", "geometry"]]
    df_persons = pd.merge(df_persons, df_homes)
    df_persons = gpd.GeoDataFrame(df_persons, geometry="geometry",
                                  crs=df_homes.crs)

    df_nds = context.stage("braunschweig.data.schools.facilities")
    df_universities = context.stage("braunschweig.data.schools.university_facilities")
    df_kita = context.stage("braunschweig.data.schools.kita_facilities")

    # Attach home RegioStaR-7 code to each pupil via a spatial join with the
    # municipalities layer. The municipalities GeoDataFrame must share the same
    # CRS as df_persons (both EPSG:25832). Persons outside all municipality
    # polygons receive -1 (unmatched); slope_vector_for_level falls back to the
    # scalar slope for those, which is the safe conservative default.
    df_zones = context.stage("data.spatial.municipalities")[["commune_id", "geometry"]]
    if df_zones.crs is not None and df_persons.crs is not None:
        df_zones = df_zones.to_crs(df_persons.crs)
    df_rs7 = context.stage("braunschweig.data.bbsr.regiostar")[["commune_id", "regiostar7"]]
    joined = gpd.sjoin(df_persons[["person_id", "geometry"]], df_zones,
                       how="left", predicate="within").drop(columns="index_right")
    joined = joined.drop_duplicates("person_id")
    # municipalities carry the 12-digit ARS; regiostar keys on the 8-digit AGS.
    joined["commune_id"] = joined["commune_id"].map(ars_to_ags8)
    joined = joined.merge(df_rs7, on="commune_id", how="left")
    rs7_by_person = joined.set_index("person_id")["regiostar7"]
    df_persons["home_rs7"] = (df_persons["person_id"].map(rs7_by_person)
                              .fillna(-1).astype(int))

    cfg = {
        "slope_by_level": context.config("education_gravity_slope_by_level"),
        "slope_by_level_rs7": context.config("education_gravity_slope_by_level_rs7"),
        "max_radius_km_by_level":
            context.config("education_gravity_max_radius_km_by_level"),
        "university_slope": context.config("education_university_slope"),
        "university_max_radius_km":
            context.config("education_university_max_radius_km"),
        "max_iterations": context.config("education_gravity_max_iterations"),
        "tolerance": context.config("education_gravity_tolerance"),
        "bbs_share": context.config("education_bbs_share"),
        "bbs_share_by_age": _resolve_bbs_share_by_age(context),
        "adult_education_min_age": context.config("education_adult_min_age"),
        "adult_education_slope": context.config("education_adult_slope"),
        "adult_education_max_radius_km": context.config("education_adult_max_radius_km"),
    }
    university_flows = university_report = None
    if flows_enabled:
        cfg["student_flows_enabled"] = True
        cfg["student_status_fallback_warn_share"] = context.config(KEY_STATUS_FALLBACK_WARN_SHARE)
        university_flows = _university_flow_inputs(context, df_persons)
        university_report = {}
    out = assign_education_locations(
        df_persons, df_nds, df_universities, df_kita, cfg, rng, university_flows=university_flows,
        university_report=university_report)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=df_nds.crs)

    assert len(out) == len(df_persons), (
        f"education_gravity placed {len(out)} of {len(df_persons)} pupils")
    assert set(out["person_id"]) == set(df_persons["person_id"])
    if flows_enabled:
        university_report["random_stream"] = {"random_seed": int(context.config("random_seed")),
                                              "stream_id": UNIVERSITY_STREAM_ID,
                                              "generator": "numpy.random.default_rng([random_seed, stream_id])"}
        with open(os.path.join(context.path(), UNIVERSITY_REPORT_FILE), "w", encoding="utf-8") as handle:
            json.dump(_json_safe(university_report), handle, indent=2, sort_keys=True, allow_nan=False)
        return out[["person_id", "commune_id", "location_id", "geometry", "routed_level", "destination_id",
                    "assignment_path"]]
    return out[["person_id", "commune_id", "location_id", "geometry"]]
