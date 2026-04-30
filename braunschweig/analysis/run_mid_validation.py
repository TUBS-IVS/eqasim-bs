"""MiD 2023 validation as a runnable script.

Ports `braunschweig/analysis/validation_mid2023.ipynb` to a parametrised
CLI tool so any eqasim run output directory can be analysed without
opening Jupyter.

Usage (PowerShell, conda env `eqasim` activated):

    python -m braunschweig.analysis.run_mid_validation `
        --output-dir eqasim-data/output_bs_25pct_parking `
        --prefix braunschweig_25pct_parking_ `
        --analysis-out eqasim-data/output_bs_25pct_parking/analysis/mid_validation

The script
  1. reads the eqasim CSV / GPKG outputs of one run,
  2. spatially joins home points to the eight ZGB Kreise (VG250),
  3. computes commute distance bands per Kreis vs MiD P13,
  4. computes employment / driver-license rate vs MiD P9 / P17.1,
  5. writes a battery of PNGs + per-table CSVs + a `report.json` that
     mirrors the metric structure consumed by the dashboard.

All intermediate tables are also written so a downstream comparison
(e.g. parking-on vs no-parking) can join on them without re-reading the
GPKG inputs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.mid_validation")

REPO_ROOT = Path(__file__).resolve().parents[2]

# ZGB-8 Kreise: AGS-5 → display name.  Kept consistent with
# `braunschweig.analysis.dashboard.build_dashboard.KREIS_NAMES`.
ZGB8: dict[str, str] = {
    "03101": "SK Braunschweig",
    "03102": "SK Salzgitter",
    "03103": "SK Wolfsburg",
    "03151": "LK Gifhorn",
    "03153": "LK Goslar",
    "03154": "LK Helmstedt",
    "03157": "LK Peine",
    "03158": "LK Wolfenbüttel",
}

# MiD P13 distance bands (km).  Matches the keys in
# `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`.
BANDS: list[tuple[float, float, str]] = [
    (0.0, 0.5, "d_0"),
    (0.5, 5.0, "d_0_5"),
    (5.0, 10.0, "d_5_10"),
    (10.0, 20.0, "d_10_20"),
    (20.0, 30.0, "d_20_30"),
    (30.0, 50.0, "d_30_50"),
    (50.0, 100.0, "d_50_100"),
    (100.0, np.inf, "d_100p"),
]

# VG250 zip shipped alongside the rest of the spatial inputs.
VG250_ZIP = (
    REPO_ROOT
    / "eqasim-data"
    / "data"
    / "germany"
    / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
)
VG250_INNER = (
    "vg250-ew_12-31.utm32s.gpkg.ebenen/"
    "vg250-ew_ebenen_1231/DE_VG250.gpkg"
)

MID_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"

# RegioStaR-7 reference for per-Raumtyp diagnostics. Filename pinned via
# scripts/download_regiostar.py (TASK-004); not regenerated here.
REGIOSTAR_XLSX = (
    REPO_ROOT / "eqasim-data" / "data" / "regiostar"
    / "regiostar_referenzdatei.xlsx"
)
REGIOSTAR_SHEET = "ReferenzGebietsstand2020"
REGIOSTAR7_LABELS: dict[int, str] = {
    71: "Metropole",
    72: "Regiopole/Großstadt",
    73: "Mittelstadt/städt. Raum",
    74: "Kleinstadt/dörfl. Raum",
    75: "Zentrale Stadt (ländlich)",
    76: "Mittelstadt (ländlich)",
    77: "Kleinstadt/dörfl. (ländlich)",
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Args:
    output_dir: Path
    prefix: str
    analysis_out: Path
    label: str


def _parse_args(argv: list[str] | None) -> _Args:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--output-dir",
        required=True,
        help="eqasim run output directory containing *_persons.csv / *_homes.gpkg / ...",
    )
    ap.add_argument(
        "--prefix",
        required=False,
        default=None,
        help="Filename prefix shared by all CSV/GPKG files. "
        "Auto-detected from the directory name when omitted.",
    )
    ap.add_argument(
        "--analysis-out",
        required=False,
        default=None,
        help="Destination folder for figures + tables + report.json. "
        "Defaults to <output-dir>/analysis/mid_validation/.",
    )
    ap.add_argument(
        "--label",
        required=False,
        default=None,
        help="Human-readable label written into report.json. Defaults to the prefix.",
    )
    ns = ap.parse_args(argv)

    output_dir = Path(ns.output_dir).resolve()
    if not output_dir.is_dir():
        ap.error(f"--output-dir does not exist: {output_dir}")

    prefix = ns.prefix
    if prefix is None:
        # Pick any *_persons.csv and strip "persons.csv".
        candidates = list(output_dir.glob("*_persons.csv"))
        if not candidates:
            ap.error(
                f"No *_persons.csv in {output_dir}; pass --prefix explicitly."
            )
        prefix = candidates[0].name[: -len("persons.csv")]

    analysis_out = (
        Path(ns.analysis_out).resolve()
        if ns.analysis_out is not None
        else output_dir / "analysis" / "mid_validation"
    )
    analysis_out.mkdir(parents=True, exist_ok=True)

    return _Args(
        output_dir=output_dir,
        prefix=prefix,
        analysis_out=analysis_out,
        label=ns.label or prefix.rstrip("_"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def band_share(distances_km: np.ndarray) -> dict[str, float]:
    """Percentage share of commute distances per MiD P13 band.

    Returns a dict mapping band name → share in percent.  Missing values
    contribute 0%.
    """
    distances_km = np.asarray(distances_km, dtype=float)
    distances_km = distances_km[~np.isnan(distances_km)]
    if distances_km.size == 0:
        return {name: 0.0 for _, _, name in BANDS}
    return {
        name: float(100.0 * np.mean((distances_km >= lo) & (distances_km < hi)))
        for lo, hi, name in BANDS
    }


def _bool_share(series: pd.Series) -> float:
    """Share of truthy values in ``series`` as a percentage.

    Accepts the eqasim convention where booleans are stringified
    (``"True"`` / ``"False"`` / ``"1"`` / ``"0"``).
    """
    if len(series) == 0:
        return float("nan")
    truthy = series.astype(str).str.lower().isin(["true", "1", "yes"])
    return float(100.0 * truthy.mean())


def _load_kreise(homes_crs: Any) -> gpd.GeoDataFrame:
    if not VG250_ZIP.exists():
        raise FileNotFoundError(
            f"VG250 archive missing: {VG250_ZIP}.  Re-run the synpp data download."
        )
    with zipfile.ZipFile(VG250_ZIP) as z, z.open(VG250_INNER) as fh:
        vg = gpd.read_file(fh, layer="vg250_gem")
    vg["ars5"] = vg["ARS"].astype(str).str[:5]
    kreise = (
        vg[vg["ars5"].isin(ZGB8)][["ars5", "geometry"]]
        .dissolve(by="ars5", as_index=False)
        .to_crs(homes_crs)
    )
    kreise["kreis_name"] = kreise["ars5"].map(ZGB8)
    return kreise


def _load_gemeinden(homes_crs: Any) -> gpd.GeoDataFrame:
    """Load ZGB-8 Gemeinde polygons keyed by 8-digit AGS (commune_id).

    VG250 ``ARS`` is 12 digits (Land(2) + RB(1) + Kreis(2) + VG(4) + Gem(3)).
    The 8-digit AGS used by the RegioStaR reference and downstream stages
    is ``ARS[0:5] + ARS[9:12]`` (Kreis prefix + 3-digit Gemeinde number).
    """
    if not VG250_ZIP.exists():
        raise FileNotFoundError(
            f"VG250 archive missing: {VG250_ZIP}.  Re-run the synpp data download."
        )
    with zipfile.ZipFile(VG250_ZIP) as z, z.open(VG250_INNER) as fh:
        vg = gpd.read_file(fh, layer="vg250_gem")
    ars = vg["ARS"].astype(str).str.zfill(12)
    vg["commune_id"] = ars.str[:5] + ars.str[9:12]
    vg["ars5"] = vg["commune_id"].str[:5]
    gem = (
        vg[vg["ars5"].isin(ZGB8)][["commune_id", "geometry"]]
        .dissolve(by="commune_id", as_index=False)
        .to_crs(homes_crs)
    )
    return gem


def _load_regiostar() -> pd.DataFrame:
    """Load the RegioStaR-7 reference (commune_id → regiostar7).

    Returns an empty frame with the expected schema if the source file is
    missing — RS7 diagnostics will then be silently skipped instead of
    failing the whole validation run.
    """
    if not REGIOSTAR_XLSX.exists():
        LOGGER.warning(
            "RegioStaR reference missing (%s); RS7 breakdowns will be skipped.",
            REGIOSTAR_XLSX,
        )
        return pd.DataFrame(columns=["commune_id", "regiostar7"])
    raw = pd.read_excel(REGIOSTAR_XLSX, sheet_name=REGIOSTAR_SHEET, header=0)
    df = pd.DataFrame({
        "commune_id": raw["gem_20"].astype("Int64").astype(str).str.zfill(8),
        "regiostar7": pd.to_numeric(raw["RegioStaR7"], errors="coerce")
                       .astype("Int64"),
    }).dropna(subset=["regiostar7"])
    df["regiostar7"] = df["regiostar7"].astype(int)
    df["rs7_label"] = df["regiostar7"].map(REGIOSTAR7_LABELS).fillna("unknown")
    return df


def _load_mid() -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for code in ("P9", "P12_1", "P13", "P17_1"):
        path = MID_DIR / f"mid2023_{code}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing MiD reference table: {path}. "
                "Run scripts/seed_mid_constraint_tables.py first."
            )
        df = pd.read_csv(path)
        df["ars5"] = df["ars5"].astype(str)
        df["kreis"] = df["kreis"].astype(str)
        tables[code] = df
    return tables


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _read_csv(output_dir: Path, prefix: str, name: str) -> pd.DataFrame:
    return pd.read_csv(output_dir / f"{prefix}{name}.csv", sep=";")


def _read_gpkg(output_dir: Path, prefix: str, name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(output_dir / f"{prefix}{name}.gpkg")


def _save_fig(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _df_to_markdown(df: pd.DataFrame) -> str:
    """Render ``df`` as a GitHub-flavoured markdown table.

    Provided locally so the script does not depend on the optional
    ``tabulate`` package (`pandas.DataFrame.to_markdown` requires it).
    """
    if df.empty:
        return "(no rows)"
    cols = [str(c) for c in df.columns]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows: list[str] = []
    for _, row in df.iterrows():
        cells: list[str] = []
        for value in row.values:
            if isinstance(value, float):
                cells.append(f"{value:.2f}")
            else:
                cells.append(str(value))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *rows])


def _commute_distances(
    activities: gpd.GeoDataFrame,
    homes_kreis: gpd.GeoDataFrame,
    persons_kreis: pd.DataFrame,
) -> pd.DataFrame:
    work = (
        activities[activities["purpose"] == "work"]
        .drop_duplicates("person_id")[["person_id", "household_id", "geometry"]]
        .rename(columns={"geometry": "work_geom"})
    )
    home_lookup = (
        homes_kreis[["household_id", "geometry"]]
        .rename(columns={"geometry": "home_geom"})
        .drop_duplicates("household_id")
    )
    commute = work.merge(home_lookup, on="household_id", how="inner")
    if commute.empty:
        return commute.assign(distance_km=[], ars5=[], kreis_name=[])
    commute["distance_km"] = commute.apply(
        lambda r: r["home_geom"].distance(r["work_geom"]) / 1000.0, axis=1
    )
    commute = commute.merge(
        persons_kreis[["person_id", "ars5", "kreis_name"]],
        on="person_id",
        how="left",
    )
    return commute


def _commute_band_table(
    commute: pd.DataFrame, mid_p13: pd.DataFrame
) -> pd.DataFrame:
    p13 = mid_p13.set_index("ars5")
    rows: list[dict[str, Any]] = []
    for ars5, label in ZGB8.items():
        sub = commute[commute["ars5"] == ars5]
        if sub.empty:
            continue
        syn = band_share(sub["distance_km"].values)
        ref = p13.loc[ars5]
        for _, _, name in BANDS:
            rows.append(
                {
                    "ars5": ars5,
                    "kreis": label,
                    "band": name,
                    "synthetic_pct": syn[name],
                    "mid_pct": float(ref.get(name, np.nan)),
                }
            )
    return pd.DataFrame(rows)


def _plot_commute_bands(compare: pd.DataFrame, path: Path) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(16, 7), sharey=True)
    band_labels = [name for _, _, name in BANDS]
    band_x = np.arange(len(band_labels))
    for ax, ars5 in zip(axes.ravel(), ZGB8.keys()):
        sub = compare[compare["ars5"] == ars5]
        if sub.empty:
            ax.set_axis_off()
            continue
        ax.bar(
            band_x - 0.2,
            sub["synthetic_pct"],
            width=0.4,
            label="synth",
            color="tab:blue",
        )
        ax.bar(
            band_x + 0.2,
            sub["mid_pct"],
            width=0.4,
            label="MiD",
            color="tab:orange",
        )
        ax.set_xticks(band_x)
        ax.set_xticklabels(band_labels, rotation=45, ha="right")
        ax.set_title(f"{ars5} {ZGB8[ars5]}")
        ax.set_ylabel("% of commuters")
    axes[0, 0].legend()
    fig.suptitle("Commute distance: synthetic vs MiD 2023 P13")
    _save_fig(fig, path)


def _plot_license(table: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(table))
    ax.bar(x - 0.2, table["synthetic_pct"], width=0.4, label="synthetic (KBA)")
    ax.bar(x + 0.2, table["mid_pct"], width=0.4, label="MiD P17.1")
    ax.set_xticks(x)
    ax.set_xticklabels(table["kreis"], rotation=35, ha="right")
    ax.set_ylabel("% with license (age >= 17)")
    ax.set_title("Driver license rate per Kreis")
    ax.legend()
    _save_fig(fig, path)


def _plot_employment(table: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(table))
    ax.bar(x - 0.2, table["synthetic_pct"], width=0.4, label="synthetic")
    ax.bar(x + 0.2, table["mid_pct"], width=0.4, label="MiD P9")
    ax.set_xticks(x)
    ax.set_xticklabels(table["kreis"], rotation=35, ha="right")
    ax.set_ylabel("% employed (age 15-74)")
    ax.set_title("Employment rate per Kreis")
    ax.legend()
    _save_fig(fig, path)


def _plot_rs7_kpis(table: pd.DataFrame, path: Path) -> None:
    """Bar chart of synthetic KPIs per RegioStaR-7 class.

    Plots license %, employment %, PT-subscription % and mean commute km
    side-by-side; commute km is shown on a secondary y-axis because it
    has a different scale from percentage shares.
    """
    if table.empty:
        return
    fig, ax = plt.subplots(figsize=(11, 4.5))
    x = np.arange(len(table))
    width = 0.22
    ax.bar(x - 1.5 * width, table["license_pct"], width=width, label="license %")
    ax.bar(x - 0.5 * width, table["employment_pct"], width=width, label="employment %")
    ax.bar(x + 0.5 * width, table.get("pt_subscription_pct", 0),
           width=width, label="PT subscription %")
    ax.set_ylabel("% of persons")
    ax.set_xticks(x)
    ax.set_xticklabels(
        [f"{int(c)}\n{lab}" for c, lab in zip(table["regiostar7"], table["rs7_label"])],
        rotation=0, ha="center", fontsize=8,
    )
    ax2 = ax.twinx()
    ax2.plot(x, table["mean_commute_km"], color="black",
             marker="o", linewidth=1.5, label="mean commute km")
    ax2.set_ylabel("mean commute (km)")
    ax.set_title("KPIs per RegioStaR-7 class (synthetic only)")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    _save_fig(fig, path)


def _plot_demographics(
    persons: pd.DataFrame, households: pd.DataFrame, path: Path
) -> None:
    hhsize_order = ["1", "2", "3", "4", "5+"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.5))
    persons["age"].hist(bins=30, ax=axes[0])
    axes[0].set_title("Age")
    (
        households["household_size"]
        .astype(str)
        .value_counts()
        .reindex(hhsize_order)
        .fillna(0)
        .plot.bar(ax=axes[1])
    )
    axes[1].set_title("Household size")
    households["number_of_cars"].value_counts().sort_index().plot.bar(ax=axes[2])
    axes[2].set_title("Cars per HH")
    _save_fig(fig, path)


def _plot_trips(trips: pd.DataFrame, path: Path) -> None:
    trips = trips.copy()
    trips["duration_min"] = (
        trips["arrival_time"] - trips["departure_time"]
    ) / 60.0
    trips["departure_hour"] = (trips["departure_time"] // 3600).astype(int) % 24
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.5))
    trips["departure_hour"].value_counts().sort_index().plot.bar(
        ax=axes[0], title="Trip departure hour"
    )
    trips["duration_min"].clip(upper=120).hist(bins=40, ax=axes[1])
    axes[1].set_title("Trip duration (min, clipped at 120)")
    _save_fig(fig, path)


def _plot_purposes(activities: gpd.GeoDataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 3.5))
    activities["purpose"].value_counts().plot.bar(
        ax=ax, title="Activity purposes (count)"
    )
    _save_fig(fig, path)


def _plot_homes_map(
    homes: gpd.GeoDataFrame,
    activities: gpd.GeoDataFrame,
    kreise: gpd.GeoDataFrame,
    path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(10, 9))
    kreise.boundary.plot(ax=ax, color="black", linewidth=0.5)
    homes.sample(min(len(homes), 5000), random_state=0).plot(
        ax=ax, markersize=1, color="tab:blue", label="homes", alpha=0.4
    )
    for purpose, color in [
        ("shop", "tab:orange"),
        ("leisure", "tab:green"),
        ("other", "tab:red"),
    ]:
        sub = activities[activities["purpose"] == purpose]
        if sub.empty:
            continue
        sub.sample(min(len(sub), 2000), random_state=0).plot(
            ax=ax, markersize=1, color=color, label=purpose, alpha=0.4
        )
    ax.set_title("Homes and secondary activity locations (ZGB-8)")
    ax.legend()
    ax.set_axis_off()
    _save_fig(fig, path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(args: _Args) -> dict[str, Any]:
    LOGGER.info("Loading run outputs from %s (prefix=%s)", args.output_dir, args.prefix)

    persons = _read_csv(args.output_dir, args.prefix, "persons")
    households = _read_csv(args.output_dir, args.prefix, "households")
    trips = _read_csv(args.output_dir, args.prefix, "trips")
    homes = _read_gpkg(args.output_dir, args.prefix, "homes")
    activities = _read_gpkg(args.output_dir, args.prefix, "activities")

    for col in ("number_of_cars", "number_of_bicycles"):
        households[col] = pd.to_numeric(households[col], errors="coerce")
    households["household_size"] = households["household_size"].astype(str)

    LOGGER.info("Loading VG250 polygons + MiD reference tables")
    kreise = _load_kreise(homes.crs)
    gemeinden = _load_gemeinden(homes.crs)
    regiostar = _load_regiostar()
    mid = _load_mid()

    LOGGER.info("Spatial-joining home points to ZGB-8 Kreise")
    homes_kreis = gpd.sjoin(
        homes,
        kreise[["ars5", "kreis_name", "geometry"]],
        how="left",
        predicate="within",
    ).drop(columns="index_right")

    # Second sjoin onto Gemeinde polygons → 8-digit AGS → RegioStaR-7.
    if not regiostar.empty:
        homes_gem = gpd.sjoin(
            homes,
            gemeinden[["commune_id", "geometry"]],
            how="left",
            predicate="within",
        ).drop(columns="index_right")
        rs7_per_household = (
            homes_gem[["household_id", "commune_id"]]
            .merge(regiostar[["commune_id", "regiostar7", "rs7_label"]],
                   on="commune_id", how="left")
            .drop_duplicates("household_id")
        )
        homes_kreis = homes_kreis.merge(
            rs7_per_household[["household_id", "commune_id",
                               "regiostar7", "rs7_label"]],
            on="household_id", how="left",
        )
    else:
        homes_kreis["commune_id"] = pd.NA
        homes_kreis["regiostar7"] = pd.NA
        homes_kreis["rs7_label"] = pd.NA

    persons_kreis = persons.merge(
        homes_kreis[["household_id", "ars5", "kreis_name",
                     "commune_id", "regiostar7", "rs7_label"]],
        on="household_id",
        how="left",
    )

    out = args.analysis_out
    LOGGER.info("Writing intermediate tables + figures to %s", out)

    persons_kreis.to_csv(out / "persons_with_kreis.csv", index=False)

    # --- Demographics + trips + purposes plots (no MiD reference rows). ---
    _plot_demographics(persons_kreis, households, out / "01_demographics.png")
    _plot_trips(trips, out / "02_trips.png")
    _plot_purposes(activities, out / "03_activity_purposes.png")
    _plot_homes_map(homes, activities, kreise, out / "04_homes_map.png")

    # --- Commute distance vs MiD P13. ---
    LOGGER.info("Computing commute distances vs MiD P13")
    commute = _commute_distances(activities, homes_kreis, persons_kreis)
    commute_band = _commute_band_table(commute, mid["P13"])
    commute_band.to_csv(out / "commute_bands_vs_p13.csv", index=False)
    _plot_commute_bands(commute_band, out / "05_commute_distance_p13.png")

    mean_syn = commute.groupby("ars5")["distance_km"].mean()
    mean_mid = mid["P13"].set_index("ars5")["mittel"]
    commute_mean = pd.DataFrame(
        {
            "ars5": mean_syn.index,
            "kreis": mean_syn.index.map(ZGB8),
            "mean_synthetic_km": mean_syn.round(2).values,
            "mean_mid_km": mean_mid.reindex(mean_syn.index).round(2).values,
        }
    )
    commute_mean["delta_km"] = (
        commute_mean["mean_synthetic_km"] - commute_mean["mean_mid_km"]
    ).round(2)
    commute_mean.to_csv(out / "commute_mean_vs_p13.csv", index=False)

    # --- License rate (KBA) vs MiD P17.1. ---
    # MiD 2023 P17.1 reports licence shares for the population aged 14+
    # (Tabelle A, page 87).  We mirror that base population here even
    # though the synthesis floor is LICENSE_MIN_AGE = 18 (BF17 ignored).
    # Because all 14–17-year-olds are deterministically "nein", the
    # synthetic share is structurally lowered by ~the share of that
    # cohort (~4–5 pp) compared to a 14+ MiD reference that includes
    # ~19 % BF17 holders.  The deviation is reported in summary.md.
    LOGGER.info("Computing license rate vs MiD P17.1 (population 14+)")
    lic_pop = persons_kreis[persons_kreis["age"] >= 14]
    lic_syn = (
        lic_pop.groupby("ars5")["has_driving_license"]
        .apply(_bool_share)
        .rename("synthetic_pct")
    )
    lic_mid = mid["P17_1"].set_index("ars5")["ja"]
    lic_tbl = pd.DataFrame(
        {
            "ars5": lic_syn.index,
            "kreis": lic_syn.index.map(ZGB8),
            "synthetic_pct": lic_syn.round(1).values,
            "mid_pct": lic_mid.reindex(lic_syn.index).round(1).values,
        }
    )
    lic_tbl["delta_pp"] = (
        lic_tbl["synthetic_pct"] - lic_tbl["mid_pct"]
    ).round(1)
    lic_tbl.to_csv(out / "license_vs_p17_1.csv", index=False)
    _plot_license(lic_tbl, out / "06_license_rate.png")

    # --- Employment vs MiD P9. ---
    LOGGER.info("Computing employment rate vs MiD P9")
    work_pop = persons_kreis[persons_kreis["age"].between(15, 74)]
    emp_syn = (
        work_pop.groupby("ars5")["employed"]
        .apply(_bool_share)
        .rename("synthetic_pct")
    )
    p9 = mid["P9"].set_index("ars5")
    emp_mid = (
        p9["vollzeit"].fillna(0)
        + p9["teilzeit"].fillna(0)
        + p9["geringfuegig"].fillna(0)
        + p9["sonstiges"].fillna(0)
        + p9["erwerbstaetig_unspec"].fillna(0)
    )
    emp_tbl = pd.DataFrame(
        {
            "ars5": emp_syn.index,
            "kreis": emp_syn.index.map(ZGB8),
            "synthetic_pct": emp_syn.round(1).values,
            "mid_pct": emp_mid.reindex(emp_syn.index).round(1).values,
        }
    )
    emp_tbl["delta_pp"] = (
        emp_tbl["synthetic_pct"] - emp_tbl["mid_pct"]
    ).round(1)
    emp_tbl.to_csv(out / "employment_vs_p9.csv", index=False)
    _plot_employment(emp_tbl, out / "07_employment_rate.png")

    # --- Per-RegioStaR-7 diagnostic (no MiD reference; descriptive only). ---
    # Aggregates the same KPIs (commute distance, license, employment,
    # PT-subscription) across the seven BMV/BBSR Raumtypen so urban vs
    # rural disparities become visible. The MiD 2023 Großraum-BS report
    # has no RS7 breakdown, so this table compares synthetic shares only.
    rs7_tbl = pd.DataFrame()
    if persons_kreis["regiostar7"].notna().any():
        LOGGER.info("Aggregating KPIs per RegioStaR-7 class")
        commute_rs7 = commute.dropna(subset=["distance_km"]).merge(
            persons_kreis[["person_id", "regiostar7", "rs7_label"]],
            on="person_id", how="left",
        )
        commute_mean_rs7 = (
            commute_rs7.dropna(subset=["regiostar7"])
                       .groupby(["regiostar7", "rs7_label"])["distance_km"]
                       .mean().round(2).rename("mean_commute_km")
        )
        lic_rs7 = (
            lic_pop.dropna(subset=["regiostar7"])
                   .groupby(["regiostar7", "rs7_label"])["has_driving_license"]
                   .apply(_bool_share).round(1).rename("license_pct")
        )
        emp_rs7 = (
            work_pop.dropna(subset=["regiostar7"])
                    .groupby(["regiostar7", "rs7_label"])["employed"]
                    .apply(_bool_share).round(1).rename("employment_pct")
        )
        if "has_pt_subscription" in persons_kreis.columns:
            pt_rs7 = (
                persons_kreis.dropna(subset=["regiostar7"])
                             .groupby(["regiostar7", "rs7_label"])["has_pt_subscription"]
                             .apply(_bool_share).round(1).rename("pt_subscription_pct")
            )
        else:
            pt_rs7 = pd.Series(dtype=float, name="pt_subscription_pct")
        n_rs7 = (
            persons_kreis.dropna(subset=["regiostar7"])
                         .groupby(["regiostar7", "rs7_label"]).size()
                         .rename("n_persons")
        )
        rs7_tbl = (
            pd.concat([n_rs7, commute_mean_rs7, lic_rs7, emp_rs7, pt_rs7], axis=1)
              .reset_index()
              .sort_values("regiostar7")
        )
        rs7_tbl.to_csv(out / "kpis_by_regiostar7.csv", index=False)
        _plot_rs7_kpis(rs7_tbl, out / "08_kpis_by_regiostar7.png")

    # --- Secondary-location success. ---
    valid = activities["geometry"].notna() & ~activities["geometry"].is_empty
    by_purpose = pd.DataFrame(
        {
            "total": activities.groupby("purpose").size(),
            "valid_geom": activities[valid].groupby("purpose").size(),
        }
    ).fillna(0).astype(int)
    by_purpose["success_pct"] = (
        100.0 * by_purpose["valid_geom"] / by_purpose["total"]
    ).round(2)
    by_purpose.to_csv(out / "secondary_success.csv")

    # --- report.json ---
    report = {
        "label": args.label,
        "output_dir": str(args.output_dir),
        "prefix": args.prefix,
        "n_persons": int(len(persons)),
        "n_households": int(len(households)),
        "n_trips": int(len(trips)),
        "n_activities": int(len(activities)),
        "unassigned_homes": int(persons_kreis["ars5"].isna().sum()),
        "trips_per_person": float(round(len(trips) / max(len(persons), 1), 4)),
        "commute_mean_km_synth": dict(
            zip(commute_mean["ars5"], commute_mean["mean_synthetic_km"])
        )
        if not commute_mean.empty
        else {},
        "commute_mean_km_mid": dict(
            zip(commute_mean["ars5"], commute_mean["mean_mid_km"])
        )
        if not commute_mean.empty
        else {},
        "license_pct_synth": dict(zip(lic_tbl["ars5"], lic_tbl["synthetic_pct"])),
        "license_pct_mid": dict(zip(lic_tbl["ars5"], lic_tbl["mid_pct"])),
        "employment_pct_synth": dict(zip(emp_tbl["ars5"], emp_tbl["synthetic_pct"])),
        "employment_pct_mid": dict(zip(emp_tbl["ars5"], emp_tbl["mid_pct"])),
        "secondary_success_pct": by_purpose["success_pct"].to_dict(),
        "kpis_by_regiostar7": (
            rs7_tbl.assign(regiostar7=rs7_tbl["regiostar7"].astype(int))
                   .to_dict(orient="records")
            if not rs7_tbl.empty else []
        ),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- summary.md ---
    md_lines: list[str] = [
        f"# MiD 2023 validation — {args.label}",
        "",
        f"- Output directory: `{args.output_dir}`",
        f"- Prefix: `{args.prefix}`",
        f"- Persons: {report['n_persons']:,}",
        f"- Households: {report['n_households']:,}",
        f"- Trips: {report['n_trips']:,}  (trips/person = {report['trips_per_person']})",
        f"- Unassigned homes (no Kreis match): {report['unassigned_homes']}",
        "",
        "## Driver license rate vs MiD P17.1 (% with license, age >= 14)",
        "",
        _df_to_markdown(lic_tbl),
        "",
        "_Note:_ MiD P17.1 base population is 14+. The synthesis floor is "
        "`LICENSE_MIN_AGE = 18` (BF17/begleitetes Fahren intentionally "
        "ignored), so the synthetic share is structurally lowered by the "
        "fraction of 14–17-year-olds with a (BF17) licence — about 19 % "
        "of that cohort per the MiD age margin, i.e. ~1 pp on the total.",
        "",
        "## Employment rate vs MiD P9 (% employed, age 15-74)",
        "",
        _df_to_markdown(emp_tbl),
        "",
        "## Mean commute distance vs MiD P13 (km)",
        "",
        _df_to_markdown(commute_mean),
        "",
    ]
    if not rs7_tbl.empty:
        md_lines += [
            "## KPIs per RegioStaR-7 class (synthetic only — no MiD reference)",
            "",
            _df_to_markdown(rs7_tbl),
            "",
        ]
    (out / "summary.md").write_text("\n".join(md_lines), encoding="utf-8")

    LOGGER.info("Done. Wrote %d files to %s", len(list(out.iterdir())), out)
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
