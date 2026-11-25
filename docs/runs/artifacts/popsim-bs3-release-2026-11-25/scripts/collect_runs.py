"""Collect the numbers of the 0.10.0+bs.3 release-candidate runs (issue #677) into runs.csv.

Usage: collect_runs.py <pulled_runs_dir> <out_csv>
<pulled_runs_dir> holds, per run <batch>_<arm>_r<round>, the files <run>.time (GNU time -v), <run>.exit,
<run>.sha256, <run>.pyfile and <run>/output/populationsim.log, and per round <batch>_r<round>.load (all pulled
from the run server). Writes one row per (batch, arm, round, metric, value); metrics: exit_code, wall_s,
peak_rss_kb, step_s:<step>, sha256:<file>, pyfile, log:balancer_fast_path_count, log:balancer_fast_path_line,
log:batch_draw_count, log:batch_draw_line, load:uptime, load:foreign_procs, load:mem_available_gb, load:waited_s.
"""
import csv
import re
import sys
from pathlib import Path

RUN = re.compile(r"^(b\d{3})_(bs2_on|rc_on|rc_off|rc2_on)_r(\d+)$")
STEP = re.compile(r"completed step '([^']+)' : ([0-9.]+) seconds")
BALANCER = "balancer fast path: fast "
BATCH_DRAW = "households drawn in one batch"


def wall_seconds(text):
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", text)
    secs = 0.0
    for p in (float(x) for x in m.group(1).split(":")):
        secs = secs * 60 + p
    return secs


def log_line(lines, needle):
    """Return (count, text after the logger name of the last matching line or '')."""
    hits = [ln for ln in lines if needle in ln]
    return len(hits), (hits[-1].split(" - ", 2)[-1].strip() if hits else "")


def main(src, out):
    src = Path(src)
    rows = []
    for d in sorted(p for p in src.iterdir() if p.is_dir() and RUN.match(p.name)):
        batch, arm, rnd = RUN.match(d.name).groups()
        key = (batch, arm, int(rnd))
        time_txt = (src / f"{d.name}.time").read_text()
        rows.append((*key, "exit_code", (src / f"{d.name}.exit").read_text().split()[1]))
        rows.append((*key, "wall_s", wall_seconds(time_txt)))
        rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", time_txt).group(1)
        rows.append((*key, "peak_rss_kb", rss))
        log_lines = (d / "output" / "populationsim.log").read_text().splitlines()
        for step, secs in STEP.findall("\n".join(log_lines)):
            rows.append((*key, f"step_s:{step}", secs))
        for line in (src / f"{d.name}.sha256").read_text().splitlines():
            digest, name = line.split(None, 1)
            rows.append((*key, f"sha256:{Path(name.strip()).name}", digest))
        # the user's home directory is written as "~" (no user name in tracked files)
        pyfile = re.sub(r"^/home/[^/]+/", "~/", (src / f"{d.name}.pyfile").read_text().strip())
        rows.append((*key, "pyfile", pyfile))
        for label, needle in (("balancer_fast_path", BALANCER), ("batch_draw", BATCH_DRAW)):
            n, text = log_line(log_lines, needle)
            rows.append((*key, f"log:{label}_count", n))
            rows.append((*key, f"log:{label}_line", text))
        load = src / f"{batch}_r{rnd}.load"
        for line in load.read_text().splitlines():
            name, _, value = line.partition("=")
            if name in ("uptime", "foreign_procs", "mem_available_gb", "waited_s"):
                rows.append((*key, f"load:{name}", value.strip()))
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["batch", "arm", "round", "metric", "value"])
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
