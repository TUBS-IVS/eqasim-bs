"""Calibrate the decay lengths of the garage options on a plans file (parking cost zones v2, spec Amendments E5, H2 and I6, #436).

A curation and analysis aid, never a pipeline stage. The off-street options of a car stay in a paid zone (the garages and, since
spec Amendment I, the large public surface lots of the garage dataset) have the weights street 1 and option g
``exp(-d_g / lambda)`` (ASSUMPTION G1, straight-line distance ``d_g`` in metres in EPSG:25832, only options within the maximum
distance D_max, ASSUMPTION G2). The decay length lambda is CALIBRATED, not estimated (ASSUMPTION G3): it is set so that the mean
option probability of the destination universe equals the SrV 2023 target. There are TWO decay lengths on the same plans (spec
Amendment H1): lambda for every purpose but work and education (E5, the city centre visitors) and lambda_c for work and
education (ASSUMPTION G1-c, H2, the commuters); the weights of a stay use the one its purpose selects
(``braunschweig.parking.cost.garage_decay_m_for_purpose``).

Definitions (the committed calibration table records them, so that its number can be read without this file):

* Option: a PRICED row of the garage dataset (a garage or a surface lot). An option is FREE when its schedule is the free
  schedule ``garages.FREE_SCHEDULE_TEXT`` ('0- free': one open free band, no tiers, cost 0 at every stay duration, tested by
  ``garages.is_free_schedule``) and PAID otherwise. The marker is read from the schedule (``is_free_option``), never from a name
  or a rate; a grace period (a free first band that something follows) is no free option.
* Two measures (spec Amendment I6): the model share has the universe of the SrV quantity of its target.

  - PAYMENT-INCLUSIVE (``MEASURE_ALL_OPTIONS``; lambda, E5): ``sum(w) / (1 + sum(w))`` over EVERY option within D_max. The SrV
    shares of ``srv2023_city_center_parking`` contain free and paying parkers, so free options count.
  - PAID-ONLY (``MEASURE_PAID_OPTIONS``; lambda_c, H2, and the Wolfsburg check, H3b): ``sum(w_paid) / (1 + sum(w_paid))`` over
    the PAID options within D_max. The SrV quantity is paid garage / (paid garage + paid street) among the paying street and
    garage users (ruling R-4h-1); a free option is a free parker in SrV terms and leaves numerator and denominator. Derivation:
    with W the sum of all weights, the model's choice probabilities are street ``1 / (1 + W)`` and option i ``w_i / (1 + W)``;
    the paid-only share ``sum(p_i, i paid) / (sum(p_i, i paid) + p_street)`` is ``(W_paid / (1 + W)) / (W_paid / (1 + W) + 1 /
    (1 + W)) = W_paid / (1 + W_paid)``, because the factor ``1 + W`` cancels: the paid-only share is the payment-inclusive form
    on the paid options alone (``measure_distances`` selects them). A stay without a paid option within D_max has the paid-only
    share 0 (its paying users park on the street). A universe is averaged over its activities, as in E5; this mean of the
    per-activity shares equals the pooled ratio of summed probabilities where no free option is in range or where every activity
    sees the same options, and may differ from it where free options reach only a part of the universe (the pooled ratio is not
    computed).
* Destination universe: every main activity of the selected plans (the activities of type ``* interaction`` that the router
  inserts are no destinations; the freight agents, ids ``freight_*``, are trip creators and no residents and are excluded
  with the shared ``braunschweig.analysis.freight_filter``, counted in the log and the header) whose type is none of
  ``HOME_AND_COMMUTER_PURPOSES`` (home, work, education) and that lies in one of the calibration zones
  ``CALIBRATION_ZONE_IDS`` (``bs_zone_ia``, ``bs_zone_ib``), whatever the mode of the trip that ends there. The zone of an
  activity is the one ``braunschweig.parking.attach.attach_parking_zones`` assigns, recomputed on the zone polygons of the data
  path; the plans supply the coordinates, the purposes and the activity attribute ``parkingFree`` (the free-parking draw, which
  does not depend on the garage dataset), so the plans need no regeneration for another option set.
* Option probability of a destination (payment-inclusive measure, lambda): ``sum(w) / (1 + sum(w))`` over the priced options
  within D_max, the weights above. The price does not enter (E3), the early rules of a stay (parkingFree, resident, fee window)
  and E4 are not applied: the quantity is the choice share of the options, as the calibration target is a share of places, not
  of payments.
* Target of lambda: ``share(garage_large_lot) / (share(garage_large_lot) + share(street))`` of the SrV 2023 table
  ``srv2023_city_center_parking`` among the Braunschweig residents who park on the street or in a garage or large lot when
  they drive to the city centre (employer lots and other places leave the denominator because the model handles them by
  the employer-free draw), READ from the committed table, never typed here. Universe caveat: SrV asks residents about their
  usual place, the model averages over the destinations of all persons of the plans.
* D_max: the config key ``parking_garage_max_distance_m`` of ``configs/base_bs.yml`` (``--config``; ASSUMPTION G2), read and
  never defaulted from a code constant: the script fails when the key is missing. ``--max-distance-m`` overrides it (a
  sensitivity; the table records the value used).
* Search: bisection on lambda in ``[--lambda-min-m, --lambda-max-m]`` (default 10 to 5000 m), which stops when the mean
  probability is within ``--tolerance`` (default 0.0005) of the target; the mean probability rises with lambda, so the search
  fails (``ValueError``) when the target lies outside the range at its two ends. The reported lambda is rounded to 0.01 m and
  its achieved mean recomputed.
* Commuter universe (H2): the main activities of type work or education (``cost.COMMUTER_PURPOSES``) inside the same
  calibration zones that do NOT carry the activity attribute ``parkingFree``. The free-parking draw frees everyone whose
  payment is free (``share_free_total`` of the class), so the universe holds the PAYERS only; the activities that carry
  parkingFree are counted and named. Like-for-like commuter target (ruling R-4h-1, revision of H2, with the paid-only measure of
  I6): the PAID-only share ``share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid)`` of the row
  ``bs_zentrum`` of ``srv2023_commute_parking_by_workplace_class``, READ from the table, never typed. (The free-inclusive share
  ``share_garage_large_lot / (share_garage_large_lot + share_street)`` has the free street and garage parkers in its
  denominator, which the model's universe does not contain, and is not used.) The same bisection on the PAID options gives
  lambda_c. The bs_zentrum paid share is therefore a calibration target and no longer checks the model.
* Independent check (H3b) on the SAME plans, reported as a number and never as validation: the paid-only mean option
  probability with lambda_c of the work and education activities without parkingFree inside the Wolfsburg zones
  ``WOLFSBURG_ZONE_IDS`` against the same paid-only share of the row ``03103`` of the same table, with no pre-registered bound.
  The universe size is stated; the reference is a quantity of residents, the model averages over destinations. The paid share is
  NOT checked here (ruling R-5-4, issue #436): a zone-level check, blind to the fee window, the free threshold and the maximum
  stay, read 1.0 in the zones Ia and Ib and said nothing. The time-aware paid share is computed on the outcomes of a model run by
  ``scripts/parking/compare_parking_targets.py``, whose commuter and Wolfsburg rows count ALL off-street options (the outcome
  report has no paid/free split), so that run comparison is not exact where free options lie within D_max.
* Sensitivities (spec Amendment I6): ``--sensitivity-garages LABEL=PATH`` (repeatable) runs the SAME calibration (plans, zones,
  targets, D_max, measures, tolerance) on another garage dataset, e.g. the large-lot thresholds 30 and 100 of Task 4j, and writes
  lambda and lambda_c of each as header lines of the table, labelled sensitivity and never a release value; the release rows are
  those of the primary dataset. A sensitivity dataset with the content of the primary one is refused.

Fallback transparency: the log and the table report, per universe, how many activities have an option of the measure within
D_max (the share whose probability can be positive at all; a universe below ``MINIMUM_REACH_SHARE_WARNING`` is warned about) and
how many free options lie within D_max of the universe (distinct options and activities reached); a target that the options
cannot reach fails instead of returning the end of the range, and an empty commuter or Wolfsburg universe fails instead of
skipping its row.

Output: the committed table ``parking_garage_decay_calibration_2026.csv`` (long format ``quantity,value,unit,note`` under a
header that names the inputs with their SHA-256, the measures, all three universes, both target derivations, the sensitivities
and the code state; the rows ``decay_length_m`` and ``decay_commute_length_m`` are the release values of the config keys
``parking_garage_decay_m`` and ``parking_garage_decay_commute_m``). The table belongs to the garage dataset whose SHA-256 its
header names: another option set needs another calibration. The script refuses to replace an existing table unless
``--overwrite`` is given. Run it on the plans of the reference scenario only (the 25 % plans run of issue #436); a plans file
of another scenario gives another lambda.

Usage (from the repository root)::

    python scripts/parking/calibrate_garage_decay.py --plans <population.xml.gz> [--data-path eqasim-data/data]
        [--out eqasim-data/data/braunschweig/parking/parking_garage_decay_calibration_2026.csv] [--overwrite]
        [--sensitivity-garages lot_threshold_30=<geojson> --sensitivity-garages lot_threshold_100=<geojson>]
        [--facility-kinds garage]

``--facility-kinds`` (default: every kind) restricts the priced options to the given ``facility_kind`` values of the garage
dataset (ruling C1 of the Task 5a review). The RELEASE value is the calibration on every kind; a filtered run is a sensitivity
number, written to another ``--out``, never over the release table. The table records the filter (header line and the rows
``garage_facility_filter_active`` and ``priced_garages_used``). The option matters only where surface lots lie within D_max of
the universe. Before Task 4j the Braunschweig option set held no surface lot (all 13 surface lots were in Wolfsburg), so
``--facility-kinds garage`` was vacuous for the zones Ia and Ib; since Task 4j (spec Amendment I) the set holds the large
surface lots, so the option is a garages-only sensitivity there. It is not part of the run plan of the release.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import logging
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.analysis import freight_filter  # noqa: E402
from braunschweig.parking import cost, garages as parking_garages, restricted_inputs  # noqa: E402

log = logging.getLogger("calibrate_garage_decay")

DEFAULT_DATA_PATH = REPO / "eqasim-data" / "data"
ZONES_RELATIVE = "braunschweig/parking/parking_zones_2026.geojson"
GARAGES_RELATIVE = "braunschweig/parking/parking_garages_2026.geojson"
CITY_CENTER_RELATIVE = "braunschweig/srv/srv2023_city_center_parking.csv"
COMMUTE_RELATIVE = "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv"
TABLE_RELATIVE = "braunschweig/parking/parking_garage_decay_calibration_2026.csv"
DEFAULT_CONFIG_PATH = REPO / "configs" / "base_bs.yml"
#: The pipeline config key of ASSUMPTION G2 (the maximum straight-line distance of a garage option, metres): the single
#: source of D_max of the calibration, so that the calibrated lambda and the run use the same distance.
MAX_DISTANCE_CONFIG_KEY = "parking_garage_max_distance_m"
EXPOSURE_SCRIPT = REPO / "scripts" / "curation" / "parking_zones_2026" / "count_zone_exposure.py"

#: Spec E5: the calibration zones, the two fee zones of the Braunschweig city centre.
CALIBRATION_ZONE_IDS = ("bs_zone_ia", "bs_zone_ib")
#: Spec E5: the destination universe leaves out these activity types; work and education are the commuter purposes of the
#: commuter universe of H2 (cost.COMMUTER_PURPOSES).
HOME_AND_COMMUTER_PURPOSES = frozenset({cost.HOME_PURPOSE, *cost.COMMUTER_PURPOSES})
#: The rows of the SrV tables the targets are read from (PAID_ROW is read by ``read_paid_share_reference`` for the
#: comparison script; the calibration itself does not use it).
GARAGE_ROW, STREET_ROW, PAID_ROW = "garage_large_lot", "street", "paid_share_overall"
#: Spec H2: the workplace class whose commuter garage share is the CALIBRATION TARGET of lambda_c (row of the SrV table).
COMMUTER_CLASS = "bs_zentrum"
#: The per-place payment columns of the commute table the commuter shares are read from (ruling R-4h-1, paid-only).
GARAGE_PAID_COLUMN, STREET_PAID_COLUMN = "share_garage_large_lot_paid", "share_street_paid"
#: Spec H3b: the Wolfsburg zones (the tariff areas of the municipal fee ordinance) and the table row of Wolfsburg, whose
#: commuter garage share is the independent CHECK (a number, no validation).
WOLFSBURG_ZONE_IDS = ("wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3")
WOLFSBURG_CLASS = "03103"
DEFAULT_LAMBDA_MIN_M, DEFAULT_LAMBDA_MAX_M = 10.0, 5000.0
DEFAULT_TOLERANCE = 0.0005
#: The bisection halves an interval of at most 5000 m: 80 halvings are far below any tolerance, so reaching this bound
#: without the tolerance means the target is not met by a continuous function (reported as a failure).
MAXIMUM_ITERATIONS = 80
LAMBDA_DECIMALS = 2
TABLE_COLUMNS = ("quantity", "value", "unit", "note")
#: Spec Amendment I6: the SrV quantity of a target decides which options count in the model share. The payment-inclusive
#: measure (every option; the visitor target of E5, whose SrV shares contain free and paying parkers) and the paid-only measure
#: (the paid options only; the commuter target of H2 and the Wolfsburg check of H3b, whose SrV quantity is among the paying users).
MEASURE_ALL_OPTIONS, MEASURE_PAID_OPTIONS = "all_options", "paid_options"
MEASURES = (MEASURE_ALL_OPTIONS, MEASURE_PAID_OPTIONS)
#: Fallback transparency: a universe in which fewer than this share of the activities has an option of its measure within D_max
#: is warned about (its probability is then 0 for the rest). A plausibility threshold for the warning, not a scientific bound.
MINIMUM_REACH_SHARE_WARNING = 0.5
#: A sensitivity label: letters, digits, underscore, dot and hyphen (it is a header word and a key of the result).
SENSITIVITY_LABEL_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


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


def read_configured_max_distance_m(config_path=DEFAULT_CONFIG_PATH) -> float:
    """D_max (ASSUMPTION G2, metres) as configured: the key ``parking_garage_max_distance_m`` of the ``config`` section of
    ``config_path`` (default ``configs/base_bs.yml``). Raises ``ValueError`` naming the key when it is missing or no
    positive number; there is no fallback to a code constant, because a lambda calibrated for another distance than the run
    uses would be silently inconsistent. Reads the file."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"pipeline config missing: {path} (it carries {MAX_DISTANCE_CONFIG_KEY})")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = document.get("config") if isinstance(document, dict) else None
    value = section.get(MAX_DISTANCE_CONFIG_KEY) if isinstance(section, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{path}: config key {MAX_DISTANCE_CONFIG_KEY} must be a positive number of metres, got {value!r}"
                         + ("" if section is not None else " (the file has no 'config' section)"))
    return float(value)


def file_sha256(path) -> str:
    """SHA-256 of a file; text files are hashed with LF line endings (as git stores them), binary ``.gz`` as they are."""
    data = Path(path).read_bytes()
    if Path(path).suffix != ".gz":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def read_srv_table(path) -> pd.DataFrame:
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
    table = read_srv_table(path)
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
    """``paid_share_overall`` of ``srv2023_city_center_parking``, the paid-share reference of
    ``scripts/parking/compare_parking_targets.py`` (not used by the calibration)."""
    table = read_srv_table(path)
    rows = table.loc[table["parking_type"] == PAID_ROW, "share"]
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one row {PAID_ROW!r}, found {len(rows)}")
    return _positive_share(float(rows.iloc[0]), f"{path} row {PAID_ROW}")


def read_commuter_reference(path, workplace_class: str = COMMUTER_CLASS) -> Target:
    """The PAID-only commuter garage share of a row of ``srv2023_commute_parking_by_workplace_class`` among the paying street
    and garage users (ruling R-4h-1): ``share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid)`` of
    the class row ``workplace_class`` (default ``COMMUTER_CLASS`` = bs_zentrum, the calibration target of H2;
    ``WOLFSBURG_CLASS`` is the check of H3b). Raises ``ValueError`` when the class row is missing or duplicated, when the
    table lacks the per-place payment columns (written before the revision: re-extract it) or when a share is no number in
    (0, 1]."""
    table = read_srv_table(path)
    rows = table[(table["workplace_class"] == workplace_class) & (table["level"] == "class")]
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one class row {workplace_class!r}, found {len(rows)}")
    missing = [column for column in (GARAGE_PAID_COLUMN, STREET_PAID_COLUMN) if column not in rows.columns]
    if missing:
        raise ValueError(f"{path}: the per-place payment column(s) {missing} are missing; the table was written before the "
                         "revision of spec H2 (ruling R-4h-1): regenerate it with scripts/extract_srv_commute_parking.py")
    garage = _positive_share(float(rows[GARAGE_PAID_COLUMN].iloc[0]), f"{path} {workplace_class} {GARAGE_PAID_COLUMN}")
    street = _positive_share(float(rows[STREET_PAID_COLUMN].iloc[0]), f"{path} {workplace_class} {STREET_PAID_COLUMN}")
    return Target(garage / (garage + street), GARAGE_PAID_COLUMN, garage, STREET_PAID_COLUMN, street, Path(path).name)


def load_exposure_module():
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
    """Mean over the rows of ``sum(w) / (1 + sum(w))`` with ``w = exp(-d / decay_m)`` for the options within
    ``max_distance_m`` (the street has the weight 1; an option beyond the maximum distance has weight 0). ``distances_m`` has one
    row per destination and one column per option OF THE MEASURE (``measure_distances`` selects the paid options for the
    paid-only measure). Raises ``ValueError`` for an empty universe or a non-positive decay."""
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


def parse_facility_kinds(text: str):
    """``"all"`` -> None (every kind); ``"garage,surface_lot"`` -> ``("garage", "surface_lot")``."""
    text = text.strip()
    if text == "all":
        return None
    return tuple(kind.strip() for kind in text.split(",") if kind.strip())


def validate_facility_kinds(facility_kinds):
    """None (every kind) or a non-empty tuple of kinds of ``garages.FACILITY_KINDS``; ``ValueError`` for an unknown kind."""
    if facility_kinds is None:
        return None
    kinds = tuple(facility_kinds)
    if not kinds:
        raise ValueError("facility_kinds must name at least one kind, or be None for every kind")
    for kind in kinds:
        if kind not in parking_garages.FACILITY_KINDS:
            raise ValueError(f"unknown facility kind {kind!r}; the kinds are {sorted(parking_garages.FACILITY_KINDS)}")
    return kinds


class UniverseMasks(NamedTuple):
    """Boolean masks over the activities (see ``universe_masks``) and the number of excluded parkingFree commuters."""

    universe: np.ndarray
    commuters: np.ndarray
    wolfsburg_commuters: np.ndarray
    commuters_with_parking_free: int


def universe_masks(activities: pd.DataFrame, zone_id: pd.Series, calibration_zone_ids=CALIBRATION_ZONE_IDS,
                   wolfsburg_zone_ids=WOLFSBURG_ZONE_IDS) -> UniverseMasks:
    """Boolean masks over ``activities`` (columns ``purpose``, ``x``, ``y``, ``parking_free``; ``zone_id`` aligned to its
    index): the destination universe of spec E5 (inside a calibration zone, purpose not home, work or education; parkingFree
    does not change it), the commuter universe of H2 (inside a calibration zone, purpose work or education, NOT parkingFree)
    and the Wolfsburg check universe of H3b (inside a Wolfsburg zone, purpose work or education, NOT parkingFree), plus the
    number of work and education activities inside the calibration zones that carry parkingFree and are left out of the
    commuter universe. Raises ``ValueError`` when ``parking_free`` is missing: a frame that does not say who parks free must
    not be read as nobody does."""
    if "parking_free" not in activities.columns:
        raise ValueError("the activities lack the column parking_free: the commuter universe of H2 leaves out the activities "
                         "that carry parkingFree and cannot be selected without it (use the plans reader of "
                         "count_zone_exposure.py)")
    inside = zone_id.isin(list(calibration_zone_ids)).to_numpy()
    in_wolfsburg = zone_id.isin(list(wolfsburg_zone_ids)).to_numpy()
    purpose = activities["purpose"].astype(str)
    is_commuter = purpose.isin(cost.COMMUTER_PURPOSES).to_numpy()
    is_free = activities["parking_free"].to_numpy(dtype=bool)
    universe = inside & ~purpose.isin(HOME_AND_COMMUTER_PURPOSES).to_numpy()
    commuters = inside & is_commuter & ~is_free
    return UniverseMasks(universe, commuters, in_wolfsburg & is_commuter & ~is_free, int((inside & is_commuter & is_free).sum()))


# --------------------------------------------------------------------------- free and paid options, the measures (I6)


def _is_set(value) -> bool:
    """Whether a cell of the garage dataset holds a value: ``None``, NaN, ``pd.NA`` and the empty string are empty (the loader
    reads an empty text cell as ``None``)."""
    if isinstance(value, str):
        return value.strip() != ""
    return value is not None and not pd.isna(value)


def is_free_option(tariff_tiers, tariff_duration_bands) -> bool:
    """Whether an option is FREE: its schedule is the free schedule ``garages.FREE_SCHEDULE_TEXT`` ("0- free"), i.e.
    ``tariff_duration_bands`` parses (``garages.parse_duration_bands``) to ONE open-ended free band
    (``garages.is_free_schedule``) and the option has no ``tariff_tiers``. A free first band that something follows, a closed
    free band (the grace period of a tiered garage) and every other schedule are PAID options. The marker is the schedule of the
    row, never its name or a rate. Raises ``ValueError`` for a schedule text that does not parse (``garages.validate_garages``
    refuses it earlier)."""
    if _is_set(tariff_tiers) or not _is_set(tariff_duration_bands):
        return False
    return parking_garages.is_free_schedule(parking_garages.parse_duration_bands(tariff_duration_bands))


def paid_option_flags(options: pd.DataFrame) -> np.ndarray:
    """One bool per row of ``options`` (columns ``tariff_tiers`` and ``tariff_duration_bands`` of the garage dataset): True for
    a PAID option, False for a FREE one (``is_free_option``)."""
    return np.array([not is_free_option(tiers, bands)
                     for tiers, bands in zip(options["tariff_tiers"], options["tariff_duration_bands"])], dtype=bool)


def measure_distances(distances_m: np.ndarray, paid: np.ndarray, measure: str) -> np.ndarray:
    """The columns of ``distances_m`` (one per option, ``paid`` the flags of ``paid_option_flags``) that count in ``measure``.

    ``MEASURE_ALL_OPTIONS`` (payment-inclusive, lambda): every column. ``MEASURE_PAID_OPTIONS`` (paid-only, lambda_c and the
    Wolfsburg check): the columns of the PAID options; a free option is a free parker in SrV terms and leaves numerator and
    denominator of the share, which reduces to the same form ``sum(w) / (1 + sum(w))`` on the paid options alone (the derivation
    is in the module documentation). Raises ``ValueError`` for another measure or flags that do not match the columns."""
    if measure not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; the measures are {MEASURES}")
    if len(paid) != distances_m.shape[1]:
        raise ValueError(f"{len(paid)} paid flags for {distances_m.shape[1]} option columns")
    return distances_m if measure == MEASURE_ALL_OPTIONS else distances_m[:, paid]


class OptionReach(NamedTuple):
    """How the options reach the activities of ONE universe (fallback transparency; counts within the maximum distance):
    the activities, those with at least one option, those with at least one PAID option (the activities whose paid-only share
    can be above 0), the distinct FREE options within reach of at least one activity (``free_option_ids``: their ids, sorted,
    when ``option_reach`` was given the ids of the options) and the activities with a free option."""

    activities: int
    with_option_in_range: int
    with_paid_option_in_range: int
    free_options_in_range: int
    with_free_option_in_range: int
    free_option_ids: tuple = ()


def option_reach(distances_m: np.ndarray, paid: np.ndarray, max_distance_m: float, option_ids=None) -> OptionReach:
    """The ``OptionReach`` of a universe: ``distances_m`` has one row per activity and one column per option (``paid`` the
    flags of ``paid_option_flags``, ``option_ids`` optionally the id of every option, which names the free options in range); an
    option at exactly ``max_distance_m`` is in range. Pure."""
    if len(paid) != distances_m.shape[1]:
        raise ValueError(f"{len(paid)} paid flags for {distances_m.shape[1]} option columns")
    if option_ids is not None and len(option_ids) != distances_m.shape[1]:
        raise ValueError(f"{len(option_ids)} option ids for {distances_m.shape[1]} option columns")
    in_range = distances_m <= max_distance_m
    free_in_range = in_range[:, ~paid]
    free_reached = free_in_range.any(axis=0)
    names = ()
    if option_ids is not None:
        names = tuple(sorted(str(name) for name in np.asarray(option_ids, dtype=object)[~paid][free_reached]))
    return OptionReach(activities=int(distances_m.shape[0]), with_option_in_range=int(in_range.any(axis=1).sum()),
                       with_paid_option_in_range=int(in_range[:, paid].any(axis=1).sum()),
                       free_options_in_range=int(free_reached.sum()),
                       with_free_option_in_range=int(free_in_range.any(axis=1).sum()), free_option_ids=names)


def _warn_when_barely_reached(name: str, reached: int, activities: int, kind: str, max_distance_m: float, context: str) -> None:
    """Warn when fewer than ``MINIMUM_REACH_SHARE_WARNING`` of the activities of a universe have an option (``kind``: "priced"
    or "paid") within ``max_distance_m``: the probability of the others is 0, so the calibration or the check rests on a
    minority."""
    if activities and reached / activities < MINIMUM_REACH_SHARE_WARNING:
        log.warning("[garage-decay] %s%s: only %d/%d (%.1f %%) of the activities have a %s option within reach (%g m), below "
                    "the warning share of %.0f %%: the option set barely covers this universe", context, name, reached,
                    activities, 100.0 * reached / activities, kind, max_distance_m, 100.0 * MINIMUM_REACH_SHARE_WARNING)


class OptionSetCalibration(NamedTuple):
    """Both calibrations on ONE option set (the garage dataset of the release or of a sensitivity), the Wolfsburg check mean at
    its lambda_c and how the options reach the three universes."""

    options: int
    free_options: int
    visitors: Calibration
    commuters: Calibration
    wolfsburg_mean: float
    visitor_reach: OptionReach
    commuter_reach: OptionReach
    wolfsburg_reach: OptionReach


def calibrate_option_set(activities: pd.DataFrame, masks: UniverseMasks, options: pd.DataFrame, *, visitor_target: Target,
                         commuter_target: Target, max_distance_m: float, lambda_min_m: float, lambda_max_m: float,
                         tolerance: float, context: str = "") -> OptionSetCalibration:
    """Calibrate lambda (payment-inclusive measure on every option, E5) and lambda_c (paid-only measure on the paid options,
    H2) on the priced ``options`` and compute the paid-only Wolfsburg check mean at lambda_c (H3b). ``context`` prefixes the log
    lines and the errors (e.g. ``"sensitivity lot_threshold_30: "``). Logs, per universe, the activities with an option within
    D_max and the free options within D_max. Raises ``ValueError`` naming the calibration (and the measure) whose target the
    options cannot reach."""
    paid = paid_option_flags(options)
    x_m, y_m = options.geometry.x.to_numpy(dtype=float), options.geometry.y.to_numpy(dtype=float)
    visitor_distances = garage_distances_m(activities[masks.universe], x_m, y_m)
    commuter_distances = garage_distances_m(activities[masks.commuters], x_m, y_m)
    wolfsburg_distances = garage_distances_m(activities[masks.wolfsburg_commuters], x_m, y_m)
    option_ids = options["garage_id"].tolist()
    visitor_reach = option_reach(visitor_distances, paid, max_distance_m, option_ids)
    commuter_reach = option_reach(commuter_distances, paid, max_distance_m, option_ids)
    wolfsburg_reach = option_reach(wolfsburg_distances, paid, max_distance_m, option_ids)
    log.info("[garage-decay] %soption set: %d priced options (%d paid, %d free)", context, len(options), int(paid.sum()),
             int((~paid).sum()))
    log.info("[garage-decay] %sdestination universe %d activities; with a priced garage within %g m: %d/%d (%.1f %%); free "
             "options within %g m: %d distinct, reach %d/%d activities", context, visitor_reach.activities, max_distance_m,
             visitor_reach.with_option_in_range, visitor_reach.activities,
             100.0 * visitor_reach.with_option_in_range / visitor_reach.activities, max_distance_m,
             visitor_reach.free_options_in_range, visitor_reach.with_free_option_in_range, visitor_reach.activities)
    log.info("[garage-decay] %scommuter universe %d work and education activities without parkingFree inside %s (excluded as "
             "parkingFree %d); with a priced garage within %g m: %d/%d (%.1f %%); paid options within %g m: %d/%d (%.1f %%); "
             "free options within %g m: %d distinct, reach %d/%d activities", context, commuter_reach.activities,
             ", ".join(CALIBRATION_ZONE_IDS), masks.commuters_with_parking_free, max_distance_m,
             commuter_reach.with_option_in_range, commuter_reach.activities,
             100.0 * commuter_reach.with_option_in_range / commuter_reach.activities, max_distance_m,
             commuter_reach.with_paid_option_in_range, commuter_reach.activities,
             100.0 * commuter_reach.with_paid_option_in_range / commuter_reach.activities, max_distance_m,
             commuter_reach.free_options_in_range, commuter_reach.with_free_option_in_range, commuter_reach.activities)
    log.info("[garage-decay] %sWolfsburg check universe %d work and education activities without parkingFree inside %s; with a "
             "priced garage within %g m: %d/%d; paid options within %g m: %d/%d; free options within %g m: %d distinct, reach "
             "%d/%d activities", context, wolfsburg_reach.activities, ", ".join(WOLFSBURG_ZONE_IDS), max_distance_m,
             wolfsburg_reach.with_option_in_range, wolfsburg_reach.activities, max_distance_m,
             wolfsburg_reach.with_paid_option_in_range, wolfsburg_reach.activities, max_distance_m,
             wolfsburg_reach.free_options_in_range, wolfsburg_reach.with_free_option_in_range, wolfsburg_reach.activities)
    _warn_when_barely_reached("destination universe", visitor_reach.with_option_in_range, visitor_reach.activities, "priced",
                              max_distance_m, context)
    _warn_when_barely_reached("commuter universe", commuter_reach.with_paid_option_in_range, commuter_reach.activities, "paid",
                              max_distance_m, context)
    _warn_when_barely_reached("Wolfsburg check universe", wolfsburg_reach.with_paid_option_in_range,
                              wolfsburg_reach.activities, "paid", max_distance_m, context)
    search = dict(max_distance_m=max_distance_m, lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, tolerance=tolerance)
    try:
        visitors = calibrate_decay(measure_distances(visitor_distances, paid, MEASURE_ALL_OPTIONS), visitor_target.value,
                                   **search)
    except ValueError as error:
        raise ValueError(f"{context}lambda (payment-inclusive measure, every option counts): {error}") from error
    log.info("[garage-decay] %starget %.6f (%s, payment-inclusive measure); lambda %.2f m, mean %.6f after %d halvings",
             context, visitor_target.value, visitor_target.table, visitors.decay_m, visitors.achieved, visitors.iterations)
    try:
        commuters = calibrate_decay(measure_distances(commuter_distances, paid, MEASURE_PAID_OPTIONS), commuter_target.value,
                                    **search)
    except ValueError as error:
        raise ValueError(f"{context}lambda_c (PAID-only measure, paid options only, {commuter_reach.with_paid_option_in_range} "
                         f"of {commuter_reach.activities} activities have one within {max_distance_m:g} m): {error}") from error
    log.info("[garage-decay] %scommuter target %.6f (%s class %s, PAID-only measure); lambda_c %.2f m, mean %.6f after %d "
             "halvings", context, commuter_target.value, commuter_target.table, COMMUTER_CLASS, commuters.decay_m,
             commuters.achieved, commuters.iterations)
    wolfsburg_mean = mean_garage_probability(measure_distances(wolfsburg_distances, paid, MEASURE_PAID_OPTIONS),
                                             commuters.decay_m, max_distance_m)
    return OptionSetCalibration(len(options), int((~paid).sum()), visitors, commuters, wolfsburg_mean, visitor_reach,
                                commuter_reach, wolfsburg_reach)


def git_state() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
                                timeout=20).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True,
                               text=True, check=True, timeout=20).stdout.strip()
        return commit + (" (working tree has uncommitted changes)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown (no git checkout)"


def option_set_text(priced: pd.DataFrame) -> str:
    """The priced options per facility kind as header text, e.g. ``12 garage, 0 surface_lot (the option set holds no surface
    lot)``; the kinds are those of ``garages.FACILITY_KINDS``."""
    counts = [(kind, int((priced["facility_kind"] == kind).sum())) for kind in sorted(parking_garages.FACILITY_KINDS)]
    text = ", ".join(f"{count} {kind}" for kind, count in counts)
    if dict(counts).get("surface_lot", 0) == 0:
        text += " (the option set holds no surface lot)"
    return text


def _kind_counts_text(options: pd.DataFrame) -> str:
    """``2 garage + 1 surface_lot``: the options per facility kind of a sensitivity header line."""
    return " + ".join(f"{int((options['facility_kind'] == kind).sum())} {kind}" for kind in sorted(parking_garages.FACILITY_KINDS))


def exclude_freight_agents(activities: pd.DataFrame, persons: int) -> tuple:
    """``(activities, persons, freight_agents)`` without the freight agents (person ids ``freight_*``): they are trip creators and
    no residents, and the SrV shares are about residents, so the shared exclusion of the person-travel analyses
    (``braunschweig.analysis.freight_filter``, which logs the excluded main activities) applies. ``persons`` is the number of
    person elements of the plans file; the freight agents that own a main activity are subtracted. The returned frame is
    re-indexed 0..n-1."""
    is_freight = activities["person_id"].astype(str).str.startswith(freight_filter.FREIGHT_PERSON_PREFIX)
    freight_agents = int(activities.loc[is_freight, "person_id"].nunique())
    kept = freight_filter.drop_freight_agents(activities, "person_id", label="garage-decay").reset_index(drop=True)
    return kept, persons - freight_agents, freight_agents


def purpose_counts_text(activities: pd.DataFrame) -> str:
    """The main activities per purpose as header text, sorted by purpose: ``escort 509, leisure 2548, shop 3658``."""
    counts = activities["purpose"].astype(str).value_counts().sort_index()
    return ", ".join(f"{purpose} {count}" for purpose, count in counts.items())


def _free_reach_text(reach: OptionReach, max_distance_m: float, consequence: str) -> str:
    names = f" ({', '.join(reach.free_option_ids)})" if reach.free_option_ids else ""
    return (f"Free options within {max_distance_m:g} m of this universe: {reach.free_options_in_range} distinct{names}, "
            f"reaching {reach.with_free_option_in_range} of its {reach.activities} activities; {consequence}.")


def table_text(*, inputs: dict, persons: int, freight_agents: int, purposes: dict, listed_options: int, options_used: int,
               facility_kinds_text: str, option_set_text: str, release: OptionSetCalibration, target: Target,
               commuter_target: Target, wolfsburg_reference: Target, commuter_excluded_free: int, sensitivities: list,
               tolerance: float,
               lambda_min_m: float, lambda_max_m: float, max_distance_m: float, generated_on: str, code_state: str) -> str:
    """The calibration table as text: the provenance header and the long-format rows ``TABLE_COLUMNS``."""
    zone_text = ", ".join(CALIBRATION_ZONE_IDS)
    calibration, commuter_calibration = release.visitors, release.commuters
    universe, commuters, wolfsburg = release.visitor_reach, release.commuter_reach, release.wolfsburg_reach
    header = [
        "# Table: parking_garage_decay_calibration_2026.csv",
        "# Calibration of the decay lengths lambda (visitors) and lambda_c (commuters) of the off-street options (parking cost "
        "zones v2, spec Amendments E5, H2 and I6, ASSUMPTIONS G1 to G3 and G1-c, issue #436).",
        f"# Generated by scripts/parking/calibrate_garage_decay.py on {generated_on}; code state {code_state}.",
        "# Inputs (SHA-256; text files with LF line endings, .gz files as stored):",
        *[f"#   {name}: {path} sha256={digest}" for name, (path, digest) in inputs.items()],
        "# This table belongs to the garage dataset named above (sha256) exactly: another option set (a regenerated release, a "
        "car park that becomes a free option) needs a new calibration.",
        "# Measures (spec Amendment I6, like-for-like with the SrV quantity of each target): lambda: payment-inclusive, the share "
        "counts EVERY off-street option, sum(w) / (1 + sum(w)); lambda_c and the Wolfsburg check: PAID-only, the share counts "
        "the paid options only, sum(w_paid) / (1 + sum(w_paid)). A free option is the free schedule '0- free' (cost 0 at every "
        "stay duration, read from the schedule of the row, never a name or a rate); in SrV terms it is a free parker and leaves "
        "numerator and denominator: with the choice probabilities street 1 / (1 + W) and option i w_i / (1 + W) (W = the sum of "
        "all weights) the paid-only share sum(p_paid) / (sum(p_paid) + p_street) = W_paid / (1 + W_paid), because the factor "
        "(1 + W) cancels. A universe is averaged over its activities; an activity without a paid option in range has the "
        "paid-only share 0.",
        f"# Universe of lambda: the main activities of the selected plans ({persons} persons; {freight_agents} freight agents "
        f"with ids {freight_filter.FREIGHT_PERSON_PREFIX}* excluded, they are no residents) inside {zone_text} "
        f"whose type is none of {', '.join(sorted(HOME_AND_COMMUTER_PURPOSES))} (all modes, destination universe): "
        f"{universe.activities} activities, {universe.with_option_in_range} of them with at least one priced garage within "
        f"{max_distance_m:g} m. By purpose: {purposes['visitors']}. "
        + _free_reach_text(universe, max_distance_m, "they count in this payment-inclusive measure"),
        f"# Garages: {options_used} priced of {listed_options} listed in the dataset used (facility kinds: "
        f"{facility_kinds_text}; a filtered run is a sensitivity number, the release value uses every kind); straight-line "
        "distances in EPSG:25832.",
        f"# Option set: {option_set_text}. The target's garage_large_lot share contains large surface lots; where the option set "
        f"has none, the garages carry that share alone, which pushes lambda up. {release.free_options} of the {release.options} "
        f"options {'is' if release.free_options == 1 else 'are'} free (the free schedule).",
        "# Quantity: the mean over a universe of sum(w) / (1 + sum(w)), w = exp(-d / decay) of the options of the measure within "
        "the maximum distance (street weight 1; no price, no early rule, no E4).",
        f"# Target of lambda: {target.numerator_name} / ({target.numerator_name} + {target.other_name}) = {target.numerator} / "
        f"({target.numerator} + {target.other}) = {target.value:.6f} of {target.table} (Braunschweig residents who park on the "
        "street or in a garage or large lot when they drive to the city centre), with the payment-inclusive measure (every "
        "option counts) because the SrV shares contain free and paying parkers. A calibration target, no validation. "
        "Universe caveat: SrV asks residents about their usual place, the model averages over destinations.",
        f"# Search for lambda: bisection in [{lambda_min_m:g}, {lambda_max_m:g}] m to a tolerance of {tolerance:g} in the mean "
        f"probability, {calibration.iterations} halvings; the mean is {calibration.high_end_mean:.4f} at the upper end and "
        f"{calibration.low_end_mean:.4f} at the lower end. The decays are rounded to {LAMBDA_DECIMALS} decimals.",
        f"# Commuter universe: the {commuters.activities} work and education activities inside {zone_text} that do not carry "
        f"parkingFree ({commuter_excluded_free} more carry parkingFree and are left out: the free-parking draw frees them, so "
        f"the universe holds the payers only), {commuters.with_option_in_range} of them with at least one priced garage within "
        f"{max_distance_m:g} m and {commuters.with_paid_option_in_range} of {commuters.activities} activities with at least one "
        f"PAID option within {max_distance_m:g} m (the activities whose paid-only share can be above 0). "
        f"By purpose: {purposes['commuters']}. "
        + _free_reach_text(commuters, max_distance_m, "they leave the paid-only measure"),
        f"# Target of lambda_c: {commuter_target.numerator_name} / ({commuter_target.numerator_name} + "
        f"{commuter_target.other_name}) = {commuter_target.numerator} / ({commuter_target.numerator} + {commuter_target.other}) "
        f"= {commuter_target.value:.6f} of class {COMMUTER_CLASS} of {commuter_target.table} (the PAYING street and garage "
        "users among the commuters of the Oberbezirk Zentrum, wider than the zones Ia and Ib: like-for-like with a universe "
        "of payers, ruling R-4h-1; the free-inclusive share would put free parkers into the denominator), with the PAID-only "
        "measure (the free option leaves numerator and denominator). A calibration target, no validation; the same universe "
        "caveat applies.",
        f"# Search for lambda_c: the same bisection, {commuter_calibration.iterations} halvings; the mean is "
        f"{commuter_calibration.high_end_mean:.4f} at the upper end and {commuter_calibration.low_end_mean:.4f} at the lower end.",
        "# The same decays apply in every town (transfer assumption). The config values parking_garage_decay_m and "
        "parking_garage_decay_commute_m of configs/base_bs.yml equal decay_length_m and decay_commute_length_m below "
        "(tests/test_parking_garage_decay_config.py).",
        "# Independent check (H3b), computed on the same plans with lambda_c and the PAID-only measure, a number only with no "
        "pre-registered bound and no validation (the reference is a quantity of residents, the model averages over "
        "destinations, and the universe is a sample whose size is stated):",
        f"#   Wolfsburg: mean garage probability of the {wolfsburg.activities} work and education activities without "
        f"parkingFree inside {', '.join(WOLFSBURG_ZONE_IDS)} ({wolfsburg.with_option_in_range} with a priced garage within "
        f"{max_distance_m:g} m, {wolfsburg.with_paid_option_in_range} with a PAID option) against {wolfsburg_reference.value:.4f} "
        f"= {wolfsburg_reference.numerator_name} / ({wolfsburg_reference.numerator_name} + {wolfsburg_reference.other_name}) of "
        f"class {WOLFSBURG_CLASS} of {wolfsburg_reference.table} (paying street and garage users), which the calibration does "
        "not use; PAID-only measure (the free option leaves numerator and denominator). "
        f"By purpose: {purposes['wolfsburg']}. "
        + _free_reach_text(wolfsburg, max_distance_m, "they leave the paid-only measure"),
        "# The paid share of a run is compared with the SrV by scripts/parking/compare_parking_targets.py on the outcomes "
        "the model priced (time-aware); no paid share is checked here (ruling R-5-4). That run comparison counts ALL off-street "
        "options in its commuter and Wolfsburg rows (the outcome report has no paid/free split) and is labelled so.",
    ]
    for entry in sensitivities:
        header.append(
            f"# Sensitivity of the option set (spec Amendment I6; NOT a release value) {entry['label']}: the same calibration "
            f"on the same plans, zones, targets, maximum distance, measures and tolerance with the garage dataset "
            f"{entry['path']} sha256={entry['sha256']}, {entry['options']} options ({entry['kind_counts']}, "
            f"{entry['free_options']} free): lambda = {entry['decay_length_m']:.{LAMBDA_DECIMALS}f} m (achieved mean "
            f"{entry['achieved']:.6f}), lambda_c = {entry['decay_commute_length_m']:.{LAMBDA_DECIMALS}f} m (achieved mean "
            f"{entry['commuter_achieved']:.6f}); the release values are decay_length_m and decay_commute_length_m below.")
    rows = [
        ("decay_length_m", f"{calibration.decay_m:.{LAMBDA_DECIMALS}f}", "m", "calibrated lambda of the garage weights"),
        ("decay_commute_length_m", f"{commuter_calibration.decay_m:.{LAMBDA_DECIMALS}f}", "m",
         "calibrated lambda_c of the garage weights of work and education (ASSUMPTION G1-c)"),
        ("garage_max_distance_m", f"{max_distance_m:g}", "m", "ASSUMPTION G2"),
        ("target_garage_probability", f"{target.value:.6f}", "share",
         f"{target.table}; measure: every option (payment-inclusive)"),
        ("achieved_mean_garage_probability", f"{calibration.achieved:.6f}", "share",
         "mean over the universe at lambda; measure: every option (payment-inclusive)"),
        ("target_commuter_garage_probability", f"{commuter_target.value:.6f}", "share",
         f"{commuter_target.table} class {COMMUTER_CLASS}; measure: paid-only (the free option leaves numerator and denominator)"),
        ("achieved_mean_commuter_garage_probability", f"{commuter_calibration.achieved:.6f}", "share",
         "mean over the commuter universe at lambda_c; measure: paid-only"),
        ("tolerance", f"{tolerance:g}", "share", "absolute in the mean probability"),
        ("universe_activities", str(universe.activities), "count", "destination universe of spec E5"),
        ("commuter_universe_activities", str(commuters.activities), "count",
         "work and education activities in the zones without parkingFree (spec H2)"),
        ("commuter_activities_excluded_parking_free", str(commuter_excluded_free), "count",
         "work and education activities in the zones that carry parkingFree"),
        ("garage_facility_filter_active", "0" if facility_kinds_text == "all" else "1", "flag",
         f"facility kinds used: {facility_kinds_text}"),
        ("priced_garages_used", str(options_used), "count", f"priced options of the facility kinds {facility_kinds_text}"),
        ("priced_free_options_used", str(release.free_options), "count",
         "priced options with the free schedule (cost 0 at every stay duration)"),
        ("universe_activities_with_garage_in_range", str(universe.with_option_in_range), "count",
         "activities with a priced option within D_max (every option)"),
        ("universe_free_options_within_d_max", str(universe.free_options_in_range), "count",
         "distinct free options within D_max of at least one activity of the destination universe"),
        ("universe_activities_with_free_option_in_range", str(universe.with_free_option_in_range), "count",
         "destination universe activities with a free option within D_max"),
        ("commuter_universe_activities_with_garage_in_range", str(commuters.with_option_in_range), "count",
         "commuter activities with a priced option within D_max (every option)"),
        ("commuter_universe_activities_with_paid_option_in_range", str(commuters.with_paid_option_in_range), "count",
         "commuter activities with a paid option within D_max (the paid-only measure can be above 0)"),
        ("commuter_universe_free_options_within_d_max", str(commuters.free_options_in_range), "count",
         "distinct free options within D_max of at least one commuter activity (they leave the paid-only measure)"),
        ("commuter_universe_activities_with_free_option_in_range", str(commuters.with_free_option_in_range), "count",
         "commuter activities with a free option within D_max"),
        ("lambda_search_min_m", f"{lambda_min_m:g}", "m", "lower end of the bisection"),
        ("lambda_search_max_m", f"{lambda_max_m:g}", "m", "upper end of the bisection"),
        ("check_wolfsburg_commuter_mean_garage_probability", f"{release.wolfsburg_mean:.6f}", "share",
         "independent check at lambda_c and no validation; measure: paid-only"),
        ("check_wolfsburg_commuter_reference_share", f"{wolfsburg_reference.value:.6f}", "share",
         f"{wolfsburg_reference.table} class {WOLFSBURG_CLASS}"),
        ("check_wolfsburg_commuter_activities", str(wolfsburg.activities), "count",
         "work and education activities without parkingFree in the Wolfsburg zones"),
        ("check_wolfsburg_commuter_activities_with_garage_in_range", str(wolfsburg.with_option_in_range), "count",
         "Wolfsburg check activities with a priced option within D_max (every option)"),
        ("check_wolfsburg_commuter_activities_with_paid_option_in_range", str(wolfsburg.with_paid_option_in_range), "count",
         "Wolfsburg check activities with a paid option within D_max"),
        ("check_wolfsburg_free_options_within_d_max", str(wolfsburg.free_options_in_range), "count",
         "distinct free options within D_max of at least one Wolfsburg check activity (they leave the paid-only measure)"),
        ("check_wolfsburg_activities_with_free_option_in_range", str(wolfsburg.with_free_option_in_range), "count",
         "Wolfsburg check activities with a free option within D_max"),
    ]
    if any("," in field for row in rows for field in row):
        raise ValueError("a table field contains a comma")   # the long format has no quoting
    body = [",".join(TABLE_COLUMNS)] + [",".join(row) for row in rows]
    return "\n".join(header + body) + "\n"


def read_calibration_table(path) -> dict:
    """The rows of a calibration table as ``{quantity: float}`` (``#`` lines skipped); ``decay_length_m`` and
    ``decay_commute_length_m`` must be present and positive. Raises ``FileNotFoundError`` for a missing file and
    ``ValueError`` for a malformed table, also for a table written before spec Amendment H (no commuter decay)."""
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
    if not values.get("decay_commute_length_m", 0) > 0:
        raise ValueError(f"{path}: decay_commute_length_m is missing or not positive (a table written before spec Amendment H "
                         "has no commuter decay: regenerate it with scripts/parking/calibrate_garage_decay.py)")
    return values


def load_priced_options(path, facility_kinds):
    """``(listed, priced)`` of a garage dataset: the validated frame and its priced rows, restricted to ``facility_kinds``
    (None = every kind). Raises ``ValueError`` when no priced option is left."""
    listed = parking_garages.load_garages(path)
    parking_garages.validate_garages(listed)
    priced = listed[listed["priced"].astype(bool)]
    if priced.empty:
        raise ValueError(f"{path}: no priced garage to calibrate the options of")
    if facility_kinds is not None:
        priced = priced[priced["facility_kind"].isin(facility_kinds)]
        if priced.empty:
            raise ValueError(f"{path}: no priced garage of the facility kind(s) {', '.join(facility_kinds)} to calibrate the "
                             "options of")
    return listed, priced


def validate_sensitivity_garages(sensitivity_garages, release_garages_path) -> list:
    """The sensitivity datasets as ``[(label, path)]`` in the given order, checked BEFORE any plan is read: every label matches
    ``SENSITIVITY_LABEL_PATTERN``, every file exists and no file has the content (SHA-256) of the release dataset (a sensitivity
    that is the release would be no sensitivity). Raises ``ValueError`` / ``FileNotFoundError`` naming the label."""
    if not sensitivity_garages:
        return []
    release_digest = file_sha256(release_garages_path)
    checked = []
    for label, path in dict(sensitivity_garages).items():
        if not isinstance(label, str) or not SENSITIVITY_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"sensitivity label {label!r} must consist of letters, digits, underscore, dot and hyphen")
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"sensitivity dataset {label} not found: {path} (the garage datasets of the lot-size "
                                    "thresholds are written by scripts/curation/parking_zones_2026/regional_garages.py)")
        if file_sha256(path) == release_digest:
            raise ValueError(f"sensitivity dataset {label} ({path}) has the same SHA-256 as the release dataset "
                             f"{release_garages_path}: it is the release, not a sensitivity")
        checked.append((label, path))
    return checked


def run(*, plans, zones_path, garages_path, city_center_path, commute_path, out_path, overwrite: bool = False,
        max_distance_m: float | None = None, config_path=DEFAULT_CONFIG_PATH, lambda_min_m: float = DEFAULT_LAMBDA_MIN_M,
        lambda_max_m: float = DEFAULT_LAMBDA_MAX_M, tolerance: float = DEFAULT_TOLERANCE,
        facility_kinds=None, sensitivity_garages=None, generated_on: str | None = None) -> dict:
    """Calibrate lambda (E5, payment-inclusive) and lambda_c (H2, paid-only) on ``plans``, write the table to ``out_path`` and
    return the results (a dict of the table's numbers, including the paid-only Wolfsburg check of H3b and the sensitivities).

    ``max_distance_m`` (D_max, metres) defaults to the config key ``parking_garage_max_distance_m`` of ``config_path``
    (``read_configured_max_distance_m``; fails when missing); an explicit value is a sensitivity and is recorded in the table.

    ``facility_kinds`` (None = every kind) restricts the priced options to the given ``facility_kind`` values of the garage
    dataset (``garage``, ``surface_lot``), so that lambda can be reported with and without the surface lots; the table
    records the filter. The RELEASE value is the calibration on every kind; a filtered run is a sensitivity number.

    ``sensitivity_garages`` (``{label: path}``) runs the same calibration on other garage datasets (spec Amendment I6, e.g. the
    large-lot thresholds 30 and 100); each result is a header line of the table and an entry of ``result["sensitivities"]``,
    never a release value.

    Raises ``FileNotFoundError`` (naming the data record and the request route) when the zone polygons or the garage dataset,
    both local restricted files, are absent, ``FileExistsError`` when ``out_path`` exists and ``overwrite`` is false
    (a calibrated release value is never replaced silently), ``ValueError`` for an empty universe (destination, commuter
    or Wolfsburg check), an unreachable target (naming the calibration and its measure) or a refused sensitivity. Side effects:
    reads the inputs, logs, writes ``out_path`` (its directory must exist) after every calibration has succeeded.
    """
    out_path = Path(out_path) if out_path is not None else None
    # the zone polygons and the garage dataset are local restricted files (issue #436): name the record and the request route
    restricted_inputs.require_restricted_file("parking_zones_2026", "zone polygons", zones_path)
    restricted_inputs.require_restricted_file("parking_garages_2026", "garage dataset", garages_path)
    if max_distance_m is None:
        max_distance_m = read_configured_max_distance_m(config_path)
        log.info("[garage-decay] maximum garage distance %g m from %s of %s", max_distance_m, MAX_DISTANCE_CONFIG_KEY,
                 path_label(config_path))
    kinds = validate_facility_kinds(facility_kinds)
    if kinds is not None and out_path is not None and out_path.name == Path(TABLE_RELATIVE).name:
        raise ValueError(f"a facility-filtered calibration is a sensitivity number and must not be written under the release "
                         f"table's file name {out_path.name!r} ({out_path}); choose another --out")
    if out_path is not None and out_path.exists() and not overwrite:
        raise FileExistsError(f"{out_path} exists; a calibrated release value is not replaced silently (use --overwrite)")
    sensitivity_inputs = validate_sensitivity_garages(sensitivity_garages, garages_path)
    exposure = load_exposure_module()
    activities, persons = exposure.read_main_activities(plans)
    activities, persons, freight_agents = exclude_freight_agents(activities, persons)
    zone_id = exposure.zone_per_activity(activities, zones_path)
    masks = universe_masks(activities, zone_id)
    if not masks.universe.any():
        raise ValueError(f"no destination of the universe: the {len(activities)} main activities of the {persons} persons "
                         f"contain none outside home, work and education inside {', '.join(CALIBRATION_ZONE_IDS)}")
    if not masks.commuters.any():
        raise ValueError(f"the commuter universe is empty: none of the {len(activities)} main activities of the {persons} "
                         f"persons is a work or education activity without parkingFree inside {', '.join(CALIBRATION_ZONE_IDS)} "
                         f"({masks.commuters_with_parking_free} carry parkingFree); lambda_c (spec H2) cannot be calibrated")
    if not masks.wolfsburg_commuters.any():
        raise ValueError(f"the Wolfsburg check has no universe: none of the {len(activities)} main activities of the {persons} "
                         "persons is a work or education activity without parkingFree inside "
                         f"{', '.join(WOLFSBURG_ZONE_IDS)} (spec H3b); a check that cannot be computed is not skipped")
    target = read_city_center_target(city_center_path)
    commuter_target = read_commuter_reference(commute_path, COMMUTER_CLASS)
    wolfsburg_reference = read_commuter_reference(commute_path, WOLFSBURG_CLASS)
    listed, priced = load_priced_options(garages_path, kinds)
    kinds_text = "all" if kinds is None else "+".join(kinds)
    log.info("[garage-decay] %d main activities of %d persons after the exclusion of %d freight agents with ids %s* (no "
             "residents); priced options %d of %d listed (facility kinds %s)", len(activities), persons, freight_agents,
             freight_filter.FREIGHT_PERSON_PREFIX, len(priced), len(listed), kinds_text)
    purposes = {name: purpose_counts_text(activities[mask]) for name, mask in (
        ("visitors", masks.universe), ("commuters", masks.commuters), ("wolfsburg", masks.wolfsburg_commuters))}
    log.info("[garage-decay] universes by purpose: destination %s; commuter %s; Wolfsburg check %s", purposes["visitors"],
             purposes["commuters"], purposes["wolfsburg"])
    search = dict(max_distance_m=max_distance_m, lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, tolerance=tolerance)
    release = calibrate_option_set(activities, masks, priced, visitor_target=target, commuter_target=commuter_target, **search)
    log.info("[garage-decay] Wolfsburg check (a number, no validation): paid-only garage probability %.4f at lambda_c over %d "
             "work and education activities without parkingFree inside %s (%d with a paid option within %g m) vs reference "
             "%.4f (class %s)", release.wolfsburg_mean, release.wolfsburg_reach.activities, ", ".join(WOLFSBURG_ZONE_IDS),
             release.wolfsburg_reach.with_paid_option_in_range, max_distance_m, wolfsburg_reference.value, WOLFSBURG_CLASS)
    sensitivities = []
    for label, path in sensitivity_inputs:
        _, sensitivity_options = load_priced_options(path, kinds)
        outcome = calibrate_option_set(activities, masks, sensitivity_options, visitor_target=target,
                                       commuter_target=commuter_target, context=f"sensitivity {label}: ", **search)
        sensitivities.append({
            "label": label, "path": path_label(path), "sha256": file_sha256(path), "options": outcome.options,
            "free_options": outcome.free_options, "kind_counts": _kind_counts_text(sensitivity_options),
            "decay_length_m": outcome.visitors.decay_m, "achieved": outcome.visitors.achieved,
            "decay_commute_length_m": outcome.commuters.decay_m, "commuter_achieved": outcome.commuters.achieved})
        log.info("[garage-decay] sensitivity %s (NOT a release value): lambda %.2f m, lambda_c %.2f m on %d options (%d free)",
                 label, outcome.visitors.decay_m, outcome.commuters.decay_m, outcome.options, outcome.free_options)
    inputs = {name: (path_label(path), file_sha256(path)) for name, path in (
        ("plans", plans), ("zones", zones_path), ("garages", garages_path),
        ("srv2023_city_center_parking", city_center_path), ("srv2023_commute_parking_by_workplace_class", commute_path))}
    text = table_text(
        inputs=inputs, persons=persons, freight_agents=freight_agents, purposes=purposes, listed_options=len(listed),
        options_used=len(priced), facility_kinds_text=kinds_text, option_set_text=option_set_text(priced), release=release,
        target=target, commuter_target=commuter_target, wolfsburg_reference=wolfsburg_reference,
        commuter_excluded_free=masks.commuters_with_parking_free, sensitivities=sensitivities, tolerance=tolerance,
        lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, max_distance_m=max_distance_m,
        generated_on=generated_on or datetime.date.today().isoformat(), code_state=git_state())
    if out_path is not None:
        out_path.write_bytes(text.encode("ascii"))
        log.info("[garage-decay] wrote %s", out_path)
    return {"decay_length_m": release.visitors.decay_m, "decay_commute_length_m": release.commuters.decay_m,
            "target": target.value, "achieved": release.visitors.achieved, "commuter_target": commuter_target.value,
            "commuter_achieved": release.commuters.achieved, "universe_activities": release.visitor_reach.activities,
            "with_garage_in_range": release.visitor_reach.with_option_in_range,
            "commuter_universe_activities": release.commuter_reach.activities,
            "commuter_with_garage_in_range": release.commuter_reach.with_option_in_range,
            "commuter_with_paid_option_in_range": release.commuter_reach.with_paid_option_in_range,
            "commuter_activities_excluded_parking_free": masks.commuters_with_parking_free,
            "wolfsburg_activities": release.wolfsburg_reach.activities,
            "wolfsburg_with_garage_in_range": release.wolfsburg_reach.with_option_in_range,
            "wolfsburg_with_paid_option_in_range": release.wolfsburg_reach.with_paid_option_in_range,
            "wolfsburg_mean": release.wolfsburg_mean, "wolfsburg_reference": wolfsburg_reference.value,
            "persons": persons, "freight_agents_excluded": freight_agents, "universe_purposes": purposes,
            "priced_options_used": release.options, "free_options_used": release.free_options,
            "universe_free_option_ids": release.visitor_reach.free_option_ids,
            "commuter_free_option_ids": release.commuter_reach.free_option_ids,
            "wolfsburg_free_option_ids": release.wolfsburg_reach.free_option_ids,
            "universe_free_options_in_range": release.visitor_reach.free_options_in_range,
            "universe_activities_with_free_option": release.visitor_reach.with_free_option_in_range,
            "commuter_free_options_in_range": release.commuter_reach.free_options_in_range,
            "commuter_activities_with_free_option": release.commuter_reach.with_free_option_in_range,
            "wolfsburg_free_options_in_range": release.wolfsburg_reach.free_options_in_range,
            "wolfsburg_activities_with_free_option": release.wolfsburg_reach.with_free_option_in_range,
            "sensitivities": sensitivities, "text": text}


def path_label(path) -> str:
    """The path as recorded in the table: relative to the repository when inside it (POSIX), else as given."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return resolved.as_posix()


def parse_labelled_path(text: str) -> tuple:
    """``"lot_threshold_30=path/to/garages.geojson"`` -> ``("lot_threshold_30", Path(...))`` for ``--sensitivity-garages``;
    ``argparse.ArgumentTypeError`` for a text without a label or without a path."""
    label, separator, path = text.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError(f"{text!r}: expected LABEL=PATH (a label and the path of a garage dataset)")
    return label.strip(), Path(path.strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plans", type=Path, required=True, help="MATSim plans file (.xml or .xml.gz) of the reference scenario")
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH, help="data directory holding the committed inputs")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"the calibration table to write (default: <data-path>/{TABLE_RELATIVE})")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing calibration table")
    parser.add_argument("--max-distance-m", type=float, default=None,
                        help=f"D_max in metres (default: the config key {MAX_DISTANCE_CONFIG_KEY} of --config; an explicit "
                             "value is a sensitivity and is recorded in the table)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="pipeline config holding the key of D_max (default: configs/base_bs.yml)")
    parser.add_argument("--lambda-min-m", type=float, default=DEFAULT_LAMBDA_MIN_M)
    parser.add_argument("--lambda-max-m", type=float, default=DEFAULT_LAMBDA_MAX_M)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--sensitivity-garages", dest="sensitivity_garages", action="append", type=parse_labelled_path,
                        metavar="LABEL=PATH", default=None,
                        help="a sensitivity of the option set (spec Amendment I6): the same calibration on another garage "
                             "dataset (e.g. the large-lot thresholds 30 and 100), written as a header line of the table, "
                             "labelled sensitivity and never a release value; repeatable, a label once")
    parser.add_argument("--facility-kinds", type=parse_facility_kinds, default=None, metavar="KIND[,KIND]|all",
                        help="calibrate on the priced options of these facility kinds only (garage, surface_lot; default all); "
                             "a filtered run is a sensitivity number and must not be written over the release table. "
                             "It matters only where surface lots lie within D_max of the universe: since Task 4j the "
                             "Braunschweig option set has large lots, so 'garage' is a garages-only sensitivity there")
    args = parser.parse_args(argv)
    data = args.data_path
    if args.facility_kinds is not None:
        # a filtered run is a sensitivity number: it must never replace the release table (the default --out)
        if args.out is None or args.out.resolve() == (data / TABLE_RELATIVE).resolve():
            parser.error("--facility-kinds gives a sensitivity number, not the release value: pass --out with another path "
                         f"than the release table {data / TABLE_RELATIVE}")
    sensitivity_garages = None
    if args.sensitivity_garages is not None:
        sensitivity_garages = {}
        for label, path in args.sensitivity_garages:
            if label in sensitivity_garages:
                parser.error(f"--sensitivity-garages: the label {label!r} is given twice")
            sensitivity_garages[label] = path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    run(plans=args.plans, zones_path=data / ZONES_RELATIVE, garages_path=data / GARAGES_RELATIVE,
        city_center_path=data / CITY_CENTER_RELATIVE, commute_path=data / COMMUTE_RELATIVE,
        out_path=args.out or data / TABLE_RELATIVE, overwrite=args.overwrite, max_distance_m=args.max_distance_m,
        config_path=args.config, lambda_min_m=args.lambda_min_m, lambda_max_m=args.lambda_max_m, tolerance=args.tolerance,
        facility_kinds=args.facility_kinds, sensitivity_garages=sensitivity_garages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
