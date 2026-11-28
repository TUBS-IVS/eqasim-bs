"""Run the initial mode choice of the #678 branch on one cut population file and summarise it (B9 step 1).

Mirrors ``braunschweig.matsim.initial_mode_choice.run`` of the branch without synpp: the second config comes from
``mode_choice_reach.write_initial_config(config, ReachSettings(True, 13))``, the runner arguments from
``initial_mode_choice.arguments`` (no excluded subpopulations: the file holds residents only, and the runner fails
when the exclusion list matches nobody), the JVM options from ``matsim.runtime.java.run`` (-Xmx, java.io.tmpdir,
matsim.useLocalDtds; cwd = the run directory). Persons before/after with ``count_persons``, trip changes with
``summarise``. The run directory holds a COPY of the run's final config and symbolic links to the other files of the
run's prepare stage directory (read-only). The population is never replaced; the output stays in mode_choice/.

An optional 8th argument appends ``--config:standaloneModeChoice.chunkSize <n>`` to the branch's arguments. The
runner hands chunks of ``chunkSize`` plans (default 1,000) to its threads, so a file of fewer than 1,000 persons runs
on ONE thread whatever ``numberOfThreads`` says; in the full population the few long-tour persons are spread over
many chunks. A small chunk size restores that spread for the cut files (it changes the chunk random seeds, not the
model).

Usage: python i678_run_initial_choice.py <eqasim-bs clone> <run dir> <population file name> <jar> <java> <threads> <Xmx>
       [chunk size]
"""
from __future__ import annotations

import json
import logging
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

clone, run_dir, population, jar, java_binary, threads, memory = sys.argv[1:8]
chunk_size = sys.argv[8] if len(sys.argv) > 8 else None
sys.path.append(clone)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import pandas as pd  # noqa: E402

from braunschweig.matsim import initial_mode_choice as imc  # noqa: E402
from braunschweig.matsim.simulation import mode_choice_reach  # noqa: E402

CONFIG_NAME = "braunschweig_100pct_prod20261031_config.xml"
RELAXED = re.compile(r"border-tour vehicle legs relaxed for missing availability")


def main() -> int:
    run = Path(run_dir)
    os.chdir(run)
    written = mode_choice_reach.write_initial_config(run / CONFIG_NAME, mode_choice_reach.ReachSettings(True, 13))
    config_file = os.path.relpath(written, run).replace(os.sep, "/")
    persons_before = imc.count_persons(run / population)
    args = imc.arguments(config_file, population, int(threads), ())
    if chunk_size is not None:
        args += ["--config:standaloneModeChoice.chunkSize", str(int(chunk_size))]
    temp = run / "__java_temp"
    temp.mkdir(exist_ok=True)
    command = [java_binary, "-cp", jar, "-Xmx" + memory, f"-Djava.io.tmpdir={temp}", "-Dmatsim.useLocalDtds=true",
               imc.RUNNER] + args
    print("Executing java:", " ".join(command), flush=True)
    started_wall = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    started = time.monotonic()
    with open(run / "java_output.txt", "w", encoding="utf-8") as log:
        code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT, cwd=run)
    wall = time.monotonic() - started
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)  # the JVM is the only child that ran
    print(f"java exit {code}, wall time {wall:.1f} s", flush=True)
    result = {"population": population, "config_file": config_file, "threads": int(threads), "xmx": memory,
              "chunk_size": None if chunk_size is None else int(chunk_size),
              "java_exit": code, "java_wall_time_s": round(wall, 1),
              "java_cpu_user_s": round(usage.ru_utime, 1), "java_cpu_sys_s": round(usage.ru_stime, 1),
              "java_max_rss_kb": usage.ru_maxrss, "started": started_wall,
              "persons_before": persons_before}
    out = run / imc.OUTPUT_DIRECTORY
    if code == 0:
        result["persons_after"] = imc.count_persons(out / imc.OUTPUT_PLANS)
        result["persons_removed"] = persons_before - result["persons_after"]
        before = pd.read_csv(out / imc.INPUT_TRIPS, sep=imc.CSV_DELIMITER, dtype=str)
        after = pd.read_csv(out / imc.OUTPUT_TRIPS, sep=imc.CSV_DELIMITER, dtype=str)
        result["summary"] = imc.summarise(before, after)
    text = (run / "java_output.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    result["relaxed_lines"] = [line for line in text if RELAXED.search(line)]
    result["n_exception_lines"] = sum("Exception" in line for line in text)
    (run / "run_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
