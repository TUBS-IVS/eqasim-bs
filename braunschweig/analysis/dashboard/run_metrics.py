"""Per-run metric computation: eqasim/MATSim outputs + spatial breakdowns.

This module holds ``metrics_eqasim`` and ``metrics_matsim`` -- the two
functions that turn a run's eqasim CSV output and MATSim
``simulation_output/`` into the KPI dictionaries assembled by
``build_dashboard.assemble_run_record`` -- plus ``_find_sim_output`` and
``_detect_sample_rate``, moved verbatim from ``build_dashboard.py``.

``metrics_matsim`` itself calls a cluster of VG250/per-Kreis spatial helpers
(``_ensure_vg250``, ``_load_zgb_kreise``, ``_classify_points``,
``metrics_time_of_day``, ``metrics_per_kreis``, ``metrics_od_matrix``) that
were not in the originally planned move list for this module but had no
other sibling to own them at the time of this extraction: leaving them in
``build_dashboard.py`` would force this module to import back from the
facade to reach them, creating an import cycle (and, concretely, a circular
``ImportError`` at import time, since ``build_dashboard.py`` in turn imports
``metrics_matsim`` from here). They are moved here together with
``metrics_matsim``, disclosed in this docstring; a future, further split
could still carve them out into their own ``spatial_metrics`` module. Their
constants (``ZGB_ARS5``, ``VG250_ZIP``, ``VG250_CACHE``) move with them for
the same reason -- they are used exclusively by this cluster.

``KREIS_NAMES``, ``_safe_read_csv`` and ``_to_km_bands`` are imported from
the sibling module ``mid_reference.py`` (their owner after the previous split
step); this module never imports ``build_dashboard`` back.

``build_dashboard.py`` re-exports every name below so existing callers of
``build_dashboard.<name>`` keep working unchanged.

This module must not import ``build_dashboard`` -- that would create an
import cycle between the facade and this leaf module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from braunschweig.analysis.dashboard.mid_reference import KREIS_NAMES
from braunschweig.analysis.dashboard.mid_reference import _safe_read_csv
from braunschweig.analysis.dashboard.mid_reference import _to_km_bands

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = Path(__file__).resolve().parent

# ZGB-8 Kreis ARS codes (5-digit) used for spatial joins.
ZGB_ARS5 = list(KREIS_NAMES.keys())

# VG250 cached extraction path (zip is shipped under eqasim-data/data/germany/).
VG250_ZIP = REPO_ROOT / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
VG250_CACHE = DASHBOARD_DIR / ".cache" / "DE_VG250.gpkg"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_sim_output(cache_root: Path) -> Path | None:
    """Locate `matsim.simulation.run__*.cache/simulation_output/`."""
    for d in cache_root.glob("matsim.simulation.run__*.cache"):
        cand = d / "simulation_output"
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# eqasim output metrics
# ---------------------------------------------------------------------------


def metrics_eqasim(output_dir: Path, sample_rate: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}

    persons_p = next(output_dir.glob("*_persons.csv"), None)
    trips_p = next(output_dir.glob("*_trips.csv"), None)
    hh_p = next(output_dir.glob("*_households.csv"), None)
    acts_p = next(output_dir.glob("*_activities.csv"), None)
    if persons_p is None or trips_p is None:
        return out

    persons = pd.read_csv(persons_p, sep=";")
    trips = pd.read_csv(trips_p, sep=";")

    out["available"] = True
    out["n_persons"] = int(len(persons))
    out["n_households"] = int(persons["household_id"].nunique())
    out["n_trips"] = int(len(trips))
    out["sample_rate"] = sample_rate
    if hh_p is not None:
        out["n_households"] = int(sum(1 for _ in open(hh_p, encoding="utf-8")) - 1)
    if acts_p is not None:
        out["n_activities"] = int(sum(1 for _ in open(acts_p, encoding="utf-8")) - 1)

    # demographics
    out["mean_age"] = float(persons["age"].mean())
    out["share_employed_pct"] = float(persons["employed"].mean() * 100)
    out["share_license_pct"] = float(persons["has_driving_license"].mean() * 100)
    out["share_pt_sub_pct"] = float(persons["has_pt_subscription"].mean() * 100)
    out["share_female_pct"] = float((persons["sex"] == "female").mean() * 100)
    out["share_urban_pct"] = float(persons["is_urban_resident"].mean() * 100)

    # trip purposes
    pur = trips["following_purpose"].value_counts(normalize=True) * 100
    out["trip_purpose_pct"] = {k: float(round(v, 2)) for k, v in pur.items()}

    # trips per person
    out["trips_per_person"] = float(round(len(trips) / max(len(persons), 1), 2))
    return out


# ---------------------------------------------------------------------------
# VG250 / spatial helpers (per-Kreis + OD matrix)
# ---------------------------------------------------------------------------


def _ensure_vg250() -> Path | None:
    """Extract DE_VG250.gpkg from the zip into the dashboard cache (once)."""
    if VG250_CACHE.exists():
        return VG250_CACHE
    if not VG250_ZIP.exists():
        return None
    import zipfile
    VG250_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(VG250_ZIP) as z:
        target = next((n for n in z.namelist() if n.endswith("DE_VG250.gpkg")), None)
        if target is None:
            return None
        with z.open(target) as src, open(VG250_CACHE, "wb") as dst:
            dst.write(src.read())
    return VG250_CACHE


def _load_zgb_kreise():
    """Return GeoDataFrame of the 8 ZGB Kreise (CRS EPSG:25832)."""
    p = _ensure_vg250()
    if p is None:
        return None
    try:
        import geopandas as gpd
    except ImportError:
        return None
    krs = gpd.read_file(p, layer="vg250_krs")
    # ARS is the 12-digit Amtlicher Regionalschlüssel; first 5 chars = Kreis code.
    krs["ars5"] = krs["ARS"].astype(str).str[:5]
    krs = krs[krs["ars5"].isin(ZGB_ARS5)].copy()
    krs["name"] = krs["ars5"].map(KREIS_NAMES).fillna(krs.get("GEN", krs["ars5"]))
    krs = krs.to_crs(25832)[["ars5", "name", "geometry"]].reset_index(drop=True)
    return krs


def _classify_points(xs: np.ndarray, ys: np.ndarray, kreise) -> np.ndarray:
    """Spatial-join arrays of x/y (EPSG:25832) to the 8 ZGB Kreise.

    Returns an object array of ARS5 strings (or "external" for points outside).
    """
    import geopandas as gpd
    import pandas as _pd

    pts = gpd.GeoDataFrame(
        {"_idx": np.arange(len(xs))},
        geometry=gpd.points_from_xy(xs, ys),
        crs=25832,
    )
    j = gpd.sjoin(pts, kreise[["ars5", "geometry"]], how="left", predicate="within")
    # Multiple matches per point can occur on borders → keep first.
    j = j.drop_duplicates(subset="_idx").set_index("_idx").sort_index()
    out = j["ars5"].astype("object").fillna("external").to_numpy()
    return out


def metrics_time_of_day(et: pd.DataFrame) -> dict[str, Any]:
    """Hourly trip start histograms broken down by mode and purpose."""
    if et is None or len(et) == 0 or "departure_time" not in et.columns:
        return {}
    hours = (et["departure_time"].astype(float) // 3600).clip(0, 23).astype(int)
    et = et.assign(_h=hours)
    by_mode = (
        et.groupby(["_h", "mode"]).size().unstack(fill_value=0)
        .reindex(range(24), fill_value=0)
    )
    by_pur = (
        et.groupby(["_h", "following_purpose"]).size().unstack(fill_value=0)
        .reindex(range(24), fill_value=0)
    )
    return {
        "hours": list(range(24)),
        "by_mode": {m: [int(v) for v in by_mode[m].tolist()] for m in by_mode.columns},
        "by_purpose": {p: [int(v) for v in by_pur[p].tolist()] for p in by_pur.columns},
        "total_per_hour": [int(v) for v in et.groupby("_h").size().reindex(range(24), fill_value=0).tolist()],
    }


def metrics_per_kreis(et: pd.DataFrame, kreise) -> dict[str, Any]:
    """Per-ZGB-Kreis sim metrics, classified by trip origin (home end of commute)."""
    if et is None or len(et) == 0 or kreise is None:
        return {}
    com = et[et["following_purpose"] == "work"].copy()
    if not len(com):
        return {}
    # Classify origin (home end) of the commute trip.
    ars = _classify_points(com["origin_x"].to_numpy(), com["origin_y"].to_numpy(), kreise)
    com["ars5"] = ars
    out: dict[str, Any] = {}
    for ars5 in ZGB_ARS5:
        sub = com[com["ars5"] == ars5]
        if not len(sub):
            continue
        ms = (sub["mode"].value_counts(normalize=True) * 100).round(1).to_dict()
        out[ars5] = {
            "name": KREIS_NAMES.get(ars5, ars5),
            "n_trips": int(len(sub)),
            "mean_km": float(round(sub["km"].mean(), 2)),
            "median_km": float(round(sub["km"].median(), 2)),
            "mode_share_pct": {k: float(v) for k, v in ms.items()},
        }
    return out


def metrics_od_matrix(et: pd.DataFrame, kreise) -> dict[str, Any]:
    """Origin-Destination matrices at Kreis level, per following_purpose.

    Cells are absolute trip counts; "external" aggregates trips touching
    Kreise outside ZGB-8.
    """
    if et is None or len(et) == 0 or kreise is None:
        return {}
    # Sub-sample to keep the JSON small if there are many trips.
    df = et[["origin_x", "origin_y", "destination_x", "destination_y",
             "following_purpose", "mode"]].dropna()
    o = _classify_points(df["origin_x"].to_numpy(), df["origin_y"].to_numpy(), kreise)
    d = _classify_points(df["destination_x"].to_numpy(), df["destination_y"].to_numpy(), kreise)
    df = df.assign(_o=o, _d=d)
    zones = ZGB_ARS5 + ["external"]
    matrices: dict[str, list[list[int]]] = {}
    purposes = sorted(df["following_purpose"].dropna().unique().tolist())
    for pur in purposes:
        sub = df[df["following_purpose"] == pur]
        # Build matrix counts via pivot
        ct = (
            sub.groupby(["_o", "_d"]).size().unstack(fill_value=0)
            .reindex(index=zones, columns=zones, fill_value=0)
        )
        matrices[pur] = ct.astype(int).values.tolist()
    return {
        "zones": zones,
        "zone_names": [KREIS_NAMES.get(z, "Outside ZGB") for z in zones],
        "purposes": purposes,
        "matrices": matrices,
    }


# ---------------------------------------------------------------------------
# MATSim simulation_output metrics
# ---------------------------------------------------------------------------


def metrics_matsim(sim_output: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    if sim_output is None or not sim_output.exists():
        return out
    out["available"] = True

    # modestats — share per iteration
    ms = _safe_read_csv(sim_output / "modestats.csv", sep=";")
    if ms is not None and len(ms):
        modes = [c for c in ms.columns if c != "iteration"]
        # Exclude the cordon "outside" pseudo-mode and renormalise the per-iteration
        # shares over the real modes, so the reported modal split sums to 100% for
        # trips inside the study area. No-op when the cordon is off ("outside" absent).
        real_modes = [m for m in modes if m != "outside"]
        if "outside" in modes and real_modes:
            ms = ms.copy()
            row_sums = ms[real_modes].sum(axis=1).replace(0, 1.0)
            for m in real_modes:
                ms[m] = ms[m] / row_sums
            modes = real_modes
        last = ms.iloc[-1]
        first = ms.iloc[0]
        out["modes"] = modes
        out["mode_share_pct_iter0"] = {m: float(round(first[m] * 100, 2)) for m in modes}
        out["mode_share_pct_final"] = {m: float(round(last[m] * 100, 2)) for m in modes}
        out["mode_share_evolution"] = {
            "iterations": [int(x) for x in ms["iteration"].tolist()],
            **{m: [float(round(v * 100, 3)) for v in ms[m].tolist()] for m in modes},
        }
        out["last_iteration"] = int(last["iteration"])

    # scorestats — convergence
    ss = _safe_read_csv(sim_output / "scorestats.csv", sep=";")
    if ss is not None and len(ss):
        out["score_evolution"] = {
            "iterations": [int(x) for x in ss["iteration"].tolist()],
            "avg_executed": [float(round(v, 3)) for v in ss["avg_executed"].tolist()],
            "avg_best": [float(round(v, 3)) for v in ss["avg_best"].tolist()],
            "avg_worst": [float(round(v, 3)) for v in ss["avg_worst"].tolist()],
        }
        out["score_final"] = float(round(ss.iloc[-1]["avg_executed"], 3))

    # avg leg/trip distance evolution
    tds = _safe_read_csv(sim_output / "traveldistancestats.csv", sep=";")
    if tds is not None and len(tds):
        cols = {c.strip().lower(): c for c in tds.columns}
        leg_col = cols.get("avg. average leg distance") or list(tds.columns)[1]
        trip_col = cols.get("avg. average trip distance") or list(tds.columns)[2]
        out["distance_evolution"] = {
            "iterations": [int(x) for x in tds["ITERATION"].tolist()],
            "avg_leg_km": [float(round(v / 1000, 3)) for v in tds[leg_col].tolist()],
            "avg_trip_km": [float(round(v / 1000, 3)) for v in tds[trip_col].tolist()],
        }
        out["avg_trip_km_final"] = float(round(tds.iloc[-1][trip_col] / 1000, 2))
        out["avg_leg_km_final"] = float(round(tds.iloc[-1][leg_col] / 1000, 2))

    # eqasim_trips — distance distribution + per-mode mean km, per-purpose mean km
    et = _safe_read_csv(sim_output / "eqasim_trips.csv", sep=";")
    if et is not None and len(et):
        from braunschweig.analysis.freight_filter import drop_freight_agents
        et = drop_freight_agents(et, label="dashboard")
        et = et[et["routed_distance"].notna()].copy()
        # Drop cordon out-of-scope legs: "outside" is not a real transport mode but
        # the eqasim marker for the portion of a trip beyond the cordon (set by the
        # scenario cutter). Excluding it keeps the modal split / distance KPIs about
        # the real modes used inside the study area. No-op when the cordon is off.
        et = et[et["mode"] != "outside"].copy()
        et["km"] = et["routed_distance"].astype(float) / 1000.0

        # Overall distance distribution (all trips)
        out["all_trip_dist_pct"] = _to_km_bands(et["km"].values)
        out["mean_trip_km"] = float(round(et["km"].mean(), 2))
        out["median_trip_km"] = float(round(et["km"].median(), 2))

        # mode share by # trips (final iteration of MATSim, aggregated trips)
        sh = et["mode"].value_counts(normalize=True) * 100
        out["sim_trip_mode_share_pct"] = {k: float(round(v, 2)) for k, v in sh.items()}

        # Per-mode mean distance (km)
        per_mode_mean = et.groupby("mode")["km"].mean().round(2).to_dict()
        out["mean_km_by_mode"] = {k: float(v) for k, v in per_mode_mean.items()}

        # Per-mode distance distribution (for stacked chart)
        out["dist_pct_by_mode"] = {}
        for mode, sub in et.groupby("mode"):
            out["dist_pct_by_mode"][mode] = _to_km_bands(sub["km"].values)

        # Commute = trips with destination = work
        com = et[et["following_purpose"] == "work"]
        if len(com):
            out["commute"] = {
                "n_trips": int(len(com)),
                "mean_km": float(round(com["km"].mean(), 2)),
                "median_km": float(round(com["km"].median(), 2)),
                "p95_km": float(round(com["km"].quantile(0.95), 2)),
                "dist_pct": _to_km_bands(com["km"].values),
                "mode_share_pct": {
                    k: float(round(v, 2))
                    for k, v in (com["mode"].value_counts(normalize=True) * 100).items()
                },
            }

        # Per-purpose mean km
        out["mean_km_by_purpose"] = {
            k: float(round(v, 2))
            for k, v in et.groupby("following_purpose")["km"].mean().items()
        }

        # --- new feature blocks ----------------------------------------
        try:
            out["time_of_day"] = metrics_time_of_day(et)
        except Exception as exc:  # pragma: no cover - defensive
            out["time_of_day_error"] = str(exc)

        kreise = _load_zgb_kreise()
        if kreise is not None:
            try:
                out["per_kreis_sim"] = metrics_per_kreis(et, kreise)
            except Exception as exc:  # pragma: no cover
                out["per_kreis_sim_error"] = str(exc)
            try:
                out["od_matrix"] = metrics_od_matrix(et, kreise)
            except Exception as exc:  # pragma: no cover
                out["od_matrix_error"] = str(exc)
        else:
            out["per_kreis_sim_error"] = "VG250 not available"

    # Iteration timing from stopwatch (minutes per iter)
    sw = _safe_read_csv(sim_output / "stopwatch.csv", sep=";")
    if sw is not None and "iteration" in sw.columns:
        # Last column "iteration" is the duration HH:MM:SS
        last_col = list(sw.columns)[-1]
        durations = []
        for v in sw[last_col].astype(str):
            m = re.match(r"^(\d+):(\d+):(\d+)$", v)
            if m:
                h, mm, ss = (int(x) for x in m.groups())
                durations.append(h * 60 + mm + ss / 60.0)
        if durations:
            out["iter_minutes"] = [round(x, 2) for x in durations]
            out["mean_iter_minutes"] = float(round(np.mean(durations), 2))
            out["total_minutes"] = float(round(np.sum(durations), 1))

    # eqasim_termination presence -> early convergence
    out["terminated_early"] = (sim_output / "eqasim_termination.csv").exists()

    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _detect_sample_rate(output_dir: Path) -> float | None:
    name = output_dir.name
    m = re.search(r"_(\d+)pct", name)
    if m:
        return int(m.group(1)) / 100.0
    if name.endswith("output_bs"):
        return 0.01
    return None
