"""Enrollment-vs-capacity report for the education gravity model.

Pure helpers (enrollment_vs_capacity, level_summary) plus a CLI that reads a run's
cached education_gravity assignment + school facilities and writes:
  - school_enrollment_vs_capacity.csv
  - level_summary.csv

Usage (PowerShell, conda env ``eqasim`` activated)::

    python -m braunschweig.analysis.run_education_validation `
        --working-directory eqasim-data/cache_bs `
        --sampling-rate 0.01 `
        --output-dir eqasim-data/output_bs/analysis/education_validation
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.education_validation")


# ---------------------------------------------------------------------------
# Pure helpers (fully unit-tested)
# ---------------------------------------------------------------------------


def enrollment_vs_capacity(assignments, facilities, sampling_rate):
    """Per-school assigned pupils (scaled to 100% via sampling_rate), capacity,
    and fill_ratio = assigned / capacity.

    Parameters
    ----------
    assignments : DataFrame with columns [location_id, ...]
        One row per assigned pupil in the synthetic sample.
    facilities : DataFrame with columns [school_id, name, level, capacity]
        One row per school.
    sampling_rate : float
        Fraction of the full population represented by ``assignments``.
        Assigned counts are divided by this value to estimate 100% enrollment.

    Returns
    -------
    DataFrame with columns [school_id, name, level, capacity,
                            assigned_pupils, fill_ratio].
    """
    counts = (assignments.groupby("location_id").size()
              .rename("sampled").reset_index()
              .rename(columns={"location_id": "school_id"}))
    out = facilities.merge(counts, on="school_id", how="left")
    out["sampled"] = out["sampled"].fillna(0.0)
    out["assigned_pupils"] = out["sampled"] / float(sampling_rate)
    out["fill_ratio"] = out["assigned_pupils"] / out["capacity"]
    return out[["school_id", "name", "level", "capacity",
                "assigned_pupils", "fill_ratio"]]


def level_summary(assignments):
    """Per-level pupil count + mean/median straight-line commute km.

    Parameters
    ----------
    assignments : DataFrame with columns [level, commute_km, ...]
        One row per assigned pupil.

    Returns
    -------
    DataFrame with columns [level, n_pupils, mean_commute_km,
                            median_commute_km].
    """
    g = assignments.groupby("level")
    return pd.DataFrame({
        "level": list(g.groups.keys()),
        "n_pupils": g.size().values,
        "mean_commute_km": g["commute_km"].mean().values,
        "median_commute_km": g["commute_km"].median().values,
    })


# ---------------------------------------------------------------------------
# CLI glue
# ---------------------------------------------------------------------------


def _load_stage(working_directory, stage_name):
    """Load the most-recently-written synpp pickle for ``stage_name``.

    Pickle files are named ``<stage_name>__<hash>.p``; glob is used to find
    them and the most recently modified one is returned.

    Raises
    ------
    RuntimeError
        If no pickle for the stage is found in ``working_directory``.
    """
    pattern = os.path.join(working_directory, f"{stage_name}__*.p")
    hits = glob.glob(pattern)
    # Fallback: some synpp versions use the bare name without hash suffix.
    if not hits:
        direct = os.path.join(working_directory, f"{stage_name}.p")
        if os.path.exists(direct):
            hits = [direct]
    if not hits:
        raise RuntimeError(
            f"[run_education_validation] no cached pickle found for stage "
            f"'{stage_name}' in '{working_directory}'.  "
            f"Run the synpp pipeline first (stage must be complete)."
        )
    path = max(hits, key=os.path.getmtime)
    LOGGER.info("Loading stage '%s' from %s", stage_name, path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _build(working_directory, sampling_rate, output_dir):
    """Compute enrollment and distance summaries from the synpp cache.

    Loads three cached stages, computes the straight-line home-to-school
    commute distance for each assigned pupil, then writes two CSV files
    into ``output_dir``.

    Parameters
    ----------
    working_directory : str or Path
        synpp working/cache directory containing stage pickle files.
    sampling_rate : float
        Fraction of the full population represented in the cache.
    output_dir : str or Path
        Destination directory for output CSV files (created if absent).
    """
    working_directory = str(working_directory)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load cached stages. ---
    assignments = _load_stage(
        working_directory, "braunschweig.synthesis.locations.education_gravity"
    )
    facilities_raw = _load_stage(
        working_directory, "braunschweig.data.schools.facilities"
    )
    homes = _load_stage(
        working_directory, "synthesis.population.spatial.home.locations"
    )

    # --- Normalise facilities to the expected schema. ---
    # The facilities stage returns a GeoDataFrame; we only need the tabular part.
    facilities = pd.DataFrame(facilities_raw).copy()
    if "name" not in facilities.columns:
        # The raw NDS schools CSV may not expose 'name' after build_facilities_frame;
        # fall back to school_id so the output remains consistent.
        facilities["name"] = facilities["school_id"]

    # --- Compute straight-line home->school commute_km. ---
    # assignments is a GeoDataFrame[person_id, commune_id, location_id, geometry]
    # homes is a GeoDataFrame/DataFrame[household_id, geometry, ...]
    # We need person_id -> household_id from the persons cache (or we join via
    # home geometry directly through household). Because assignments has no
    # household_id column, we need the person->household mapping from the
    # enriched population (or sampled population) stage.
    try:
        pop = _load_stage(working_directory, "synthesis.population.sampled")
    except RuntimeError:
        # Older cache layouts may store the sampled population under a
        # different key; fall back to None and skip home-distance computation.
        pop = None

    asgn_df = pd.DataFrame(assignments).copy()

    if pop is not None and hasattr(homes, "geometry"):
        import geopandas as gpd

        # Build person_id -> household_id mapping.
        pop_df = pd.DataFrame(pop)
        if "person_id" in pop_df.columns and "household_id" in pop_df.columns:
            ph = pop_df[["person_id", "household_id"]].drop_duplicates("person_id")
            asgn_df = asgn_df.merge(ph, on="person_id", how="left")

        # Build household_id -> home geometry mapping.
        homes_df = pd.DataFrame(homes)
        if "household_id" in homes_df.columns:
            home_geom = homes_df[["household_id", "geometry"]].drop_duplicates(
                "household_id"
            )
            asgn_df = asgn_df.merge(home_geom, on="household_id", how="left",
                                    suffixes=("_school", "_home"))

            # Compute straight-line distance if both geometry columns are present.
            if "geometry_school" in asgn_df.columns and "geometry_home" in asgn_df.columns:
                def _dist_km(row):
                    g_home = row["geometry_home"]
                    g_school = row["geometry_school"]
                    if g_home is None or g_school is None:
                        return np.nan
                    try:
                        return g_home.distance(g_school) / 1000.0
                    except Exception:
                        return np.nan

                asgn_df["commute_km"] = asgn_df.apply(_dist_km, axis=1)
            else:
                # geometry column was not suffixed (no conflict); try direct.
                if "geometry" in asgn_df.columns:
                    # geometry is the school point; home geometry merged as
                    # 'geometry_home' only if rename happened.
                    asgn_df["commute_km"] = np.nan
                else:
                    asgn_df["commute_km"] = np.nan
        else:
            asgn_df["commute_km"] = np.nan
    else:
        asgn_df["commute_km"] = np.nan

    # Attach education level from facilities (location_id == school_id for NDS
    # levels; OSM-assigned location_ids use a different scheme but are still
    # included in the enrollment count).
    level_map = facilities.set_index("school_id")["level"].to_dict()
    if "level" not in asgn_df.columns:
        asgn_df["level"] = asgn_df["location_id"].map(level_map).fillna("other")

    # --- Write outputs. ---
    ev = enrollment_vs_capacity(asgn_df, facilities, sampling_rate)
    ev_path = output_dir / "school_enrollment_vs_capacity.csv"
    ev.to_csv(ev_path, index=False)
    LOGGER.info("Wrote %s (%d rows)", ev_path, len(ev))

    # level_summary is only meaningful when commute_km is available.
    ls = level_summary(asgn_df.dropna(subset=["commute_km"]))
    ls_path = output_dir / "level_summary.csv"
    ls.to_csv(ls_path, index=False)
    LOGGER.info("Wrote %s (%d rows)", ls_path, len(ls))

    # Optional figures (matplotlib); skipped gracefully when not installed or
    # when running headless without a display.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Fill-ratio scatter: capacity vs assigned_pupils.
        fig, ax = plt.subplots(figsize=(8, 6))
        for lvl, grp in ev.groupby("level"):
            ax.scatter(grp["capacity"], grp["assigned_pupils"],
                       label=lvl, alpha=0.7, s=20)
        cap_max = ev["capacity"].max() if not ev.empty else 1.0
        ax.plot([0, cap_max], [0, cap_max], "k--", linewidth=0.8,
                label="full capacity")
        ax.set_xlabel("School capacity (pupils)")
        ax.set_ylabel("Assigned pupils (full population)")
        ax.set_title("Enrollment vs. capacity per school")
        ax.legend(fontsize=8)
        fig.tight_layout()
        scatter_path = output_dir / "enrollment_vs_capacity_scatter.png"
        fig.savefig(scatter_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        LOGGER.info("Wrote %s", scatter_path)

        # Commute-distance histogram per level (only if distances available).
        valid_km = asgn_df.dropna(subset=["commute_km"])
        if not valid_km.empty:
            levels_sorted = sorted(valid_km["level"].unique())
            fig, axes = plt.subplots(
                1, len(levels_sorted),
                figsize=(4 * len(levels_sorted), 3.5),
                sharey=False,
            )
            if len(levels_sorted) == 1:
                axes = [axes]
            for ax, lvl in zip(axes, levels_sorted):
                sub = valid_km[valid_km["level"] == lvl]["commute_km"]
                ax.hist(sub.values, bins=30, color="tab:blue", alpha=0.8)
                ax.set_title(lvl)
                ax.set_xlabel("commute distance (km)")
                ax.set_ylabel("pupils")
            fig.suptitle("Home-to-school commute distance distribution")
            fig.tight_layout()
            hist_path = output_dir / "commute_distance_histogram.png"
            fig.savefig(hist_path, dpi=120, bbox_inches="tight")
            plt.close(fig)
            LOGGER.info("Wrote %s", hist_path)
    except Exception as exc:
        LOGGER.warning("Could not produce figures: %s", exc)

    return output_dir


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------


def _parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--working-directory",
        required=True,
        help="synpp working/cache directory containing stage pickle files.",
    )
    ap.add_argument(
        "--sampling-rate",
        type=float,
        required=True,
        help="Fraction of the full population represented in the cache "
             "(e.g. 0.01 for a 1%% sample).",
    )
    ap.add_argument(
        "--output-dir",
        required=False,
        default=None,
        help="Destination directory for output files.  "
             "Defaults to <working-directory>/analysis/education_validation/.",
    )
    ns = ap.parse_args(argv)

    working_directory = Path(ns.working_directory).resolve()
    if not working_directory.is_dir():
        ap.error(f"--working-directory does not exist: {working_directory}")

    if ns.sampling_rate <= 0.0 or ns.sampling_rate > 1.0:
        ap.error(
            f"--sampling-rate must be in (0, 1]; got {ns.sampling_rate}"
        )

    output_dir = (
        Path(ns.output_dir).resolve()
        if ns.output_dir is not None
        else working_directory / "analysis" / "education_validation"
    )
    return ns.working_directory, ns.sampling_rate, output_dir


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    working_directory, sampling_rate, output_dir = _parse_args(argv)
    out = _build(working_directory, sampling_rate, output_dir)
    print(f"[run_education_validation] Wrote outputs to: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
