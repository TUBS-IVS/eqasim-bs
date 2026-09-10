"""synpp stage: realised departure times of the synthetic population vs SrV 2023 (issue #123).

What the departure-time model (:mod:`braunschweig.popsim.departure_time_model`, ADR-0114) does to
a synthetic day is invisible in an aggregate mode split, so this stage measures it directly and
writes a report next to the population. Three things, in this order:

1. **Decomposition.** Every trips row carries the offset the model applied
   (:data:`~braunschweig.popsim.departure_time_model.OFFSET_COLUMN`), so a realised departure
   splits into ``raw`` (the donor's reported MiD clock time, ``W_SZS``/``W_SZM``), ``pre_offset``
   (``realised - offset``) and ``realised`` (what MATSim simulates). ``decomposition.csv`` puts
   the three hourly profiles side by side per ``(segment, purpose, position)``.
2. **The 15-minute comparison.** ``comparison.csv`` / ``emd.csv`` score the model's realised
   departure distribution against BOTH columns of the committed SrV reference -- the de-rounded
   one (the behaviour) and the as-reported one (the survey's clock grid) -- with
   :func:`braunschweig.calibration.metrics.emd_on_bands`. This is the unbiased comparison the
   hour-level ``dep_hour_share_*`` rows of
   :mod:`braunschweig.analysis.synthesis.plan_structure_vs_srv` cannot give.
3. **Hold-out.** ``srv_mapped`` maps the FIRST departure of a chain and nothing else, so every
   EMD row is labelled ``calibrated`` (position ``first``) or ``holdout`` (positions ``later`` /
   ``all``), and ``activity_duration.csv`` compares a dimension no departure-time model touches
   at all: the duration of the activity following each leg.

A VALIDATION REFERENCE, never a control target for this stage: it reads the committed tables and
writes report files only. (The ``srv_mapped`` model does read the ``position == "first"`` rows of
the departure-time table as a mapping target -- that is exactly why this stage labels that
position ``calibrated`` and reports the hold-out dimensions next to it.)

Raw-time coverage
-----------------
On the REPORTING-DAY view a spliced home-office chain has no raw MiD columns at all:
:mod:`braunschweig.synthesis.commute_day.plan_replacement` nulls a replaced row's extra columns
and recomputes only :data:`OFFSET_COLUMN`. ``share_raw`` is therefore NaN-safe by construction,
the coverage ``n_raw / n`` is logged and recorded in ``provenance.json``, and a coverage below
:data:`~braunschweig.analysis.departure_time.RAW_COVERAGE_WARN_THRESHOLD` is WARNED about --
never silently absorbed (CLAUDE.md "Fallback transparency"). A trips frame WITHOUT
:data:`OFFSET_COLUMN` is refused outright: without it the realised time cannot be decomposed at
all, and a report that silently dropped the decomposition would read like a measured result.

Comparison universe (a stated limitation)
-----------------------------------------
Unlike the plan-structure stage this one does NOT segment by home Kreis, so it needs no VG250
join. The consequence is stated rather than assumed away: SrV 2023 surveys seven of the eight ZGB
Kreise (Wolfsburg 03103 is not surveyed), so the model side covers a slightly wider population
than the reference. Departure-time PROFILES are shape comparisons, for which that difference is
far smaller than for a level metric, but ``summary.md`` records it as a limitation.

Outputs, under ``<output_path>/<departure_time_output_subdir>/``: ``decomposition.csv``,
``comparison.csv``, ``emd.csv``, ``activity_duration.csv``, ``summary.md``, ``provenance.json``.

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
from braunschweig.analysis import departure_time as D
from braunschweig.analysis import plan_structure as _plan_structure
from braunschweig.calibration import srv_departure_times as SRVDT
from braunschweig.calibration import srv_plan_structure as _srv_plan_structure
from braunschweig.popsim import departure_time_model as _departure_time_model
from braunschweig.popsim.departure_time_model import (
    OFFSET_COLUMN, person_groups, persons_from_synthetic_schema)
from braunschweig.popsim.stage import config_keys as _config_keys

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.departure_time_vs_srv")

_LOG_TAG = "[departure_time_vs_srv]"

KEY_SUBDIR = "departure_time_output_subdir"
DEFAULT_SUBDIR = "analysis/departure_time_vs_srv"

#: Which trips stage the model side is read from -- the same two views (and the same defaults)
#: as ``plan_structure_vs_srv.KEY_TRIPS_VIEW``, under this stage's own key so the two reports can
#: be produced for different views in one run.
KEY_TRIPS_VIEW = "departure_time_trips_view"
DEFAULT_TRIPS_VIEW = "final"

#: The departure-time model a run used. Re-exported from
#: ``braunschweig.popsim.stage.config_keys`` rather than re-typed: this stage only RECORDS the
#: value in its report (a comparison of two runs is meaningless without it), so it must read the
#: same key, with the same default, as the stage that actually applies the model.
KEY_DEPARTURE_TIME_MODEL = _config_keys.KEY_DEPARTURE_TIME_MODEL
DEFAULT_DEPARTURE_TIME_MODEL = _config_keys.DEFAULT_DEPARTURE_TIME_MODEL

#: Directory of the committed SrV tables below ``data_path`` (same layout as the sibling
#: comparison stages).
SRV_SUBDIR = ("braunschweig", "srv")

#: The bins of one clock hour, used to aggregate the committed 15-minute reference to the hourly
#: resolution of the summary headline.
BINS_PER_HOUR = int(round(60 / SRVDT.BIN_MINUTES))

#: The headline cell of ``summary.md``: the morning school departure, the sharpest and most
#: recognisable feature of a real start-time distribution and the one eqasim's uniform jitter
#: destroys most visibly. Its SrV shares are READ from the committed table, never typed here.
HEADLINE_SEGMENT = "school_age_6_17_not_employed"
HEADLINE_PURPOSE = "education"
HEADLINE_HOURS = (6, 7)

#: Repository root, used for the git commit recorded in ``provenance.json``
#: (this file sits at <root>/braunschweig/analysis/synthesis/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

#: synpp hashes only THIS module's source, so every module that shapes the comparison must be
#: folded into the validation token or an edit to a metric would silently serve a cached report
#: built by the old code. ``departure_time`` is the whole model side; ``srv_departure_times``
#: owns the bin geometry, the segment/purpose/position taxonomy and the duration bands that BOTH
#: sides are expressed in; ``srv_plan_structure`` owns the harmonised group rule and the
#: duration bound; ``plan_structure`` owns the purpose harmonisation this module imports;
#: ``departure_time_model`` owns ``OFFSET_COLUMN`` and ``person_groups``, i.e. the column the
#: decomposition reads and the segment every cell is keyed on. ``reported_time_precision`` is
#: deliberately NOT hashed: this stage de-rounds nothing (the committed reference is already
#: de-rounded), so its half widths cannot change any number written here.
_HELPER_MODULES = (D, _plan_structure, SRVDT, _srv_plan_structure, _departure_time_model)


def validate(context):
    """synpp validation token: md5 over this stage's helper modules.

    Same mechanism and boundary semantics as
    ``braunschweig.analysis.synthesis.plan_structure_vs_srv.validate()``: over-hashing only costs
    a re-run of a minutes-long analysis stage, while under-hashing silently reports a stale
    comparison as a current result.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    context.stage("synthesis.population.enriched")
    # The trips view is aliased to the LOCAL name "trips" so execute() need not branch on the
    # view again, exactly as plan_structure_vs_srv does.
    trips_view = context.config(KEY_TRIPS_VIEW, DEFAULT_TRIPS_VIEW)
    if trips_view == "final":
        context.stage("synthesis.population.trips.final", alias="trips")
    elif trips_view == "pre_assignment":
        context.stage("synthesis.population.trips", alias="trips")
    else:
        raise ValueError(f"{_LOG_TAG} {KEY_TRIPS_VIEW} must be 'final' or 'pre_assignment', "
                         f"got {trips_view!r}")
    # No synthesis.population.sampled dependency (ruling A-R13): the harmonised group needs a
    # person's age and employment status, and the ENRICHED frame already carries both in the
    # synthetic schema persons_from_synthetic_schema consumes. Declaring the sampled MiD frame
    # only to re-derive them from HP_ALTER / P_TAET would pull an extra stage into the DAG for a
    # value this stage already has, and would make the group rule depend on which of two schemas
    # a run happened to read. No home-location dependency either: this stage does not segment by
    # Kreis, so it needs no VG250 join.
    context.config("output_path")
    context.config("data_path")
    context.config("sampling_rate")
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_DEPARTURE_TIME_MODEL, DEFAULT_DEPARTURE_TIME_MODEL)


# --------------------------------------------------------------------------- references

def srv_directory(data_path: str) -> str:
    """Directory of the committed SrV tables below ``data_path``."""
    return os.path.join(str(data_path), *SRV_SUBDIR)


def departure_time_reference_path(data_path: str) -> str:
    """Absolute path of the committed SrV 15-minute departure-time reference."""
    return os.path.join(srv_directory(data_path), SRVDT.DEPARTURE_TIME_TABLE)


def activity_duration_reference_path(data_path: str) -> str:
    """Absolute path of the committed SrV activity-duration reference."""
    return os.path.join(srv_directory(data_path), SRVDT.ACTIVITY_DURATION_TABLE)


def _load_committed(path: str, columns, what: str) -> pd.DataFrame:
    """Read a committed SrV table (``#`` provenance header skipped) and check its columns.

    A missing table aborts the stage naming the path and the regeneration script: degrading to a
    model-only report would produce a well-formed file that reads like a measured comparison
    while nothing was compared.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            "the committed SrV %s %s was not found at %s; it lives under "
            "eqasim-data/data/braunschweig/srv and is rebuilt by "
            "scripts/extract_srv_departure_times.py"
            % (what, os.path.basename(path), path))
    table = pd.read_csv(path, comment="#")
    missing = [column for column in columns if column not in table.columns]
    if missing:
        raise ValueError("the committed SrV %s %s is missing column(s) %s; got %s"
                         % (what, path, missing, list(table.columns)))
    return table


def _restrict_universe(table: pd.DataFrame, path: str) -> pd.DataFrame:
    """Keep the one universe the committed tables are built for, failing loudly on anything else.

    Both tables are built for :data:`SRVDT.UNIVERSE` only. Silently accepting a second universe
    would mix two definitions of a reporting day in one comparison.
    """
    universes = sorted(table["universe"].astype(str).unique().tolist())
    if universes != [SRVDT.UNIVERSE]:
        raise ValueError(
            "the committed SrV table %s carries universe(s) %s; this comparison is defined for "
            "'%s' only" % (path, universes, SRVDT.UNIVERSE))
    return table[table["universe"] == SRVDT.UNIVERSE].reset_index(drop=True)


def load_departure_time_reference(data_path: str) -> pd.DataFrame:
    """The committed 15-minute departure-time reference, ALL positions.

    Deliberately not
    :func:`braunschweig.popsim.departure_time_model.load_departure_time_reference`, which filters
    to ``position == "first"`` because the MODEL may only ever consume that one: this stage needs
    the ``later`` / ``all`` positions precisely because they are the hold-out dimensions.
    """
    path = departure_time_reference_path(data_path)
    table = _restrict_universe(
        _load_committed(path, SRVDT.DEPARTURE_TIME_COLUMNS, "departure-time reference"), path)
    LOGGER.info("%s departure-time reference: %d row(s), %d cell(s) from %s", _LOG_TAG,
                len(table), int(table.groupby(["segment", "purpose", "position"]).ngroups), path)
    return table


def load_activity_duration_reference(data_path: str) -> pd.DataFrame:
    """The committed activity-duration reference (the hold-out dimension)."""
    path = activity_duration_reference_path(data_path)
    table = _restrict_universe(
        _load_committed(path, SRVDT.ACTIVITY_DURATION_COLUMNS, "activity-duration reference"),
        path)
    LOGGER.info("%s activity-duration reference: %d row(s), %d cell(s) from %s", _LOG_TAG,
                len(table), int(table.groupby(["segment", "purpose"]).ngroups), path)
    return table


def reference_hour_shares(reference: pd.DataFrame, segment: str, purpose: str, position: str,
                          hours) -> dict:
    """SrV ``share_as_reported`` / ``share_derounded`` summed over the bins of each clock hour.

    Used by the summary headline so its SrV numbers are READ from the committed table rather
    than typed into the code (CLAUDE.md "No invented reference values"). Returns
    ``{hour: {"derounded": x, "as_reported": y}}``, NaN for an hour the cell does not cover.
    """
    cell = reference[(reference["segment"] == segment) & (reference["purpose"] == purpose)
                     & (reference["position"] == position)]
    shares = {}
    for hour in hours:
        bins = range(hour * BINS_PER_HOUR, (hour + 1) * BINS_PER_HOUR)
        rows = cell[cell["bin_15min"].isin(list(bins))]
        shares[hour] = {
            "derounded": float(rows["share_derounded"].sum()) if len(rows) else float("nan"),
            "as_reported": float(rows["share_as_reported"].sum()) if len(rows) else float("nan"),
        }
    return shares


# --------------------------------------------------------------------------- report

def _fmt(value, decimals=4) -> str:
    """Format a number for ``summary.md``; NaN renders as the explicit "n/a"."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{value:.{decimals}f}"


def _pct(value, decimals=2) -> str:
    """Format a share in [0, 1] as a percentage; NaN renders as "n/a"."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{100.0 * value:.{decimals}f} %"


def _emd_table_lines(rows: pd.DataFrame) -> list:
    """Markdown table body for a slice of the EMD table, ordered by the de-rounded distance."""
    lines = ["| segment | purpose | position | EMD vs de-rounded | EMD vs as-reported | "
             "n_model | n_srv |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for _, row in rows.iterrows():
        lines.append(f"| {row['segment']} | {row['purpose']} | {row['position']} | "
                     f"{_fmt(row['emd_vs_derounded'])} | {_fmt(row['emd_vs_as_reported'])} | "
                     f"{int(row['n_model'])} | {int(row['n_srv'])} |")
    return lines


def summary_markdown(decomposition, emd, durations, headline, provenance) -> str:
    """The human-readable report; every number in it also exists in a CSV next to it.

    Cautious by construction: the report says what was MEASURED and against which reference, and
    never that a distribution was "validated" or "hit a target" (CLAUDE.md "Research reporting",
    "No invented reference values"). The SrV headline shares come from ``headline``, which
    :func:`reference_hour_shares` read out of the committed table.
    """
    parameters = provenance.get("parameters", {})
    inputs = provenance.get("inputs", {})
    model = provenance.get("model", {})
    lines = [
        "# Departure times: synthetic population vs SrV 2023",
        "",
        "Measured comparison of the realised departure times of the synthetic population "
        "against the committed SrV 2023 reference. VALIDATION REFERENCE for every dimension "
        "reported here; the `calibrated` rows are the one dimension the `srv_mapped` model is "
        "fitted on (the FIRST departure of a chain), the `holdout` rows are not.",
        "",
        "Parameters: " + ", ".join(f"{key}={value}" for key, value in parameters.items()),
        f"Generated at: {provenance.get('generated_at')}",
        f"Pipeline commit: {provenance.get('pipeline_commit')}",
        f"References: {inputs.get('departure_time_reference_path')}, "
        f"{inputs.get('activity_duration_reference_path')}",
        "",
        "## Headline: the morning school departure",
        "",
        f"First `{HEADLINE_PURPOSE}` legs of `{HEADLINE_SEGMENT}` "
        f"(n_model={model.get('n_headline_legs')}), by clock hour. `raw` is the donor's "
        "reported MiD time, `pre_offset` the model's time before its offset, `realised` what "
        "MATSim simulates; the SrV columns are the committed reference's two views of the same "
        "hour.",
        "",
        "| hour | model raw | model pre-offset | model realised | SrV de-rounded | "
        "SrV as reported |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    head = decomposition[(decomposition["segment"] == HEADLINE_SEGMENT)
                         & (decomposition["purpose"] == HEADLINE_PURPOSE)
                         & (decomposition["position"] == D.POSITION_FIRST)].set_index("hour")
    for hour in HEADLINE_HOURS:
        row = head.loc[hour] if hour in head.index else None
        srv = headline.get(hour, {})
        lines.append(
            f"| {hour} | {_pct(None if row is None else row['share_raw'])} | "
            f"{_pct(None if row is None else row['share_pre_offset'])} | "
            f"{_pct(None if row is None else row['share_realised'])} | "
            f"{_pct(srv.get('derounded'))} | {_pct(srv.get('as_reported'))} |")
    lines += [
        "",
        f"Raw MiD departure times are available for {model.get('n_legs_with_raw_time')} of "
        f"{model.get('n_legs')} model legs "
        f"({_pct(model.get('share_legs_with_raw_time'))}). A spliced home-office day carries no "
        "raw MiD columns at all (the plan replacement nulls a replaced row's extra columns), so "
        "on the reporting-day view the `raw` column describes only the non-replaced legs; "
        "`n_raw` in decomposition.csv is that column's denominator per cell, and a cell without "
        "any raw time carries NaN rather than a fabricated zero.",
        "",
        "## Calibrated dimension (first departure of a chain)",
        "",
        "The `srv_mapped` model maps exactly this distribution, so a small distance here is a "
        "FIT, not independent evidence. Reported for both reference views: the de-rounded one "
        "is the behavioural target, the as-reported one still carries the survey's clock grid.",
        "",
    ]
    calibrated = emd[emd["dimension"] == D.DIMENSION_CALIBRATED].sort_values(
        ["segment", "purpose"])
    lines += _emd_table_lines(calibrated[calibrated["purpose"] == SRVDT.PURPOSE_ALL])
    lines += [
        "",
        "## Hold-out dimensions (later legs and the pooled day)",
        "",
        "Nothing in the departure-time model touches these directly; a change here can only "
        "come from the chain the first departure was moved with.",
        "",
    ]
    holdout = emd[(emd["dimension"] == D.DIMENSION_HOLDOUT)
                  & (emd["purpose"] == SRVDT.PURPOSE_ALL)].sort_values(["segment", "position"])
    lines += _emd_table_lines(holdout)
    lines += [
        "",
        "## Hold-out dimension: activity durations",
        "",
        "Duration of the activity following each leg (next departure minus arrival, within the "
        "person). A per-person offset moves a whole chain, so no departure-time model changes a "
        "within-person duration; a difference here reflects which donor day a person received.",
        "",
        "| segment | purpose | band | model | srv | delta_pp | n_model | n_srv |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    pooled = durations[(durations["segment"] == D.SEGMENT_ALL)
                       & (durations["purpose"] == SRVDT.PURPOSE_ALL)]
    for _, row in pooled.iterrows():
        lines.append(f"| {row['segment']} | {row['purpose']} | {row['band']} | "
                     f"{_fmt(row['share_model'])} | {_fmt(row['share_srv'])} | "
                     f"{_fmt(row['delta_pp'], 2)} | {int(row['n_model'])} | "
                     f"{int(row['n_srv'])} |")
    lines += [
        "",
        "## Limitations",
        "",
        "* SrV 2023 surveys seven of the eight ZGB Kreise (Wolfsburg 03103 is not surveyed), "
        "while the model side here is the whole synthetic population: this stage does not "
        "segment by home Kreis, so the two universes differ slightly. Departure-time profiles "
        "are shape comparisons, for which that difference matters far less than for a level "
        "metric, but it is not zero.",
        "* The model side is unweighted (a synthetic population is already expanded); the SrV "
        "side is weighted, with `n_srv` the unweighted respondent count behind each cell.",
        f"* {provenance.get('comparison', {}).get('n_cells_skipped_model_empty')} cell(s) were "
        "skipped because the model side had no leg and "
        f"{provenance.get('comparison', {}).get('n_cells_skipped_reference_empty')} because the "
        "reference cell is empty; an empty side is never compared against substituted zeros.",
        "* A stabilised or well-fitting distribution is a measured agreement with this "
        "reference, not a validation of travel behaviour as a whole.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(directory, decomposition, comparison, emd, durations, headline,
                  provenance) -> None:
    """Write the six report artifacts into ``directory`` (created if absent).

    The CSVs stay header-free (no provenance comment lines) so a consumer can read them with a
    plain ``pandas.read_csv``; the parameter block lives in ``provenance.json`` and at the top of
    ``summary.md``.
    """
    os.makedirs(directory, exist_ok=True)
    decomposition.to_csv(os.path.join(directory, "decomposition.csv"), index=False)
    comparison.to_csv(os.path.join(directory, "comparison.csv"), index=False)
    emd.to_csv(os.path.join(directory, "emd.csv"), index=False)
    durations.to_csv(os.path.join(directory, "activity_duration.csv"), index=False)
    with open(os.path.join(directory, "provenance.json"), "w", encoding="utf-8") as handle:
        json.dump(provenance, handle, indent=2)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(summary_markdown(decomposition, emd, durations, headline, provenance))


# --------------------------------------------------------------------------- stage

def execute(context):
    subdir = context.config(KEY_SUBDIR)
    data_path = context.config("data_path")
    output_path = context.config("output_path")
    sampling_rate = float(context.config("sampling_rate"))
    trips_view = context.config(KEY_TRIPS_VIEW)
    model_name = context.config(KEY_DEPARTURE_TIME_MODEL)
    out_dir = os.path.join(output_path, subdir)

    # Both references are loaded FIRST: a missing or unusable reference must abort before any
    # harmonisation work is done.
    reference = load_departure_time_reference(data_path)
    duration_reference = load_activity_duration_reference(data_path)

    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("trips")

    LOGGER.info("%s parameters: trips_view=%s, %s=%s, sampling_rate=%.4f; %d person(s), %d "
                "leg(s); writing to %s", _LOG_TAG, trips_view, KEY_DEPARTURE_TIME_MODEL,
                model_name, sampling_rate, len(df_persons), len(df_trips), out_dir)

    groups = person_groups(persons_from_synthetic_schema(df_persons))
    frame = D.harmonise_model_times(df_persons, df_trips, groups, trips_view=trips_view)

    decomposition = D.decomposition(frame)
    comparison = D.bin_comparison(frame, reference)
    emd = D.emd_table(comparison)
    durations = D.activity_duration_comparison(frame, duration_reference)

    headline = reference_hour_shares(reference, HEADLINE_SEGMENT, HEADLINE_PURPOSE,
                                     D.POSITION_FIRST, HEADLINE_HOURS)
    headline_cell = decomposition[(decomposition["segment"] == HEADLINE_SEGMENT)
                                  & (decomposition["purpose"] == HEADLINE_PURPOSE)
                                  & (decomposition["position"] == D.POSITION_FIRST)]
    n_headline_legs = int(headline_cell["n"].iloc[0]) if len(headline_cell) else 0

    provenance = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {
            "trips_view": trips_view,
            KEY_DEPARTURE_TIME_MODEL: model_name,
            "output_subdir": subdir,
            "sampling_rate": sampling_rate,
        },
        "inputs": {
            "departure_time_reference_path": departure_time_reference_path(data_path),
            "activity_duration_reference_path": activity_duration_reference_path(data_path),
            "n_departure_time_reference_rows": int(len(reference)),
            "n_activity_duration_reference_rows": int(len(duration_reference)),
            "universe": SRVDT.UNIVERSE,
        },
        "model": {
            "n_persons": int(len(df_persons)),
            # Raw-time coverage: the decomposition's `raw` column describes only these legs.
            "n_legs": int(frame.attrs["n_legs"]),
            "n_legs_with_raw_time": int(frame.attrs["n_legs_with_raw_time"]),
            "share_legs_with_raw_time": float(frame.attrs["share_legs_with_raw_time"]),
            "raw_columns_present": bool(frame.attrs["raw_columns_present"]),
            "n_legs_without_offset": int(frame.attrs["n_legs_without_offset"]),
            "n_headline_legs": n_headline_legs,
        },
        "decomposition": {
            key: int(value) for key, value in decomposition.attrs.items()},
        "comparison": {
            key: int(value) for key, value in comparison.attrs.items()},
        "activity_duration": {
            key: int(value) for key, value in durations.attrs.items()},
    }
    write_outputs(out_dir, decomposition, comparison, emd, durations, headline, provenance)

    for dimension in (D.DIMENSION_CALIBRATED, D.DIMENSION_HOLDOUT):
        rows = emd[(emd["dimension"] == dimension) & (emd["segment"] == D.SEGMENT_ALL)
                   & (emd["purpose"] == SRVDT.PURPOSE_ALL)]
        for _, row in rows.iterrows():
            LOGGER.info("%s %s position=%s: EMD vs de-rounded %.4f, vs as-reported %.4f "
                        "(n_model=%d, n_srv=%d)", _LOG_TAG, dimension, row["position"],
                        row["emd_vs_derounded"], row["emd_vs_as_reported"],
                        int(row["n_model"]), int(row["n_srv"]))
    for hour in HEADLINE_HOURS:
        cell = headline_cell[headline_cell["hour"] == hour]
        if not len(cell):
            continue
        row = cell.iloc[0]
        LOGGER.info("%s headline hour %d (first %s legs of %s): raw %s, pre-offset %s, "
                    "realised %s; SrV de-rounded %s, as reported %s", _LOG_TAG, hour,
                    HEADLINE_PURPOSE, HEADLINE_SEGMENT, _pct(row["share_raw"]),
                    _pct(row["share_pre_offset"]), _pct(row["share_realised"]),
                    _pct(headline[hour]["derounded"]), _pct(headline[hour]["as_reported"]))

    return dict(decomposition=decomposition, comparison=comparison, emd=emd,
                activity_duration=durations)
