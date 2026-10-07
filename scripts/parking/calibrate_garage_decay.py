"""Calibrate the decay length of the garage options on a plans file (parking cost zones v2, spec Amendment E5, issue #436).

A curation and analysis aid, never a pipeline stage. The garage options of a car stay in a paid zone have the weights
street 1 and garage g ``exp(-d_g / lambda)`` (ASSUMPTION G1, straight-line distance ``d_g`` in metres in EPSG:25832, only
garages within the maximum distance D_max, ASSUMPTION G2). The decay length lambda is CALIBRATED, not estimated (ASSUMPTION
G3): it is set so that the mean garage probability of the destination universe equals the SrV 2023 target.

Definitions (the committed calibration table records them, so that its number can be read without this file):

* Destination universe: every main activity of the selected plans (the activities of type ``* interaction`` that the router
  inserts are no destinations) whose type is none of ``HOME_AND_COMMUTER_PURPOSES`` (home, work, education) and that lies in
  one of the calibration zones ``CALIBRATION_ZONE_IDS`` (``bs_zone_ia``, ``bs_zone_ib``), whatever the mode of the trip that
  ends there. The zone of an activity is the one ``braunschweig.parking.attach.attach_parking_zones`` assigns.
* Garage probability of a destination: ``sum(w) / (1 + sum(w))`` over the priced garages within D_max, the weights above.
  The price does not enter (E3), the early rules of a stay (parkingFree, resident, fee window) and E4 are not applied: the
  quantity is the choice share of the garage options, as the calibration target is a share of places, not of payments.
* Target: ``share(garage_large_lot) / (share(garage_large_lot) + share(street))`` of the SrV 2023 table
  ``srv2023_city_center_parking`` among the Braunschweig residents who park on the street or in a garage or large lot when
  they drive to the city centre (employer lots and other places leave the denominator because the model handles them by
  the employer-free draw), READ from the committed table, never typed here. Universe caveat: SrV asks residents about their
  usual place, the model averages over the destinations of all persons of the plans.
* Search: bisection on lambda in ``[--lambda-min-m, --lambda-max-m]`` (default 10 to 5000 m), which stops when the mean
  garage probability is within ``--tolerance`` (default 0.0005) of the target; the mean probability rises with lambda, so
  the search fails (``ValueError``) when the target lies outside the range at its two ends. The reported lambda is rounded
  to 0.01 m and its achieved mean recomputed.
* Independent checks on the SAME plans, reported as numbers and never as validation: (1) the mean garage probability of the
  work and education activities in the calibration zones against ``share(garage_large_lot) / (share(garage_large_lot) +
  share(street))`` of the class ``bs_zentrum`` of ``srv2023_commute_parking_by_workplace_class`` (0.464 in the committed
  table); (2) the share of universe stays whose zone has a street product with a positive hourly rate (zone level and blind
  to the time of day and the stay length, an upper bound of the paid share of a stay) next to ``paid_share_overall`` of
  ``srv2023_city_center_parking`` (0.8333, among the respondents with a valid payment answer). Both references are
  quantities of residents, the model averages over destinations.

Fallback transparency: the log and the table report how many universe stays have a garage within D_max (the share whose
probability can be positive at all); a target that the garages cannot reach fails instead of returning the end of the range.

Output: the committed table ``parking_garage_decay_calibration_2026.csv`` (long format ``quantity,value,unit,note`` under a
header that names the inputs with their SHA-256, the universe, the target derivation and the code state). The script
refuses to replace an existing table unless ``--overwrite`` is given. Run it on the plans of the reference scenario only
(the server run of Task 5 of issue #436); a plans file of another scenario gives another lambda.

Usage (from the repository root)::

    python scripts/parking/calibrate_garage_decay.py --plans <population.xml.gz> [--data-path eqasim-data/data]
        [--out eqasim-data/data/braunschweig/parking/parking_garage_decay_calibration_2026.csv] [--overwrite]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import logging
import math
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import cost, garages as parking_garages, zones as parking_zones  # noqa: E402

log = logging.getLogger("calibrate_garage_decay")

DEFAULT_DATA_PATH = REPO / "eqasim-data" / "data"
ZONES_RELATIVE = "braunschweig/parking/parking_zones_2026.geojson"
TARIFFS_RELATIVE = "braunschweig/parking/parking_tariffs_2026.csv"
GARAGES_RELATIVE = "braunschweig/parking/parking_garages_2026.geojson"
CITY_CENTER_RELATIVE = "braunschweig/srv/srv2023_city_center_parking.csv"
COMMUTE_RELATIVE = "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv"
TABLE_RELATIVE = "braunschweig/parking/parking_garage_decay_calibration_2026.csv"
EXPOSURE_SCRIPT = REPO / "scripts" / "curation" / "parking_zones_2026" / "count_zone_exposure.py"

#: Spec E5: the calibration zones, the two fee zones of the Braunschweig city centre.
CALIBRATION_ZONE_IDS = ("bs_zone_ia", "bs_zone_ib")
#: Spec E5: the destination universe leaves out these activity types; work and education are the commuter purposes of the
#: independent check (cost.COMMUTER_PURPOSES).
HOME_AND_COMMUTER_PURPOSES = frozenset({cost.HOME_PURPOSE, *cost.COMMUTER_PURPOSES})
#: The rows of the SrV tables the targets are read from.
GARAGE_ROW, STREET_ROW, PAID_ROW = "garage_large_lot", "street", "paid_share_overall"
COMMUTER_CLASS = "bs_zentrum"
DEFAULT_LAMBDA_MIN_M, DEFAULT_LAMBDA_MAX_M = 10.0, 5000.0
DEFAULT_TOLERANCE = 0.0005
#: The bisection halves an interval of at most 5000 m: 80 halvings are far below any tolerance, so reaching this bound
#: without the tolerance means the target is not met by a continuous function (reported as a failure).
MAXIMUM_ITERATIONS = 80
LAMBDA_DECIMALS = 2
TABLE_COLUMNS = ("quantity", "value", "unit", "note")


class Target(NamedTuple):
    """A share derived from two rows of an SrV table: ``numerator / (numerator + denominator_other)``."""

    value: float
    numerator_name: str
    numerator: float
    other_name: str
    other: float
    table: str


class Calibration(NamedTuple):
    decay_m: float
    achieved: float
    iterations: int
    low_end_mean: float
    high_end_mean: float


def file_sha256(path) -> str:
    """SHA-256 of a file; text files are hashed with LF line endings (as git stores them), binary ``.gz`` as they are."""
    data = Path(path).read_bytes()
    if Path(path).suffix != ".gz":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _read_srv_table(path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"SrV table missing: {path}")
    return pd.read_csv(path, comment="#", dtype={"workplace_class": str, "level": str})


def _positive_share(value, what: str) -> float:
    if not (isinstance(value, (int, float, np.floating)) and math.isfinite(value) and 0.0 < value <= 1.0):
        raise ValueError(f"{what}: a share in (0, 1] is required, got {value!r}")
    return float(value)


def read_city_center_target(path) -> Target:
    """The calibration target of spec E5 from ``srv2023_city_center_parking``: ``garage_large_lot / (garage_large_lot +
    street)``. Raises ``ValueError`` when a row is missing, duplicated or no share in (0, 1]."""
    table = _read_srv_table(path)
    if "parking_type" not in table.columns or "share" not in table.columns:
        raise ValueError(f"{path}: columns parking_type and share are required, found {list(table.columns)}")
    shares = {}
    for name in (GARAGE_ROW, STREET_ROW):
        rows = table.loc[table["parking_type"] == name, "share"]
        if len(rows) != 1:
            raise ValueError(f"{path}: expected exactly one row {name!r}, found {len(rows)}")
        shares[name] = _positive_share(float(rows.iloc[0]), f"{path} row {name}")
    value = shares[GARAGE_ROW] / (shares[GARAGE_ROW] + shares[STREET_ROW])
    return Target(value, GARAGE_ROW, shares[GARAGE_ROW], STREET_ROW, shares[STREET_ROW], Path(path).name)


def read_paid_share_reference(path) -> float:
    """``paid_share_overall`` of ``srv2023_city_center_parking`` (the independent paid-share reference)."""
    table = _read_srv_table(path)
    rows = table.loc[table["parking_type"] == PAID_ROW, "share"]
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one row {PAID_ROW!r}, found {len(rows)}")
    return _positive_share(float(rows.iloc[0]), f"{path} row {PAID_ROW}")


def read_commuter_reference(path) -> Target:
    """The commuter garage share of the class ``bs_zentrum`` of ``srv2023_commute_parking_by_workplace_class`` among street
    and garage users: ``share_garage_large_lot / (share_garage_large_lot + share_street)`` (spec E5, not used in the
    calibration)."""
    table = _read_srv_table(path)
    rows = table[(table["workplace_class"] == COMMUTER_CLASS) & (table["level"] == "class")]
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one class row {COMMUTER_CLASS!r}, found {len(rows)}")
    garage = _positive_share(float(rows["share_garage_large_lot"].iloc[0]), f"{path} share_garage_large_lot")
    street = _positive_share(float(rows["share_street"].iloc[0]), f"{path} share_street")
    return Target(garage / (garage + street), "share_garage_large_lot", garage, "share_street", street, Path(path).name)


def _load_exposure_module():
    """The plans reader of the earlier exposure checks (``count_zone_exposure.py``), loaded from its file: it streams the
    selected plans and assigns the zones with the production function, so no plan or zone logic is copied here."""
    spec = importlib.util.spec_from_file_location("count_zone_exposure_for_calibration", EXPOSURE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def garage_distances_m(activities: pd.DataFrame, garage_x_m: np.ndarray, garage_y_m: np.ndarray) -> np.ndarray:
    """Straight-line distances in metres (EPSG:25832) of every activity (rows) to every garage (columns):
    ``sqrt(dx * dx + dy * dy)``, as ``cost.garage_options_in_range``. Pure."""
    dx = activities["x"].to_numpy(dtype=float)[:, None] - garage_x_m[None, :]
    dy = activities["y"].to_numpy(dtype=float)[:, None] - garage_y_m[None, :]
    return np.sqrt(dx * dx + dy * dy)


def mean_garage_probability(distances_m: np.ndarray, decay_m: float, max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M) -> float:
    """Mean over the rows of ``sum(w) / (1 + sum(w))`` with ``w = exp(-d / decay_m)`` for the garages within
    ``max_distance_m`` (the street has the weight 1; a garage beyond the maximum distance has weight 0). ``distances_m`` has one row
    per destination and one column per priced garage. Raises ``ValueError`` for an empty universe or a non-positive decay."""
    if distances_m.shape[0] == 0:
        raise ValueError("the destination universe is empty: no activity to average the garage probability over")
    if not (math.isfinite(decay_m) and decay_m > 0):
        raise ValueError(f"decay_m must be a positive finite number of metres, got {decay_m!r}")
    if distances_m.shape[1] == 0:
        return 0.0
    weights = np.where(distances_m <= max_distance_m, np.exp(-distances_m / decay_m), 0.0)
    total = weights.sum(axis=1)
    return float(np.mean(total / (1.0 + total)))


def calibrate_decay(distances_m: np.ndarray, target: float, *, max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M,
                    lambda_min_m: float = DEFAULT_LAMBDA_MIN_M, lambda_max_m: float = DEFAULT_LAMBDA_MAX_M,
                    tolerance: float = DEFAULT_TOLERANCE) -> Calibration:
    """Bisection on lambda for ``mean_garage_probability(distances_m, lambda) == target`` within ``tolerance``.

    The mean rises with lambda (the weights rise), so the interval ``[lambda_min_m, lambda_max_m]`` brackets the solution
    exactly when the mean at its two ends encloses the target; otherwise ``ValueError`` names both end values (the target
    is unreachable with these garages and this universe, and no end of the range is returned as an answer). The result
    is rounded to ``LAMBDA_DECIMALS`` decimals and its mean recomputed; ``ValueError`` when the rounded value misses
    ``tolerance``. ``ValueError`` as well when ``MAXIMUM_ITERATIONS`` halvings do not reach the tolerance.
    """
    if not 0.0 < target < 1.0:
        raise ValueError(f"the target must lie in (0, 1), got {target}")
    if not 0.0 < lambda_min_m < lambda_max_m or tolerance <= 0:
        raise ValueError(f"need 0 < lambda_min_m < lambda_max_m and tolerance > 0, got {lambda_min_m}, {lambda_max_m}, "
                         f"{tolerance}")
    low_mean = mean_garage_probability(distances_m, lambda_min_m, max_distance_m)
    high_mean = mean_garage_probability(distances_m, lambda_max_m, max_distance_m)
    if not low_mean - tolerance <= target <= high_mean + tolerance:
        raise ValueError(f"the target {target:.4f} is unreachable: the mean garage probability is {low_mean:.4f} at "
                         f"lambda = {lambda_min_m} m and {high_mean:.4f} at lambda = {lambda_max_m} m")
    low, high, iterations = lambda_min_m, lambda_max_m, 0
    while True:
        middle = 0.5 * (low + high)
        mean = mean_garage_probability(distances_m, middle, max_distance_m)
        iterations += 1
        if abs(mean - target) <= tolerance:
            break
        if iterations >= MAXIMUM_ITERATIONS:
            raise ValueError(f"the bisection did not reach the tolerance {tolerance} in {MAXIMUM_ITERATIONS} halvings "
                             f"(mean {mean:.6f} at lambda = {middle:.4f} m, target {target:.6f})")
        if mean < target:
            low = middle
        else:
            high = middle
    decay_m = round(middle, LAMBDA_DECIMALS)
    achieved = mean_garage_probability(distances_m, decay_m, max_distance_m)
    if abs(achieved - target) > tolerance:
        raise ValueError(f"lambda rounded to {decay_m} m gives the mean {achieved:.6f}, outside the tolerance {tolerance} of "
                         f"the target {target:.6f}")
    return Calibration(decay_m, achieved, iterations, low_mean, high_mean)


def universe_masks(activities: pd.DataFrame, zone_id: pd.Series, calibration_zone_ids=CALIBRATION_ZONE_IDS) -> tuple:
    """Boolean masks over ``activities`` (columns ``purpose``, ``x``, ``y``; ``zone_id`` aligned to its index): the
    destination universe of spec E5 (inside a calibration zone, purpose not home, work or education) and the commuter
    activities of the independent check (inside a calibration zone, purpose work or education)."""
    inside = zone_id.isin(list(calibration_zone_ids)).to_numpy()
    purpose = activities["purpose"].astype(str)
    universe = inside & ~purpose.isin(HOME_AND_COMMUTER_PURPOSES).to_numpy()
    commuters = inside & purpose.isin(cost.COMMUTER_PURPOSES).to_numpy()
    return universe, commuters


def street_paid_share(zone_id: pd.Series, tariffs: pd.DataFrame) -> float:
    """Share of the activities (``zone_id`` of each, all inside a zone) whose zone has a street product with a positive hourly
    rate: zone level, blind to the time of day, the purpose and the stay length (an upper bound of the paid share of a stay)."""
    rate = tariffs.set_index("zone_id")["hourly_rate_eur"]
    if zone_id.empty:
        raise ValueError("no activity to count the street-paid share over")
    missing = sorted(set(zone_id) - set(rate.index))
    if missing:
        raise ValueError(f"zones {missing} have no tariff row")
    return float((pd.to_numeric(rate.reindex(zone_id).fillna(0.0)) > 0).mean())


def _git_state() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
                                timeout=20).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True,
                               text=True, check=True, timeout=20).stdout.strip()
        return commit + (" (working tree has uncommitted changes)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown (no git checkout)"


def table_text(*, inputs: dict, universe_size: int, persons: int, with_garage: int, priced_garages: int, listed_garages: int,
               target: Target, calibration: Calibration, tolerance: float, lambda_min_m: float, lambda_max_m: float,
               max_distance_m: float, commuter_mean: float, commuter_count: int, commuter_reference: Target,
               paid_share: float, paid_reference: float, generated_on: str, code_state: str) -> str:
    """The calibration table as text: the provenance header and the long-format rows ``TABLE_COLUMNS``."""
    header = [
        "# Table: parking_garage_decay_calibration_2026.csv",
        "# Calibration of the decay length lambda of the garage options (parking cost zones v2, spec Amendment E5, ASSUMPTIONS "
        "G1 to G3, issue #436).",
        f"# Generated by scripts/parking/calibrate_garage_decay.py on {generated_on}; code state {code_state}.",
        "# Inputs (SHA-256; text files with LF line endings, .gz files as stored):",
        *[f"#   {name}: {path} sha256={digest}" for name, (path, digest) in inputs.items()],
        f"# Universe: the main activities of the selected plans ({persons} persons) inside {', '.join(CALIBRATION_ZONE_IDS)} "
        f"whose type is none of {', '.join(sorted(HOME_AND_COMMUTER_PURPOSES))} (all modes, destination universe): "
        f"{universe_size} activities, {with_garage} of them with at least one priced garage within {max_distance_m:g} m.",
        f"# Garages: {priced_garages} priced of {listed_garages} listed in the dataset; straight-line distances in EPSG:25832.",
        "# Quantity: the mean over the universe of sum(w) / (1 + sum(w)), w = exp(-d / lambda) of the priced garages within "
        "the maximum distance (street weight 1; no price, no early rule, no E4).",
        f"# Target: {target.numerator_name} / ({target.numerator_name} + {target.other_name}) = {target.numerator} / "
        f"({target.numerator} + {target.other}) = {target.value:.6f} of {target.table} (Braunschweig residents who park on the "
        "street or in a garage or large lot when they drive to the city centre). A calibration target, no validation. "
        "Universe caveat: SrV asks residents about their usual place, the model averages over destinations.",
        f"# Search: bisection on lambda in [{lambda_min_m:g}, {lambda_max_m:g}] m to a tolerance of {tolerance:g} in the mean "
        f"probability, {calibration.iterations} halvings; the mean is {calibration.high_end_mean:.4f} at the upper end and "
        f"{calibration.low_end_mean:.4f} at the lower end. lambda is rounded to {LAMBDA_DECIMALS} decimals.",
        "# The same lambda applies in every town (transfer assumption). The config value parking_garage_decay_m of "
        "configs/base_bs.yml equals decay_length_m below (tests/test_parking_garage_decay_config.py).",
        "# Independent checks, computed on the same plans, numbers only and no validation (both references are "
        "quantities of residents, the model averages over destinations):",
        f"#   commuters: mean garage probability of the {commuter_count} work and education activities inside "
        f"{', '.join(CALIBRATION_ZONE_IDS)} against {commuter_reference.value:.4f} = {commuter_reference.numerator_name} / "
        f"({commuter_reference.numerator_name} + {commuter_reference.other_name}) of the class {COMMUTER_CLASS} of "
        f"{commuter_reference.table}, which the calibration does not use.",
        f"#   paid share: share of the universe activities whose zone has a positive street hourly rate (zone level, blind "
        f"to the time of day and the stay length) against paid_share_overall {paid_reference:.4f} of {target.table}.",
    ]
    rows = [
        ("decay_length_m", f"{calibration.decay_m:.{LAMBDA_DECIMALS}f}", "m", "calibrated lambda of the garage weights"),
        ("garage_max_distance_m", f"{max_distance_m:g}", "m", "ASSUMPTION G2"),
        ("target_garage_probability", f"{target.value:.6f}", "share", f"{target.table}"),
        ("achieved_mean_garage_probability", f"{calibration.achieved:.6f}", "share", "mean over the universe at lambda"),
        ("tolerance", f"{tolerance:g}", "share", "absolute in the mean probability"),
        ("universe_activities", str(universe_size), "count", "destination universe of spec E5"),
        ("universe_activities_with_garage_in_range", str(with_garage), "count", "activities with a priced garage within D_max"),
        ("lambda_search_min_m", f"{lambda_min_m:g}", "m", "lower end of the bisection"),
        ("lambda_search_max_m", f"{lambda_max_m:g}", "m", "upper end of the bisection"),
        ("check_commuter_mean_garage_probability", f"{commuter_mean:.6f}", "share", "independent check and no validation"),
        ("check_commuter_reference_share", f"{commuter_reference.value:.6f}", "share", f"{commuter_reference.table}"),
        ("check_commuter_activities", str(commuter_count), "count", "work and education activities in the zones"),
        ("check_street_paid_share_zone_level", f"{paid_share:.6f}", "share", "independent check and time-blind and no validation"),
        ("check_paid_share_srv_reference", f"{paid_reference:.6f}", "share", f"{target.table} row {PAID_ROW}"),
    ]
    if any("," in field for row in rows for field in row):
        raise ValueError("a table field contains a comma")   # the long format has no quoting
    body = [",".join(TABLE_COLUMNS)] + [",".join(row) for row in rows]
    return "\n".join(header + body) + "\n"


def read_calibration_table(path) -> dict:
    """The rows of a calibration table as ``{quantity: float}`` (``#`` lines skipped); ``decay_length_m`` must be present
    and positive. Raises ``FileNotFoundError`` for a missing file and ``ValueError`` for a malformed table."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"calibration table missing: {path}")
    table = pd.read_csv(path, comment="#", dtype={"quantity": str, "value": str})
    if list(table.columns) != list(TABLE_COLUMNS):
        raise ValueError(f"{path}: columns {list(table.columns)} differ from {list(TABLE_COLUMNS)}")
    if table["quantity"].duplicated().any():
        raise ValueError(f"{path}: duplicate quantity rows")
    values = {row.quantity: float(row.value) for row in table.itertuples()}
    if not values.get("decay_length_m", 0) > 0:
        raise ValueError(f"{path}: decay_length_m is missing or not positive")
    return values


def run(*, plans, zones_path, tariffs_path, garages_path, city_center_path, commute_path, out_path, overwrite: bool = False,
        max_distance_m: float = cost.GARAGE_MAX_DISTANCE_M, lambda_min_m: float = DEFAULT_LAMBDA_MIN_M,
        lambda_max_m: float = DEFAULT_LAMBDA_MAX_M, tolerance: float = DEFAULT_TOLERANCE,
        generated_on: str | None = None) -> dict:
    """Calibrate on ``plans``, write the table to ``out_path`` and return the results (a dict of the table's numbers).

    Raises ``FileExistsError`` when ``out_path`` exists and ``overwrite`` is false (a calibrated release value is never
    replaced silently), ``ValueError`` for an empty universe or an unreachable target. Side effects: reads the inputs, logs,
    writes ``out_path`` (its directory must exist).
    """
    out_path = Path(out_path) if out_path is not None else None
    if out_path is not None and out_path.exists() and not overwrite:
        raise FileExistsError(f"{out_path} exists; a calibrated release value is not replaced silently (use --overwrite)")
    exposure = _load_exposure_module()
    activities, persons = exposure.read_main_activities(plans)
    zone_id = exposure.zone_per_activity(activities, zones_path)
    universe, commuters = universe_masks(activities, zone_id)
    if not universe.any():
        raise ValueError(f"no destination of the universe: the {len(activities)} main activities of the {persons} persons "
                         f"contain none outside home, work and education inside {', '.join(CALIBRATION_ZONE_IDS)}")
    garage_frame = parking_garages.load_garages(garages_path)
    parking_garages.validate_garages(garage_frame)
    priced = garage_frame[garage_frame["priced"].astype(bool)]
    if priced.empty:
        raise ValueError(f"{garages_path}: no priced garage to calibrate the options of")
    garage_x, garage_y = priced.geometry.x.to_numpy(dtype=float), priced.geometry.y.to_numpy(dtype=float)
    distances = garage_distances_m(activities[universe], garage_x, garage_y)
    with_garage = int((distances <= max_distance_m).any(axis=1).sum())
    log.info("[garage-decay] universe %d of %d main activities (%d persons); with a priced garage within %g m: %d/%d "
             "(%.1f %%); priced garages %d of %d", int(universe.sum()), len(activities), persons, max_distance_m, with_garage,
             int(universe.sum()), 100.0 * with_garage / int(universe.sum()), len(priced), len(garage_frame))
    target = read_city_center_target(city_center_path)
    calibration = calibrate_decay(distances, target.value, max_distance_m=max_distance_m, lambda_min_m=lambda_min_m,
                                  lambda_max_m=lambda_max_m, tolerance=tolerance)
    log.info("[garage-decay] target %.6f (%s); lambda %.2f m, mean %.6f after %d halvings", target.value, target.table,
             calibration.decay_m, calibration.achieved, calibration.iterations)
    commuter_reference = read_commuter_reference(commute_path)
    if commuters.any():
        commuter_distances = garage_distances_m(activities[commuters], garage_x, garage_y)
        commuter_mean = mean_garage_probability(commuter_distances, calibration.decay_m, max_distance_m)
    else:
        raise ValueError("no work or education activity inside the calibration zones: the commuter check has no universe")
    tariffs = parking_zones.load_tariffs(tariffs_path)
    paid_share = street_paid_share(zone_id[universe], tariffs)
    paid_reference = read_paid_share_reference(city_center_path)
    log.info("[garage-decay] independent checks (numbers, no validation): commuter garage probability %.4f over %d "
             "activities vs reference %.4f; street-paid share (zone level) %.4f vs SrV paid share %.4f", commuter_mean,
             int(commuters.sum()), commuter_reference.value, paid_share, paid_reference)
    text = table_text(
        inputs={name: (_label(path), file_sha256(path)) for name, path in (
            ("plans", plans), ("zones", zones_path), ("tariffs", tariffs_path), ("garages", garages_path),
            ("srv2023_city_center_parking", city_center_path), ("srv2023_commute_parking_by_workplace_class", commute_path))},
        universe_size=int(universe.sum()), persons=persons, with_garage=with_garage, priced_garages=len(priced),
        listed_garages=len(garage_frame), target=target, calibration=calibration, tolerance=tolerance,
        lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, max_distance_m=max_distance_m, commuter_mean=commuter_mean,
        commuter_count=int(commuters.sum()), commuter_reference=commuter_reference, paid_share=paid_share,
        paid_reference=paid_reference, generated_on=generated_on or datetime.date.today().isoformat(),
        code_state=_git_state())
    if out_path is not None:
        out_path.write_bytes(text.encode("ascii"))
        log.info("[garage-decay] wrote %s", out_path)
    return {"decay_length_m": calibration.decay_m, "target": target.value, "achieved": calibration.achieved,
            "universe_activities": int(universe.sum()), "with_garage_in_range": with_garage,
            "commuter_mean": commuter_mean, "commuter_reference": commuter_reference.value,
            "street_paid_share": paid_share, "paid_reference": paid_reference, "text": text}


def _label(path) -> str:
    """The path as recorded in the table: relative to the repository when inside it (POSIX), else as given."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return resolved.as_posix()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plans", type=Path, required=True, help="MATSim plans file (.xml or .xml.gz) of the reference scenario")
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH, help="data directory holding the committed inputs")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"the calibration table to write (default: <data-path>/{TABLE_RELATIVE})")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing calibration table")
    parser.add_argument("--max-distance-m", type=float, default=cost.GARAGE_MAX_DISTANCE_M)
    parser.add_argument("--lambda-min-m", type=float, default=DEFAULT_LAMBDA_MIN_M)
    parser.add_argument("--lambda-max-m", type=float, default=DEFAULT_LAMBDA_MAX_M)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    data = args.data_path
    run(plans=args.plans, zones_path=data / ZONES_RELATIVE, tariffs_path=data / TARIFFS_RELATIVE,
        garages_path=data / GARAGES_RELATIVE, city_center_path=data / CITY_CENTER_RELATIVE,
        commute_path=data / COMMUTE_RELATIVE, out_path=args.out or data / TABLE_RELATIVE, overwrite=args.overwrite,
        max_distance_m=args.max_distance_m, lambda_min_m=args.lambda_min_m, lambda_max_m=args.lambda_max_m,
        tolerance=args.tolerance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
