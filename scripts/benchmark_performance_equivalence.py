"""Compare real kernels in independent original/OFF/ON Python processes.

Synthetic timings are bounded kernel measurements, not full-run projections.
An optional trusted pickle input bundle maps case names to fixture dictionaries.
No source checkout, production cache or scientific configuration is modified.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import pickle
import platform
import statistics
import subprocess
import sys
import tempfile
import time

CASES = ("fleet", "home", "validation", "donor")


def make_fixture(case, size):
    import numpy as np
    import pandas as pd

    ids = np.arange(size)
    if case == "fleet":
        return {
            "persons": pd.DataFrame({
                "household_id": ids, "person_id": ids, "age": 40 + ids % 30,
                "has_license": True, "number_of_cars": 1 + ids % 2,
                "economic_status": "medium",
            }),
            "homes": pd.DataFrame({"household_id": ids, "commune_id": "031010000000"}),
            "regiostar": pd.DataFrame({
                "commune_id": ["03101000"], "name": ["Braunschweig"], "regiostar7": [71],
            }),
        }
    if case == "home":
        import geopandas as gpd
        from shapely.geometry import Point, box

        # Repeated assignments within one cell expose repeated CRS work without
        # requiring the large production building inventory.
        count = min(size, 300)
        cell = "CRS3035RES100mN2689100E4337000"
        return {
            "households": pd.DataFrame({
                "household_id": ["h%d" % i for i in range(count)],
                "commune_id": "031010000000", "ZENSUS100m": cell,
                "building_type_3class": "mehrfamilienhaus", "household_size": 1,
            }),
            "buildings": gpd.GeoDataFrame({
                "building_id": [0], "weight": [100000.0], "area_m2": [100000.0],
                "commune_id": ["031010000000"],
                "footprint": gpd.GeoSeries([box(4337010, 2689110, 4337090, 2689190)],
                                           crs=3035).to_crs(25832).tolist(),
            }, geometry=[Point(4337050, 2689150)], crs=3035).to_crs(25832),
            "cells": pd.DataFrame([{
                "ZENSUS100m": cell,
                "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
                "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": float(count),
                "BewohntWhg_Leerstand_100m_Gitter": float(count),
                "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": float(count),
            }]),
        }
    if case == "validation":
        trips = pd.DataFrame({
            "person_id": np.repeat(ids, 2),
            "departure_time": np.tile([28800.0, 61200.0], size),
            "arrival_time": np.tile([30600.0, 62400.0], size),
            "preceding_purpose": np.tile(["home", "work"], size),
            "following_purpose": np.tile(["work", "home"], size),
            "is_first_trip": np.tile([True, False], size),
            "is_last_trip": np.tile([False, True], size),
        })
        trips.loc[trips.index % 20 == 0, "arrival_time"] = 64000.0
        return {"trips": trips.iloc[::-1].copy()}
    pool = pd.DataFrame({
        "H_ID": ids + 1000, "P_ID": 1, "HP_ALTER": 6 + ids % 75,
        "HP_SEX": 1 + ids % 2, "P_FSCHEIN": 1 + ids % 3,
        "P_TAET": 1 + ids % 9, "P_FKARTE": 1 + ids % 5,
        "P_GEW": 0.5 + ids % 7,
    }, index=ids * 3 + 7)
    targets = [row for _, row in pool.iloc[:min(size, 200)].iterrows()]
    return {"pool": pool, "targets": targets}


def kernel(case, mode, fixture, *, donor_age_bands="fine"):
    import numpy as np

    on = mode == "on"
    original = mode == "original"
    if case == "fleet":
        from braunschweig.synthesis.vehicles.cars.household import build_household_car_frame
        kwargs = {} if original else {"indexed_home_lookup": on}
        return build_household_car_frame(
            fixture["persons"], fixture["homes"], fixture["regiostar"], **kwargs)
    if case == "home":
        from braunschweig.synthesis.locations.home_cell import assign_homes_typed
        kwargs = {} if original else {"batch_coordinates": on}
        frame, report = assign_homes_typed(
            fixture["households"], fixture["buildings"], fixture["cells"],
            random_seed=1234, **kwargs)
        return frame, dataclasses.asdict(report)
    if case == "validation":
        from braunschweig.popsim.plan_validation import PlanValidator
        kwargs = {} if original else {"vectorized": on}
        return dataclasses.asdict(PlanValidator(**kwargs).validate_trips(fixture["trips"]))
    from braunschweig.popsim import weekend_plan_match as matching
    rng = np.random.RandomState(1234)
    edges = (matching.FINE_CHILD_AGE_BAND_EDGES if donor_age_bands == "fine"
             else matching.AGE_BAND_EDGES)
    kwargs = {"age_band_edges": edges}
    if on:
        kwargs["prepared_pool"] = matching.prepare_person_pool(
            fixture["pool"], age_band_edges=edges)
    draws = [matching.match_person(row, fixture["pool"], rng=rng, **kwargs)
             for row in fixture["targets"]]
    return draws, rng.get_state()


def assert_exact(actual, expected):
    import numpy as np
    import pandas as pd

    if isinstance(expected, pd.DataFrame):
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        if hasattr(expected, "geometry"):
            assert actual.crs == expected.crs
            assert list(actual.geometry.to_wkb()) == list(expected.geometry.to_wkb())
    elif isinstance(expected, np.ndarray):
        np.testing.assert_array_equal(actual, expected)
        assert actual.dtype == expected.dtype
    elif isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            assert_exact(actual[key], expected[key])
    elif isinstance(expected, (tuple, list)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_exact(left, right)
    else:
        assert type(actual) is type(expected), (type(actual), type(expected))
        if isinstance(expected, np.generic):
            assert actual.dtype == expected.dtype
        assert actual == expected, (actual, expected)


def git_identity(root):
    def read(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    return {"commit": read("rev-parse", "HEAD"), "dirty": bool(read("status", "--porcelain"))}


def worker(args):
    # Protect even direct --mode invocations before importing the selected checkout.
    sys.dont_write_bytecode = True
    root = args.repository_root.resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    if args.input_bundle:
        with args.input_bundle.open("rb") as stream:
            fixture = pickle.load(stream)[args.case]
    else:
        fixture = make_fixture(args.case, args.size)
    # Import once outside the timed samples. Every call resets its random state,
    # while candidate preparation remains INSIDE the timed public kernel.
    warm = kernel(args.case, args.mode, fixture, donor_age_bands=args.donor_age_bands)
    timings = []
    for _ in range(args.repeat):
        started = time.perf_counter()
        result = kernel(args.case, args.mode, fixture, donor_age_bands=args.donor_age_bands)
        timings.append(time.perf_counter() - started)
        assert_exact(result, warm)
    with args.output.open("wb") as stream:
        pickle.dump({"result": result, "seconds": timings,
                     "input_rows": {key: len(value) for key, value in fixture.items()}},
                    stream, protocol=4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--input-bundle", type=Path, help="Trusted pickle of case fixture dictionaries")
    parser.add_argument("--donor-age-bands", choices=("fine", "coarse"), default="fine",
                        help="Donor matching age edges (default: production fine child bands)")
    parser.add_argument("--mode", choices=("original", "off", "on"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.size < 1 or args.repeat < 1:
        parser.error("--size and --repeat must be positive")
    args.output = args.output.resolve()
    if args.input_bundle:
        args.input_bundle = args.input_bundle.resolve()
    if args.mode:
        worker(args)
        return
    if args.reference_root is None:
        parser.error("--reference-root is required for comparison against original code")
    root, reference = args.repository_root.resolve(), args.reference_root.resolve()
    sys.path.insert(0, str(root))
    cases = [args.case] if args.case else list(CASES)
    report = {
        "scope": "bounded function benchmarks; no full-pipeline speedup claim",
        "reference": git_identity(reference), "candidate": git_identity(root),
        "environment": {"python": sys.version, "platform": platform.platform(),
                        **{package: importlib.metadata.version(package)
                           for package in ("numpy", "pandas", "geopandas", "synpp")}},
        "size": args.size, "repeat": args.repeat, "random_seed": 1234,
        "input_kind": "explicit bundle" if args.input_bundle else "synthetic",
        "cases": {},
    }
    if "donor" in cases:
        report["donor_age_bands"] = args.donor_age_bands
    if args.input_bundle:
        digest = hashlib.sha256()
        with args.input_bundle.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        report["input_bundle_sha256"] = digest.hexdigest()
    with tempfile.TemporaryDirectory(prefix="eqasim-equivalence-") as temporary:
        for case in cases:
            measurements = {}
            for mode in ("original", "off", "on"):
                output = Path(temporary) / (case + "-" + mode + ".pickle")
                command = [
                    sys.executable, "-B", str(Path(__file__).resolve()), "--mode", mode,
                    "--case", case, "--repository-root", str(reference if mode == "original" else root),
                    "--size", str(args.size), "--repeat", str(args.repeat), "--output", str(output),
                    "--donor-age-bands", args.donor_age_bands,
                ]
                if args.input_bundle:
                    command += ["--input-bundle", str(args.input_bundle)]
                subprocess.run(command, check=True, env={**os.environ, "PYTHONUTF8": "1"})
                with output.open("rb") as stream:
                    measurements[mode] = pickle.load(stream)
            for mode in ("off", "on"):
                assert_exact(measurements[mode]["result"], measurements["original"]["result"])
                assert measurements[mode]["input_rows"] == measurements["original"]["input_rows"]
            medians = {mode: statistics.median(value["seconds"])
                       for mode, value in measurements.items()}
            report["cases"][case] = {
                "original_off_on_exact": True, "median_seconds": medians,
                "input_rows": measurements["original"]["input_rows"],
                "seconds": {mode: value["seconds"] for mode, value in measurements.items()},
                "speedup_original_over_on": medians["original"] / medians["on"],
            }
            print(case, report["cases"][case], flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
