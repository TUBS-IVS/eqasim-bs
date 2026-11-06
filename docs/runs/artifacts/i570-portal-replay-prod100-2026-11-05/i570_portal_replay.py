"""#570 replay: the portal stage of prod100_20261031 re-run with the branch code on the run's own cached inputs.

Read-only on the run directory. Loads the inputs the portal stage loaded in prod100_20261031 (cache stems from the
B2 probe, run log lines 41971-41982), runs ``build_portal_trips`` of the GIVEN code (the #570 branch) end to end with
the real batch pt router, and compares its report with the run's own ``portal_trips_report.json``.

Replay-only stand-in: the run's trips were built before the home-office splice carried ``is_synthetic_closure``
(commit e8678cb4), so spliced rows hold NaN there. For this replay those rows take the flag from the donor
``trip_key`` suffix ``_closure`` (the plan repair's marker, ADR-0108); the count is logged. Production does not need
this: the splice now carries the flag.

Usage on felix (eqasim env):
  PYTHONDONTWRITEBYTECODE=1 nice python i570_portal_replay.py --code ~/i570_smoke/eqasim-bs \
      --cache ~/prod100_20261031/cache --run-yml ~/prod100_20261031/run.yml --out ~/scratch_i570/out
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import subprocess
import sys
import time
from pathlib import Path

STEMS = {
    "trips": "braunschweig.synthesis.commute_day.trips_day_stage__0c7a9ee97d2d8ef0a5958a8a51b3c689",
    "municipalities": "data.spatial.municipalities__c04db1943ae15b4681d256f4d6eee421",
    "home": "braunschweig.synthesis.locations.home_cell__7d76184709d2b6fcaf0735a52d801a42",
    "cordon_gates": "braunschweig.synthesis.cordon_gates__11eceeb40f1c607cc7669833cb38e93c",
    "cordon_network": "braunschweig.data.cordon_network__4dce13c683a6cc2017996fd709fa2c6e",
    "external_points": "braunschweig.data.external_secondary_points__c04db1943ae15b4681d256f4d6eee421",
    "supply": "matsim.scenario.supply.processed__4dce13c683a6cc2017996fd709fa2c6e",
    "primary": "braunschweig.locations.synthesis.replacement_education_gravity__73fbbf444ee75cf740778dff43e520ff",
    "persons": "braunschweig.popsim.enriched_adapter__451bf968c790012e5fcf2ace9efddefe",
    "eqasim": "matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f",
}
PORTAL_STEM = "braunschweig.synthesis.portal_trips.stage__3bf655a528d6076561ce70276e1f8d1a"
CLOSURE = "is_synthetic_closure"
logger = logging.getLogger("i570_portal_replay")


def load(cache: Path, key: str):
    path = cache / (STEMS[key] + ".p")
    t0 = time.time()
    with open(path, "rb") as handle:
        value = pickle.load(handle)
    logger.info("loaded %s in %.0f s", key, time.time() - t0)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--run-yml", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--java-memory", default="32g")
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(out / "replay.log"), logging.StreamHandler()])
    code, cache = Path(args.code).expanduser(), Path(args.cache).expanduser()
    sys.path.insert(0, str(code))

    import numpy as np
    import pandas as pd

    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction
    from braunschweig.data.transit.connections import TASK_COLUMNS
    from braunschweig.data.transit.schedule import read_schedule
    from braunschweig.synpp_fork import registry
    from braunschweig.synthesis.portal_trips import config_keys as K
    from braunschweig.synthesis.portal_trips import stage as S

    settings = registry.load_settings(str(code / "configs" / "base_bs.yml"), str(Path(args.run_yml).expanduser()))
    config = settings.get("config", {}) or {}

    def cfg(key, default):
        return config.get(key, default)

    jar = cache / (STEMS["eqasim"] + ".cache") / load(cache, "eqasim")
    router_config = cache / (PORTAL_STEM + ".cache") / "pt_router_config.xml"
    java = cfg("java_binary", "java") or "java"
    calls = {"n": 0}

    def router(tasks: pd.DataFrame):
        calls["n"] += 1
        work = out / f"router_{calls['n']:03d}"
        work.mkdir()
        tasks[list(TASK_COLUMNS)].to_csv(work / "router_tasks.csv", index=False)
        command = [java, f"-Xmx{args.java_memory}", "-cp", str(jar),
                   "org.eqasim.core.tools.routing.RunBatchPublicTransportRouter",
                   "--config-path", str(router_config), "--input-path", str(work / "router_tasks.csv"),
                   "--output-trips-path", str(work / "router_trips.csv"),
                   "--output-legs-path", str(work / "router_legs.csv"), "--threads", str(args.threads)]
        with open(work / "java.log", "w") as handle:
            subprocess.run(command, check=True, cwd=work, stdout=handle, stderr=subprocess.STDOUT)
        trips = pd.read_csv(work / "router_trips.csv", dtype={"identifier": str})
        legs = pd.read_csv(work / "router_legs.csv", dtype={"identifier": str, "access_stop_id": str,
                                                          "egress_stop_id": str}, keep_default_na=False, na_values=[])
        return trips, legs

    trips = load(cache, "trips")
    flag = trips[CLOSURE]
    missing = flag.isna().to_numpy()
    from_key = trips["trip_key"].astype(str).str.endswith("_closure").to_numpy()
    logger.info("replay stand-in: %d/%d trips without a flag (spliced home-office rows); of them %d marked closure by "
                "trip_key; flagged closures already present %d", int(missing.sum()), len(trips),
                int((missing & from_key).sum()), int(flag.fillna(False).astype(bool).sum()))
    trips[CLOSURE] = np.where(missing, from_key, flag.fillna(False).astype(bool)).astype(bool)

    muni = load(cache, "municipalities")
    polygon = build_cordon_polygon(muni, buffer_m_from_fraction(muni, float(cfg("cordon_network_buffer_fraction",
                                                                                     0.10))))
    supply = load(cache, "supply")
    schedule = read_schedule(str(cache / (STEMS["supply"] + ".cache") / supply["schedule_path"]))
    df_work, df_education = load(cache, "primary")
    gate_settings = {"n_candidates": int(cfg(K.KEY_GATE_CANDIDATES, K.DEFAULT_GATE_CANDIDATES)),
                     "service_window_s": float(cfg(K.KEY_GATE_SERVICE_WINDOW_S, K.DEFAULT_GATE_SERVICE_WINDOW_S)),
                     "shift_window_s": float(cfg(K.KEY_SHIFT_WINDOW_S, K.DEFAULT_SHIFT_WINDOW_S)),
                     "shift_step_s": float(cfg(K.KEY_SHIFT_STEP_S, K.DEFAULT_SHIFT_STEP_S)),
                     "min_stay_s": float(cfg(K.KEY_MIN_STAY_S, K.DEFAULT_MIN_STAY_S)),
                     "no_connection_warn_share": float(cfg(K.KEY_NO_CONNECTION_WARN_SHARE,
                                                           K.DEFAULT_NO_CONNECTION_WARN_SHARE)),
                     "shift_return_earlier": bool(cfg(K.KEY_SHIFT_RETURN_EARLIER, K.DEFAULT_SHIFT_RETURN_EARLIER)),
                     "no_connection_last_resort": cfg(K.KEY_NO_CONNECTION_LAST_RESORT,
                                                      K.DEFAULT_NO_CONNECTION_LAST_RESORT)}
    t0 = time.time()
    result = S.build_portal_trips(
        trips=trips, persons=load(cache, "persons"), df_home=load(cache, "home"), df_work=df_work,
        df_education=df_education, gates=load(cache, "cordon_gates")["gates"], links=load(cache, "cordon_network"),
        external_points=load(cache, "external_points"), schedule=schedule, router=router,
        pt_gate_modes=cfg(K.KEY_PT_GATE_MODES, K.DEFAULT_PT_GATE_MODES), gate_settings=gate_settings,
        soft_mode_max_distance_m={"walk": float(cfg(K.KEY_WALK_MAX_DISTANCE_M, K.DEFAULT_WALK_MAX_DISTANCE_M)),
                                  "bicycle": float(cfg(K.KEY_BICYCLE_MAX_DISTANCE_M,
                                                       K.DEFAULT_BICYCLE_MAX_DISTANCE_M))},
        cordon_polygon=polygon,
        threshold_m=float(cfg(K.KEY_MAX_ROUTABLE_DISTANCE_M, K.DEFAULT_MAX_ROUTABLE_DISTANCE_M)),
        tolerance=float(cfg(K.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE, K.DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE)),
        warn_share=float(cfg(K.KEY_MODE_SUBSTITUTION_WARN_SHARE, K.DEFAULT_MODE_SUBSTITUTION_WARN_SHARE)),
        fallback_warn_share=float(cfg(K.KEY_FALLBACK_WARN_SHARE, K.DEFAULT_FALLBACK_WARN_SHARE)),
        seed=int(cfg("random_seed", None)))
    logger.info("build_portal_trips finished in %.0f s, %d router calls", time.time() - t0, calls["n"])

    after = result["report"]
    with open(cache / (PORTAL_STEM + ".cache") / S.REPORT_FILE_NAME) as handle:
        before = json.load(handle)
    with open(out / "report_after.json", "w") as handle:
        json.dump(after, handle, indent=2, default=S._json_default)

    out_trips = result["trips"]
    last = out_trips.groupby("person_id").tail(1)
    ends_outside = last["following_purpose"].eq("outside")
    checks = {
        "persons_ending_outside": int(ends_outside.sum()),
        "max_arrival_h": float(out_trips["arrival_time"].max() / 3600.0),
        "negative_trip_duration": int((out_trips["arrival_time"] < out_trips["departure_time"]).sum()),
        "closure_rows_left_after_outside": int(
            (out_trips[CLOSURE].astype(bool) & out_trips["preceding_purpose"].eq("outside")).sum()),
    }
    keys = ["n_stays", "n_stays_without_return", "n_stays_closure_return_dropped", "n_pt_stays", "pt_gate_reasons",
            "no_connection_actions", "n_stays_by_mode", "n_stays_by_kind", "n_mode_substituted"]
    summary = {"before_prod100_20261031": {k: before.get(k) for k in keys},
               "after_i570_replay": {k: after.get(k) for k in keys}, "checks_after": checks}
    with open(out / "summary.json", "w") as handle:
        json.dump(summary, handle, indent=2, default=S._json_default)
    logger.info("summary: %s", json.dumps(summary, default=S._json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
