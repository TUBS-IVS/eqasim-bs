"""Collect the numbers of popsim-env-security-update-2026-11-27 into runs.csv.

Usage: collect_runs.py <pulled_dir> <out_csv>
<pulled_dir> mirrors the run root on the run server without the environments and the run copies' data:
  runs/<batch>_<arm>.exit, .time (GNU time -v), .sha256 and runs/<batch>_<arm>/output/populationsim.log,
  runs.load, <arm>.tool.json, <arm>.freeze.txt, <arm>/uv.lock, pytest_<arm>.log and .exit,
  sync_check_<case>_<mode>.exit.
Writes one row per (batch, arm, metric, value). Arm-level rows carry batch "all"; metrics: tool:<key>,
freeze:<package>, lock:<package>, lock_commit:populationsim, pytest:exit, pytest:passed, pytest:failed,
sync_check:<mode>:exit (arm = the case old, new or stale). Run rows: exit_code, wall_s, peak_rss_kb, sha256:<file>,
log:balancer_fast_path_count, log:balancer_fast_path_line, log:batch_draw_count, log:batch_draw_line; load rows
carry batch "all", arm "load". Package names are normalised (lower case, runs of '-', '_', '.' as '-'); the home
directory is written as "~".
"""
import csv
import json
import re
import sys
import tomllib
from pathlib import Path

RUN = re.compile(r"^(b\d{3})_(old|new)$")
BALANCER = "balancer fast path: fast "
BATCH_DRAW = "households drawn in one batch"
HOME = re.compile(r"^/home/[^/]+/")


def normalise(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wall_seconds(text):
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", text)
    secs = 0.0
    for part in (float(x) for x in m.group(1).split(":")):
        secs = secs * 60 + part
    return secs


def log_line(lines, needle):
    """Return (count, text after the logger name of the last matching line or '')."""
    hits = [ln for ln in lines if needle in ln]
    return len(hits), (hits[-1].split(" - ", 2)[-1].strip() if hits else "")


def read_exit(path):
    return path.read_text().split()[1]


def main(src, out):
    src = Path(src)
    rows = []
    for d in sorted(p for p in (src / "runs").iterdir() if p.is_dir() and RUN.match(p.name)):
        batch, arm = RUN.match(d.name).groups()
        base = src / "runs" / d.name
        time_txt = Path(f"{base}.time").read_text()
        rows.append((batch, arm, "exit_code", read_exit(Path(f"{base}.exit"))))
        rows.append((batch, arm, "wall_s", wall_seconds(time_txt)))
        rows.append((batch, arm, "peak_rss_kb",
                     re.search(r"Maximum resident set size \(kbytes\): (\d+)", time_txt).group(1)))
        for line in Path(f"{base}.sha256").read_text().splitlines():
            digest, name = line.split(None, 1)
            rows.append((batch, arm, f"sha256:{Path(name.strip()).name}", digest))
        log_lines = (d / "output" / "populationsim.log").read_text().splitlines()
        for label, needle in (("balancer_fast_path", BALANCER), ("batch_draw", BATCH_DRAW)):
            n, text = log_line(log_lines, needle)
            rows.append((batch, arm, f"log:{label}_count", n))
            rows.append((batch, arm, f"log:{label}_line", text))
    for line in (src / "runs.load").read_text().splitlines():
        name, _, value = line.partition("=")
        rows.append(("all", "load", name, value.strip()))
    for arm in ("old", "new"):
        tool = json.loads((src / f"{arm}.tool.json").read_text())
        for key in sorted(tool):
            rows.append(("all", arm, f"tool:{key}", HOME.sub("~/", str(tool[key]))))
        for line in (src / f"{arm}.freeze.txt").read_text().splitlines():
            if " @ " in line:
                name, _, url = line.partition(" @ ")
                rows.append(("all", arm, f"freeze:{normalise(name.strip())}", "git:" + url.strip().rsplit("@", 1)[-1]))
            elif "==" in line:
                name, _, version = line.partition("==")
                rows.append(("all", arm, f"freeze:{normalise(name.strip())}", version.strip()))
        lock = tomllib.loads((src / arm / "uv.lock").read_text(encoding="utf-8"))
        for package in lock["package"]:
            rows.append(("all", arm, f"lock:{normalise(package['name'])}", package.get("version", "")))
            git = package.get("source", {}).get("git")
            if git:
                rows.append(("all", arm, f"lock_commit:{normalise(package['name'])}", git.rsplit("#", 1)[-1]))
        log = (src / f"pytest_{arm}.log").read_text()
        rows.append(("all", arm, "pytest:exit", read_exit(src / f"pytest_{arm}.exit")))
        rows.append(("all", arm, "pytest:fork_commit", log.splitlines()[0].strip()))
        for word in ("passed", "failed"):
            m = re.findall(rf"(\d+) {word}", log)
            rows.append(("all", arm, f"pytest:{word}", m[-1] if m else "0"))
    for case in ("old", "new", "stale"):
        for mode in ("offline", "online"):
            rows.append(("all", case, f"sync_check:{mode}:exit", read_exit(src / f"sync_check_{case}_{mode}.exit")))
    with open(out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["batch", "arm", "metric", "value"])
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
