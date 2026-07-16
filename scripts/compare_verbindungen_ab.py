"""Compare two VerBindungen validation summaries (#132 A/B).

A = baseline (work_production_mass: population), B = candidate (svb_wohn).
Prints a markdown delta table; the verdict is a HUMAN decision recorded as an
ADR in docs/DECISIONS.md (no automatic thresholds -- see the spec's stage-3
gate).

Usage::

    python scripts/compare_verbindungen_ab.py \
        --a <run_A>/verbindungen_validation_summary.csv \
        --b <run_B>/verbindungen_validation_summary.csv \
        [--label-a population --label-b svb_wohn] [--output ab_table.md]
"""
from __future__ import annotations

import argparse

import pandas as pd


def _read_summary(path: str) -> pd.Series:
    df = pd.read_csv(path, comment="#")
    return df.set_index("metric")["value"]


def render_comparison(path_a: str, path_b: str,
                      label_a: str = "A", label_b: str = "B") -> str:
    a, b = _read_summary(path_a), _read_summary(path_b)
    idx = a.index.union(b.index)
    lines = [
        f"| metric | {label_a} | {label_b} | delta ({label_b} - {label_a}) |",
        "|---|---|---|---|",
    ]
    for metric in idx:
        va = a.get(metric)
        vb = b.get(metric)
        try:
            delta = f"{float(vb) - float(va):+.4f}"
        except (TypeError, ValueError):
            delta = "n/a"
        lines.append(f"| {metric} | {va} | {vb} | {delta} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--label-a", default="population")
    parser.add_argument("--label-b", default="svb_wohn")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    table = render_comparison(args.a, args.b, args.label_a, args.label_b)
    print(table)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(table + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
