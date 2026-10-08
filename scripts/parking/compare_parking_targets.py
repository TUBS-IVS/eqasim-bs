"""Compare the parking metrics of model arms with the committed SrV 2023 references (parking cost zones v2, Task 5, issue #436).

An analysis aid, never a pipeline stage and never a model input. For every arm (OFF, LEGACY ring, ZONES v2, ZONES v2 without
garages, ...) it reads the output directory of one MATSim run and writes one table of metrics with the model value, the
reference (READ from the committed SrV tables, never typed here), the difference in percentage points and, for EVERY row, the
universe of the number and its caveat. A comparison is a comparison: no row of this table validates the model, convergence of
a run is not validation and a smoke run is not validation.

Inputs per arm (read only)
    * ``ITERS/it.N/N.parking_outcomes.csv``, the outcome report of the Java parking module (CSV v3 header ``outcome,zone_id,
      purpose,count,share,garage_probability_sum,stays_with_garages_in_range``; v2 is the first five columns). The version
      is detected by the header and the rows are keyed by (outcome NAME, zone id, purpose), never by position or by the
      enum order (the Java enum order differs from ``braunschweig.parking.cost.OUTCOMES``). An empty zone id belongs to
      NO_ZONE only. Arms that price no zones (OFF, LEGACY) have no report and are given with ``--arm-without-outcomes``;
      their outcome rows stay empty and say why, so the table is rectangular and nothing is missing silently.
    * ``eqasim_trips.csv.gz`` (or ``.csv``, ``;``-separated) of the run: the chosen trips of its FINAL iteration with the
      destination coordinates in EPSG:25832, joined to the zone polygons of the committed release by the production function
      ``braunschweig.parking.attach.attach_parking_zones`` (via the plans helper of ``count_zone_exposure.py``).
    * the committed tariff table (zone type and workplace class of every zone) and the two committed SrV tables
      ``srv2023_city_center_parking.csv`` and ``srv2023_commute_parking_by_workplace_class.csv``.

What ``count`` means (stated again in every outcome row)
    The ``count`` of the outcome report is the number of PRICING CALLS: car alternatives that mode choice evaluated, chosen or
    not. It is not a number of trips or persons. A share of the report is therefore a share of evaluated alternatives. The
    chosen car trips are used only where a metric needs trips: the car mode shares (eqasim_trips). The expected garage share of
    a universe is ``sum(garage_probability_sum) / sum(count)`` over the selected rows (rows of early outcomes count with
    probability 0); the report's own ``share`` column is not used. This garage share is NOT the E5 calibration universe
    (all stays of all modes at destinations, no early rules), which is why its row is labelled accordingly.

Metrics (``universe`` identifiers are in the table; Ia and Ib are the two city-centre zones ``bs_zone_ia`` and ``bs_zone_ib``)
    * ``paid_share_ia_ib_*``: pricing calls with a PAID_* outcome over all calls at destinations in Ia and Ib, for all
      purposes, without home and without home, work and education. Time-aware because the Java side applied the fee window,
      the free threshold and the maximum stay. An UPPER BOUND of the chosen-trip paid share: PAID_EXPECTED and zero-cent
      PAID_* calls count as paid, and the calls include non-chosen alternatives, which are less likely chosen when they
      pay. Reference: ``paid_share_overall`` of ``srv2023_city_center_parking``.
    * ``garage_share_ia_ib_other_purposes``: expected garage share of the calls of the other purposes (not home, work,
      education) in Ia and Ib. Reference: the calibration target ``garage_large_lot / (garage_large_lot + street)`` of the same
      table, labelled "calibration target, not validation". Diagnostics: the share of those calls with a garage in range and
      the share priced as an expectation (PAID_EXPECTED).
    * ``garage_share_ia_ib_work_education_*``: expected garage share of the work and education calls in Ia and Ib, with and
      without the EMPLOYER_FREE calls in the denominator. Reference: ``share_garage_large_lot / (share_garage_large_lot +
      share_street)`` of the class ``bs_zentrum`` of ``srv2023_commute_parking_by_workplace_class``: an independent check,
      not used in the calibration.
    * ``free_share_work_education_class_<class>`` and ``employer_free_share_work_education_class_<class>``: the share of the
      work and education calls in the street and resident zones of a workplace class that carry a no-charge outcome
      (EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS, FREE_WITHIN_LIMIT) and the share that is EMPLOYER_FREE alone (the
      mechanism of the free-parking draw). Reference: ``share_free_total`` of the class row.
    * ``pricing_calls_*`` and ``no_zone_share_of_pricing_calls``: the call counts and the share of calls outside every zone
      (primary-path coverage: a share near 0 or 1 points to a broken zone join).
    * ``car_trip_share_*``, ``trips_*``: the share of chosen trips by ``car`` among the trips ending in any zone, outside every
      zone, in Ia and Ib and overall (model only, no reference) and the trip counts behind it.

Usage (from the repository root)::

    python scripts/parking/compare_parking_targets.py --data-path eqasim-data/data \
        --arm-without-outcomes off=<run off>/simulation_output --arm-without-outcomes legacy=<run legacy>/simulation_output \
        --arm zones_v2=<run zones>/simulation_output --arm zones_v2_no_garages=<run no garages>/simulation_output \
        --out <dir>/parking_targets_comparison_<scenario>_<date>.csv

``--iteration N`` selects the iteration of the outcome reports and ``--iteration FIRST-LAST`` pools an inclusive range
(counts summed cell by cell; default: the last ``ITERS/it.N`` of each arm alone). Only the agents that replan in an
iteration price their car alternatives (about 5 % of the agents with the eqasim default), so one iteration is a small sample
and a range enlarges it; the calls of different iterations are not independent. eqasim_trips is written for the final
iteration only, so a selection other than the last iteration alone is logged as a warning. The table, the per-arm delta
table (``<out>_arm_deltas.csv``: every arm against every EARLIER arm, share metrics with a value in both) and the
provenance (``<out>_provenance.json``: input paths and SHA-256, iterations, code state) are written; an existing file is
replaced only with ``--overwrite``. Every table row carries ``universe_size`` (the pricing calls or trips behind it) and
the delta table ``n_model`` and ``n_baseline``; a difference between two arms is one stochastic run each, descriptive only.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import logging
import math
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import attach, cost, zones as parking_zones  # noqa: E402


def _load_script(path: Path, name: str):
    """Load a sibling script as a module from its file (the scripts are no package): the plans helper and the SrV readers
    are reused, not copied."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


calibrate = _load_script(SCRIPTS_DIR / "calibrate_garage_decay.py", "calibrate_garage_decay_for_comparison")
exposure = calibrate.load_exposure_module()

log = logging.getLogger("compare_parking_targets")

DEFAULT_DATA_PATH = calibrate.DEFAULT_DATA_PATH
ZONES_RELATIVE = calibrate.ZONES_RELATIVE
TARIFFS_RELATIVE = "braunschweig/parking/parking_tariffs_2026.csv"
CITY_CENTER_RELATIVE = calibrate.CITY_CENTER_RELATIVE
COMMUTE_RELATIVE = calibrate.COMMUTE_RELATIVE

#: Spec E5: the two city-centre zones of Braunschweig, shared with the calibration.
CITY_CENTER_ZONE_IDS = calibrate.CALIBRATION_ZONE_IDS
CAR_MODE = "car"
COMMUTER_PURPOSES = tuple(sorted(cost.COMMUTER_PURPOSES))
HOME_PURPOSE = cost.HOME_PURPOSE

#: Every outcome of the Python reference belongs to exactly one class; ``_check_outcome_classes`` refuses a new outcome that
#: nobody classified, so a new pricing branch cannot be counted as neither paid nor free by accident. Counting every PAID_*
#: call as paid makes the paid share an UPPER BOUND of the paid share of chosen trips: PAID_EXPECTED (a mean over the street and
#: the garages) and a PAID_* call that costs zero cents count as paid, and the calls include the non-chosen alternatives, which
#: are less likely chosen when they pay, so the chosen-trip paid share is expected to be lower.
PAID_OUTCOMES = (cost.PAID_METERED, cost.PAID_LONG_STAY, cost.PAID_CAMPUS_MEMBER, cost.PAID_CAMPUS_GUEST, cost.PAID_GARAGE,
                 cost.PAID_COMMUTER, cost.PAID_EXPECTED)
#: Outcomes that cost nothing by their own rule (a PAID_* call that happens to cost 0 ct is counted as paid).
FREE_OUTCOMES = (cost.EMPLOYER_FREE, cost.RESIDENT_FREE, cost.OUTSIDE_FEE_HOURS, cost.FREE_WITHIN_LIMIT)
OTHER_OUTCOMES = (cost.HOME, cost.NO_ZONE)

REPORT_V2_COLUMNS = ("outcome", "zone_id", "purpose", "count", "share")
REPORT_V3_COLUMNS = REPORT_V2_COLUMNS + ("garage_probability_sum", "stays_with_garages_in_range")
REPORT_V1_COLUMNS = ("outcome", "count", "share")
#: ``share`` is count / total written with six decimals: it may differ from the recomputed value by the rounding only.
SHARE_TOLERANCE = 1e-6
#: Garage probabilities are summed in fixed point (1e-9 per stay), written with six decimals.
GARAGE_SUM_TOLERANCE = 1e-6
OUTCOME_REPORT_FILE_NAME = "parking_outcomes.csv"

TRIPS_FILE_NAMES = ("eqasim_trips.csv.gz", "eqasim_trips.csv")
TRIPS_COLUMNS = ("mode", "destination_x", "destination_y")
ARM_LABEL_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
TABLE_COLUMNS = ("arm", "metric", "unit", "universe", "universe_size", "model", "reference", "delta_pp", "reference_source",
                 "universe_note")
DELTA_COLUMNS = ("arm", "baseline_arm", "metric", "universe", "n_model", "n_baseline", "model", "baseline_model", "delta_pp")
SHARE_UNIT, COUNT_UNIT = "share", "count"
MODEL_ONLY = "none (model only)"
SHARE_DECIMALS, DELTA_DECIMALS = 6, 4

CALLS_NOTE = ("count of the outcome report = pricing calls (car alternatives that mode choice evaluated, chosen or not) of "
              "the selected iteration(s), not trips or persons")
GARAGE_INERT_NOTE = "no garage option acted in this run (options off or no garage in range)"
POOLING_NOTE = ("pooling does not change the pre-equilibrium status of early iterations, the pooled calls are not independent "
                "(the same agents can replan more than once) and stable shares are convergence, not validation")
NO_REPORT_NOTE = "no parking outcome report for this arm (it prices no zones); the metric needs the outcomes of the Java module"
V2_REPORT_NOTE = "the report is CSV v2 and has no garage columns (the run predates CSV v3), so no garage figure is available"
EMPTY_UNIVERSE_NOTE = "No pricing call of this universe in this arm, so there is no model value."
RESIDENT_CAVEAT = ("SrV asks Braunschweig residents about their usual place, the model counts calls at all destinations: "
                   "a comparison, not a validation")


def format_iterations(iterations) -> str:
    """``[7, 8, 9, 10]`` -> ``"7-10"``; ``[3, 5, 6]`` -> ``"3, 5-6"`` (sorted, runs of consecutive numbers collapsed)."""
    numbers = sorted(set(int(number) for number in iterations))
    runs, start = [], 0
    for position in range(1, len(numbers) + 1):
        if position == len(numbers) or numbers[position] != numbers[position - 1] + 1:
            first, last = numbers[start], numbers[position - 1]
            runs.append(str(first) if first == last else f"{first}-{last}")
            start = position
    return ", ".join(runs)


def iteration_note(iterations) -> str:
    """The sentence that names the outcome report iteration(s) of an arm in every outcome row (with the pooling caveat)."""
    if len(set(iterations)) == 1:
        return f"Outcome report of iteration {format_iterations(iterations)}."
    return (f"Outcome reports of iterations {format_iterations(iterations)}, pooled (counts summed cell by cell): "
            f"{POOLING_NOTE}.")


def _check_outcome_classes() -> None:
    classified = [*PAID_OUTCOMES, *FREE_OUTCOMES, *OTHER_OUTCOMES]
    missing = sorted(set(cost.OUTCOMES) - set(classified))
    extra = sorted(set(classified) - set(cost.OUTCOMES))
    if missing or extra or len(set(classified)) != len(classified):
        raise RuntimeError(f"the outcome classes of compare_parking_targets.py do not match braunschweig.parking.cost.OUTCOMES: "
                           f"unclassified {missing}, unknown {extra}; classify every outcome exactly once as paid, free or other")


_check_outcome_classes()


# ----------------------------------------------------------------------------------------------------- the outcome report


def read_parking_outcomes(path) -> tuple[pd.DataFrame, int]:
    """Read one outcome report: ``(frame, version)`` with the columns outcome, zone_id, purpose (text; an empty zone id stays
    ``""``), count (int), share and, for v3, garage_probability_sum and stays_with_garages_in_range (NaN for v2).

    The version is detected by the header (the column set, never the position). Raises ``FileNotFoundError``, and
    ``ValueError`` for a v1 report (no zone id), another header, a report without a row (no priced stay), a count that is no
    non-negative whole number, an outcome the reference does not know, a NO_ZONE row with a zone id, another row without
    one, a duplicate (outcome, zone id, purpose), a share that differs from count / total by more than the six-decimal
    rounding, a garage probability sum outside [0, count] and stays with garages in range outside [0, count].
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"outcome report missing: {path}")
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = list(raw.columns)
    if columns == list(REPORT_V1_COLUMNS):
        raise ValueError(f"{path}: this is a version 1 report (outcome,count,share): it has no zone_id and purpose, so no zone "
                         "can be selected; rerun the arm with the outcome CSV v2 or v3 of the parking module")
    if set(columns) == set(REPORT_V3_COLUMNS) and len(columns) == len(REPORT_V3_COLUMNS):
        version = 3
    elif set(columns) == set(REPORT_V2_COLUMNS) and len(columns) == len(REPORT_V2_COLUMNS):
        version = 2
    else:
        raise ValueError(f"{path}: the report header {columns} is neither the v3 header {list(REPORT_V3_COLUMNS)} nor the v2 "
                         f"header {list(REPORT_V2_COLUMNS)}")
    if raw.empty:
        raise ValueError(f"{path}: the report has no row, so the iteration has no priced stay (the report of iteration 0 is "
                         "written before any replanning; select a later iteration)")
    counts = pd.to_numeric(raw["count"], errors="coerce")
    if counts.isna().any() or (counts < 0).any() or (counts % 1 != 0).any():
        raise ValueError(f"{path}: every count must be a non-negative whole number, found {sorted(set(raw['count']))[:5]}")
    unknown = sorted(set(raw["outcome"]) - set(cost.OUTCOMES))
    if unknown:
        raise ValueError(f"{path}: unknown outcome {unknown[0]!r} (all unknown: {unknown}); the outcome names are those of "
                         "braunschweig.parking.cost.OUTCOMES")
    no_zone = raw["outcome"] == cost.NO_ZONE
    if (no_zone & (raw["zone_id"] != "")).any():
        raise ValueError(f"{path}: a NO_ZONE row has a zone id; a stay outside every zone has none")
    if (~no_zone & (raw["zone_id"] == "")).any():
        raise ValueError(f"{path}: an outcome other than NO_ZONE needs a zone id, found a row without one")
    if (raw["purpose"] == "").any():
        raise ValueError(f"{path}: a row has no purpose")
    duplicated = raw.duplicated(["outcome", "zone_id", "purpose"], keep=False)
    if duplicated.any():
        example = raw.loc[duplicated, ["outcome", "zone_id", "purpose"]].iloc[0].tolist()
        raise ValueError(f"{path}: duplicate (outcome, zone_id, purpose) rows, e.g. {example}")
    frame = pd.DataFrame({"outcome": raw["outcome"], "zone_id": raw["zone_id"], "purpose": raw["purpose"],
                          "count": counts.astype("int64"), "share": pd.to_numeric(raw["share"], errors="coerce")})
    total = int(frame["count"].sum())
    recomputed = frame["count"] / total if total else pd.Series(0.0, index=frame.index)
    if frame["share"].isna().any() or ((frame["share"] - recomputed).abs() > SHARE_TOLERANCE).any():
        raise ValueError(f"{path}: the share column differs from count / total (total {total}) by more than the rounding of "
                         "six decimals; the report is not one the Java listener wrote")
    if version == 3:
        probability = pd.to_numeric(raw["garage_probability_sum"], errors="coerce")
        in_range = pd.to_numeric(raw["stays_with_garages_in_range"], errors="coerce")
        if probability.isna().any() or (probability < 0).any() or (probability > frame["count"] + GARAGE_SUM_TOLERANCE).any():
            raise ValueError(f"{path}: garage_probability_sum must lie in [0, count] in every row")
        if in_range.isna().any() or (in_range < 0).any() or (in_range > frame["count"]).any() or (in_range % 1 != 0).any():
            raise ValueError(f"{path}: stays_with_garages_in_range must be a whole number in [0, count] in every row")
        frame["garage_probability_sum"] = probability.astype(float)
        frame["stays_with_garages_in_range"] = in_range.astype("int64")
    else:
        frame["garage_probability_sum"] = np.nan
        frame["stays_with_garages_in_range"] = np.nan
    return frame, version


def last_iteration(run_output) -> int:
    """The highest N of the directories ``ITERS/it.N`` of a run output; ``FileNotFoundError`` when there is none."""
    iters = Path(run_output) / "ITERS"
    found = [int(match.group(1)) for entry in (iters.iterdir() if iters.is_dir() else [])
             for match in [re.fullmatch(r"it\.(\d+)", entry.name)] if match and entry.is_dir()]
    if not found:
        raise FileNotFoundError(f"{iters} holds no iteration directory it.N, so there is no parking outcome report in "
                                f"{run_output}; an arm that prices no zones (OFF, LEGACY) is given with --arm-without-outcomes")
    return max(found)


def outcome_report_path(run_output, iteration: int) -> Path:
    return Path(run_output) / "ITERS" / f"it.{iteration}" / f"{iteration}.{OUTCOME_REPORT_FILE_NAME}"


# --------------------------------------------------------------------------------------------------------------- references


class References(NamedTuple):
    """The reference values, each read from the committed SrV table named in ``tables``."""

    paid_share: float
    garage_target: calibrate.Target
    commuter_garage: calibrate.Target
    free_share_by_class: pd.Series
    tables: dict


def read_references(data_path) -> References:
    """Read the references from the two committed SrV tables below ``data_path`` (never typed in this script)."""
    data_path = Path(data_path)
    city_center, commute = data_path / CITY_CENTER_RELATIVE, data_path / COMMUTE_RELATIVE
    return References(
        paid_share=calibrate.read_paid_share_reference(city_center),
        garage_target=calibrate.read_city_center_target(city_center),
        commuter_garage=calibrate.read_commuter_reference(commute),
        free_share_by_class=attach.free_share_by_class(calibrate.read_srv_table(commute)),
        tables={"city_center": city_center, "commute": commute})


def free_draw_zone_classes(tariffs: pd.DataFrame) -> pd.Series:
    """Workplace class of every street and resident zone (the zone types of the free-parking draw; a campus zone has its own
    share, ASSUMPTION C2, and no class row), indexed by zone id."""
    rows = tariffs[tariffs["zone_type"].isin(attach.FREE_DRAW_ZONE_TYPES)]
    return pd.Series(rows["workplace_class"].to_numpy(dtype=object), index=pd.Index(rows["zone_id"].to_numpy(dtype=object),
                                                                                     name="zone_id"), name="workplace_class")


# ------------------------------------------------------------------------------------------------------------------ trips


def find_trips_file(run_output) -> Path:
    for name in TRIPS_FILE_NAMES:
        candidate = Path(run_output) / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no eqasim_trips file ({' or '.join(TRIPS_FILE_NAMES)}) in {run_output}")


def read_trips(run_output) -> tuple[pd.DataFrame, Path]:
    """The chosen trips of a run (columns ``TRIPS_COLUMNS``) and the file they came from. Raises ``ValueError`` for a missing
    column, no trip and a destination that is not a finite number."""
    path = find_trips_file(run_output)
    header = list(pd.read_csv(path, sep=";", nrows=0).columns)
    missing = [column for column in TRIPS_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"{path}: the trips lack the column(s) {missing}; found {header}")
    trips = pd.read_csv(path, sep=";", usecols=list(TRIPS_COLUMNS))
    if trips.empty:
        raise ValueError(f"{path}: no trip")
    coordinates = trips[["destination_x", "destination_y"]].to_numpy(dtype=float)
    if not np.isfinite(coordinates).all():
        raise ValueError(f"{path}: a trip destination is not a finite number; the zone join needs EPSG:25832 metres")
    return trips, path


def trip_zones(trips: pd.DataFrame, zones_path) -> pd.Series:
    """The zone id of the destination of every trip (NaN outside every zone), by the production zone assignment of
    ``attach_parking_zones``; it raises when not one destination lies in a zone (a broken join or CRS)."""
    activities = pd.DataFrame({"person_id": "trip", "activity_index": np.arange(len(trips)), "purpose": "trip",
                               "x": trips["destination_x"].to_numpy(dtype=float),
                               "y": trips["destination_y"].to_numpy(dtype=float)})
    return exposure.zone_per_activity(activities, zones_path)


# --------------------------------------------------------------------------------------------------------------- metrics


class MetricSpec(NamedTuple):
    """One metric of an outcome arm: how to compute it and what it is compared with."""

    name: str
    unit: str
    universe: str
    reference: float | None
    reference_source: str
    note: str
    needs_garage_columns: bool
    compute: Callable[[pd.DataFrame], "float | None"]
    size: "Callable[[pd.DataFrame], int] | None" = None   # the universe size (pricing calls) of the row


def _ratio(numerator, denominator):
    """``numerator / denominator`` or None for an empty universe."""
    return None if denominator <= 0 else float(numerator) / float(denominator)


def _calls(frame: pd.DataFrame, mask) -> int:
    return int(frame.loc[mask, "count"].sum())


def expected_garage_share(frame: pd.DataFrame, mask) -> "float | None":
    """``sum(garage_probability_sum) / sum(count)`` over the rows of ``mask`` (rows of early outcomes count with probability
    0), None for an empty selection. Needs a v3 report."""
    return _ratio(frame.loc[mask, "garage_probability_sum"].sum(), _calls(frame, mask))


def _paid_specs(references: References) -> list[MetricSpec]:
    source = f"{references.tables['city_center'].name} row {calibrate.PAID_ROW}"
    note = (f"{CALLS_NOTE}; paid = a PAID_* outcome over all calls at destinations in the zones Ia and Ib, priced time-aware by "
            "the Java model (fee window, free threshold, maximum stay). UPPER BOUND of the chosen-trip paid share: PAID_EXPECTED "
            "calls and zero-cent PAID_* calls count as paid, and the calls include non-chosen alternatives; paying alternatives "
            "are less likely chosen, so the chosen-trip paid share is expected to be lower. SrV: share paying among the "
            "Braunschweig residents with a valid payment answer about their usual city-centre parking, employer lots included. "
            f"{RESIDENT_CAVEAT}")
    universes = (("all_purposes", "all purposes", lambda frame: np.ones(len(frame), dtype=bool)),
                 ("non_home", "without home", lambda frame: (frame["purpose"] != HOME_PURPOSE).to_numpy()),
                 ("other_purposes", "without home, work and education",
                  lambda frame: ~frame["purpose"].isin({HOME_PURPOSE, *COMMUTER_PURPOSES}).to_numpy()))
    specs = []
    for suffix, label, purpose_mask in universes:
        def universe_mask(frame, purpose_mask=purpose_mask, city_ids=CITY_CENTER_ZONE_IDS):
            return frame["zone_id"].isin(city_ids).to_numpy() & purpose_mask(frame)

        def compute(frame, universe_mask=universe_mask):
            paid = frame["outcome"].isin(PAID_OUTCOMES).to_numpy()
            return _ratio(_calls(frame, universe_mask(frame) & paid), _calls(frame, universe_mask(frame)))
        specs.append(MetricSpec(f"paid_share_ia_ib_{suffix}", SHARE_UNIT, f"pricing_calls_ia_ib_{suffix}",
                                references.paid_share, source, f"{note}. Universe: {label}.", False, compute,
                                lambda frame, universe_mask=universe_mask: _calls(frame, universe_mask(frame))))
    return specs


def _garage_specs(references: References) -> list[MetricSpec]:
    target, commuter = references.garage_target, references.commuter_garage
    target_source = (f"{target.table} rows {target.numerator_name} / ({target.numerator_name} + {target.other_name})")
    commuter_source = (f"{commuter.table} class {calibrate.COMMUTER_CLASS}: {commuter.numerator_name} / "
                       f"({commuter.numerator_name} + {commuter.other_name})")

    def other_purposes(frame, city_ids=CITY_CENTER_ZONE_IDS):
        return (frame["zone_id"].isin(city_ids) & ~frame["purpose"].isin({HOME_PURPOSE, *COMMUTER_PURPOSES})).to_numpy()

    def commuters(frame, city_ids=CITY_CENTER_ZONE_IDS):
        return (frame["zone_id"].isin(city_ids) & frame["purpose"].isin(COMMUTER_PURPOSES)).to_numpy()

    garage_note = (
        "calibration target, not validation: lambda is set so that the mean garage probability of the E5 destination universe "
        "(all stays of all modes at destinations in Ia and Ib, no early rules) equals this share. This row is NOT the "
        f"calibration universe: {CALLS_NOTE}, of the other purposes (not home, work, education) in Ia and Ib; early outcomes "
        "(outside fee hours, free within limit, resident, employer free) and stays with a free street (E4) stay in the "
        f"denominator with garage probability 0. {RESIDENT_CAVEAT}")
    commuter_note = (
        "independent check, not used in the calibration. Reference: garage / (garage + street) of the SrV class bs_zentrum "
        "(Oberbezirk Zentrum, wider than the zones Ia and Ib) among street and garage users only (employer lots and other "
        f"places excluded). Model: work and education calls in Ia and Ib; {CALLS_NOTE}; garage share = sum of garage probability "
        "over calls.")
    return [
        MetricSpec("garage_share_ia_ib_other_purposes", SHARE_UNIT, "pricing_calls_ia_ib_other_purposes", target.value,
                   target_source, garage_note, True, lambda frame: expected_garage_share(frame, other_purposes(frame)),
                   lambda frame: _calls(frame, other_purposes(frame))),
        MetricSpec("garage_in_range_share_ia_ib_other_purposes", SHARE_UNIT, "pricing_calls_ia_ib_other_purposes", None,
                   MODEL_ONLY, "diagnostic (primary-method coverage): calls with at least one priced garage within the maximum "
                   f"distance, whether or not the garage acted; {CALLS_NOTE}", True,
                   lambda frame: _ratio(frame.loc[other_purposes(frame), "stays_with_garages_in_range"].sum(),
                                        _calls(frame, other_purposes(frame))),
                   lambda frame: _calls(frame, other_purposes(frame))),
        MetricSpec("paid_expected_share_ia_ib_other_purposes", SHARE_UNIT, "pricing_calls_ia_ib_other_purposes", None,
                   MODEL_ONLY, f"diagnostic: calls priced as an expectation over street and garages (PAID_EXPECTED); {CALLS_NOTE}",
                   False, lambda frame: _ratio(
                       _calls(frame, other_purposes(frame) & (frame["outcome"] == cost.PAID_EXPECTED).to_numpy()),
                       _calls(frame, other_purposes(frame))),
                   lambda frame: _calls(frame, other_purposes(frame))),
        MetricSpec("garage_share_ia_ib_work_education_all_calls", SHARE_UNIT, "pricing_calls_ia_ib_work_education",
                   commuter.value, commuter_source, commuter_note + " This row keeps the EMPLOYER_FREE calls in the denominator.",
                   True, lambda frame: expected_garage_share(frame, commuters(frame)),
                   lambda frame: _calls(frame, commuters(frame))),
        MetricSpec("garage_share_ia_ib_work_education_without_employer_free", SHARE_UNIT,
                   "pricing_calls_ia_ib_work_education_without_employer_free", commuter.value, commuter_source,
                   commuter_note + " This row removes the EMPLOYER_FREE calls (the employer-lot users) from the denominator, the "
                   "closer universe to the street and garage users of the reference.", True,
                   lambda frame: expected_garage_share(
                       frame, commuters(frame) & (frame["outcome"] != cost.EMPLOYER_FREE).to_numpy()),
                   lambda frame: _calls(frame, commuters(frame) & (frame["outcome"] != cost.EMPLOYER_FREE).to_numpy())),
    ]


def _coverage_specs(city_ids) -> list[MetricSpec]:
    return [
        MetricSpec("pricing_calls_total", COUNT_UNIT, "pricing_calls_all", None, MODEL_ONLY,
                   f"{CALLS_NOTE}; all calls of the iteration, in every zone and outside", False,
                   lambda frame: float(frame["count"].sum()), lambda frame: int(frame["count"].sum())),
        MetricSpec("pricing_calls_ia_ib", COUNT_UNIT, "pricing_calls_ia_ib_all_purposes", None, MODEL_ONLY,
                   f"{CALLS_NOTE}; calls at destinations in the zones Ia and Ib", False,
                   lambda frame: float(frame.loc[frame["zone_id"].isin(city_ids), "count"].sum()),
                   lambda frame: int(frame.loc[frame["zone_id"].isin(city_ids), "count"].sum())),
        MetricSpec("no_zone_share_of_pricing_calls", SHARE_UNIT, "pricing_calls_all", None, MODEL_ONLY,
                   f"coverage of the primary path (fallback transparency): {CALLS_NOTE}; share of calls decided NO_ZONE "
                   "(destination outside every zone, free by assumption Z1); a share near 0 or near 1 points to a broken zone "
                   "join or a wrong zone release", False,
                   lambda frame: _ratio(_calls(frame, (frame["outcome"] == cost.NO_ZONE).to_numpy()), int(frame["count"].sum())),
                   lambda frame: int(frame["count"].sum())),
    ]


def _class_specs(references: References, zone_classes: pd.Series) -> list[MetricSpec]:
    specs = []
    for workplace_class in sorted(set(zone_classes)):
        if workplace_class not in references.free_share_by_class.index:
            raise ValueError(f"workplace class {workplace_class!r} of the zones "
                             f"{sorted(zone_classes.index[zone_classes == workplace_class])} has no class row in "
                             f"{references.tables['commute'].name}")
        zone_ids = sorted(zone_classes.index[zone_classes == workplace_class])
        reference = float(references.free_share_by_class[workplace_class])
        source = f"{references.tables['commute'].name} class {workplace_class} {attach.FREE_SHARE_COLUMN}"
        wider = (" (for bs_zentrum the whole Oberbezirk Zentrum, wider than the zones Ia and Ib)"
                 if workplace_class == calibrate.COMMUTER_CLASS else "")
        scope = (f"{CALLS_NOTE}; work and education calls in the street and resident zones of the class {workplace_class} "
                 f"({', '.join(zone_ids)}). The SrV class covers the whole Kreis or Oberbezirk area{wider}, the model universe "
                 "only the listed zones; stays outside every zone are free by assumption Z1, carry no class and are not in the "
                 "universe. Independent check, not validation. Where the run's draw maps this class to a proxy class "
                 "(parking_free_share_proxy_classes of the run's config, ASSUMPTION A1-b) the draw's target is the proxy's "
                 "share, not this row's reference.")
        free_direction = (
            "Two effects lower the model share below the class value by construction: a PAID_* call that costs zero cents counts "
            "as paid, and the stays outside every zone (free by assumption Z1) are left out of the universe, so a negative "
            "difference is expected by construction from these two effects alone; against them the draw frees the in-zone persons "
            "with the class share and the other early rules (resident, outside fee hours, free within limit) add free calls "
            "that raise the model share, so the sign of the difference is not a result by itself.")
        employer_direction = (
            "The draw frees a person with the class share of the class of the person's FIRST paid-zone work or education "
            "activity, so a difference near zero is the mechanism working; a clearly negative one means the draw does not reach "
            "these calls (for example persons whose first zone lies in another class), a positive one that the proxy class or the "
            "shift of the run's config differs from the table value.")

        def selection(frame, zone_ids=tuple(zone_ids)):
            return (frame["zone_id"].isin(zone_ids) & frame["purpose"].isin(COMMUTER_PURPOSES)).to_numpy()

        specs.append(MetricSpec(
            f"free_share_work_education_class_{workplace_class}", SHARE_UNIT,
            f"pricing_calls_work_education_zones_of_class_{workplace_class}", reference, source,
            f"free = outcome EMPLOYER_FREE, RESIDENT_FREE, OUTSIDE_FEE_HOURS or FREE_WITHIN_LIMIT; {free_direction} {scope}", False,
            lambda frame, selection=selection: _ratio(
                _calls(frame, selection(frame) & frame["outcome"].isin(FREE_OUTCOMES).to_numpy()),
                _calls(frame, selection(frame))),
            lambda frame, selection=selection: _calls(frame, selection(frame))))
        specs.append(MetricSpec(
            f"employer_free_share_work_education_class_{workplace_class}", SHARE_UNIT,
            f"pricing_calls_work_education_zones_of_class_{workplace_class}", reference, source,
            f"mechanism check of the free-parking draw (EMPLOYER_FREE calls only). {employer_direction} {scope}", False,
            lambda frame, selection=selection: _ratio(
                _calls(frame, selection(frame) & (frame["outcome"] == cost.EMPLOYER_FREE).to_numpy()),
                _calls(frame, selection(frame))),
            lambda frame, selection=selection: _calls(frame, selection(frame))))
    return specs


def outcome_specs(references: References, zone_classes: pd.Series, city_ids=CITY_CENTER_ZONE_IDS) -> list[MetricSpec]:
    """Every metric computed from an outcome report, in the order of the table."""
    return [*_paid_specs(references), *_garage_specs(references), *_coverage_specs(city_ids),
            *_class_specs(references, zone_classes)]


def _row(arm, spec: MetricSpec, model: "float | None", note: str, size: "int | None" = None) -> dict:
    reference = spec.reference
    delta = None if model is None or reference is None or spec.unit != SHARE_UNIT else 100.0 * (model - reference)
    return {"arm": arm, "metric": spec.name, "unit": spec.unit, "universe": spec.universe,
            "universe_size": np.nan if size is None else float(size),
            "model": np.nan if model is None else model, "reference": np.nan if reference is None else reference,
            "delta_pp": np.nan if delta is None else delta, "reference_source": spec.reference_source, "universe_note": note}


def garage_options_inert(frame: pd.DataFrame, version: "int | None") -> bool:
    """True for a v3 report whose two garage columns are 0 in every row: no garage option acted (options off, or no garage in
    range of any priced stay). A measured 0 of the garage rows must not be read as a model result without this flag."""
    return bool(version == 3 and (frame["garage_probability_sum"] == 0).all() and (frame["stays_with_garages_in_range"] == 0).all())


def outcome_rows(arm: str, specs: list[MetricSpec], frame: "pd.DataFrame | None", version: "int | None",
                 iterations=None) -> list[dict]:
    """The outcome metrics of one arm; ``frame`` None is an arm without a report (empty values with the reason).

    ``iterations`` are the iteration numbers of the (pooled) report; every note of a row names them. A v3 report without any
    garage figure is logged as a warning and flagged in the notes of the garage rows."""
    rows = []
    prefix = f"{iteration_note(iterations)} " if frame is not None and iterations else ""
    inert = frame is not None and garage_options_inert(frame, version)
    if inert:
        log.warning("[parking-compare] %s: %s; the garage rows are measured zeros, not a result of garage options", arm,
                    GARAGE_INERT_NOTE)
    for spec in specs:
        if frame is None:
            rows.append(_row(arm, spec, None, f"{NO_REPORT_NOTE}. {spec.note}"))
            continue
        size = spec.size(frame) if spec.size is not None else None
        if spec.needs_garage_columns and version == 2:
            rows.append(_row(arm, spec, None, f"{prefix}{V2_REPORT_NOTE}. {spec.note}", size))
            continue
        value = spec.compute(frame)
        note = spec.note if value is not None else f"{EMPTY_UNIVERSE_NOTE} {spec.note}"
        if inert and spec.needs_garage_columns:
            note = f"{GARAGE_INERT_NOTE}. {note}"
        rows.append(_row(arm, spec, value, f"{prefix}{note}", size))
        if value is None:
            log.warning("[parking-compare] %s: no pricing call in the universe of %s", arm, spec.name)
    return rows


def trip_rows(arm: str, trips: pd.DataFrame, zone_id: pd.Series, city_ids=CITY_CENTER_ZONE_IDS) -> list[dict]:
    """The mode-share metrics of one arm from its chosen trips and the zones of their destinations."""
    car = (trips["mode"] == CAR_MODE).to_numpy()
    in_zone = zone_id.notna().to_numpy()
    in_city = zone_id.isin(city_ids).to_numpy()
    note = ("chosen trips of the run's final iteration (eqasim_trips), mode car only (car_passenger excluded); destination in a "
            "zone polygon of the committed release, EPSG:25832; model only, no reference")
    universes = (("all_trips", np.ones(len(trips), dtype=bool), "all chosen trips"),
                 ("ending_in_zones", in_zone, "chosen trips ending in any parking zone"),
                 ("ending_outside_zones", ~in_zone, "chosen trips ending outside every parking zone"),
                 ("ending_in_ia_ib", in_city, "chosen trips ending in the zones Ia and Ib"))
    rows = []
    for suffix, mask, label in universes:
        spec = MetricSpec(f"car_trip_share_{suffix}", SHARE_UNIT, f"chosen_trips_{suffix}", None, MODEL_ONLY, f"{label}; {note}",
                          False, lambda frame: None)
        rows.append(_row(arm, spec, _ratio(int((car & mask).sum()), int(mask.sum())),
                         spec.note if mask.any() else f"{EMPTY_UNIVERSE_NOTE} {spec.note}", int(mask.sum())))
    for name, value, label in (("trips_total", len(trips), "all chosen trips"),
                               ("trips_ending_in_zones", int(in_zone.sum()), "chosen trips ending in any parking zone"),
                               ("trips_ending_in_ia_ib", int(in_city.sum()), "chosen trips ending in the zones Ia and Ib"),
                               ("car_trips_ending_in_ia_ib", int((car & in_city).sum()), "chosen car trips ending in Ia and Ib")):
        spec = MetricSpec(name, COUNT_UNIT, name, None, MODEL_ONLY, f"{label}; {note}", False, lambda frame: None)
        rows.append(_row(arm, spec, float(value), spec.note, int(value)))
    return rows


# ------------------------------------------------------------------------------------------------------ the comparison


class ArmSpec(NamedTuple):
    """One model arm: its label, the output directory of its MATSim run and whether it has a parking outcome report."""

    label: str
    run_output: Path
    with_outcomes: bool


class Comparison(NamedTuple):
    table: pd.DataFrame
    deltas: pd.DataFrame
    provenance: dict


def _input_record(path) -> dict:
    return {"path": Path(path).resolve().as_posix(), "sha256": calibrate.file_sha256(path)}


def pool_outcome_reports(reports) -> tuple[pd.DataFrame, int]:
    """Sum the outcome reports ``[(frame, version), ...]`` of several iterations cell by cell (outcome, zone id, purpose).

    One report is returned unchanged. The pooled counts are pricing calls summed over the iterations: only the agents that
    replan in an iteration price their car alternatives, so one iteration is a small sample and pooling enlarges it (the
    calls of different iterations are not independent: the same agents can replan more than once). The share column is
    recomputed from the pooled counts. ``ValueError`` when the reports have different versions.
    """
    versions = {version for _, version in reports}
    if len(versions) != 1:
        raise ValueError(f"the outcome reports to pool have different versions {sorted(versions)}")
    version = versions.pop()
    if len(reports) == 1:
        return reports[0][0], version
    keys = ["outcome", "zone_id", "purpose"]
    stacked = pd.concat([frame for frame, _ in reports], ignore_index=True)
    sums = ["count", "garage_probability_sum", "stays_with_garages_in_range"] if version == 3 else ["count"]
    pooled = stacked.groupby(keys, sort=True, as_index=False)[sums].sum()
    pooled["share"] = pooled["count"] / pooled["count"].sum()
    if version == 2:
        pooled["garage_probability_sum"] = np.nan
        pooled["stays_with_garages_in_range"] = np.nan
    return pooled[list(stacked.columns)], version


def compare(arms, *, data_path, zones_geojson, iterations=None, city_center_zone_ids=CITY_CENTER_ZONE_IDS) -> Comparison:
    """Compare the arms with the SrV references; returns the metric table, the per-arm delta table and the provenance.

    ``iterations`` selects the outcome reports as a sequence of iteration numbers, pooled by ``pool_outcome_reports``
    (default: the last ``ITERS/it.N`` of each arm alone). Raises ``ValueError`` for no
    arm, a duplicate or malformed label, a report that fails ``read_parking_outcomes``, a report zone without a tariff row,
    an unusable trips file or references, and ``FileNotFoundError`` for a missing report or trips file.
    """
    arms = list(arms)
    if not arms:
        raise ValueError("compare needs at least one arm")
    labels = [arm.label for arm in arms]
    for label in labels:
        if not ARM_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"arm label {label!r} must consist of letters, digits, underscore, dot and hyphen")
    if len(set(labels)) != len(labels):
        raise ValueError(f"duplicate arm label in {labels}")
    data_path, zones_geojson = Path(data_path), Path(zones_geojson)
    references = read_references(data_path)
    tariffs_path = data_path / TARIFFS_RELATIVE
    tariffs = parking_zones.load_tariffs(tariffs_path)
    zone_classes = free_draw_zone_classes(tariffs)
    specs = outcome_specs(references, zone_classes, city_center_zone_ids)

    rows, arm_provenance = [], {}
    for arm in arms:
        run_output = Path(arm.run_output)
        if not run_output.is_dir():
            raise FileNotFoundError(f"run output of arm {arm.label} not found: {run_output}")
        record = {"run_output": run_output.resolve().as_posix(), "iterations": None, "last_iteration": None,
                  "outcome_reports": None, "outcome_report_version": None}
        frame, version = None, None
        if arm.with_outcomes:
            last = last_iteration(run_output)
            chosen = [last] if iterations is None else sorted(set(iterations))
            reports = [outcome_report_path(run_output, number) for number in chosen]
            for report in reports:
                if not report.is_file():
                    raise FileNotFoundError(f"arm {arm.label}: no parking outcome report {report}")
            frame, version = pool_outcome_reports([read_parking_outcomes(report) for report in reports])
            unknown = sorted(set(frame.loc[frame["outcome"] != cost.NO_ZONE, "zone_id"]) - set(tariffs["zone_id"]))
            if unknown:
                raise ValueError(f"{reports[0].parent.parent}: zone id(s) {unknown} have no row in the tariff table "
                                 f"{tariffs_path}; the run and the committed release must belong together")
            if chosen != [last]:
                log.warning("[parking-compare] arm %s: iteration(s) %s are not the last iteration (%d) alone; eqasim_trips is "
                            "written for the final iteration only, so the trip metrics and the outcome metrics do not "
                            "describe the same iteration", arm.label, chosen, last)
            record.update(iterations=chosen, last_iteration=last, outcome_report_version=version,
                          outcome_reports=[{"path": report.resolve().as_posix(), "sha256": calibrate.file_sha256(report)}
                                           for report in reports])
            log.info("[parking-compare] arm %s: outcome report v%d, iteration(s) %s, %d pricing calls in %d rows", arm.label,
                     version, chosen, int(frame["count"].sum()), len(frame))
        rows.extend(outcome_rows(arm.label, specs, frame, version, record["iterations"]))
        trips, trips_path = read_trips(run_output)
        zone_id = trip_zones(trips, zones_geojson)
        record.update(trips_file=trips_path.resolve().as_posix(), trips_sha256=calibrate.file_sha256(trips_path),
                      trips_rows=int(len(trips)))
        rows.extend(trip_rows(arm.label, trips, zone_id, city_center_zone_ids))
        arm_provenance[arm.label] = record
        log.info("[parking-compare] arm %s: %d chosen trips, %.1f %% end in a zone", arm.label, len(trips),
                 100.0 * float(zone_id.notna().mean()))

    table = pd.DataFrame(rows, columns=list(TABLE_COLUMNS))
    provenance = {
        "generated_on": datetime.date.today().isoformat(), "code_state": calibrate.git_state(),
        "script": "scripts/parking/compare_parking_targets.py",
        "iterations_option": None if iterations is None else sorted(set(iterations)),
        "city_center_zone_ids": list(city_center_zone_ids),
        "inputs": {"srv2023_city_center_parking": _input_record(references.tables["city_center"]),
                   "srv2023_commute_parking_by_workplace_class": _input_record(references.tables["commute"]),
                   "zones": _input_record(zones_geojson), "tariffs": _input_record(tariffs_path)},
        "arms": arm_provenance,
        "note": "a comparison of model output with SrV references, not a validation; every row of the table names its universe"}
    return Comparison(table, arm_deltas(table, labels), provenance)


def arm_deltas(table: pd.DataFrame, labels) -> pd.DataFrame:
    """Every arm against every EARLIER arm (in the given order) for the share metrics that have a model value in both."""
    shares = table[table["unit"] == SHARE_UNIT]
    indexed = shares.set_index(["arm", "metric"])
    rows = []
    for position, arm in enumerate(labels):
        for baseline in labels[:position]:
            for metric in shares.loc[shares["arm"] == arm, "metric"]:
                model, baseline_model = indexed.loc[(arm, metric), "model"], indexed.loc[(baseline, metric), "model"]
                if math.isnan(model) or math.isnan(baseline_model):
                    continue
                rows.append({"arm": arm, "baseline_arm": baseline, "metric": metric,
                             "universe": indexed.loc[(arm, metric), "universe"],
                             "n_model": indexed.loc[(arm, metric), "universe_size"],
                             "n_baseline": indexed.loc[(baseline, metric), "universe_size"], "model": model,
                             "baseline_model": baseline_model, "delta_pp": 100.0 * (model - baseline_model)})
    return pd.DataFrame(rows, columns=list(DELTA_COLUMNS))


# ----------------------------------------------------------------------------------------------------------------- output


def _format(value, decimals: int, as_count: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if as_count:
        return str(int(round(value)))
    return f"{round(float(value), decimals) + 0.0:.{decimals}f}"   # "+ 0.0" turns -0.0 into 0.0


def table_text(table: pd.DataFrame) -> pd.DataFrame:
    """The metric table as text cells: shares with six decimals, counts as whole numbers, differences with four decimals."""
    out = table.copy()
    counts = (table["unit"] == COUNT_UNIT).to_numpy()
    out["universe_size"] = [_format(v, SHARE_DECIMALS, True) for v in table["universe_size"]]
    out["model"] = [_format(v, SHARE_DECIMALS, c) for v, c in zip(table["model"], counts)]
    out["reference"] = [_format(v, SHARE_DECIMALS) for v in table["reference"]]
    out["delta_pp"] = [_format(v, DELTA_DECIMALS) for v in table["delta_pp"]]
    return out


def delta_text(deltas: pd.DataFrame) -> pd.DataFrame:
    out = deltas.copy()
    for column in ("n_model", "n_baseline"):
        out[column] = [_format(v, SHARE_DECIMALS, True) for v in deltas[column]]
    for column in ("model", "baseline_model"):
        out[column] = [_format(v, SHARE_DECIMALS) for v in deltas[column]]
    out["delta_pp"] = [_format(v, DELTA_DECIMALS) for v in deltas["delta_pp"]]
    return out


def write_comparison(result: Comparison, out_path, *, overwrite: bool = False) -> tuple[Path, Path, Path]:
    """Write the table, the delta table (``<out>_arm_deltas.csv``) and the provenance (``<out>_provenance.json``); an existing
    file is replaced only with ``overwrite``. Returns the three paths."""
    out_path = Path(out_path)
    delta_path = out_path.with_name(out_path.stem + "_arm_deltas.csv")
    provenance_path = out_path.with_name(out_path.stem + "_provenance.json")
    existing = [path for path in (out_path, delta_path, provenance_path) if path.exists()]
    if existing and not overwrite:
        raise SystemExit(f"{', '.join(str(path) for path in existing)} exists; pass --overwrite to replace it")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table_text(result.table).to_csv(out_path, index=False, lineterminator="\n", encoding="ascii")
    delta_text(result.deltas).to_csv(delta_path, index=False, lineterminator="\n", encoding="ascii")
    provenance_path.write_bytes((json.dumps(result.provenance, indent=2, sort_keys=True) + "\n").encode("ascii"))
    return out_path, delta_path, provenance_path


def parse_iterations(text: str) -> list[int]:
    """``"7"`` -> [7]; ``"3-10"`` -> [3, ..., 10] (inclusive). ``argparse.ArgumentTypeError`` for anything else."""
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", text.strip())
    if not match or (match.group(2) is not None and int(match.group(2)) < int(match.group(1))):
        raise argparse.ArgumentTypeError(f"{text!r}: expected an iteration N or an inclusive range FIRST-LAST (FIRST <= LAST)")
    first = int(match.group(1))
    return [first] if match.group(2) is None else list(range(first, int(match.group(2)) + 1))


class _ArmAction(argparse.Action):
    """Collect ``--arm`` and ``--arm-without-outcomes`` into ONE list in command-line order (the order of the deltas)."""

    def __call__(self, parser, namespace, values, option_string=None):
        arms = list(getattr(namespace, self.dest) or [])
        label, separator, directory = values.partition("=")
        if not separator or not label or not directory:
            parser.error(f"{option_string} expects LABEL=RUN_OUTPUT_DIRECTORY, got {values!r}")
        arms.append(ArmSpec(label, Path(directory), option_string == "--arm"))
        setattr(namespace, self.dest, arms)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", dest="arms", action=_ArmAction, metavar="LABEL=RUN_OUTPUT",
                        help="an arm with a parking outcome report: the MATSim output directory holding ITERS/ and eqasim_trips")
    parser.add_argument("--arm-without-outcomes", dest="arms", action=_ArmAction, metavar="LABEL=RUN_OUTPUT",
                        help="an arm that prices no zones (OFF, LEGACY): mode shares only")
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH, help="data directory with the committed inputs")
    parser.add_argument("--zones-geojson", type=Path, default=None,
                        help=f"the zone release (default: <data-path>/{ZONES_RELATIVE})")
    parser.add_argument("--iteration", type=parse_iterations, default=None, metavar="N|FIRST-LAST",
                        help="iteration of the outcome reports, or an inclusive range that is pooled (default: the last one)")
    parser.add_argument("--out", type=Path, required=True, help="the comparison table (csv); two sibling files are written")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")
    args = parser.parse_args(argv)
    if not args.arms:
        parser.error("at least one --arm or --arm-without-outcomes is required")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    zones_geojson = args.zones_geojson or args.data_path / ZONES_RELATIVE
    required = [("run output of arm " + arm.label, arm.run_output) for arm in args.arms]
    required += [("zone release", zones_geojson), ("tariff table", args.data_path / TARIFFS_RELATIVE),
                 ("SrV city-centre table", args.data_path / CITY_CENTER_RELATIVE),
                 ("SrV commute table", args.data_path / COMMUTE_RELATIVE)]
    for label, path in required:
        if not Path(path).exists():
            raise SystemExit(f"{label} not found: {path}")
    result = compare(args.arms, data_path=args.data_path, zones_geojson=zones_geojson, iterations=args.iteration)
    for path in write_comparison(result, args.out, overwrite=args.overwrite):
        log.info("[parking-compare] wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
