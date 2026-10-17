"""Prepare the ZONES arm of the direct parking-zone smoke with the real package code.

The arm directory must hold a copy of the OFF arm inputs (isParis stripped, vehicles completed). This script
1. loads the committed zone release through the synpp stage braunschweig.parking.zones_stage (fake context),
2. attaches parkingZone / parkingFree / residentParkingZone with braunschweig.parking.attach (the writer's
   functions) to the activities parsed from the plans file, using the run's random seed,
3. writes the tariff model JSON next to the config with braunschweig.parking.tariff_export and the
   braunschweigParking module into the config with braunschweig.matsim.config_modules, exactly as
   braunschweig.matsim.simulation.prepare does,
4. prints aggregate counts (zones, free-parking draw, residents). Nothing else is written.

It is a smoke harness (scratchpad), not pipeline code: the plans are parsed line by line like
add_parking_attributes.py does; person ids of the June scenario are numeric and are passed as integers so the
draw sorts them like the pipeline's integer ids.
"""
from __future__ import annotations

import argparse
import gzip
import logging
import sys
from pathlib import Path

import add_parking_attributes as apa

PARKING_KEYS = {
    "parking_zones_path": "braunschweig/parking/parking_zones_2026.geojson",
    "parking_tariffs_path": "braunschweig/parking/parking_tariffs_2026.csv",
    "parking_coverage_register_path": "braunschweig/parking/parking_coverage_register_2026.csv",
    "parking_workplace_shares_path": "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv",
}


class FakeContext:
    """The subset of the synpp context the zones stage uses: config values and a stage cache path."""

    def __init__(self, config: dict, cache_path: Path):
        self._config = dict(config)
        self._cache_path = cache_path

    def config(self, key, default=None):
        if key in self._config:
            return self._config[key]
        if default is not None:
            return default
        raise KeyError(f"fake context has no config value for {key!r}")

    def path(self):
        self._cache_path.mkdir(parents=True, exist_ok=True)
        return str(self._cache_path)

    def stage(self, name, *args, **kwargs):
        raise KeyError(f"the zones stage is not expected to depend on {name!r}")

    def progress(self, iterable=None, *args, **kwargs):
        return iterable


def load_release(repo: Path, data_path: Path, cache_path: Path):
    sys.path.insert(0, str(repo))
    from braunschweig.parking import zones_stage
    context = FakeContext({"data_path": str(data_path), **PARKING_KEYS}, cache_path)
    return zones_stage.execute(context)


def attach_attributes(activities, release, random_seed: int, shift: float):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point
    from braunschweig.parking import attach

    rows = [(int(person), index, purpose, x, y)
            for person, acts in activities.items() for index, purpose, x, y in acts]
    frame = pd.DataFrame(rows, columns=["person_id", "activity_index", "purpose", "x", "y"])
    locations = gpd.GeoDataFrame(frame[["person_id", "activity_index"]],
                                 geometry=[Point(x, y) for x, y in zip(frame["x"], frame["y"])], crs="EPSG:25832")
    acts = attach.attach_parking_zones(frame[["person_id", "activity_index", "purpose"]], locations, release["zones"])
    persons = pd.DataFrame({"person_id": sorted(int(person) for person in activities)})
    persons = attach.attach_resident_zones(persons, acts, release["tariffs"])
    acts = attach.draw_parking_free(acts, release["tariffs"], release["workplace_shares"], random_seed, shift=shift)

    person_attrs, activity_attrs = {}, {}
    for row in persons.itertuples(index=False):
        if isinstance(row.resident_parking_zone, str):
            person_attrs[str(row.person_id)] = [("residentParkingZone", "java.lang.String", row.resident_parking_zone)]
    for row in acts.itertuples(index=False):
        extra = []
        if isinstance(row.parking_zone, str):
            extra.append(("parkingZone", "java.lang.String", row.parking_zone))
        if bool(row.parking_free):
            extra.append(("parkingFree", "java.lang.Boolean", "true"))
        if extra:
            activity_attrs[(str(row.person_id), int(row.activity_index))] = extra
    zoned = acts["parking_zone"].notna()
    print(f"[smoke] activities in a zone: {int(zoned.sum())}/{len(acts)} ({100.0 * zoned.mean():.1f} %)")
    print("[smoke] zoned activities by zone:", acts.loc[zoned, "parking_zone"].value_counts().to_dict())
    print(f"[smoke] parkingFree activities: {int(acts['parking_free'].sum())}; "
          f"persons with residentParkingZone: {len(person_attrs)}")
    return person_attrs, activity_attrs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="integration worktree root (package code + committed data)")
    parser.add_argument("--arm-dir", required=True, help="ZONES arm directory (copy of the OFF inputs)")
    parser.add_argument("--prefix", default="braunschweig_cordon_gatecheck_")
    parser.add_argument("--random-seed", type=int, default=1234)
    parser.add_argument("--shift", type=float, default=0.0)
    parser.add_argument("--snapshot-date", default="2026-09-28")
    parser.add_argument("--terminal-stay-rule", default="until_fee_end")
    args = parser.parse_args(argv)
    # INFO shows the rate lines of the package code ([parking] / [parking-zones]), the transparency evidence.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    repo, arm = Path(args.repo).resolve(), Path(args.arm_dir).resolve()
    data_path = repo / "eqasim-data" / "data"

    release = load_release(repo, data_path, arm / "tmp" / "zones_stage_cache")
    print("[smoke] release sources:", [(s["source_id"], s["sha256"][:12]) for s in release["sources"]])

    population = arm / f"{args.prefix}population.xml.gz"
    lines = apa.read_lines(population)
    if any('name="isParis"' in line for line in lines):
        raise SystemExit("the ZONES arm population still carries isParis attributes; start from the OFF copy")
    if any('name="parkingZone"' in line for line in lines):
        raise SystemExit("the ZONES arm population already carries parking attributes; start from a fresh OFF copy")
    activities = apa.first_pass(lines)
    person_attrs, activity_attrs = attach_attributes(activities, release, args.random_seed, args.shift)
    out, counts = apa.rewrite(lines, person_attrs, activity_attrs)
    with gzip.open(population, "wt", encoding="utf-8") as stream:
        stream.writelines(out)
    print("[smoke] attributes written:", counts)

    from braunschweig.matsim import config_modules
    from braunschweig.parking import tariff_export
    model = tariff_export.build_tariff_model(release["tariffs"], snapshot_date=args.snapshot_date,
                                             sources=release["sources"],
                                             terminal_stay_rule=args.terminal_stay_rule)
    tariffs_name = tariff_export.tariff_model_file_name(args.prefix, args.snapshot_date)
    tariff_export.write_tariff_model(arm / tariffs_name, model)
    config_modules.write_module(arm / f"{args.prefix}config.xml", "braunschweigParking", {
        "enabled": "true", "tariffsPath": tariffs_name, "terminalStayRule": args.terminal_stay_rule})
    print(f"[smoke] tariff model {tariffs_name}: {len(model['zones'])} zones; module braunschweigParking written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
