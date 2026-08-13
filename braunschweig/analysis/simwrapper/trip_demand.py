"""Spatial-demand tab: trip-level extraction from ``eqasim_trips.csv`` (issue #267 split).

This module holds the pure helper functions that turn the full
``eqasim_trips.csv`` DataFrame into the two slim, tab-specific views used
across the SimWrapper spatial dashboard -- origin/destination coordinates for
the hexagon density map (:func:`_trips_xy`) and purpose-to-mode trip counts
for the behaviour sankey (:func:`_purpose_to_mode`) -- plus the tab emitter
that writes the hexagon map itself (:func:`emit_spatial_demand`).

``_purpose_to_mode`` is consumed by ``emit_behaviour``, which still lives in
the :mod:`braunschweig.analysis.simwrapper.spatial_export` facade (a later
task of this split); the facade imports it back from here (facade -> sibling
is the allowed direction, never the reverse).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from braunschweig.analysis.freight_filter import drop_freight_agents
from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")


# ---------------------------------------------------------------------------
# Pure helper functions (testable without disk I/O)
# ---------------------------------------------------------------------------

def _trips_xy(df: pd.DataFrame) -> pd.DataFrame:
    """Return a slim DataFrame of origin/destination x/y coordinates.

    Filters out:
    - Rows where ``mode == "outside"`` (cordon marker trips with no internal geometry).
    - Rows where ``origin_x`` is null (no valid origin coordinate).

    Retains rows where ``destination_x`` is null (origin still valid; null
    destination is written as NaN in the output CSV).

    Args:
        df: Full eqasim_trips DataFrame (sep=";").  Must contain columns
            ``origin_x``, ``origin_y``, ``destination_x``, ``destination_y``,
            ``mode``.

    Returns:
        DataFrame with exactly four columns:
        ``origin_x``, ``origin_y``, ``destination_x``, ``destination_y``.
    """
    mask = df["mode"].ne("outside") & df["origin_x"].notna()
    subset = df.loc[mask, ["origin_x", "origin_y", "destination_x", "destination_y"]]
    return subset.reset_index(drop=True)


def _purpose_to_mode(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate trip counts by (following_purpose, mode) for the sankey.

    Drops rows where ``mode == "outside"`` so external cordon trips do not
    appear in the flow diagram.

    Args:
        df: Full eqasim_trips DataFrame (sep=";").  Must contain columns
            ``following_purpose`` and ``mode``.

    Returns:
        DataFrame with columns ``from``, ``to``, ``value`` (trip count),
        one row per (following_purpose, mode) pair present in the data.
        Sorted descending by ``value`` for stable output.
    """
    filtered = df[df["mode"].ne("outside")].copy()
    counts = (
        filtered.groupby(["following_purpose", "mode"], sort=True)
        .size()
        .reset_index(name="value")
        .rename(columns={"following_purpose": "from", "mode": "to"})
        .sort_values("value", ascending=False)
        .reset_index(drop=True)
    )
    return counts


# ---------------------------------------------------------------------------
# Tab 1: Spatial demand (hexagons)
# ---------------------------------------------------------------------------

def emit_spatial_demand(
    sim_output_dir: Path | None,
    folder: Path,
) -> "dict[str, Any] | None":
    """Write ``trips_xy.csv`` and return the Spatial demand dashboard tab.

    Reads ``eqasim_trips.csv`` from ``sim_output_dir`` (the resolved
    ``simulation_output/`` directory, e.g. as returned by
    ``build_dashboard._find_sim_output``).  Keeps origin/destination x/y for
    all trips except cordon ``outside`` trips.  Returns ``None`` with a
    WARNING when the file is absent.

    File written: ``<folder>/trips_xy.csv``
    (columns: origin_x, origin_y, destination_x, destination_y).

    Args:
        sim_output_dir: Resolved path to the MATSim ``simulation_output/``
            directory, or ``None`` when the sim output could not be located.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict or ``None``.
    """
    trips_path: Path | None = None
    if sim_output_dir is not None:
        trips_path = Path(sim_output_dir) / "eqasim_trips.csv"
        if not trips_path.exists():
            trips_path = None

    if trips_path is None:
        LOGGER.warning(
            "[spatial_demand] eqasim_trips.csv not found in %s -- "
            "spatial demand tab skipped",
            sim_output_dir,
        )
        return None

    df = pd.read_csv(trips_path, sep=";")
    df = drop_freight_agents(df, label="spatial_demand")
    xy = _trips_xy(df)
    LOGGER.info("[spatial_demand] %d trips after filtering (mode!=outside, origin_x notna)", len(xy))

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    xy.to_csv(folder / "trips_xy.csv", index=False, encoding="utf-8")
    LOGGER.info("[spatial_demand] wrote trips_xy.csv (%d rows)", len(xy))

    return w.dashboard(
        "Spatial demand",
        "Trip origins & destinations (hexagon density)",
        {
            # One big map card per row (full width) so it renders large.
            "hex": [
                w.card_hexagons(
                    "Trip origins and destinations",
                    "trips_xy.csv",
                    from_x="origin_x",
                    from_y="origin_y",
                    to_x="destination_x",
                    to_y="destination_y",
                    aggregation_name="Trips",
                    from_title="Origins",
                    to_title="Destinations",
                    radius=300,
                    height=13,
                    description=(
                        "Each hexagon colour encodes trip count. "
                        "Select 'Origins' or 'Destinations' in the panel."
                    ),
                )
            ]
        },
    )
