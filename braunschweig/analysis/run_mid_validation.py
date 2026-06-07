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
  5. computes education-trip distance per RegioStaR-7 + level vs MiD
     Tabelle 43 (the targets the education gravity slopes were calibrated to),
  6. writes a battery of PNGs + per-table CSVs + a `report.json` that
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from braunschweig.analysis import spatial
from braunschweig.data.mid.school_distance import build_target_table

LOGGER = logging.getLogger("braunschweig.analysis.mid_validation")

# Re-exported from spatial so callers and tests can import them from here
# while the single source of truth lives in braunschweig.analysis.spatial.
REPO_ROOT = spatial.REPO_ROOT
ZGB8 = spatial.ZGB8

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

MID_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"

# eqasim main mode -> MiD P12_1 category, kept consistent with
# braunschweig.analysis.dashboard.build_dashboard.MODE_LABEL.  Only the four
# MiD P12_1 columns (auto / oeffentlich / fahrrad / zu_fuss) have a reference,
# so car_passenger is folded into "Car" for the commute comparison.
MODE_TO_MID: dict[str, str] = {
    "car": "Car",
    "car_passenger": "Car",
    "pt": "PT",
    "bicycle": "Bicycle",
    "walk": "Walk",
}

# Order in which the four comparable MiD P12_1 modes are reported.
MID_MODE_ORDER: list[str] = ["Car", "PT", "Bicycle", "Walk"]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Args:
    output_dir: Path
    prefix: str
    analysis_out: Path
    label: str
    sim_cache: Path | None


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
    ap.add_argument(
        "--sim-cache",
        required=False,
        default=None,
        help="synpp cache directory containing matsim.simulation.run__*.cache/"
        "simulation_output/eqasim_trips.csv. When given, the all-trip modal "
        "split is read from the MATSim simulation output (the eqasim pipeline "
        "trips.csv carries no mode). Omitted -> the modal-split block is skipped.",
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

    sim_cache = Path(ns.sim_cache).resolve() if ns.sim_cache is not None else None
    if sim_cache is not None and not sim_cache.is_dir():
        ap.error(f"--sim-cache does not exist: {sim_cache}")

    return _Args(
        output_dir=output_dir,
        prefix=prefix,
        analysis_out=analysis_out,
        label=ns.label or prefix.rstrip("_"),
        sim_cache=sim_cache,
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


def mode_share(modes: pd.Series) -> dict[str, float]:
    """Percentage share of each transport mode in ``modes``.

    Returns a dict mapping mode name -> share in percent over the
    NaN-dropped observations.  The shares sum to 100 for any non-empty
    input and to nothing (empty dict) for empty / all-NaN input, so the
    helper is safe to call before knowing whether a run carries mode
    information at all.
    """
    valid = modes.dropna()
    if valid.empty:
        return {}
    counts = valid.value_counts(normalize=True) * 100.0
    return {str(mode): float(share) for mode, share in counts.items()}


def _bool_share(series: pd.Series) -> float:
    """Share of truthy values in ``series`` as a percentage.

    Accepts the eqasim convention where booleans are stringified
    (``"True"`` / ``"False"`` / ``"1"`` / ``"0"``).
    """
    if len(series) == 0:
        return float("nan")
    truthy = series.astype(str).str.lower().isin(["true", "1", "yes"])
    return float(100.0 * truthy.mean())


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


def _find_sim_trips(sim_cache: Path | None) -> pd.DataFrame | None:
    """Load the MATSim simulation-output trips (mode + purpose), or None.

    The eqasim pipeline trips.csv has no realised mode; the mode is written by
    the MATSim mobility simulation to ``matsim.simulation.run__*.cache/
    simulation_output/eqasim_trips.csv``.  The lookup mirrors
    ``braunschweig.analysis.dashboard.build_dashboard._find_sim_output`` so both
    analysis entry points resolve the same file.  Returns None (and the modal-
    split block is skipped) when no --sim-cache is given or the file is absent.
    """
    if sim_cache is None:
        return None
    for cache_dir in sim_cache.glob("matsim.simulation.run__*.cache"):
        trips_path = cache_dir / "simulation_output" / "eqasim_trips.csv"
        if trips_path.exists():
            LOGGER.info("Reading realised trip modes from %s", trips_path)
            return pd.read_csv(trips_path, sep=";")
    LOGGER.warning(
        "No simulation_output/eqasim_trips.csv under %s; modal-split block skipped.",
        sim_cache,
    )
    return None


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


# ---------------------------------------------------------------------------
# Overall (all-trip) modal split  (model-improvement item #10)
# ---------------------------------------------------------------------------
#
# The eqasim *pipeline* trips.csv carries no mode (mode is assigned by the
# MATSim mobility simulation), so the realised modal split is read from the
# simulation output `eqasim_trips.csv` when a --sim-cache is supplied.  There
# is currently NO committed all-trip modal-split reference table for the ZGB
# region; this block therefore *measures* the synthetic distribution fully and
# only compares the work-commute subset to the committed MiD P12_1 table.  The
# all-trip figures are reported as awaiting an external SrV-Braunschweig / MiD
# all-trip reference.


def _mode_share_table(
    trips: pd.DataFrame, mid_p12_1: pd.DataFrame | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build the all-trip modal-split tables from a mode-carrying trips frame.

    Returns three frames:

    * ``all_tbl``       — overall modal split (``mode``, ``n_trips``,
      ``share_pct``); ``share_pct`` sums to 100.
    * ``by_purpose``    — modal split per ``following_purpose`` (each purpose's
      ``share_pct`` sums to 100), present only if the column exists.
    * ``commute_cmp``   — work-commute synthetic vs MiD P12_1 comparison over
      the four comparable MiD modes (``mode``, ``synthetic_pct``, ``mid_pct``,
      ``delta_pp``).  Empty when either the work subset or the P12_1 reference
      is unavailable.

    All three frames are empty when ``trips`` has no ``mode`` column, so the
    feature degrades gracefully on runs without a simulation output.
    """
    empty = pd.DataFrame()
    if "mode" not in trips.columns:
        return empty, empty, empty

    # Drop cordon out-of-scope legs: "outside" is the eqasim marker for the part of
    # a trip beyond the cordon (set by the scenario cutter), not a real transport
    # mode. Excluding it keeps the modal split about real modes inside the study
    # area. No-op when the cordon is off ("outside" absent).
    trips = trips[trips["mode"] != "outside"]

    # --- Overall (all-trip) modal split. ---
    overall = mode_share(trips["mode"])
    n_by_mode = trips["mode"].dropna().value_counts()
    all_tbl = pd.DataFrame(
        {
            "mode": list(overall.keys()),
            "n_trips": [int(n_by_mode.get(m, 0)) for m in overall],
            "share_pct": [round(v, 2) for v in overall.values()],
        }
    )

    # --- Modal split per following_purpose (descriptive, no reference). ---
    by_rows: list[dict[str, Any]] = []
    if "following_purpose" in trips.columns:
        for purpose, sub in trips.groupby("following_purpose"):
            for mode, share in mode_share(sub["mode"]).items():
                by_rows.append(
                    {
                        "following_purpose": str(purpose),
                        "mode": mode,
                        "n_trips": int((sub["mode"] == mode).sum()),
                        "share_pct": round(share, 2),
                    }
                )
    by_purpose = pd.DataFrame(
        by_rows, columns=["following_purpose", "mode", "n_trips", "share_pct"]
    )

    # --- Work-commute subset vs MiD P12_1 (the only referenced comparison). ---
    commute_cmp = empty
    if mid_p12_1 is not None and "following_purpose" in trips.columns:
        work = trips[trips["following_purpose"] == "work"]
        if not work.empty:
            # Map eqasim modes to the four MiD P12_1 categories and aggregate.
            syn_raw = mode_share(work["mode"])
            syn_mid: dict[str, float] = {label: 0.0 for label in MID_MODE_ORDER}
            for mode, share in syn_raw.items():
                label = MODE_TO_MID.get(mode)
                if label is not None:
                    syn_mid[label] += share
            ref_row = mid_p12_1[mid_p12_1["ars5"] == "03ZGB"]
            mid_cols = {"Car": "auto", "PT": "oeffentlich",
                        "Bicycle": "fahrrad", "Walk": "zu_fuss"}
            ref = ref_row.iloc[0] if not ref_row.empty else None
            rows: list[dict[str, Any]] = []
            for label in MID_MODE_ORDER:
                mid_val = (
                    float(ref[mid_cols[label]])
                    if ref is not None and mid_cols[label] in ref_row.columns
                    else float("nan")
                )
                rows.append(
                    {
                        "mode": label,
                        "synthetic_pct": round(syn_mid[label], 1),
                        "mid_pct": round(mid_val, 1) if mid_val == mid_val else mid_val,
                        "delta_pp": round(syn_mid[label] - mid_val, 1)
                        if mid_val == mid_val else float("nan"),
                    }
                )
            commute_cmp = pd.DataFrame(
                rows, columns=["mode", "synthetic_pct", "mid_pct", "delta_pp"]
            )

    return all_tbl, by_purpose, commute_cmp


# ---------------------------------------------------------------------------
# Education-trip distance vs MiD Tabelle 43 (per RegioStaR-7, per level)
# ---------------------------------------------------------------------------

# Routed/straight-line detour factor used to convert the MiD Tabelle 43 routed
# targets to a straight-line equivalent. Kept equal to the calibration default
# in scripts/calibrate_education_slopes.py so the post-sim validation compares
# against the same target the slopes were fitted to.
_EDU_DETOUR_FACTOR = 1.3

# Pupil-age -> school level, matching braunschweig.data.mid.school_distance
# AGEGROUP_TO_LEVEL so realised education-trip distances are validated on the
# same basis the gravity slopes were calibrated to. BBS / university pupils
# (ages 18+) have no MiD Tabelle 43 target and are therefore not validated here.
_EDU_AGE_LEVELS: list[tuple[int, int, str]] = [
    (0, 6, "kindergarten"),
    (7, 10, "grundschule"),
    (11, 13, "sekundar_1"),
    (14, 17, "oberstufe"),
]


def education_level_for_age(age: Any) -> str | None:
    """MiD Tabelle 43 school level for a pupil age, or None outside its scope."""
    if pd.isna(age):
        return None
    a = int(age)
    for lower, upper, level in _EDU_AGE_LEVELS:
        if lower <= a <= upper:
            return level
    return None


def _education_distances(
    activities: gpd.GeoDataFrame,
    homes_kreis: gpd.GeoDataFrame,
    persons_kreis: pd.DataFrame,
) -> pd.DataFrame:
    """Straight-line home->education distance (km) per pupil, with home RS7 and
    the MiD Tabelle 43 level derived from the pupil's age."""
    education = (
        activities[activities["purpose"] == "education"]
        .drop_duplicates("person_id")[["person_id", "household_id", "geometry"]]
        .rename(columns={"geometry": "edu_geom"})
    )
    home_lookup = (
        homes_kreis[["household_id", "geometry"]]
        .rename(columns={"geometry": "home_geom"})
        .drop_duplicates("household_id")
    )
    education = education.merge(home_lookup, on="household_id", how="inner")
    if education.empty:
        return education.assign(distance_km=[], regiostar7=[], age=[], level=[])
    education["distance_km"] = education.apply(
        lambda r: r["home_geom"].distance(r["edu_geom"]) / 1000.0, axis=1
    )
    education = education.merge(
        persons_kreis[["person_id", "age", "regiostar7"]],
        on="person_id",
        how="left",
    )
    education["level"] = education["age"].map(education_level_for_age)
    return education


def _education_distance_table(
    education: pd.DataFrame, t43_raw: pd.DataFrame, detour_factor: float
) -> pd.DataFrame:
    """Per (RegioStaR-7, level): pupil count, mean synthetic straight-line km,
    the MiD Tabelle 43 straight-line target, and the signed deviation."""
    targets = build_target_table(t43_raw, detour_factor)
    valid = education.dropna(subset=["level", "regiostar7"])
    rows: list[dict[str, Any]] = []
    for (rs7, level), sub in valid.groupby(["regiostar7", "level"]):
        target = targets[
            (targets["regiostar7"] == int(rs7)) & (targets["level"] == level)
        ]
        if target.empty:
            continue
        rows.append(
            {
                "regiostar7": int(rs7),
                "level": level,
                "n_pupils": int(len(sub)),
                "mean_synthetic_km": round(float(sub["distance_km"].mean()), 2),
                "target_km": round(float(target["target_km"].iloc[0]), 2),
                "routed_km": round(float(target["routed_km"].iloc[0]), 2),
            }
        )
    table = pd.DataFrame(
        rows,
        columns=["regiostar7", "level", "n_pupils",
                 "mean_synthetic_km", "target_km", "routed_km"],
    )
    if not table.empty:
        table["delta_km"] = (
            table["mean_synthetic_km"] - table["target_km"]
        ).round(2)
    return table


def _load_t43() -> pd.DataFrame | None:
    """Load the committed MiD Tabelle 43 reference, or None if absent (the
    education-distance validation block is then skipped)."""
    path = MID_DIR / "mid2023_T43_school_distance_by_rs7.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


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
    kreise = spatial.load_kreise(homes.crs)
    mid = _load_mid()

    LOGGER.info("Spatial-joining home points to ZGB-8 Kreise + Gemeinden (assign_geographies)")
    homes_kreis = spatial.assign_geographies(homes, kreise=kreise)

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

    # --- Education-trip distance vs MiD Tabelle 43 (per RegioStaR-7, level). ---
    # Validates the realised home->education distances of a finished run against
    # the same per-(RS7, level) targets the education gravity slopes were
    # calibrated to. Skipped if the T43 reference is not present.
    LOGGER.info("Computing education-trip distances vs MiD Tabelle 43")
    t43_raw = _load_t43()
    if t43_raw is not None:
        education = _education_distances(activities, homes_kreis, persons_kreis)
        education_table = _education_distance_table(
            education, t43_raw, _EDU_DETOUR_FACTOR
        )
        education_table.to_csv(out / "education_distance_vs_t43.csv", index=False)
    else:
        education_table = pd.DataFrame()

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

    # --- Overall (all-trip) modal split (model-improvement item #10). ---
    # Measures the realised modal split from the MATSim simulation output (the
    # eqasim pipeline trips.csv has no mode).  The all-trip split has NO
    # external reference yet; only the work-commute subset is compared to MiD
    # P12_1.  Skipped entirely when no --sim-cache / no eqasim_trips.csv.
    LOGGER.info("Computing all-trip modal split (sim-cache=%s)", args.sim_cache)
    sim_trips = _find_sim_trips(args.sim_cache)
    mode_all_tbl = pd.DataFrame()
    mode_by_purpose_tbl = pd.DataFrame()
    mode_commute_cmp = pd.DataFrame()
    if sim_trips is not None:
        mode_all_tbl, mode_by_purpose_tbl, mode_commute_cmp = _mode_share_table(
            sim_trips, mid.get("P12_1")
        )
        if not mode_all_tbl.empty:
            mode_all_tbl.to_csv(out / "mode_share_all.csv", index=False)
        if not mode_by_purpose_tbl.empty:
            mode_by_purpose_tbl.to_csv(out / "mode_share_by_purpose.csv", index=False)
        if not mode_commute_cmp.empty:
            mode_commute_cmp.to_csv(out / "mode_share_commute_vs_p12_1.csv", index=False)

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
        "education_distance_vs_t43": (
            education_table.to_dict(orient="records")
            if not education_table.empty else []
        ),
        "kpis_by_regiostar7": (
            rs7_tbl.assign(regiostar7=rs7_tbl["regiostar7"].astype(int))
                   .to_dict(orient="records")
            if not rs7_tbl.empty else []
        ),
        # Overall (all-trip) modal split — measurement only, no all-trip
        # reference yet; the work subset is compared to MiD P12_1 separately.
        "mode_share_pct": (
            dict(zip(mode_all_tbl["mode"], mode_all_tbl["share_pct"]))
            if not mode_all_tbl.empty else {}
        ),
        "mode_share_commute_vs_p12_1": (
            mode_commute_cmp.to_dict(orient="records")
            if not mode_commute_cmp.empty else []
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
    if not education_table.empty:
        md_lines += [
            "## Education-trip distance vs MiD Tabelle 43 "
            "(km, straight-line, per RegioStaR-7 + level)",
            "",
            "_Realised home->education distances of school-age pupils (ages "
            "0-17, the T43 scope) compared to the per-(RS7, level) targets the "
            "education gravity slopes were calibrated to (routed / detour "
            f"{_EDU_DETOUR_FACTOR}). BBS / university pupils are out of T43 "
            "scope and not shown._",
            "",
            _df_to_markdown(education_table),
            "",
        ]
    if not rs7_tbl.empty:
        md_lines += [
            "## KPIs per RegioStaR-7 class (synthetic only — no MiD reference)",
            "",
            _df_to_markdown(rs7_tbl),
            "",
        ]
    # Overall (all-trip) modal split — measurement addition (item #10).
    if not mode_all_tbl.empty:
        md_lines += [
            "## Overall (all-trip) modal split — measurement only",
            "",
            "_Realised main-mode share over ALL trips of the run, read from the "
            "MATSim simulation output (`eqasim_trips.csv`). There is currently "
            "**no committed all-trip modal-split reference** for the ZGB region, "
            "so these figures are reported for measurement only and are awaiting "
            "an external SrV-Braunschweig / MiD all-trip modal split. Only the "
            "work-commute subset below is compared to a reference (MiD P12_1)._",
            "",
            _df_to_markdown(mode_all_tbl),
            "",
        ]
        if not mode_by_purpose_tbl.empty:
            md_lines += [
                "### Modal split per following activity purpose (descriptive)",
                "",
                _df_to_markdown(mode_by_purpose_tbl),
                "",
            ]
        if not mode_commute_cmp.empty:
            md_lines += [
                "### Work-commute modal split vs MiD P12_1 (ZGB total)",
                "",
                "_The only referenced modal-split comparison. MiD P12_1 reports "
                "'every mode used per commute' (rows can sum >100 %); the "
                "synthetic column is the simulated MAIN mode, so an exact match "
                "is not expected. car_passenger is folded into Car._",
                "",
                _df_to_markdown(mode_commute_cmp),
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
