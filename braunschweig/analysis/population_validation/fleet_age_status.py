"""Validation panel: vehicle age × economic-status summary.

Compares the SYNTHETIC fleet's age-by-status distribution against the MiD 2023
reference table (``mid2023_age_by_segment_status.csv``).

Two metrics per economic_status:
  * ``mean_age_yr`` -- synthetic mean vehicle age in years (uses the midpoint
    mapping from :data:`AGE_BAND_MIDPOINT_YEARS`).
  * ``under_5_share`` -- share of vehicles in the ``under_5`` age band.

Both are reported alongside the MiD reference values (base-weighted pooled over
all segments) so the income-age coupling can be eyeballed and regression-checked.

Read-only analysis; never modifies synthesis outputs.

Data-absent-safe: returns an empty DataFrame (zero rows, correct columns) when
the vehicles frame is None OR is missing the ``age`` / ``economic_status``
columns. Every such skip is logged at WARNING level.
"""

from __future__ import annotations

import logging

import pandas as pd

LOGGER = logging.getLogger(
    "braunschweig.analysis.population_validation.fleet_age_status"
)

#: Canonical status ordering, low -> high.
STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")

#: Age-band midpoint in years (mirrors fleet_sampling_de.AGE_BAND_MIDPOINT_YEARS).
AGE_BAND_MIDPOINT_YEARS: dict[str, float] = {
    "under_5": 2.0,
    "5_to_9": 7.0,
    "10_to_14": 12.0,
    "15_to_19": 17.0,
    "20_to_24": 22.0,
    "25_to_29": 27.0,
    "30_plus": 32.0,
}

#: Columns emitted by :func:`build_panel`.
PANEL_COLUMNS = (
    "economic_status",
    "mid_mean_age_yr",
    "mid_under_5_share",
    "synthetic_mean_age_yr",
    "synthetic_under_5_share",
    "delta_mean_age_yr",
    "delta_under_5_share_pp",
)


def _mid_reference(data_path: str) -> pd.DataFrame:
    """Compute base-weighted MiD reference values per economic_status.

    Pools over all (segment, status) cells, weighting by ``base_weighted``.
    Returns a DataFrame indexed on ``status`` with columns
    ``mid_mean_age_yr`` and ``mid_under_5_share``.
    """
    from braunschweig.data.kba import fleet_tables as ft

    df = ft.load_mid_age_by_segment_status(data_path)

    rows = []
    for status in STATUS_ORDER:
        sub = df[df["status"] == status]
        if sub.empty:
            LOGGER.warning(
                "[fleet_age_status] MiD table has no rows for status=%r; "
                "reference values will be NaN for that status.", status,
            )
            rows.append({"status": status,
                         "mid_mean_age_yr": float("nan"),
                         "mid_under_5_share": float("nan")})
            continue

        # Base-weighted mean age and under_5 share.
        total_weight = 0.0
        weighted_mean_age = 0.0
        weighted_under5 = 0.0

        # Single string key (not a 1-element list): a list-key groupby yields a
        # scalar key on pandas 1.5.x (the eqasim runtime) and a 1-tuple on 2.x/3.x,
        # so the tuple-unpack `(seg,)` raises ValueError at runtime. The string form
        # yields a scalar on every version.
        for seg, grp in sub.groupby("segment"):
            # Each segment has one row per age_band; pivot to a share dict.
            shares = grp.set_index("age_band")["share"]
            bw = float(grp["base_weighted"].iloc[0])
            if bw <= 0:
                continue
            # mean age for this (segment, status) cell.
            cell_mean_age = sum(
                AGE_BAND_MIDPOINT_YEARS.get(band, 0.0) * float(shares.get(band, 0.0))
                for band in AGE_BAND_MIDPOINT_YEARS
            )
            cell_under5 = float(shares.get("under_5", 0.0))
            weighted_mean_age += bw * cell_mean_age
            weighted_under5 += bw * cell_under5
            total_weight += bw

        if total_weight <= 0:
            rows.append({"status": status,
                         "mid_mean_age_yr": float("nan"),
                         "mid_under_5_share": float("nan")})
        else:
            rows.append({
                "status": status,
                "mid_mean_age_yr": weighted_mean_age / total_weight,
                "mid_under_5_share": weighted_under5 / total_weight,
            })

    return pd.DataFrame(rows).set_index("status")


def build_panel(vehicles: pd.DataFrame | None, data_path: str) -> pd.DataFrame:
    """Build the age × economic-status validation panel.

    Parameters
    ----------
    vehicles:
        The synthetic vehicles frame (e.g. ``frames.vehicles`` from
        :class:`~braunschweig.analysis.population_validation.population_source.PopulationFrames`).
        Must carry ``age`` (numeric, years) and ``economic_status`` columns to
        produce non-empty output. ``None`` or a frame missing those columns
        causes a graceful skip (returns zero-row DataFrame with correct columns).
    data_path:
        Path to the eqasim-data/data directory; used to load the MiD reference
        table via :func:`braunschweig.data.kba.fleet_tables.load_mid_age_by_segment_status`.

    Returns
    -------
    DataFrame with columns ``PANEL_COLUMNS``, one row per economic_status.
    Zero rows when the vehicles frame is absent or lacks the required columns.
    """
    empty = pd.DataFrame(columns=list(PANEL_COLUMNS))

    if vehicles is None:
        LOGGER.warning(
            "[fleet_age_status] vehicles frame is None; "
            "age×status validation panel skipped."
        )
        return empty

    if "age" not in vehicles.columns or "economic_status" not in vehicles.columns:
        missing = [c for c in ("age", "economic_status") if c not in vehicles.columns]
        LOGGER.warning(
            "[fleet_age_status] vehicles frame missing column(s) %s; "
            "age×status validation panel skipped.", missing,
        )
        return empty

    # Synthetic summary. Restrict to the household FLEET subset first: vehicles.csv
    # also carries eqasim routing vehicles (mode=='car', economic_status=nan) that
    # would otherwise be silently dropped by the per-status groupby -- filter them
    # out explicitly via the shared fleet filter so the counts are unambiguous.
    from braunschweig.analysis import fleet_filter as _ff
    df = _ff.fleet_vehicles(vehicles, context="fleet_age_status")[["economic_status", "age"]].copy()
    df["age"] = pd.to_numeric(df["age"], errors="coerce")
    df["under_5"] = df["age"] < 5

    syn_rows = []
    for status in STATUS_ORDER:
        sub = df[df["economic_status"] == status]
        if sub.empty:
            syn_rows.append({
                "status": status,
                "synthetic_mean_age_yr": float("nan"),
                "synthetic_under_5_share": float("nan"),
            })
        else:
            syn_rows.append({
                "status": status,
                "synthetic_mean_age_yr": float(sub["age"].mean()),
                "synthetic_under_5_share": float(sub["under_5"].mean()),
            })
    syn = pd.DataFrame(syn_rows).set_index("status")

    # MiD reference.
    try:
        ref = _mid_reference(data_path)
    except Exception:
        LOGGER.exception(
            "[fleet_age_status] failed to load MiD reference; "
            "age×status panel will have NaN reference columns."
        )
        ref = pd.DataFrame(
            {"mid_mean_age_yr": float("nan"), "mid_under_5_share": float("nan")},
            index=pd.Index(list(STATUS_ORDER), name="status"),
        )

    # Merge + compute deltas.
    combined = syn.join(ref, how="left")
    combined["economic_status"] = combined.index
    combined["delta_mean_age_yr"] = (
        combined["synthetic_mean_age_yr"] - combined["mid_mean_age_yr"]
    )
    combined["delta_under_5_share_pp"] = (
        (combined["synthetic_under_5_share"] - combined["mid_under_5_share"]) * 100
    )

    panel = combined.reset_index(drop=True)[list(PANEL_COLUMNS)]

    # Log a compact summary.
    try:
        LOGGER.info(
            "[fleet_age_status] age×status panel (synthetic vs MiD reference):\n%s",
            panel.to_string(index=False),
        )
    except Exception:
        pass

    return panel
