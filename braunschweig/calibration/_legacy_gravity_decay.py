"""Poisson-GLM gravity-distance-decay calibration (TASK-001).

Fits a Poisson GLM to BA Pendleratlas Kreis-pair flows:

    log E[flow_ij] = α_i (origin FE) + γ_j (destination FE) + β · d_ij

The β estimate is the data-driven distance-decay slope to plug into
``braunschweig.gravity.gravity_slope`` (currently default −0.2, BS override
−0.18). Output is written as JSON to
``eqasim-data/cache_bs/calibration/gravity_beta.json`` and printed.

Run:
    python -m scripts.calibrate_gravity_decay [--scope ZGB] [--max-distance 200]

The default scope is **flows touching ZGB-8** (any orig or dest in ZGB).

Methodological note (2026-04-26)
--------------------------------
The BA Pendleratlas dataset by definition contains **only cross-Kreis
commuters** ("Pendler" = Beschäftigte deren Wohn-Kreis ≠ Arbeits-Kreis).
0 % of the 48 340 OD-pairs are intra-Kreis — see
``scripts/check_pendler_intra.py``. As a consequence:

  - The flow-weighted mean distance implied by the BA-Atlas fit
    (~46 km on ZGB-8 at β = −0.065) is a **conditional** cross-Kreis
    mean. It is **not** directly comparable to MiD `commute_distance`
    means (~20.7 km on ZGB-8, MiD P13 ``mittel``) because MiD includes
    intra-Kreis commuters which dominate short-distance trips.
  - β governs only the within-(orig, dest)-Kreis spread of the
    synthesised Gemeinde-pair flow matrix; the intra/cross share is
    pinned by the BA-Atlas Kreis totals through ``bavaria.ipf``.
  - MiD is a single-day diary survey → infrequent long-distance
    commuters (Wochenpendler) are systematically under-counted in MiD
    but fully present in BA Pendleratlas, which inflates BA tail.

The right MiD reference for ``commute_distance`` validation is the
``mittel`` column of MiD P13 (Wegezweck=Arbeit), not the band-midpoint
approximation. ZGB-8 person-weighted mean = 20.7 km; per-Kreis values
in [eqasim-data/data/braunschweig/mid/mid2023_P13.csv].
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import statsmodels.api as sm

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "eqasim-data" / "data"
OUTPUT_PATH = REPO / "eqasim-data" / "cache_bs" / "calibration" / "gravity_beta.json"

# 8 ZGB Kreise (matches braunschweig.political_prefix in BS configs).
ZGB8 = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")

# Existing pendler CSV file names from braunschweig/data/census/pendler.py.
EIN_CSV = DATA_DIR / "braunschweig" / "statistik_pendler_2026042493412.csv"
AUS_CSV = DATA_DIR / "braunschweig" / "statistik_pendler_2026042493430.csv"

VG250_VSI = (
    "/vsizip/" + str(DATA_DIR / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    + "/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
)


log = logging.getLogger("calibrate_gravity_decay")


# --- BA pendler loader -----------------------------------------------------

NUMERIC_COLS = ["total", "male", "female", "de", "foreign", "apprentice"]


def _read_pendler_csv(path: Path, orientation: str) -> pd.DataFrame:
    raw = pd.read_csv(path, sep=";", skiprows=10, encoding="utf-8", dtype=str)
    if len(raw.columns) != 10:
        raise RuntimeError(f"Unexpected column count in {path}: {len(raw.columns)}")
    if orientation == "ein":
        raw.columns = ["dest_name", "dest_ars", "orig_name", "orig_ars"] + NUMERIC_COLS
    else:
        raw.columns = ["orig_name", "orig_ars", "dest_name", "dest_ars"] + NUMERIC_COLS
    mask = (
        raw["orig_ars"].str.fullmatch(r"\d{5}", na=False)
        & raw["dest_ars"].str.fullmatch(r"\d{5}", na=False)
    )
    df = raw[mask].copy()
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(".", "", regex=False), errors="coerce"
        )
    df = df.dropna(subset=["total"]).copy()
    df["total"] = df["total"].astype(int)
    return df[["orig_ars", "dest_ars", "total"]].rename(columns={"total": "flow"})


def load_pendler() -> pd.DataFrame:
    df = pd.concat(
        [_read_pendler_csv(EIN_CSV, "ein"), _read_pendler_csv(AUS_CSV, "aus")],
        ignore_index=True,
    )
    df = (
        df.groupby(["orig_ars", "dest_ars"], as_index=False)
          .agg(flow=("flow", "max"))
    )
    df = df[df["orig_ars"] != df["dest_ars"]].copy()
    return df


# --- Kreis centroid distances ---------------------------------------------


def load_kreis_centroids() -> gpd.GeoDataFrame:
    df = gpd.read_file(
        VG250_VSI, layer="vg250_krs",
        columns=["ARS", "GEN", "GF"],
        engine="pyogrio",
    )
    df["ars5"] = df["ARS"].astype(str).str[:5]
    df = df.sort_values(["ars5", "GF"], ascending=[True, False])
    names = df.groupby("ars5").agg(kreis_name=("GEN", "first")).reset_index()
    df_geom = df.dissolve(by="ars5", as_index=False)[["ars5", "geometry"]]
    df_geom = df_geom.merge(names, on="ars5", how="left")
    df_geom["centroid"] = df_geom.geometry.centroid
    return df_geom


def kreis_distance_matrix(kreise: gpd.GeoDataFrame) -> pd.DataFrame:
    """Long-form Kreis×Kreis centroid-Euclidean distance (km)."""
    coords = np.array([(p.x, p.y) for p in kreise["centroid"]])
    ids = kreise["ars5"].to_numpy()
    n = len(ids)
    diff = coords[:, None, :] - coords[None, :, :]
    d_km = np.sqrt((diff ** 2).sum(axis=2)) / 1000.0
    rows = pd.DataFrame({
        "orig_ars": np.repeat(ids, n),
        "dest_ars": np.tile(ids, n),
        "distance_km": d_km.reshape(-1),
    })
    return rows[rows["orig_ars"] != rows["dest_ars"]]


# --- Poisson-GLM fit -------------------------------------------------------


def fit_poisson_glm(
    df: pd.DataFrame,
    fix_origins: Iterable[str] | None = None,
) -> dict:
    """Fit Poisson GLM with O+D fixed effects and continuous distance.

    `df` columns: orig_ars, dest_ars, distance_km, flow (int).
    Returns dict with β estimate, std error, z-statistic, p-value, n_obs.
    """
    df = df.copy()
    df = df[df["flow"] >= 0]

    # Build design matrix: O dummies + D dummies + distance.
    O = pd.get_dummies(df["orig_ars"], prefix="O", drop_first=True)
    D = pd.get_dummies(df["dest_ars"], prefix="D", drop_first=True)
    X = pd.concat([O.astype(float), D.astype(float), df[["distance_km"]]], axis=1)
    X = sm.add_constant(X, has_constant="add")

    y = df["flow"].astype(float).to_numpy()
    log.info("Fitting Poisson GLM: %d obs, %d params", len(y), X.shape[1])
    model = sm.GLM(y, X.to_numpy(), family=sm.families.Poisson())
    res = model.fit(maxiter=200, tol=1e-8)

    beta_idx = X.columns.get_loc("distance_km")
    beta = float(res.params[beta_idx])
    se = float(res.bse[beta_idx])
    return {
        "beta": beta,
        "se": se,
        "z": beta / se if se > 0 else float("nan"),
        "log_likelihood": float(res.llf),
        "deviance": float(res.deviance),
        "n_obs": int(len(y)),
        "n_params": int(X.shape[1]),
        "converged": bool(res.converged),
    }


def fit_offset_only(df: pd.DataFrame, beta: float) -> tuple[float, np.ndarray]:
    """Fit O+D fixed effects with β fixed (as offset). Returns (logL, μ̂).

    μ̂_ij is the GLM-predicted flow per row of df.
    """
    df = df.copy()
    O = pd.get_dummies(df["orig_ars"], prefix="O", drop_first=True)
    D = pd.get_dummies(df["dest_ars"], prefix="D", drop_first=True)
    X = pd.concat([O.astype(float), D.astype(float)], axis=1)
    X = sm.add_constant(X, has_constant="add")
    offset = beta * df["distance_km"].to_numpy()
    y = df["flow"].astype(float).to_numpy()
    res = sm.GLM(y, X.to_numpy(), family=sm.families.Poisson(), offset=offset
                ).fit(maxiter=200, tol=1e-8)
    mu = np.asarray(res.mu)
    return float(res.llf), mu


def fit_default_offset_only(df: pd.DataFrame, beta_default: float) -> dict:
    """Backward-compat wrapper: null-comparison fit at a fixed β."""
    llf, _ = fit_offset_only(df, beta_default)
    return {"beta": beta_default, "log_likelihood": llf, "n_obs": int(len(df))}


# --- Joint MLE + MiD calibration -----------------------------------------


def joint_calibrate(
    df: pd.DataFrame,
    target_mean_km: float,
    lam: float = 5.0,
    beta_grid: np.ndarray | None = None,
) -> dict:
    """Grid-search β to minimise -logL + λ · (M_pred(β) − M_MiD)².

    M_pred(β) = Σ μ̂(β)·d / Σ μ̂(β) is the prediction-weighted mean
    distance under O+D FE re-fit at that β. λ controls the trade-off.
    """
    if beta_grid is None:
        beta_grid = np.linspace(-0.30, -0.02, 29)
    d = df["distance_km"].to_numpy()
    rows = []
    for b in beta_grid:
        llf, mu = fit_offset_only(df, float(b))
        m_pred = float((mu * d).sum() / mu.sum())
        rows.append({
            "beta": float(b),
            "logL": llf,
            "mean_pred_km": m_pred,
            "loss": -llf + lam * (m_pred - target_mean_km) ** 2,
        })
    grid = pd.DataFrame(rows)
    best = grid.loc[grid["loss"].idxmin()].to_dict()
    return {
        "lambda": lam,
        "target_mean_km": target_mean_km,
        "best_beta": best["beta"],
        "best_logL": best["logL"],
        "best_mean_pred_km": best["mean_pred_km"],
        "best_loss": best["loss"],
        "grid": grid.to_dict(orient="records"),
    }


# --- Empirical interpolation from pipeline runs ---------------------------


def interpolate_synth_runs(
    runs: list[tuple[float, float]], target_mean_km: float
) -> float:
    """Linear interpolation through (β_i, mean_synth_i) at synthesis level.

    `runs` is a list of (β, observed mean commute km) pairs from the
    1 % pipeline. Returns the β at which the linear interpolant equals
    `target_mean_km`. Requires at least 2 distinct β values.
    """
    runs = sorted(runs, key=lambda x: x[0])
    betas = np.array([r[0] for r in runs])
    means = np.array([r[1] for r in runs])
    if len(runs) < 2 or means.max() == means.min():
        raise ValueError("Need ≥ 2 runs with distinct mean to interpolate")
    return float(np.interp(target_mean_km, means, betas))


# --- Pipeline -------------------------------------------------------------


def calibrate(
    scope: tuple[str, ...] = ZGB8,
    max_distance_km: float = 250.0,
    beta_default: float = -0.18,
    target_mean_km: float = 12.6,
    lam: float = 5.0,
    synth_runs: list[tuple[float, float]] | None = None,
) -> dict:
    log.info("Loading BA pendler …")
    flows = load_pendler()
    log.info("  %d non-self Kreis-pair flows loaded", len(flows))

    log.info("Loading VG250 Kreise + computing centroid distances …")
    kreise = load_kreis_centroids()
    distances = kreis_distance_matrix(kreise)
    log.info("  %d Kreise, %d directed pairs", len(kreise), len(distances))

    df = flows.merge(distances, on=["orig_ars", "dest_ars"], how="inner")
    in_scope = df["orig_ars"].isin(scope) | df["dest_ars"].isin(scope)
    df = df[in_scope & (df["distance_km"] <= max_distance_km)].copy()
    log.info(
        "  scope filter: %d pairs touching ZGB-8 within %.0f km",
        len(df), max_distance_km,
    )
    if len(df) < 100:
        raise RuntimeError(f"Too few obs after scope filter ({len(df)})")

    # 1. Pure MLE (β unconstrained).
    fit = fit_poisson_glm(df)
    log.info(
        "Pure MLE β = %.4f ± %.4f (z=%.2f), log-L=%.1f",
        fit["beta"], fit["se"], fit["z"], fit["log_likelihood"],
    )

    # 2. Default-β null comparison.
    null = fit_default_offset_only(df, beta_default)
    log.info("Default β = %.4f, log-L=%.1f", beta_default, null["log_likelihood"])

    # 3. Joint MLE + MiD-target calibration on Kreis level.
    joint = joint_calibrate(df, target_mean_km=target_mean_km, lam=lam)
    log.info(
        "Joint (λ=%.1f, target=%.1f km) → β=%.4f, M_pred=%.2f km, log-L=%.1f",
        lam, target_mean_km, joint["best_beta"],
        joint["best_mean_pred_km"], joint["best_logL"],
    )

    # 4. Empirical synthesis-level interpolation, if pipeline points provided.
    interp_beta = None
    if synth_runs and len(synth_runs) >= 2:
        try:
            interp_beta = interpolate_synth_runs(synth_runs, target_mean_km)
            log.info(
                "Interpolated synthesis-level β = %.4f from %d pipeline runs "
                "→ target %.1f km",
                interp_beta, len(synth_runs), target_mean_km,
            )
        except ValueError as e:
            log.warning("Interpolation failed: %s", e)

    out = {
        "calibrated": fit,
        "default": null,
        "joint": {k: v for k, v in joint.items() if k != "grid"},
        "joint_grid": joint["grid"],
        "interpolated_beta_synthesis": interp_beta,
        "synth_runs": synth_runs,
        "scope_kreise": list(scope),
        "max_distance_km": max_distance_km,
        "beta_default_used": beta_default,
        "target_mean_km": target_mean_km,
        "lambda": lam,
        "log_likelihood_improvement_pct": (
            100.0 * (fit["log_likelihood"] - null["log_likelihood"])
            / abs(null["log_likelihood"])
        ),
    }
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--max-distance", type=float, default=250.0)
    p.add_argument("--beta-default", type=float, default=-0.18)
    p.add_argument("--target-mean-km", type=float, default=12.6,
                   help="MiD reference mean work-commute distance (ZGB)")
    p.add_argument("--lambda", dest="lam", type=float, default=5.0,
                   help="Penalty weight in joint loss J = -logL + λ(M-M*)²")
    p.add_argument(
        "--synth-run", action="append", default=[],
        metavar="BETA,MEAN_KM",
        help="Pipeline run point as 'beta,mean_km'; repeat for multiple",
    )
    p.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = p.parse_args()

    synth_runs = []
    for s in args.synth_run:
        b, m = s.split(",")
        synth_runs.append((float(b), float(m)))
    if not synth_runs:
        # Defaults: previous BS runs.
        synth_runs = [(-0.18, 8.94), (-0.065, 24.16)]

    out = calibrate(
        scope=ZGB8,
        max_distance_km=args.max_distance,
        beta_default=args.beta_default,
        target_mean_km=args.target_mean_km,
        lam=args.lam,
        synth_runs=synth_runs,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("β written to %s", args.out)
    print("\n=== Gravity-β calibration summary ===")
    print(f"  Pure MLE β              = {out['calibrated']['beta']:+.4f} "
          f"(log-L {out['calibrated']['log_likelihood']:+.1f})")
    print(f"  Default β               = {args.beta_default:+.4f} "
          f"(log-L {out['default']['log_likelihood']:+.1f})")
    print(f"  Joint MLE+MiD β         = {out['joint']['best_beta']:+.4f} "
          f"(M_pred {out['joint']['best_mean_pred_km']:.2f} km, "
          f"log-L {out['joint']['best_logL']:+.1f})")
    if out["interpolated_beta_synthesis"] is not None:
        print(f"  Synth-interpolated β    = {out['interpolated_beta_synthesis']:+.4f} "
              f"(from {len(synth_runs)} pipeline runs → {args.target_mean_km:.1f} km)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
