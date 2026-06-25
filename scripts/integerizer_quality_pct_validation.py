"""PopulationSim-style controls-validation plot for the integerizer-quality run.

Mirrors https://activitysim.github.io/populationsim/validation.html : per control, the
PERCENTAGE difference between the synthetic population and the control total, with error
bars = standard deviation across the geographic units.

Our controls are enforced at ZENSUS100m, so we report the percentage difference at two
levels (both reusing the reviewed braunschweig.analysis.integerizer_quality.cell_error
realised-vs-target recompute -- no duplicated control logic):

- KREIS aggregate: sum realised / sum target per (control, Kreis) -> pct diff; mean +/- SD
  across the 8 ZGB Kreise. This is the fair "is the synthesis regionally unbiased" view
  (analogous to the REGION/TRACT rows in the reference plot).
- 100m cell: pct diff per cell (target>0); mean +/- SD across cells. Naturally wide
  (a tiny cell can be off by +/-100% on a count of 1) -- shown for completeness.

It is a fit metric vs the run's OWN control inputs (not an external ground truth).
"""
from __future__ import annotations

import argparse
import logging
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.analysis.integerizer_quality import cell_error  # noqa: E402

logger = logging.getLogger(__name__)


def _short(name: str) -> str:
    """Trim the verbose Zensus column names for the plot labels."""
    return (name.replace("_ZENSUS100m", "")
                .replace("_Groesse_des_privaten_Haushalts_100m_Gitter", " [HH-Groesse]")
                .replace("_Typ_priv_HH_Familie_100m_Gitter", " [HH-Typ]")
                .replace("_Tenure_100m_Gitter", " [Tenure]")
                .replace("_agg", "")[:48])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PopulationSim-style controls validation plot")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--mid-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--random-seed", type=int, default=1234)
    ap.add_argument("--tiers", default="tier0,tier1,tier2,tier3")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]

    long = cell_error.cell_error_table(
        args.work_dir, args.mid_dir, random_seed=args.random_seed, tiers=tiers,
        employment_grid=True, weekend=True)

    # (a) cell-level percentage difference (target > 0).
    cdf = long[long["target"] > 0].copy()
    cdf["pct"] = 100.0 * (cdf["realised"] - cdf["target"]) / cdf["target"]
    cell = (cdf.groupby("control")["pct"].agg(cell_mean_pct="mean", cell_sd_pct="std",
                                              n_cells="count"))

    # (b) Kreis-aggregate percentage difference.
    if "KREIS" in long.columns:
        agg = (long.groupby(["control", "KREIS"])
               .agg(r=("realised", "sum"), t=("target", "sum")).reset_index())
        agg = agg[agg["t"] > 0].copy()
        agg["pct"] = 100.0 * (agg["r"] - agg["t"]) / agg["t"]
        kreis = (agg.groupby("control")["pct"].agg(kreis_mean_pct="mean",
                                                   kreis_sd_pct="std", n_kreis="count"))
    else:
        kreis = pd.DataFrame(columns=["kreis_mean_pct", "kreis_sd_pct", "n_kreis"])

    out = cell.join(kreis, how="outer").reset_index().sort_values("control")
    od = os.path.join(args.output_dir, "integerizer_quality")
    os.makedirs(od, exist_ok=True)
    out.to_csv(os.path.join(od, "controls_validation_pct.csv"), index=False)
    logger.info("[validation_pct] wrote controls_validation_pct.csv (%d controls)", len(out))

    # Forest plot: Kreis-aggregate mean +/- SD per control.
    plot = out.dropna(subset=["kreis_mean_pct"]).copy()
    y = np.arange(len(plot))
    fig, ax = plt.subplots(figsize=(11, max(6, 0.32 * len(plot))))
    ax.errorbar(plot["kreis_mean_pct"], y, xerr=plot["kreis_sd_pct"].fillna(0),
                fmt="s", color="#2b6cb0", ecolor="#2b6cb0", capsize=3, markersize=5, lw=1)
    ax.axvline(0, color="black", lw=1)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(c) for c in plot["control"]], fontsize=7)
    ax.set_ylim(-1, len(plot))
    ax.invert_yaxis()
    ax.set_xlabel("Percentage Difference [+/- SDEV] across the 8 ZGB Kreise")
    ax.set_title("ZGB PopulationSim Controls Validation (100m controls, Kreis aggregate)")
    ax.grid(axis="x", ls=":", alpha=0.4)
    plt.tight_layout()
    fig.savefig(os.path.join(od, "controls_validation.png"), dpi=130)
    logger.info("[validation_pct] wrote controls_validation.png")

    # Console summary for quick reading.
    print(out[["control", "kreis_mean_pct", "kreis_sd_pct", "cell_mean_pct",
               "cell_sd_pct"]].round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
