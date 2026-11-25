"""Collect the numbers of the #677 variant runs into runs.csv.

Usage: collect_runs.py <pulled_runs_dir> <out_csv>
<pulled_runs_dir> holds, per run <batch>_<arm>_r<round>, the files <run>.time (GNU time -v), <run>.exit,
<run>.sha256 and <run>/output/populationsim.log (pulled from the run server). Writes one row per
(batch, arm, round, metric, value); metrics: exit_code, wall_s, peak_rss_kb, step_s:<step>, sha256:<file>.
"""
import csv
import re
import sys
from pathlib import Path

RUN = re.compile(r"^(b\d{3})_([ABC])_r(\d+)$")
STEP = re.compile(r"completed step '([^']+)' : ([0-9.]+) seconds")


def wall_seconds(text):
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", text)
    parts = [float(p) for p in m.group(1).split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


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
        for step, secs in STEP.findall((d / "output" / "populationsim.log").read_text()):
            rows.append((*key, f"step_s:{step}", secs))
        for line in (src / f"{d.name}.sha256").read_text().splitlines():
            digest, name = line.split(None, 1)
            rows.append((*key, f"sha256:{Path(name.strip()).name}", digest))
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["batch", "arm", "round", "metric", "value"])
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
