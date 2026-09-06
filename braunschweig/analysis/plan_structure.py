"""Model side of the SrV 2023 plan-structure comparison (issue #369).

Harmonises the synthetic population's person and trip tables to EXACTLY the schema that
:mod:`braunschweig.calibration.srv_plan_structure` defines for the SrV side, so that both
sides of the comparison are measured by ONE metric implementation
(:func:`~braunschweig.calibration.srv_plan_structure.person_level` and
:func:`~braunschweig.calibration.srv_plan_structure.segment_metrics`). Re-implementing any
metric here would silently make the two sides incomparable, which is the failure mode the
schema split in the SrV module exists to prevent (CLAUDE.md "Validate metric
apples-to-apples").

This module is pure: no synpp context, no file system, no VG250 access. The synpp stage
``braunschweig.analysis.synthesis.plan_structure_vs_srv`` supplies the frames and writes the
report.

Universe of the model side
--------------------------
Every synthetic person exists and starts the reporting day at home, so the model matches the
reference's PRIMARY universe ``at_home_zero`` (a person without a trip is a legitimate
immobile person counted with 0 trips, not missing data). The harmonised person frame
therefore carries ``away_from_home = False`` and ``reported_at_home = True`` for every
person -- the two SrV-side universe flags, stated explicitly rather than left blank so a
reader can see which universe the model side claims.

Weights
-------
Every model person and trip carries ``weight = 1.0``: a synthetic population is already
expanded, so the weighted and unweighted metrics coincide. With ``sampling_rate < 1`` the
shares are unaffected (the sample is uniform) while the absolute ``n_persons_*`` counts are
scaled down by the sampling rate -- see the note on count metrics below.

Comparison scale
----------------
:func:`compare` reports ``delta_pp = (model - srv) * 100`` for SHARE metrics (values in
[0, 1]: ``mobility_rate``, ``share_*``, ``participation_*``, ``purpose_share_*``,
``dep_hour_share_*``) so the difference reads in percentage points, and the PLAIN difference
``model - srv`` for everything else (``trips_per_person*``, ``tours_per_mobile``,
``nonhome_acts_per_mobile``, ``mean_work_activity_h``). The column keeps the name
``delta_pp`` for both, so the metric name is what tells a reader the unit;
:func:`is_share_metric` is the single place that decides.

Count metrics (``n_persons_unweighted``, ``n_persons_weighted``,
``n_work_activities_measured``) are carried through the comparison for completeness, but
their delta is NOT a scientifically meaningful quantity: the SrV side is an expanded survey
of 18,223 respondents and the model side is a (possibly sampled) synthetic population. Read
them as the two sides' sample sizes, never as a gap.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration import srv_plan_structure as SRV

logger = logging.getLogger(__name__)

_LOG_TAG = "[plan structure model]"

#: Columns required on the ``synthesis.population.enriched`` frame.
MODEL_PERSON_COLUMNS = ("person_id", "household_id", "age", "sex", "employed")
#: Columns required on the ``synthesis.population.trips`` frame.
MODEL_TRIP_COLUMNS = ("person_id", "trip_index", "departure_time", "arrival_time",
                      "preceding_purpose", "following_purpose")
#: Optional flag written by ``braunschweig.popsim.plan_validation`` on a synthesised
#: return-home leg (the home-closure model, issue #367).
CLOSURE_COLUMN = "is_synthetic_closure"
#: Column carrying the home Kreis, as produced by ``spatial.assign_geographies``.
KREIS_COLUMN = "ars5"
HOME_PURPOSE = "home"
SECONDS_PER_MINUTE = 60.0

#: A metric whose value is a share in [0, 1]; its delta is reported in percentage points.
SHARE_METRIC_PREFIXES = ("share_", "participation_", "purpose_share_", "dep_hour_share_")
SHARE_METRIC_NAMES = ("mobility_rate",)

COMPARISON_COLUMNS = ["segment", "metric", "model", "srv", "delta_pp", "n_srv"]
MODEL_STRUCTURE_COLUMNS = ["segment", "metric", "value", "n_unweighted"]
REFERENCE_INPUT_COLUMNS = ("segment", "metric", "value", "n_unweighted")

ACCEPTED_DEVIATION_COLUMNS = ["deviation", "metric", "model", "srv", "delta_pp", "n_srv",
                              "note"]
#: Why the three deviations below are expected. The ADR id is deliberately NOT inlined here:
#: it is allocated with the ADRs themselves and lives in ONE place, the ``decisions:`` list of
#: the stage record ``docs/registry/stages/
#: braunschweig.analysis.synthesis.plan_structure_vs_srv.yml`` (one fact, one file).
ACCEPTED_DEVIATION_NOTE = ("closed plans by decision (issue #367; ADR in the stage record's "
                           "decisions)")
#: The three consequences of the "every plan starts and ends at home" decision, each derived
#: from one comparison metric. ``complement`` says whether the reported value is
#: ``1 - metric`` (an OPEN day is the complement of a closed one).
ACCEPTED_DEVIATIONS = (
    ("open_end_days", "share_mobile_last_to_home", True),
    ("open_start_days", "share_mobile_first_from_home", True),
    ("single_trip_days", "share_trips_1", False),
)

#: Metrics logged as the headline of a run and printed in ``summary.md``.
HEADLINE_METRICS = (
    "mobility_rate", "trips_per_person", "trips_per_mobile_person",
    "nonhome_acts_per_mobile", "tours_per_mobile",
    "share_mobile_first_from_home", "share_mobile_last_to_home",
    "share_mobile_odd_trip_count", "share_trips_home_based",
    "share_trips_followed_by_same_purpose",
    "share_trips_0", "share_trips_1", "share_trips_2",
    "participation_work", "participation_education", "participation_shop",
    "participation_leisure", "participation_escort", "participation_other",
    "purpose_share_work", "purpose_share_education", "purpose_share_shop",
    "purpose_share_leisure", "purpose_share_escort", "purpose_share_other",
    "purpose_share_home",
    "mean_work_activity_h",
)


# --------------------------------------------------------------------------- small helpers

def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s; got %s"
                         % (name, missing, sorted(frame.columns)))


def is_share_metric(metric: str) -> bool:
    """Whether ``metric`` is a share in [0, 1] (its delta is percentage points)."""
    return metric in SHARE_METRIC_NAMES or metric.startswith(SHARE_METRIC_PREFIXES)


# --------------------------------------------------------------------------- harmonisation

def harmonise_model(persons: pd.DataFrame, trips: pd.DataFrame,
                    homes_with_ars5: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Harmonise the eqasim person/trip frames to the SrV comparison schema.

    Parameters
    ----------
    persons:
        ``synthesis.population.enriched``; must carry :data:`MODEL_PERSON_COLUMNS`.
    trips:
        ``synthesis.population.trips``; must carry :data:`MODEL_TRIP_COLUMNS`.
        ``departure_time`` / ``arrival_time`` are SECONDS since midnight and become the
        SrV-side ``dep_min`` / ``arr_min`` MINUTES.
    homes_with_ars5:
        ``braunschweig.analysis.spatial.assign_geographies`` output; must carry
        ``household_id`` and :data:`KREIS_COLUMN`. Duplicate household rows are removed
        (the sjoin can emit several rows for a home on a polygon boundary) and counted.

    Returns
    -------
    ``(persons, trips)`` with exactly
    :data:`~braunschweig.calibration.srv_plan_structure.HARMONISED_PERSON_COLUMNS` and
    :data:`~braunschweig.calibration.srv_plan_structure.HARMONISED_TRIP_COLUMNS`, trips
    sorted by ``(pid, seq)``.

    ``pid`` keeps the model's native ``person_id`` dtype (an integer) rather than being cast
    to the SrV side's string ids: the two sides are never concatenated, only compared metric
    by metric, and the integer key keeps the per-segment ``isin`` restrictions in
    ``segment_metrics`` cheap on a full-scale population.

    Raises ``ValueError`` when a trip belongs to a person that is not in ``persons``: such a
    trip would be dropped silently by the person merge and shrink every trip-level metric.
    """
    _require_columns(persons, MODEL_PERSON_COLUMNS, "persons")
    _require_columns(trips, MODEL_TRIP_COLUMNS, "trips")
    _require_columns(homes_with_ars5, ("household_id", KREIS_COLUMN), "homes")

    orphaned = trips.loc[~trips["person_id"].isin(set(persons["person_id"])), "person_id"]
    if len(orphaned) > 0:
        raise ValueError(
            "%d of %d trips belong to a person that is not in the person frame (e.g. %s); "
            "synthesis.population.trips and synthesis.population.enriched must describe the "
            "same population" % (len(orphaned), len(trips),
                                 sorted(orphaned.unique().tolist())[:5]))

    homes = homes_with_ars5[["household_id", KREIS_COLUMN]]
    n_home_rows = len(homes)
    homes = homes.drop_duplicates("household_id")
    if len(homes) != n_home_rows:
        logger.warning("%s %d of %d home rows were duplicate household_id rows (boundary "
                       "sjoin); the first row per household is used", _LOG_TAG,
                       n_home_rows - len(homes), n_home_rows)

    merged = persons.merge(homes, on="household_id", how="left")
    n_persons = len(merged)
    n_no_kreis = int(merged[KREIS_COLUMN].isna().sum())
    logger.info("%s home Kreis resolved for %d/%d persons (%.2f%%), unresolved %d (%.2f%%)",
                _LOG_TAG, n_persons - n_no_kreis, n_persons,
                100.0 * (n_persons - n_no_kreis) / n_persons if n_persons else float("nan"),
                n_no_kreis, 100.0 * n_no_kreis / n_persons if n_persons else float("nan"))
    if n_no_kreis:
        logger.warning("%s %d persons have no resolvable home Kreis; they are KEPT in the "
                       "harmonised frame (dropping them here would shrink the model side "
                       "invisibly), but the comparison stage excludes them from the "
                       "head-to-head universe and raises above its configured unmatched-home "
                       "share", _LOG_TAG, n_no_kreis)

    age = pd.to_numeric(merged["age"], errors="coerce")
    sex = merged["sex"].astype(str).where(merged["sex"].astype(str).isin(SRV.SEXES))
    n_unmapped_sex = int(sex.isna().sum())
    if n_unmapped_sex:
        logger.warning("%s %d/%d persons carry a sex outside %s; they are in no sex segment",
                       _LOG_TAG, n_unmapped_sex, n_persons, list(SRV.SEXES))

    trip_counts = trips.groupby("person_id").size()
    out = pd.DataFrame({
        "pid": merged["person_id"].values,
        "weight": 1.0,
        "age": age.values,
        "sex": sex.values,
        "employed": merged["employed"].fillna(False).astype(bool).values,
        "kreis": merged[KREIS_COLUMN].values,
        "n_trips": merged["person_id"].map(trip_counts).fillna(0).astype(int).values,
        # Every synthetic person exists and starts the day at home (universe at_home_zero).
        "away_from_home": False,
        "reported_at_home": True,
    })
    out["age_band"] = pd.cut(out["age"], list(SRV.AGE_BINS),
                             labels=list(SRV.AGE_LABELS)).astype(str)
    out["group"] = SRV.harmonised_group(out)
    out = out[SRV.HARMONISED_PERSON_COLUMNS].reset_index(drop=True)

    harmonised_trips = pd.DataFrame({
        "pid": trips["person_id"].values,
        "seq": pd.to_numeric(trips["trip_index"], errors="coerce").astype(int).values,
        "weight": 1.0,
        "purpose": _harmonise_purpose(trips["following_purpose"], "following_purpose"),
        "prev_purpose": _harmonise_purpose(trips["preceding_purpose"], "preceding_purpose"),
        "dep_min": pd.to_numeric(trips["departure_time"], errors="coerce").values
        / SECONDS_PER_MINUTE,
        "arr_min": pd.to_numeric(trips["arrival_time"], errors="coerce").values
        / SECONDS_PER_MINUTE,
    })
    harmonised_trips = (harmonised_trips.sort_values(["pid", "seq"])
                        [SRV.HARMONISED_TRIP_COLUMNS].reset_index(drop=True))
    logger.info("%s harmonised %d persons (%d mobile, %.2f%%) and %d trips; invalid "
                "departure time %d, invalid arrival time %d", _LOG_TAG, len(out),
                int((out["n_trips"] > 0).sum()),
                100.0 * (out["n_trips"] > 0).mean() if len(out) else float("nan"),
                len(harmonised_trips), int(harmonised_trips["dep_min"].isna().sum()),
                int(harmonised_trips["arr_min"].isna().sum()))
    return out, harmonised_trips


def _harmonise_purpose(values: pd.Series, column: str) -> np.ndarray:
    """Map an eqasim purpose column onto the seven harmonised purposes.

    The popsim_mid trip builder emits exactly
    :data:`~braunschweig.calibration.srv_plan_structure.PURPOSES`, so this map is normally
    the identity. A value outside that set becomes ``"unknown"`` -- counted as a trip
    everywhere but excluded from the purpose-share denominator, exactly as on the SrV side --
    and is WARNED about with its share, because an unmapped purpose silently reassigned to
    "other" would move the purpose shares without any trace (CLAUDE.md fallback transparency).
    """
    text = values.astype(str)
    known = text.isin(SRV.PURPOSES)
    n_unknown = int((~known).sum())
    if n_unknown:
        logger.warning("%s %d/%d trips carry a %s outside %s (found %s); they map to '%s' "
                       "(%.2f%%): counted as trips but excluded from the purpose-share "
                       "denominator", _LOG_TAG, n_unknown, len(text), column,
                       list(SRV.PURPOSES), sorted(text[~known].unique().tolist())[:10],
                       SRV.UNKNOWN_PURPOSE, 100.0 * n_unknown / len(text) if len(text) else 0.0)
    return text.where(known, SRV.UNKNOWN_PURPOSE).values


# --------------------------------------------------------------------------- model table

def model_structure(per: pd.DataFrame, trips: pd.DataFrame, segments) -> pd.DataFrame:
    """Long model plan-structure table for the given ``(label, person subset)`` segments.

    ``per`` is a :func:`~braunschweig.calibration.srv_plan_structure.person_level` frame and
    ``trips`` the full harmonised trip table (``segment_metrics`` restricts it to each
    segment's persons). Returns one row per (segment, metric) with
    :data:`MODEL_STRUCTURE_COLUMNS`; ``n_unweighted`` repeats the segment's person count on
    every one of its rows, mirroring the committed reference table's shape.
    """
    rows = []
    for label, subset in segments:
        metrics = SRV.segment_metrics(subset, trips, label)
        n_unweighted = int(metrics["n_persons_unweighted"])
        for metric, value in metrics.items():
            if metric == "segment":
                continue
            rows.append({"segment": label, "metric": metric, "value": float(value),
                         "n_unweighted": n_unweighted})
    return pd.DataFrame(rows, columns=MODEL_STRUCTURE_COLUMNS)


# --------------------------------------------------------------------------- comparison

def compare(model_long: pd.DataFrame, reference_long: pd.DataFrame) -> pd.DataFrame:
    """Join the model and reference long tables into the comparison table.

    Both frames carry ``segment``, ``metric``, ``value`` and ``n_unweighted``;
    ``reference_long`` may still carry the reference's ``universe`` column, but it must then
    hold EXACTLY ONE universe -- mixing ``at_home_zero`` and ``at_home_only`` rows would
    compare against two different definitions of a day at once and is refused
    (:class:`ValueError`).

    The join is an OUTER join, so a segment present on only one side keeps its row with a
    ``NaN`` on the other side and a ``NaN`` delta. That is what makes Wolfsburg (03103, not
    surveyed by SrV) visible as model-only instead of silently vanishing or, worse, being
    compared against a substituted zero.

    Returns :data:`COMPARISON_COLUMNS`, sorted by (segment, metric).
    """
    _require_columns(model_long, REFERENCE_INPUT_COLUMNS, "model_long")
    _require_columns(reference_long, REFERENCE_INPUT_COLUMNS, "reference_long")
    if "universe" in reference_long.columns:
        universes = sorted(reference_long["universe"].dropna().unique().tolist())
        if len(universes) != 1:
            raise ValueError(
                "reference_long carries %d universe values %s; the comparison is defined "
                "against exactly one universe, so filter it before calling compare"
                % (len(universes), universes))

    model = (model_long[["segment", "metric", "value"]]
             .rename(columns={"value": "model"}))
    reference = (reference_long[list(REFERENCE_INPUT_COLUMNS)]
                 .rename(columns={"value": "srv", "n_unweighted": "n_srv"}))

    merged = model.merge(reference, on=["segment", "metric"], how="outer", indicator=True)
    share = merged["metric"].map(is_share_metric)
    difference = merged["model"] - merged["srv"]
    merged["delta_pp"] = np.where(share, difference * 100.0, difference)
    merged = merged.sort_values(["segment", "metric"]).reset_index(drop=True)

    # Row presence, not value presence: a NaN value also occurs for a segment that is EMPTY
    # on one side, which is a different statement from "this side has no such row at all".
    n_model_only = int((merged["_merge"] == "left_only").sum())
    n_reference_only = int((merged["_merge"] == "right_only").sum())
    logger.info("%s comparison: %d rows, %d matched, %d model-only (no SrV segment), "
                "%d reference-only (no model persons in that segment)", _LOG_TAG,
                len(merged), len(merged) - n_model_only - n_reference_only,
                n_model_only, n_reference_only)
    return merged[COMPARISON_COLUMNS]


def accepted_deviations(comparison: pd.DataFrame,
                        segment: str = "all") -> pd.DataFrame:
    """The three deviations that follow from the "plans start and end at home" decision.

    Every synthetic day is a closed home-based chain by construction (issue #367), so the
    model cannot reproduce the SrV shares of days that end away from home, start away from
    home, or consist of a single trip. These are ACCEPTED deviations, not defects, and this
    table states them explicitly with both sides' values so a reader of the comparison does
    not read them as a model failure.

    ``open_end_days`` and ``open_start_days`` are complements (``1 - metric``) of the
    corresponding closure metric; ``single_trip_days`` is read directly. The delta is
    reported in percentage points (all three are shares).
    """
    rows = []
    indexed = comparison[comparison["segment"] == segment].set_index("metric")
    for name, metric, complement in ACCEPTED_DEVIATIONS:
        if metric not in indexed.index:
            raise ValueError(
                "the comparison table has no metric %r in segment %r, so the accepted "
                "deviation %r cannot be reported" % (metric, segment, name))
        row = indexed.loc[metric]
        model = float(row["model"])
        srv = float(row["srv"])
        if complement:
            model, srv = 1.0 - model, 1.0 - srv
        rows.append({"deviation": name, "metric": metric, "model": model, "srv": srv,
                     "delta_pp": (model - srv) * 100.0, "n_srv": row["n_srv"],
                     "note": ACCEPTED_DEVIATION_NOTE})
    return pd.DataFrame(rows, columns=ACCEPTED_DEVIATION_COLUMNS)


# --------------------------------------------------------------------------- closure

def closure_metrics(trips: pd.DataFrame) -> dict:
    """Rates of the SYNTHESISED home-closure legs in the model trip table.

    ``trips`` is the raw ``synthesis.population.trips`` frame (``person_id``,
    ``following_purpose`` and the optional :data:`CLOSURE_COLUMN`). The closure is a
    modelling assumption, not observed behaviour, so its extent is reported as an explicit
    rate with both numerator and denominator (CLAUDE.md fallback transparency); the
    denominators mirror ``braunschweig.popsim.trips_stage._log_closure_share`` exactly, so
    the two reports cannot drift apart:

    * ``share_trips_synthetic_closure`` -- synthesised legs / all trips
    * ``share_persons_closed`` -- persons with a synthesised leg / persons WITH a trip
      (an immobile person has no chain that could be closed)
    * ``share_home_trips_synthetic`` -- synthesised legs / all trips to home

    When the column is absent the three shares are 0.0 and ``closure_column_present`` is
    False, with a warning: ``PlanValidator.repair_trips`` always adds the column, so a
    missing one means the repair did not run and the zeroes must not be read as "no closures".
    """
    _require_columns(trips, ("person_id", "following_purpose"), "trips")
    n_trips = int(len(trips))
    n_persons = int(trips["person_id"].nunique())
    is_home = trips["following_purpose"].astype(str) == HOME_PURPOSE
    n_home_trips = int(is_home.sum())

    present = CLOSURE_COLUMN in trips.columns
    if not present:
        logger.warning(
            "%s no '%s' column on the trip table; the synthetic-closure rates are reported "
            "as 0 and flagged (closure_column_present=False). PlanValidator.repair_trips "
            "always adds the column, so a missing one means the repair was skipped -- the "
            "zeroes are NOT evidence that no chain was closed.", _LOG_TAG, CLOSURE_COLUMN)
        closure = pd.Series(False, index=trips.index)
    else:
        closure = trips[CLOSURE_COLUMN].fillna(False).astype(bool)

    n_closure = int(closure.sum())
    n_persons_closed = int(trips.loc[closure, "person_id"].nunique())
    n_home_closure = int((closure & is_home).sum())
    metrics = {
        "closure_column_present": present,
        "n_trips": n_trips,
        "n_trips_synthetic_closure": n_closure,
        "share_trips_synthetic_closure": (n_closure / n_trips) if n_trips else 0.0,
        "n_persons": n_persons,
        "n_persons_closed": n_persons_closed,
        "share_persons_closed": (n_persons_closed / n_persons) if n_persons else 0.0,
        "n_home_trips": n_home_trips,
        "n_home_trips_synthetic": n_home_closure,
        "share_home_trips_synthetic": (n_home_closure / n_home_trips) if n_home_trips else 0.0,
    }
    logger.info("%s synthetic closure: %d/%d trips (%.2f%%), %d/%d persons with a trip "
                "(%.2f%%), %d/%d home trips (%.2f%%)", _LOG_TAG,
                n_closure, n_trips, 100.0 * metrics["share_trips_synthetic_closure"],
                n_persons_closed, n_persons, 100.0 * metrics["share_persons_closed"],
                n_home_closure, n_home_trips, 100.0 * metrics["share_home_trips_synthetic"])
    return metrics


def closure_frame(metrics: dict) -> pd.DataFrame:
    """The :func:`closure_metrics` dict as the long ``closure.csv`` table."""
    rows = [{"metric": key, "value": float(value)}
            for key, value in metrics.items() if key != "closure_column_present"]
    rows.append({"metric": "closure_column_present",
                 "value": float(bool(metrics["closure_column_present"]))})
    return pd.DataFrame(rows, columns=["metric", "value"])
