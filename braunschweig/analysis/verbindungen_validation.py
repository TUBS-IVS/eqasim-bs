"""Validate the synthetic home->work assignment against VerBindungen (#124).

Compares the model's POTENTIAL commute OD (every employed synthetic person's
home cell x work cell; NOT MATSim trips) against the 2019 VerBindungen QZM on
the 44 ZGB Verkehrszellen, share-based and censoring-aware, plus a
production-margin check against the BA workers-at-home margins and a
Kreis-level vintage-drift diagnostic (2019 reference vs. 2025 Pendleratlas).

Universe notes (carried into every output):
- QZM = ALL workers incl. Beamte/Selbststaendige (2019, potential commutes,
  relations < 10 censored).
- BA margins = SvB + aGeB only (2019, rounded to 10s, Dominanz-suppressed).
- Model = all employed synthetic persons with an assigned work location
  (uniform sample; share-based metrics, sampling rate cancels).

Checks:
A. margin_check          -- workers per home cell, model vs. BA WO margins.
B. conditional_od_check  -- P(work cell | home cell) vs. QZM on observed
                            relations (the #132 A/B axis) + EMD over
                            centroid-distance bands + intra-cell share.
C. vintage_drift_check   -- QZM Kreis-pair shares (2019) vs. Pendleratlas
                            (2025), cross-Kreis pairs only (the raw pendler
                            stage carries no intra-Kreis rows).
"""
from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pandas as pd

DISTANCE_BANDS_KM = [0.0, 2.0, 5.0, 10.0, 20.0, 50.0, float("inf")]


def assign_points_to_cells(gdf_points: gpd.GeoDataFrame,
                           df_cells: gpd.GeoDataFrame) -> pd.Series:
    """Map each point to the cell containing it; NaN when outside every cell.

    Both frames must be EPSG:25832. The original index of *gdf_points* is
    preserved so the caller can join the result back.
    """
    if gdf_points.crs is None or gdf_points.crs.to_epsg() != 25832:
        raise ValueError("points must be EPSG:25832")
    joined = gpd.sjoin(
        gdf_points[["geometry"]], df_cells[["cell_id", "geometry"]],
        how="left", predicate="within",
    )
    # A point exactly on a shared boundary can match twice; keep the first.
    joined = joined[~joined.index.duplicated(keep="first")]
    return joined["cell_id"].reindex(gdf_points.index)


def conditional_od_check(df_model_od: pd.DataFrame,
                         df_ref_od: pd.DataFrame) -> tuple:
    """Per-origin TVD of P(dest | origin), censoring-aware.

    For each origin cell with reference mass: restrict the model row to the
    reference-observed destinations, renormalise, and compute
    TVD = 0.5 * sum |p_model - p_ref|. Model mass on relations ABSENT from the
    reference (censored < 10 upstream or true zero) is reported per origin as
    ``censored_model_share`` (of the full model row mass), never silently
    dropped. Origins without reference mass are skipped (reported in stats).
    Overall ``weighted_tvd`` weights origins by reference row mass.

    An empty reference frame raises ValueError (fail-early): it means the
    upstream loader/clip produced no relations, not that the model fits.
    """
    if df_ref_od.empty:
        raise ValueError(
            "[verbindungen_validation] reference OD frame is empty -- no "
            "relations to compare against (check the work_od loader/clip)"
        )
    ref = df_ref_od.groupby(["origin_cell_id", "destination_cell_id"])["commuters"].sum()
    model = df_model_od.groupby(["origin_cell_id", "destination_cell_id"])["commuters"].sum()

    rows = []
    ref_by_origin = ref.groupby(level=0).sum()
    for origin, ref_row_mass in ref_by_origin.items():
        p_ref = ref.loc[origin] / ref_row_mass
        model_row = model.loc[origin] if origin in model.index.get_level_values(0) else pd.Series(dtype=float)
        model_full = float(model_row.sum())
        observed = model_row.reindex(p_ref.index).fillna(0.0)
        observed_mass = float(observed.sum())
        censored_share = (model_full - observed_mass) / model_full if model_full > 0 else np.nan
        if observed_mass > 0:
            p_model = observed / observed_mass
            tvd = 0.5 * float((p_model - p_ref).abs().sum())
        else:
            tvd = np.nan
        rows.append(dict(
            origin_cell_id=origin,
            ref_row_commuters=float(ref_row_mass),
            model_row_commuters=model_full,
            tvd=tvd,
            censored_model_share=censored_share,
        ))
    per_origin = pd.DataFrame(rows)

    valid = per_origin.dropna(subset=["tvd"])
    weights = valid["ref_row_commuters"] / valid["ref_row_commuters"].sum()
    model_total = float(model.sum())
    observed_pairs = set(ref.index)
    censored_total = float(
        model[~model.index.isin(observed_pairs)].sum()
    ) if model_total else 0.0
    stats = dict(
        weighted_tvd=float((valid["tvd"] * weights).sum()) if len(valid) else np.nan,
        n_origins_compared=int(len(valid)),
        n_origins_skipped_no_model=int(per_origin["tvd"].isna().sum()),
        censored_model_share=censored_total / model_total if model_total else np.nan,
    )
    return per_origin, stats


def band_shares(df_od: pd.DataFrame, df_cells: gpd.GeoDataFrame,
                bands_km: list) -> pd.Series:
    """Commuter-mass share per centroid-distance band (index = band label)."""
    cx = df_cells.set_index("cell_id")["centroid_x"]
    cy = df_cells.set_index("cell_id")["centroid_y"]
    dx = df_od["origin_cell_id"].map(cx) - df_od["destination_cell_id"].map(cx)
    dy = df_od["origin_cell_id"].map(cy) - df_od["destination_cell_id"].map(cy)
    dist_km = np.sqrt(dx.to_numpy() ** 2 + dy.to_numpy() ** 2) / 1000.0
    labels = [f"{bands_km[i]:g}-{bands_km[i + 1]:g}km" for i in range(len(bands_km) - 1)]
    band = pd.cut(dist_km, bins=bands_km, labels=labels, right=False,
                  include_lowest=True)
    mass = df_od.groupby(band, observed=False)["commuters"].sum()
    return mass / mass.sum()


def emd_1d(shares_a: pd.Series, shares_b: pd.Series) -> float:
    """1-D earth mover's distance over ordered band shares (unit: bands)."""
    a = shares_a.to_numpy(dtype=float)
    b = shares_b.reindex(shares_a.index).to_numpy(dtype=float)
    return float(np.abs(np.cumsum(a - b)).sum())


def margin_check(model_counts: pd.Series, ref_counts: pd.Series) -> dict:
    """Share-based SRMSE + Pearson r of per-cell margins (NA cells dropped)."""
    df = pd.DataFrame({"model": model_counts, "ref": ref_counts}).dropna()
    df = df[df["ref"] > 0]
    m = df["model"] / df["model"].sum()
    r = df["ref"] / df["ref"].sum()
    srmse = float(np.sqrt(((m - r) ** 2).mean()) / r.mean())
    # A constant share vector has zero variance; its Pearson r is undefined
    # and stays NaN -- suppress only numpy's divide warning, not the NaN.
    with np.errstate(invalid="ignore"):
        pearson = float(np.corrcoef(m, r)[0, 1]) if len(df) > 1 else np.nan
    return dict(srmse=srmse, pearson_r=pearson, n_cells=int(len(df)))


def vintage_drift_check(df_ref_od: pd.DataFrame, df_cells: gpd.GeoDataFrame,
                        df_pendler: pd.DataFrame) -> pd.DataFrame:
    """Cross-Kreis pair shares: VerBindungen 2019 vs. BA Pendleratlas 2025.

    Both sides are restricted to CROSS-Kreis pairs within the scope Kreise and
    normalised to shares of the cross-Kreis total (the raw pendler stage has
    no intra-Kreis rows; universes differ -- all workers vs. SvB -- so only
    the share structure is comparable).
    """
    kreis = df_cells.set_index("cell_id")["kreis_id"]
    ref = df_ref_od.copy()
    ref["orig_kreis"] = ref["origin_cell_id"].map(kreis)
    ref["dest_kreis"] = ref["destination_cell_id"].map(kreis)
    ref = ref[ref["orig_kreis"] != ref["dest_kreis"]]
    ref_pairs = ref.groupby(["orig_kreis", "dest_kreis"])["commuters"].sum()

    scope = set(kreis.unique())
    pen = df_pendler[
        df_pendler["orig_ars"].isin(scope) & df_pendler["dest_ars"].isin(scope)
        & (df_pendler["orig_ars"] != df_pendler["dest_ars"])
    ]
    pen_pairs = pen.groupby(["orig_ars", "dest_ars"])["flow"].sum()
    pen_pairs.index = pen_pairs.index.set_names(["orig_kreis", "dest_kreis"])

    idx = ref_pairs.index.union(pen_pairs.index)
    out = pd.DataFrame(index=idx)
    out["commuters_2019"] = ref_pairs.reindex(idx).fillna(0.0)
    out["flow_2025"] = pen_pairs.reindex(idx).fillna(0.0)
    out["share_2019"] = out["commuters_2019"] / out["commuters_2019"].sum()
    out["share_2025"] = out["flow_2025"] / out["flow_2025"].sum()
    out["share_drift"] = out["share_2019"] - out["share_2025"]
    return out.reset_index()
