"""synpp stage: the out-commuting students' modes and their legs rewritten as a stay at a gate (#140, Phase 4, B6a).

A READ-ONLY MEASUREMENT of the synthesis. Phase 4 does not change how a leg out of the region is moded or rewritten
(``braunschweig/synthesis/outside_tour_modes`` and ``braunschweig/synthesis/portal_trips``; issue #678 changes both
in parallel); this stage only reports what those mechanisms did to the students. Two tables:

* ``student_out_commuter_modes.csv``: the residents of the day whose destination is a surrounding institution
  (``scope == "surrounding"`` in the facilities), by distance band of the students' mode table
  (``braunschweig.data.education.student_mode_reference``, bands of the ROUTED length = straight line x
  ``braunschweig.constants.ROUTED_DETOUR_FACTOR``, as the in-commuter path), the mode count of the band and its
  share, against the students' table the in-commuters draw from (reference restricted to car and public transport;
  the model's share among car and public transport is given next to it so the two are comparable);
* ``student_gated_education_legs.csv``: per home Kreis and destination the students whose home to destination
  straight line exceeds ``braunschweig.portal.max_routable_distance_m`` (the portal layer's classification
  distance for an education leg), how many of them have the leg rewritten as a stay at a gate, and whether the
  destination lies in a Kreis of the region. A student within the routable distance is not counted. A leg
  between two Kreise of the region beyond that distance counts at its site in the acceptance table of
  ``student_flows`` but does not arrive there in the simulation.

Code state: the tables measure the SYNTHESIS of the code state the stage runs on (the commit is written to the
report as ``pipeline_commit``). PR 695 is merged; on ``main`` it changes ``portal_trips/stage.py`` and
``outside_tour_modes/stage.py`` only in their helper lists (the lead's review of this task), so no column used here
changes. The mode choice that issue #678 adds happens in the simulation (Java side) and is NOT measured by this
stage; ``rule_state`` in the report says so.

Inputs and the gate marker
--------------------------
The trips are the frame the simulation starts from, :data:`TRIPS_STAGE` (``synthesis.population.trips.simulated``,
the alias of ``braunschweig.synthesis.outside_tour_modes.stage``, which re-judges the modes of the tours that touch
the outside; ``braunschweig.matsim.scenario.population`` writes that day). The stage copies the frame it reads
(``out = trips.copy()``), so ``portal_leg`` and ``trip_index`` of the portal rewrite survive and the gate marker
and the modes are read from the same frame. A leg is rewritten as a stay at a gate when its row has ``portal_leg``
True and ``following_purpose == "outside"`` (outbound leg; the return leg has ``preceding_purpose == "outside"``):
``portal_trips.rewrite.rewrite_trips`` sets exactly these. The leg's original purpose is overwritten there, so the
education leg of a student is chosen by :func:`outbound_education_legs`: the first leg that follows ``education``
and is no gate leg; if there is none, the first outbound gate leg (an education leg that was itself rewritten). A
gate stay for another purpose before the education leg therefore does not hide it. Counted and reported, because
they make the choice uncertain (:func:`leg_diagnostics`): students with a gate stay before their education leg,
with more than one gate stay, with further education legs after the first, and students with a gate stay and no
education leg left (the leg may have been swallowed by the legs a stay removes). The stage relies only on the trip
columns :data:`COLUMNS_RELIED_ON`. With ``braunschweig.portal.enabled`` off there is no gate and the stage refuses at
DAG time.

Residents of the day are the persons with a destination whose ``assignment_path`` is not ``absent_*`` (the student
flows' rule); students are ``P_TAET == 10``. Home Kreis: ``departement_id`` of the home zones, as everywhere in
Phase 4. The destination's Kreis is ``site_ags8[:5]`` of its facility row.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import numpy as np
import pandas as pd

from braunschweig import constants as _constants
from braunschweig import provenance as run_provenance
from braunschweig import synpp_tokens
from braunschweig.analysis import json_output
from braunschweig.data.education import student_flows_inputs as _flows_inputs
from braunschweig.data.education import student_mode_reference as _student_mode_reference
from braunschweig.synthesis.portal_trips import config_keys as _portal_config_keys

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.student_out_commuters")
_LOG_TAG = "[student_out_commuters]"

STATUS = "measurement of the synthesis, not validation"
RULE_STATE = ("measures the synthesis of the code state it runs on (see pipeline_commit); the mode choice of issue "
              "#678 happens in the simulation and is not measured here")

KEY_OUTPUT_SUBDIR = "student_out_commuters_output_subdir"
DEFAULT_OUTPUT_SUBDIR = "analysis/student_out_commuters"
KEY_PORTAL_ENABLED = _portal_config_keys.KEY_ENABLED
KEY_MAX_ROUTABLE_DISTANCE_M = _portal_config_keys.KEY_MAX_ROUTABLE_DISTANCE_M
DEFAULT_MAX_ROUTABLE_DISTANCE_M = _portal_config_keys.DEFAULT_MAX_ROUTABLE_DISTANCE_M
#: Repeated from ``student_incommuters`` (a stage module whose closure this stage should not inherit) and pinned
#: against it by ``tests/test_student_out_commuters.py``: the pooling of thin bands is the in-commuter path's.
KEY_MIN_BAND_PERSONS = "braunschweig.student_incommuters.mode_band_min_persons"
DEFAULT_MIN_BAND_PERSONS = _student_mode_reference.DEFAULT_MIN_BAND_PERSONS
#: Share of the out-commuters without an education leg in the trips above which a WARNING is logged.
#: ASSUMPTION: not from a table; a high share means a failed id join, not students without a leg.
KEY_NO_LEG_WARN_SHARE = "braunschweig.students.out_commuter_no_leg_warn_share"
DEFAULT_NO_LEG_WARN_SHARE = 0.25

ENRICHED_STAGE = "synthesis.population.enriched"
HOME_ZONES_STAGE = "synthesis.population.spatial.home.zones"
HOME_LOCATIONS_STAGE = "synthesis.population.spatial.home.locations"
GRAVITY_STAGE = "braunschweig.synthesis.locations.education_gravity"
FACILITIES_STAGE = "braunschweig.data.schools.university_facilities"
#: Literal, pinned against ``outside_tour_modes.config_keys.SIMULATED_TRIPS_ALIAS`` (importing that package would
#: pull its closure into this token).
TRIPS_STAGE = "synthesis.population.trips.simulated"

#: Repeated from their owners, pinned by ``tests/test_student_out_commuters.py``.
GATE_LEG_COLUMN = "portal_leg"                     # portal_trips.rewrite.PORTAL_LEG_COLUMN
OUTSIDE_PURPOSE = _portal_config_keys.OUTSIDE_PURPOSE
EDUCATION_PURPOSE = "education"
ABSENT_PATH_PREFIX = "absent_"                     # education_gravity.ABSENT_PATH_PREFIX
SCOPE_SURROUNDING = "surrounding"
DETOUR_FACTOR = _constants.ROUTED_DETOUR_FACTOR
COLUMNS_RELIED_ON = ("person_id", "trip_index", "mode", "preceding_purpose", "following_purpose", GATE_LEG_COLUMN)

REPORT_FILE = "student_out_commuters_report.json"
OUTPUT_FILES = {"modes": "student_out_commuter_modes.csv", "gated": "student_gated_education_legs.csv",
                "summary": "student_out_commuters_summary.md"}
MODE_COLUMNS = ["distance_band", "mode", "n_persons", "n_gated", "model_share_of_band", "model_share_of_car_pt",
                "reference_share_car_pt", "reference_band_pooled"]
GATED_COLUMNS = ["home_kreis", "destination_id", "dest_kreis", "dest_in_region", "n_beyond", "n_gated",
                 "n_not_gated", "n_not_gated_with_leg", "n_not_gated_without_leg", "mean_straight_km"]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_HELPER_MODULES = (_flows_inputs, _student_mode_reference, _portal_config_keys, _constants, run_provenance,
                   json_output)
#: The import closure of the helpers above (ADR-0136); one name per line.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.cordon.mode_reference",
    "braunschweig.data.csv_header",
)


# ======================================================================================= pure functions
def _leg_flags(trips: pd.DataFrame, person_ids) -> pd.DataFrame:
    missing = [column for column in COLUMNS_RELIED_ON if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the trips lack the column(s) {missing}; found {list(trips.columns)}")
    legs = trips[trips["person_id"].isin(list(person_ids))].copy()
    marker = legs[GATE_LEG_COLUMN].astype(bool)
    legs["is_gate"] = (marker & (legs["following_purpose"] == OUTSIDE_PURPOSE)
                       & (legs["preceding_purpose"] != OUTSIDE_PURPOSE))
    legs["is_education"] = (legs["following_purpose"] == EDUCATION_PURPOSE) & ~marker
    return legs.sort_values(["person_id", "trip_index"], kind="mergesort")


def outbound_education_legs(trips: pd.DataFrame, person_ids) -> pd.DataFrame:
    """``[person_id, mode, gated]``: the education leg of each person that has one (module docstring).

    The leg is the first one, by ``trip_index``, that follows ``education`` without being a gate leg
    (``gated`` False); a person without one takes the first outbound gate leg (``portal_leg`` True,
    ``following_purpose == "outside"``, ``preceding_purpose`` not ``"outside"``; ``gated`` True), an education leg
    that was itself rewritten. A person with neither has no row (the caller counts it).
    """
    legs = _leg_flags(trips, person_ids)
    education = legs[legs["is_education"]].drop_duplicates("person_id").assign(gated=False)
    gate = legs[legs["is_gate"] & ~legs["person_id"].isin(education["person_id"])].drop_duplicates(
        "person_id").assign(gated=True)
    chosen = pd.concat([education, gate]).sort_values("person_id", kind="mergesort")
    return chosen[["person_id", "mode", "gated"]].reset_index(drop=True)


def leg_diagnostics(trips: pd.DataFrame, person_ids) -> pd.DataFrame:
    """Per person of ``person_ids`` (one row each): what makes the choice of the education leg uncertain.

    Columns ``person_id, n_gate_stays`` (outbound gate legs), ``n_education_legs`` (legs following education that
    are no gate leg), ``gate_before_education`` (a gate stay precedes the first education leg),
    ``gate_without_education`` (a gate stay and no education leg left). A person without trips has zeros.
    """
    legs = _leg_flags(trips, person_ids)
    grouped = legs.groupby("person_id")
    result = pd.DataFrame({"n_gate_stays": grouped["is_gate"].sum(), "n_education_legs": grouped["is_education"].sum()})
    first_gate = legs[legs["is_gate"]].groupby("person_id")["trip_index"].min()
    first_education = legs[legs["is_education"]].groupby("person_id")["trip_index"].min()
    result = result.reindex(pd.Index(list(person_ids), name="person_id"), fill_value=0)
    result["n_gate_stays"] = result["n_gate_stays"].astype(int)
    result["n_education_legs"] = result["n_education_legs"].astype(int)
    gate_at = first_gate.reindex(result.index)
    education_at = first_education.reindex(result.index)
    result["gate_before_education"] = (gate_at < education_at).to_numpy()
    result["gate_without_education"] = ((result["n_gate_stays"] > 0) & (result["n_education_legs"] == 0)).to_numpy()
    return result.reset_index()


def pooled_bands(table: pd.DataFrame, min_band_persons: int) -> frozenset:
    """The bands of the students' mode table that ``pool_thin_bands`` merges with a neighbour."""
    groups = _student_mode_reference.pool_thin_bands(table, min_band_persons)
    return frozenset(band for group in groups if len(group) > 1 for band in group)


def out_commuter_mode_table(students: pd.DataFrame, legs: pd.DataFrame, detour_factor: float,
                            reference: dict, pooled: frozenset = frozenset()) -> pd.DataFrame:
    """The modes of the students at a surrounding institution by band of the routed length, against the reference.

    ``students``: ``[person_id, scope, straight_km]``; ``legs``: :func:`outbound_education_legs`; ``reference``:
    ``{band: {mode: probability}}`` over car and pt (``load_student_education_mode_by_distance``); ``pooled``: the
    bands whose reference is pooled with a neighbour (:func:`pooled_bands`), marked in ``reference_band_pooled``. An out-commuter
    without a leg is counted in ``attrs["n_without_leg"]`` and not in a band; ``attrs["n_out_commuters"]`` is all.
    """
    out = students[students["scope"] == SCOPE_SURROUNDING]
    table = out.merge(legs, on="person_id", how="left")
    have_leg = table["mode"].notna()
    counted = table[have_leg].copy()
    counted["distance_band"] = [_student_mode_reference.student_mode_band(km * detour_factor)
                                for km in counted["straight_km"]]
    grouped = (counted.groupby(["distance_band", "mode"]).agg(n_persons=("person_id", "size"),
                                                              n_gated=("gated", "sum")).reset_index())
    grouped["n_gated"] = grouped["n_gated"].astype(int)
    band_total = grouped.groupby("distance_band")["n_persons"].transform("sum")
    allowed = grouped["mode"].isin(_student_mode_reference.ALLOWED_MODES)
    car_pt_total = grouped[allowed].groupby("distance_band")["n_persons"].sum()
    grouped["model_share_of_band"] = grouped["n_persons"] / band_total
    grouped["model_share_of_car_pt"] = np.where(
        allowed, grouped["n_persons"] / grouped["distance_band"].map(car_pt_total), np.nan)
    grouped["reference_share_car_pt"] = [
        float(reference.get(band, {}).get(mode, np.nan)) if mode in _student_mode_reference.ALLOWED_MODES else np.nan
        for band, mode in zip(grouped["distance_band"], grouped["mode"])]
    grouped["reference_band_pooled"] = grouped["distance_band"].isin(pooled)
    order = {label: i for i, label in enumerate(_student_mode_reference.BAND_LABELS)}
    grouped = grouped.sort_values(["distance_band", "mode"], key=lambda s: s.map(order) if s.name == "distance_band"
                                  else s, kind="mergesort").reset_index(drop=True)
    result = grouped.reindex(columns=MODE_COLUMNS)
    result.attrs["n_out_commuters"] = int(len(out))
    result.attrs["n_without_leg"] = int((~have_leg).sum())
    return result


def gated_leg_table(students: pd.DataFrame, legs: pd.DataFrame, max_routable_distance_m: float) -> pd.DataFrame:
    """Per home Kreis and destination: the students beyond the routable distance and those rewritten at a gate.

    ``students``: ``[person_id, home_kreis, destination_id, dest_kreis, straight_km]``. Only a student whose
    straight line is MORE than ``max_routable_distance_m`` is counted (the portal layer's rule, ``gt``).
    ``dest_in_region`` marks a destination Kreis of the region, so a leg between two Kreise of the region beyond the
    distance is visible.
    """
    beyond = students[students["straight_km"] * 1000.0 > max_routable_distance_m]
    if beyond.empty:
        return pd.DataFrame(columns=GATED_COLUMNS)
    merged = beyond.merge(legs, on="person_id", how="left")
    merged["gated"] = merged["gated"].fillna(False).astype(bool)
    merged["no_leg"] = merged["mode"].isna()
    rows = (merged.groupby(["home_kreis", "destination_id", "dest_kreis"], sort=True)
            .agg(n_beyond=("person_id", "size"), n_gated=("gated", "sum"), n_without_leg=("no_leg", "sum"),
                 mean_straight_km=("straight_km", "mean")).reset_index())
    rows["n_gated"] = rows["n_gated"].astype(int)
    rows["n_without_leg"] = rows["n_without_leg"].astype(int)
    rows["n_not_gated"] = rows["n_beyond"] - rows["n_gated"]
    # a gated student always has a leg, so a student without a leg is never gated
    rows["n_not_gated_without_leg"] = rows["n_without_leg"]
    rows["n_not_gated_with_leg"] = rows["n_not_gated"] - rows["n_without_leg"]
    rows["dest_in_region"] = rows["dest_kreis"].isin(_flows_inputs.ZGB_KREISE)
    return rows[GATED_COLUMNS]


def student_education_frame(persons: pd.DataFrame, home_kreis: pd.Series, homes, gravity, facilities) -> pd.DataFrame:
    """One row per resident student of the day: home Kreis, destination, scope, destination Kreis, straight km.

    Columns ``person_id, home_kreis, destination_id, scope, dest_kreis, straight_km``. Raises ``ValueError`` on a
    household without a home Kreis, a gravity person that is no enriched person, a destination without a facility
    row, a destination whose facility rows disagree on scope or site, or a CRS other than EPSG:25832.
    """
    for column in ("person_id", "household_id", "P_TAET"):
        if column not in persons.columns:
            raise ValueError(f"{_LOG_TAG} the enriched persons lack the column {column!r}")
    for name, frame in (("home locations", homes), ("education locations", gravity)):
        crs = getattr(frame, "crs", None)
        if crs is None or crs.to_epsg() != 25832:
            raise ValueError(f"{_LOG_TAG} the {name} must be in EPSG:25832, got {crs}")
    for column in ("destination_id", "assignment_path"):
        if column not in gravity.columns:
            raise ValueError(f"{_LOG_TAG} the education locations lack the ON-path column {column!r}")
    unknown = set(gravity["person_id"]) - set(persons["person_id"])
    if unknown:
        raise ValueError(f"{_LOG_TAG} {len(unknown)} person(s) of the education locations are no enriched person")
    taet = pd.to_numeric(persons["P_TAET"], errors="coerce")
    student_ids = persons.loc[taet == _flows_inputs.STUDENT_P_TAET, "person_id"]
    located = gravity[gravity["person_id"].isin(student_ids) & gravity["destination_id"].notna()]
    located = located[~located["assignment_path"].fillna("").astype(str).str.startswith(ABSENT_PATH_PREFIX)]
    destination = facilities.drop_duplicates(["destination_id", "scope", "site_ags8"])
    if destination["destination_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} a destination has facility rows with two scopes or sites")
    destination = destination.set_index("destination_id")
    missing = sorted(set(located["destination_id"]) - set(destination.index))
    if missing:
        raise ValueError(f"{_LOG_TAG} destinations without a facility row: {missing[:5]}")
    household = located["person_id"].map(persons.set_index("person_id")["household_id"])
    kreis = household.map(home_kreis)
    if kreis.isna().any():
        raise ValueError(f"{_LOG_TAG} {int(kreis.isna().sum())} students live in a household without a home Kreis")
    home_xy = pd.DataFrame({"x": homes.geometry.x.to_numpy(dtype=float), "y": homes.geometry.y.to_numpy(dtype=float)},
                           index=homes["household_id"].to_numpy())
    dx = household.map(home_xy["x"]).to_numpy(dtype=float) - located.geometry.x.to_numpy(dtype=float)
    dy = household.map(home_xy["y"]).to_numpy(dtype=float) - located.geometry.y.to_numpy(dtype=float)
    straight_km = np.hypot(dx, dy) / 1000.0
    if np.isnan(straight_km).any():
        raise ValueError(f"{_LOG_TAG} {int(np.isnan(straight_km).sum())} student(s) without a home point")
    return pd.DataFrame({
        "person_id": located["person_id"].to_numpy(), "home_kreis": kreis.to_numpy(dtype=object),
        "destination_id": located["destination_id"].to_numpy(dtype=object),
        "scope": located["destination_id"].map(destination["scope"]).to_numpy(dtype=object),
        "dest_kreis": located["destination_id"].map(destination["site_ags8"]).astype(str).str[:5].to_numpy(dtype=object),
        "straight_km": straight_km})


# ============================================================================================== stage
def _mode_table_path(data_path) -> str:
    return os.path.join(str(data_path), _student_mode_reference.TABLE_RELATIVE_PATH)


def validate(context):
    token = synpp_tokens.source_token(_HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
                                      stage_label=__name__ + ".validate()")
    path = _mode_table_path(context.config("data_path"))
    if not os.path.isfile(path):
        raise RuntimeError(f"{_LOG_TAG} the students' mode table is missing: {path}")
    token["files"] = {"mid_student_education_distance_mode": synpp_tokens.text_file_sha256(path)}
    return token


def configure(context):
    if not context.config(KEY_PORTAL_ENABLED, _portal_config_keys.DEFAULT_ENABLED):
        raise ValueError(f"{_LOG_TAG} {KEY_PORTAL_ENABLED} is false: without the portal layer no leg is rewritten "
                         f"as a stay at a gate and there is nothing to report; drop this stage from the run list")
    for stage in (ENRICHED_STAGE, HOME_ZONES_STAGE, HOME_LOCATIONS_STAGE, GRAVITY_STAGE, FACILITIES_STAGE,
                  TRIPS_STAGE):
        context.stage(stage)
    context.config("data_path")
    context.config("output_path")
    context.config(KEY_MAX_ROUTABLE_DISTANCE_M, DEFAULT_MAX_ROUTABLE_DISTANCE_M)
    context.config(KEY_MIN_BAND_PERSONS, DEFAULT_MIN_BAND_PERSONS)
    context.config(KEY_OUTPUT_SUBDIR, DEFAULT_OUTPUT_SUBDIR)
    context.config(KEY_NO_LEG_WARN_SHARE, DEFAULT_NO_LEG_WARN_SHARE)


def _write_csv(table: pd.DataFrame, directory: str, name: str) -> None:
    table.to_csv(os.path.join(directory, name), index=False, float_format="%.10g", lineterminator="\n",
                 encoding="utf-8")


def summary_markdown(report: dict, modes: pd.DataFrame, gated: pd.DataFrame) -> str:
    counts = report["counts"]
    lines = ["# Out-commuting students: modes and legs at a gate", "", f"Status: {STATUS}.", "",
             f"Rule state: {RULE_STATE}.", "",
             f"- out-commuting students of the day: {counts['n_out_commuters']} "
             f"({counts['n_out_commuters_without_leg']} without an education leg in the trips)",
             f"- students beyond {report['parameters']['max_routable_distance_m']:g} m straight line: "
             f"{counts['n_beyond']}, rewritten at a gate: {counts['n_gated']}, of those between two Kreise of the "
             f"region: {counts['n_beyond_within_region']}; beyond and not at a gate: "
             f"{counts['n_beyond_not_gated_with_leg']} with an education leg, "
             f"{counts['n_beyond_not_gated_without_leg']} without", "",
             "Students whose education leg is uncertain (module docstring):",
             f"- a gate stay before the education leg: {counts['n_gate_before_education']}",
             f"- more than one gate stay: {counts['n_multiple_gate_stays']}",
             f"- further education legs after the first: {counts['n_further_education_legs']}",
             f"- a gate stay and no education leg left: {counts['n_gate_without_education']} "
             f"({counts['n_gate_without_education_within_distance']} within the routable distance, where the "
             f"leg may have been swallowed by a stay)",
             f"- bands of the reference pooled with a neighbour: {report['pooled_reference_bands'] or 'none'}", "",
             f"Tables: `{OUTPUT_FILES['modes']}` ({len(modes)} rows), `{OUTPUT_FILES['gated']}` ({len(gated)} rows)."]
    return "\n".join(lines) + "\n"


def execute(context):
    data_path = str(context.config("data_path"))
    out_dir = os.path.join(str(context.config("output_path")), str(context.config(KEY_OUTPUT_SUBDIR)))
    max_m = float(context.config(KEY_MAX_ROUTABLE_DISTANCE_M))
    min_band_persons = int(context.config(KEY_MIN_BAND_PERSONS))
    if not (np.isfinite(max_m) and max_m > 0):
        raise ValueError(f"{_LOG_TAG} {KEY_MAX_ROUTABLE_DISTANCE_M} must be finite and > 0, got {max_m!r}")
    warn_share = float(context.config(KEY_NO_LEG_WARN_SHARE))
    reference = _student_mode_reference.load_student_education_mode_by_distance(data_path, min_band_persons)
    mode_table = pd.read_csv(_mode_table_path(data_path), comment="#", encoding="utf-8",
                             dtype={"distance_band": str, "mode": str})
    pooled = pooled_bands(mode_table, min_band_persons)

    frame = student_education_frame(
        context.stage(ENRICHED_STAGE), _flows_inputs.home_kreis_by_household(context.stage(HOME_ZONES_STAGE)),
        context.stage(HOME_LOCATIONS_STAGE), context.stage(GRAVITY_STAGE), context.stage(FACILITIES_STAGE))
    trips = context.stage(TRIPS_STAGE)
    legs = outbound_education_legs(trips, frame["person_id"])
    diagnostics = leg_diagnostics(trips, frame["person_id"])
    modes = out_commuter_mode_table(frame, legs, DETOUR_FACTOR, reference, pooled)
    gated = gated_leg_table(frame, legs, max_m)

    n_out, n_out_no_leg = modes.attrs["n_out_commuters"], modes.attrs["n_without_leg"]
    n_beyond = int(gated["n_beyond"].sum()) if len(gated) else 0
    n_gated = int(gated["n_gated"].sum()) if len(gated) else 0
    n_region = int(gated.loc[gated["dest_in_region"], "n_beyond"].sum()) if len(gated) else 0
    # Not a fallback: the students without an education leg in the final trips (counted so that a join that fails
    # everywhere is visible as a rate).
    LOGGER.info("%s out-commuters with an education leg in the trips: primary %d/%d (%.1f%%), without a leg %d "
                "(%.1f%%)", _LOG_TAG, n_out - n_out_no_leg, n_out, 100.0 * (n_out - n_out_no_leg) / max(n_out, 1),
                n_out_no_leg, 100.0 * n_out_no_leg / max(n_out, 1))
    if n_out and n_out_no_leg / n_out > warn_share:
        LOGGER.warning("%s %d of %d out-commuters (%.1f%%) have no education leg in the trips, above the warn share "
                       "%g: the person ids of the trips and the education locations probably do not match",
                       _LOG_TAG, n_out_no_leg, n_out, 100.0 * n_out_no_leg / n_out, warn_share)
    beyond_mask = (frame["straight_km"] * 1000.0 > max_m).to_numpy()
    by_person = diagnostics.set_index("person_id").reindex(frame["person_id"])
    within = ~beyond_mask
    leg_counts = {
        "n_gate_before_education": int(by_person["gate_before_education"].sum()),
        "n_multiple_gate_stays": int((by_person["n_gate_stays"] > 1).sum()),
        "n_further_education_legs": int((by_person["n_education_legs"] > 1).sum()),
        "n_gate_without_education": int(by_person["gate_without_education"].sum()),
        # within the routable distance the education leg is no gate stay: a gate stay and no education leg there
        # points at a leg swallowed by the legs another stay removes
        "n_gate_without_education_within_distance": int(
            by_person["gate_without_education"].to_numpy()[within].sum()),
        "n_beyond_not_gated_with_leg": int(gated["n_not_gated_with_leg"].sum()) if len(gated) else 0,
        "n_beyond_not_gated_without_leg": int(gated["n_not_gated_without_leg"].sum()) if len(gated) else 0}
    LOGGER.info("%s uncertain education legs: %s", _LOG_TAG, leg_counts)
    LOGGER.info("%s beyond %g m: %d student(s), %d rewritten at a gate, %d between two Kreise of the region",
                _LOG_TAG, max_m, n_beyond, n_gated, n_region)

    report = {
        "status": STATUS, "rule_state": RULE_STATE, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {"max_routable_distance_m": max_m, "detour_factor": DETOUR_FACTOR,
                       "mode_band_min_persons": min_band_persons, "trips_stage": TRIPS_STAGE,
                       KEY_NO_LEG_WARN_SHARE: warn_share},
        "columns_relied_on": list(COLUMNS_RELIED_ON),
        "inputs": {"mid_student_education_distance_mode": {
            "path_relative_to_data_path": _student_mode_reference.TABLE_RELATIVE_PATH.replace(os.sep, "/"),
            "sha256": synpp_tokens.text_file_sha256(_mode_table_path(data_path))}},
        "counts": {"n_resident_students": int(len(frame)), "n_out_commuters": n_out,
                   "n_out_commuters_without_leg": n_out_no_leg, "n_beyond": n_beyond, "n_gated": n_gated,
                   "n_beyond_within_region": n_region, **leg_counts},
        "pooled_reference_bands": sorted(pooled),
    }
    os.makedirs(out_dir, exist_ok=True)
    _write_csv(modes, out_dir, OUTPUT_FILES["modes"])
    _write_csv(gated, out_dir, OUTPUT_FILES["gated"])
    json_output.write_json(os.path.join(out_dir, REPORT_FILE), report)
    json_output.write_json(os.path.join(str(context.path()), REPORT_FILE), report)
    with open(os.path.join(out_dir, OUTPUT_FILES["summary"]), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(summary_markdown(report, modes, gated))
    return {"modes": modes, "gated": gated, "report": report}
