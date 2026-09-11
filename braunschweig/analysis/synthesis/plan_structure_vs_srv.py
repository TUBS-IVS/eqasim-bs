"""synpp stage: realised daily plan structure of the synthetic population vs SrV 2023.

Measures what issue #369 is about -- 14.1 % of synthetic persons received an EMPTY plan
because their MiD donor reported no diary -- and everything the plan-structure fix changes
around it: mobility rate, trips per person, the trip-count distribution, per-purpose
participation and trip rates, purpose shares, home-based share, tours per mobile person, the
day-closure indicators, work-activity durations and departure-hour profiles.

Both sides are measured by ONE metric implementation: the model frames are harmonised to the
SrV comparison schema by :func:`braunschweig.analysis.plan_structure.harmonise_model` and
then pushed through
:func:`braunschweig.calibration.srv_plan_structure.person_level` /
:func:`~braunschweig.calibration.srv_plan_structure.segment_metrics`, the very functions that
built the committed reference table (CLAUDE.md "Validate metric apples-to-apples"). The
reference is a VALIDATION REFERENCE, never a control target -- no synthesis or location stage
reads it, and this stage writes report files only.

Comparison universe
-------------------
SrV 2023 surveys seven of the eight ZGB Kreise (Wolfsburg 03103 is not surveyed), so the
head-to-head table is restricted to model persons whose HOME Kreis is one of those seven:
comparing an "all" segment that includes Wolfsburg residents against a reference that
excludes them would be an undeclared universe mismatch. The in-scope Kreise are read from the
committed reference itself (its ``kreis_*`` segments) and cross-checked against
``ZGB_KREISE`` minus Wolfsburg; a disagreement raises rather than silently shifting the
comparison universe. Every Kreis outside that set -- Wolfsburg in the canonical
configuration -- is reported MODEL-ONLY in ``by_kreis.csv``, with ``srv`` and ``delta_pp``
left NaN, never a substituted zero. Persons whose home Kreis cannot be resolved at all are
counted, logged and excluded from the head-to-head universe; above
``plan_structure_max_unmatched_home_share`` (default 0.05) the stage RAISES, because a broken
VG250 / household join must not produce a well-formed report that silently describes only part
of the population.

Reporting-day view and the general day-absence draw (issue #370)
------------------------------------------------------------------
``plan_structure_trips_view`` (:data:`KEY_TRIPS_VIEW`, default :data:`DEFAULT_TRIPS_VIEW`
``"final"``) picks which trips stage the model side is harmonised from:

* ``"final"`` -- the REPORTING-DAY view ``synthesis.population.trips.final``, i.e. what MATSim
  actually simulates (commute-day replacement and the general day-absence removal already
  applied, per their own flags). This is the default because a plan-structure comparison
  should describe the day that is run, not an intermediate one.
* ``"pre_assignment"`` -- the pre-assignment view ``synthesis.population.trips``, for a
  comparison that predates ADR-0104 / issue #370 or that deliberately wants the day BEFORE any
  reporting-day replacement.

Independent of the view, ``day_absence_enabled`` (:data:`KEY_DAY_ABSENCE_ENABLED`, same key and
default as every other consumer of :data:`ABSENCE_STAGE`) controls whether the general
day-absence draw's away-from-home persons are marked on the model side at all: only with the
``"final"`` view AND this flag on is :data:`ABSENCE_STAGE` read, and
:func:`braunschweig.analysis.plan_structure.harmonise_model` gets their person ids, so
``away_from_home`` / ``reported_at_home`` reflect the draw instead of the pre-#370 "always at
home" default.

That, in turn, decides what the two SrV universes (``plan_structure_srv_universe``,
:data:`~braunschweig.calibration.srv_plan_structure.UNIVERSE_AT_HOME_ZERO` /
:data:`~braunschweig.calibration.srv_plan_structure.UNIVERSE_AT_HOME_ONLY`) actually compare:

* ``at_home_zero`` (default) -- every model person counts, an away-from-home person as a
  zero-trip person (exactly the pre-#370 behaviour, since ``synthesis.population.trips.final``
  already has no trips for an absent person). Comparing this universe WITHOUT the day-absence
  model switched on is a silent universe mismatch -- the model then has no away-from-home
  concept at all while the reference still counts its away respondents as zero-trip -- so the
  stage logs a WARNING naming both sides.
* ``at_home_only`` -- away-from-home persons are EXCLUDED from the model side entirely, before
  :func:`~braunschweig.calibration.srv_plan_structure.person_level`, mirroring exactly how the
  reference's own ``at_home_only`` universe is built
  (:func:`~braunschweig.calibration.srv_plan_structure.build_reference`). The excluded count is
  recorded as ``n_persons_excluded_away`` in ``provenance.json``, never silently absorbed.

``provenance.json`` and ``summary.md`` always record ``trips_view``, ``day_absence_enabled``
and ``n_persons_away_from_home`` (the away-from-home count on the model side BEFORE any
``at_home_only`` exclusion), regardless of universe, so a reader can tell which reporting-day
view and which absence state the numbers describe.

Outputs, under ``<output_path>/<plan_structure_output_subdir>/``:

* ``comparison.csv`` -- the full long comparison (segment, metric, model, srv, delta_pp,
  n_srv) over the head-to-head universe
* ``headline.csv`` -- the ``all`` segment of the same table
* ``by_kreis.csv`` -- the ``kreis_*`` rows, head-to-head Kreise plus the model-only ones
* ``closure.csv`` -- the synthesised home-closure rates (a modelling assumption, reported as
  an explicit rate). Measured over the WHOLE model trip table, not restricted to the
  head-to-head Kreise: the closure is a property of the trip builder, not of a region.
* ``accepted_deviations.csv`` -- the three consequences of the closed-plan decision
* ``summary.md`` and ``provenance.json``

The stage reads cached synthesis stages only (no MATSim), so it runs on a cached population.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import inspect
import json
import logging
import os

import numpy as np
import pandas as pd

from braunschweig import provenance as run_provenance
from braunschweig.analysis import plan_structure as P
from braunschweig.calibration import srv_distance_targets as T
from braunschweig.calibration import srv_plan_structure as SRV
from braunschweig.synthesis.day_absence import absence as _day_absence
from braunschweig.synthesis.day_absence.absence import absent_person_ids

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.plan_structure_vs_srv")

_LOG_TAG = "[plan_structure_vs_srv]"

KEY_SUBDIR = "plan_structure_output_subdir"
KEY_UNIVERSE = "plan_structure_srv_universe"
KEY_MAX_UNMATCHED_HOME_SHARE = "plan_structure_max_unmatched_home_share"
#: Which trips stage the model side is harmonised from (issue #370, Task 6): the REPORTING-DAY
#: view (default) or the pre-assignment one. See the module docstring section "Reporting-day
#: view and the general day-absence draw".
KEY_TRIPS_VIEW = "plan_structure_trips_view"
DEFAULT_TRIPS_VIEW = "final"
#: Whether the general day-absence draw's away-from-home persons are marked on the model side.
#: Same key and default as every other consumer of :data:`ABSENCE_STAGE`
#: (``braunschweig.synthesis.day_absence.absence_stage.KEY_ENABLED``,
#: ``braunschweig.synthesis.commute_day.trips_day_stage.KEY_DAY_ABSENCE_ENABLED``,
#: ``braunschweig.synthesis.commute_day.output_day.KEY_DAY_ABSENCE_ENABLED``): reusing the
#: identical name lets ONE config value gate all of them together.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
#: The general day-absence synpp stage this module reads when the ``"final"`` view AND
#: :data:`KEY_DAY_ABSENCE_ENABLED` are both on.
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"

DEFAULT_SUBDIR = "analysis/plan_structure_vs_srv"
#: Above this share of persons whose home point resolves to no Kreis the stage RAISES; 5%
#: mirrors ``cds_max_unmatched_home_share`` / ``srv_distance_max_unmatched_home_share`` in the
#: sibling per-Kreis stages. A high unmatched rate almost always means a broken VG250 /
#: household join (stale archive, wrong CRS) rather than genuinely home-less persons, and the
#: head-to-head universe would then silently exclude that share of the population while the
#: report still looked well-formed.
DEFAULT_MAX_UNMATCHED_HOME_SHARE = 0.05
#: The reference universe that matches a synthetic population: every person exists and
#: starts the day at home, so an away-from-home reporting day counts as 0 trips.
DEFAULT_UNIVERSE = SRV.UNIVERSE_AT_HOME_ZERO

#: Directory of the committed SrV tables below ``data_path`` (same layout as
#: ``braunschweig.analysis.reference.srv.commute_distance``).
SRV_SUBDIR = ("braunschweig", "srv")

#: The Kreise SrV 2023 surveys: the ZGB minus Wolfsburg, which SrV does not cover.
EXPECTED_SRV_KREISE = tuple(sorted(set(T.ZGB_KREISE) - {T.WOLFSBURG_KREIS}))

KREIS_SEGMENT_PREFIX = "kreis_"

#: Repository root, used for the git commit recorded in ``provenance.json``
#: (this file sits at <root>/braunschweig/analysis/synthesis/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

#: synpp hashes only THIS module's source, so the helper modules that define the
#: harmonisation and every metric must be folded into the validation token; without them an
#: edit to a metric would silently serve a cached comparison built by the old code.
#: ``_day_absence`` is folded in because this module directly imports
#: :func:`~braunschweig.synthesis.day_absence.absence.absent_person_ids` (issue #370, Task 6):
#: the :data:`ABSENCE_STAGE` dependency already invalidates this stage's synpp cache when the
#: absence stage's OWN output changes, but hashing the pure helper here too costs nothing and
#: keeps "over-hash rather than under-hash" uniform across every direct import in this file.
_HELPER_MODULES = (P, SRV, _day_absence)
#: Imported inside validate() rather than at module level, exactly as execute() does: a
#: top-level import of braunschweig.analysis.spatial pulls geopandas and the VG250 access into
#: every import of this stage. Its ``assign_geographies`` decides EVERY person's home Kreis and
#: therefore the head-to-head universe and all per-Kreis rows, so a change there must invalidate
#: this stage's cache.
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.analysis.spatial",)


def validate(context):
    """synpp validation token: md5 over this stage's helper modules.

    Same mechanism and boundary semantics as ``braunschweig.popsim.trips_stage.validate()``.
    Over-hashing only costs a re-run of a minutes-long analysis stage, while under-hashing
    silently reports a stale comparison as a current result. A deferred module that fails to
    import raises rather than being skipped -- skipping it would keep the stale cache alive
    exactly when the code is broken.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_module = importlib.import_module(module_name)
            deferred_source = inspect.getsource(deferred_module)
        except Exception as error:
            raise RuntimeError(
                f"plan_structure_vs_srv validate(): cannot hash the deferred helper module "
                f"{module_name!r} ({type(error).__name__}: {error}); it must not be skipped, "
                "because skipping it would silently reuse a stale cached comparison."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    context.stage("synthesis.population.enriched")
    # issue #370, Task 6: KEY_TRIPS_VIEW picks the reporting-day view ("final", default) or the
    # pre-assignment one, aliased to the LOCAL name "trips" so execute() need not branch on the
    # view again. Only the "final" view carries the general day-absence removal, so the absence
    # stage is declared (and later read) ONLY when both it and the flag are on -- a run using the
    # pre-assignment view, or with the flag off, must not pull the absence stage into its DAG.
    trips_view = context.config(KEY_TRIPS_VIEW, DEFAULT_TRIPS_VIEW)
    if trips_view == "final":
        context.stage("synthesis.population.trips.final", alias="trips")
    elif trips_view == "pre_assignment":
        context.stage("synthesis.population.trips", alias="trips")
    else:
        raise ValueError(f"{_LOG_TAG} {KEY_TRIPS_VIEW} must be 'final' or 'pre_assignment', "
                         f"got {trips_view!r}")
    absence_on = bool(context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED))
    if trips_view == "final" and absence_on:
        context.stage(ABSENCE_STAGE)
    context.stage("synthesis.population.spatial.home.locations")
    context.config("output_path")
    context.config("data_path")
    context.config("sampling_rate")
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_UNIVERSE, DEFAULT_UNIVERSE)
    context.config(KEY_MAX_UNMATCHED_HOME_SHARE, DEFAULT_MAX_UNMATCHED_HOME_SHARE)


# --------------------------------------------------------------------------- reference

def srv_directory(data_path: str) -> str:
    """Directory of the committed SrV tables below ``data_path``."""
    return os.path.join(str(data_path), *SRV_SUBDIR)


def reference_path(data_path: str) -> str:
    """Absolute path of the committed SrV plan-structure reference table."""
    return os.path.join(srv_directory(data_path), SRV.PLAN_STRUCTURE_TABLE)


def load_reference(data_path: str, universe: str) -> pd.DataFrame:
    """Load the committed reference and restrict it to one universe.

    Reading and the missing-file guard are delegated to
    :func:`braunschweig.calibration.srv_plan_structure.load_plan_structure_reference`, so the
    stage and the reference builder share one statement of where the table lives and how a
    missing one is reported: it raises ``FileNotFoundError`` naming the path and the
    regeneration script (a missing reference must abort the stage, never degrade it to a
    model-only report that would read like a measured result). ``ValueError`` is raised here
    for an unknown or empty universe.
    """
    path = reference_path(data_path)
    if universe not in SRV.UNIVERSES:
        raise ValueError("unknown plan-structure universe %r; expected one of %s"
                         % (universe, list(SRV.UNIVERSES)))
    table = SRV.load_plan_structure_reference(srv_directory(data_path))
    selected = table[table["universe"] == universe]
    if len(selected) == 0:
        raise ValueError(
            "the committed reference %s carries no rows for universe %r (present: %s)"
            % (path, universe, sorted(table["universe"].unique().tolist())))
    LOGGER.info("%s reference: %d/%d rows for universe '%s' from %s", _LOG_TAG,
                len(selected), len(table), universe, path)
    return selected


def reference_kreise(reference: pd.DataFrame) -> tuple:
    """The Kreis codes the reference actually carries, cross-checked against the ZGB set.

    The reference table is the truth about which Kreise CAN be compared. A disagreement with
    :data:`EXPECTED_SRV_KREISE` means the committed table changed shape, which would silently
    change the head-to-head universe -- so it raises instead of being logged and ignored.
    """
    segments = reference["segment"].astype(str)
    codes = tuple(sorted(
        segment[len(KREIS_SEGMENT_PREFIX):]
        for segment in segments[segments.str.startswith(KREIS_SEGMENT_PREFIX)].unique()))
    if codes != EXPECTED_SRV_KREISE:
        raise ValueError(
            "the committed plan-structure reference carries the Kreis segments %s, but the "
            "SrV-surveyed Kreise are %s (ZGB_KREISE minus Wolfsburg %s); the head-to-head "
            "comparison universe is defined by that set, so a change must be reviewed, not "
            "absorbed silently"
            % (list(codes), list(EXPECTED_SRV_KREISE), T.WOLFSBURG_KREIS))
    return codes


def guard_unmatched_home_share(n_unmatched, n_total, max_unmatched_home_share) -> float:
    """Log the unmatched-home rate and RAISE above ``max_unmatched_home_share``.

    Same shape (and default) as ``_guard_unmatched_home_share`` in the sibling stage
    ``braunschweig.analysis.synthesis.work_participation_by_kreis``. A person whose home point
    resolves to no Kreis is excluded from the head-to-head universe; a high share of such
    persons almost always signals a broken VG250 / household join (stale archive, wrong CRS)
    rather than genuinely home-less persons, and the report would then still LOOK well-formed
    while silently describing only part of the population (CLAUDE.md "Fallback transparency":
    a high fallback rate is a failure signal, not a footnote). Returns the rate.
    """
    rate = float(n_unmatched) / float(n_total) if n_total else 0.0
    LOGGER.info("%s home Kreis: %d/%d persons (%.2f%%) have no ars5 match", _LOG_TAG,
                n_unmatched, n_total, 100.0 * rate)
    if rate > max_unmatched_home_share:
        raise ValueError(
            f"{n_unmatched}/{n_total} ({100.0 * rate:.1f}%) persons have no home Kreis match; "
            f"exceeds {KEY_MAX_UNMATCHED_HOME_SHARE}={max_unmatched_home_share} -- the "
            f"head-to-head comparison universe would silently exclude that share of the "
            f"population; check the VG250 archive and the "
            f"synthesis.population.spatial.home.locations / household_id join")
    return rate


# --------------------------------------------------------------------------- report

def _fmt(value, decimals=4) -> str:
    """Format a number for ``summary.md``; NaN renders as the explicit "n/a"."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{value:.{decimals}f}"


def _metric_table(rows: pd.DataFrame, unit_column="delta") -> list:
    """Markdown table body for a (segment-restricted) slice of the comparison."""
    lines = [f"| metric | model | srv | {unit_column} | n_srv |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for _, row in rows.iterrows():
        n_srv = "n/a" if pd.isna(row["n_srv"]) else f"{int(row['n_srv'])}"
        lines.append(f"| {row['metric']} | {_fmt(row['model'])} | {_fmt(row['srv'])} | "
                     f"{_fmt(row['delta_pp'], 2)} | {n_srv} |")
    return lines


def summary_markdown(comparison, by_kreis, closure, deviations, provenance) -> str:
    """The human-readable report; every number in it also exists in a CSV next to it."""
    parameters = provenance.get("parameters", {})
    inputs = provenance.get("inputs", {})
    model = provenance.get("model", {})
    lines = [
        "# Plan structure: synthetic population vs SrV 2023",
        "",
        "VALIDATION REFERENCE, not a calibration target: no synthesis or location stage "
        "reads the SrV plan-structure table, and this stage writes report files only.",
        "",
        "Parameters: " + ", ".join(f"{key}={value}" for key, value in parameters.items()),
        f"Generated at: {provenance.get('generated_at')}",
        f"Pipeline commit: {provenance.get('pipeline_commit')}",
        f"Reference: {provenance.get('inputs', {}).get('reference_path')}",
        "",
        "## Comparison universe",
        "",
        f"Reporting-day trips view: {parameters.get('trips_view')} "
        f"(day_absence_enabled={parameters.get('day_absence_enabled')}); "
        f"{model.get('n_persons_away_from_home')} model person(s) marked away from home by "
        f"the general day-absence draw, {model.get('n_persons_excluded_away')} of them "
        f"excluded from the model side under universe '{parameters.get('srv_universe')}'.",
        f"Head-to-head Kreise (SrV-surveyed): {', '.join(inputs.get('kreise_in_scope', []))}.",
        f"Model persons in scope: {model.get('n_persons_in_scope')} of "
        f"{model.get('n_persons_total')} "
        f"({_fmt(model.get('share_persons_in_scope'), 4)}); "
        f"{model.get('n_persons_out_of_scope')} person(s) live in a Kreis SrV does not survey "
        f"({', '.join(model.get('kreise_model_only', [])) or 'none'}, reported model-only in "
        f"by_kreis.csv) and {model.get('n_persons_no_kreis')} "
        f"({_fmt(model.get('share_persons_no_kreis'), 4)}) have no resolvable home Kreis "
        f"(the stage raises above "
        f"{parameters.get('max_unmatched_home_share')}).",
        "",
        "Deltas: percentage points for share metrics (mobility_rate, share_*, "
        "participation_*, purpose_share_*, dep_hour_share_*), a plain difference otherwise. "
        "The n_persons_* and n_work_activities_measured rows are the two sides' sample "
        "sizes, not a gap: SrV is an expanded survey of 18,223 respondents, the model side a "
        "(possibly sampled) synthetic population.",
        "",
        "The dep_hour_share_* rows compare de-rounded model times with as-reported SrV times "
        "at hour level and are biased at the hour boundary; the unbiased 15-minute comparison "
        "lives in analysis/departure_time_vs_srv/.",
        "",
        "## Headline (segment 'all')",
        "",
    ]
    headline = comparison[comparison["segment"] == "all"]
    ordered = [headline[headline["metric"] == metric] for metric in P.HEADLINE_METRICS]
    ordered = pd.concat([frame for frame in ordered if len(frame)], ignore_index=True) \
        if any(len(frame) for frame in ordered) else headline.iloc[0:0]
    lines += _metric_table(ordered)

    lines += ["", "## Accepted deviations (closed plans by decision)", "",
              "Every synthetic day is a closed home-based chain by construction (issue "
              "#367), so the model cannot reproduce the SrV shares below. They are accepted "
              "deviations, not defects.", "",
              "| deviation | model | srv | delta_pp | note |",
              "| --- | ---: | ---: | ---: | --- |"]
    for _, row in deviations.iterrows():
        lines.append(f"| {row['deviation']} | {_fmt(row['model'])} | {_fmt(row['srv'])} | "
                     f"{_fmt(row['delta_pp'], 2)} | {row['note']} |")

    lines += ["", "## Synthetic home closure (modelling assumption)", "",
              "Measured over the whole model trip table (every Kreis), not only the "
              "head-to-head universe: the closure is a property of the trip builder.", "",
              "| metric | value |", "| --- | ---: |"]
    for _, row in closure.iterrows():
        lines.append(f"| {row['metric']} | {_fmt(row['value'])} |")

    kreis_metrics = ("mobility_rate", "trips_per_person", "share_mobile_last_to_home")
    lines += ["", "## By home Kreis", "",
              "A model-only Kreis (not surveyed by SrV) carries n/a for srv and delta.", ""]
    for metric in kreis_metrics:
        lines += [f"### {metric}", "",
                  "| segment | model | srv | delta | n_srv |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        rows = by_kreis[by_kreis["metric"] == metric].sort_values("segment")
        for _, row in rows.iterrows():
            n_srv = "n/a" if pd.isna(row["n_srv"]) else f"{int(row['n_srv'])}"
            lines.append(f"| {row['segment']} | {_fmt(row['model'])} | {_fmt(row['srv'])} | "
                         f"{_fmt(row['delta_pp'], 2)} | {n_srv} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_outputs(directory, comparison, headline, by_kreis, closure, deviations,
                  provenance) -> None:
    """Write the seven report artifacts into ``directory`` (created if absent).

    The CSVs stay header-free (no provenance comment lines) so a consumer can read them with
    a plain ``pandas.read_csv``; the parameter block lives in ``provenance.json`` and at the
    top of ``summary.md``.
    """
    os.makedirs(directory, exist_ok=True)
    comparison.to_csv(os.path.join(directory, "comparison.csv"), index=False)
    headline.to_csv(os.path.join(directory, "headline.csv"), index=False)
    by_kreis.to_csv(os.path.join(directory, "by_kreis.csv"), index=False)
    closure.to_csv(os.path.join(directory, "closure.csv"), index=False)
    deviations.to_csv(os.path.join(directory, "accepted_deviations.csv"), index=False)
    with open(os.path.join(directory, "provenance.json"), "w", encoding="utf-8") as handle:
        json.dump(provenance, handle, indent=2)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(summary_markdown(comparison, by_kreis, closure, deviations, provenance))


# --------------------------------------------------------------------------- stage

def execute(context):
    # VG250 access only at run time (the module import pulls geopandas + the archive).
    from braunschweig.analysis import spatial

    universe = context.config(KEY_UNIVERSE)
    subdir = context.config(KEY_SUBDIR)
    data_path = context.config("data_path")
    output_path = context.config("output_path")
    sampling_rate = float(context.config("sampling_rate"))
    max_unmatched_home_share = float(context.config(KEY_MAX_UNMATCHED_HOME_SHARE))
    trips_view = context.config(KEY_TRIPS_VIEW)
    absence_on = bool(context.config(KEY_DAY_ABSENCE_ENABLED))
    # The absence draw is actually consulted only for the "final" (reporting-day) view AND with
    # the flag on -- the same condition configure() used to decide whether to declare
    # ABSENCE_STAGE at all. The pre_assignment view has no away-from-home information on the
    # model side REGARDLESS of the flag, so this, not the raw flag, is what "the model has no
    # away-from-home state" actually means below.
    absence_applied = trips_view == "final" and absence_on
    out_dir = os.path.join(output_path, subdir)

    # The reference is loaded FIRST: a missing or unusable reference must abort before any
    # expensive geometry or metric work is done.
    reference = load_reference(data_path, universe)
    kreise_in_scope = reference_kreise(reference)

    # issue #370: at_home_zero counts an away-from-home person as a zero-trip person, which
    # matches the reporting-day trips view (an absent person already has no trip there) ONLY
    # when the day-absence draw was actually applied. Without it the model has no away-from-home
    # concept at all, so this universe pairing silently compares two different definitions of
    # the reporting day unless flagged (CLAUDE.md "Fallback transparency").
    if universe == SRV.UNIVERSE_AT_HOME_ZERO and not absence_applied:
        LOGGER.warning(
            "%s universe=%r without the day-absence draw applied (%s=%s, trips_view=%s): the "
            "model side has no away-from-home state at all, while the reference universe "
            "counts an away-from-home person as a zero-trip person -- compare against "
            "universe=%r or enable %s on the 'final' trips view for a like-for-like universe",
            _LOG_TAG, universe, KEY_DAY_ABSENCE_ENABLED, absence_on, trips_view,
            SRV.UNIVERSE_AT_HOME_ONLY, KEY_DAY_ABSENCE_ENABLED)

    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("trips")
    df_home = context.stage("synthesis.population.spatial.home.locations")

    # ABSENCE_STAGE was declared in configure() iff absence_applied -- fetching it otherwise
    # would be an undeclared-stage error in real synpp.
    absent_ids = absent_person_ids(context.stage(ABSENCE_STAGE)["absence"]) \
        if absence_applied else None

    LOGGER.info("%s parameters: universe=%s, sampling_rate=%.4f, "
                "max_unmatched_home_share=%.3f, trips_view=%s, day_absence_enabled=%s, "
                "kreise_in_scope=%s; writing to %s", _LOG_TAG,
                universe, sampling_rate, max_unmatched_home_share, trips_view, absence_on,
                list(kreise_in_scope), out_dir)

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    persons, trips = P.harmonise_model(df_persons, df_trips, homes,
                                       absent_person_ids=absent_ids)
    n_persons_away_from_home = int(persons["away_from_home"].sum())

    # issue #370: at_home_only additionally EXCLUDES away-from-home persons from the model side,
    # mirroring exactly how the reference's own at_home_only universe is built
    # (SRV.build_reference: `persons[persons["reported_at_home"]]`). Trips are restricted to the
    # same persons afterwards -- SRV.person_level raises on a trip whose person is not in the
    # person frame.
    n_persons_excluded_away = 0
    if universe == SRV.UNIVERSE_AT_HOME_ONLY:
        n_before_exclusion = len(persons)
        persons = persons[~persons["away_from_home"]].reset_index(drop=True)
        trips = trips[trips["pid"].isin(set(persons["pid"]))].reset_index(drop=True)
        n_persons_excluded_away = n_before_exclusion - len(persons)
        LOGGER.info(
            "%s universe %s: excluded %d/%d away-from-home persons (%.2f%%) from the model "
            "side before SRV.person_level", _LOG_TAG, universe, n_persons_excluded_away,
            n_before_exclusion,
            100.0 * n_persons_excluded_away / n_before_exclusion if n_before_exclusion
            else float("nan"))

    per = SRV.person_level(persons, trips)
    # ONE string view of the home Kreis, used for every comparison below: the reference's
    # segment codes are strings, so an object/str dtype mismatch on either side of the
    # membership test would silently empty the head-to-head universe. pandas' nullable
    # "string" dtype keeps a missing Kreis as <NA> instead of turning it into the literal
    # "nan" that a bare ``astype(str)`` would produce.
    kreis_code = per["kreis"].astype("string")
    per_in_scope = per[kreis_code.isin(kreise_in_scope)]
    model_kreise = sorted(kreis_code.dropna().unique().tolist())
    kreise_model_only = [code for code in model_kreise if code not in kreise_in_scope]
    n_persons_total = int(len(per))
    n_persons_in_scope = int(len(per_in_scope))
    n_persons_no_kreis = int(kreis_code.isna().sum())
    n_persons_out_of_scope = n_persons_total - n_persons_in_scope - n_persons_no_kreis
    unmatched_home_share = guard_unmatched_home_share(
        n_persons_no_kreis, n_persons_total, max_unmatched_home_share)
    LOGGER.info("%s head-to-head universe: %d/%d persons (%.2f%%) live in the %d "
                "SrV-surveyed Kreise; %d live in a model-only Kreis (%s), %d have no "
                "resolvable home Kreis", _LOG_TAG, n_persons_in_scope, n_persons_total,
                100.0 * n_persons_in_scope / n_persons_total if n_persons_total else
                float("nan"), len(kreise_in_scope), n_persons_out_of_scope,
                ", ".join(kreise_model_only) or "none", n_persons_no_kreis)

    model_long = P.model_structure(per_in_scope, trips, SRV.segment_frames(per_in_scope))
    comparison = P.compare(model_long, reference)

    # Kreise SrV does not survey: measured on the model side alone and appended to the
    # per-Kreis table with a NaN reference, never compared against a substituted value.
    model_only_long = P.model_structure(
        per, trips,
        [(KREIS_SEGMENT_PREFIX + code, per[kreis_code == code])
         for code in kreise_model_only])
    model_only = model_only_long.rename(columns={"value": "model"})
    model_only["srv"] = float("nan")
    model_only["delta_pp"] = float("nan")
    model_only["n_srv"] = float("nan")

    headline = comparison[comparison["segment"] == "all"].reset_index(drop=True)
    kreis_rows = comparison[comparison["segment"].str.startswith(KREIS_SEGMENT_PREFIX)]
    by_kreis = (pd.concat([kreis_rows, model_only[P.COMPARISON_COLUMNS]], ignore_index=True)
                .sort_values(["segment", "metric"]).reset_index(drop=True))

    closure = P.closure_metrics(df_trips)
    closure_table = P.closure_frame(closure)
    deviations = P.accepted_deviations(comparison)

    provenance = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {
            "srv_universe": universe,
            "output_subdir": subdir,
            "sampling_rate": sampling_rate,
            "max_unmatched_home_share": max_unmatched_home_share,
            # issue #370, Task 6: which reporting-day view fed the model side and whether the
            # general day-absence draw's away-from-home persons were marked on it.
            "trips_view": trips_view,
            "day_absence_enabled": absence_on,
        },
        "inputs": {
            "reference_path": reference_path(data_path),
            "n_reference_rows": int(len(reference)),
            "kreise_in_scope": list(kreise_in_scope),
        },
        "model": {
            # n_persons_total is measured on `per` (SRV.person_level's output), i.e. AFTER any
            # at_home_only exclusion above -- the opposite timing of n_persons_away_from_home
            # below, which is measured BEFORE that exclusion (final-review fix wave, minor
            # polish: made explicit here, not only in the module docstring).
            "n_persons_total": n_persons_total,
            "n_persons_in_scope": n_persons_in_scope,
            "share_persons_in_scope": (n_persons_in_scope / n_persons_total
                                       if n_persons_total else float("nan")),
            "n_persons_out_of_scope": n_persons_out_of_scope,
            "n_persons_no_kreis": n_persons_no_kreis,
            "share_persons_no_kreis": unmatched_home_share,
            # issue #370, Task 6: away-from-home count BEFORE any at_home_only exclusion, and
            # how many of them that exclusion then dropped (0 under at_home_zero).
            "n_persons_away_from_home": n_persons_away_from_home,
            "n_persons_excluded_away": n_persons_excluded_away,
            "n_trips_total": int(len(trips)),
            "n_trips_in_scope": int(trips["pid"].isin(set(per_in_scope["pid"])).sum()),
            # The in-scope Kreise are an INPUT fact (they come from the reference table) and
            # are recorded once, under "inputs"; only the model-only ones belong here.
            "kreise_model_only": kreise_model_only,
        },
        "closure": closure,
    }
    write_outputs(out_dir, comparison, headline, by_kreis, closure_table, deviations,
                  provenance)

    indexed = headline.set_index("metric")
    for metric in P.HEADLINE_METRICS:
        if metric not in indexed.index:
            continue
        row = indexed.loc[metric]
        unit = "pp" if P.is_share_metric(metric) else "abs"
        LOGGER.info("%s headline %s: model=%s srv=%s delta=%s %s", _LOG_TAG, metric,
                    _fmt(row["model"]), _fmt(row["srv"]), _fmt(row["delta_pp"], 2), unit)
    for _, row in deviations.iterrows():
        LOGGER.info("%s accepted deviation %s: model=%s srv=%s (%s)", _LOG_TAG,
                    row["deviation"], _fmt(row["model"]), _fmt(row["srv"]), row["note"])

    return dict(comparison=comparison, headline=headline, by_kreis=by_kreis,
                closure=closure_table, accepted_deviations=deviations)
