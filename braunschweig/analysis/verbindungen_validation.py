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


def build_validation_outputs(df_home: gpd.GeoDataFrame,
                             df_work: gpd.GeoDataFrame,
                             df_persons: pd.DataFrame,
                             df_cells: gpd.GeoDataFrame,
                             df_ref_od: pd.DataFrame,
                             df_margins: pd.DataFrame,
                             df_pendler: pd.DataFrame) -> dict:
    """Assemble all validation outputs from raw stage frames (pure, testable)."""
    # --- realised potential OD: employed persons with a work location -------
    df = df_work[["person_id", "geometry"]].merge(
        df_persons[["person_id", "household_id"]], on="person_id", how="left")
    df = df.merge(
        df_home[["household_id", "geometry"]].rename(
            columns={"geometry": "home_geometry"}),
        on="household_id", how="left")

    work_pts = gpd.GeoDataFrame(df[["person_id"]], geometry=df["geometry"],
                                crs=df_work.crs)
    home_pts = gpd.GeoDataFrame(df[["person_id"]], geometry=df["home_geometry"],
                                crs=df_home.crs)
    df["work_cell_id"] = assign_points_to_cells(work_pts, df_cells).to_numpy()
    df["home_cell_id"] = assign_points_to_cells(home_pts, df_cells).to_numpy()

    n_total = len(df)
    assigned = df.dropna(subset=["home_cell_id", "work_cell_id"])
    unassigned_share = 1.0 - (len(assigned) / n_total) if n_total else np.nan
    print(
        "[braunschweig.analysis.verbindungen_validation] workers: "
        f"{len(assigned)}/{n_total} inside ZGB cells "
        f"(unassigned share {100.0 * unassigned_share:.2f}%)"
    )

    df_model_od = (assigned.groupby(["home_cell_id", "work_cell_id"])
                   .size().rename("commuters").reset_index()
                   .rename(columns={"home_cell_id": "origin_cell_id",
                                    "work_cell_id": "destination_cell_id"}))

    # --- check B: conditional OD + bands + intra share ----------------------
    per_origin, od_stats = conditional_od_check(df_model_od, df_ref_od)
    s_model = band_shares(df_model_od, df_cells, DISTANCE_BANDS_KM)
    s_ref = band_shares(df_ref_od, df_cells, DISTANCE_BANDS_KM)
    band_emd = emd_1d(s_model, s_ref)
    intra_model = float(
        df_model_od.loc[df_model_od["origin_cell_id"]
                        == df_model_od["destination_cell_id"], "commuters"].sum()
    ) / max(float(df_model_od["commuters"].sum()), 1.0)
    intra_ref = float(
        df_ref_od.loc[df_ref_od["origin_cell_id"]
                      == df_ref_od["destination_cell_id"], "commuters"].sum()
    ) / max(float(df_ref_od["commuters"].sum()), 1.0)

    # per-Kreis-pair divergence (feeds the stage-3 gate)
    kreis = df_cells.set_index("cell_id")["kreis_id"]
    m = df_model_od.copy()
    m["orig_kreis"] = m["origin_cell_id"].map(kreis)
    m["dest_kreis"] = m["destination_cell_id"].map(kreis)
    r = df_ref_od.copy()
    r["orig_kreis"] = r["origin_cell_id"].map(kreis)
    r["dest_kreis"] = r["destination_cell_id"].map(kreis)
    by_pair = []
    for (ok, dk), r_pair in r.groupby(["orig_kreis", "dest_kreis"]):
        m_pair = m[(m["orig_kreis"] == ok) & (m["dest_kreis"] == dk)]
        _, pair_stats = conditional_od_check(
            m_pair[["origin_cell_id", "destination_cell_id", "commuters"]],
            r_pair[["origin_cell_id", "destination_cell_id", "commuters"]])
        by_pair.append(dict(orig_kreis=ok, dest_kreis=dk,
                            ref_commuters=float(r_pair["commuters"].sum()),
                            model_commuters=float(m_pair["commuters"].sum()),
                            weighted_tvd=pair_stats["weighted_tvd"]))
    od_by_kreis_pair = pd.DataFrame(by_pair)

    # --- check A: production margins ----------------------------------------
    model_margin = assigned.groupby("home_cell_id").size()
    ref_margin = df_margins.set_index("cell_id")["workers_at_home"].astype("Float64")
    margin_stats = margin_check(
        model_margin.reindex(df_margins["cell_id"]).fillna(0.0),
        ref_margin,
    )
    margin_frame = pd.DataFrame({
        "cell_id": df_margins["cell_id"],
        "model_workers_at_home": model_margin.reindex(df_margins["cell_id"]).fillna(0).to_numpy(),
        "reference_workers_at_home_2019": df_margins["workers_at_home"].to_numpy(),
    })

    # --- check C: vintage drift ---------------------------------------------
    drift = vintage_drift_check(df_ref_od, df_cells, df_pendler)
    # A single Kreis-pair (or none) cannot define a correlation; NaN is the
    # correct "not applicable" signal here, not a fabricated 0/1 (same
    # errstate guard as margin_check's Pearson r -- suppress only numpy's
    # divide warning, not the NaN itself).
    with np.errstate(invalid="ignore"):
        vintage_pearson = (
            float(np.corrcoef(drift["share_2019"], drift["share_2025"])[0, 1])
            if len(drift) > 1 else np.nan
        )

    summary = pd.DataFrame(
        [
            ("unassigned_person_share", unassigned_share),
            ("weighted_tvd", od_stats["weighted_tvd"]),
            ("censored_model_share", od_stats["censored_model_share"]),
            ("n_origins_compared", od_stats["n_origins_compared"]),
            ("band_emd", band_emd),
            ("intra_cell_share_model", intra_model),
            ("intra_cell_share_reference", intra_ref),
            ("margin_srmse", margin_stats["srmse"]),
            ("margin_pearson_r", margin_stats["pearson_r"]),
            ("margin_n_cells", margin_stats["n_cells"]),
            ("vintage_pearson_r", vintage_pearson),
            ("vintage_max_abs_share_drift",
             float(drift["share_drift"].abs().max()) if len(drift) else np.nan),
        ],
        columns=["metric", "value"],
    )
    print("[braunschweig.analysis.verbindungen_validation] "
          + ", ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                      for k, v in summary.itertuples(index=False)))
    return dict(summary=summary, margin=margin_frame, od_per_origin=per_origin,
                od_by_kreis_pair=od_by_kreis_pair, vintage_drift=drift)


_PROVENANCE_HEADER = (
    "# VerBindungen validation (#124). Reference: VerBindungen 2019 "
    "(31.12.2019, potential commutes, relations<10 censored; QZM universe = "
    "all workers, BA margins = SvB+aGeB only). Model: synthetic employed "
    "persons with assigned work location (potential assignment, uniform "
    "sample, share-based). Shares only -- never compare absolute counts.\n"
)

# Output file names, shared by the synpp stage (execute()) and the standalone
# cache re-run entry point (run_verbindungen_validation.py) so the two never
# drift apart.
_OUTPUT_FILE_NAMES = dict(
    summary="verbindungen_validation_summary.csv",
    margin="verbindungen_margin_check.csv",
    od_per_origin="verbindungen_od_check.csv",
    od_by_kreis_pair="verbindungen_od_check_by_kreis_pair.csv",
    vintage_drift="verbindungen_vintage_drift.csv",
)


def write_validation_outputs(outputs: dict, directory: str) -> None:
    """Write the 5 validation CSVs (with the provenance header) into *directory*.

    Creates *directory* if it does not exist yet. Shared by the synpp stage
    (``execute()``, writing under ``<output_path>/analysis/verbindungen``) and
    the standalone cache re-run entry point (``run_verbindungen_validation.py``)
    so the file names, header and directory-creation semantics never drift
    apart between the two call sites.
    """
    os.makedirs(directory, exist_ok=True)
    for key, name in _OUTPUT_FILE_NAMES.items():
        target = os.path.join(directory, name)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(_PROVENANCE_HEADER)
            outputs[key].to_csv(f, index=False)
        print(f"[braunschweig.analysis.verbindungen_validation] wrote {target}")


def configure(context):
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("braunschweig.data.verbindungen.zones")
    context.stage("braunschweig.data.verbindungen.work_od")
    context.stage("braunschweig.data.verbindungen.margins")
    context.stage("braunschweig.data.census.pendler")
    context.config("output_path")


def execute(context):
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, _ = context.stage("synthesis.population.spatial.primary.locations")
    df_persons = context.stage(
        "synthesis.population.spatial.primary.candidates")["persons"]
    df_cells, _ = context.stage("braunschweig.data.verbindungen.zones")
    df_ref_od = context.stage("braunschweig.data.verbindungen.work_od")
    df_margins = context.stage("braunschweig.data.verbindungen.margins")
    df_pendler = context.stage("braunschweig.data.census.pendler")

    outputs = build_validation_outputs(
        df_home, df_work, df_persons, df_cells, df_ref_od, df_margins,
        df_pendler)

    # User-facing validation output goes under <output_path>/analysis/... (the
    # cordon_validation / analysis_suite convention), NOT context.path() (the
    # synpp cache dir), so it survives cache cleanup and is where a researcher
    # actually looks for run outputs.
    directory = os.path.join(context.config("output_path"), "analysis", "verbindungen")
    write_validation_outputs(outputs, directory)
    return outputs
