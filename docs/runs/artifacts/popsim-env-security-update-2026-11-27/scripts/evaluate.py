"""Apply the pre-registered bounds of popsim-env-security-update-2026-11-27 to runs.csv.

Usage: evaluate.py <runs.csv> <bs3_runs.csv>
  <bs3_runs.csv> = docs/runs/artifacts/popsim-bs3-release-2026-11-25/runs.csv (rows of arm rc2_on = release 0.10.0+bs.3)
Checks (bounds exactly as in docs/runs/popsim-env-security-update-2026-11-27.yml):
  1 identity: per batch the sha256 of the three output files of arm new equal those of arm old
  2 reference: the hashes of arm old equal the rc2_on rows of the bs3 runs.csv
  3 every run exits 0 (6 of 6)
  4 every run has exactly one balancer summary line (fast N of N, every fallback and re-run count 0) and exactly one
    batch-draw line
  5 each arm ran its own environment: imported populationsim under ~/seclock-2026-11-27/<arm>/.venv/, installed commit
    = the commit the arm's lock pins, freeze version = lock version for every package of the lock
  6 fork balancer tests: arm new 0 failed and at least 1 passed, exit 0 (arm old reported)
  7 start check: offline exit 0 for old and new, non-zero for stale (online exits reported)
Prints one line per check with MET or NOT MET; exit code 1 if any check is NOT MET.
"""
import csv
import re
import sys

FILES = ("final_expanded_household_ids.csv", "synthetic_households.csv", "synthetic_persons.csv")
BATCHES = ("b000", "b032", "b028")
ARMS = ("old", "new")
BALANCER = re.compile(
    r"balancer fast path: fast (\d+) of (\d+) calls .*fallback value cap (\d+), fallback SVML (\d+), "
    r"fallback non-finite (\d+), re-run after the delta guard (\d+)"
)
PROJECT = "eqasim-bs-populationsim"  # the virtual uv project itself; it is never installed


def main(path, bs3_path):
    v = {(r["batch"], r["arm"], r["metric"]): r["value"] for r in csv.DictReader(open(path, newline=""))}
    ref = {(r["batch"], r["metric"]): r["value"] for r in csv.DictReader(open(bs3_path, newline=""))
           if r["arm"] == "rc2_on"}
    result = {}

    def report(number, ok, text):
        result[number] = ok
        print(f"check {number} {text}: {'MET' if ok else 'NOT MET'}")

    ok = True
    for b in BATCHES:
        for f in FILES:
            equal = v[(b, "old", f"sha256:{f}")] == v[(b, "new", f"sha256:{f}")]
            ok &= equal
            print(f"  {b} {f}: old {v[(b, 'old', f'sha256:{f}')][:12]} new {v[(b, 'new', f'sha256:{f}')][:12]} "
                  f"{'equal' if equal else 'DIFFERENT'}")
    report(1, ok, "identity new = old, 9 pairs")

    ok = True
    for b in BATCHES:
        for f in FILES:
            equal = v[(b, "old", f"sha256:{f}")] == ref[(b, f"sha256:{f}")]
            ok &= equal
            if not equal:
                print(f"  reference MISMATCH {b} {f}")
    report(2, ok, "arm old equals release 0.10.0+bs.3 (rc2_on)")

    bad = [(b, a) for b in BATCHES for a in ARMS if v[(b, a, "exit_code")] != "0"]
    report(3, not bad, f"exit codes 0, non-zero {bad or 'none'}")

    ok = True
    for b in BATCHES:
        for a in ARMS:
            nb, nd = int(v[(b, a, "log:balancer_fast_path_count")]), int(v[(b, a, "log:batch_draw_count")])
            m = BALANCER.search(v[(b, a, "log:balancer_fast_path_line")])
            clean = bool(m) and m.group(1) == m.group(2) and all(int(g) == 0 for g in m.groups()[2:])
            good = nb == 1 and nd == 1 and clean
            ok &= good
            print(f"  {b} {a}: balancer lines {nb}, batch-draw lines {nd}, clean {clean}; "
                  f"'{v[(b, a, 'log:balancer_fast_path_line')][:70]}' | '{v[(b, a, 'log:batch_draw_line')][:50]}'")
    report(4, ok, "one clean balancer line and one batch-draw line per run")

    ok = True
    for a in ARMS:
        pyfile = v[("all", a, "tool:populationsim_file")]
        in_venv = pyfile.startswith(f"~/seclock-2026-11-27/{a}/.venv/")
        commit_ok = v[("all", a, "tool:commit")] == v[("all", a, "lock_commit:populationsim")]
        lock = {k[2].split(":", 1)[1]: val for k, val in v.items() if k[:2] == ("all", a) and k[2].startswith("lock:")}
        freeze = {k[2].split(":", 1)[1]: val for k, val in v.items()
                  if k[:2] == ("all", a) and k[2].startswith("freeze:")}
        mismatched = []
        for name, version in sorted(lock.items()):
            if name == PROJECT:
                continue
            expected = "git:" + v[("all", a, "lock_commit:populationsim")] if name == "populationsim" else version
            if freeze.get(name) != expected:
                mismatched.append((name, version, freeze.get(name)))
        extra = sorted(set(freeze) - set(lock))
        good = in_venv and commit_ok and not mismatched
        ok &= good
        print(f"  {a}: populationsim file {pyfile} (in own .venv: {in_venv}); commit equals lock: {commit_ok}; "
              f"{len(lock) - 1} locked packages, mismatched {mismatched or 'none'}; installed but not locked "
              f"{extra or 'none'}")
    report(5, ok, "each arm ran its own environment")

    for a in ARMS:
        print(f"  {a}: fork commit {v[('all', a, 'pytest:fork_commit')]}, exit {v[('all', a, 'pytest:exit')]}, "
              f"passed {v[('all', a, 'pytest:passed')]}, failed {v[('all', a, 'pytest:failed')]}")
    report(6, v[("all", "new", "pytest:exit")] == "0" and v[("all", "new", "pytest:failed")] == "0"
           and int(v[("all", "new", "pytest:passed")]) >= 1, "fork balancer tests pass in arm new")

    exits = {(c, m): v[("all", c, f"sync_check:{m}:exit")] for c in ("old", "new", "stale")
             for m in ("offline", "online")}
    print(f"  start check exits {exits}")
    report(7, exits[("old", "offline")] == "0" and exits[("new", "offline")] == "0"
           and exits[("stale", "offline")] != "0", "start check: 0 for old and new, non-zero for stale")

    for b in BATCHES:
        for a in ARMS:
            print(f"  (reported) {b} {a}: wall {float(v[(b, a, 'wall_s')]):.1f} s, peak RSS {v[(b, a, 'peak_rss_kb')]} kB")
    print("  (reported) load: " + "; ".join(f"{k[2]}={val}" for k, val in v.items() if k[1] == "load"))
    failed = [n for n, good in sorted(result.items()) if not good]
    print("verdict: " + ("all checks met" if not failed else f"NOT all checks met: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
