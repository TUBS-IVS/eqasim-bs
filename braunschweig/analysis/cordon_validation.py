"""synpp stage: per-run cross-cordon commuter validation output.

Writes, into ``<output_path>/analysis/cordon/`` on every cordon run:
  - commuter_validation.csv  : in-commuter counts per (Kreis, direction, mode)
  - gates.csv / gates.gpkg   : per-entry-point flows (+ point geometry for QGIS)
  - summary.md               : short digest
  - gate_volumes.csv         : per-gate BA in/out SvB gravity expectation
  - gate_volumes.gpkg        : same as point geometry for QGIS (both directions)
  - gate_volumes_scaled.csv  : the same expectation with the OUTBOUND direction restricted to
    the workers who actually commute on the reporting day (ADR-0104 check 3; written only when
    the reporting-day state model is enabled)
  - commute_day_state_scaling.json : the counts behind that scaling factor -- how many external
    workers there are, how many are at_workplace, and both drops of the state/work-location
    join -- as strict JSON (NaN becomes null), so the evidence for check 3 survives the run log

So "how many in-commuters enter where" AND "how many out-commuters leave where"
(gravity expectation, not realized agents) are visible + mappable on every run.
Flag-gated on ``cordon_enabled`` (no-op otherwise). Uses the tested
``braunschweig.data.cordon.validation_output`` writers.

ADR-0104 check 3 (the reporting-day out-commuter expectation): the BA Pendleratlas SvB
out-commuter volumes the gate expectation is built from are a REGISTER universe -- every
employee whose workplace lies outside the region, counted whether or not they travel on any
given day. The synthetic reporting day is not: a worker drawn to ``home`` or ``absent`` makes
no commute trip. The unscaled expectation therefore over-states what the simulation can
produce at the cordon by construction. This stage does not silently replace it: BOTH
expectations are written side by side, the realised ``at_workplace`` share of EXTERNAL workers
that scales them is written into the file and logged, and the unscaled ``gate_volumes.csv``
stays exactly as it was.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import os

from braunschweig.analysis import json_output as _json_output
from braunschweig.analysis.json_output import write_json
from braunschweig.data.cordon import gate_assignment as _gate_assignment
from braunschweig.data.cordon import validation_output as _validation_output
from braunschweig.data.cordon.gate_assignment import gate_volume_summary
from braunschweig.data.cordon.validation_output import (
    write_cordon_validation,
    write_gate_volumes,
)

LOGGER = logging.getLogger("braunschweig.analysis.cordon_validation")

_LOG_TAG = "[braunschweig.analysis.cordon_validation]"

KEY_COMMUTE_DAY_STATE_ENABLED = "commute_day_state_enabled"
DEFAULT_COMMUTE_DAY_STATE_ENABLED = True

STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"
#: State of a worker who actually travels to their workplace on the reporting day.
AT_WORKPLACE_STATE = "at_workplace"
#: Prefix ``braunschweig.data.external_workplaces`` puts in front of the 8-digit AGS of a
#: fabricated out-of-region workplace; the same marker
#: ``braunschweig.analysis.synthesis.work_participation_by_kreis`` uses.
EXTERNAL_PREFIX = "EXT"

SCALED_GATE_COLUMNS = ("gate_id", "inbound", "outbound", "outbound_at_workplace",
                       "at_workplace_share_external", "n_kreise")

#: Side-car holding the counts behind the scaling factor. The factor itself appears in every row
#: of gate_volumes_scaled.csv, but the counts that produced it (and the two join drops) would
#: otherwise live only in the run log, which is not an artifact anybody can re-read later.
SCALING_JSON_NAME = "commute_day_state_scaling.json"

#: Modules whose sources this stage's cache token must cover (see :func:`validate`): every file
#: this stage writes is produced by one of them, so an edit to a writer must devalidate the
#: cached outputs exactly like an edit here.
_HELPER_MODULES = (_validation_output, _gate_assignment, _json_output)


def validate(context):
    """synpp validation token: md5 over the helper modules that shape this stage's output.

    synpp hashes only THIS module's source, so an edit to a helper it writes its files through
    would otherwise leave the cached outputs in place although their content or format changed
    (same mechanism as ``braunschweig.synthesis.locations.secondary_chainsolvers.validate``).
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    context.config("cordon_enabled", False)
    context.config("output_path")
    context.config("sampling_rate")
    context.config(KEY_COMMUTE_DAY_STATE_ENABLED, DEFAULT_COMMUTE_DAY_STATE_ENABLED)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.cordon_gates")
        if context.config(KEY_COMMUTE_DAY_STATE_ENABLED):
            context.stage(STATE_STAGE)
            context.stage("synthesis.population.spatial.primary.locations")
            # The workplace pool is declared under its CONCRETE name rather than under the
            # synthesis.locations.work alias seam, for the reason documented at length in
            # braunschweig.analysis.synthesis.work_participation_by_kreis.configure: a NEW
            # declaration of the seam name renames the single resulting node in the production
            # DAG snapshot, which four other stages already depend on.
            context.stage("braunschweig.locations.work")


def external_at_workplace_share(states, work_locations, workplaces, stats=None):
    """Realised share of EXTERNAL workers who are ``at_workplace`` on the reporting day.

    ``states`` is the ``states`` frame of ``braunschweig.synthesis.commute_day.state_stage``
    (one row per worker: ``person_id, commute_day_state``); ``work_locations`` is the work half
    of ``synthesis.population.spatial.primary.locations`` (``person_id, location_id``);
    ``workplaces`` is ``braunschweig.locations.work`` (``location_id, commune_id``). A workplace
    is external exactly when its ``commune_id`` carries the :data:`EXTERNAL_PREFIX` -- the same
    test the Phase A measurement uses -- and the ``commune_id`` is joined via ``location_id``
    rather than read from the primary-locations frame, because the latter names that column
    ``commune_id`` only while ``taz_work_location_choice`` is off.

    Returns a float in [0, 1]. Every exclusion is counted and logged; a population with NO
    external worker at all RAISES rather than returning a substituted 1.0, because a scaling
    factor that no worker supports would be an invented number attached to a written
    expectation (CLAUDE.md "No invented reference values").
    """
    for frame, columns, what in (
            (states, ("person_id", "commute_day_state"), "the commute-day state frame"),
            (work_locations, ("person_id", "location_id"),
             "synthesis.population.spatial.primary.locations (work)"),
            (workplaces, ("location_id", "commune_id"), "braunschweig.locations.work")):
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError(f"{_LOG_TAG} {what} is missing the required column(s) {missing} "
                             f"(present: {sorted(frame.columns)[:20]})")

    frame = states[["person_id", "commute_day_state"]].merge(
        work_locations[["person_id", "location_id"]], on="person_id", how="inner")
    # The inner join silently drops both sides' non-matches, so both drops are reported: a state
    # row without a work location means the state stage and the primary-location assignment
    # disagree about who works, and a work location without a state means a worker the state
    # stage never saw. Either is a person_id join defect, not a rounding detail (CLAUDE.md
    # "Fallback transparency").
    n_states_dropped = len(states) - len(frame)
    n_work_rows_dropped = len(work_locations) - len(frame)
    if n_states_dropped or n_work_rows_dropped:
        LOGGER.warning(
            "%s the state/work-location join kept %d row(s): %d of the %d state row(s) have no "
            "assigned work location and %d of the %d work-location row(s) have no drawn state; "
            "both sides should describe the same workers -- check the person_id join",
            _LOG_TAG, len(frame), n_states_dropped, len(states), n_work_rows_dropped,
            len(work_locations))

    frame = frame.merge(workplaces[["location_id", "commune_id"]].drop_duplicates("location_id"),
                        on="location_id", how="left")

    n_workers = len(frame)
    n_unresolved = int(frame["commune_id"].isna().sum())
    is_external = frame["commune_id"].astype(str).str.startswith(EXTERNAL_PREFIX)
    n_external = int(is_external.sum())
    n_at_workplace = int((frame.loc[is_external, "commute_day_state"] == AT_WORKPLACE_STATE).sum())

    if n_external == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {n_workers} workers has an external workplace "
            f"(commune_id starting with {EXTERNAL_PREFIX!r}); {n_unresolved} have no workplace "
            "row at all. The out-commuter expectation cannot be scaled by a share no worker "
            "supports -- check the location_id join to braunschweig.locations.work and the "
            "external-workplace fabrication before running the cordon validation.")

    share = n_at_workplace / n_external
    LOGGER.info("%s reporting-day out-commuters: %d/%d external workers (%.2f%%) are "
                "%s; %d/%d workers have no resolvable workplace",
                _LOG_TAG, n_at_workplace, n_external, 100.0 * share, AT_WORKPLACE_STATE,
                n_unresolved, n_workers)
    if stats is not None:
        stats.update(n_workers=n_workers, n_external=n_external,
                     n_external_at_workplace=n_at_workplace,
                     n_workplace_unresolved=n_unresolved,
                     n_states_without_work_location=n_states_dropped,
                     n_work_locations_without_state=n_work_rows_dropped, share=float(share))
    return float(share)


def scaled_gate_volumes(assignment, at_workplace_share):
    """Per-gate gate volumes with the OUTBOUND expectation restricted to reporting-day commuters.

    ``assignment`` is the cordon-gates stage's Kreis->gate assignment
    (``ars5, gate_id, inbound, outbound``); it is aggregated per gate by the SAME
    :func:`braunschweig.data.cordon.gate_assignment.gate_volume_summary` that
    ``gate_volumes.csv`` uses, so the two files' unscaled columns are identical by construction.

    ``outbound_at_workplace`` is ``outbound * at_workplace_share``, rounded to whole commuters
    by ``pandas.Series.round`` -- banker's rounding (half-to-even), so an exact .5 goes to the
    nearest EVEN integer rather than always up; the per-gate difference is at most one commuter
    and does not accumulate in one direction across gates;
    ``outbound`` (the register expectation) and ``at_workplace_share_external`` are kept in the
    same row, so the scaled number can never be read without the factor that produced it.
    Columns are :data:`SCALED_GATE_COLUMNS`. INBOUND is deliberately NOT scaled: the in-commuter
    agents are injected from the BA flows by a separate stage and carry no reporting-day state,
    so applying a resident-side share to them would assert something this model does not know.
    """
    if not 0.0 <= float(at_workplace_share) <= 1.0:
        raise ValueError(f"{_LOG_TAG} at_workplace_share must be a share in [0, 1], got "
                         f"{at_workplace_share!r}")
    summary = gate_volume_summary(assignment, value_cols=("inbound", "outbound"))
    summary = summary.copy()
    summary["outbound_at_workplace"] = (summary["outbound"] * float(at_workplace_share)).round()
    summary["outbound_at_workplace"] = summary["outbound_at_workplace"].astype(int)
    summary["at_workplace_share_external"] = float(at_workplace_share)
    return summary[list(SCALED_GATE_COLUMNS)].reset_index(drop=True)


def execute(context):
    if not context.config("cordon_enabled"):
        return None

    incommuters = context.stage("braunschweig.synthesis.incommuters")
    agents = incommuters["validation"]
    # MiD/Mikrozensus target modal split for the "ein" direction, produced by
    # build_incommuter_frames.  Passed to write_cordon_validation so the summary
    # reports realized-vs-target for the in-commuter modal split (CLAUDE.md
    # no-invented-reference-values: the target is derived from the same Mikrozensus
    # reference the mode draw uses, labelled honestly as an aggregate proxy).
    mode_target = incommuters.get("mode_target", None)
    # BA Pendler OD target (per external Kreis, direction "ein"), produced by
    # build_incommuter_frames from the same inbound flow the agents are expanded from.
    # Passed so commuter_validation.csv reports the realized-vs-target per-Kreis deviation
    # (mode-agnostic: BA has no mode). This is a consistency / coverage check, not an
    # independent validation -- see od_deviation_vs_target (issue #134).
    od_target = incommuters.get("od_target", None)
    gate_volume = context.stage("braunschweig.synthesis.cordon_gates")
    out_dir = os.path.join(context.config("output_path"), "analysis", "cordon")

    paths = write_cordon_validation(
        out_dir, agents, sampling_rate=float(context.config("sampling_rate")),
        od_target=od_target, mode_target=mode_target, crs="EPSG:25832")

    gate_paths = write_gate_volumes(
        out_dir, gate_volume["gates"], gate_volume["assignment"], crs="EPSG:25832")
    paths.update(gate_paths)

    # Log per-gate totals so "ein + aus" is visible in the run log.
    total_inbound = int(gate_volume["assignment"]["inbound"].sum())
    total_outbound = int(gate_volume["assignment"]["outbound"].sum())
    print(f"{_LOG_TAG} {len(agents)} in-commuter records -> {out_dir}")
    print(f"{_LOG_TAG} gate volumes (BA SvB gravity expectation): "
          f"inbound (ein) {total_inbound:,} | outbound (aus) {total_outbound:,}")

    if bool(context.config(KEY_COMMUTE_DAY_STATE_ENABLED)):
        states = context.stage(STATE_STAGE)["states"]
        df_work, _df_education = context.stage("synthesis.population.spatial.primary.locations")
        workplaces = context.stage("braunschweig.locations.work")
        scaling_stats = {}
        share = external_at_workplace_share(states, df_work, workplaces, stats=scaling_stats)
        scaled = scaled_gate_volumes(gate_volume["assignment"], share)
        scaled_path = os.path.join(out_dir, "gate_volumes_scaled.csv")
        scaled.to_csv(scaled_path, index=False)
        paths["gate_volumes_scaled_csv"] = scaled_path
        scaling_stats["outbound_register"] = int(scaled["outbound"].sum())
        scaling_stats["outbound_at_workplace"] = int(scaled["outbound_at_workplace"].sum())
        paths["commute_day_state_scaling_json"] = write_json(
            os.path.join(out_dir, SCALING_JSON_NAME), scaling_stats)
        # Both totals are taken from the SAME per-gate table, so the comparison is not
        # blurred by the per-gate integer rounding gate_volume_summary applies.
        total_outbound_register = int(scaled["outbound"].sum())
        total_outbound_scaled = int(scaled["outbound_at_workplace"].sum())
        print(f"{_LOG_TAG} reporting-day outbound expectation (ADR-0104 check 3): "
              f"{total_outbound_scaled:,} of the {total_outbound_register:,} register "
              f"out-commuters (at_workplace share of external workers {share:.4f}) "
              f"-> {scaled_path}")

    return paths
