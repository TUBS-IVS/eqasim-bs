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
counted, logged and excluded from the head-to-head universe.

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

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.plan_structure_vs_srv")

_LOG_TAG = "[plan_structure_vs_srv]"

KEY_SUBDIR = "plan_structure_output_subdir"
KEY_UNIVERSE = "plan_structure_srv_universe"

DEFAULT_SUBDIR = "analysis/plan_structure_vs_srv"
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

#: synpp hashes only THIS module's source, so the two helper modules that define the
#: harmonisation and every metric must be folded into the validation token; without them an
#: edit to a metric would silently serve a cached comparison built by the old code.
_HELPER_MODULES = (P, SRV)


def validate(context):
    """synpp validation token: md5 over this stage's helper modules.

    Same mechanism as ``braunschweig.popsim.trips_stage.validate()``. Over-hashing only
    costs a re-run of a minutes-long analysis stage, while under-hashing silently reports a
    stale comparison as a current result.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.spatial.home.locations")
    context.config("output_path")
    context.config("data_path")
    context.config("sampling_rate")
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_UNIVERSE, DEFAULT_UNIVERSE)


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
        f"Head-to-head Kreise (SrV-surveyed): {', '.join(inputs.get('kreise_in_scope', []))}.",
        f"Model persons in scope: {model.get('n_persons_in_scope')} of "
        f"{model.get('n_persons_total')} "
        f"({_fmt(model.get('share_persons_in_scope'), 4)}); "
        f"{model.get('n_persons_out_of_scope')} person(s) live in a Kreis SrV does not survey "
        f"({', '.join(model.get('kreise_model_only', [])) or 'none'}, reported model-only in "
        f"by_kreis.csv) and {model.get('n_persons_no_kreis')} have no resolvable home Kreis.",
        "",
        "Deltas: percentage points for share metrics (mobility_rate, share_*, "
        "participation_*, purpose_share_*, dep_hour_share_*), a plain difference otherwise. "
        "The n_persons_* and n_work_activities_measured rows are the two sides' sample "
        "sizes, not a gap: SrV is an expanded survey of 18,223 respondents, the model side a "
        "(possibly sampled) synthetic population.",
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
    out_dir = os.path.join(output_path, subdir)

    # The reference is loaded FIRST: a missing or unusable reference must abort before any
    # expensive geometry or metric work is done.
    reference = load_reference(data_path, universe)
    kreise_in_scope = reference_kreise(reference)

    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    df_home = context.stage("synthesis.population.spatial.home.locations")
    LOGGER.info("%s parameters: universe=%s, sampling_rate=%.4f, kreise_in_scope=%s; "
                "writing to %s", _LOG_TAG, universe, sampling_rate, list(kreise_in_scope),
                out_dir)

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    persons, trips = P.harmonise_model(df_persons, df_trips, homes)

    per = SRV.person_level(persons, trips)
    in_scope_mask = per["kreis"].isin(kreise_in_scope)
    per_in_scope = per[in_scope_mask]
    model_kreise = sorted(per["kreis"].dropna().astype(str).unique().tolist())
    kreise_model_only = [code for code in model_kreise if code not in kreise_in_scope]
    n_persons_total = int(len(per))
    n_persons_in_scope = int(len(per_in_scope))
    n_persons_no_kreis = int(per["kreis"].isna().sum())
    n_persons_out_of_scope = n_persons_total - n_persons_in_scope - n_persons_no_kreis
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
        [(KREIS_SEGMENT_PREFIX + code, per[per["kreis"] == code])
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
        },
        "inputs": {
            "reference_path": reference_path(data_path),
            "n_reference_rows": int(len(reference)),
            "kreise_in_scope": list(kreise_in_scope),
        },
        "model": {
            "n_persons_total": n_persons_total,
            "n_persons_in_scope": n_persons_in_scope,
            "share_persons_in_scope": (n_persons_in_scope / n_persons_total
                                       if n_persons_total else float("nan")),
            "n_persons_out_of_scope": n_persons_out_of_scope,
            "n_persons_no_kreis": n_persons_no_kreis,
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
