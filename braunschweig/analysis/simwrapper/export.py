"""Convert a build_dashboard ``record`` dict into a self-contained SimWrapper
dashboard folder (CSV data + dashboard-*.yaml tabs).

The ``record`` is produced by
``braunschweig.analysis.dashboard.build_dashboard.assemble_run_record`` -- the
same structure that already drives the interactive HTML dashboard. This module
adds no scientific logic; it only reshapes that data into SimWrapper files so
the dashboard can be opened in simwrapper.app inside the MATSim ecosystem.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from braunschweig.analysis.dashboard.build_dashboard import (
    REPO_ROOT,
    assemble_run_record,
)
from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper")

# Ordered registry of (filename, emit-fn). Each emit-fn writes its CSV(s) into
# the target folder and returns a dashboard dict (or None if data is absent).
EmitFn = Callable[[dict, Path], "dict[str, Any] | None"]


def export_run(record: dict, target_dir: Path) -> list[Path]:
    """Write all CSVs + dashboard-*.yaml for one run. Returns YAML paths."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, (name, fn) in enumerate(_REGISTRY, start=1):
        try:
            board = fn(record, target_dir)
        except Exception as exc:  # pragma: no cover - defensive, logged loudly
            LOGGER.warning("[simwrapper] tab '%s' skipped: %s", name, exc)
            board = None
        if board is None:
            LOGGER.info("[simwrapper] tab '%s' has no data, skipped", name)
            continue
        path = w.write_yaml(target_dir, f"dashboard-{idx}-{name}.yaml", board)
        written.append(path)
    LOGGER.info("[simwrapper] wrote %d dashboard tab(s) to %s",
                len(written), target_dir)
    return written


# Filled in by Tasks 4-10. Declared here so the orchestrator imports cleanly.
_REGISTRY: list[tuple[str, EmitFn]] = []


def emit_overview(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a SimWrapper Overview tab with headline KPI tiles and a full KPI table.

    Columns written to ``overview_kpis.csv``: ``metric``, ``value``.
    Returns ``None`` when the eqasim sub-record is absent or unavailable.
    """
    eqa = record.get("eqasim", {})
    ms = record.get("matsim", {})
    cmp = record.get("comparisons", {})
    if not eqa.get("available"):
        return None
    cm = cmp.get("commute_mean_km", {})
    dd = cmp.get("distance_distribution", {})
    rows = [
        ("Persons", eqa.get("n_persons")),
        ("Households", eqa.get("n_households")),
        ("Trips", eqa.get("n_trips")),
        ("Trips / person", eqa.get("trips_per_person")),
        ("Mean trip (km)", ms.get("mean_trip_km")),
        ("Mean commute (km)", cm.get("sim")),
        ("Commute vs MiD (%)", cm.get("diff_pct")),
        ("Distance EMD vs MiD", dd.get("emd")),
        ("Final score", ms.get("score_final")),
        ("Iterations", (ms.get("last_iteration") + 1) if ms.get("last_iteration") is not None else None),
        ("Employed (%)", eqa.get("share_employed_pct")),
        ("Driving licence (%)", eqa.get("share_license_pct")),
        ("PT subscription (%)", eqa.get("share_pt_sub_pct")),
    ]
    df = pd.DataFrame(rows, columns=["metric", "value"])
    name = w.write_csv(folder, "overview_kpis.csv", df)
    return w.dashboard(
        "Overview", f"Braunschweig run - {record.get('label', '')}",
        {"kpis": [w.card_tile("Headline KPIs", name, width=2)],
         "table": [w.card_table("All KPIs", name, width=2)]},
        description="MiD 2023 ZGB as reference. Generated from the eqasim run outputs.",
    )


_REGISTRY.append(("overview", emit_overview))


def emit_mode_share(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a Mode share tab with final-iteration shares, commute comparison vs MiD, and evolution.

    CSVs written:
    - ``mode_share_final.csv``: columns ``mode``, ``share_pct`` (all-trip final iteration).
    - ``mode_share_commute_vs_mid.csv``: columns ``mode``, ``sim_pct``, ``mid_pct``
      (work-trip comparison against MiD P12_1; only when data is present).
    - ``mode_share_evolution.csv``: columns ``iteration`` + one column per mode
      (share across MATSim iterations; only when evolution data is present).

    Returns ``None`` when MATSim output is absent or mode-share data is missing.
    """
    ms = record.get("matsim", {})
    cmp = record.get("comparisons", {})
    if not ms.get("available") or not ms.get("mode_share_pct_final"):
        return None
    rows: dict[str, list] = {}

    # Final all-trip mode share (bar chart).
    final = pd.DataFrame(
        [(m, v) for m, v in ms["mode_share_pct_final"].items()],
        columns=["mode", "share_pct"],
    )
    n_final = w.write_csv(folder, "mode_share_final.csv", final)
    rows["final"] = [w.card_bar(
        "All-trip mode share (final iteration)", n_final,
        x="mode", columns=["share_pct"], y_axis_name="%",
    )]

    # Commute mode share sim vs MiD P12_1 (bar chart, only when present).
    wm = cmp.get("work_mode_share")
    if wm:
        df = pd.DataFrame({
            "mode": wm["modes"],
            "sim_pct": wm["sim_pct"],
            "mid_pct": wm["mid_pct"],
        })
        n = w.write_csv(folder, "mode_share_commute_vs_mid.csv", df)
        rows["commute"] = [w.card_bar(
            "Commute mode share - Sim vs MiD P12_1", n,
            x="mode", columns=["sim_pct", "mid_pct"],
            legend_titles=["Simulation", "MiD 2023"],
            y_axis_name="%",
            description=wm.get("note", ""),
        )]

    # Mode-share evolution across iterations (line chart, only when present).
    evo = ms.get("mode_share_evolution")
    if evo and "iterations" in evo:
        modes = [m for m in ms.get("modes", []) if m in evo]
        df = pd.DataFrame({"iteration": evo["iterations"], **{m: evo[m] for m in modes}})
        n = w.write_csv(folder, "mode_share_evolution.csv", df)
        rows["evolution"] = [w.card_line(
            "Mode share evolution across iterations", n,
            x="iteration", columns=modes,
            legend_titles=[m.upper() for m in modes],
            x_axis_name="Iteration", y_axis_name="%", width=2,
        )]

    return w.dashboard("Mode share", "Mode share", rows)


_REGISTRY.append(("mode-share", emit_mode_share))


def emit_distances(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a Distances tab with commute distance distribution vs MiD P13 and mean km by mode.

    CSVs written:
    - ``commute_distance_vs_mid.csv``: columns ``band``, ``sim_pct``, ``mid_pct``
      (distance band comparison against MiD P13; only when present).
    - ``mean_km_by_mode.csv``: columns ``mode``, ``mean_km``
      (mean trip distance per mode; only when present).

    Returns ``None`` when MATSim output is absent or no distance data is present in the record.
    """
    ms = record.get("matsim", {})
    cmp = record.get("comparisons", {})
    if not ms.get("available"):
        return None
    rows: dict[str, list] = {}

    # Commute distance distribution sim vs MiD P13 (grouped bar chart).
    dd = cmp.get("distance_distribution")
    if dd:
        df = pd.DataFrame({
            "band": dd["bands"],
            "sim_pct": dd["sim_pct"],
            "mid_pct": dd["mid_pct"],
        })
        n = w.write_csv(folder, "commute_distance_vs_mid.csv", df)
        ok = "OK" if dd.get("ok") else "FAIL"
        rows["dist"] = [w.card_bar(
            f"Commute distance distribution - Sim vs MiD P13 "
            f"(EMD {dd.get('emd')}, <={dd.get('tolerance')} {ok})",
            n, x="band", columns=["sim_pct", "mid_pct"],
            legend_titles=["Simulation", "MiD 2023"],
            x_axis_name="km class", y_axis_name="%", width=2,
        )]

    # Mean trip distance by mode (bar chart).
    mk = ms.get("mean_km_by_mode")
    if mk:
        df = pd.DataFrame([(m, v) for m, v in mk.items()], columns=["mode", "mean_km"])
        n = w.write_csv(folder, "mean_km_by_mode.csv", df)
        rows["mean_km"] = [w.card_bar(
            "Mean distance by mode", n,
            x="mode", columns=["mean_km"], y_axis_name="km",
        )]

    return w.dashboard("Distances", "Trip and commute distances", rows) if rows else None


_REGISTRY.append(("distances", emit_distances))


def emit_time_of_day(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a Time of day tab with stacked hourly trip counts by mode and purpose.

    CSVs written:
    - ``trips_by_hour_mode.csv``: columns ``hour`` + one column per mode
      (trip departure counts per hour, one row per hour 0-23; only when present).
    - ``trips_by_hour_purpose.csv``: columns ``hour`` + one column per purpose
      (trip departure counts per hour; only when present).

    Returns ``None`` when time-of-day data or the hours list is absent.
    """
    tod = record.get("matsim", {}).get("time_of_day") or {}
    if not tod.get("hours"):
        return None
    rows: dict[str, list] = {}

    # Trips by hour and mode (stacked bar chart).
    by_mode = tod.get("by_mode", {})
    if by_mode:
        df = pd.DataFrame({"hour": tod["hours"], **by_mode})
        n = w.write_csv(folder, "trips_by_hour_mode.csv", df)
        rows["mode"] = [w.card_bar(
            "Trips by hour and mode", n,
            x="hour", columns=list(by_mode.keys()), stacked=True,
            x_axis_name="Hour", y_axis_name="Trips", width=2,
        )]

    # Trips by hour and purpose (stacked bar chart).
    by_pur = tod.get("by_purpose", {})
    if by_pur:
        df = pd.DataFrame({"hour": tod["hours"], **by_pur})
        n = w.write_csv(folder, "trips_by_hour_purpose.csv", df)
        rows["purpose"] = [w.card_bar(
            "Trips by hour and purpose", n,
            x="hour", columns=list(by_pur.keys()), stacked=True,
            x_axis_name="Hour", y_axis_name="Trips", width=2,
        )]

    return w.dashboard("Time of day", "Trip departures over the day", rows) if rows else None


def emit_convergence(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a Convergence tab with MATSim score and distance evolution across iterations.

    CSVs written:
    - ``score_evolution.csv``: columns ``iteration``, ``avg_executed``, ``avg_best``,
      ``avg_worst`` (plan scores per iteration; only when present).
    - ``distance_evolution.csv``: columns ``iteration``, ``avg_trip_km``, ``avg_leg_km``
      (mean trip and leg distances per iteration; only when present).

    Returns ``None`` when no convergence data is present.
    """
    ms = record.get("matsim", {})
    rows: dict[str, list] = {}

    # Score evolution (line chart).
    sc = ms.get("score_evolution")
    if sc and "iterations" in sc:
        df = pd.DataFrame({
            "iteration": sc["iterations"],
            "avg_executed": sc["avg_executed"],
            "avg_best": sc["avg_best"],
            "avg_worst": sc["avg_worst"],
        })
        n = w.write_csv(folder, "score_evolution.csv", df)
        rows["score"] = [w.card_line(
            "Score evolution", n,
            x="iteration", columns=["avg_executed", "avg_best", "avg_worst"],
            legend_titles=["Executed", "Best", "Worst"],
            x_axis_name="Iteration", y_axis_name="Score",
        )]

    # Distance evolution (line chart).
    di = ms.get("distance_evolution")
    if di and "iterations" in di:
        df = pd.DataFrame({
            "iteration": di["iterations"],
            "avg_trip_km": di["avg_trip_km"],
            "avg_leg_km": di["avg_leg_km"],
        })
        n = w.write_csv(folder, "distance_evolution.csv", df)
        rows["dist"] = [w.card_line(
            "Average distance evolution", n,
            x="iteration", columns=["avg_trip_km", "avg_leg_km"],
            legend_titles=["Trip (km)", "Leg (km)"],
            x_axis_name="Iteration", y_axis_name="km",
        )]

    return w.dashboard("Convergence", "MATSim convergence", rows) if rows else None


_REGISTRY.append(("time-of-day", emit_time_of_day))
_REGISTRY.append(("convergence", emit_convergence))


def emit_per_kreis(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a Per-Kreis tab with a table and mean commute bar chart.

    CSV written: ``per_kreis_sim.csv`` with columns ``ars5``, ``name``,
    ``n_trips``, ``mean_km``, ``median_km``, ``car_pct``, ``pt_pct``,
    ``bicycle_pct``, ``walk_pct`` — one row per Kreis present in the record.

    Returns ``None`` when no per-Kreis data is found.
    """
    pk = record.get("matsim", {}).get("per_kreis_sim") or {}
    if not pk:
        return None
    rows_data = []
    for ars5, d in pk.items():
        ms = d.get("mode_share_pct", {})
        rows_data.append({
            "ars5": ars5,
            "name": d.get("name", ars5),
            "n_trips": d.get("n_trips"),
            "mean_km": d.get("mean_km"),
            "median_km": d.get("median_km"),
            "car_pct": ms.get("car"),
            "pt_pct": ms.get("pt"),
            "bicycle_pct": ms.get("bicycle"),
            "walk_pct": ms.get("walk"),
        })
    df = pd.DataFrame(rows_data).sort_values("name")
    n = w.write_csv(folder, "per_kreis_sim.csv", df)
    return w.dashboard(
        "Per-Kreis", "Per-Kreis simulation metrics (commute origin)",
        {
            "table": [w.card_table("Per-Kreis table", n, width=2)],
            "mean": [w.card_bar(
                "Mean commute distance by Kreis", n,
                x="name", columns=["mean_km"],
                y_axis_name="km", width=2,
            )],
        },
    )


_REGISTRY.append(("per-kreis", emit_per_kreis))
