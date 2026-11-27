"""Wiring run of the post-run emissions stage chain on a real archive (issue #688).

Classification: wiring_proof. NOT a result: the HBEFA tables used here are the MATSim SAMPLE tables,
whose values are deliberately altered. Every emission number this run writes is meaningless and must
never be quoted. This driver therefore prints no emission value; it prints counts, shares of
vehicle-km, the tool's arguments, runtimes and whether files are byte-identical.

The pipeline code refuses the sample tables (braunschweig.emissions.hbefa_tables). The refusal is
switched off HERE, in the driver only. Two passes:

  A  real road-type mapping. The sample tables hold one road-type family only, so the pre-check is
     expected to stop the chain before the Java run. That is the behaviour under test.
  B  every link on the fallback road type (class table emptied HERE, in the driver only; the bound
     is raised to 1.0 by configuration), so the tool can answer every lookup from the sample tables
     and the whole chain runs: hbefa -> inputs -> events (Java) -> summary.

Writes only below ~/felix_emissions/chain_wiring_1pct. The archive is read, never written.
"""
from __future__ import annotations

import filecmp
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path

HOME = Path.home()
REPO = HOME / "felix_emissions" / "repo"
sys.path.insert(0, str(REPO))

from braunschweig.data import hbefa as hbefa_stage  # noqa: E402
from braunschweig.emissions import events as events_stage  # noqa: E402
from braunschweig.emissions import hbefa_tables, road_types  # noqa: E402
from braunschweig.emissions import inputs as inputs_stage  # noqa: E402
from braunschweig.emissions import summary as summary_stage  # noqa: E402
from matsim.runtime import java as java_runtime  # noqa: E402

OUTPUT_PATH = HOME / "eqasim-bs" / "eqasim-data" / "output_bs_1pct_pv3_v3"
RUN_JAR_NAME = "braunschweig_1pct_pv3_v3_run.jar"
SAMPLE_TABLES = HOME / "felix_emissions" / "sample_tables"
WORK = HOME / "felix_emissions" / "chain_wiring_1pct"
EARLIER_TOOL_OUTPUT = HOME / "felix_emissions" / "wiring_1pct" / "out_run2"
EARLIER_VEHICLE_KM = HOME / "felix_emissions" / "wiring_1pct" / "vkm_1pct_pv3_v3.json"
JAVA_BINARY = HOME / "tools" / "jdk-25.0.3+9" / "bin" / "java"

STAGES = {
    "braunschweig.data.hbefa": hbefa_stage,
    "braunschweig.emissions.inputs": inputs_stage,
    "braunschweig.emissions.events": events_stage,
    "braunschweig.emissions.summary": summary_stage,
}


class _Recording:
    """Configure-time context: collects the declared defaults of a stage."""

    def __init__(self):
        self.declared = {}

    def config(self, key, *default, **kwargs):
        self.declared.setdefault(key, default)
        return default[0] if default else None

    def stage(self, name, *args, **kwargs):
        return None


class Context:
    """Execute-time context: declared defaults plus the values of this run; one folder per stage."""

    def __init__(self, root: Path, overrides: dict):
        self.root = root
        self.values = {}
        for stage in STAGES.values():
            recording = _Recording()
            stage.configure(recording)
            self.values.update({key: default[0] for key, default in recording.declared.items() if default})
        self.values.update(overrides)
        self.results = {"matsim.runtime.eqasim": RUN_JAR_NAME, "matsim.runtime.java": None}
        self.current = None

    def config(self, key):
        return self.values[key]

    def stage(self, name):
        return self.results[name]

    def path(self, name=None):
        name = name or self.current
        if name == "matsim.runtime.eqasim":
            return str(OUTPUT_PATH)  # the run jar next to the archive; read only
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def run(self, name):
        self.current = name
        started = time.monotonic()
        self.results[name] = STAGES[name].execute(self)
        return time.monotonic() - started


def base_config() -> dict:
    return {
        "output_path": str(OUTPUT_PATH),
        "sampling_rate": 0.01,
        "java_binary": str(JAVA_BINARY),
        "java_memory": "8G",
        hbefa_stage.KEY_TABLE_PATHS["cold_average"]: str(SAMPLE_TABLES / "sample_41_EFA_ColdStart_vehcat_2020average.csv"),
        hbefa_stage.KEY_TABLE_PATHS["warm_average"]: str(SAMPLE_TABLES / "sample_41_EFA_HOT_vehcat_2020average.csv"),
        hbefa_stage.KEY_TABLE_PATHS["cold_detailed"]: str(SAMPLE_TABLES / "sample_41_EFA_ColdStart_SubSegm_2020detailed.csv"),
        hbefa_stage.KEY_TABLE_PATHS["warm_detailed"]: str(SAMPLE_TABLES / "sample_41_EFA_HOT_SubSegm_2020detailed.csv"),
        inputs_stage.KEY_COMPUTATION_METHOD: "StopAndGoFraction",
        # The sample detailed table holds a handful of sub-segments: nearly every fleet type is
        # answered below it. Accepted on purpose for the wiring run (configuration, not a patch).
        inputs_stage.KEY_MAX_TYPE_FALLBACK: 1.0,
    }


def lookup_counts(report: dict) -> dict:
    lookups = report.get("lookups") or {}
    return {
        key: (len(lookups[key]) if isinstance(lookups.get(key), list) else lookups.get(key))
        for key in ("excluded_types", "emitting_types", "types_expecting_detailed", "primary_types",
                    "fallback_types", "fallback_type_share", "aborting_types")
    }


def road_type_counts(report: dict) -> dict:
    roads = report["road_types"]
    return {
        "drivable_links": roads["drivable_links"],
        "fallback_links": roads["fallback_links"],
        "fallback_length_share": roads["fallback_length_share"],
        "not_drivable_links": roads["not_drivable_links"],
        "distinct_road_types_on_drivable_links": len(roads["length_meters_by_road_type"]),
    }


def pass_a(result: dict) -> None:
    context = Context(WORK / "pass_a_real_road_types", base_config())
    seconds = {"hbefa": context.run("braunschweig.data.hbefa")}
    stopped = None
    started = time.monotonic()
    try:
        context.run("braunschweig.emissions.inputs")
    except ValueError as error:
        stopped = str(error)[:400]
    seconds["inputs"] = time.monotonic() - started
    report = json.loads((context.root / "braunschweig.emissions.inputs" / inputs_stage.REPORT_FILE_NAME).read_text())
    result["pass_a"] = {
        "seconds": seconds,
        "precheck_stopped_the_chain": stopped is not None,
        "stop_message_start": stopped,
        "archive_sample_size": report["archive_sample_size"],
        "road_types": road_type_counts(report),
        "lookups": lookup_counts(report),
        "view": {key: report["view"][key] for key in ("vehicle_types", "transit_vehicle_types", "links")},
        "excluded_types": report["view"]["excluded_types"],
    }


def pass_b(result: dict) -> None:
    # Driver-only: with an empty class table every link gets the fallback road type, the one
    # family the sample tables hold. The committed table and the pipeline code are untouched.
    road_types._load_classes = lambda: {}
    config = base_config()
    config[inputs_stage.KEY_MAX_ROAD_TYPE_FALLBACK] = 1.0
    context = Context(WORK / "pass_b_all_links_on_fallback", config)
    seconds = {}
    for name in STAGES:
        seconds[name] = context.run(name)
    root = context.root
    inputs_report = json.loads((root / "braunschweig.emissions.inputs" / inputs_stage.REPORT_FILE_NAME).read_text())
    events_report = json.loads((root / "braunschweig.emissions.events" / events_stage.REPORT_FILE_NAME).read_text())
    summary = context.results["braunschweig.emissions.summary"]["summary"]

    earlier = json.loads(EARLIER_VEHICLE_KM.read_text())
    earlier_km = {row["group"]: row["vehicle_km"] for row in earlier["rows"]}
    groups = summary["groups"]
    fleet_km = groups["primary"]["vehicle_km_sample"] + groups["fallback"]["vehicle_km_sample"]
    by_choice_km = groups["average_table_by_choice"]["vehicle_km_sample"]
    expected_by_choice = earlier_km["default_car"] + earlier_km["heavy_truck"]
    events_dir = root / "braunschweig.emissions.events"
    identical = {}
    for name in ("emissions_per_vehicle_type.csv", "emissions_per_network_mode.csv", "emissions_total.csv",
                 "emissions_per_link.csv"):
        mine, theirs = events_dir / name, EARLIER_TOOL_OUTPUT / name
        identical[name] = (filecmp.cmp(mine, theirs, shallow=False) if mine.is_file() and theirs.is_file()
                           else "missing")
    result["pass_b"] = {
        "seconds": seconds,
        "tool_arguments": events_report["arguments"],
        "consistency_check": inputs_report["settings"]["consistency_check"],
        "computation_method": inputs_report["settings"]["computation_method"],
        "road_types": road_type_counts(inputs_report),
        "lookups": lookup_counts(inputs_report),
        "tool_output_files": sorted(p.name for p in events_dir.iterdir() if p.is_file()),
        "tool_outputs_byte_identical_to_the_hand_built_wiring_run": identical,
        "pollutants_in_summary": len(summary["pollutants"]),
        "pollutants_without_day_total": summary["pollutants_without_day_total"],
        "day_total_pollutants_without_type_rows": summary["day_total_pollutants_without_type_rows"],
        "pollutants_with_day_total_above_total": summary["pollutants_with_day_total_above_total"],
        "after_24h_share_range": [
            min(e["after_24h_share"] for e in summary["pollutants"].values() if e["after_24h_share"] is not None),
            max(e["after_24h_share"] for e in summary["pollutants"].values() if e["after_24h_share"] is not None),
        ],
        "vehicle_km": {
            "event_lines": [summary["event_lines"], earlier["event_lines"]],
            "fleet_km_sample": [fleet_km, earlier_km["fleet_typed_car"]],
            "average_by_choice_km_sample": [by_choice_km, expected_by_choice],
            "excluded_km_sample": groups["excluded"]["vehicle_km_sample"],
            "unknown_vehicle_link_entries": summary["unknown_vehicle_link_entries"],
            "vehicle_km_after_24h_share": summary["vehicle_km_after_24h_share"],
            "fallback_vehicle_km_share": summary["fallback_vehicle_km_share"],
            "average_table_by_choice_vehicle_km_share": summary["average_table_by_choice_vehicle_km_share"],
            "types_driving_without_emissions": len(summary["types_driving_without_emissions"]),
            "max_relative_difference_to_the_earlier_measurement": max(
                abs(fleet_km - earlier_km["fleet_typed_car"]) / earlier_km["fleet_typed_car"],
                abs(by_choice_km - expected_by_choice) / expected_by_choice,
            ),
        },
    }


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    (WORK / "WIRING_PROOF_README.txt").write_text(
        "wiring_proof (issue #688): computed with the MATSim SAMPLE tables, whose values are altered on\n"
        "purpose. Every emission number below this folder is meaningless. Never quote one.\n", encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(WORK / "stages.log", encoding="utf-8")])
    # Driver-only: the pipeline refuses the MATSim sample tables by name and by content hash.
    hbefa_tables.SAMPLE_NAME_PREFIX = "\x00never-a-file-name-prefix"
    hbefa_tables.SAMPLE_TABLE_SHA256 = frozenset()
    recording = _Recording()
    java_runtime.configure(recording)
    result = {"classification": "wiring_proof", "archive": str(OUTPUT_PATH / "matsim_output"),
              "java_defaults_declared": sorted(recording.declared)}
    for name, step in (("pass_a", pass_a), ("pass_b", pass_b)):
        try:
            step(result)
        except Exception:  # noqa: BLE001 - the driver reports the failure of a pass and goes on
            result[name + "_failed"] = traceback.format_exc()[-3000:]
    (WORK / "wiring_result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print("WIRING_RESULT_BEGIN")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("WIRING_RESULT_END")


if __name__ == "__main__":
    main()
