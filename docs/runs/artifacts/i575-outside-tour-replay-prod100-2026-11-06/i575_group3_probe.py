"""#575 group-3 probe: are the unconnected "outside_tour" legs of the B3 probe in tours the outside-tour stage flags?

Read-only. Hypothesis of #575: the frozen pt legs labelled ``outside_tour`` by the B3 long-walk probe
(``b3b_long_walk_probe.py``, ``legs_connection.csv.gz``: label ``outside_tour``, ``check`` False) belong to tours with
every activity inside the extent, which the outside-tour stage therefore never checks; only their routed path leaves
the extent, and the cutter freezes them. The probe recomputes, with the stage's own helpers on the run's cached inputs
of the stage (same stand-in context as the replay script), per such leg:

* ``outside_flag``: the stage's ``outside_tour_flags`` (a destination outside the cordon polygon or an ``outside``
  activity anywhere in the home-based tour);
* ``portal_tour``: the tour contains a portal leg.

Prints counts and writes ``group3_legs.csv.gz`` to ``--out``.

Usage on felix (eqasim env, started in the run's checkout as the replay):
  python i575_group3_probe.py --code ~/i575_replay/eqasim-bs --cache ~/prod100_20261031/cache \
      --run-yml ~/prod100_20261031/run.yml --run-log ~/prod100_20261031/logs/run_20261031T071313.log \
      --legs ~/scratch_b3/out_b2/legs_connection.csv.gz --out ~/scratch_i575/group3
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STAGE = "braunschweig.synthesis.outside_tour_modes.stage"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--code", "--cache", "--run-yml", "--run-log", "--legs", "--out"):
        parser.add_argument(name, required=True)
    args = parser.parse_args()
    code = Path(args.code).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(code))
    spec = importlib.util.spec_from_file_location(
        "replay", code / "docs/runs/artifacts/i573-check-replay-prod100-2026-11-05/i573_check_replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    from braunschweig.synpp_fork import registry  # noqa: PLC0415
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction  # noqa: PLC0415
    from braunschweig.synthesis.outside_tour_modes import stage, tours  # noqa: PLC0415
    from braunschweig.synthesis.portal_trips.rewrite import PORTAL_LEG_COLUMN  # noqa: PLC0415

    settings = registry.load_settings(str(code / "configs" / "base_bs.yml"), str(Path(args.run_yml).expanduser()))
    replay.ReplayContext.aliases = dict(settings.get("aliases", {}) or {})
    stems = replay.stems_before(Path(args.run_log).expanduser(), STAGE)
    stems.pop("__self__")
    context = replay.ReplayContext(config=dict(settings.get("config", {}) or {}), stems=stems,
                                   cache=Path(args.cache).expanduser(), out_dir=out)
    stage.configure(context)
    context.configuring = False

    trips = context.stage(stage.FINAL_TRIPS_STAGE)
    municipalities = context.stage(stage.MUNICIPALITIES_STAGE)
    locations = context.stage(stage.LOCATIONS_STAGE)
    polygon = build_cordon_polygon(
        municipalities, buffer_m_from_fraction(municipalities, float(context.config("cordon_network_buffer_fraction"))))
    ordered = trips.sort_values(["person_id", "trip_index"], kind="mergesort").reset_index(drop=True)
    tour_ids = tours.home_based_tour_ids(ordered)
    endpoints = tours.leg_endpoints(ordered, locations)
    outside = tours.outside_tour_flags(ordered, tour_ids, tours.destination_outside(endpoints, polygon))
    portal_leg = ordered[PORTAL_LEG_COLUMN].eq(True).to_numpy(dtype=bool)
    portal_tour = pd.Series(portal_leg).groupby([ordered["person_id"].to_numpy(), tour_ids]).transform("any")
    keyed = pd.DataFrame({"identifier": ordered["person_id"].astype(str) + ":" + ordered["trip_index"].astype(str),
                          "outside_flag": outside, "portal_tour": portal_tour.to_numpy(dtype=bool),
                          "mode": ordered["mode"].astype(str).to_numpy()})

    legs = pd.read_csv(Path(args.legs).expanduser(), dtype={"identifier": str})
    group = legs[(legs["label"] == "outside_tour") & ~legs["check"].astype(bool)]
    joined = group.merge(keyed, on="identifier", how="left", validate="one_to_one")
    unmatched = int(joined["outside_flag"].isna().sum())
    print(f"unconnected outside_tour legs {len(group)}; not found in trips.final {unmatched}")
    print("outside_flag x portal_tour:")
    print(joined.groupby(["outside_flag", "portal_tour"], dropna=False).size().to_string())
    print("mode in trips.final:", joined["mode"].value_counts(dropna=False).to_dict())
    joined.to_csv(out / "group3_legs.csv.gz", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
