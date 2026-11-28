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

Code state: the tables measure the rule BEFORE issue #678 (the code of this branch, ``portal_trips`` and
``outside_tour_modes`` as they stand on ``main`` before PR 695). They are to be re-run after #678 merges; the
report states this in ``rule_state``.

Inputs and the gate marker
--------------------------
The trips are the REWRITTEN day, :data:`TRIPS_STAGE` (``synthesis.population.trips.final``), not the pre-portal
view the SrV comparisons read: the gate is the rewrite itself. A leg is rewritten as a stay at a gate when its row
has ``portal_leg`` True and ``following_purpose == "outside"`` (outbound leg; the return leg has
``preceding_purpose == "outside"``): ``portal_trips.rewrite.rewrite_trips`` sets exactly these. Its original
purpose is overwritten, so the education leg of a student is the first leg that follows ``education`` or the first
outbound gate leg. The stage relies only on the trip columns :data:`COLUMNS_RELIED_ON` (person id, trip index,
mode, the two purposes, the gate marker). With ``braunschweig.portal.enabled`` off there is no gate and the stage
refuses at DAG time. The code of PR 695 was not changed at the time of writing (``git diff`` of its branch against
the merge base shows no change under ``portal_trips`` or ``outside_tour_modes``), so no column is lost there.

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
RULE_STATE = ("measures the rule before issue #678: portal_trips and outside_tour_modes as on main before PR 695; "
              "re-run after #678 merges")

KEY_OUTPUT_SUBDIR = "student_out_commuters_output_subdir"
DEFAULT_OUTPUT_SUBDIR = "analysis/student_out_commuters"
KEY_PORTAL_ENABLED = _portal_config_keys.KEY_ENABLED
KEY_MAX_ROUTABLE_DISTANCE_M = _portal_config_keys.KEY_MAX_ROUTABLE_DISTANCE_M
DEFAULT_MAX_ROUTABLE_DISTANCE_M = _portal_config_keys.DEFAULT_MAX_ROUTABLE_DISTANCE_M
#: The key ``student_mode_reference`` names for the pooling of thin bands (its ``DEFAULT_MIN_BAND_PERSONS`` comment);
#: declared here with that default so this comparison pools the students' table like the in-commuter path will.
KEY_MIN_BAND_PERSONS = "braunschweig.student_incommuters.mode_band_min_persons"
DEFAULT_MIN_BAND_PERSONS = _student_mode_reference.DEFAULT_MIN_BAND_PERSONS

ENRICHED_STAGE = "synthesis.population.enriched"
HOME_ZONES_STAGE = "synthesis.population.spatial.home.zones"
HOME_LOCATIONS_STAGE = "synthesis.population.spatial.home.locations"
GRAVITY_STAGE = "braunschweig.synthesis.locations.education_gravity"
FACILITIES_STAGE = "braunschweig.data.schools.university_facilities"
TRIPS_STAGE = _portal_config_keys.FINAL_TRIPS_STAGE

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
                "reference_share_car_pt"]
GATED_COLUMNS = ["home_kreis", "destination_id", "dest_kreis", "dest_in_region", "n_beyond", "n_gated",
                 "n_not_gated", "n_without_leg", "mean_straight_km"]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_HELPER_MODULES = (_flows_inputs, _student_mode_reference, _portal_config_keys, _constants, run_provenance,
                   json_output)
#: The import closure of the helpers above (ADR-0136); one name per line.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.cordon.mode_reference",
    "braunschweig.data.csv_header",
)


# ======================================================================================= pure functions
def outbound_education_legs(trips: pd.DataFrame, person_ids) -> pd.DataFrame:
    """``[person_id, mode, gated]``: the education leg of each person that has one (module docstring).

    The leg is the first one, by ``trip_index``, that follows ``education`` without being a gate leg, or that is an
    outbound gate leg (``portal_leg`` True, ``following_purpose == "outside"``, ``preceding_purpose`` not
    ``"outside"``); ``gated`` says which. A person without such a leg has no row (the caller counts it).
    """
    missing = [column for column in COLUMNS_RELIED_ON if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the trips lack the column(s) {missing}; found {list(trips.columns)}")
    legs = trips[trips["person_id"].isin(list(person_ids))]
    is_gate = (legs[GATE_LEG_COLUMN].astype(bool) & (legs["following_purpose"] == OUTSIDE_PURPOSE)
               & (legs["preceding_purpose"] != OUTSIDE_PURPOSE))
    is_education = (legs["following_purpose"] == EDUCATION_PURPOSE) & ~legs[GATE_LEG_COLUMN].astype(bool)
    chosen = legs[is_gate | is_education].assign(gated=is_gate[is_gate | is_education])
    chosen = chosen.sort_values(["person_id", "trip_index"], kind="mergesort").drop_duplicates("person_id")
    return chosen[["person_id", "mode", "gated"]].reset_index(drop=True)


def out_commuter_mode_table(students: pd.DataFrame, legs: pd.DataFrame, detour_factor: float,
                            reference: dict) -> pd.DataFrame:
    """The modes of the students at a surrounding institution by band of the routed length, against the reference.

    ``students``: ``[person_id, scope, straight_km]``; ``legs``: :func:`outbound_education_legs`; ``reference``:
    ``{band: {mode: probability}}`` over car and pt (``load_student_education_mode_by_distance``). An out-commuter
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
             f"region: {counts['n_beyond_within_region']}", "",
             f"Tables: `{OUTPUT_FILES['modes']}` ({len(modes)} rows), `{OUTPUT_FILES['gated']}` ({len(gated)} rows)."]
    return "\n".join(lines) + "\n"


def execute(context):
    data_path = str(context.config("data_path"))
    out_dir = os.path.join(str(context.config("output_path")), str(context.config(KEY_OUTPUT_SUBDIR)))
    max_m = float(context.config(KEY_MAX_ROUTABLE_DISTANCE_M))
    min_band_persons = int(context.config(KEY_MIN_BAND_PERSONS))
    if not (np.isfinite(max_m) and max_m > 0):
        raise ValueError(f"{_LOG_TAG} {KEY_MAX_ROUTABLE_DISTANCE_M} must be finite and > 0, got {max_m!r}")
    reference = _student_mode_reference.load_student_education_mode_by_distance(data_path, min_band_persons)

    frame = student_education_frame(
        context.stage(ENRICHED_STAGE), _flows_inputs.home_kreis_by_household(context.stage(HOME_ZONES_STAGE)),
        context.stage(HOME_LOCATIONS_STAGE), context.stage(GRAVITY_STAGE), context.stage(FACILITIES_STAGE))
    legs = outbound_education_legs(context.stage(TRIPS_STAGE), frame["person_id"])
    modes = out_commuter_mode_table(frame, legs, DETOUR_FACTOR, reference)
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
    if n_out and n_out_no_leg == n_out:
        LOGGER.warning("%s no out-commuter has an education leg in the trips: the person ids of the trips and the "
                       "education locations probably do not match", _LOG_TAG)
    LOGGER.info("%s beyond %g m: %d student(s), %d rewritten at a gate, %d between two Kreise of the region",
                _LOG_TAG, max_m, n_beyond, n_gated, n_region)

    report = {
        "status": STATUS, "rule_state": RULE_STATE, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {"max_routable_distance_m": max_m, "detour_factor": DETOUR_FACTOR,
                       "mode_band_min_persons": min_band_persons, "trips_stage": TRIPS_STAGE},
        "columns_relied_on": list(COLUMNS_RELIED_ON),
        "inputs": {"mid_student_education_distance_mode": {
            "path_relative_to_data_path": _student_mode_reference.TABLE_RELATIVE_PATH.replace(os.sep, "/"),
            "sha256": synpp_tokens.text_file_sha256(_mode_table_path(data_path))}},
        "counts": {"n_resident_students": int(len(frame)), "n_out_commuters": n_out,
                   "n_out_commuters_without_leg": n_out_no_leg, "n_beyond": n_beyond, "n_gated": n_gated,
                   "n_beyond_within_region": n_region},
    }
    os.makedirs(out_dir, exist_ok=True)
    _write_csv(modes, out_dir, OUTPUT_FILES["modes"])
    _write_csv(gated, out_dir, OUTPUT_FILES["gated"])
    json_output.write_json(os.path.join(out_dir, REPORT_FILE), report)
    json_output.write_json(os.path.join(str(context.path()), REPORT_FILE), report)
    with open(os.path.join(out_dir, OUTPUT_FILES["summary"]), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(summary_markdown(report, modes, gated))
    return {"modes": modes, "gated": gated, "report": report}
