"""Smoke check for braunschweig/analysis/population_fit.ipynb logic.

Runs the data-access portions of the notebook outside Jupyter so the
notebook can be trusted to execute when opened. Not part of pytest:
invoke directly when refreshing the notebook artefacts.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS = Path(__file__).resolve().parents[1] / "braunschweig" / "analysis" / "results"


def main() -> None:
    summary = {
        rate: json.loads((RESULTS / f"{rate}pct" / "report.json").read_text(encoding="utf-8"))
        for rate in (10, 25)
    }
    for rate, s in summary.items():
        t = s["trip_summary"]
        pop_total = next(p for p in s["population"] if p["ars5"] == "TOTAL")
        print(
            f"{rate:>3}%: synth_persons={t['n_persons']:>8,} | "
            f"expanded={int(pop_total['synth_expanded']):>9,} | "
            f"dev={pop_total['deviation_pct']:+5.2f}% | "
            f"R2_OD={s['od_fit']['r2']:.3f} | "
            f"trips/p={t['trips_per_person']:.3f} | "
            f"daily_km={t['daily_distance_km']:.1f}"
        )

    hh_25 = pd.DataFrame(summary[25]["hh_size_per_kreis"])
    print("\nHH-size TVD per Kreis (25%):")
    print(hh_25[["ars5", "kreis_name", "tvd_pp", "chi2", "dof", "n_synth_hh"]].to_string(index=False))

    print("\nRegression guard (25%):")
    for r in summary[25].get("regression_guard", []):
        print(f"  {r['kpi']:<28} {r['value']:>8.3f} (tol {r['tolerance']:>6.2f}) -> {r['status']}")

    # SRMSE summary across controls.
    def srmse(synth, target):
        synth = np.asarray(synth, dtype=float)
        target = np.asarray(target, dtype=float)
        return float(np.sqrt(np.mean((synth - target) ** 2)) / target.mean()) if target.sum() else float("nan")

    print("\nSRMSE summary:")
    for rate, s in summary.items():
        pop_df = pd.DataFrame(s["population"])
        pop_df = pop_df[pop_df["ars5"] != "TOTAL"]
        mode = pd.DataFrame(s["mode_share"])
        purp = pd.DataFrame(s["purpose_mix_remapped"])
        print(
            f"  {rate:>3}%  pop_per_kreis={srmse(pop_df['synth_expanded'], pop_df['zensus_2022']):.4f}  "
            f"mode_share={srmse(mode['synth_share'], mode['mid_share']):.4f}  "
            f"purpose={srmse(purp['synth_share'], purp['mid_share']):.4f}"
        )

    print("\nOK")


if __name__ == "__main__":
    main()
