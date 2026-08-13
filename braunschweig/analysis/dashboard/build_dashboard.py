"""Build / refresh the Braunschweig simulation dashboard.

Usage (PowerShell, conda env `eqasim` activated):

    python -m braunschweig.analysis.dashboard.build_dashboard `
        --output-dir eqasim-data/output_bs_25pct `
        --sim-cache eqasim-data/cache_bs_25pct `
        --label "25pct_v1"

The script
  1. reads the eqasim CSV outputs and MATSim simulation_output/ for one run,
  2. computes a battery of KPIs (mode share, distance bands, commute means,
     iteration evolution, ...),
  3. compares them against MiD 2023 Braunschweig reference values
     (`eqasim-data/data/braunschweig/mid/mid2023_*.csv`),
  4. writes a `metrics.json` into `braunschweig/analysis/dashboard/runs/<run_id>/`,
  5. regenerates `braunschweig/analysis/dashboard/index.html` with all runs
     embedded as JSON (no web-server required, just open the HTML file).

Re-run the script after every new simulation to add a new version.

Module layout: this file is the facade for the ``dashboard`` package. The
large ``HTML_TEMPLATE`` string literal used by ``render_dashboard`` lives in
the sibling module ``html_template.py`` and is re-exported below so existing
callers of ``build_dashboard.HTML_TEMPLATE`` keep working unchanged.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from braunschweig.analysis.dashboard.html_template import HTML_TEMPLATE  # noqa: F401  (re-exports)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = Path(__file__).resolve().parent
RUNS_DIR = DASHBOARD_DIR / "runs"
MID_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"

# MiD P13 distance bands (km).  Upper bound 250 km used for >=100 km bin.
P13_BINS_KM = [0.0, 0.5, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, 250.0]
P13_LABELS = [
    "0–0.5", "0.5–5", "5–10", "10–20",
    "20–30", "30–50", "50–100", "100+",
]
# MiD P13 also exposes "d_0" (=0 km, no commute) which we keep separate.

KREIS_NAMES = {
    "03101": "Braunschweig",
    "03102": "Salzgitter",
    "03103": "Wolfsburg",
    "03151": "Gifhorn",
    "03153": "Goslar",
    "03154": "Helmstedt",
    "03157": "Peine",
    "03158": "Wolfenbüttel",
}

# Mode mapping eqasim -> MiD.  MiD P12_1 reports any-mode used per commute
# (rows can sum >100 %).  We compare to the synth main mode.
MODE_LABEL = {
    "car": "Car",
    "car_passenger": "Car (passenger)",
    "pt": "PT",
    "bicycle": "Bicycle",
    "walk": "Walk",
}

# ZGB-8 Kreis ARS codes (5-digit) used for spatial joins.
ZGB_ARS5 = list(KREIS_NAMES.keys())

# VG250 cached extraction path (zip is shipped under eqasim-data/data/germany/).
VG250_ZIP = REPO_ROOT / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
VG250_CACHE = DASHBOARD_DIR / ".cache" / "DE_VG250.gpkg"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_read_csv(path: Path, **kw: Any) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path, **kw)


def _find_sim_output(cache_root: Path) -> Path | None:
    """Locate `matsim.simulation.run__*.cache/simulation_output/`."""
    for d in cache_root.glob("matsim.simulation.run__*.cache"):
        cand = d / "simulation_output"
        if cand.exists():
            return cand
    return None


def _to_km_bands(distances_km: np.ndarray) -> dict[str, float]:
    """Return percentage shares per P13 band."""
    if distances_km.size == 0:
        return {lbl: 0.0 for lbl in P13_LABELS}
    edges = np.array(P13_BINS_KM)
    idx = np.clip(np.searchsorted(edges, distances_km, side="right") - 1, 0, len(P13_LABELS) - 1)
    counts = np.bincount(idx, minlength=len(P13_LABELS))
    pct = counts / counts.sum() * 100.0
    return {lbl: float(round(pct[i], 2)) for i, lbl in enumerate(P13_LABELS)}


def _earth_movers_distance(p_pct: list[float], q_pct: list[float]) -> float:
    """1D EMD on equal-mass percentage histograms."""
    p = np.array(p_pct, dtype=float) / 100.0
    q = np.array(q_pct, dtype=float) / 100.0
    if p.sum() == 0 or q.sum() == 0:
        return float("nan")
    p /= p.sum()
    q /= q.sum()
    return float(np.abs(np.cumsum(p) - np.cumsum(q)).sum())


# ---------------------------------------------------------------------------
# MiD reference loader
# ---------------------------------------------------------------------------


def load_mid_reference() -> dict[str, Any]:
    # dtype=str at READ time: int64 inference would strip the ars5 leading
    # zero of a per-Kreis-only file irreversibly; do not rely on the "03ZGB"
    # row forcing object dtype (see data/mid/reference_tables._read_csv).
    p12 = _safe_read_csv(MID_DIR / "mid2023_P12_1.csv", dtype={"ars5": str})
    p13 = _safe_read_csv(MID_DIR / "mid2023_P13.csv", dtype={"ars5": str})

    ref: dict[str, Any] = {"available": False}
    if p12 is None or p13 is None:
        return ref

    p12_total = p12.loc[p12["ars5"] == "03ZGB"].iloc[0]
    p13_total = p13.loc[p13["ars5"] == "03ZGB"].iloc[0]

    # P13 distance distribution → renormalise (drop "keine_feste_arbeit"/"keine_angabe")
    d_cols = ["d_0_5", "d_5_10", "d_10_20", "d_20_30", "d_30_50", "d_50_100", "d_100p"]
    raw = {c: float(p13_total[c]) for c in d_cols}
    # Add "d_0" (=0 km) into 0-0.5 bucket so we have 8 bins matching synth.
    raw_first = float(p13_total["d_0"]) + raw["d_0_5"]
    pct_list = [raw_first, raw["d_5_10"], raw["d_10_20"], raw["d_20_30"],
                raw["d_30_50"], raw["d_50_100"], raw["d_100p"]]
    # Re-normalise to 100 (some kreise have rounding errors)
    s = sum(pct_list)
    if s > 0:
        pct_list = [round(x / s * 100, 2) for x in pct_list]
    # The synth bands have 8 entries (we split 0-0.5 vs 0.5-5);
    # for simplicity fold synth's "0-0.5" + "0.5-5" together when EMD is computed.
    p13_dist = {
        "0–5": pct_list[0],
        "5–10": pct_list[1],
        "10–20": pct_list[2],
        "20–30": pct_list[3],
        "30–50": pct_list[4],
        "50–100": pct_list[5],
        "100+": pct_list[6],
    }

    per_kreis = []
    for _, row in p13.iterrows():
        ars5 = str(row["ars5"])
        if ars5 == "03ZGB":
            continue
        per_kreis.append({
            "ars5": ars5,
            "name": KREIS_NAMES.get(ars5, str(row["kreis"])),
            "mean_km": float(row["mittel"]),
            "n_weighted": float(row["n_weighted"]),
        })

    p12_per_kreis = []
    for _, row in p12.iterrows():
        ars5 = str(row["ars5"])
        if ars5 == "03ZGB":
            continue
        p12_per_kreis.append({
            "ars5": ars5,
            "name": KREIS_NAMES.get(ars5, str(row["kreis"])),
            "auto": float(row["auto"]),
            "oeffentlich": float(row["oeffentlich"]),
            "fahrrad": float(row["fahrrad"]),
            "zu_fuss": float(row["zu_fuss"]),
        })

    ref.update({
        "available": True,
        "p12_modal_split_zgb": {
            # Note: MiD P12_1 = "any mode used"; rows can sum >100.
            "Car": float(p12_total["auto"]),
            "PT": float(p12_total["oeffentlich"]),
            "Bicycle": float(p12_total["fahrrad"]),
            "Walk": float(p12_total["zu_fuss"]),
        },
        "p13_mean_km_zgb": float(p13_total["mittel"]),
        "p13_distance_pct_zgb": p13_dist,
        "p13_per_kreis": per_kreis,
        "p12_per_kreis": p12_per_kreis,
        "n_weighted_zgb": float(p13_total["n_weighted"]),
    })
    return ref


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
# Comparisons against MiD
# ---------------------------------------------------------------------------


def build_comparisons(eqa: dict, ms: dict, mid: dict) -> dict[str, Any]:
    cmp: dict[str, Any] = {}
    if not mid.get("available"):
        return cmp

    # commute distance vs MiD P13 (ZGB-Gesamt)
    if ms.get("commute"):
        sim_mean = ms["commute"]["mean_km"]
        ref_mean = mid["p13_mean_km_zgb"]
        cmp["commute_mean_km"] = {
            "sim": sim_mean, "mid": ref_mean,
            "diff_km": round(sim_mean - ref_mean, 2),
            "diff_pct": round((sim_mean - ref_mean) / ref_mean * 100, 1),
        }

        # MiD distribution alignment (re-aggregate sim into MiD bands)
        # MiD bands: 0–5 / 5–10 / 10–20 / 20–30 / 30–50 / 50–100 / 100+
        sim_dist = ms["commute"]["dist_pct"]
        sim_aligned = {
            "0–5": sim_dist["0–0.5"] + sim_dist["0.5–5"],
            "5–10": sim_dist["5–10"],
            "10–20": sim_dist["10–20"],
            "20–30": sim_dist["20–30"],
            "30–50": sim_dist["30–50"],
            "50–100": sim_dist["50–100"],
            "100+": sim_dist["100+"],
        }
        emd = _earth_movers_distance(
            list(sim_aligned.values()),
            list(mid["p13_distance_pct_zgb"].values()),
        )
        cmp["distance_distribution"] = {
            "bands": list(sim_aligned.keys()),
            "sim_pct": list(sim_aligned.values()),
            "mid_pct": list(mid["p13_distance_pct_zgb"].values()),
            "emd": round(emd, 4),
            "tolerance": 0.08,  # from quality/QUALITY.md scenario 7
            "ok": bool(emd <= 0.08),
        }

    # mode share — work commute vs MiD P12_1 ZGB
    if ms.get("commute") and ms["commute"].get("mode_share_pct"):
        sim_ms = ms["commute"]["mode_share_pct"]
        sim_translated = {
            "Car": sim_ms.get("car", 0.0),
            "Car (passenger)": sim_ms.get("car_passenger", 0.0),
            "PT": sim_ms.get("pt", 0.0),
            "Bicycle": sim_ms.get("bicycle", 0.0),
            "Walk": sim_ms.get("walk", 0.0),
        }
        mid_ms = mid["p12_modal_split_zgb"]
        cmp["work_mode_share"] = {
            "modes": ["Car", "PT", "Bicycle", "Walk"],
            "sim_pct": [round(sim_translated[m], 1) for m in ["Car", "PT", "Bicycle", "Walk"]],
            "mid_pct": [round(mid_ms[m], 1) for m in ["Car", "PT", "Bicycle", "Walk"]],
            "note": "MiD P12_1 reports 'every mode used per commute' (rows can sum >100%); sim shows main mode only.",
        }
    return cmp


# ---------------------------------------------------------------------------
# Run record + dashboard rendering
# ---------------------------------------------------------------------------


def assemble_run_record(
    label: str,
    output_dir: Path,
    sim_cache: Path | None,
    sample_rate: float | None,
    notes: str = "",
) -> dict[str, Any]:
    # sim_cache may be None for a synthesis-only run (no MATSim). In that case
    # there is no simulation_output and the MATSim metrics stay "available: False",
    # so the MATSim-dependent dashboard tabs skip (no silent failure).
    sim_output = _find_sim_output(sim_cache) if sim_cache is not None else None
    eqa = metrics_eqasim(output_dir, sample_rate)
    ms = metrics_matsim(sim_output) if sim_output else {"available": False}
    mid = load_mid_reference()
    cmp = build_comparisons(eqa, ms, mid)

    ts = _dt.datetime.now().isoformat(timespec="seconds")
    run_id = (
        _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + re.sub(r"[^A-Za-z0-9_-]", "_", label or "run")
    )

    return {
        "run_id": run_id,
        "label": label,
        "created_at": ts,
        "notes": notes,
        "sample_rate": sample_rate,
        "paths": {
            "output_dir": str(output_dir),
            "sim_output": str(sim_output) if sim_output else None,
        },
        "eqasim": eqa,
        "matsim": ms,
        "mid_reference": mid,
        "comparisons": cmp,
    }


def write_run(record: dict) -> Path:
    run_dir = RUNS_DIR / record["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    f = run_dir / "metrics.json"
    f.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return f


def collect_all_runs() -> list[dict]:
    runs: list[dict] = []
    if not RUNS_DIR.exists():
        return runs
    for d in sorted(RUNS_DIR.iterdir()):
        f = d / "metrics.json"
        if f.exists():
            try:
                runs.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    runs.sort(key=lambda r: r.get("created_at", ""))
    return runs


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def render_dashboard(runs: list[dict]) -> Path:
    runs_json = json.dumps(runs, ensure_ascii=False, default=str)
    html = HTML_TEMPLATE.replace("__RUNS_JSON__", runs_json)
    out = DASHBOARD_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Braunschweig simulation dashboard.")
    ap.add_argument("--output-dir", required=False, default="eqasim-data/output_bs_25pct",
                    help="eqasim CSV output folder for the run.")
    ap.add_argument("--sim-cache", required=False, default="eqasim-data/cache_bs_25pct",
                    help="Synpp cache folder containing matsim.simulation.run__*.cache/.")
    ap.add_argument("--label", required=False, default=None,
                    help="Friendly label for this run (defaults to <output_dir name>).")
    ap.add_argument("--notes", required=False, default="", help="Free-form notes.")
    ap.add_argument("--sample-rate", required=False, type=float, default=None,
                    help="Sampling rate (0.01 / 0.1 / 0.25). Detected from folder name if omitted.")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="Only re-render index.html from existing runs/ — no new run added.")
    args = ap.parse_args()

    if not args.rebuild_only:
        out_dir = (REPO_ROOT / args.output_dir).resolve()
        sim_cache = (REPO_ROOT / args.sim_cache).resolve()
        rate = args.sample_rate if args.sample_rate else _detect_sample_rate(out_dir)
        label = args.label or out_dir.name.replace("output_bs_", "").replace("output_bs", "1pct")
        rec = assemble_run_record(label, out_dir, sim_cache, rate, args.notes)
        f = write_run(rec)
        print(f"[dashboard] wrote {f.relative_to(REPO_ROOT)}")

    runs = collect_all_runs()
    html = render_dashboard(runs)
    print(f"[dashboard] {len(runs)} run(s) embedded")
    print(f"[dashboard] open {html.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
