"""Apply the pre-registered bounds of popsim-codex-variants-2026-11-25 to runs.csv.

Usage: evaluate.py <runs.csv>
Checks (bounds exactly as in docs/runs/popsim-codex-variants-2026-11-25.yml):
  1 identity: per batch run, B and C sha256 of the three output files equal those of arm A (same batch, same round)
  2 changed-step time on b028: (median_A - median_X) > (max_A - min_A) over the 3 repeats;
    changed step B = sum of sub_balancing.geography=KREIS, =ZENSUS1km, =ZENSUS100m; C = expand_households
  3 peak RSS on b028: median_X <= 1.05 * median_A
  4 every run exits 0
  verdict per variant: "port" only if all four are met
"""
import csv
import statistics
import sys
from collections import defaultdict

FILES = ("final_expanded_household_ids.csv", "synthetic_households.csv", "synthetic_persons.csv")
SUB = ("sub_balancing.geography=KREIS", "sub_balancing.geography=ZENSUS1km", "sub_balancing.geography=ZENSUS100m")
CHANGED = {"A_vs_B": SUB, "A_vs_C": ("expand_households",)}


def main(path):
    v = {}
    for r in csv.DictReader(open(path)):
        v[(r["batch"], r["arm"], int(r["round"]), r["metric"])] = r["value"]
    runs = sorted({k[:3] for k in v})
    batches = sorted({k[0] for k in v})

    def changed(batch, arm, rnd, step_set):
        return sum(float(v[(batch, arm, rnd, f"step_s:{s}")]) for s in step_set)

    exits_ok = all(v[(*k, "exit_code")] == "0" for k in runs)
    print(f"check 4 exit codes all 0 over {len(runs)} runs: {'MET' if exits_ok else 'NOT MET'}")
    verdict = {}
    for x, label in (("B", "A_vs_B"), ("C", "A_vs_C")):
        step_set = CHANGED[label]
        ident = []
        for b in batches:
            for rnd in sorted({k[2] for k in runs if k[0] == b and k[1] == "A"}):
                eq = all(v[(b, x, rnd, f"sha256:{f}")] == v[(b, "A", rnd, f"sha256:{f}")] for f in FILES)
                ident.append(eq)
                print(f"  identity {x} vs A {b} r{rnd}: {'equal' if eq else 'DIFFERENT'}")
        ident_ok = all(ident)
        rounds28 = sorted({k[2] for k in runs if k[0] == "b028" and k[1] == "A"})
        ta = [changed("b028", "A", r, step_set) for r in rounds28]
        tx = [changed("b028", x, r, step_set) for r in rounds28]
        gain = statistics.median(ta) - statistics.median(tx)
        spread = max(ta) - min(ta)
        time_ok = gain > spread
        ra = statistics.median(float(v[("b028", "A", r, "peak_rss_kb")]) for r in rounds28)
        rx = statistics.median(float(v[("b028", x, r, "peak_rss_kb")]) for r in rounds28)
        rss_ok = rx <= 1.05 * ra
        print(f"{x}: check 1 identity {'MET' if ident_ok else 'NOT MET'}")
        print(f"{x}: check 2 changed step A {['%.2f' % t for t in ta]} s, {x} {['%.2f' % t for t in tx]} s; "
              f"median_A - median_{x} = {gain:.2f} s vs A spread {spread:.2f} s: {'MET' if time_ok else 'NOT MET'}")
        print(f"{x}: check 3 peak RSS median A {ra:.0f} kB, {x} {rx:.0f} kB, ratio {rx / ra:.4f} (bound 1.05): "
              f"{'MET' if rss_ok else 'NOT MET'}")
        verdict[x] = ident_ok and time_ok and rss_ok and exits_ok
    for x in ("B", "C"):
        print(f"verdict {x}: {'port' if verdict[x] else 'not ported'}")


if __name__ == "__main__":
    main(sys.argv[1])
