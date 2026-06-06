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
    p12 = _safe_read_csv(MID_DIR / "mid2023_P12_1.csv")
    p13 = _safe_read_csv(MID_DIR / "mid2023_P13.csv")

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
    sim_cache: Path,
    sample_rate: float | None,
    notes: str = "",
) -> dict[str, Any]:
    sim_output = _find_sim_output(sim_cache)
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

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Braunschweig — Simulation Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #f5f5f7;
  --bg-card: #ffffff;
  --bg-soft: #fbfbfd;
  --text: #1d1d1f;
  --text-soft: #6e6e73;
  --line: rgba(0,0,0,0.06);
  --accent: #0066cc;
  --good: #28a745;
  --bad: #d70015;
  --warn: #c79100;
  --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 8px 32px rgba(0,0,0,0.04);
  --radius: 18px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000;
    --bg-card: #1c1c1e;
    --bg-soft: #2c2c2e;
    --text: #f5f5f7;
    --text-soft: #98989d;
    --line: rgba(255,255,255,0.08);
    --shadow: 0 1px 3px rgba(0,0,0,0.6), 0 8px 32px rgba(0,0,0,0.4);
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
               "Helvetica Neue", "Inter", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "ss01", "tnum";
  letter-spacing: -0.01em;
}
.layout { display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }
.sidebar {
  background: var(--bg-card);
  border-right: 1px solid var(--line);
  padding: 24px 18px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.sidebar h1 {
  font-size: 17px;
  font-weight: 700;
  margin: 0 0 4px 4px;
  letter-spacing: -0.02em;
}
.sidebar p.tag {
  font-size: 11px;
  color: var(--text-soft);
  margin: 0 0 24px 4px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.sidebar h2 {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-soft);
  margin: 16px 4px 8px;
}
.run-item {
  display: block;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 120ms;
  border: 1px solid transparent;
}
.run-item:hover { background: var(--bg-soft); }
.run-item.active {
  background: var(--bg-soft);
  border-color: var(--line);
}
.run-item .label { font-size: 13px; font-weight: 600; }
.run-item .meta { font-size: 11px; color: var(--text-soft); margin-top: 2px; }
.run-item .swatch {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}

.main { padding: 40px 56px 80px; max-width: 1400px; }
.hero { margin-bottom: 32px; }
.hero h1 {
  font-size: 38px;
  font-weight: 700;
  letter-spacing: -0.03em;
  margin: 0 0 6px;
}
.hero .sub { color: var(--text-soft); font-size: 15px; }

.kpi-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 28px; }
.card {
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 22px 24px;
  box-shadow: var(--shadow);
}
.card h3 {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-soft);
  margin: 0 0 12px;
}
.kpi { font-size: 36px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05; }
.kpi small { font-size: 14px; font-weight: 500; color: var(--text-soft); margin-left: 4px; }
.kpi-sub { font-size: 12px; color: var(--text-soft); margin-top: 6px; }
.kpi-delta { display: inline-block; font-size: 12px; font-weight: 600; padding: 3px 8px; border-radius: 999px; margin-left: 8px; }
.kpi-delta.good { background: rgba(40,167,69,0.15); color: var(--good); }
.kpi-delta.bad  { background: rgba(215,0,21,0.15); color: var(--bad); }
.kpi-delta.warn { background: rgba(199,145,0,0.15); color: var(--warn); }
.kpi-delta.flat { background: var(--bg-soft); color: var(--text-soft); }

.section { margin-top: 36px; }
.section h2 {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 16px;
}
.charts-grid { display: grid; gap: 16px; grid-template-columns: 1fr 1fr; }
.charts-grid .card.full { grid-column: 1 / -1; }
.chart-wrap { position: relative; height: 300px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: var(--text-soft); padding: 8px 10px; border-bottom: 1px solid var(--line); }
td { padding: 8px 10px; border-bottom: 1px solid var(--line); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }

.pill {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.pill.ok  { background: rgba(40,167,69,0.15); color: var(--good); }
.pill.fail{ background: rgba(215,0,21,0.15); color: var(--bad); }

.legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; font-size: 12px; color: var(--text-soft); }
.legend span::before { content: "■"; margin-right: 4px; }

.toggle-row { display:flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px;}
.toggle {
  font-size: 12px; font-weight: 600;
  padding: 6px 12px; border-radius: 999px;
  background: var(--bg-soft); color: var(--text-soft);
  cursor: pointer; user-select: none;
  border: 1px solid var(--line);
}
.toggle.on { background: var(--accent); color: #fff; border-color: var(--accent); }

.muted { color: var(--text-soft); font-size: 12px; }
.empty { color: var(--text-soft); font-size: 13px; padding: 12px; }
hr.soft { border: none; border-top: 1px solid var(--line); margin: 24px 0; }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <h1>BS Simulation</h1>
    <p class="tag">Dashboard · MiD 2023 / eqasim</p>

    <h2>Runs</h2>
    <div id="run-list"></div>

    <h2 style="margin-top:24px">Active</h2>
    <div id="active-list" class="muted">—</div>
  </aside>
  <main class="main" id="main"></main>
</div>

<script>
const RUNS_DATA = __RUNS_JSON__;
const PALETTE = ['#0066cc', '#ff9500', '#34c759', '#af52de', '#ff3b30', '#5ac8fa', '#ffcc00'];
const MODE_COLORS = {
  car: '#0066cc', pt: '#34c759', bicycle: '#ff9500', walk: '#af52de', car_passenger: '#5ac8fa',
};

let activeIds = [];

function $(sel, root=document) { return root.querySelector(sel); }
function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; }
function fmt(n, dp=0) { if (n === null || n === undefined || Number.isNaN(n)) return '—'; return Number(n).toLocaleString('de-DE', {minimumFractionDigits: dp, maximumFractionDigits: dp}); }
function fmtPct(n, dp=1) { if (n===null||n===undefined||Number.isNaN(n)) return '—'; return Number(n).toFixed(dp) + '%'; }
function deltaPill(diff, unit='') {
  if (diff === null || diff === undefined || Number.isNaN(diff)) return '';
  const abs = Math.abs(diff);
  const cls = abs < 1 ? 'flat' : abs < 5 ? 'warn' : (diff > 0 ? 'bad' : 'good');
  const sign = diff > 0 ? '+' : '';
  return `<span class="kpi-delta ${cls}">${sign}${diff.toFixed(1)}${unit}</span>`;
}

function renderSidebar() {
  const list = $('#run-list');
  list.innerHTML = '';
  if (RUNS_DATA.length === 0) {
    list.innerHTML = '<p class="muted" style="margin:8px 4px">No runs yet.</p>';
    return;
  }
  RUNS_DATA.slice().reverse().forEach((r, idx) => {
    const item = el('div', 'run-item');
    if (activeIds.includes(r.run_id)) item.classList.add('active');
    const colorIdx = RUNS_DATA.findIndex(x => x.run_id === r.run_id) % PALETTE.length;
    const color = PALETTE[colorIdx];
    item.innerHTML = `
      <div class="label"><span class="swatch" style="background:${color}"></span>${r.label || r.run_id}</div>
      <div class="meta">${r.created_at?.replace('T',' ').slice(0,16) ?? ''} · ${r.sample_rate ? (r.sample_rate*100)+'%' : '—'}</div>
    `;
    item.onclick = (e) => {
      if (e.shiftKey || e.metaKey || e.ctrlKey) {
        if (activeIds.includes(r.run_id)) activeIds = activeIds.filter(x => x !== r.run_id);
        else activeIds.push(r.run_id);
      } else {
        activeIds = [r.run_id];
      }
      if (activeIds.length === 0) activeIds = [r.run_id];
      render();
    };
    list.appendChild(item);
  });
}

function renderActiveList() {
  const a = $('#active-list');
  a.innerHTML = '';
  if (activeIds.length === 0) { a.textContent = '—'; return; }
  activeIds.forEach((id, i) => {
    const r = RUNS_DATA.find(x => x.run_id === id);
    if (!r) return;
    const colorIdx = RUNS_DATA.findIndex(x => x.run_id === r.run_id) % PALETTE.length;
    a.appendChild(el('div', '', `
      <div style="font-size:12px;margin-bottom:3px">
        <span class="swatch" style="background:${PALETTE[colorIdx]}"></span>
        <strong>${r.label || r.run_id}</strong>
      </div>
    `));
  });
  a.appendChild(el('div', 'muted', '<br>Tip: <em>Shift</em>+click to compare multiple runs.'));
}

function activeRuns() { return activeIds.map(id => RUNS_DATA.find(r => r.run_id === id)).filter(Boolean); }
function colorFor(run) { const i = RUNS_DATA.findIndex(r => r.run_id === run.run_id) % PALETTE.length; return PALETTE[i]; }

function render() {
  if (RUNS_DATA.length === 0) {
    $('#main').innerHTML = `<div class="hero"><h1>No data</h1><p class="sub">Create a run with:<br><code>python -m braunschweig.analysis.dashboard.build_dashboard --output-dir … --sim-cache … --label "v1"</code></p></div>`;
    renderSidebar();
    renderActiveList();
    return;
  }
  if (activeIds.length === 0) activeIds = [RUNS_DATA[RUNS_DATA.length-1].run_id];
  renderSidebar();
  renderActiveList();
  const runs = activeRuns();
  const main = $('#main');
  main.innerHTML = '';

  // Hero
  const hero = el('div', 'hero');
  const lead = runs[0];
  hero.innerHTML = `
    <h1>Braunschweig Simulation</h1>
    <p class="sub">${runs.length === 1 ? 'Run' : runs.length+' runs compared'} · MiD 2023 ZGB as reference · ${lead.created_at?.replace('T',' ').slice(0,16) ?? ''}</p>
  `;
  main.appendChild(hero);

  main.appendChild(renderKPIGrid(runs));
  main.appendChild(renderModeSection(runs));
  main.appendChild(renderDistanceSection(runs));
  main.appendChild(renderTimeOfDaySection(runs));
  main.appendChild(renderConvergenceSection(runs));
  main.appendChild(renderPerKreisSimSection(runs));
  main.appendChild(renderODSection(runs));
  main.appendChild(renderPerKreisSection(runs));
  main.appendChild(renderQualitySection(runs));
}

function renderKPIGrid(runs) {
  const sec = el('div', 'kpi-grid');

  // KPI 1 — Persons
  sec.appendChild(kpiCard(
    'Persons',
    runs.map(r => ({label: r.label, value: r.eqasim?.n_persons, color: colorFor(r)})),
    v => fmt(v),
    runs[0].eqasim?.sample_rate ? `Sampling: ${(runs[0].eqasim.sample_rate*100).toFixed(0)} %` : ''
  ));

  // Trips per person
  sec.appendChild(kpiCard(
    'Trips / person',
    runs.map(r => ({label: r.label, value: r.eqasim?.trips_per_person, color: colorFor(r)})),
    v => fmt(v, 2),
    'MiD-DE mean ≈ 3.0–3.5'
  ));

  // Mean trip km
  sec.appendChild(kpiCard(
    'Mean trip distance',
    runs.map(r => ({label: r.label, value: r.matsim?.mean_trip_km, color: colorFor(r)})),
    v => fmt(v, 1) + ' km',
    'from eqasim_trips.csv'
  ));

  // Commute mean km vs MiD
  sec.appendChild(kpiCard(
    'Mean commute',
    runs.map(r => {
      const c = r.comparisons?.commute_mean_km;
      return {label: r.label, value: c?.sim, sub: c ? deltaPill(c.diff_pct, '%') : '', color: colorFor(r)};
    }),
    v => fmt(v, 1) + ' km',
    `MiD P13 target: ${runs[0].mid_reference?.p13_mean_km_zgb?.toFixed(1)} km`
  ));

  // Earth-mover dist
  const emds = runs.map(r => r.comparisons?.distance_distribution?.emd);
  sec.appendChild(kpiCard(
    'Distance EMD vs MiD',
    runs.map((r, i) => ({label: r.label, value: emds[i], color: colorFor(r)})),
    v => v == null ? '—' : v.toFixed(3),
    'Quality threshold ≤ 0.080'
  ));

  // Final score
  sec.appendChild(kpiCard(
    'Final score',
    runs.map(r => ({label: r.label, value: r.matsim?.score_final, color: colorFor(r)})),
    v => v == null ? '—' : v.toFixed(2),
    runs[0].matsim?.last_iteration != null ? `after iter ${runs[0].matsim.last_iteration}${runs[0].matsim.terminated_early ? ' (early stop)' : ''}` : ''
  ));

  // Iteration count
  sec.appendChild(kpiCard(
    'Iterations',
    runs.map(r => ({label: r.label, value: r.matsim?.last_iteration, color: colorFor(r)})),
    v => v == null ? '—' : (v + 1) + '',
    runs[0].matsim?.mean_iter_minutes ? `⌀ ${runs[0].matsim.mean_iter_minutes} min/iter` : ''
  ));

  // Female / urban / employed share
  sec.appendChild(kpiCard(
    'Employed',
    runs.map(r => ({label: r.label, value: r.eqasim?.share_employed_pct, color: colorFor(r)})),
    v => fmtPct(v, 1),
    'MiD P9: ~50 %'
  ));

  return sec;
}

function kpiCard(title, items, fmtFn, sub='') {
  const c = el('div', 'card');
  c.appendChild(el('h3', '', title));
  items.forEach((it, i) => {
    const row = el('div', '', '');
    if (items.length === 1) {
      row.innerHTML = `<div class="kpi" style="color:${it.color}">${fmtFn(it.value)}${it.sub || ''}</div>`;
    } else {
      row.style.display = 'flex'; row.style.alignItems = 'baseline'; row.style.justifyContent = 'space-between';
      row.style.marginBottom = i === items.length-1 ? '0' : '4px';
      row.innerHTML = `
        <div style="font-size:12px;color:var(--text-soft);max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          <span class="swatch" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${it.color};margin-right:6px"></span>${it.label}
        </div>
        <div style="font-size:18px;font-weight:600">${fmtFn(it.value)} ${it.sub || ''}</div>
      `;
    }
    c.appendChild(row);
  });
  if (sub) c.appendChild(el('div', 'kpi-sub', sub));
  return c;
}

function sectionWrap(title) {
  const s = el('div', 'section');
  s.appendChild(el('h2', '', title));
  return s;
}

function renderModeSection(runs) {
  const s = sectionWrap('Mode share');
  const grid = el('div', 'charts-grid');

  // Card 1: All-trip mode share — sim final
  const c1 = el('div', 'card');
  c1.appendChild(el('h3','', 'All trips share (final)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const allModes = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mode_share_pct_final || {}))));
  new Chart(wrap1.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allModes,
      datasets: runs.map(r => ({
        label: r.label,
        data: allModes.map(m => r.matsim?.mode_share_pct_final?.[m] ?? 0),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: '%'}),
  });

  // Card 2: Work-commute sim vs MiD
  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Commute trips — Sim vs. MiD P12_1'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const cmp0 = runs[0].comparisons?.work_mode_share;
  if (cmp0) {
    const datasets = [];
    runs.forEach(r => {
      const cmp = r.comparisons?.work_mode_share;
      if (!cmp) return;
      datasets.push({label: r.label + ' (Sim)', data: cmp.sim_pct, backgroundColor: colorFor(r), borderRadius: 6});
    });
    datasets.push({label: 'MiD 2023', data: cmp0.mid_pct, backgroundColor: '#999', borderRadius: 6, borderColor: '#000', borderWidth: 1});
    new Chart(wrap2.appendChild(document.createElement('canvas')), {
      type: 'bar',
      data: { labels: cmp0.modes, datasets },
      options: chartOpts({yLabel: '%'}),
    });
    c2.appendChild(el('p','muted', cmp0.note || ''));
  } else {
    c2.appendChild(el('p','empty','No commute mode-share data.'));
  }

  // Card 3: Mode share evolution (single run only — fan out per mode)
  const c3 = el('div', 'card full');
  c3.appendChild(el('h3','', 'Mode share evolution (across iterations)'));
  const wrap3 = el('div', 'chart-wrap'); c3.appendChild(wrap3); grid.appendChild(c3);
  const datasets3 = [];
  runs.forEach(r => {
    const ev = r.matsim?.mode_share_evolution;
    if (!ev) return;
    (r.matsim.modes || []).forEach((m, i) => {
      datasets3.push({
        label: `${r.label} · ${m}`,
        data: ev[m],
        borderColor: MODE_COLORS[m] || PALETTE[i],
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        borderDash: runs.length > 1 && r === runs[0] ? [] : (runs.length > 1 ? [4,3] : []),
      });
    });
  });
  if (datasets3.length) {
    new Chart(wrap3.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.mode_share_evolution.iterations, datasets: datasets3 },
      options: chartOpts({yLabel: '%', xLabel: 'Iteration'}),
    });
  } else {
    c3.appendChild(el('p','empty','—'));
  }

  s.appendChild(grid);
  return s;
}

function renderDistanceSection(runs) {
  const s = sectionWrap('Trip and commute distances');
  const grid = el('div', 'charts-grid');

  // Card 1: commute distance distribution vs MiD bands
  const c1 = el('div', 'card full');
  c1.appendChild(el('h3', '', 'Commute distance distribution — Sim vs. MiD P13 (ZGB)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const dd = runs[0].comparisons?.distance_distribution;
  if (dd) {
    const datasets = [];
    runs.forEach(r => {
      const d = r.comparisons?.distance_distribution;
      if (!d) return;
      datasets.push({label: r.label + ' (Sim)', data: d.sim_pct, backgroundColor: colorFor(r), borderRadius: 6});
    });
    datasets.push({label: 'MiD 2023', data: dd.mid_pct, backgroundColor: '#bbb', borderRadius: 6});
    new Chart(wrap1.appendChild(document.createElement('canvas')), {
      type: 'bar',
      data: { labels: dd.bands, datasets },
      options: chartOpts({yLabel: '%', xLabel: 'km class'}),
    });
    const emd = runs[0].comparisons.distance_distribution.emd;
    const ok = runs[0].comparisons.distance_distribution.ok;
    c1.appendChild(el('p','muted',`EMD = ${emd.toFixed(3)} (threshold ≤ 0.08) <span class="pill ${ok?'ok':'fail'}">${ok?'OK':'FAIL'}</span>`));
  } else {
    c1.appendChild(el('p','empty','—'));
  }

  // Card 2: mean km by mode
  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Mean distance by mode'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const allModes = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mean_km_by_mode || {}))));
  new Chart(wrap2.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allModes,
      datasets: runs.map(r => ({
        label: r.label,
        data: allModes.map(m => r.matsim?.mean_km_by_mode?.[m] ?? null),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: 'km'}),
  });

  // Card 3: mean km by purpose
  const c3 = el('div', 'card');
  c3.appendChild(el('h3','', 'Mean distance by purpose'));
  const wrap3 = el('div', 'chart-wrap'); c3.appendChild(wrap3); grid.appendChild(c3);
  const allPur = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mean_km_by_purpose || {}))));
  new Chart(wrap3.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allPur,
      datasets: runs.map(r => ({
        label: r.label,
        data: allPur.map(p => r.matsim?.mean_km_by_purpose?.[p] ?? null),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: 'km'}),
  });

  s.appendChild(grid);
  return s;
}

function renderConvergenceSection(runs) {
  const s = sectionWrap('Convergence');
  const grid = el('div', 'charts-grid');

  const c1 = el('div', 'card');
  c1.appendChild(el('h3','', 'Score (avg_executed)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const ds1 = runs.map(r => r.matsim?.score_evolution ? ({
    label: r.label,
    data: r.matsim.score_evolution.avg_executed,
    borderColor: colorFor(r),
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
  }) : null).filter(Boolean);
  if (ds1.length) {
    new Chart(wrap1.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.score_evolution.iterations, datasets: ds1 },
      options: chartOpts({yLabel: 'score', xLabel: 'Iteration'}),
    });
  } else c1.appendChild(el('p','empty','—'));

  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Mean iter trip distance'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const ds2 = runs.map(r => r.matsim?.distance_evolution ? ({
    label: r.label,
    data: r.matsim.distance_evolution.avg_trip_km,
    borderColor: colorFor(r),
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
  }) : null).filter(Boolean);
  if (ds2.length) {
    new Chart(wrap2.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.distance_evolution.iterations, datasets: ds2 },
      options: chartOpts({yLabel: 'km', xLabel: 'Iteration'}),
    });
  } else c2.appendChild(el('p','empty','—'));

  s.appendChild(grid);
  return s;
}

function renderPerKreisSection(runs) {
  const s = sectionWrap('Per-Kreis reference (MiD)');
  const card = el('div', 'card full');
  const ref = runs[0].mid_reference;
  if (!ref?.available || !ref.p13_per_kreis?.length) {
    card.appendChild(el('p','empty','MiD reference not loaded.'));
    s.appendChild(card);
    return s;
  }
  const tbl = el('table');
  tbl.innerHTML = `
    <thead><tr>
      <th>Kreis</th><th>ARS</th>
      <th class="num">MiD mean commute km</th>
      <th class="num">MiD car %</th>
      <th class="num">MiD PT %</th>
      <th class="num">MiD bicycle %</th>
      <th class="num">MiD walk %</th>
      <th class="num">n (weighted)</th>
    </tr></thead>
    <tbody></tbody>
  `;
  const tb = tbl.querySelector('tbody');
  ref.p13_per_kreis.forEach(k => {
    const p12 = (ref.p12_per_kreis || []).find(x => x.ars5 === k.ars5) || {};
    const tr = el('tr');
    tr.innerHTML = `
      <td>${k.name}</td><td>${k.ars5}</td>
      <td class="num">${k.mean_km.toFixed(1)}</td>
      <td class="num">${p12.auto?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.oeffentlich?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.fahrrad?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.zu_fuss?.toFixed(0) ?? '—'}</td>
      <td class="num">${k.n_weighted.toFixed(0)}</td>
    `;
    tb.appendChild(tr);
  });
  card.appendChild(tbl);
  card.appendChild(el('p','muted','Note: P12_1 reports “every mode used” per commute, so rows can sum >100 %. Per-Kreis sim values are shown in the dedicated Sim section above.'));
  s.appendChild(card);
  return s;
}

function renderQualitySection(runs) {
  const s = sectionWrap('Quality checks');
  const grid = el('div', 'charts-grid');
  runs.forEach(r => {
    const c = el('div', 'card');
    c.appendChild(el('h3','', r.label));
    const checks = [
      ['EMD ≤ 0.08 (MiD distance)', r.comparisons?.distance_distribution?.ok, r.comparisons?.distance_distribution?.emd?.toFixed(3)],
      ['Mean commute within ±20 % of MiD', Math.abs(r.comparisons?.commute_mean_km?.diff_pct ?? 999) <= 20, (r.comparisons?.commute_mean_km?.diff_pct?.toFixed(1) ?? '—') + '%'],
      ['Trips/person 2.5–4.0', r.eqasim?.trips_per_person >= 2.5 && r.eqasim?.trips_per_person <= 4.0, r.eqasim?.trips_per_person],
      ['p95 commute ≤ 200 km', (r.matsim?.commute?.p95_km ?? 999) <= 200, r.matsim?.commute?.p95_km?.toFixed(0) + ' km'],
      ['Score increasing (final > iter 0)', (r.matsim?.score_evolution?.avg_executed?.slice(-1)[0] ?? -1e9) > (r.matsim?.score_evolution?.avg_executed?.[0] ?? 0), r.matsim?.score_final],
    ];
    const tbl = el('table');
    tbl.innerHTML = '<thead><tr><th>Check</th><th class="num">Value</th><th>Status</th></tr></thead><tbody></tbody>';
    const tb = tbl.querySelector('tbody');
    checks.forEach(([name, ok, val]) => {
      const tr = el('tr');
      tr.innerHTML = `<td>${name}</td><td class="num">${val ?? '—'}</td><td><span class="pill ${ok?'ok':'fail'}">${ok?'OK':'FAIL'}</span></td>`;
      tb.appendChild(tr);
    });
    c.appendChild(tbl);
    grid.appendChild(c);
  });
  s.appendChild(grid);
  return s;
}

function renderTimeOfDaySection(runs) {
  const s = sectionWrap('Time-of-day distribution');
  const grid = el('div', 'charts-grid');
  const lead = runs[0];
  const tod = lead.matsim?.time_of_day;
  if (!tod) {
    const c = el('div','card full');
    c.appendChild(el('p','empty','No time-of-day data (run an updated dashboard build).'));
    grid.appendChild(c); s.appendChild(grid); return s;
  }

  // Trips per hour (totals across runs, line chart)
  const c1 = el('div','card full');
  c1.appendChild(el('h3','', 'Trips per hour (totals)'));
  const w1 = el('div','chart-wrap'); c1.appendChild(w1); grid.appendChild(c1);
  const ds1 = runs.map(r => r.matsim?.time_of_day ? ({
    label: r.label,
    data: r.matsim.time_of_day.total_per_hour,
    borderColor: colorFor(r),
    backgroundColor: colorFor(r) + '22',
    borderWidth: 2, pointRadius: 0, fill: true, tension: 0.25,
  }) : null).filter(Boolean);
  new Chart(w1.appendChild(document.createElement('canvas')), {
    type: 'line',
    data: { labels: tod.hours.map(h => h.toString().padStart(2,'0')+':00'), datasets: ds1 },
    options: chartOpts({yLabel: 'trips', xLabel: 'hour'}),
  });

  // Stacked-by-mode (lead run only)
  const c2 = el('div','card');
  c2.appendChild(el('h3','', `Trips per hour by mode \u2014 ${lead.label}`));
  const w2 = el('div','chart-wrap'); c2.appendChild(w2); grid.appendChild(c2);
  const modes = Object.keys(tod.by_mode);
  new Chart(w2.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: tod.hours,
      datasets: modes.map((m, i) => ({
        label: m,
        data: tod.by_mode[m],
        backgroundColor: MODE_COLORS[m] || PALETTE[i % PALETTE.length],
        stack: 'm',
      })),
    },
    options: { ...chartOpts({yLabel: 'trips', xLabel: 'hour'}),
      scales: { x: {stacked: true}, y: {stacked: true, beginAtZero: true} } },
  });

  // Stacked-by-purpose
  const c3 = el('div','card');
  c3.appendChild(el('h3','', `Trips per hour by purpose \u2014 ${lead.label}`));
  const w3 = el('div','chart-wrap'); c3.appendChild(w3); grid.appendChild(c3);
  const purs = Object.keys(tod.by_purpose);
  new Chart(w3.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: tod.hours,
      datasets: purs.map((p, i) => ({
        label: p, data: tod.by_purpose[p],
        backgroundColor: PALETTE[i % PALETTE.length], stack: 'p',
      })),
    },
    options: { ...chartOpts({yLabel: 'trips', xLabel: 'hour'}),
      scales: { x: {stacked: true}, y: {stacked: true, beginAtZero: true} } },
  });

  s.appendChild(grid);
  return s;
}

function renderPerKreisSimSection(runs) {
  const s = sectionWrap('Per-Kreis simulation values');
  const card = el('div','card full');
  const lead = runs[0];
  const sim = lead.matsim?.per_kreis_sim;
  const ref = lead.mid_reference;
  if (!sim || Object.keys(sim).length === 0) {
    card.appendChild(el('p','empty','Per-Kreis spatial join unavailable (VG250 missing or geopandas error). Re-run the dashboard build to populate.'));
    s.appendChild(card); return s;
  }
  const tbl = el('table');
  tbl.innerHTML = `
    <thead><tr>
      <th>Kreis</th><th class="num">Sim n trips</th>
      <th class="num">Sim mean km</th><th class="num">MiD mean km</th><th class="num">\u0394 km</th>
      <th class="num">Sim car %</th><th class="num">Sim PT %</th>
      <th class="num">Sim bicycle %</th><th class="num">Sim walk %</th>
    </tr></thead><tbody></tbody>`;
  const tb = tbl.querySelector('tbody');
  Object.entries(sim).forEach(([ars5, k]) => {
    const refKreis = (ref?.p13_per_kreis || []).find(x => x.ars5 === ars5);
    const dKm = refKreis ? (k.mean_km - refKreis.mean_km) : null;
    const ms = k.mode_share_pct || {};
    const tr = el('tr');
    tr.innerHTML = `
      <td>${k.name}</td>
      <td class="num">${k.n_trips.toLocaleString()}</td>
      <td class="num">${k.mean_km.toFixed(1)}</td>
      <td class="num">${refKreis ? refKreis.mean_km.toFixed(1) : '\u2014'}</td>
      <td class="num">${dKm == null ? '\u2014' : (dKm > 0 ? '+' : '') + dKm.toFixed(1)}</td>
      <td class="num">${(ms.car ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.pt ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.bicycle ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.walk ?? 0).toFixed(0)}</td>
    `;
    tb.appendChild(tr);
  });
  card.appendChild(tbl);
  card.appendChild(el('p','muted','Sim trips classified by spatial join of the trip origin (home end of commute) against VG250 Kreis polygons. Sim mode-share is the main mode; MiD P12_1 is any-mode-used and not directly comparable.'));
  s.appendChild(card);
  return s;
}

function renderODSection(runs) {
  const s = sectionWrap('Origin-Destination by activity type');
  const lead = runs[0];
  const od = lead.matsim?.od_matrix;
  const card = el('div','card full');
  if (!od || !od.purposes?.length) {
    card.appendChild(el('p','empty','OD matrix unavailable for this run.'));
    s.appendChild(card); return s;
  }
  // Controls
  const ctrl = el('div','toggle-row');
  od.purposes.forEach((p, i) => {
    const t = el('span','toggle' + (i === 0 ? ' on' : ''), p);
    t.dataset.pur = p;
    t.onclick = () => {
      ctrl.querySelectorAll('.toggle').forEach(x => x.classList.remove('on'));
      t.classList.add('on');
      drawHeat(p);
    };
    ctrl.appendChild(t);
  });
  card.appendChild(ctrl);
  const heat = el('div'); heat.style.overflowX = 'auto';
  card.appendChild(heat);
  card.appendChild(el('p','muted','Rows = origin Kreis, columns = destination Kreis. Cells encode trip counts for the selected purpose; colour intensity is normalised per matrix. \u201cOutside ZGB\u201d aggregates trips touching Kreise outside the 8-Kreis ZGB area.'));

  function drawHeat(pur) {
    const m = od.matrices[pur];
    const max = Math.max(1, ...m.flat());
    let html = '<table style="border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums">';
    html += '<thead><tr><th></th>';
    od.zone_names.forEach(n => { html += `<th style="padding:4px 6px;writing-mode:vertical-rl;transform:rotate(180deg);height:90px">${n}</th>`; });
    html += '</tr></thead><tbody>';
    m.forEach((row, i) => {
      html += `<tr><td style="padding:4px 8px;font-weight:600">${od.zone_names[i]}</td>`;
      row.forEach(v => {
        const a = v / max;
        const c = `rgba(0, 102, 204, ${a.toFixed(2)})`;
        const txt = v >= 100 ? Math.round(v).toLocaleString() : (v > 0 ? v.toString() : '');
        html += `<td style="padding:4px 6px;text-align:right;background:${c};color:${a>0.5?'#fff':'inherit'};border:1px solid var(--line)">${txt}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    heat.innerHTML = html;
  }
  drawHeat(od.purposes[0]);

  s.appendChild(card);
  return s;
}

function chartOpts({yLabel='', xLabel=''}={}) {
  const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const grid = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const tick = isDark ? '#98989d' : '#6e6e73';
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: tick, font: {size: 11, family: '-apple-system, BlinkMacSystemFont, system-ui, sans-serif'} } },
      tooltip: { mode: 'index', intersect: false }
    },
    scales: {
      x: { grid: { color: grid, drawBorder: false }, ticks: { color: tick, font: {size: 11} }, title: { display: !!xLabel, text: xLabel, color: tick } },
      y: { grid: { color: grid, drawBorder: false }, ticks: { color: tick, font: {size: 11} }, title: { display: !!yLabel, text: yLabel, color: tick }, beginAtZero: true },
    },
  };
}

render();
</script>
</body>
</html>"""


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
