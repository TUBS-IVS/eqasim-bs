"""Detailed fleet evaluation: brand mix, age×income, powertrain, fuel consistency.

Produces:
  * ``fleet_overview.png``  -- 4-panel chart (brands, age×income, powertrain, consistency)
  * ``fleet_summary.csv``   -- tabular summary (brand counts/shares, powertrain mix,
                               contradiction count, top-fingerprint share, age×status table)

Data-absent-safe throughout: returns ``{}`` + logs WARNING when vehicles frame is
None, empty, or missing required columns. Never raises; all errors are logged.

Read-only analysis; never modifies synthesis outputs.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd

log = logging.getLogger("braunschweig.analysis.population_validation.fleet_evaluation")

#: Columns that must be present for any output to be produced.
REQUIRED_COLS = {"brand", "powertrain", "age", "economic_status", "segment", "fuel_detail"}

#: Canonical status ordering, low -> high (mirrors fleet_age_status.STATUS_ORDER).
_STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")

#: powertrain (lowercase) -> substring(s) that must appear in fuel_detail (German) to
#: be considered consistent.  All comparisons are done case-insensitively.
_VALID_FUEL_SUBSTR: dict[str, tuple[str, ...]] = {
    "petrol": ("benzin",),
    "diesel": ("diesel",),
    "gas": ("autogas", "benzin/autogas"),
    "bev": ("elektro",),
    "phev": ("plug-in", "elektro"),
    "hybrid": ("elektro",),
    "hydrogen": ("wasserstoff",),
    "other": (),   # no check for "other"
}


def _count_contradictions(df: pd.DataFrame) -> int:
    """Count rows where fuel_detail is inconsistent with powertrain.

    A contradiction is defined as: the fuel_detail string does NOT contain any of
    the expected substrings for its powertrain (case-insensitive).  Powertrains not
    in ``_VALID_FUEL_SUBSTR`` are skipped (treated as no contradiction).
    """
    total = 0
    fd = df["fuel_detail"].astype(str).str.lower()
    pt = df["powertrain"].astype(str).str.lower()

    for powertrain, substrings in _VALID_FUEL_SUBSTR.items():
        if not substrings:
            continue
        mask_pt = pt == powertrain
        if not mask_pt.any():
            continue
        fuel_ok = fd[mask_pt].apply(
            lambda f: any(s in f for s in substrings)
        )
        total += int((~fuel_ok).sum())
    return total


def build_fleet_evaluation(vehicles, out_dir: Path, data_path) -> dict:
    """Produce fleet evaluation charts + summary CSV.

    Parameters
    ----------
    vehicles:
        The synthetic vehicles DataFrame (e.g. ``frames.vehicles``).  Must carry
        the columns in :data:`REQUIRED_COLS` to produce output.  ``None`` or an
        empty frame causes a graceful skip.
    out_dir:
        Directory where output files are written.
    data_path:
        Path to eqasim-data/data; forwarded to ``fleet_age_status.build_panel``
        for the MiD reference values.

    Returns
    -------
    dict mapping output file name (str) to absolute path (str).
    Empty dict when the vehicles frame is absent or lacks required columns.
    """
    out_dir = Path(out_dir)

    # ------------------------------------------------------------------ #
    # Guard: data-absent-safe
    # ------------------------------------------------------------------ #
    if vehicles is None:
        log.warning("[fleet_evaluation] vehicles frame is None; fleet evaluation skipped.")
        return {}

    if not isinstance(vehicles, pd.DataFrame) or vehicles.empty:
        log.warning("[fleet_evaluation] vehicles frame is empty; fleet evaluation skipped.")
        return {}

    missing_cols = REQUIRED_COLS - set(vehicles.columns)
    if missing_cols:
        log.warning(
            "[fleet_evaluation] vehicles frame missing column(s) %s; "
            "fleet evaluation skipped.", sorted(missing_cols)
        )
        return {}

    # ------------------------------------------------------------------ #
    # Filter to mode=='car' if mode column present (drop car_passenger etc.)
    # ------------------------------------------------------------------ #
    df = vehicles.copy()
    if "mode" in df.columns:
        n_before = len(df)
        df = df[df["mode"] == "car"].copy()
        n_dropped = n_before - len(df)
        if n_dropped:
            log.info(
                "[fleet_evaluation] filtered %d non-car rows (mode != 'car'); "
                "%d cars remain.", n_dropped, len(df)
            )
        if df.empty:
            log.warning(
                "[fleet_evaluation] no rows with mode=='car' after filtering; "
                "fleet evaluation skipped."
            )
            return {}

    # Coerce age to numeric.
    df["age"] = pd.to_numeric(df["age"], errors="coerce")

    # ------------------------------------------------------------------ #
    # Pre-compute metrics
    # ------------------------------------------------------------------ #
    # Brand counts / shares (top 20).
    brand_counts = (
        df["brand"].astype(str).value_counts().rename_axis("brand").reset_index(name="count")
    )
    brand_counts["share"] = brand_counts["count"] / len(df)
    brand_top20 = brand_counts.head(20).copy()
    brand_top15 = brand_counts.head(15).copy()

    # Powertrain mix.
    pt_counts = (
        df["powertrain"].astype(str).value_counts().rename_axis("powertrain").reset_index(name="count")
    )
    pt_counts["share"] = pt_counts["count"] / len(df)

    # Contradiction count.
    contradiction_count = _count_contradictions(df)

    # Engine fingerprint share (top (engine_power_kw, displacement_ccm) pair).
    top_fingerprint_share = float("nan")
    if "engine_power_kw" in df.columns and "displacement_ccm" in df.columns:
        fp = df.groupby(["engine_power_kw", "displacement_ccm"]).size()
        if len(fp):
            top_fingerprint_share = float(fp.max()) / len(df)

    # Age × economic_status summary (mean age per status).
    age_status_rows = []
    for status in _STATUS_ORDER:
        sub = df[df["economic_status"] == status]
        age_status_rows.append({
            "economic_status": status,
            "n": len(sub),
            "mean_age_yr": float(sub["age"].mean()) if len(sub) else float("nan"),
        })
    age_status = pd.DataFrame(age_status_rows)

    # MiD reference mean age per status (via fleet_age_status.build_panel).
    mid_mean_ages: dict[str, float] = {}
    try:
        from braunschweig.analysis.population_validation import fleet_age_status as FAS
        panel = FAS.build_panel(df, data_path)
        if not panel.empty and "mid_mean_age_yr" in panel.columns:
            mid_mean_ages = dict(zip(panel["economic_status"], panel["mid_mean_age_yr"]))
    except Exception as exc:
        log.warning("[fleet_evaluation] could not load MiD age reference: %s", exc)

    # Consistency bar data: count per contradiction type.
    consistency_labels = []
    consistency_values = []
    fd_lower = df["fuel_detail"].astype(str).str.lower()
    pt_lower = df["powertrain"].astype(str).str.lower()
    for pt_name, substrings in _VALID_FUEL_SUBSTR.items():
        if not substrings:
            continue
        mask_pt = pt_lower == pt_name
        if not mask_pt.any():
            continue
        n_contradiction = int((~fd_lower[mask_pt].apply(
            lambda f: any(s in f for s in substrings)
        )).sum())
        if n_contradiction > 0 or pt_name in ("bev", "petrol", "diesel"):
            consistency_labels.append(pt_name)
            consistency_values.append(n_contradiction)

    # ------------------------------------------------------------------ #
    # Chart: fleet_overview.png (4-panel)
    # ------------------------------------------------------------------ #
    try:
        fig = plt.figure(figsize=(14, 10), constrained_layout=True)
        gs = gridspec.GridSpec(2, 2, figure=fig)

        ax_brand = fig.add_subplot(gs[0, 0])
        ax_age = fig.add_subplot(gs[0, 1])
        ax_pt = fig.add_subplot(gs[1, 0])
        ax_cons = fig.add_subplot(gs[1, 1])

        # Panel 1: Top-15 brand bar chart (horizontal).
        y_brand = range(len(brand_top15) - 1, -1, -1)
        ax_brand.barh(list(y_brand), brand_top15["share"] * 100, color="steelblue")
        ax_brand.set_yticks(list(y_brand))
        ax_brand.set_yticklabels(brand_top15["brand"], fontsize=8)
        ax_brand.set_xlabel("Fleet share (%)")
        ax_brand.set_title("Top 15 brands")
        ax_brand.axvline(0, color="black", linewidth=0.5)

        # Panel 2: Mean age by economic status (synthetic vs MiD reference).
        statuses_present = [s for s in _STATUS_ORDER if s in age_status["economic_status"].values]
        x_age = range(len(statuses_present))
        age_syn = [
            float(age_status.loc[age_status["economic_status"] == s, "mean_age_yr"].iloc[0])
            if s in age_status["economic_status"].values else float("nan")
            for s in statuses_present
        ]
        ax_age.bar(
            [xi - 0.2 for xi in x_age], age_syn, width=0.35,
            label="Synthetic", color="steelblue", alpha=0.85
        )
        if mid_mean_ages:
            age_mid = [mid_mean_ages.get(s, float("nan")) for s in statuses_present]
            ax_age.bar(
                [xi + 0.2 for xi in x_age], age_mid, width=0.35,
                label="MiD reference", color="darkorange", alpha=0.85
            )
        ax_age.set_xticks(list(x_age))
        ax_age.set_xticklabels(statuses_present, rotation=25, ha="right", fontsize=8)
        ax_age.set_ylabel("Mean vehicle age (years)")
        ax_age.set_title("Mean age by economic status")
        ax_age.legend(fontsize=8)

        # Panel 3: Powertrain mix bar chart.
        pt_labels = pt_counts["powertrain"].tolist()
        pt_shares = (pt_counts["share"] * 100).tolist()
        ax_pt.bar(range(len(pt_labels)), pt_shares, color="mediumseagreen")
        ax_pt.set_xticks(range(len(pt_labels)))
        ax_pt.set_xticklabels(pt_labels, rotation=25, ha="right", fontsize=8)
        ax_pt.set_ylabel("Fleet share (%)")
        ax_pt.set_title("Powertrain mix")

        # Panel 4: Fuel consistency (contradiction count per powertrain).
        bar_colors = ["tomato" if v > 0 else "mediumseagreen" for v in consistency_values]
        ax_cons.bar(range(len(consistency_labels)), consistency_values, color=bar_colors)
        ax_cons.set_xticks(range(len(consistency_labels)))
        ax_cons.set_xticklabels(consistency_labels, rotation=25, ha="right", fontsize=8)
        ax_cons.set_ylabel("Contradiction count")
        ax_cons.set_title(
            f"Fuel/powertrain consistency  (total contradictions: {contradiction_count})"
        )
        ax_cons.axhline(0, color="black", linewidth=0.5)

        fig.suptitle(
            f"Fleet evaluation  |  n={len(df):,} cars", fontsize=12, fontweight="bold"
        )
        out_png = out_dir / "fleet_overview.png"
        fig.savefig(out_png, dpi=110, bbox_inches="tight")
        plt.close(fig)
        log.info("[fleet_evaluation] wrote %s", out_png)
    except Exception as exc:
        log.warning("[fleet_evaluation] could not produce fleet_overview.png: %s", exc)
        out_png = None

    # ------------------------------------------------------------------ #
    # Summary CSV: fleet_summary.csv
    # ------------------------------------------------------------------ #
    out_csv = None
    try:
        rows = []

        # Section: brand counts / shares (top 20).
        rows.append({"section": "brand", "key": "# brand counts (top 20)",
                     "value": ""})
        for _, r in brand_top20.iterrows():
            rows.append({
                "section": "brand",
                "key": str(r["brand"]),
                "value": f"{r['count']} ({r['share'] * 100:.1f}%)",
            })

        # Section: powertrain mix shares.
        rows.append({"section": "powertrain", "key": "# powertrain mix shares",
                     "value": ""})
        for _, r in pt_counts.iterrows():
            rows.append({
                "section": "powertrain",
                "key": str(r["powertrain"]),
                "value": f"{r['count']} ({r['share'] * 100:.2f}%)",
            })

        # Section: consistency.
        rows.append({"section": "consistency", "key": "# fuel/powertrain consistency",
                     "value": ""})
        rows.append({
            "section": "consistency",
            "key": "contradiction_count",
            "value": str(contradiction_count),
        })
        if not pd.isna(top_fingerprint_share):
            rows.append({
                "section": "consistency",
                "key": "top_fingerprint_share",
                "value": f"{top_fingerprint_share:.4f}",
            })

        # Section: age × status table.
        rows.append({"section": "age_status", "key": "# mean age by economic_status",
                     "value": ""})
        for _, r in age_status.iterrows():
            mid_ref = mid_mean_ages.get(str(r["economic_status"]), float("nan"))
            mid_str = f"{mid_ref:.2f}" if not pd.isna(mid_ref) else "n/a"
            rows.append({
                "section": "age_status",
                "key": str(r["economic_status"]),
                "value": (
                    f"n={int(r['n'])}, mean_age={r['mean_age_yr']:.2f} yr "
                    f"(MiD ref: {mid_str} yr)"
                ),
            })

        summary_df = pd.DataFrame(rows, columns=["section", "key", "value"])
        out_csv = out_dir / "fleet_summary.csv"
        summary_df.to_csv(out_csv, index=False)
        log.info("[fleet_evaluation] wrote %s", out_csv)
    except Exception as exc:
        log.warning("[fleet_evaluation] could not produce fleet_summary.csv: %s", exc)
        out_csv = None

    # ------------------------------------------------------------------ #
    # Return paths of written files.
    # ------------------------------------------------------------------ #
    result: dict = {}
    if out_png is not None and out_png.exists():
        result["fleet_overview.png"] = str(out_png)
    if out_csv is not None and out_csv.exists():
        result["fleet_summary.csv"] = str(out_csv)
    return result
