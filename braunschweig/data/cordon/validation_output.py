"""Write the cross-cordon commuter validation outputs (CSV + GPKG), every run.

Emits, into ``<out_dir>``:
  - ``commuter_validation.csv`` : counts per (Kreis, direction, mode), with
    deviation vs the BA Pendler OD target when provided.
  - ``gates.csv``               : flows per entry point (entry_x, entry_y,
    entry_kind, direction, mode) + gate_id for traceability.
  - ``gates.gpkg``              : the same as point geometry (EPSG:25832) for QGIS,
    with ``entry_kind`` so road gates and rail stations are distinguishable.
  - ``summary.md``              : a short human digest incl. modal-split deviation
    and car-via-road-gate vs pt-via-rail-station counts.

B6 change: the gates outputs now group by the ACTUAL entry point
(``entry_x/entry_y/entry_kind``), so PT in-commuters appear at their real rail
station in the map, not at the A2 motorway road gate.  The geometry is built from
``entry_x/entry_y``.

Pure I/O on top of :mod:`braunschweig.data.cordon.validation`; the synpp analysis
stage calls this so "how well did we hit reality / where does boundary traffic
enter" is visible on every run.
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

from braunschweig.data.cordon.gate_assignment import gate_volume_summary
from braunschweig.data.cordon.validation import (
    counts_by_kreis_direction_mode,
    deviation_vs_target,
    gate_flows,
    modal_split_deviation,
)


def write_cordon_validation(out_dir: str, agents: pd.DataFrame, od_target=None,
                            mode_target=None, sampling_rate: float = 1.0,
                            crs: str = "EPSG:25832") -> dict:
    """Write the commuter + per-entry-point validation outputs; return the file paths.

    ``agents`` must have the B5 schema: columns ``[ars5, direction, mode,
    entry_kind, entry_x, entry_y, gate_id]``.  ``entry_kind`` is ``"rail_station"``
    for PT in-commuters placed at a Bahnhof or ``"road_gate"`` for car agents (and
    PT agents reassigned to car because their source Kreis has no eligible rail
    station).
    """
    os.makedirs(out_dir, exist_ok=True)

    counts = counts_by_kreis_direction_mode(agents)
    commuter = (deviation_vs_target(counts, od_target, sampling_rate)
                if od_target is not None else counts)
    commuter_path = os.path.join(out_dir, "commuter_validation.csv")
    commuter.to_csv(commuter_path, index=False)

    flows = gate_flows(agents)
    gates_csv = os.path.join(out_dir, "gates.csv")
    flows.to_csv(gates_csv, index=False)

    gates_gpkg = os.path.join(out_dir, "gates.gpkg")
    gdf = gpd.GeoDataFrame(
        flows,
        # Geometry is the ACTUAL boarding point: rail station for PT, road gate for car.
        geometry=gpd.points_from_xy(flows["entry_x"], flows["entry_y"]),
        crs=crs,
    )
    gdf.to_file(gates_gpkg, driver="GPKG")

    summary_path = os.path.join(out_dir, "summary.md")
    _write_summary(summary_path, agents, counts, mode_target)

    return {
        "commuter_validation": commuter_path,
        "gates_csv": gates_csv,
        "gates_gpkg": gates_gpkg,
        "summary": summary_path,
    }


def _write_summary(path: str, agents: pd.DataFrame, counts: pd.DataFrame,
                   mode_target) -> None:
    lines = ["# Cross-cordon commuter validation", ""]
    lines.append(f"- Agents: {len(agents):,}")
    for direction, sub in counts.groupby("direction"):
        lines.append(f"- {direction}: {int(sub['n'].sum()):,} agents")

    # Entry-kind breakdown: all kinds present in the agents frame (B6 addition, extended).
    # Covers road_gate (car), rail_station (pt), real_origin (hybrid in-commuters), and any
    # future kinds — so the summary never silently undercounts when a new kind is introduced.
    if "entry_kind" in agents.columns:
        lines.append("")
        lines.append("## Entry points by kind")
        by_kind = agents.groupby("entry_kind").size().sort_values(ascending=False)
        for kind, n in by_kind.items():
            lines.append(f"- {kind}: {int(n):,}")
        by_kind_mode = agents.groupby(["entry_kind", "mode"]).size()
        for (kind, mode), n in by_kind_mode.items():
            lines.append(f"  - {kind} / {mode}: {int(n):,}")

    if mode_target is not None:
        lines += ["", "## Modal split vs target (percentage points)", "",
                  "| direction | mode | share_pct | target | pp_dev |",
                  "| --- | --- | --- | --- | --- |"]
        dev = modal_split_deviation(counts, mode_target)
        for _, r in dev.iterrows():
            lines.append(f"| {r['direction']} | {r['mode']} | {r['share_pct']:.1f} "
                         f"| {r['share_pct_target']:.1f} | {r['pp_dev']:+.1f} |")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_gate_volumes(out_dir: str, gates: gpd.GeoDataFrame, assignment: pd.DataFrame,
                       crs: str = "EPSG:25832") -> dict:
    """Write per-gate BA in/out gravity expectation (not realized agents) to CSV + GPKG.

    Computes the per-gate sum of inbound and outbound SvB commuter volumes from the
    gravity-based Kreis->gate assignment, merges the gate point geometry, and writes:
      - ``gate_volumes.csv``  : columns [gate_id, inbound, outbound, n_kreise, gate_x, gate_y]
      - ``gate_volumes.gpkg`` : same data as point geometry (``crs``) for QGIS, with
        inbound + outbound attributes so both directions are mappable per gate.

    These volumes are the **gravity expectation** distributed from the BA Pendler SvB
    data; they are NOT realized simulated agents (out-commuters are ZGB residents who
    leave the region and are not injected into the simulation).

    Args:
        out_dir: directory to write outputs into (created if absent).
        gates: GeoDataFrame with columns [gate_id] and point geometry.
        assignment: DataFrame [ars5, gate_id, inbound, outbound] from the cordon-gates
            stage (output of :func:`braunschweig.data.cordon.gate_assignment.population_gravity_gate_assignment`).
        crs: coordinate reference system for the GPKG output (default EPSG:25832).

    Returns:
        dict with keys ``gate_volumes_csv`` and ``gate_volumes_gpkg`` (absolute paths).
    """
    os.makedirs(out_dir, exist_ok=True)

    summary = gate_volume_summary(assignment, value_cols=("inbound", "outbound"))

    # Build a point-coordinate lookup from the gates GeoDataFrame.
    gate_pts = gates[["gate_id"]].copy()
    gate_pts["gate_x"] = gates.geometry.x.values
    gate_pts["gate_y"] = gates.geometry.y.values

    summary = summary.merge(gate_pts, on="gate_id", how="left")

    csv_cols = ["gate_id", "inbound", "outbound", "n_kreise", "gate_x", "gate_y"]
    csv_out = summary[csv_cols]
    csv_path = os.path.join(out_dir, "gate_volumes.csv")
    csv_out.to_csv(csv_path, index=False)

    gpkg_path = os.path.join(out_dir, "gate_volumes.gpkg")
    gdf = gpd.GeoDataFrame(
        summary[["gate_id", "inbound", "outbound", "n_kreise", "source_kreise"]],
        geometry=gpd.points_from_xy(summary["gate_x"], summary["gate_y"]),
        crs=crs,
    )
    gdf.to_file(gpkg_path, driver="GPKG")

    return {
        "gate_volumes_csv": csv_path,
        "gate_volumes_gpkg": gpkg_path,
    }
