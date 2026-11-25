"""Apply the pre-registered bounds of popsim-bs3-release-2026-11-25 to runs.csv.

Usage: evaluate.py <runs.csv> <variants_runs.csv> <run_performance_verdict.json>
  <variants_runs.csv> = docs/runs/artifacts/popsim-codex-variants-2026-11-25/runs.csv (arm A hashes)
  <run_performance_verdict.json> = docs/runs/artifacts/popsim-run-performance-2026-11-18/run_performance_verdict.json
Checks (bounds exactly as in docs/runs/popsim-bs3-release-2026-11-25.yml):
  1 identity: per batch, sha256 of the three output files equal for rc_on, rc_off and bs2_on
    (all repeats of bs2_on and rc_on on batch_028 included)
  2 reference: these hashes equal the controlled-rounding hashes of 0.10.0+bs.2 (verdict json, identity_controlled_*)
    and, for batch_000 and batch_028, arm A of the variants runs.csv
  3 every run exits 0
  4 every rc_on run has the balancer fast-path summary line and the batch-draw line (counts printed; the
    balancer line must report fast N of N calls and no fallback or re-run)
  5 peak RSS on batch_028: median rc_on <= 1.05 * median bs2_on
  6 reported: batch_028 wall time, sum of the three sub_balancing steps and expand_households, per repeat;
    "faster" only if (median_bs2_on - median_rc_on) > (max_bs2_on - min_bs2_on)
"""
import csv
import json
import re
import statistics
import sys

FILES = ("final_expanded_household_ids.csv", "synthetic_households.csv", "synthetic_persons.csv")
SUB = ("sub_balancing.geography=KREIS", "sub_balancing.geography=ZENSUS1km", "sub_balancing.geography=ZENSUS100m")
QUANTITIES = {
    "wall_s": ("wall_s",),
    "sub_balancing_sum_s": tuple(f"step_s:{s}" for s in SUB),
    "expand_households_s": ("step_s:expand_households",),
}
BALANCER = re.compile(
    r"balancer fast path: fast (\d+) of (\d+) calls .*fallback value cap (\d+), fallback SVML (\d+), "
    r"fallback non-finite (\d+), re-run after the delta guard (\d+)"
)
VERDICT_KEY = {"b000": "batch_000", "b032": "batch_032", "b028": "batch_028"}


def main(path, variants_path, verdict_path):
    v = {}
    for r in csv.DictReader(open(path, newline="")):
        v[(r["batch"], r["arm"], int(r["round"]), r["metric"])] = r["value"]
    runs = sorted({k[:3] for k in v})
    result = {}

    # check 3
    bad = [k for k in runs if v[(*k, "exit_code")] != "0"]
    result[3] = not bad
    print(f"check 3 exit codes: {len(runs)} runs, non-zero {bad or 'none'}: {'MET' if not bad else 'NOT MET'}")

    # check 1
    ok1 = True
    for b in sorted({k[0] for k in runs}):
        hashes = {}
        for k in (r for r in runs if r[0] == b):
            hashes[k] = tuple(v[(*k, f"sha256:{f}")] for f in FILES)
        distinct = set(hashes.values())
        arms = sorted({(k[1], k[2]) for k in hashes})
        eq = len(distinct) == 1
        ok1 &= eq
        print(f"  identity {b}: {len(hashes)} runs {arms}: {'all equal' if eq else 'DIFFERENT (%d distinct)' % len(distinct)}")
    result[1] = ok1
    print(f"check 1 identity rc_on = rc_off = bs2_on per batch: {'MET' if ok1 else 'NOT MET'}")

    # check 2
    verdict = json.load(open(verdict_path))["checks"]
    var = {}
    for r in csv.DictReader(open(variants_path, newline="")):
        if r["arm"] == "A" and r["metric"].startswith("sha256:"):
            var.setdefault((r["batch"], r["metric"].split(":", 1)[1]), set()).add(r["value"])
    ok2 = True
    for b in sorted({k[0] for k in runs}):
        ref = verdict[f"identity_controlled_{VERDICT_KEY[b]}"]["sha256_on"]
        for k in (r for r in runs if r[0] == b):
            for f in FILES:
                match = v[(*k, f"sha256:{f}")] == ref[f]
                ok2 &= match
                if not match:
                    print(f"  reference MISMATCH {k} {f}")
        for f in FILES:
            vs = var.get((b, f))
            if vs is not None:
                match = vs == {ref[f]}
                ok2 &= match
                print(f"  variants arm A {b} {f}: {'equal' if match else 'DIFFERENT'} to the released-tool hash")
    result[2] = ok2
    print(f"check 2 equal to the released 0.10.0+bs.2 hashes: {'MET' if ok2 else 'NOT MET'}")

    # check 4
    ok4 = True
    for k in (r for r in runs if r[1] == "rc_on"):
        nb, nd = int(v[(*k, "log:balancer_fast_path_count")]), int(v[(*k, "log:batch_draw_count")])
        m = BALANCER.search(v[(*k, "log:balancer_fast_path_line")])
        clean = bool(m) and m.group(1) == m.group(2) and all(int(g) == 0 for g in m.groups()[2:])
        good = nb >= 1 and nd == 1 and clean
        ok4 &= good
        print(f"  {k[0]} r{k[2]} rc_on: balancer lines {nb}, batch-draw lines {nd}; "
              f"'{v[(*k, 'log:balancer_fast_path_line')][:80]}' | '{v[(*k, 'log:batch_draw_line')][:75]}'")
    for k in (r for r in runs if r[1] != "rc_on"):
        print(f"  (info) {k[0]} {k[1]} r{k[2]}: balancer lines {v[(*k, 'log:balancer_fast_path_count')]}, "
              f"batch-draw lines {v[(*k, 'log:batch_draw_count')]}")
    result[4] = ok4
    print(f"check 4 fast-path summary and batch-draw line in every rc_on run: {'MET' if ok4 else 'NOT MET'}")

    # check 5 and report 6 on batch_028
    rounds = sorted({k[2] for k in runs if k[0] == "b028" and k[1] == "bs2_on"})
    ra = statistics.median(float(v[("b028", "bs2_on", r, "peak_rss_kb")]) for r in rounds)
    rx = statistics.median(float(v[("b028", "rc_on", r, "peak_rss_kb")]) for r in rounds)
    result[5] = rx <= 1.05 * ra
    print(f"check 5 peak RSS median bs2_on {ra:.0f} kB, rc_on {rx:.0f} kB, ratio {rx / ra:.4f} (bound 1.05): "
          f"{'MET' if result[5] else 'NOT MET'}")
    print(f"report 6 batch_028 timing, repeats {rounds}")
    for name, keys in QUANTITIES.items():
        ta = [sum(float(v[("b028", "bs2_on", r, k)]) for k in keys) for r in rounds]
        tx = [sum(float(v[("b028", "rc_on", r, k)]) for k in keys) for r in rounds]
        gain = statistics.median(ta) - statistics.median(tx)
        spread = max(ta) - min(ta)
        print(f"  {name}: bs2_on {['%.2f' % t for t in ta]}, rc_on {['%.2f' % t for t in tx]}; "
              f"median_bs2_on - median_rc_on = {gain:.2f} s, bs2_on spread = {spread:.2f} s: "
              f"{'faster' if gain > spread else 'no statement of faster'}")
    print("verdict: " + ("all checks 1-5 met" if all(result.values()) else "NOT all checks met: " +
                         str([k for k, ok in result.items() if not ok])))


if __name__ == "__main__":
    main(*sys.argv[1:4])
