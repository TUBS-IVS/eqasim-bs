"""Behaviour tab: purpose-to-mode sankey + per-Kreis car-share scatter (issue #267 split).

This module holds the tab emitter :func:`emit_behaviour`, which writes two
independent SimWrapper cards from two independent data sources:

- A sankey of trip ``following_purpose`` -> ``mode`` transitions, built from
  ``eqasim_trips.csv`` via :func:`braunschweig.analysis.simwrapper.trip_demand._purpose_to_mode`
  (imported directly from that sibling, not via the ``spatial_export`` facade,
  per this split's no-back-import-cycle rule).
- A scatter of simulated vs. MiD-reference per-Kreis car mode share, built
  from the run record (``record["matsim"]["per_kreis_sim"]`` and
  ``record["mid_reference"]["p12_per_kreis"]``).

Either part is skipped independently (logged) when its input is absent; the
tab itself is skipped when neither part produced output.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from braunschweig.analysis.freight_filter import drop_freight_agents
from braunschweig.analysis.simwrapper import writers as w
from braunschweig.analysis.simwrapper.trip_demand import _purpose_to_mode

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")


# ---------------------------------------------------------------------------
# Tab 3: Behaviour (sankey + scatter)
# ---------------------------------------------------------------------------

def emit_behaviour(
    sim_output_dir: Path | None,
    record: "dict[str, Any] | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Write purpose-to-mode sankey and per-Kreis car-share scatter.

    Sankey:
        Reads ``eqasim_trips.csv`` from ``sim_output_dir``; aggregates
        trip counts by (following_purpose, mode); writes
        ``purpose_to_mode.csv`` (SEMICOLON-delimited).  Skipped when
        trips file is absent.

    Scatter:
        Reads ``record["matsim"]["per_kreis_sim"]`` (sim car-share %) and
        ``record["mid_reference"]["p12_per_kreis"]`` (MiD P12 car-share %
        as ``auto`` column); joins on ``ars5``; writes
        ``kreis_car_sim_vs_mid.csv``.  Skipped when either side absent.

    Returns ``None`` if neither part produced output.

    Args:
        sim_output_dir: Resolved path to the MATSim ``simulation_output/``
            directory, or ``None``.
        record: Run record dict from ``assemble_run_record``.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict or ``None``.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, Any]]] = {}

    # --- Sankey: purpose -> mode ---
    trips_path: Path | None = None
    if sim_output_dir is not None:
        trips_path = Path(sim_output_dir) / "eqasim_trips.csv"
        if not trips_path.exists():
            trips_path = None

    if trips_path is not None:
        df = pd.read_csv(trips_path, sep=";")
        df = drop_freight_agents(df, label="behaviour")
        ptm = _purpose_to_mode(df)
        LOGGER.info(
            "[behaviour] sankey: %d (purpose, mode) pairs from %d trips",
            len(ptm), len(df),
        )
        # SimWrapper sankey expects SEMICOLON-delimited CSV.
        ptm.to_csv(folder / "purpose_to_mode.csv", sep=";", index=False, encoding="utf-8")
        LOGGER.info("[behaviour] wrote purpose_to_mode.csv")
        rows["sankey"] = [
            w.card_sankey(
                "Trip purpose to mode transitions",
                "purpose_to_mode.csv",
                sort=True,
                width=2,
                description=(
                    "Flow: following_purpose (from) -> mode (to). "
                    "Width = trip count. Cordon 'outside' trips excluded."
                ),
            )
        ]
    else:
        LOGGER.info(
            "[behaviour] eqasim_trips.csv not found in %s -- sankey skipped",
            sim_output_dir,
        )

    # --- Scatter: sim car-share vs MiD P12 car-share per Kreis ---
    per_kreis_sim = (record or {}).get("matsim", {}).get("per_kreis_sim") or {}
    p12_per_kreis = (record or {}).get("mid_reference", {}).get("p12_per_kreis") or []

    if per_kreis_sim and p12_per_kreis:
        sim_rows = []
        for ars5, d in per_kreis_sim.items():
            ms = d.get("mode_share_pct", {})
            sim_rows.append({"ars5": ars5, "sim_car_pct": ms.get("car", 0.0)})
        sim_df = pd.DataFrame(sim_rows)

        mid_rows = [
            {"ars5": str(entry["ars5"]), "mid_car_pct": float(entry["auto"])}
            for entry in p12_per_kreis
            if "ars5" in entry and "auto" in entry
        ]
        mid_df = pd.DataFrame(mid_rows)

        scatter_df = sim_df.merge(mid_df, on="ars5", how="inner")
        LOGGER.info(
            "[behaviour] scatter: %d Kreise matched (sim per_kreis_sim=%d, MiD p12=%d)",
            len(scatter_df), len(sim_df), len(mid_df),
        )
        if not scatter_df.empty:
            w.write_csv(folder, "kreis_car_sim_vs_mid.csv", scatter_df)
            LOGGER.info("[behaviour] wrote kreis_car_sim_vs_mid.csv (%d rows)", len(scatter_df))
            rows["scatter"] = [
                w.card_scatter(
                    "Car share per Kreis: Sim vs MiD P12.1",
                    "kreis_car_sim_vs_mid.csv",
                    x="mid_car_pct",
                    y="sim_car_pct",
                    x_axis_name="MiD 2023 P12.1 car share (%)",
                    y_axis_name="Simulation car share (%)",
                    width=2,
                    description=(
                        "Scatter of commute car mode share per Kreis: "
                        "x = MiD 2023 reference (auto %), y = simulation. "
                        "Points on the diagonal indicate a good fit."
                    ),
                )
            ]
        else:
            LOGGER.warning("[behaviour] scatter: no Kreise matched between sim and MiD -- scatter skipped")
    else:
        missing = []
        if not per_kreis_sim:
            missing.append("matsim.per_kreis_sim")
        if not p12_per_kreis:
            missing.append("mid_reference.p12_per_kreis")
        LOGGER.info("[behaviour] scatter skipped: missing %s", ", ".join(missing))

    if not rows:
        LOGGER.warning("[behaviour] neither sankey nor scatter produced output -- behaviour tab skipped")
        return None

    return w.dashboard(
        "Behaviour",
        "Mode transitions & per-Kreis fit",
        rows,
        description=(
            "Left: sankey of trip purpose -> mode (all trips, excl. cordon). "
            "Right: per-Kreis car share scatter (sim vs MiD P12.1 reference)."
        ),
    )
