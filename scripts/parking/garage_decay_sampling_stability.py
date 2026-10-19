"""Sampling stability of the decay lengths lambda and lambda_c (parking cost zones v3, Task B7 fix round 2, issue #436).

The calibration of ``calibrate_garage_decay.py`` fits lambda and lambda_c on the 25 % reference plans, one synthetic
population. This script measures how much the two values would move under another 25 % sample of persons of the same
kind. It is a SAMPLING SENSITIVITY, never a release value: it changes no config key and no calibrated value, and it says
nothing about the uncertainty of the SrV targets (the target sensitivities of the calibration table do) or of the synthesis
of the population.

It reuses the functions of the calibration module and copies none of them: the universes (``read_main_activities``,
``universe_masks``), the stays (``build_stays``), the production pricing and the measures of every stay (``price_stay``,
``option_share``, ``is_payer``) and the bisection (``calibrate_decay``). What it adds is the weighting of the stays by PERSON: the
mean off-street share of a universe is the weighted mean over its stays with the weight of the person of the stay (the measure
of ``mean_option_share`` with person weights; with every weight 1 it is the module's own mean, which the script checks at the
release lambda to 1e-12 before it measures anything), and the per-stay shares are memoised per decay length (a bisection
evaluates the same decay lengths for many samples; the decay length is rounded to ``calibrate_garage_decay.LAMBDA_DECIMALS``, as
the module rounds its own result).

Two measurements, both on the PERSONS of the plans (a person's stays stay together; households are not split by a person-level
resample, which is a limitation):

* SPLIT HALF: for each of ``--split-seeds`` fixed seeds (0, 1, ...) the persons are split at random into two halves A and B,
  each half is calibrated on its own, and ``SD(A - B) / sqrt(2)`` over the seeds is the standard deviation of one half sample (the
  two halves are disjoint and so independent, ``Var(A - B) = 2 sigma^2``). A half is 50 % of the 25 % plans, so the standard
  deviation of a 25 % sample is that / sqrt(2) (the variance of a mean falls with 1 / n) and of a 100 % sample 1 / 2 of that.
* BOOTSTRAP: for each of ``--bootstrap`` fixed seeds (1000, 1001, ...) the persons are resampled with replacement (the weight of a
  person is the number of times it is drawn) and the full resample is calibrated; the standard deviation over the resamples is the
  standard deviation of a 25 % sample directly (the extrapolation to 100 % is again 1 / 2).

The bisection of the production run stops at the first midpoint within its tolerance ``--production-tolerance`` (0.0005 of the
share), which leaves a lambda uncertain by several metres; the samples are calibrated with the tight ``--tolerance`` (2e-5), so the
spread of the values is the sampling and not the bisection. The table also states the range of lambda that the production tolerance
alone allows (the full sample calibrated to the target minus and plus the production tolerance). A sample whose target is
unreachable is counted and left out of the standard deviations, never given an invented value.

Output: the committed table ``parking_garage_decay_sampling_stability_2026.csv`` (long format ``quantity,value,unit,note`` under a
header with the inputs and their SHA-256, the method and the limitations; every single run is a row, so the standard deviations
can be recomputed). The table belongs to the plans, zone file, tariff table, garage dataset and calibration table whose SHA-256
its header names. Run it on the plans of the reference scenario and on a clean tree with committed code (the code-state line
names the commit); the script refuses to replace an existing table unless ``--overwrite`` is given.

Usage (from the repository root)::

    python scripts/parking/garage_decay_sampling_stability.py --plans <population.xml.gz> [--data-path eqasim-data/data]
        [--out eqasim-data/data/braunschweig/parking/parking_garage_decay_sampling_stability_2026.csv] [--overwrite]
        [--split-seeds 20] [--bootstrap 50] [--tolerance 2e-5]

CRS: EPSG:25832 (distances in metres); shares are 0 to 1; no money value is produced.
"""
from __future__ import annotations

import argparse
import datetime
import logging
import math
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_garage_decay as cgd  # noqa: E402
from braunschweig.parking import cost, tariff_export  # noqa: E402
from braunschweig.parking import zones as parking_zones  # noqa: E402

log = logging.getLogger("garage_decay_sampling_stability")

TABLE_RELATIVE = "braunschweig/parking/parking_garage_decay_sampling_stability_2026.csv"
DEFAULT_SPLIT_SEEDS = 20
DEFAULT_BOOTSTRAP_RESAMPLES = 50
#: The bootstrap seeds are ``BOOTSTRAP_SEED_OFFSET + b`` so that they never repeat a split seed (0 ... ``--split-seeds`` - 1).
BOOTSTRAP_SEED_OFFSET = 1000
#: Tolerance (share, 0 to 1) of the bisection of every sample: tight, so that the spread is the sampling and not the bisection.
DEFAULT_SAMPLE_TOLERANCE = 2e-5
#: The universes that the sampling stability is measured on: the destination universe (lambda, payment-inclusive measure) and
#: the commuter universe (lambda_c, paid-only payer-conditional measure). The Wolfsburg check universe has no calibration.
UNIVERSES = ("visitors", "commuters")
#: A consistency check that the person-weighted mean with every weight 1 is the mean of the calibration module.
UNIT_WEIGHT_AGREEMENT = 1e-12
#: How far (m) a production lambda recomputed here may differ from the committed calibration table (both round to 0.01 m).
PRODUCTION_REPRODUCTION_M = 0.005
TABLE_COLUMNS = ("quantity", "value", "unit", "note")


class UniverseSample(NamedTuple):
    """One calibration universe: its priced stays, the index of the person of every stay (into the persons of the plans), the
    measure and whether it is payer-conditional (as ``calibrate_garage_decay`` defines them) and the SrV target value."""

    name: str
    stays: tuple
    stay_person: np.ndarray
    measure: str
    payers_only: bool
    target: float


class StayShares:
    """The off-street share and the payer flag of every stay of a universe at a decay length, from the production pricing
    (``price_stay``, ``option_share``, ``is_payer``), memoised per decay length rounded to ``LAMBDA_DECIMALS``."""

    def __init__(self, sample: UniverseSample, paid_by_id: Mapping, option_price_beta_u_per_eur: float):
        self.sample = sample
        self.paid_by_id = paid_by_id
        self.beta = option_price_beta_u_per_eur
        self._cache = {}
        self.pricings = 0

    def arrays(self, decay_m: float) -> tuple:
        key = round(float(decay_m), cgd.LAMBDA_DECIMALS)
        if key not in self._cache:
            shares, payers = [], []
            for stay in self.sample.stays:
                detail = cgd.price_stay(stay, key, self.beta)
                shares.append(cgd.option_share(detail, self.sample.measure, self.paid_by_id))
                payers.append(cgd.is_payer(detail))
            self._cache[key] = (np.array(shares, dtype=float), np.array(payers, dtype=float))
            self.pricings += 1
        return self._cache[key]

    def weighted_mean(self, person_weights: np.ndarray, decay_m: float) -> float:
        """The mean share over the stays weighted by the weight of their person; the payer-conditional measure leaves the
        non-payers out of numerator and denominator. ``ValueError`` where the weights leave no stay (or no payer)."""
        shares, payers = self.arrays(decay_m)
        weights = person_weights[self.sample.stay_person].astype(float)
        if self.sample.payers_only:
            weights = weights * payers
        total = float(weights.sum())
        if total <= 0.0:
            raise ValueError(f"the person weights leave no {'payer ' if self.sample.payers_only else ''}stay in the "
                             f"{self.sample.name} universe")
        return float((weights * shares).sum() / total)


def calibrate_weighted(shares: StayShares, person_weights: np.ndarray, tolerance: float, target: float | None = None):
    """lambda (m) of the universe for the person weights, by ``calibrate_decay`` on the weighted mean, or None where the target is
    unreachable with this sample (``TargetUnreachable``: never an end of the range). ``target`` defaults to the SrV target of the
    universe."""
    wanted = shares.sample.target if target is None else target
    try:
        return cgd.calibrate_decay(lambda decay_m: shares.weighted_mean(person_weights, decay_m), wanted,
                                   tolerance=tolerance).decay_m
    except cgd.TargetUnreachable:
        return None


def split_weights(person_count: int, seed: int) -> tuple:
    """The 0/1 person weights of the two halves A and B of the split of ``seed``: a seeded random permutation of the persons, the
    first ``person_count // 2`` in A, the rest in B (the halves are disjoint)."""
    permutation = np.random.default_rng(seed).permutation(person_count)
    half_a = np.zeros(person_count)
    half_a[permutation[: person_count // 2]] = 1.0
    return half_a, 1.0 - half_a


def bootstrap_weights(person_count: int, seed: int) -> np.ndarray:
    """The person weights of the bootstrap resample of ``seed``: the number of times each person is drawn, with replacement,
    ``person_count`` draws."""
    draws = np.random.default_rng(seed).integers(0, person_count, person_count)
    return np.bincount(draws, minlength=person_count).astype(float)


def half_sample_sd(values_a: Sequence, values_b: Sequence) -> float:
    """The standard deviation of one half sample, ``SD(A - B) / sqrt(2)`` over the seeds (``ddof`` 1): A and B are disjoint
    halves, so ``Var(A - B) = 2 sigma^2``."""
    difference = np.asarray(values_a, dtype=float) - np.asarray(values_b, dtype=float)
    return float(difference.std(ddof=1) / math.sqrt(2.0))


def sd_of_full_from_half(half_sd: float) -> float:
    """The standard deviation of the sample that is twice a half (the 25 % plans from the 12.5 % halves): ``half_sd / sqrt(2)``."""
    return half_sd / math.sqrt(2.0)


def sd_of_larger_sample(sd: float, factor: float) -> float:
    """The standard deviation of a sample ``factor`` times as large (the variance of a mean falls with 1 / n)."""
    return sd / math.sqrt(factor)


def run_split_half(shares: StayShares, person_count: int, seeds: int, tolerance: float) -> list:
    """[{"seed", "half", "decay_m" (None where unreachable)}] of the split-half calibrations of the seeds ``0 ... seeds - 1``."""
    rows = []
    for seed in range(seeds):
        for half, weights in zip(("A", "B"), split_weights(person_count, seed)):
            rows.append({"seed": seed, "half": half, "decay_m": calibrate_weighted(shares, weights, tolerance)})
    return rows


def run_bootstrap(shares: StayShares, person_count: int, resamples: int, tolerance: float) -> list:
    """[{"resample", "decay_m" (None where unreachable)}] of the person bootstrap with the seeds ``BOOTSTRAP_SEED_OFFSET + b``."""
    return [{"resample": b, "decay_m": calibrate_weighted(
        shares, bootstrap_weights(person_count, BOOTSTRAP_SEED_OFFSET + b), tolerance)} for b in range(resamples)]


def summarise_split_half(rows: list) -> dict:
    """The standard deviations of the split-half rows of one universe: the half sample (``SD(A - B) / sqrt(2)``), the 25 % sample
    and the 100 % sample extrapolated from it, the mean, minimum and maximum of the half values and the runs left out because the
    target was unreachable. A seed with an unreachable half leaves both halves out."""
    by_seed = {}
    for row in rows:
        by_seed.setdefault(row["seed"], {})[row["half"]] = row["decay_m"]
    pairs = [(halves["A"], halves["B"]) for halves in by_seed.values() if halves["A"] is not None and halves["B"] is not None]
    if len(pairs) < 2:
        raise ValueError("fewer than two split seeds have both halves calibrated: no standard deviation")
    values_a, values_b = zip(*pairs)
    half_sd = half_sample_sd(values_a, values_b)
    sd_25 = sd_of_full_from_half(half_sd)
    values = np.array(values_a + values_b, dtype=float)
    return {"seeds_used": len(pairs), "seeds": len(by_seed), "unreachable_runs": sum(row["decay_m"] is None for row in rows),
            "half_sd_m": half_sd, "sd_25_pct_m": sd_25, "sd_100_pct_m": sd_of_larger_sample(sd_25, 4.0),
            "mean_m": float(values.mean()), "min_m": float(values.min()), "max_m": float(values.max())}


def summarise_bootstrap(rows: list) -> dict:
    """The distribution of the bootstrap lambdas of one universe: standard deviation (the 25 % sample directly, ``ddof`` 1), its
    extrapolation to 100 %, mean, 2.5 and 97.5 percent quantiles and the unreachable resamples."""
    values = np.array([row["decay_m"] for row in rows if row["decay_m"] is not None], dtype=float)
    if len(values) < 2:
        raise ValueError("fewer than two bootstrap resamples are calibrated: no standard deviation")
    low, high = np.percentile(values, [2.5, 97.5])
    sd = float(values.std(ddof=1))
    return {"resamples_used": len(values), "resamples": len(rows), "unreachable_runs": len(rows) - len(values), "sd_25_pct_m": sd,
            "sd_100_pct_m": sd_of_larger_sample(sd, 4.0), "mean_m": float(values.mean()), "p2_5_m": float(low),
            "p97_5_m": float(high)}


def check_unit_weights_agree(shares: StayShares, decay_m: float, person_count: int) -> float:
    """The largest difference between the person-weighted mean with every weight 1 and ``calibrate_garage_decay.mean_option_share``
    of the same stays at ``decay_m``; ``ValueError`` above ``UNIT_WEIGHT_AGREEMENT``. Returns the difference."""
    sample = shares.sample
    mine = shares.weighted_mean(np.ones(person_count), decay_m)
    theirs = cgd.mean_option_share(list(sample.stays), round(decay_m, cgd.LAMBDA_DECIMALS), shares.beta, sample.measure,
                                   shares.paid_by_id, sample.payers_only)
    if abs(mine - theirs) > UNIT_WEIGHT_AGREEMENT:
        raise ValueError(f"the person-weighted mean {mine!r} differs from the calibration module's mean_option_share "
                         f"{theirs!r} in the {sample.name} universe: the weighting is not the module's measure")
    return abs(mine - theirs)


def production_tolerance_range(shares: StayShares, person_count: int, production_tolerance: float, tolerance: float) -> tuple:
    """(lambda at the target minus the production tolerance, lambda at the target plus it) on the full sample, calibrated tightly:
    the range of lambda that the production bisection (it stops at the first midpoint within the tolerance) may return."""
    weights = np.ones(person_count)
    target = shares.sample.target
    return (calibrate_weighted(shares, weights, tolerance, target - production_tolerance),
            calibrate_weighted(shares, weights, tolerance, target + production_tolerance))


# --------------------------------------------------------------------------- the universes of the plans


def build_samples(plans: Path, data: Path, config_path: Path) -> dict:
    """The two universes of the plans as ``UniverseSample`` (stays built by the module's ``build_stays`` on the options of the
    release garage dataset), with the paid flags of the options, the option price coefficient, the number of persons (every
    person with a main activity, freight agents excluded) and the person ids. The same inputs and steps as
    ``calibrate_garage_decay.run``."""
    pricing = cgd.read_pricing_config(config_path)
    exposure = cgd.load_exposure_module()
    activities, persons = exposure.read_main_activities(plans)
    activities, persons, freight = cgd.exclude_freight_agents(activities, persons)
    zones_path = data / cgd.ZONES_RELATIVE
    tariffs = parking_zones.load_tariffs(data / cgd.TARIFFS_RELATIVE)
    tariffs_by_zone = {}
    for row in tariffs.to_dict(orient="records"):
        zone = tariff_export.tariff_row_to_zone(row, snapshot_date=pricing.tariff_snapshot_date)
        tariffs_by_zone[zone.zone_id] = zone
    zone_id = exposure.zone_per_activity(activities, zones_path)
    masks = cgd.universe_masks(activities, zone_id, cgd.wolfsburg_zone_ids(tariffs))
    _, options = cgd.load_priced_options(data / cgd.GARAGES_RELATIVE, None)
    paid_by_id = dict(zip(options["garage_id"].tolist(), cgd.paid_option_flags(options).tolist()))
    garages = [tariff_export.garage_row_to_tariff(row, monthly_imputation=pricing.monthly_imputation)
               for _, row in options.iterrows()]
    person_ids = sorted(activities["person_id"].unique().tolist())
    index_of = {person: index for index, person in enumerate(person_ids)}
    targets = {"visitors": cgd.read_city_center_target(data / cgd.CITY_CENTER_RELATIVE).value,
               "commuters": cgd.read_commuter_reference(data / cgd.COMMUTE_RELATIVE, cgd.COMMUTER_CLASS).value}
    specification = {"visitors": (masks.universe, cgd.MEASURE_ALL_OPTIONS, False),
                     "commuters": (masks.commuters, cgd.MEASURE_PAID_OPTIONS, True)}
    columns = ["purpose", "x", "y", "start_time_s", "end_time_s"]
    samples = {}
    for name, (mask, measure, payers_only) in specification.items():
        stays, _ = cgd.build_stays(activities.loc[mask, columns], zone_id[mask].reset_index(drop=True), tariffs_by_zone, garages,
                                   max_distance_m=pricing.max_distance_m,
                                   minimum_stay_s=pricing.minimum_stay_min * cost.SECONDS_PER_MINUTE)
        stay_person = np.array([index_of[person] for person in activities.loc[mask, "person_id"]], dtype=int)
        samples[name] = UniverseSample(name, tuple(stays), stay_person, measure, payers_only, targets[name])
        log.info("[sampling] %s universe: %d stays of %d persons", name, len(stays), len(set(stay_person.tolist())))
    return {"samples": samples, "paid_by_id": paid_by_id, "beta": pricing.option_price_beta_u_per_eur, "persons": persons,
            "freight_agents": freight, "person_count": len(person_ids)}


# --------------------------------------------------------------------------- the table


def _row(quantity: str, value, unit: str, note: str) -> str:
    if isinstance(value, float):
        text = "" if math.isnan(value) else f"{value:.2f}"
    else:
        text = "" if value is None else str(value)
    return ",".join(f'"{field}"' if "," in field else field for field in (quantity, text, unit, note))


def table_text(*, inputs: dict, persons: int, freight_agents: int, person_count: int, beta: float, tolerance: float,
               production_tolerance: float, production: dict, tolerance_range: dict, split_rows: dict, split: dict,
               boot_rows: dict, boot: dict, seeds: int, resamples: int, stays: dict, generated_on: str, code_state: str,
               runtime_s: float) -> str:
    """The sampling-stability table: the header (what, how, inputs, limitations), then the rows."""
    names = {"visitors": ("lambda", "decay_length_m"), "commuters": ("lambda_c", "decay_commute_length_m")}
    lines = [
        "# Sampling stability of the calibrated decay lengths (parking cost zones v3, Task B7 fix round 2, issue #436).",
        f"# Generated by scripts/parking/garage_decay_sampling_stability.py on {generated_on}; code state {code_state}.",
        "# A SAMPLING SENSITIVITY, NEVER A RELEASE VALUE: it changes no config key and no calibrated value and is no validation.",
        "# Question: how much would lambda and lambda_c move under another 25 % sample of persons of the same kind? The values",
        "# are those of calibrate_garage_decay.py, calibrated on person-weighted samples of the same stays with the module's own",
        "# functions (build_stays, price_stay, option_share, is_payer, calibrate_decay); the weighting by person is the only",
        "# addition and is checked against the module's mean_option_share (every weight 1) before anything is measured.",
        f"# SPLIT HALF: {seeds} fixed seeds (0 ... {seeds - 1}); the persons are split at random into two disjoint halves, each",
        "# half is calibrated; SD(A - B) / sqrt(2) over the seeds is the standard deviation of one half sample; a 25 % sample is",
        "# twice a half, so its SD is that / sqrt(2), and a 100 % sample is four times the 25 % sample, so its SD is half of the",
        "# 25 % SD.",
        f"# BOOTSTRAP: {resamples} fixed seeds ({BOOTSTRAP_SEED_OFFSET} ...); the persons are resampled with replacement and the",
        "# full resample is calibrated; the SD over the resamples is the 25 % SD directly; the 100 % SD is again half of it.",
        f"# Bisection tolerance of the samples {tolerance:g} of the share (the production run uses {production_tolerance:g},",
        "# which leaves lambda uncertain by the range stated in the rows production_tolerance_range_*).",
        f"# Option price coefficient {beta:g} per EUR (as the calibration). Persons {persons} (freight agents excluded:",
        f"# {freight_agents}); persons with a main activity {person_count}; stays: destination universe {stays['visitors']},",
        f"# commuter universe {stays['commuters']}. Runtime {runtime_s:.0f} s.",
        "# LIMITATIONS: persons are split or resampled one by one, so the members of a household are split (the real sampling",
        "# unit is the household, so the SD is a lower bound where households are correlated); one synthetic population (the",
        "# 25 % plans) is measured, with no synthesis uncertainty (the controls, the donor assignment and the draws of the",
        "# population are fixed); the SrV target uncertainty is separate (see the sensitivities of the calibration table); the SD",
        "# at 100 % is an extrapolation by 1 / sqrt(n) and not a measurement; a share is an expectation over the stays, not an",
        "# observed parking behaviour.",
        "# Inputs (path, SHA-256 of the file; text files hashed with LF line endings):",
    ]
    lines += [f"#   {name}: {path} sha256={digest}" for name, (path, digest) in inputs.items()]
    lines.append(",".join(TABLE_COLUMNS))
    for universe in UNIVERSES:
        symbol, row_name = names[universe]
        lines.append(_row(f"production_{row_name}", production[universe], "m",
                          f"{symbol} recomputed with the production tolerance on the full sample (equals the calibration table)"))
        low, high = tolerance_range[universe]
        lines.append(_row(f"production_tolerance_range_low_{symbol}", low, "m",
                          f"{symbol} at the target minus the production tolerance (tight bisection, full sample)"))
        lines.append(_row(f"production_tolerance_range_high_{symbol}", high, "m",
                          f"{symbol} at the target plus the production tolerance; (high - low) / 2 is the +- of the bisection"))
        half = split[universe]
        lines.append(_row(f"split_half_seeds_{symbol}", half["seeds_used"], "count", "seeds with both halves calibrated"))
        lines.append(_row(f"split_half_sd_of_one_half_{symbol}", half["half_sd_m"], "m", "SD(A - B) / sqrt(2) over the seeds"))
        lines.append(_row(f"split_half_sd_25_pct_{symbol}", half["sd_25_pct_m"], "m",
                          "sampling SD of the 25 % plans = SD of one half / sqrt(2)"))
        lines.append(_row(f"split_half_sd_100_pct_{symbol}", half["sd_100_pct_m"], "m",
                          "extrapolated sampling SD of a 100 % population = SD at 25 % / 2"))
        lines.append(_row(f"split_half_mean_{symbol}", half["mean_m"], "m", "mean of the half values"))
        lines.append(_row(f"split_half_min_{symbol}", half["min_m"], "m", "smallest half value"))
        lines.append(_row(f"split_half_max_{symbol}", half["max_m"], "m", "largest half value"))
        boots = boot[universe]
        lines.append(_row(f"bootstrap_resamples_{symbol}", boots["resamples_used"], "count", "resamples calibrated"))
        lines.append(_row(f"bootstrap_sd_25_pct_{symbol}", boots["sd_25_pct_m"], "m",
                          "sampling SD of the 25 % plans = SD over the person bootstrap resamples"))
        lines.append(_row(f"bootstrap_sd_100_pct_{symbol}", boots["sd_100_pct_m"], "m",
                          "extrapolated sampling SD of a 100 % population = SD at 25 % / 2"))
        lines.append(_row(f"bootstrap_mean_{symbol}", boots["mean_m"], "m", "mean of the resample values"))
        lines.append(_row(f"bootstrap_p2_5_{symbol}", boots["p2_5_m"], "m", "2.5 % quantile of the resample values"))
        lines.append(_row(f"bootstrap_p97_5_{symbol}", boots["p97_5_m"], "m", "97.5 % quantile of the resample values"))
        lines.append(_row(f"unreachable_runs_{symbol}", split[universe]["unreachable_runs"] + boots["unreachable_runs"], "count",
                          "split-half and bootstrap runs whose target was unreachable (left out, never given a value)"))
    for universe in UNIVERSES:
        symbol = names[universe][0]
        for row in split_rows[universe]:
            lines.append(_row(f"run_split_seed{row['seed']:02d}_{row['half']}_{symbol}", row["decay_m"], "m",
                              "calibrated on one half of the persons"))
        for row in boot_rows[universe]:
            lines.append(_row(f"run_bootstrap_{row['resample']:02d}_{symbol}", row["decay_m"], "m",
                              "calibrated on one person bootstrap resample"))
    return "\n".join(lines) + "\n"


def run(*, plans: Path, data: Path, config_path: Path, out_path: Path, overwrite: bool, seeds: int, resamples: int,
        tolerance: float, production_tolerance: float = cgd.DEFAULT_TOLERANCE, generated_on: str | None = None) -> dict:
    """Measure and write the table; returns the summaries. ``SystemExit`` where the table exists and ``overwrite`` is not set, and
    where the production lambdas recomputed here differ from the calibration table (another option set, plans or code)."""
    if out_path.exists() and not overwrite:
        raise SystemExit(f"{out_path} exists: pass --overwrite to replace it")
    started = time.time()
    built = build_samples(plans, data, config_path)
    person_count = built["person_count"]
    committed = cgd.read_calibration_table(data / cgd.TABLE_RELATIVE)
    shares = {name: StayShares(sample, built["paid_by_id"], built["beta"]) for name, sample in built["samples"].items()}
    expected = {"visitors": committed["decay_length_m"], "commuters": committed["decay_commute_length_m"]}
    production = {}
    for name in UNIVERSES:
        production[name] = calibrate_weighted(shares[name], np.ones(person_count), production_tolerance)
        if production[name] is None or abs(production[name] - expected[name]) > PRODUCTION_REPRODUCTION_M:
            raise SystemExit(f"the {name} lambda recomputed here ({production[name]}) is not the one of the calibration table "
                             f"({expected[name]}): the plans, the options or the code differ from the calibration")
        difference = check_unit_weights_agree(shares[name], production[name], person_count)
        log.info("[sampling] %s: production lambda %.2f m reproduced; unit weights agree with mean_option_share to %.1e", name,
                 production[name], difference)
    tolerance_range = {name: production_tolerance_range(shares[name], person_count, production_tolerance, tolerance)
                       for name in UNIVERSES}
    split_rows = {name: run_split_half(shares[name], person_count, seeds, tolerance) for name in UNIVERSES}
    boot_rows = {name: run_bootstrap(shares[name], person_count, resamples, tolerance) for name in UNIVERSES}
    split = {name: summarise_split_half(split_rows[name]) for name in UNIVERSES}
    boot = {name: summarise_bootstrap(boot_rows[name]) for name in UNIVERSES}
    for name in UNIVERSES:
        log.info("[sampling] %s: split-half SD of a half %.2f m, 25 %% SD %.2f m, 100 %% SD %.2f m; bootstrap 25 %% SD %.2f m",
                 name, split[name]["half_sd_m"], split[name]["sd_25_pct_m"], split[name]["sd_100_pct_m"],
                 boot[name]["sd_25_pct_m"])
    inputs = {name: (cgd.path_label(path), cgd.file_sha256(path)) for name, path in (
        ("plans", plans), ("zones", data / cgd.ZONES_RELATIVE), ("tariffs", data / cgd.TARIFFS_RELATIVE),
        ("garages", data / cgd.GARAGES_RELATIVE), ("srv2023_city_center_parking", data / cgd.CITY_CENTER_RELATIVE),
        ("srv2023_commute_parking_by_workplace_class", data / cgd.COMMUTE_RELATIVE),
        ("calibration_table", data / cgd.TABLE_RELATIVE))}
    text = table_text(
        inputs=inputs, persons=built["persons"], freight_agents=built["freight_agents"], person_count=person_count,
        beta=built["beta"], tolerance=tolerance, production_tolerance=production_tolerance, production=production,
        tolerance_range=tolerance_range, split_rows=split_rows, split=split, boot_rows=boot_rows, boot=boot, seeds=seeds,
        resamples=resamples, stays={name: len(shares[name].sample.stays) for name in UNIVERSES},
        generated_on=generated_on or datetime.date.today().isoformat(), code_state=cgd.git_state(),
        runtime_s=time.time() - started)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(text.encode("ascii"))
    log.info("[sampling] wrote %s", out_path)
    return {"production": production, "tolerance_range": tolerance_range, "split": split, "bootstrap": boot, "text": text}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plans", type=Path, required=True, help="MATSim plans file (.xml or .xml.gz) of the reference scenario")
    parser.add_argument("--data-path", type=Path, default=cgd.DEFAULT_DATA_PATH, help="data directory holding the inputs")
    parser.add_argument("--out", type=Path, default=None, help=f"the table to write (default: <data-path>/{TABLE_RELATIVE})")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing table")
    parser.add_argument("--config", type=Path, default=cgd.DEFAULT_CONFIG_PATH,
                        help="pipeline config (default configs/base_bs.yml)")
    parser.add_argument("--split-seeds", type=int, default=DEFAULT_SPLIT_SEEDS, help="number of fixed split-half seeds")
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP_RESAMPLES, help="number of fixed bootstrap resamples")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_SAMPLE_TOLERANCE,
                        help="bisection tolerance (share) of the samples; tight, so that the spread is the sampling")
    args = parser.parse_args(argv)
    if args.split_seeds < 2 or args.bootstrap < 2 or not 0.0 < args.tolerance < cgd.DEFAULT_TOLERANCE:
        parser.error("need at least two split seeds, two bootstrap resamples and 0 < tolerance < the production tolerance")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    run(plans=args.plans, data=args.data_path, config_path=args.config,
        out_path=args.out or args.data_path / TABLE_RELATIVE, overwrite=args.overwrite, seeds=args.split_seeds,
        resamples=args.bootstrap, tolerance=args.tolerance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
