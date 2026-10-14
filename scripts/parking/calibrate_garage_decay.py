"""Calibrate the decay lengths of the garage options on a plans file (parking cost zones v2 and v3, #436).

Spec Amendments E5, H2 and I6 and spec v3 B5.

A curation and analysis aid, never a pipeline stage. The off-street options of a car stay in a paid zone (the garages and, since
spec Amendment I, the large public surface lots of the garage dataset) compete with the street in the choice weights of the
pricing. The decay length lambda is CALIBRATED, not estimated (ASSUMPTION G3): it is set so that the mean off-street share of the
destination universe equals the SrV 2023 target. There are TWO decay lengths on the same plans (spec Amendment H1): lambda for
every purpose but work and education (E5, the city centre visitors) and lambda_c for work and education (ASSUMPTION G1-c, H2, the
commuters); the weights of a stay use the one its purpose selects (``braunschweig.parking.cost.garage_decay_m_for_purpose``).

ROUTING THROUGH THE PRODUCTION PRICING (spec v3 B5, Part A final review). The calibration has NO weight formula of its own. Every
stay of every universe is priced and weighted by ``braunschweig.parking.cost.parking_cost_with_garages_detail`` (called as
``cost.parking_cost_with_garages_detail``), the Python reference of the Java pricing: the early rules, E4 (a free street pays no
option), the maximum-stay FEASIBILITY of spec v3 A1 (the street leaves the option set above its maximum stay unless a long-stay
product exists; a garage or lot above its own maximum stay leaves it too), the price of every option for THIS stay, and the
weights ``cost.option_weight`` = ``exp(-d / lambda - beta * c / 100)`` of spec v3 A2 (the price term, ASSUMPTION G1-d; d the
straight-line distance in metres, c the cents the option charges for the stay, beta the option price coefficient in utility
per EUR, the street at distance 0 with its own cents, and only where the street is feasible). The share of a stay is read from the
detail object that function returns (``garage_probability``, ``street_weight``, ``option_weights``, ``feasible_option_ids``), so the
calibration measures what the model prices. A closed form ``exp(-d / lambda)`` with street weight 1 (the v2 weight) is gone
from this file; with ``--beta 0`` the production weight IS that form bit for bit (tested).

Definitions (the committed calibration table records them, so that its number can be read without this file):

* Option: a PRICED row of the garage dataset (a garage or a surface lot) converted with
  ``braunschweig.parking.tariff_export.garage_row_to_tariff`` (the conversion of the tariff model, with the monthly-product
  imputation of the config key ``parking_garage_monthly_imputation``). An option is FREE when its schedule is the free schedule
  ``garages.FREE_SCHEDULE_TEXT`` ('0- free': one open free band, no tiers, cost 0 at every stay duration, tested by
  ``garages.is_free_schedule``) and PAID otherwise. The marker is read from the schedule (``is_free_option``), never from a name
  or a rate; a grace period (a free first band that something follows) is no free option. The FREE marker decides only which
  options leave the PAID-ONLY measure; it does not change a weight (a free option weighs ``exp(-d / lambda)``: its price is 0).
* Stay: one main activity of a universe, priced as the activity's own time interval (spec v3 A1: "the duration of a calibration
  activity is its activity duration, with the minimum stay L1"). The arrival is the ``start_time`` and the departure the
  ``end_time`` of the activity in the plans; an activity without ``end_time`` is a terminal stay and pays until the fee window of
  its arrival day ends (rule T1, ``cost.terminal_departure_s``); the priced departure is extended to the minimum stay
  ``parking_minimum_stay_min`` of the config (rule L1, ``cost.minimum_stay_departure_s``). A planned end before the arrival is
  priced from the arrival by the minimum stay and COUNTED (``planned_end_before_arrival``); an activity of a universe without
  ``start_time`` fails the run (a stay is never priced from midnight). The arrival is the start of the ACTIVITY, i.e. after the
  walk from the car, not the car arrival of the Java pricing: a limitation of the plans (they hold no simulated arrival).
* NOT APPLIED to a calibration stay (limitations, stated in the table): the free-parking draw ``parkingFree`` (the commuter
  universe is defined without it; the visitors keep it unapplied as in v2), the resident exemptions R1 and R2 (a stay is not
  exempt) and the EV exemption of spec v3 A5. The plans of the reference run (run manifest parking-v2-plans-25pct-2026-10-12)
  predate the EV rule and carry no ``parkingEvExempt`` attribute, so the calibration treats every stay as not exempt:
  ASSUMPTION, with that reason; the model run prices exempt stays free, so the calibration slightly overstates the paid street
  side wherever an EV-exempt person parks in a zone with an exemption. The fee window of the street, E4, the maximum stay and the
  prices DO act, because they are part of the production pricing: a stay outside the street fee window or with a free street
  (within the free threshold) has an off-street share of 0, as in the model.
* Two measures (spec Amendment I6): the model share has the universe of the SrV quantity of its target. The share of ONE stay is
  taken from the production detail object.

  - PAYMENT-INCLUSIVE (``MEASURE_ALL_OPTIONS``; lambda, E5): the share of a stay is its ``garage_probability``, the probability
    mass of EVERY feasible off-street option within D_max, ``sum(w_garage) / (w_street + sum(w_garage))`` (the street term only
    where it is feasible). The SrV shares of ``srv2023_city_center_parking`` contain free and paying parkers, so free options
    count.
  - PAID-ONLY (``MEASURE_PAID_OPTIONS``; lambda_c, H2, and the Wolfsburg check, H3b): ``W_paid / (W_paid + w_street)`` with
    ``W_paid`` the sum of the production weights of the feasible PAID options. The SrV quantity is paid garage / (paid garage +
    paid street) among the paying street and garage users (ruling R-4h-1); a free option is a free parker in SrV terms and leaves
    numerator and denominator. Derivation: with W the sum of all weights, the model's choice probabilities are street
    ``w_street / W`` and option i ``w_i / W``; the paid-only share ``sum(p_i, i paid) / (sum(p_i, i paid) + p_street)`` is
    ``W_paid / (W_paid + w_street)``, because the factor ``W`` cancels: it is the payment-inclusive form on the paid options alone.
    With beta 0 the street weighs 1 and this is ``W_paid / (1 + W_paid)``, the v2 form. A stay whose street is not feasible has
    the paid-only share 1 when a paid option is feasible and 0 when none is (no paying parker exists). A stay without a paid
    option within D_max has the paid-only share 0 (its paying users park on the street). A universe is averaged over its
    activities, as in E5; this mean of the per-activity shares equals the pooled ratio of the summed probabilities, ``sum(p_paid)
    / sum(p_paid + p_street)``, when no free option is in range of any activity of the universe and every stay is an
    expectation over the street and the options (then ``p_paid + p_street`` is 1 for every activity, so the denominator of the
    pooled ratio is the number of activities); with free options in range, or stays that are decided before the options act, the
    two may differ (the pooled ratio is not computed).
* NO FEASIBLE OPTION (spec v3 A1): a stay whose street cannot serve it (maximum stay exceeded, no long-stay product, no commuter
  product) and for which no garage or lot within D_max can serve it either. The production pricing prices it as the street at
  its metered price (outcome ``NO_FEASIBLE_OPTION``, a signal that the option set is incomplete). The calibration gives such a stay
  an off-street share of 0 in every measure (it contributes an off-street share of 0; the share is NEVER invented as 1), COUNTS
  it per universe and logs the rate ``no feasible option n/N``; a rate above ``--maximum-no-feasible-option-share-warning`` is
  warned about (a log-only plausibility threshold that changes no result). A stay whose street is infeasible but that has a
  feasible option is FORCED off-street: its share is 1 (spec v3 B5), counted as ``street infeasible``.
* Destination universe: every main activity of the selected plans (the activities of type ``* interaction`` that the router
  inserts are no destinations; the freight agents, ids ``freight_*``, are trip creators and no residents and are excluded
  with the shared ``braunschweig.analysis.freight_filter``, counted in the log and the header) whose type is none of
  ``HOME_AND_COMMUTER_PURPOSES`` (home, work, education) and that lies in one of the calibration zones
  ``CALIBRATION_ZONE_IDS`` (``bs_zone_ia``, ``bs_zone_ib``), whatever the mode of the trip that ends there. The zone of an
  activity is the one ``braunschweig.parking.attach.attach_parking_zones`` assigns, recomputed on the zone polygons of the data
  path; the plans supply the coordinates, the purposes, the activity times and the activity attribute ``parkingFree`` (the
  free-parking draw, which does not depend on the garage dataset), so the plans need no regeneration for another option set. The
  tariff of a stay is the row of its zone in the tariff table (``--data-path``/parking_tariffs_2026.csv, pinned in the header).
* Share of a destination (payment-inclusive measure, lambda): see the measures above; the price enters through the weights
  (spec v3 A2) and E4 and the early rules of the production pricing act (the payment-status flags above are not applied).
* Target of lambda: ``share(garage_large_lot) / (share(garage_large_lot) + share(street))`` of the SrV 2023 table
  ``srv2023_city_center_parking`` among the Braunschweig residents who park on the street or in a garage or large lot when
  they drive to the city centre (employer lots and other places leave the denominator because the model handles them by
  the employer-free draw), READ from the committed table, never typed here. Universe caveat: SrV asks residents about their
  usual place, the model averages over the destinations of all persons of the plans. The target is a comparison reference of a
  calibration, no validation.
* D_max: the config key ``parking_garage_max_distance_m`` of ``configs/base_bs.yml`` (``--config``; ASSUMPTION G2), read and
  never defaulted from a code constant: the script fails when the key is missing. ``--max-distance-m`` overrides it (a
  sensitivity; the table records the value used).
* The pricing parameters come from the same config (``read_pricing_config``): ``parking_minimum_stay_min`` (L1),
  ``parking_garage_monthly_imputation``, ``parking_tariff_snapshot_date``, ``parking_terminal_stay_rule`` and
  ``parking_option_price_beta_u_per_eur`` (beta); a key the config does not carry takes the default of
  ``braunschweig.matsim.simulation.prepare.PARKING_DEFAULTS``, as the pipeline does, and the values used are logged and written to
  the table. ``--beta`` overrides beta (a sensitivity: a beta other than the config's must not be written under the release
  table's name); ``--sensitivity-beta`` (repeatable) adds header lines for other coefficients next to the release values
  (``--sensitivity-beta 0`` is the price-independent form of v2).
* Search: bisection on lambda in ``[--lambda-min-m, --lambda-max-m]`` (default 10 to 5000 m), which stops when the mean
  share is within ``--tolerance`` (default 0.0005) of the target; the mean share is non-decreasing in lambda (every option weight
  rises with lambda while the street weight does not depend on it), so the search fails (``ValueError``) when the target lies
  outside the range at its two ends, which also happens when stays that are forced off-street already exceed the target. The
  reported lambda is rounded to 0.01 m and its achieved mean recomputed.
* Commuter universe (H2): the main activities of type work or education (``cost.COMMUTER_PURPOSES``) inside the same
  calibration zones that do NOT carry the activity attribute ``parkingFree``. The free-parking draw frees everyone whose
  payment is free (``share_free_total`` of the class), so the universe holds the PAYERS only; the activities that carry
  parkingFree are counted and named. In zone Ia (maximum stay 180 min, no long-stay product) every commuter stay of more than 180
  chargeable minutes is forced off-street (spec v3 B5): the street payers of the SrV target therefore map to Ib and the ParkGO
  zone-II islands, which the table states. Like-for-like commuter target (ruling R-4h-1, revision of H2, with the paid-only measure
  of I6): the PAID-only share ``share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid)`` of the row
  ``bs_zentrum`` of ``srv2023_commute_parking_by_workplace_class``, READ from the table, never typed. (The free-inclusive share
  ``share_garage_large_lot / (share_garage_large_lot + share_street)`` has the free street and garage parkers in its
  denominator, which the model's universe does not contain, and is not used.) The same bisection on the PAID measure gives
  lambda_c. The bs_zentrum paid share is therefore a calibration target and no longer checks the model.
* Independent check (H3b) on the SAME plans, reported as a number and never as validation: the paid-only mean share with
  lambda_c of the work and education activities without parkingFree inside the Wolfsburg zones
  ``wolfsburg_zone_ids`` (every zone of the tariff table with municipality_ags 03103000: the tariff zones and, since spec v3 B2,
  the Wolfsburg area zones) against the same paid-only share of the row ``03103`` of the same table, with no pre-registered bound.
  The universe size is stated; the reference is a quantity of residents, the model averages over destinations. The paid share is
  NOT checked here (ruling R-5-4, issue #436): a zone-level check, blind to the fee window, the free threshold and the maximum
  stay, read 1.0 in the zones Ia and Ib and said nothing. The time-aware paid share is computed on the outcomes of a model run by
  ``scripts/parking/compare_parking_targets.py``, whose commuter and Wolfsburg rows count ALL off-street options (the outcome
  report has no paid/free split), so that run comparison is not exact where free options lie within D_max.
* Sensitivities (spec Amendment I6 and v3 B5): ``--sensitivity-garages LABEL=PATH`` (repeatable) runs the SAME calibration (plans,
  zones, tariffs, targets, D_max, beta, measures, tolerance) on another garage dataset, e.g. the large-lot thresholds 30 and 100
  of Task 4j, and writes lambda and lambda_c of each as header lines of the table, labelled sensitivity and never a release value;
  ``--sensitivity-beta`` does the same for another option price coefficient on the release dataset. The release rows are those of
  the primary dataset at the release beta. A sensitivity dataset with the content of the primary one, or a coefficient equal to
  the release one, is refused.

Fallback transparency: the log and the table report, per universe, how every stay was priced (``StayOutcomes``: priced as an
expectation over the street and the options, of which forced off-street because the street is infeasible; priced as the street
only; free street (E4); outside the street fee window; NO FEASIBLE OPTION), how many activities have an option of the measure
within D_max (the share whose probability can be positive at all; a universe below the share of
``--minimum-reach-share-warning``, default ``MINIMUM_REACH_SHARE_WARNING``, is warned about: a log-only plausibility threshold
that changes no result) and how many free options lie within D_max of the universe (distinct options and activities reached); a
target that the options cannot reach fails instead of returning the end of the range, and an empty commuter or Wolfsburg
universe fails instead of skipping its row.

Output: the committed table ``parking_garage_decay_calibration_2026.csv`` (long format ``quantity,value,unit,note`` under a
header that names the inputs with their SHA-256, the measures, the pricing parameters, all three universes, both target
derivations, the sensitivities and the code state; the rows ``decay_length_m`` and ``decay_commute_length_m`` are the release
values of the config keys ``parking_garage_decay_m`` and ``parking_garage_decay_commute_m``, the row
``option_price_beta_u_per_eur`` the coefficient they were calibrated with). The table belongs to the garage dataset, the zone
file and the tariff table whose SHA-256 its header names: another option set needs another calibration. The script refuses to
replace an existing table unless ``--overwrite`` is given. Run it on the plans of the reference scenario only (the 25 % plans
run of issue #436); a plans file of another scenario gives another lambda.

Usage (from the repository root)::

    python scripts/parking/calibrate_garage_decay.py --plans <population.xml.gz> [--data-path eqasim-data/data]
        [--out eqasim-data/data/braunschweig/parking/parking_garage_decay_calibration_2026.csv] [--overwrite]
        [--sensitivity-garages lot_threshold_30=<geojson> --sensitivity-garages lot_threshold_100=<geojson>]
        [--sensitivity-beta 0] [--beta <utility per EUR>] [--facility-kinds garage] [--minimum-reach-share-warning 0.5]
        [--maximum-no-feasible-option-share-warning 0.1]

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
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.analysis import freight_filter  # noqa: E402
from braunschweig.parking import (cost, garages as parking_garages, restricted_inputs, tariff_export,  # noqa: E402
                                  zones as parking_zones)

log = logging.getLogger("calibrate_garage_decay")

DEFAULT_DATA_PATH = REPO / "eqasim-data" / "data"
ZONES_RELATIVE = "braunschweig/parking/parking_zones_2026.geojson"
TARIFFS_RELATIVE = "braunschweig/parking/parking_tariffs_2026.csv"
GARAGES_RELATIVE = "braunschweig/parking/parking_garages_2026.geojson"
CITY_CENTER_RELATIVE = "braunschweig/srv/srv2023_city_center_parking.csv"
COMMUTE_RELATIVE = "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv"
TABLE_RELATIVE = "braunschweig/parking/parking_garage_decay_calibration_2026.csv"
DEFAULT_CONFIG_PATH = REPO / "configs" / "base_bs.yml"
#: The pipeline config key of ASSUMPTION G2 (the maximum straight-line distance of a garage option, metres): the single
#: source of D_max of the calibration, so that the calibrated lambda and the run use the same distance.
MAX_DISTANCE_CONFIG_KEY = "parking_garage_max_distance_m"
#: The other pipeline config keys of the pricing (spec v3 B5); a key the config does not carry takes the default of
#: ``braunschweig.matsim.simulation.prepare.PARKING_DEFAULTS``, as the pipeline does.
OPTION_PRICE_BETA_CONFIG_KEY = "parking_option_price_beta_u_per_eur"
MINIMUM_STAY_CONFIG_KEY = "parking_minimum_stay_min"
MONTHLY_IMPUTATION_CONFIG_KEY = "parking_garage_monthly_imputation"
SNAPSHOT_DATE_CONFIG_KEY = "parking_tariff_snapshot_date"
TERMINAL_STAY_RULE_CONFIG_KEY = "parking_terminal_stay_rule"
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
#: Spec H3b: the Wolfsburg zones are every zone of the tariff table of this municipality (``wolfsburg_zone_ids``: the three tariff
#: zones and, since spec v3 B2, the area zones carved out of them; a hand-kept list silently dropped the area zones) and the
#: table row of Wolfsburg, whose commuter garage share is the independent CHECK (a number, no validation).
WOLFSBURG_AGS = "03103000"
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
#: is warned about (its probability is then 0 for the rest). The default of ``--minimum-reach-share-warning``: a log-only
#: plausibility threshold that changes no result, not a scientific parameter.
MINIMUM_REACH_SHARE_WARNING = 0.5
#: Fallback transparency (spec v3 B5): a universe in which more than this share of the stays has NO FEASIBLE OPTION is warned
#: about (such a stay has the off-street share 0 in every measure, so a high rate says that the option set or the maximum stays
#: are incomplete). The default of ``--maximum-no-feasible-option-share-warning``: a log-only plausibility threshold that
#: changes no result, not a scientific parameter.
MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING = 0.1
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


def _config_section(config_path) -> dict | None:
    """The ``config`` section of a pipeline config file as a dict (None when the file has none). Raises ``FileNotFoundError``
    for a missing file."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"pipeline config missing: {path} (it carries {MAX_DISTANCE_CONFIG_KEY} and the pricing keys)")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = document.get("config") if isinstance(document, dict) else None
    return section if isinstance(section, dict) else None


def read_configured_max_distance_m(config_path=DEFAULT_CONFIG_PATH) -> float:
    """D_max (ASSUMPTION G2, metres) as configured: the key ``parking_garage_max_distance_m`` of the ``config`` section of
    ``config_path`` (default ``configs/base_bs.yml``). Raises ``ValueError`` naming the key when it is missing or no
    positive number; there is no fallback to a code constant, because a lambda calibrated for another distance than the run
    uses would be silently inconsistent. Reads the file."""
    path = Path(config_path)
    section = _config_section(path)
    value = section.get(MAX_DISTANCE_CONFIG_KEY) if section is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{path}: config key {MAX_DISTANCE_CONFIG_KEY} must be a positive number of metres, got {value!r}"
                         + ("" if section is not None else " (the file has no 'config' section)"))
    return float(value)


class PricingConfig(NamedTuple):
    """The pricing parameters of the calibration (spec v3 B5), from the pipeline config. ``option_price_beta_u_per_eur`` is the
    coefficient USED (an explicit sensitivity value or the configured one) and ``configured_option_price_beta_u_per_eur`` the
    one the config (or ``PARKING_DEFAULTS``) names, which the release table must have been calibrated with. ``defaulted`` lists the
    keys the config does not carry (they took the default of ``PARKING_DEFAULTS``, as the pipeline does)."""

    max_distance_m: float
    option_price_beta_u_per_eur: float
    configured_option_price_beta_u_per_eur: float
    minimum_stay_min: int
    monthly_imputation: bool
    tariff_snapshot_date: str
    terminal_stay_rule: str
    defaulted: tuple


def _beta_value(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{where}: {OPTION_PRICE_BETA_CONFIG_KEY} must be a finite number >= 0 (utility per EUR; 0 = the "
                         f"price-independent weights of v2), got {value!r}")
    return float(value)


def _pricing_defaults() -> dict:
    """The pipeline defaults of the pricing keys (``prepare.PARKING_DEFAULTS``), imported on demand: the preparation stage is the
    one place that declares them, so the calibration cannot drift from the run."""
    from braunschweig.matsim.simulation import prepare

    return prepare.PARKING_DEFAULTS


def read_configured_option_price_beta(config_path=DEFAULT_CONFIG_PATH) -> float:
    """The option price coefficient (utility per EUR) of the run as configured: the key ``parking_option_price_beta_u_per_eur``
    of ``config_path`` or, when the file does not carry it, the default of ``prepare.PARKING_DEFAULTS`` (as the pipeline does).
    Raises ``ValueError`` naming the key for an unusable value."""
    section = _config_section(config_path) or {}
    if OPTION_PRICE_BETA_CONFIG_KEY in section:
        return _beta_value(section[OPTION_PRICE_BETA_CONFIG_KEY], str(config_path))
    return _beta_value(_pricing_defaults()[OPTION_PRICE_BETA_CONFIG_KEY], "prepare.PARKING_DEFAULTS")


def read_pricing_config(config_path=DEFAULT_CONFIG_PATH, *, max_distance_m: float | None = None,
                        option_price_beta_u_per_eur: float | None = None) -> PricingConfig:
    """The pricing parameters of the calibration from ``config_path`` (``config`` section), spec v3 B5.

    D_max is mandatory (``read_configured_max_distance_m``) unless ``max_distance_m`` is given (a sensitivity). The other keys
    (``parking_option_price_beta_u_per_eur``, ``parking_minimum_stay_min``, ``parking_garage_monthly_imputation``,
    ``parking_tariff_snapshot_date``, ``parking_terminal_stay_rule``) take the default of ``prepare.PARKING_DEFAULTS`` when the
    config does not carry them, as the pipeline does; the keys that were defaulted are named in ``defaulted``.
    ``option_price_beta_u_per_eur`` overrides the coefficient (a sensitivity, ``configured_option_price_beta_u_per_eur`` stays
    the configured one). Raises ``ValueError`` naming the key for an unusable value, ``FileNotFoundError`` for a missing file.
    """
    path = Path(config_path)
    section = _config_section(path) or {}
    defaults = _pricing_defaults()
    defaulted = []

    def value_of(key):
        if key in section:
            return section[key]
        defaulted.append(key)
        return defaults[key]

    distance_m = read_configured_max_distance_m(path) if max_distance_m is None else float(max_distance_m)
    configured_beta = _beta_value(value_of(OPTION_PRICE_BETA_CONFIG_KEY), str(path))
    beta = configured_beta if option_price_beta_u_per_eur is None else _beta_value(option_price_beta_u_per_eur,
                                                                                    "the explicit coefficient")
    minimum_stay_min = value_of(MINIMUM_STAY_CONFIG_KEY)
    if (isinstance(minimum_stay_min, bool) or not isinstance(minimum_stay_min, (int, float))
            or not math.isfinite(minimum_stay_min) or minimum_stay_min < 0 or minimum_stay_min != int(minimum_stay_min)):
        raise ValueError(f"{path}: config key {MINIMUM_STAY_CONFIG_KEY} must be a whole number of minutes >= 0, got "
                         f"{minimum_stay_min!r}")
    monthly_imputation = value_of(MONTHLY_IMPUTATION_CONFIG_KEY)
    if not isinstance(monthly_imputation, bool):
        raise ValueError(f"{path}: config key {MONTHLY_IMPUTATION_CONFIG_KEY} must be true or false, got {monthly_imputation!r}")
    snapshot = value_of(SNAPSHOT_DATE_CONFIG_KEY)
    if isinstance(snapshot, datetime.date):   # an unquoted YAML date
        snapshot = snapshot.isoformat()
    try:
        snapshot = tariff_export.check_snapshot_date(snapshot)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{path}: config key {SNAPSHOT_DATE_CONFIG_KEY}: {error}") from error
    rule = value_of(TERMINAL_STAY_RULE_CONFIG_KEY)
    if rule not in tariff_export.SUPPORTED_TERMINAL_STAY_RULES:
        raise ValueError(f"{path}: config key {TERMINAL_STAY_RULE_CONFIG_KEY} must be one of "
                         f"{tariff_export.SUPPORTED_TERMINAL_STAY_RULES}, got {rule!r}")
    return PricingConfig(max_distance_m=distance_m, option_price_beta_u_per_eur=beta,
                         configured_option_price_beta_u_per_eur=configured_beta, minimum_stay_min=int(minimum_stay_min),
                         monthly_imputation=monthly_imputation, tariff_snapshot_date=snapshot, terminal_stay_rule=rule,
                         defaulted=tuple(defaulted))


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


class Stay(NamedTuple):
    """One car stay of a calibration universe, ready for the production pricing: the tariff of its zone, the purpose (it selects
    the decay length), the arrival and the PRICED departure in seconds (after the terminal-stay rule T1 and the minimum stay L1)
    and the garage options within D_max, ``(GarageTariff, distance_m)`` by ascending garage id."""

    zone_id: str
    tariff: cost.ZoneTariff
    purpose: str
    arrival_s: int
    departure_s: int
    options: tuple


class StayTimeNotes(NamedTuple):
    """How the stays of a universe got their priced departure (fallback transparency): ``terminal_stays`` have no ``end_time`` in
    the plans (rule T1) and ``planned_end_before_arrival`` have a planned end before their arrival (priced from the arrival by the
    minimum stay)."""

    terminal_stays: int
    planned_end_before_arrival: int


def priced_stay_times_s(start_time_s, end_time_s, *, fee_end_s: int, minimum_stay_s: int) -> tuple:
    """``(arrival_s, priced_departure_s, kind)`` of a stay from the activity times of the plans (spec v3 A1: the duration of a
    calibration activity is its activity duration, with the minimum stay L1).

    ``start_time_s`` is the arrival (NaN raises ``ValueError``: a stay is never priced from midnight), ``end_time_s`` the planned
    departure (NaN = a terminal stay: rule T1, ``cost.terminal_departure_s`` with the street fee end ``fee_end_s`` of the zone).
    Seconds are rounded to whole numbers. The priced departure is extended to ``minimum_stay_s`` (rule L1,
    ``cost.minimum_stay_departure_s``). ``kind`` is ``"planned"``, ``"terminal_stay_rule"`` or ``"planned_end_before_arrival"``
    (the planned end precedes the arrival; the stay is priced from the arrival by the minimum stay). Pure."""
    if start_time_s is None or not math.isfinite(start_time_s):
        raise ValueError("the activity has no start_time: the calibration prices a stay from its activity times and never from "
                         "midnight (the plans of the reference run write start_time on every activity after the first)")
    arrival_s = int(round(start_time_s))
    if end_time_s is None or not math.isfinite(end_time_s):
        departure_s, kind = cost.terminal_departure_s(arrival_s, fee_end_s), "terminal_stay_rule"
    else:
        departure_s, kind = int(round(end_time_s)), "planned"
        if departure_s < arrival_s:
            departure_s, kind = arrival_s, "planned_end_before_arrival"
    return arrival_s, cost.minimum_stay_departure_s(arrival_s, departure_s, minimum_stay_s), kind


def build_stays(activities: pd.DataFrame, zone_ids: pd.Series, tariffs_by_zone: Mapping, garages: Sequence, *,
                max_distance_m: float, minimum_stay_s: int) -> tuple:
    """``(stays, StayTimeNotes)`` of the rows of ``activities`` (columns ``purpose``, ``x``, ``y``, ``start_time_s``,
    ``end_time_s``; ``zone_ids`` aligned by position). Every stay carries the ``ZoneTariff`` of its zone
    (``tariffs_by_zone``), its times (``priced_stay_times_s``) and the garage options within ``max_distance_m`` of its location
    from the production function ``cost.garage_options_in_range`` (inclusive; ``garages`` are ``GarageTariff`` objects).
    Raises ``ValueError`` naming the zone for a zone without a tariff row and naming the person-independent row for an activity
    without ``start_time``."""
    stays, terminal, reversed_ = [], 0, 0
    columns = (activities["purpose"].to_numpy(), activities["x"].to_numpy(dtype=float), activities["y"].to_numpy(dtype=float),
               activities["start_time_s"].to_numpy(dtype=float), activities["end_time_s"].to_numpy(dtype=float),
               np.asarray(zone_ids, dtype=object))
    for position, (purpose, x_m, y_m, start_s, end_s, zone_id) in enumerate(zip(*columns)):
        tariff = tariffs_by_zone.get(zone_id)
        if tariff is None:
            raise ValueError(f"no tariff row for the zone {zone_id!r} of a calibration activity: the tariff table and the zone "
                             "polygons must come from the same release")
        try:
            arrival_s, departure_s, kind = priced_stay_times_s(start_s, end_s, fee_end_s=tariff.fee_end_s,
                                                               minimum_stay_s=minimum_stay_s)
        except ValueError as error:
            raise ValueError(f"{error} (activity {position} of the universe: purpose {purpose}, zone {zone_id})") from error
        terminal += kind == "terminal_stay_rule"
        reversed_ += kind == "planned_end_before_arrival"
        stays.append(Stay(zone_id, tariff, str(purpose), arrival_s, departure_s,
                          tuple(cost.garage_options_in_range(garages, float(x_m), float(y_m), max_distance_m))))
    return stays, StayTimeNotes(terminal, reversed_)


def price_stay(stay: Stay, decay_m: float, option_price_beta_u_per_eur: float) -> cost.GarageStayPrice:
    """The production pricing of ONE stay: ``cost.parking_cost_with_garages_detail`` with the stay's tariff, times, purpose and
    options, the decay length ``decay_m`` (both decay arguments, so the decay of the purpose is the one given) and the option
    price coefficient. The payment-status flags are not applied (``parkingFree``, resident and EV exemptions are False, see the
    module documentation); the early rule of the fee window, E4, the maximum-stay feasibility and the price-weighted options act.
    This is the ONLY weight computation of the calibration: the weights are those ``cost.option_weight`` gives. Pure."""
    return cost.parking_cost_with_garages_detail(
        stay.tariff, stay.arrival_s, stay.departure_s, purpose=stay.purpose, parking_free=False, resident_of_zone=False,
        resident_of_district=False, ev_exempt=False, garage_options=stay.options, decay_m=decay_m, decay_commute_m=decay_m,
        option_price_beta_u_per_eur=option_price_beta_u_per_eur)


def option_share(detail: cost.GarageStayPrice, measure: str, paid_by_id: Mapping) -> float:
    """The off-street share of ONE priced stay for a measure (spec I6, v3 B5), read from the production detail object.

    ``MEASURE_ALL_OPTIONS``: the probability mass of the feasible garage and lot options, ``detail.garage_probability`` (0 where
    no expectation was formed: a stay decided by an early rule or E4, one with the street as the only option, and NO FEASIBLE
    OPTION, which is never given an invented share of 1). ``MEASURE_PAID_OPTIONS``: ``W_paid / (W_paid + w_street)`` over the
    feasible PAID options (``paid_by_id`` maps a garage id to True for a paid option) with the production weights; 1 where the
    street is not an option and a paid option is feasible, 0 where no paid option is feasible. Raises ``ValueError`` for another
    measure or a garage id missing from ``paid_by_id``."""
    if measure not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; the measures are {MEASURES}")
    if measure == MEASURE_ALL_OPTIONS:
        return detail.garage_probability
    if detail.outcome != cost.PAID_EXPECTED:
        return 0.0
    paid_weight = 0.0
    for garage_id, weight in zip(detail.feasible_option_ids, detail.option_weights):
        if garage_id not in paid_by_id:
            raise ValueError(f"garage option {garage_id!r} has no paid flag: the options of a stay must be options of the "
                             "calibrated dataset")
        if paid_by_id[garage_id]:
            paid_weight += weight
    total = paid_weight + detail.street_weight
    return paid_weight / total if total > 0.0 else 0.0


def mean_option_share(stays: Sequence, decay_m: float, option_price_beta_u_per_eur: float, measure: str,
                      paid_by_id: Mapping) -> float:
    """Mean over ``stays`` of ``option_share`` of the production pricing at the decay length ``decay_m`` (a universe is averaged
    over its activities, as in E5). Raises ``ValueError`` for an empty universe, a decay that is no positive finite number of
    metres (the production pricing reads 0 as "options off", which is no calibration state) and another measure."""
    if measure not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; the measures are {MEASURES}")
    if not stays:
        raise ValueError("the destination universe is empty: no stay to average the off-street share over")
    if isinstance(decay_m, bool) or not isinstance(decay_m, (int, float)) or not (math.isfinite(decay_m) and decay_m > 0):
        raise ValueError(f"decay_m must be a positive finite number of metres, got {decay_m!r}")
    total = 0.0
    for stay in stays:
        total += option_share(price_stay(stay, decay_m, option_price_beta_u_per_eur), measure, paid_by_id)
    return total / len(stays)


class StayOutcomes(NamedTuple):
    """How the production pricing treated the stays of a universe at a given decay (fallback transparency): ``expectation`` stays
    were priced as an expectation over the street and the options (PRIMARY method; ``street_infeasible`` of them had no street
    option and are forced off-street), ``street_only`` had the street as their only option (no feasible garage), ``street_free``
    had a free street (E4: nobody pays an option), ``outside_fee_hours`` lay outside the street fee window (the street is free)
    and ``no_feasible_option`` had no option at all (priced as the street, off-street share 0). The five classes sum to
    ``stays``."""

    stays: int
    expectation: int
    street_infeasible: int
    street_only: int
    street_free: int
    outside_fee_hours: int
    no_feasible_option: int


def stay_outcomes(stays: Sequence, decay_m: float, option_price_beta_u_per_eur: float) -> StayOutcomes:
    """The ``StayOutcomes`` of ``stays`` priced by the production code at ``decay_m``. Raises ``ValueError`` for an outcome the
    calibration does not expect (a stay decided by home, employer-free, resident or no-zone: the flags are off and the universes
    hold no home activity, so one of those would be a defect)."""
    counts = dict(expectation=0, street_infeasible=0, street_only=0, street_free=0, outside_fee_hours=0, no_feasible_option=0)
    for stay in stays:
        detail = price_stay(stay, decay_m, option_price_beta_u_per_eur)
        if detail.outcome == cost.NO_FEASIBLE_OPTION:
            counts["no_feasible_option"] += 1
        elif detail.outcome == cost.OUTSIDE_FEE_HOURS:
            counts["outside_fee_hours"] += 1
        elif detail.outcome in (cost.NO_ZONE, cost.HOME, cost.EMPLOYER_FREE, cost.RESIDENT_FREE):
            raise ValueError(f"a calibration stay of zone {stay.zone_id!r} was decided early with outcome {detail.outcome}: the "
                             "payment-status flags are off and the universes hold no home activity, so this is a defect")
        elif detail.outcome == cost.PAID_EXPECTED:
            counts["expectation"] += 1
            counts["street_infeasible"] += not detail.street_feasible
        elif detail.cents == 0:
            counts["street_free"] += 1
        else:
            counts["street_only"] += 1
    return StayOutcomes(len(stays), **counts)


def calibrate_decay(mean_of_decay, target: float, *, lambda_min_m: float = DEFAULT_LAMBDA_MIN_M,
                    lambda_max_m: float = DEFAULT_LAMBDA_MAX_M, tolerance: float = DEFAULT_TOLERANCE) -> Calibration:
    """Bisection on lambda for ``mean_of_decay(lambda) == target`` within ``tolerance``; ``mean_of_decay`` maps a decay length
    in metres to the mean off-street share of a universe (``mean_option_share`` of its stays).

    The mean is non-decreasing in lambda (every option weight rises with lambda, the street weight does not depend on it), so the
    interval ``[lambda_min_m, lambda_max_m]`` brackets the solution exactly when the mean at its two ends encloses the target;
    otherwise ``ValueError`` names both end values (the target is unreachable with these options and this universe, and no end
    of the range is returned as an answer). The result is rounded to ``LAMBDA_DECIMALS`` decimals and its mean recomputed;
    ``ValueError`` when the rounded value misses ``tolerance``. ``ValueError`` as well when ``MAXIMUM_ITERATIONS`` halvings do
    not reach the tolerance.
    """
    if not 0.0 < target < 1.0:
        raise ValueError(f"the target must lie in (0, 1), got {target}")
    if not 0.0 < lambda_min_m < lambda_max_m or tolerance <= 0:
        raise ValueError(f"need 0 < lambda_min_m < lambda_max_m and tolerance > 0, got {lambda_min_m}, {lambda_max_m}, "
                         f"{tolerance}")
    low_mean = mean_of_decay(lambda_min_m)
    high_mean = mean_of_decay(lambda_max_m)
    if not low_mean - tolerance <= target <= high_mean + tolerance:
        raise ValueError(f"the target {target:.4f} is unreachable: the mean off-street share is {low_mean:.4f} at "
                         f"lambda = {lambda_min_m} m and {high_mean:.4f} at lambda = {lambda_max_m} m")
    low, high, iterations = lambda_min_m, lambda_max_m, 0
    while True:
        middle = 0.5 * (low + high)
        mean = mean_of_decay(middle)
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
    achieved = mean_of_decay(decay_m)
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


def wolfsburg_zone_ids(tariffs: pd.DataFrame) -> tuple:
    """The zone ids of the Wolfsburg check universe (H3b) from the tariff table ``tariffs`` (``zones.load_tariffs``): every zone
    whose ``municipality_ags`` is ``WOLFSBURG_AGS``, sorted. Selecting by municipality keeps the Wolfsburg area zones
    (``wob_area_<id>``, spec v3 B2) in the universe; the one helper of the calibration and the comparison script. Logs the
    number of zones. Raises ``ValueError`` for an empty selection: a check without zones is not skipped."""
    ids = tuple(sorted(str(zone_id) for zone_id, ags in zip(tariffs["zone_id"], tariffs["municipality_ags"])
                       if str(ags) == WOLFSBURG_AGS))
    if not ids:
        raise ValueError(f"the tariff table has no zone with municipality_ags {WOLFSBURG_AGS} (Wolfsburg): the Wolfsburg check "
                         "(spec H3b) has no zones, and a check that cannot be computed is not skipped")
    areas = sum(zone_id.startswith("wob_area_") for zone_id in ids)
    log.info("[garage-decay] Wolfsburg zones (municipality_ags %s): %d of the tariff table (%d tariff or other zones, %d area "
             "zones)", WOLFSBURG_AGS, len(ids), len(ids) - areas, areas)
    return ids


def wolfsburg_zone_label(zone_ids) -> str:
    """The Wolfsburg zones in words for logs, notes and the table (their number and the selection rule, not the 18 ids)."""
    return f"the {len(tuple(zone_ids))} Wolfsburg zones (municipality_ags {WOLFSBURG_AGS} of the tariff table)"


def universe_masks(activities: pd.DataFrame, zone_id: pd.Series, wolfsburg_zone_ids,
                   calibration_zone_ids=CALIBRATION_ZONE_IDS) -> UniverseMasks:
    """Boolean masks over ``activities`` (columns ``purpose``, ``x``, ``y``, ``parking_free``; ``zone_id`` aligned to its
    index): the destination universe of spec E5 (inside a calibration zone, purpose not home, work or education; parkingFree
    does not change it), the commuter universe of H2 (inside a calibration zone, purpose work or education, NOT parkingFree)
    and the Wolfsburg check universe of H3b (inside a Wolfsburg zone, purpose work or education, NOT parkingFree), plus the
    number of work and education activities inside the calibration zones that carry parkingFree and are left out of the
    commuter universe. ``wolfsburg_zone_ids`` is the selection of ``wolfsburg_zone_ids(tariffs)``. Raises ``ValueError`` when
    ``parking_free`` is missing: a frame that does not say who parks free must not be read as nobody does, and when no Wolfsburg
    zone is given."""
    if not len(tuple(wolfsburg_zone_ids)):
        raise ValueError("no Wolfsburg zone is given: the check universe of H3b would be empty by construction")
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


def is_free_option(tariff_tiers, tariff_duration_bands) -> bool:
    """Whether an option is FREE: its schedule is the free schedule ``garages.FREE_SCHEDULE_TEXT`` ("0- free"), i.e.
    ``tariff_duration_bands`` parses (``garages.parse_duration_bands``) to ONE open-ended free band
    (``garages.is_free_schedule``) and the option has no ``tariff_tiers``. A free first band that something follows, a closed
    free band (the grace period of a tiered garage) and every other schedule are PAID options. The marker is the schedule of the
    row, never its name or a rate. Whether a cell is empty is decided by the garage module's own rule (``zones._is_set``: None,
    NaN, ``pd.NA`` and the empty string are empty, as ``garages.coverage`` counts ``priced_free``; the loader turns a blank
    cell into None before this point). Raises ``ValueError`` for a schedule text that does not parse
    (``garages.validate_garages`` refuses it earlier)."""
    if parking_zones._is_set(tariff_tiers) or not parking_zones._is_set(tariff_duration_bands):
        return False
    return parking_garages.is_free_schedule(parking_garages.parse_duration_bands(tariff_duration_bands))


def paid_option_flags(options: pd.DataFrame) -> np.ndarray:
    """One bool per row of ``options`` (columns ``tariff_tiers`` and ``tariff_duration_bands`` of the garage dataset): True for
    a PAID option, False for a FREE one (``is_free_option``)."""
    return np.array([not is_free_option(tiers, bands)
                     for tiers, bands in zip(options["tariff_tiers"], options["tariff_duration_bands"])], dtype=bool)


class OptionReach(NamedTuple):
    """How the options reach the stays of ONE universe (fallback transparency; the options of a stay are those within D_max):
    the stays, those with at least one option, those with at least one PAID option (the stays whose paid-only share can be above
    0), the distinct FREE options within reach of at least one stay (``free_option_ids``: their ids, sorted) and the stays with a
    free option."""

    activities: int
    with_option_in_range: int
    with_paid_option_in_range: int
    free_options_in_range: int
    with_free_option_in_range: int
    free_option_ids: tuple = ()


def option_reach(stays: Sequence, paid_by_id: Mapping) -> OptionReach:
    """The ``OptionReach`` of a universe from the options of its ``stays`` (``build_stays`` keeps those within D_max, an option at
    exactly D_max included); ``paid_by_id`` maps a garage id to True for a paid option. Pure."""
    with_option = with_paid = with_free = 0
    free_ids = set()
    for stay in stays:
        ids = [garage.garage_id for garage, _ in stay.options]
        free_here = {garage_id for garage_id in ids if not paid_by_id[garage_id]}
        with_option += bool(ids)
        with_paid += any(paid_by_id[garage_id] for garage_id in ids)
        with_free += bool(free_here)
        free_ids |= free_here
    return OptionReach(activities=len(stays), with_option_in_range=with_option, with_paid_option_in_range=with_paid,
                       free_options_in_range=len(free_ids), with_free_option_in_range=with_free,
                       free_option_ids=tuple(sorted(free_ids)))


def _warn_when_barely_reached(name: str, reached: int, activities: int, kind: str, max_distance_m: float, context: str,
                              minimum_share: float) -> None:
    """Warn when fewer than ``minimum_share`` of the activities of a universe have an option (``kind``: "priced" or "paid")
    within ``max_distance_m``: the probability of the others is 0, so the calibration or the check rests on a minority. The
    threshold is a log-only plausibility threshold (``--minimum-reach-share-warning``), no scientific parameter."""
    if activities and reached / activities < minimum_share:
        log.warning("[garage-decay] %s%s: only %d/%d (%.1f %%) of the activities have a %s option within reach (%g m), below "
                    "the warning share of %.0f %%: the option set barely covers this universe", context, name, reached,
                    activities, 100.0 * reached / activities, kind, max_distance_m, 100.0 * minimum_share)


class UniverseActivities(NamedTuple):
    """The activities of one universe (columns ``purpose``, ``x``, ``y``, ``start_time_s``, ``end_time_s``), the id of the zone each
    lies in (aligned by position) and, for the commuter universe, the number of work and education activities in the calibration
    zones that carry parkingFree and are left out."""

    activities: pd.DataFrame
    zone_ids: pd.Series
    excluded_parking_free: int = 0


class Universes(NamedTuple):
    """The three universes of the calibration: the destination universe of E5 (lambda), the commuter universe of H2 (lambda_c)
    and the Wolfsburg check universe of H3b."""

    visitors: UniverseActivities
    commuters: UniverseActivities
    wolfsburg: UniverseActivities


class OptionSetCalibration(NamedTuple):
    """Both calibrations on ONE option set (the garage dataset of the release or of a sensitivity) at ONE option price
    coefficient, the Wolfsburg check mean at its lambda_c, how the options reach the three universes and how the production
    pricing treated their stays (``StayOutcomes`` at the calibrated decay of each universe, ``StayTimeNotes`` of the universe)."""

    options: int
    free_options: int
    option_price_beta_u_per_eur: float
    visitors: Calibration
    commuters: Calibration
    wolfsburg_mean: float
    visitor_reach: OptionReach
    commuter_reach: OptionReach
    wolfsburg_reach: OptionReach
    visitor_outcomes: StayOutcomes
    commuter_outcomes: StayOutcomes
    wolfsburg_outcomes: StayOutcomes
    visitor_time_notes: StayTimeNotes
    commuter_time_notes: StayTimeNotes
    wolfsburg_time_notes: StayTimeNotes


def _outcomes_text(outcomes: StayOutcomes) -> str:
    return (f"stays {outcomes.stays}: expectation {outcomes.expectation} (street infeasible {outcomes.street_infeasible}: "
            f"forced off-street), street only {outcomes.street_only}, street free {outcomes.street_free}, outside fee hours "
            f"{outcomes.outside_fee_hours}, no feasible option {outcomes.no_feasible_option}/{outcomes.stays} "
            f"({100.0 * outcomes.no_feasible_option / outcomes.stays:.1f} %)")


def _warn_when_no_feasible_option_is_frequent(name: str, outcomes: StayOutcomes, context: str, maximum_share: float) -> None:
    """Warn when more than ``maximum_share`` of the stays of a universe have NO FEASIBLE OPTION: such a stay has the off-street
    share 0 in every measure (never an invented 1), so a high rate says that the option set is incomplete for this universe or
    that the feasibility inputs (maximum stays, fee windows, long-stay products) are wrong. Log-only plausibility threshold
    (``--maximum-no-feasible-option-share-warning``), no scientific parameter."""
    if outcomes.stays and outcomes.no_feasible_option / outcomes.stays > maximum_share:
        log.warning("[garage-decay] %s%s: no feasible option %d/%d (%.1f %%) is above the warning share of %.0f %%: these stays "
                    "have no street and no garage or lot that can serve them, are priced as the street and count an off-street "
                    "share of 0 (never an invented 1); the option set or the maximum stays of the zones may be incomplete",
                    context, name, outcomes.no_feasible_option, outcomes.stays,
                    100.0 * outcomes.no_feasible_option / outcomes.stays, 100.0 * maximum_share)


def calibrate_option_set(universes: Universes, options: pd.DataFrame, tariffs_by_zone: Mapping, *, visitor_target: Target,
                         commuter_target: Target, pricing: PricingConfig, option_price_beta_u_per_eur: float,
                         lambda_min_m: float, lambda_max_m: float, tolerance: float, context: str = "",
                         wolfsburg_label: str = "the Wolfsburg zones",
                         minimum_reach_share_warning: float = MINIMUM_REACH_SHARE_WARNING,
                         maximum_no_feasible_option_share_warning: float = MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING
                         ) -> OptionSetCalibration:
    """Calibrate lambda (payment-inclusive measure on every option, E5) and lambda_c (paid-only measure, H2) on the priced
    ``options`` at the option price coefficient ``option_price_beta_u_per_eur`` and compute the paid-only Wolfsburg check mean at
    lambda_c (H3b); every stay is priced by the production code (module documentation). ``pricing`` carries D_max, L1 and the
    monthly-product imputation. ``context`` prefixes the log lines and the errors (e.g. ``"sensitivity lot_threshold_30: "``).
    Logs, per universe, the activities with an option within D_max, the free options within D_max and the outcomes of the stays
    (primary method against fallbacks, the rate of NO FEASIBLE OPTION), and warns about a universe below
    ``minimum_reach_share_warning`` and above ``maximum_no_feasible_option_share_warning``. Raises ``ValueError`` naming the
    calibration (and the measure) whose target the options cannot reach."""
    max_distance_m = pricing.max_distance_m
    minimum_stay_s = pricing.minimum_stay_min * cost.SECONDS_PER_MINUTE
    paid_by_id = dict(zip(options["garage_id"].tolist(), paid_option_flags(options).tolist()))
    garages = [tariff_export.garage_row_to_tariff(row, monthly_imputation=pricing.monthly_imputation)
               for _, row in options.iterrows()]
    stays, notes = {}, {}
    for name, universe in zip(Universes._fields, universes):
        stays[name], notes[name] = build_stays(universe.activities, universe.zone_ids, tariffs_by_zone, garages,
                                               max_distance_m=max_distance_m, minimum_stay_s=minimum_stay_s)
    visitor_reach, commuter_reach, wolfsburg_reach = (option_reach(stays[name], paid_by_id) for name in Universes._fields)
    free_count = sum(1 for paid in paid_by_id.values() if not paid)
    log.info("[garage-decay] %soption set: %d priced options (%d paid, %d free); option price coefficient %g per EUR, minimum "
             "stay %d min, monthly-product imputation %s", context, len(options), len(options) - free_count, free_count,
             option_price_beta_u_per_eur, pricing.minimum_stay_min, "on" if pricing.monthly_imputation else "off")
    log.info("[garage-decay] %sdestination universe %d activities; with a priced garage within %g m: %d/%d (%.1f %%); free "
             "options within %g m: %d distinct, reach %d/%d activities", context, visitor_reach.activities, max_distance_m,
             visitor_reach.with_option_in_range, visitor_reach.activities,
             100.0 * visitor_reach.with_option_in_range / visitor_reach.activities, max_distance_m,
             visitor_reach.free_options_in_range, visitor_reach.with_free_option_in_range, visitor_reach.activities)
    log.info("[garage-decay] %scommuter universe %d work and education activities without parkingFree inside %s (excluded as "
             "parkingFree %d); with a priced garage within %g m: %d/%d (%.1f %%); paid options within %g m: %d/%d (%.1f %%); "
             "free options within %g m: %d distinct, reach %d/%d activities", context, commuter_reach.activities,
             ", ".join(CALIBRATION_ZONE_IDS), universes.commuters.excluded_parking_free, max_distance_m,
             commuter_reach.with_option_in_range, commuter_reach.activities,
             100.0 * commuter_reach.with_option_in_range / commuter_reach.activities, max_distance_m,
             commuter_reach.with_paid_option_in_range, commuter_reach.activities,
             100.0 * commuter_reach.with_paid_option_in_range / commuter_reach.activities, max_distance_m,
             commuter_reach.free_options_in_range, commuter_reach.with_free_option_in_range, commuter_reach.activities)
    log.info("[garage-decay] %sWolfsburg check universe %d work and education activities without parkingFree inside %s; with a "
             "priced garage within %g m: %d/%d; paid options within %g m: %d/%d; free options within %g m: %d distinct, reach "
             "%d/%d activities", context, wolfsburg_reach.activities, wolfsburg_label, max_distance_m,
             wolfsburg_reach.with_option_in_range, wolfsburg_reach.activities, max_distance_m,
             wolfsburg_reach.with_paid_option_in_range, wolfsburg_reach.activities, max_distance_m,
             wolfsburg_reach.free_options_in_range, wolfsburg_reach.with_free_option_in_range, wolfsburg_reach.activities)
    _warn_when_barely_reached("destination universe", visitor_reach.with_option_in_range, visitor_reach.activities, "priced",
                              max_distance_m, context, minimum_reach_share_warning)
    _warn_when_barely_reached("commuter universe", commuter_reach.with_paid_option_in_range, commuter_reach.activities, "paid",
                              max_distance_m, context, minimum_reach_share_warning)
    _warn_when_barely_reached("Wolfsburg check universe", wolfsburg_reach.with_paid_option_in_range,
                              wolfsburg_reach.activities, "paid", max_distance_m, context, minimum_reach_share_warning)
    search = dict(lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, tolerance=tolerance)

    def mean_of(name, measure):
        return lambda decay_m: mean_option_share(stays[name], decay_m, option_price_beta_u_per_eur, measure, paid_by_id)

    try:
        visitors = calibrate_decay(mean_of("visitors", MEASURE_ALL_OPTIONS), visitor_target.value, **search)
    except ValueError as error:
        raise ValueError(f"{context}lambda (payment-inclusive measure, every option counts): {error}") from error
    log.info("[garage-decay] %starget %.6f (%s, payment-inclusive measure); lambda %.2f m, mean %.6f after %d halvings",
             context, visitor_target.value, visitor_target.table, visitors.decay_m, visitors.achieved, visitors.iterations)
    try:
        commuters = calibrate_decay(mean_of("commuters", MEASURE_PAID_OPTIONS), commuter_target.value, **search)
    except ValueError as error:
        raise ValueError(f"{context}lambda_c (PAID-only measure, {commuter_reach.with_paid_option_in_range} of "
                         f"{commuter_reach.activities} activities have a paid option within {max_distance_m:g} m): "
                         f"{error}") from error
    log.info("[garage-decay] %scommuter target %.6f (%s class %s, PAID-only measure); lambda_c %.2f m, mean %.6f after %d "
             "halvings", context, commuter_target.value, commuter_target.table, COMMUTER_CLASS, commuters.decay_m,
             commuters.achieved, commuters.iterations)
    wolfsburg_mean = mean_option_share(stays["wolfsburg"], commuters.decay_m, option_price_beta_u_per_eur,
                                       MEASURE_PAID_OPTIONS, paid_by_id)
    visitor_outcomes = stay_outcomes(stays["visitors"], visitors.decay_m, option_price_beta_u_per_eur)
    commuter_outcomes = stay_outcomes(stays["commuters"], commuters.decay_m, option_price_beta_u_per_eur)
    wolfsburg_outcomes = stay_outcomes(stays["wolfsburg"], commuters.decay_m, option_price_beta_u_per_eur)
    for name, outcomes, notes_of in (("destination universe", visitor_outcomes, notes["visitors"]),
                                     ("commuter universe", commuter_outcomes, notes["commuters"]),
                                     ("Wolfsburg check universe", wolfsburg_outcomes, notes["wolfsburg"])):
        log.info("[garage-decay] %s%s: %s; terminal stays %d, planned end before arrival %d", context, name,
                 _outcomes_text(outcomes), notes_of.terminal_stays, notes_of.planned_end_before_arrival)
        _warn_when_no_feasible_option_is_frequent(name, outcomes, context, maximum_no_feasible_option_share_warning)
    return OptionSetCalibration(
        len(options), free_count, option_price_beta_u_per_eur, visitors, commuters, wolfsburg_mean, visitor_reach,
        commuter_reach, wolfsburg_reach, visitor_outcomes, commuter_outcomes, wolfsburg_outcomes, notes["visitors"],
        notes["commuters"], notes["wolfsburg"])


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
               tolerance: float, wolfsburg_label: str,
               lambda_min_m: float, lambda_max_m: float, max_distance_m: float, generated_on: str, code_state: str,
               pricing: PricingConfig, beta_sensitivities: list) -> str:
    """The calibration table as text: the provenance header and the long-format rows ``TABLE_COLUMNS``."""
    zone_text = ", ".join(CALIBRATION_ZONE_IDS)
    calibration, commuter_calibration = release.visitors, release.commuters
    universe, commuters, wolfsburg = release.visitor_reach, release.commuter_reach, release.wolfsburg_reach
    beta = release.option_price_beta_u_per_eur
    header = [
        "# Table: parking_garage_decay_calibration_2026.csv",
        "# Calibration of the decay lengths lambda (visitors) and lambda_c (commuters) of the off-street options (parking cost "
        "zones v2, spec Amendments E5, H2 and I6, ASSUMPTIONS G1 to G3 and G1-c, issue #436).",
        f"# Generated by scripts/parking/calibrate_garage_decay.py on {generated_on}; code state {code_state}.",
        "# Inputs (SHA-256; text files with LF line endings, .gz files as stored):",
        *[f"#   {name}: {path} sha256={digest}" for name, (path, digest) in inputs.items()],
        "# This table belongs to the garage dataset, the zone file and the tariff table named above (sha256) exactly: another "
        "option set (a regenerated release, a car park that becomes a free option) or another zone or tariff table (a fee window, "
        "a maximum stay or a long-stay product moves feasibility and prices) needs a new calibration.",
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
        "# Quantity: the mean over a universe of the off-street share of each stay as the production pricing gives it "
        "(cost.parking_cost_with_garages_detail: the street and the feasible options within the maximum distance, weighted as "
        "stated in the next line); the early rule of the street fee window, E4 (a free street pays no option) and the maximum "
        "stay (which options exist) act as in the model.",
        f"# Option price coefficient (spec v3 A2, ASSUMPTION G1-d): option_price_beta_u_per_eur {beta:g} utility per EUR, the "
        "magnitude of the mode-choice cost coefficient (the config key of the same name; fixed during the calibration, never "
        "fitted: one share cannot identify two parameters). Every stay is priced and weighted by "
        "cost.parking_cost_with_garages_detail with the weights cost.option_weight = exp(-d / lambda - beta * c / 100) (d the "
        "straight-line distance in metres, 0 for the street; c the cents the option charges for THIS stay), the street only where "
        "it is feasible; the calibration has no weight formula of its own"
        + ("; beta 0 would be the price-independent weights of v2." if beta != 0 else "; beta 0 is the price-independent "
           "weights of v2 (the weights then reduce to exp(-d / lambda), the street 1)."),
        f"# Stays: every stay is priced from the start_time (arrival) to the end_time (departure) of its activity in the plans; "
        f"an activity without end_time is a terminal stay (terminal-stay rule {pricing.terminal_stay_rule}, ASSUMPTION T1) and "
        f"the priced departure is extended to the minimum stay of {pricing.minimum_stay_min} min (rule L1). Tariff snapshot "
        f"{pricing.tariff_snapshot_date}; monthly-product imputation {'on' if pricing.monthly_imputation else 'off'} "
        "(ASSUMPTION P13). The arrival is the start of the activity, not the car arrival of the Java pricing, which the plans do "
        "not hold (limitation). The following are not applied to a stay (limitations): the free-parking draw parkingFree (the "
        "commuter universe excludes it, the visitors keep it unapplied as in v2), the resident exemptions R1 and R2, and the EV "
        "exemption of spec "
        "v3 A5: the plans of the reference run predate the EV rule and carry no parkingEvExempt attribute, so every stay is "
        "treated as not exempt (ASSUMPTION; the model run prices an exempt stay free, so the calibration slightly overstates the "
        "paid street side wherever an exempt person parks in a zone with an exemption).",
        "# NO FEASIBLE OPTION (spec v3 A1): a stay whose street cannot serve it (maximum stay exceeded, no long-stay or commuter "
        "product) and for which no garage or lot within the maximum distance can serve it either. The production pricing prices "
        "it as the street (outcome NO_FEASIBLE_OPTION); the calibration counts it per universe and it contributes an off-street "
        "share of 0 in every measure (never an invented share of 1). A stay whose street is infeasible but that has a feasible "
        "option is forced off-street (share 1; in Ia, with a maximum stay of 180 min and no long-stay product, every commuter "
        "stay above it, so the street payers of the SrV target map to Ib and the ParkGO zone-II islands).",
        f"# Stays by outcome at the calibrated decays (release dataset; expectation = priced as an expectation over the street "
        f"and the options, the primary method): destination universe {_outcomes_text(release.visitor_outcomes)}; commuter "
        f"universe {_outcomes_text(release.commuter_outcomes)}; Wolfsburg check universe "
        f"{_outcomes_text(release.wolfsburg_outcomes)}. Terminal stays (no end_time) {release.visitor_time_notes.terminal_stays}, "
        f"{release.commuter_time_notes.terminal_stays} and {release.wolfsburg_time_notes.terminal_stays}; planned end before "
        f"arrival {release.visitor_time_notes.planned_end_before_arrival}, "
        f"{release.commuter_time_notes.planned_end_before_arrival} and "
        f"{release.wolfsburg_time_notes.planned_end_before_arrival} (the three universes in that order).",
        f"# Target of lambda: {target.numerator_name} / ({target.numerator_name} + {target.other_name}) = {target.numerator} / "
        f"({target.numerator} + {target.other}) = {target.value:.6f} of {target.table} (Braunschweig residents who park on the "
        "street or in a garage or large lot when they drive to the city centre), with the payment-inclusive measure (every "
        "option counts) because the SrV shares contain free and paying parkers. A calibration target, no validation. "
        "Universe caveat: SrV asks residents about their usual place, the model averages over destinations.",
        f"# Search for lambda: bisection in [{lambda_min_m:g}, {lambda_max_m:g}] m to a tolerance of {tolerance:g} in the mean "
        f"probability, {calibration.iterations} halvings; the mean is {calibration.high_end_mean:.4f} at the upper end and "
        f"{calibration.low_end_mean:.4f} at the lower end. The decays are rounded to {LAMBDA_DECIMALS} decimals. A reported "
        "decay is the first bisection midpoint whose mean lies within the tolerance of the target, not the exact root of the "
        "target, so differences between option sets of the order of the tolerance divided by the local slope of the mean are "
        "not resolved (this holds for lambda_c and for the sensitivities below).",
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
        f"parkingFree inside {wolfsburg_label} ({wolfsburg.with_option_in_range} with a priced garage within "
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
    for entry in beta_sensitivities:
        header.append(
            f"# Sensitivity of the option price coefficient (spec v3 B5; NOT a release value) {entry['label']}: the same "
            "calibration on the release dataset and the same plans, zones, tariffs, targets, maximum distance, measures and "
            f"tolerance with option_price_beta_u_per_eur {entry['option_price_beta_u_per_eur']:g}"
            + (" (the price-independent weights of v2)" if entry["option_price_beta_u_per_eur"] == 0 else "")
            + f": lambda = {entry['decay_length_m']:.{LAMBDA_DECIMALS}f} m (achieved mean {entry['achieved']:.6f}), lambda_c = "
            f"{entry['decay_commute_length_m']:.{LAMBDA_DECIMALS}f} m (achieved mean {entry['commuter_achieved']:.6f}); the "
            "release values are decay_length_m and decay_commute_length_m below.")
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
    rows += [
        ("option_price_beta_u_per_eur", f"{beta:g}", "utility_per_eur",
         "ASSUMPTION G1-d; the option price coefficient the decays were calibrated with (config key "
         "parking_option_price_beta_u_per_eur)"),
        ("parking_minimum_stay_min", str(pricing.minimum_stay_min), "min", "rule L1 of the calibration stays (config key "
                                                                           "parking_minimum_stay_min)"),
    ]
    for prefix, outcomes, notes, what in (
            ("universe", release.visitor_outcomes, release.visitor_time_notes, "destination universe"),
            ("commuter_universe", release.commuter_outcomes, release.commuter_time_notes, "commuter universe"),
            ("check_wolfsburg", release.wolfsburg_outcomes, release.wolfsburg_time_notes, "Wolfsburg check universe")):
        rows += [
            (f"{prefix}_stays_expectation", str(outcomes.expectation), "count",
             f"{what}: stays priced as an expectation over the street and the options (primary method)"),
            (f"{prefix}_stays_street_infeasible", str(outcomes.street_infeasible), "count",
             f"{what}: of the expectation stays those without a street option (forced off-street)"),
            (f"{prefix}_stays_street_only", str(outcomes.street_only), "count",
             f"{what}: stays with the street as their only option (off-street share 0)"),
            (f"{prefix}_stays_street_free", str(outcomes.street_free), "count",
             f"{what}: stays with a free street (E4; off-street share 0)"),
            (f"{prefix}_stays_outside_fee_hours", str(outcomes.outside_fee_hours), "count",
             f"{what}: stays outside the street fee window (off-street share 0)"),
            (f"{prefix}_stays_no_feasible_option", str(outcomes.no_feasible_option), "count",
             f"{what}: NO FEASIBLE OPTION stays (priced as the street; off-street share 0 and never an invented 1)"),
            (f"{prefix}_stays_terminal_rule", str(notes.terminal_stays), "count",
             f"{what}: stays without end_time (terminal-stay rule T1)"),
            (f"{prefix}_stays_planned_end_before_arrival", str(notes.planned_end_before_arrival), "count",
             f"{what}: stays whose planned end precedes the arrival (priced by the minimum stay)"),
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


def validate_share_threshold(value, name: str, what: str) -> float:
    """A log-only share threshold as a float: a number in [0, 1], no bool. It changes no result. Raises ``ValueError`` naming
    the option otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a share in [0, 1] ({what}), got {value!r}")
    return float(value)


def validate_minimum_reach_share_warning(value) -> float:
    """The warning threshold of the reach log (``--minimum-reach-share-warning``) as a float: a number in [0, 1], no bool. It is
    a log-only plausibility threshold and changes no result. Raises ``ValueError`` naming the option otherwise."""
    return validate_share_threshold(value, "minimum_reach_share_warning", "a log-only plausibility threshold")


def validate_maximum_no_feasible_option_share_warning(value) -> float:
    """The warning threshold of the NO FEASIBLE OPTION rate (``--maximum-no-feasible-option-share-warning``) as a float: a number
    in [0, 1], no bool. A log-only plausibility threshold that changes no result. Raises ``ValueError`` naming the option
    otherwise."""
    return validate_share_threshold(value, "maximum_no_feasible_option_share_warning", "a log-only plausibility threshold")


def beta_sensitivity_label(option_price_beta_u_per_eur: float) -> str:
    """The label of a beta sensitivity, ``beta_<value>`` (``beta_0``, ``beta_0.5``): a header word and a key of the result."""
    return f"beta_{option_price_beta_u_per_eur:g}"


def validate_sensitivity_betas(sensitivity_betas, release_beta: float) -> list:
    """The beta sensitivities as a list of floats in the given order, checked BEFORE any plan is read: each a finite number >= 0
    (no bool, no text), none equal to the release coefficient (a sensitivity at the release value would be no sensitivity),
    none given twice. Raises ``ValueError`` naming ``option_price_beta_u_per_eur``."""
    if not sensitivity_betas:
        return []
    checked = []
    for value in sensitivity_betas:
        beta = _beta_value(value, "sensitivity")
        if beta == release_beta:
            raise ValueError(f"sensitivity option_price_beta_u_per_eur {beta:g} is the same as the release coefficient: it is the "
                             "release, not a sensitivity")
        if beta in checked:
            raise ValueError(f"sensitivity option_price_beta_u_per_eur {beta:g} is given twice")
        checked.append(beta)
    return checked


def run(*, plans, zones_path, garages_path, city_center_path, commute_path, out_path, tariffs_path=None,
        overwrite: bool = False, max_distance_m: float | None = None, config_path=DEFAULT_CONFIG_PATH,
        option_price_beta_u_per_eur: float | None = None, sensitivity_betas=None,
        lambda_min_m: float = DEFAULT_LAMBDA_MIN_M,
        lambda_max_m: float = DEFAULT_LAMBDA_MAX_M, tolerance: float = DEFAULT_TOLERANCE,
        facility_kinds=None, sensitivity_garages=None, generated_on: str | None = None,
        minimum_reach_share_warning: float = MINIMUM_REACH_SHARE_WARNING,
        maximum_no_feasible_option_share_warning: float = MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING) -> dict:
    """Calibrate lambda (E5, payment-inclusive) and lambda_c (H2, paid-only) on ``plans`` through the production pricing (module
    documentation), write the table to ``out_path`` and return the results (a dict of the table's numbers, including the
    paid-only Wolfsburg check of H3b, the stay outcomes of every universe and the sensitivities).

    ``max_distance_m`` (D_max, metres) defaults to the config key ``parking_garage_max_distance_m`` of ``config_path``
    (``read_configured_max_distance_m``; fails when missing); an explicit value is a sensitivity and is recorded in the table. The
    other pricing parameters (minimum stay, monthly-product imputation, tariff snapshot date, terminal-stay rule and the option
    price coefficient) come from the same config (``read_pricing_config``). ``option_price_beta_u_per_eur`` overrides the
    coefficient (a sensitivity: a value other than the config's must not be written under the release table's file name);
    ``sensitivity_betas`` (a list of coefficients) adds a header line per coefficient next to the release values, never a release
    value.

    ``tariffs_path`` is the tariff table whose rows price the stays of every zone and whose zones of Wolfsburg
    (``wolfsburg_zone_ids``, by municipality_ags) form the check universe of H3b; None = ``parking_tariffs_2026.csv`` next to the
    zone polygons.

    ``facility_kinds`` (None = every kind) restricts the priced options to the given ``facility_kind`` values of the garage
    dataset (``garage``, ``surface_lot``), so that lambda can be reported with and without the surface lots; the table
    records the filter. The RELEASE value is the calibration on every kind; a filtered run is a sensitivity number.

    ``sensitivity_garages`` (``{label: path}``) runs the same calibration on other garage datasets (spec Amendment I6, e.g. the
    large-lot thresholds 30 and 100); each result is a header line of the table and an entry of ``result["sensitivities"]``,
    never a release value.

    ``minimum_reach_share_warning`` (a share in [0, 1], default ``MINIMUM_REACH_SHARE_WARNING``) is the log-only plausibility
    threshold below which a universe that the options barely reach is warned about; ``maximum_no_feasible_option_share_warning``
    (default ``MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING``) the one above which a universe with many NO FEASIBLE OPTION stays is
    warned about; neither changes a result.

    Raises ``FileNotFoundError`` (naming the data record and the request route) when the zone polygons or the garage dataset,
    both local restricted files, are absent, ``FileExistsError`` when ``out_path`` exists and ``overwrite`` is false
    (a calibrated release value is never replaced silently), ``ValueError`` for an empty universe (destination, commuter
    or Wolfsburg check), an activity without ``start_time``, a zone without a tariff row, an unreachable target (naming the
    calibration and its measure) or a refused sensitivity. Side effects: reads the inputs, logs, writes ``out_path`` (its
    directory must exist) after every calibration has succeeded.
    """
    out_path = Path(out_path) if out_path is not None else None
    minimum_reach_share_warning = validate_minimum_reach_share_warning(minimum_reach_share_warning)
    maximum_no_feasible_option_share_warning = validate_maximum_no_feasible_option_share_warning(
        maximum_no_feasible_option_share_warning)
    # the zone polygons and the garage dataset are local restricted files (issue #436): name the record and the request route
    restricted_inputs.require_restricted_file("parking_zones_2026", "zone polygons", zones_path)
    restricted_inputs.require_restricted_file("parking_garages_2026", "garage dataset", garages_path)
    distance_is_explicit = max_distance_m is not None
    pricing = read_pricing_config(config_path, max_distance_m=max_distance_m,
                                  option_price_beta_u_per_eur=option_price_beta_u_per_eur)
    max_distance_m = pricing.max_distance_m
    beta = pricing.option_price_beta_u_per_eur
    beta_text = ("" if beta == pricing.configured_option_price_beta_u_per_eur
                 else f" (explicit, the config says {pricing.configured_option_price_beta_u_per_eur:g})")
    log.info("[garage-decay] pricing parameters of %s: maximum garage distance %g m%s; option price coefficient %g per EUR%s; "
             "minimum stay %d min; monthly-product imputation %s; tariff snapshot %s; terminal-stay rule %s%s",
             path_label(config_path), max_distance_m, " (explicit)" if distance_is_explicit else "", beta, beta_text,
             pricing.minimum_stay_min, "on" if pricing.monthly_imputation else "off", pricing.tariff_snapshot_date,
             pricing.terminal_stay_rule,
             f"; defaulted from prepare.PARKING_DEFAULTS: {', '.join(pricing.defaulted)}" if pricing.defaulted else "")
    kinds = validate_facility_kinds(facility_kinds)
    if kinds is not None and out_path is not None and out_path.name == Path(TABLE_RELATIVE).name:
        raise ValueError(f"a facility-filtered calibration is a sensitivity number and must not be written under the release "
                         f"table's file name {out_path.name!r} ({out_path}); choose another --out")
    if (beta != pricing.configured_option_price_beta_u_per_eur and out_path is not None
            and out_path.name == Path(TABLE_RELATIVE).name):
        raise ValueError(f"a calibration at option_price_beta_u_per_eur {beta:g}, other than the configured "
                         f"{pricing.configured_option_price_beta_u_per_eur:g}, is a sensitivity number and must not be written "
                         f"under the release table's file name {out_path.name!r} ({out_path}); choose another --out")
    if out_path is not None and out_path.exists() and not overwrite:
        raise FileExistsError(f"{out_path} exists; a calibrated release value is not replaced silently (use --overwrite)")
    sensitivity_inputs = validate_sensitivity_garages(sensitivity_garages, garages_path)
    beta_inputs = validate_sensitivity_betas(sensitivity_betas, beta)
    exposure = load_exposure_module()
    activities, persons = exposure.read_main_activities(plans)
    activities, persons, freight_agents = exclude_freight_agents(activities, persons)
    tariffs_path = Path(tariffs_path) if tariffs_path is not None else Path(zones_path).with_name(Path(TARIFFS_RELATIVE).name)
    tariffs = parking_zones.load_tariffs(tariffs_path)
    wolfsburg_zones = wolfsburg_zone_ids(tariffs)
    wolfsburg_label = wolfsburg_zone_label(wolfsburg_zones)
    tariffs_by_zone = {}
    for row in tariffs.to_dict(orient="records"):
        zone = tariff_export.tariff_row_to_zone(row, snapshot_date=pricing.tariff_snapshot_date)
        if zone.zone_id in tariffs_by_zone:
            raise ValueError(f"{tariffs_path}: duplicate zone_id {zone.zone_id!r}")
        tariffs_by_zone[zone.zone_id] = zone
    zone_id = exposure.zone_per_activity(activities, zones_path)
    masks = universe_masks(activities, zone_id, wolfsburg_zones)
    in_wolfsburg = zone_id.isin(list(wolfsburg_zones)).to_numpy() & masks.wolfsburg_commuters
    log.info("[garage-decay] zones and activities per universe: destination %d activities in %d calibration zones; commuter %d "
             "activities in %d calibration zones; Wolfsburg check %d activities in %d of %d Wolfsburg zones",
             int(masks.universe.sum()), zone_id[masks.universe].nunique(), int(masks.commuters.sum()),
             zone_id[masks.commuters].nunique(), int(masks.wolfsburg_commuters.sum()), zone_id[in_wolfsburg].nunique(),
             len(wolfsburg_zones))
    if not masks.universe.any():
        raise ValueError(f"no destination of the universe: the {len(activities)} main activities of the {persons} persons "
                         f"contain none outside home, work and education inside {', '.join(CALIBRATION_ZONE_IDS)}")
    if not masks.commuters.any():
        raise ValueError(f"the commuter universe is empty: none of the {len(activities)} main activities of the {persons} "
                         f"persons is a work or education activity without parkingFree inside {', '.join(CALIBRATION_ZONE_IDS)} "
                         f"({masks.commuters_with_parking_free} carry parkingFree); lambda_c (spec H2) cannot be calibrated")
    if not masks.wolfsburg_commuters.any():
        raise ValueError(f"the Wolfsburg check has no universe: none of the {len(activities)} main activities of the {persons} "
                         f"persons is a work or education activity without parkingFree inside {wolfsburg_label} (spec "
                         "H3b); a check that cannot be computed is not skipped")
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
    stay_columns = ["purpose", "x", "y", "start_time_s", "end_time_s"]

    def universe_of(mask, excluded_parking_free=0):
        return UniverseActivities(activities.loc[mask, stay_columns], zone_id[mask].reset_index(drop=True),
                                  excluded_parking_free)

    universes = Universes(universe_of(masks.universe), universe_of(masks.commuters, masks.commuters_with_parking_free),
                          universe_of(masks.wolfsburg_commuters))
    search = dict(visitor_target=target, commuter_target=commuter_target, pricing=pricing, lambda_min_m=lambda_min_m,
                  lambda_max_m=lambda_max_m, tolerance=tolerance, wolfsburg_label=wolfsburg_label,
                  minimum_reach_share_warning=minimum_reach_share_warning,
                  maximum_no_feasible_option_share_warning=maximum_no_feasible_option_share_warning)
    release = calibrate_option_set(universes, priced, tariffs_by_zone, option_price_beta_u_per_eur=beta, **search)
    log.info("[garage-decay] Wolfsburg check (a number, no validation): paid-only off-street share %.4f at lambda_c over %d "
             "work and education activities without parkingFree inside %s (%d with a paid option within %g m) vs reference "
             "%.4f (class %s)", release.wolfsburg_mean, release.wolfsburg_reach.activities, wolfsburg_label,
             release.wolfsburg_reach.with_paid_option_in_range, max_distance_m, wolfsburg_reference.value, WOLFSBURG_CLASS)
    sensitivities = []
    for label, path in sensitivity_inputs:
        _, sensitivity_options = load_priced_options(path, kinds)
        outcome = calibrate_option_set(universes, sensitivity_options, tariffs_by_zone, option_price_beta_u_per_eur=beta,
                                       context=f"sensitivity {label}: ", **search)
        sensitivities.append({
            "label": label, "path": path_label(path), "sha256": file_sha256(path), "options": outcome.options,
            "free_options": outcome.free_options, "kind_counts": _kind_counts_text(sensitivity_options),
            "decay_length_m": outcome.visitors.decay_m, "achieved": outcome.visitors.achieved,
            "decay_commute_length_m": outcome.commuters.decay_m, "commuter_achieved": outcome.commuters.achieved})
        log.info("[garage-decay] sensitivity %s (NOT a release value): lambda %.2f m, lambda_c %.2f m on %d options (%d free)",
                 label, outcome.visitors.decay_m, outcome.commuters.decay_m, outcome.options, outcome.free_options)
    beta_sensitivities = []
    for sensitivity_beta in beta_inputs:
        label = beta_sensitivity_label(sensitivity_beta)
        outcome = calibrate_option_set(universes, priced, tariffs_by_zone, option_price_beta_u_per_eur=sensitivity_beta,
                                       context=f"sensitivity {label}: ", **search)
        beta_sensitivities.append({
            "label": label, "option_price_beta_u_per_eur": sensitivity_beta, "decay_length_m": outcome.visitors.decay_m,
            "achieved": outcome.visitors.achieved, "decay_commute_length_m": outcome.commuters.decay_m,
            "commuter_achieved": outcome.commuters.achieved, "wolfsburg_mean": outcome.wolfsburg_mean})
        log.info("[garage-decay] sensitivity %s (NOT a release value): lambda %.2f m, lambda_c %.2f m at option price "
                 "coefficient %g per EUR", label, outcome.visitors.decay_m, outcome.commuters.decay_m, sensitivity_beta)
    inputs = {name: (path_label(path), file_sha256(path)) for name, path in (
        ("plans", plans), ("zones", zones_path), ("tariffs", tariffs_path), ("garages", garages_path),
        ("srv2023_city_center_parking", city_center_path), ("srv2023_commute_parking_by_workplace_class", commute_path))}
    text = table_text(
        inputs=inputs, persons=persons, freight_agents=freight_agents, purposes=purposes, listed_options=len(listed),
        options_used=len(priced), facility_kinds_text=kinds_text, option_set_text=option_set_text(priced), release=release,
        target=target, commuter_target=commuter_target, wolfsburg_reference=wolfsburg_reference,
        commuter_excluded_free=masks.commuters_with_parking_free, sensitivities=sensitivities, tolerance=tolerance,
        wolfsburg_label=wolfsburg_label, lambda_min_m=lambda_min_m, lambda_max_m=lambda_max_m, max_distance_m=max_distance_m,
        generated_on=generated_on or datetime.date.today().isoformat(), code_state=git_state(), pricing=pricing,
        beta_sensitivities=beta_sensitivities)
    if out_path is not None:
        out_path.write_bytes(text.encode("ascii"))
        log.info("[garage-decay] wrote %s", out_path)
    return {"decay_length_m": release.visitors.decay_m, "decay_commute_length_m": release.commuters.decay_m,
            "option_price_beta_u_per_eur": beta,
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
            "universe_outcomes": release.visitor_outcomes, "commuter_outcomes": release.commuter_outcomes,
            "wolfsburg_outcomes": release.wolfsburg_outcomes,
            "universe_no_feasible_option": release.visitor_outcomes.no_feasible_option,
            "commuter_no_feasible_option": release.commuter_outcomes.no_feasible_option,
            "wolfsburg_no_feasible_option": release.wolfsburg_outcomes.no_feasible_option,
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
            "sensitivities": sensitivities, "beta_sensitivities": beta_sensitivities, "text": text}


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
    parser.add_argument("--beta", dest="option_price_beta_u_per_eur", type=float, default=None, metavar="UTILITY_PER_EUR",
                        help="the option price coefficient of the weights exp(-d / lambda - beta * c / 100) in utility per EUR "
                             f"(default: the config key {OPTION_PRICE_BETA_CONFIG_KEY} of --config, else the default of "
                             "prepare.PARKING_DEFAULTS); a value other than the config's is a sensitivity number and must not "
                             "be written under the release table's name")
    parser.add_argument("--sensitivity-beta", dest="sensitivity_betas", action="append", type=float, default=None,
                        metavar="UTILITY_PER_EUR",
                        help="a sensitivity of the option price coefficient (spec v3 B5): the same calibration with another "
                             "beta on the release dataset, written as a header line of the table, labelled sensitivity and "
                             "never a release value; repeatable (--sensitivity-beta 0 is the price-independent form of v2)")
    parser.add_argument("--maximum-no-feasible-option-share-warning", type=float,
                        default=MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING, metavar="SHARE",
                        help="a universe in which more than this share (0 to 1) of the stays has NO FEASIBLE OPTION is warned "
                             f"about (default {MAXIMUM_NO_FEASIBLE_OPTION_SHARE_WARNING:g}); a log-only plausibility threshold "
                             "that changes no result and is not a scientific parameter")
    parser.add_argument("--sensitivity-garages", dest="sensitivity_garages", action="append", type=parse_labelled_path,
                        metavar="LABEL=PATH", default=None,
                        help="a sensitivity of the option set (spec Amendment I6): the same calibration on another garage "
                             "dataset (e.g. the large-lot thresholds 30 and 100), written as a header line of the table, "
                             "labelled sensitivity and never a release value; repeatable, a label once")
    parser.add_argument("--minimum-reach-share-warning", type=float, default=MINIMUM_REACH_SHARE_WARNING, metavar="SHARE",
                        help="a universe in which fewer than this share (0 to 1) of the activities has an option of its "
                             f"measure within D_max is warned about (default {MINIMUM_REACH_SHARE_WARNING:g}); a log-only "
                             "plausibility threshold that changes no result and is not a scientific parameter (0 never warns)")
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
    try:
        validate_minimum_reach_share_warning(args.minimum_reach_share_warning)
        validate_maximum_no_feasible_option_share_warning(args.maximum_no_feasible_option_share_warning)
        if args.option_price_beta_u_per_eur is not None:
            _beta_value(args.option_price_beta_u_per_eur, "--beta")
            configured_beta = read_configured_option_price_beta(args.config)
            if (args.option_price_beta_u_per_eur != configured_beta
                    and (args.out is None or args.out.resolve() == (data / TABLE_RELATIVE).resolve())):
                parser.error(f"--beta {args.option_price_beta_u_per_eur:g} differs from the configured {configured_beta:g}, "
                             "which makes the run a sensitivity number, not the release value: pass --out with another path "
                             f"than the release table {data / TABLE_RELATIVE}")
        for sensitivity_beta in args.sensitivity_betas or ():
            _beta_value(sensitivity_beta, "--sensitivity-beta")
    except ValueError as error:
        parser.error(str(error))
    sensitivity_garages = None
    if args.sensitivity_garages is not None:
        sensitivity_garages = {}
        for label, path in args.sensitivity_garages:
            if label in sensitivity_garages:
                parser.error(f"--sensitivity-garages: the label {label!r} is given twice")
            sensitivity_garages[label] = path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    run(plans=args.plans, zones_path=data / ZONES_RELATIVE, tariffs_path=data / TARIFFS_RELATIVE,
        garages_path=data / GARAGES_RELATIVE,
        city_center_path=data / CITY_CENTER_RELATIVE, commute_path=data / COMMUTE_RELATIVE,
        out_path=args.out or data / TABLE_RELATIVE, overwrite=args.overwrite, max_distance_m=args.max_distance_m,
        config_path=args.config, lambda_min_m=args.lambda_min_m, lambda_max_m=args.lambda_max_m, tolerance=args.tolerance,
        option_price_beta_u_per_eur=args.option_price_beta_u_per_eur, sensitivity_betas=args.sensitivity_betas,
        facility_kinds=args.facility_kinds, sensitivity_garages=sensitivity_garages,
        minimum_reach_share_warning=args.minimum_reach_share_warning,
        maximum_no_feasible_option_share_warning=args.maximum_no_feasible_option_share_warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
