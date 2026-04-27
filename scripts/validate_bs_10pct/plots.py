"""Matplotlib plot generators for the validation report.

Each function returns a saved PNG path (relative to the report dir).
All plots use the BS palette from style.py.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import io, metrics, diagnostics
from .config import DISTANCE_LABELS, DURATION_LABELS, MID_BASELINE, ZGB8
from .style import MODE_COLORS, PALETTE, PURPOSE_COLORS, apply_mpl_style


def _save(fig: plt.Figure, out_dir: Path, name: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path.name  # filename only — referenced relative from HTML next to it


# ---------------------------------------------------------------------------
def plot_population_per_kreis(out: Path) -> str:
    df = metrics.population_per_kreis()
    df = df[df["ars5"] != "TOTAL"].copy()
    fig, ax = plt.subplots(figsize=(9, 4.8))
    x = np.arange(len(df))
    w = 0.4
    ax.bar(x - w/2, df["zensus_2022"] / 1000, w, color=PALETTE["ref"], label="Zensus 2022")
    ax.bar(x + w/2, df["synth_expanded"] / 1000, w, color=PALETTE["synth"], label="Synth (×10)")
    for i, row in enumerate(df.itertuples()):
        ax.text(i, max(row.zensus_2022, row.synth_expanded)/1000 * 1.02,
                f"{row.deviation_pct:+.1f} %", ha="center", fontsize=9, color="#333")
    ax.set_xticks(x)
    ax.set_xticklabels(df["kreis_name"], rotation=20, ha="right")
    ax.set_ylabel("Population (thousands)")
    ax.set_title("Population per district — Synthesis vs. Census 2022")
    ax.legend(loc="upper right")
    return _save(fig, out, "01_population_per_kreis")


def plot_age_pyramid(out: Path) -> str:
    pyr = metrics.age_sex_pyramid()
    male = pyr[pyr["sex"] == "male"].set_index("age_bin")["count"]
    female = pyr[pyr["sex"] == "female"].set_index("age_bin")["count"]
    bins = male.index.tolist()
    fig, ax = plt.subplots(figsize=(7.5, 6))
    ax.barh(bins, -male.values / 1000, color=PALETTE["synth"], label="Male")
    ax.barh(bins, female.values / 1000, color=PALETTE["ref_light"], label="Female")
    ax.set_xlabel("Persons (thousands)")
    xt = ax.get_xticks()
    ax.set_xticklabels([f"{abs(int(t))}" for t in xt])
    ax.set_title("Age pyramid — Synthesis ZGB-8")
    ax.legend()
    return _save(fig, out, "02_age_pyramid")


def plot_household_size(out: Path) -> str:
    df = metrics.household_size_per_kreis()
    pivot_synth = df.pivot_table(index="size_bin", columns="ars5", values="synth_share").reindex(
        index=["1", "2", "3", "4", "5+"]
    )
    pivot_zen = df.pivot_table(index="size_bin", columns="ars5", values="zensus_share").reindex(
        index=["1", "2", "3", "4", "5+"]
    )
    fig, ax = plt.subplots(figsize=(9, 4.8))
    x = np.arange(len(pivot_synth.index))
    w = 0.35
    ax.bar(x - w/2, pivot_zen.mean(axis=1) * 100, w, color=PALETTE["ref"], label="Census 2022 (mean)")
    ax.bar(x + w/2, pivot_synth.mean(axis=1) * 100, w, color=PALETTE["synth"], label="Synthesis (mean)")
    ax.set_xticks(x)
    ax.set_xticklabels(pivot_synth.index)
    ax.set_xlabel("Household size")
    ax.set_ylabel("Share (%)")
    ax.set_title("Household size distribution — ZGB-8 mean")
    ax.legend()
    return _save(fig, out, "03_household_size")


def plot_employment_rates(out: Path) -> str:
    df = metrics.employment_summary()
    pivot = df.pivot_table(index="age_group", columns="ars5", values="employment_rate", observed=True)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    pivot.plot(kind="bar", ax=ax, colormap="Blues", edgecolor="white", legend=True)
    ax.set_ylabel("Employment rate")
    ax.set_xlabel("Age group")
    ax.set_title("Employment rate by age group and district")
    ax.legend(title="District", bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
    return _save(fig, out, "04_employment_rate")


def plot_commute_distance_distribution(out: Path) -> str:
    df = metrics.commute_distance_distribution()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(df["dist_bin"].astype(str), df["synth_share"] * 100, color=PALETTE["synth"])
    ax.set_xlabel("Commute distance (km, crow-fly)")
    ax.set_ylabel("Share (%)")
    ax.set_title("Commute distance distribution — Synthesis")
    return _save(fig, out, "05_commute_distance")


def plot_commute_per_kreis(out: Path) -> str:
    df = metrics.commute_distance_summary()
    df = df.merge(pd.DataFrame({"ars5": list(ZGB8.keys()), "kreis_name": list(ZGB8.values())}), on="ars5")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(df["kreis_name"], df["mean_km"], color=PALETTE["synth"], label="Mean")
    ax.bar(df["kreis_name"], df["median_km"], color=PALETTE["synth_light"], width=0.6, label="Median")
    ax.set_ylabel("Commute distance (km)")
    ax.set_title("Commute distance by home district")
    ax.set_xticklabels(df["kreis_name"], rotation=20, ha="right")
    ax.legend()
    return _save(fig, out, "06_commute_per_kreis")


def plot_commute_heatmap(out: Path) -> str:
    """Top Kreis-OD heatmap synth vs BA."""
    df = metrics.commute_od_kreis()
    df = df[df["orig_ars"].isin(ZGB8.keys()) & df["dest_ars"].isin(ZGB8.keys())]
    pv = df.pivot_table(index="orig_ars", columns="dest_ars", values="synth_flow_expanded", fill_value=0)
    fig, ax = plt.subplots(figsize=(7.5, 6))
    im = ax.imshow(pv.values, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(pv.columns)))
    ax.set_yticks(range(len(pv.index)))
    ax.set_xticklabels([ZGB8.get(c, c) for c in pv.columns], rotation=45, ha="right")
    ax.set_yticklabels([ZGB8.get(c, c) for c in pv.index])
    ax.set_xlabel("Workplace district")
    ax.set_ylabel("Home district")
    ax.set_title("Internal commuter flows ZGB-8 (Synthesis, ×10)")
    fig.colorbar(im, ax=ax, label="Commuters")
    return _save(fig, out, "07_commute_heatmap")


def plot_commute_scatter_ba(out: Path) -> str:
    df = metrics.commute_od_kreis()
    df = df[(df["ba_flow"] > 0) & (df["synth_flow_expanded"] > 0)]
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    ax.scatter(df["ba_flow"], df["synth_flow_expanded"], alpha=0.5, color=PALETTE["synth"])
    lim = max(df["ba_flow"].max(), df["synth_flow_expanded"].max()) * 1.05
    ax.plot([1, lim], [1, lim], color=PALETTE["ref"], linewidth=1)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("BA commuter atlas (SvB employees)")
    ax.set_ylabel("Synthesis (×10)")
    ax.set_title("District → district commuter flows — Synthesis vs. BA")
    return _save(fig, out, "08_commute_scatter_ba")


def plot_mode_share_donut(out: Path) -> str:
    df = metrics.mode_share_overall()
    df = df[df["mode"].isin(["miv", "oev", "rad", "fuss"])].copy()
    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    for ax, col, title in [
        (axes[0], "synth_share", "Synthesis"),
        (axes[1], "mid_share", "MiD 2023"),
    ]:
        colors = [MODE_COLORS.get(m, "#999") for m in df["mode"]]
        wedges, _ = ax.pie(df[col], colors=colors, startangle=90,
                           wedgeprops=dict(width=0.4, edgecolor="white"))
        ax.set_title(title)
        ax.text(0, 0, f"{df[col].sum()*100:.0f}%", ha="center", va="center",
                fontsize=14, fontweight="bold")
    fig.legend(df["mode"], loc="lower center", ncol=4, frameon=False)
    fig.suptitle("Modal split — all trips", fontsize=13, fontweight="bold")
    return _save(fig, out, "09_mode_share_donut")


def plot_mode_share_by_distance(out: Path) -> str:
    df = metrics.mode_share_by_distance()
    pv = df.pivot_table(index="dist_bin", columns="mid_mode", values="share", observed=True).fillna(0)
    pv = pv.reindex(index=DISTANCE_LABELS)
    fig, ax = plt.subplots(figsize=(9, 5))
    bottom = np.zeros(len(pv))
    for col in ["fuss", "rad", "oev", "miv", "other"]:
        if col in pv.columns:
            ax.bar(pv.index, pv[col] * 100, bottom=bottom * 100,
                   label=col, color=MODE_COLORS.get(col, "#999"))
            bottom = bottom + pv[col].values
    ax.set_xlabel("Distance (km)")
    ax.set_ylabel("Share (%)")
    ax.set_title("Modal split by distance class")
    ax.legend(loc="upper right")
    return _save(fig, out, "10_mode_x_distance")


def plot_mode_share_by_purpose(out: Path) -> str:
    df = metrics.mode_share_by_purpose()
    pv = df.pivot_table(index="following_purpose", columns="mid_mode", values="share").fillna(0)
    fig, ax = plt.subplots(figsize=(9, 5))
    bottom = np.zeros(len(pv))
    for col in ["fuss", "rad", "oev", "miv", "other"]:
        if col in pv.columns:
            ax.bar(pv.index, pv[col] * 100, bottom=bottom * 100,
                   label=col, color=MODE_COLORS.get(col, "#999"))
            bottom = bottom + pv[col].values
    ax.set_xlabel("Activity type")
    ax.set_ylabel("Share (%)")
    ax.set_title("Modal split by purpose")
    ax.legend(loc="upper right")
    return _save(fig, out, "11_mode_x_purpose")


def plot_distance_distribution(out: Path) -> str:
    df = metrics.trip_distance_distribution()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(df["dist_bin"].astype(str), df["share"] * 100, color=PALETTE["synth"])
    ax.set_xlabel("Trip distance (km, crow-fly)")
    ax.set_ylabel("Share (%)")
    ax.set_title("Trip distance distribution — all trips")
    return _save(fig, out, "12_distance_distribution")


def plot_distance_cdf(out: Path) -> str:
    trips = io.trips_full()
    d = np.sort(trips["distance_km"].values)
    cdf = np.linspace(0, 1, len(d))
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(d, cdf, color=PALETTE["synth"], linewidth=2)
    ax.axvline(MID_BASELINE["mean_trip_distance_km"], color=PALETTE["ref"],
               linestyle="--", label=f"MiD mean = {MID_BASELINE['mean_trip_distance_km']} km")
    ax.set_xscale("log")
    ax.set_xlim(0.1, 200)
    ax.set_xlabel("Trip distance (km)")
    ax.set_ylabel("Cumulative share")
    ax.set_title("Trip distance CDF (log)")
    ax.legend()
    return _save(fig, out, "13_distance_cdf")


def plot_duration_distribution(out: Path) -> str:
    df = metrics.trip_duration_distribution()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(df["dur_bin"].astype(str), df["share"] * 100, color=PALETTE["synth"])
    ax.set_xlabel("Travel time (min)")
    ax.set_ylabel("Share (%)")
    ax.set_title("Travel time distribution — all trips")
    return _save(fig, out, "14_duration_distribution")


def plot_departure_profile(out: Path) -> str:
    df = metrics.departure_profile()
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.fill_between(df["departure_hour"], df["share"] * 100, color=PALETTE["synth_light"], alpha=0.6)
    ax.plot(df["departure_hour"], df["share"] * 100, color=PALETTE["synth"], linewidth=2)
    ax.set_xlabel("Departure hour")
    ax.set_ylabel("Share (%)")
    ax.set_xticks(range(0, 24, 2))
    ax.set_title("Hourly departure profile — all trips")
    return _save(fig, out, "15_departure_profile")


def plot_purpose_mix(out: Path) -> str:
    df = metrics.purpose_mix()
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(df))
    w = 0.4
    ax.bar(x - w/2, df["mid_share"] * 100, w, color=PALETTE["ref"], label="MiD 2023")
    ax.bar(x + w/2, df["synth_share"] * 100, w, color=PALETTE["synth"], label="Synthesis")
    ax.set_xticks(x)
    ax.set_xticklabels(df["purpose"], rotation=20, ha="right")
    ax.set_ylabel("Share of all trips (%)")
    ax.set_title("Activity-purpose mix — Synthesis vs. MiD")
    ax.legend()
    return _save(fig, out, "16_purpose_mix")


def plot_choropleth_population(out: Path) -> str:
    kreise = io.load_kreise_gdf()
    pop = metrics.population_per_kreis()
    pop = pop[pop["ars5"] != "TOTAL"].drop(columns=["kreis_name"])
    gdf = kreise.merge(pop, on="ars5", how="left")
    fig, ax = plt.subplots(figsize=(8, 7))
    gdf.plot(column="deviation_pct", ax=ax, cmap="RdBu_r", legend=True,
             vmin=-5, vmax=5, edgecolor="#444", linewidth=0.6,
             legend_kwds={"label": "Deviation Synthesis vs. Census (%)", "shrink": 0.7})
    for _, r in gdf.iterrows():
        if r.geometry is not None and not r.geometry.is_empty:
            c = r.geometry.representative_point()
            ax.annotate(f"{r['kreis_name']}\n{r['deviation_pct']:+.1f}%",
                        (c.x, c.y), ha="center", fontsize=8)
    ax.set_axis_off()
    ax.set_title("Population deviation per district")
    return _save(fig, out, "17_choropleth_population")


# ---------------------------------------------------------------------------
def render_all(out_dir: Path) -> dict[str, str]:
    """Render every plot and return mapping name → filename."""
    apply_mpl_style()
    out: dict[str, str] = {}
    out["population_per_kreis"]  = plot_population_per_kreis(out_dir)
    out["age_pyramid"]           = plot_age_pyramid(out_dir)
    out["household_size"]        = plot_household_size(out_dir)
    out["employment_rate"]       = plot_employment_rates(out_dir)
    out["commute_distance"]      = plot_commute_distance_distribution(out_dir)
    out["commute_per_kreis"]     = plot_commute_per_kreis(out_dir)
    out["commute_heatmap"]       = plot_commute_heatmap(out_dir)
    out["commute_scatter_ba"]    = plot_commute_scatter_ba(out_dir)
    out["mode_share_donut"]      = plot_mode_share_donut(out_dir)
    out["mode_x_distance"]       = plot_mode_share_by_distance(out_dir)
    out["mode_x_purpose"]        = plot_mode_share_by_purpose(out_dir)
    out["distance_distribution"] = plot_distance_distribution(out_dir)
    out["distance_cdf"]          = plot_distance_cdf(out_dir)
    out["duration_distribution"] = plot_duration_distribution(out_dir)
    out["departure_profile"]     = plot_departure_profile(out_dir)
    out["purpose_mix"]           = plot_purpose_mix(out_dir)
    out["choropleth_population"] = plot_choropleth_population(out_dir)
    # --- diagnostics (calibration validation harness) ---
    out["od_scatter_top200"]    = plot_od_scatter_top(out_dir)
    out["od_outbound_top20"]    = plot_od_outbound(out_dir)
    out["hh_size_per_kreis"]    = plot_hh_size_per_kreis(out_dir)
    out["purpose_remap"]        = plot_purpose_remap(out_dir)
    return out


# ---------------------------------------------------------------------------
# Calibration diagnostics (section 7 of the report)
# ---------------------------------------------------------------------------
def plot_od_scatter_top(out: Path) -> str:
    od, stats = diagnostics.od_fit_stats(top_n=200)
    fig, ax = plt.subplots(figsize=(8, 7))
    if od.empty:
        ax.text(0.5, 0.5, "No OD pairs available", ha="center", va="center")
    else:
        ax.scatter(od["ba_flow"], od["synth_flow_expanded"],
                   s=22, color=PALETTE["synth"], alpha=0.7, edgecolor="white")
        lim_max = max(float(od["ba_flow"].max()), float(od["synth_flow_expanded"].max())) * 1.1
        ax.plot([1, lim_max], [1, lim_max], "--", color=PALETTE["ref"], linewidth=1, label="y = x")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlim(1, lim_max); ax.set_ylim(1, lim_max)
        ax.set_xlabel("BA Pendleratlas (SvB)")
        ax.set_ylabel("Synthesis (expanded ×10)")
        anno = (f"n = {stats['n_pairs']}\n"
                f"R² = {stats['r2']:.3f}\n"
                f"RMSE = {stats['rmse']:,.0f}\n"
                f"MAPE = {stats['mape_pct']:.1f}%\n"
                f"Bias = {stats['bias_pct']:+.1f}%")
        ax.text(0.04, 0.96, anno, transform=ax.transAxes, ha="left", va="top",
                fontsize=10, family="monospace",
                bbox=dict(facecolor="white", edgecolor="#cccccc", boxstyle="round,pad=0.4"))
        ax.legend(loc="lower right")
    ax.set_title("OD fit — top-200 Kreis-pairs")
    return _save(fig, out, "18_od_scatter_top200")


def plot_od_outbound(out: Path) -> str:
    df = diagnostics.od_top_outbound(top_n=20)
    fig, ax = plt.subplots(figsize=(10, 8))
    if df.empty:
        ax.text(0.5, 0.5, "No outbound flows", ha="center", va="center")
    else:
        df = df.copy()
        df["label"] = (df["orig_ars"].map(ZGB8).fillna(df["orig_ars"]) + "\n→ " + df["dest_ars"])
        y = np.arange(len(df))
        height = 0.4
        ax.barh(y - height/2, df["ba_flow"], height=height,
                label="BA Pendleratlas", color=PALETTE["ref"])
        ax.barh(y + height/2, df["synth_flow_expanded"], height=height,
                label="Synthesis (expanded)", color=PALETTE["synth"])
        ax.set_yticks(y, df["label"], fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("Commuters")
        ax.legend(loc="lower right")
    ax.set_title("Top-20 outbound commuter flows ZGB → external")
    return _save(fig, out, "19_od_outbound_top20")


def plot_hh_size_per_kreis(out: Path) -> str:
    summary, hh = diagnostics.hh_size_fit_per_kreis()
    fig, axes = plt.subplots(2, 4, figsize=(16, 8), sharey=True)
    axes = axes.flatten()
    bins = sorted(hh["size_bin"].unique())
    x = np.arange(len(bins))
    width = 0.4
    for i, ars5 in enumerate(sorted(ZGB8.keys())):
        ax = axes[i]
        sub = hh[hh["ars5"] == ars5].set_index("size_bin").reindex(bins).fillna(0)
        ax.bar(x - width/2, sub["synth_share"], width, label="Synth", color=PALETTE["synth"])
        ax.bar(x + width/2, sub["zensus_share"], width, label="Zensus 2022", color=PALETTE["ref"])
        ax.set_xticks(x, bins)
        row = summary[summary["ars5"] == ars5]
        chi = float(row["chi2"].iloc[0]) if len(row) else float("nan")
        tvd = float(row["tvd_pp"].iloc[0]) if len(row) else float("nan")
        ax.set_title(f"{ZGB8[ars5]}\nχ² = {chi:,.0f} · TVD = {tvd:.1f} pp", fontsize=10)
        ax.grid(axis="y", linewidth=0.4, alpha=0.6)
        if i == 0:
            ax.legend(loc="upper right", fontsize=8)
            ax.set_ylabel("Share of households")
    fig.suptitle("Household-size distribution per district — Synthesis vs Zensus 2022", fontsize=13)
    fig.tight_layout()
    return _save(fig, out, "20_hh_size_per_kreis")


def plot_purpose_remap(out: Path) -> str:
    """Side-by-side: raw eqasim purpose mix vs MiD-aligned remap (H1 preview)."""
    raw = metrics.purpose_mix_raw().set_index("purpose")[["synth_share", "mid_share"]]
    # Now uses raw ENTD purposes (R-D remap removed) — kept for filename compatibility.
    remap = diagnostics.purpose_mix_remapped().set_index("purpose")[["synth_share", "mid_share"]]
    purposes = sorted(set(raw.index) | set(remap.index) | set(MID_BASELINE["purpose_mix"].keys()))
    raw = raw.reindex(purposes).fillna(0)
    remap = remap.reindex(purposes).fillna(0)
    x = np.arange(len(purposes))
    width = 0.28

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(x - width, raw["synth_share"], width, label="Synth raw (eqasim)", color=PALETTE["synth"], alpha=0.6)
    ax.bar(x,         remap["synth_share"], width, label="Synth remapped (MiD-aligned)", color=PALETTE["synth"])
    ax.bar(x + width, raw["mid_share"],   width, label="MiD 2023", color=PALETTE["ref"])
    ax.set_xticks(x, purposes)
    ax.set_ylabel("Share of trips")
    ax.set_title("Activity-purpose mix — raw vs MiD-aligned remap (H1 preview)")
    ax.legend()
    ax.grid(axis="y", linewidth=0.4, alpha=0.6)
    fig.tight_layout()
    return _save(fig, out, "21_purpose_remap")
