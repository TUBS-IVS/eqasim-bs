#!/usr/bin/env python
"""Replay the residents fleet draw of a production run and measure what the BEV:PHEV
composition aggregate correction's ``[0, 1]`` clip loses (issue C5a, 2026-11-04).

Why. The number of clipped cars cannot show whether the correction works; the BEV mass
its clip loses, i.e. the Kreis aggregate residual left after the correction, can. The
reasoning and the decision are in ADR-0125 (amendment 2026-11-04); the figures this
script produced for the 100 % production run are in the run manifest
``docs/runs/prod100-fleet-composition-clip-2026-11-04.yml``.

What this script does, in order (read-only on the run directory):

1. Rebuild the exact ``df_cars`` frame the household stage handed to ``sample_fleet``
   from three cached stage outputs of the run: the household stage's own output (its
   first ``n`` rows are the household cars in ``df_cars`` order; the routing
   ``default_car`` rows are appended after them), the enriched persons (``owner_age``)
   and the home-cell locations (the 5 km grid tilt columns). This skips the ~35 min
   per-household loop of ``build_household_car_frame``; the replay check in step 3
   proves the shortcut changed nothing.
2. Call the run's own ``sample_fleet`` (same seed, same flags, same code commit) with
   a capture wrapper around ``_neutralise_composition_aggregate`` that records, per
   car, the Kreis, the electric mass and the BEV fraction before and after the
   correction. The wrapper adds nothing to the draw.
3. Compare the replayed ``df_spec`` with the cached production output row by row
   (``segment``, ``powertrain``, brand, model, euro class, age band, type id). A
   replay that is not identical is not a replay; the script says so.
4. Derive, per shifted Kreis, the quantities the car count hides: the clipped cars
   split into pure and near-pure splits, the clipped share of the electric mass, the
   BEV mass the clip loses and the resulting aggregate residual.

Usage (on the machine that holds the run, with the run's own Python environment)::

    PYTHONDONTWRITEBYTECODE=1 python replay_composition_clip.py \\
        --run-dir ~/prod100_20261031 --out-dir ~/c5a_replay_20261104

``--module-override PATH`` loads a patched ``fleet_sampling_de.py`` instead of the
run's own copy (with ``--no-capture``): the "after the fix" replay that proves the fix
does not alter a single sampled vehicle.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("replay_composition_clip")

#: synpp cache stems of ``prod100_20261031`` (read from the "Loading cache for ..."
#: lines of the household stage in logs/run_20261031T071313.log, 22:06-22:42).
DEFAULT_STEMS = {
    "household": "braunschweig.synthesis.vehicles.cars.household__6b6784a39cfbf5cdf97abeb2ac00d4ce",
    "persons": "braunschweig.popsim.enriched_adapter__451bf968c790012e5fcf2ace9efddefe",
    "home_locations": "braunschweig.synthesis.locations.home_cell__7d76184709d2b6fcaf0735a52d801a42",
}

#: The columns whose equality with the cached production output defines "identical".
COMPARED_COLUMNS = ("vehicle_id", "segment", "powertrain", "euro_class", "age_band",
                    "brand", "model", "type_id")

#: A car counts as "numerically pure" when its distance to the boundary the shift points
#: at is below this (BEV fraction within 1e-6 of 1 for a positive shift, of 0 for a
#: negative one). ``rake_2d`` floors the seed, so a structural zero never stays an exact
#: zero: such cars end up near the boundary, not on it.
PURE_HEADROOM = 1e-6


def load_pickle(path: Path):
    with open(path, "rb") as handle:
        return pickle.load(handle)


def load_fleet_module(code_dir: Path, override: Path | None):
    """Import the run's ``fleet_sampling_de`` (or a patched copy of it)."""
    sys.path.insert(0, str(code_dir))
    import braunschweig.synthesis.vehicles  # noqa: F401  (parent package first)
    name = "braunschweig.synthesis.vehicles.fleet_sampling_de"
    if override is None:
        import braunschweig.synthesis.vehicles.fleet_sampling_de as module
        return module
    spec = importlib.util.spec_from_file_location(name, str(override))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    setattr(sys.modules["braunschweig.synthesis.vehicles"], "fleet_sampling_de", module)
    return module


def rebuild_df_cars(cache_dir: Path, stems: dict, data_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rebuild the ``df_cars`` frame of the household stage; return it with the cached output."""
    from braunschweig.data.kba import fleet_tables as ft
    from braunschweig.synthesis.vehicles.cars import household

    df_types, df_vehicles = load_pickle(cache_dir / (stems["household"] + ".p"))
    n_fleet = int((df_vehicles["type_id"] != "default_car").sum())
    # The routing default_car rows are CONCATENATED after the fleet rows
    # (_add_default_cars_for_non_owners, ignore_index=True): the fleet is a prefix.
    if not (df_vehicles["type_id"].iloc[:n_fleet] != "default_car").all():
        raise SystemExit("default_car rows are not a suffix of the cached household output")
    fleet_rows = df_vehicles.iloc[:n_fleet].reset_index(drop=True)
    logger.info("cached household output: %d rows, %d fleet cars, %d default_car rows",
                len(df_vehicles), n_fleet, len(df_vehicles) - n_fleet)

    df_persons = load_pickle(cache_dir / (stems["persons"] + ".p"))
    df_home_locations = load_pickle(cache_dir / (stems["home_locations"] + ".p"))

    household_dtype = df_persons["household_id"].dtype
    owner_dtype = df_persons["person_id"].dtype
    df_cars = pd.DataFrame({
        "household_id": fleet_rows["household_id"].astype(household_dtype).to_numpy(),
        "owner_id": fleet_rows["owner_id"].astype(owner_dtype).to_numpy(),
        "economic_status": fleet_rows["economic_status"].astype(str).to_numpy(),
        "kreis_ags5": fleet_rows["kreis_ags5"].astype(str).to_numpy(),
        "gemeinde": fleet_rows["gemeinde"].to_numpy(),
        "raumtyp": fleet_rows["raumtyp"].to_numpy(),
    })
    age_by_person = df_persons.drop_duplicates("person_id").set_index("person_id")["age"]
    df_cars["owner_age"] = age_by_person.reindex(df_cars["owner_id"]).astype(float).to_numpy()
    if df_cars["owner_age"].isna().any():
        raise SystemExit("owner_age could not be recovered for every fleet car")
    df_cars = household.assign_vehicle_ids(df_cars)
    if not (df_cars["vehicle_id"].to_numpy() == fleet_rows["vehicle_id"].to_numpy()).all():
        raise SystemExit("rebuilt vehicle ids differ from the cached output: row order is not df_cars order")

    # The grid tilt block of household.execute, verbatim.
    grid_df = ft.load_ev_grid(data_path)
    ev_series = household.grid_ev_share_for_homes(df_home_locations, grid_df)
    df_cars = df_cars.join(ev_series.rename("grid_ev_share"), on="household_id", how="left")
    commune_map = (df_home_locations[["household_id", "commune_id"]]
                   .drop_duplicates("household_id")
                   .set_index("household_id")["commune_id"])
    df_cars["_commune_id_grid"] = df_cars["household_id"].map(commune_map)
    hh_grid = (df_cars[["household_id", "_commune_id_grid", "grid_ev_share"]]
               .drop_duplicates("household_id")
               .rename(columns={"_commune_id_grid": "commune_id"}))
    hh_grid_mean = household.gemeinde_grid_mean(hh_grid)
    df_cars = df_cars.join(hh_grid_mean.rename("gemeinde_grid_mean"),
                           on="household_id", how="left")
    df_cars = df_cars.drop(columns=["_commune_id_grid"])
    return df_cars, fleet_rows


class Capture:
    """Per-car state before and after ``_neutralise_composition_aggregate``."""

    def __init__(self) -> None:
        self.arrays: dict[str, np.ndarray] | None = None
        self.deltas: dict[str, float] | None = None
        self.clipped_count: int | None = None


def install_capture(fleet, capture: Capture) -> None:
    original = fleet._neutralise_composition_aggregate

    def wrapper(unmasked_pmfs, car_kreis, car_composition_factors, powertrain_idx):
        before = list(unmasked_pmfs)  # the function replaces entries, it never mutates arrays
        result = original(unmasked_pmfs, car_kreis, car_composition_factors, powertrain_idx)
        bev_index = powertrain_idx["bev"]
        phev_index = powertrain_idx["phev"]
        n = len(before)
        bev_before = np.zeros(n)
        phev_before = np.zeros(n)
        bev_after = np.zeros(n)
        phev_after = np.zeros(n)
        for position in range(n):
            if before[position] is None:
                continue
            bev_before[position] = before[position][bev_index]
            phev_before[position] = before[position][phev_index]
            bev_after[position] = unmasked_pmfs[position][bev_index]
            phev_after[position] = unmasked_pmfs[position][phev_index]
        capture.arrays = {
            "kreis": np.asarray(list(car_kreis), dtype=str),
            "has_factors": np.array([f is not None for f in car_composition_factors]),
            "bev_before": bev_before, "phev_before": phev_before,
            "bev_after": bev_after, "phev_after": phev_after,
        }
        if isinstance(result, tuple):
            # The code the run used (commit 7a41f0ba): ``(deltas, clipped_count)``.
            capture.deltas = dict(result[0])
            capture.clipped_count = int(result[1])
        else:
            # Since the C5a change: ``{kreis: CompositionShift}``.
            capture.deltas = {kreis: shift.shift for kreis, shift in result.items()}
            capture.clipped_count = sum(shift.clipped_cars for shift in result.values())
        return result

    fleet._neutralise_composition_aggregate = wrapper


def analyse(arrays: dict, deltas: dict, segment: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Per-Kreis clip report, per-segment breakdown and totals from the captured arrays.

    Everything is derived from the captured pmf entries and the per-Kreis shift, with the
    same float operations as the production loop, so the count of clipped cars below must
    equal the production count (checked by the caller).
    """
    kreis = arrays["kreis"]
    bev_before, phev_before = arrays["bev_before"], arrays["phev_before"]
    bev_after, phev_after = arrays["bev_after"], arrays["phev_after"]
    electric = bev_before + phev_before
    has_electric = electric > 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        fraction_before = np.where(has_electric, bev_before / electric, 0.0)
        fraction_after = np.where(has_electric, bev_after / (bev_after + phev_after), 0.0)

    rows = []
    segment_rows = []
    total = {"clipped_cars": 0, "clipped_electric": 0.0, "electric": 0.0, "lost_bev": 0.0,
             "cars_in_shifted_kreise": 0}
    for kreis_code, delta in sorted(deltas.items()):
        in_kreis = kreis == kreis_code
        electric_cars = in_kreis & has_electric
        electric_total = float(electric[electric_cars].sum())
        target = fraction_before + delta
        clipped = electric_cars & ((target < 0.0) | (target > 1.0))
        excess = np.where(clipped, target - np.clip(target, 0.0, 1.0), 0.0)  # signed
        lost_bev = float((electric * excess).sum())
        # Distance to the boundary the shift points at (upper for a positive shift).
        headroom = (1.0 - fraction_before) if delta > 0 else fraction_before
        pure = electric_cars & (headroom < PURE_HEADROOM)
        # Cross-check of the algebra on the captured AFTER state: the realised BEV total
        # must equal the intended one minus what the clip lost.
        intended_bev = float((electric[electric_cars] * fraction_before[electric_cars]).sum()
                             + delta * electric_total)
        realised_bev = float((electric[electric_cars] * fraction_after[electric_cars]).sum())
        rows.append({
            "kreis": kreis_code,
            "cars": int(in_kreis.sum()),
            "cars_with_electric_mass": int(electric_cars.sum()),
            "electric_mass": electric_total,
            "electric_share_of_all_cars_pp": 100.0 * electric_total / max(int(in_kreis.sum()), 1),
            "delta_pp_of_electric_mass": 100.0 * delta,
            "clipped_cars": int(clipped.sum()),
            "clipped_numerically_pure": int((clipped & pure).sum()),
            "clipped_near_boundary": int((clipped & ~pure).sum()),
            "numerically_pure_cars": int(pure.sum()),
            "numerically_pure_electric_share": float(electric[pure].sum()) / electric_total if electric_total else 0.0,
            "clipped_electric_share": float(electric[clipped].sum()) / electric_total if electric_total else 0.0,
            "lost_bev_mass": lost_bev,
            "residual_pp_of_electric_mass": 100.0 * lost_bev / electric_total if electric_total else 0.0,
            "residual_pp_of_all_cars": 100.0 * lost_bev / max(int(in_kreis.sum()), 1),
            "residual_over_delta": (lost_bev / electric_total / delta) if (electric_total and delta) else 0.0,
            "algebra_check_abs_error": abs((intended_bev - realised_bev) - lost_bev),
        })
        for name in sorted(set(segment[clipped])):
            of_segment = clipped & (segment == name)
            segment_rows.append({
                "kreis": kreis_code, "segment": name, "clipped_cars": int(of_segment.sum()),
                "clipped_electric_mass": float(electric[of_segment].sum()),
                "numerically_pure": int((of_segment & pure).sum()),
                "headroom_min": float(headroom[of_segment].min()),
                "headroom_max": float(headroom[of_segment].max()),
            })
        total["clipped_cars"] += int(clipped.sum())
        total["clipped_electric"] += float(electric[clipped].sum())
        total["electric"] += electric_total
        total["lost_bev"] += lost_bev
        total["cars_in_shifted_kreise"] += int(in_kreis.sum())
    summary = {
        "shifted_kreise": len(deltas),
        "clipped_cars_total": total["clipped_cars"],
        "cars_in_shifted_kreise": total["cars_in_shifted_kreise"],
        "clipped_electric_share_of_shifted_kreise": (
            total["clipped_electric"] / total["electric"] if total["electric"] else 0.0),
        "lost_bev_mass_total": total["lost_bev"],
        "residual_pp_of_electric_mass_pooled": (
            100.0 * total["lost_bev"] / total["electric"] if total["electric"] else 0.0),
    }
    return pd.DataFrame(rows), pd.DataFrame(segment_rows), summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="the run directory (holds cache/ and the code worktree)")
    parser.add_argument("--code-dir", type=Path, default=None,
                        help="the run's code worktree (default: RUN_DIR/eqasim-bs)")
    parser.add_argument("--out-dir", type=Path, required=True, help="where results are written")
    parser.add_argument("--random-seed", type=int, default=1234)
    parser.add_argument("--module-override", type=Path, default=None,
                        help="patched fleet_sampling_de.py to run instead of the run's copy")
    parser.add_argument("--no-capture", action="store_true",
                        help="do not wrap _neutralise_composition_aggregate (needed for a "
                             "patched module whose return value differs)")
    parser.add_argument("--stem", action="append", default=[], metavar="KEY=STEM",
                        help="override one of the cache stems (household, persons, home_locations)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    stems = dict(DEFAULT_STEMS)
    for item in args.stem:
        key, _, value = item.partition("=")
        stems[key] = value
    code_dir = (args.code_dir or args.run_dir / "eqasim-bs").resolve()
    cache_dir = (args.run_dir / "cache").resolve()
    data_path = str(code_dir / "eqasim-data" / "data")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    fleet = load_fleet_module(code_dir, args.module_override.resolve() if args.module_override else None)
    logger.info("fleet module: %s", fleet.__file__)
    df_cars, reference = rebuild_df_cars(cache_dir, stems, data_path)
    logger.info("df_cars rebuilt: %d cars in %.0f s", len(df_cars), time.time() - started)

    capture = Capture()
    if not args.no_capture:
        install_capture(fleet, capture)

    result = fleet.sample_fleet(
        df_cars, data_path, random_seed=args.random_seed, size_map=None,
        model_brands=True, consistency_v2=True, age_income_coupling=True,
        ev_income_tilt=True, wohnmobile_age_tilt=True,
        gemeinde_bev_composition_tilt=True, euro6_substage=True,
        population_label="residents")
    df_spec = result[0]
    logger.info("sample_fleet finished in %.0f s", time.time() - started)

    # -- replay check: identical to the cached production output? ---------------------
    mismatches = {}
    for column in COMPARED_COLUMNS:
        if column not in df_spec.columns or column not in reference.columns:
            mismatches[column] = "column missing"
            continue
        replayed = df_spec[column].astype(str).to_numpy()
        cached = reference[column].astype(str).to_numpy()
        mismatches[column] = int((replayed != cached).sum())
    identical = all(value == 0 for value in mismatches.values())
    verification = {"rows": int(len(df_spec)), "mismatches_by_column": mismatches,
                    "identical_to_production_output": identical,
                    "fleet_module": str(fleet.__file__)}
    (args.out_dir / "verification.json").write_text(json.dumps(verification, indent=2))
    logger.info("REPLAY CHECK: %s", json.dumps(verification))

    if capture.arrays is not None:
        # A fixed-width unicode array, not an object array: reading it needs no pickle.
        segment = df_spec["segment"].astype(str).to_numpy().astype("U32")
        np.savez_compressed(args.out_dir / "capture.npz", **capture.arrays, segment=segment)
        per_kreis, per_segment, summary = analyse(capture.arrays, capture.deltas, segment)
        summary["production_function_clipped_count"] = capture.clipped_count
        summary["independent_clipped_count_matches"] = (
            capture.clipped_count == summary["clipped_cars_total"])
        per_kreis.to_csv(args.out_dir / "per_kreis.csv", index=False)
        per_segment.to_csv(args.out_dir / "clipped_by_segment.csv", index=False)
        (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        logger.info("SUMMARY: %s", json.dumps(summary))
        logger.info("\n%s", per_kreis.to_string(index=False))
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
