"""
Gravity model wrapper for Braunschweig that calibrates the Bavaria-style
Gemeinde-level OD against the empirical Kreis-level flows from the BA
Pendleratlas.

Rationale
---------
``bavaria.gravity.model`` synthesises a full Gemeinde×Gemeinde commute
probability matrix from population, workplace counts and distances.  It
uses parameters estimated from Île-de-France, so the magnitudes are
plausible but not directly calibrated to the ZGB region.

The BA Pendleratlas gives us **observed** SvB-Pendlerströme at Kreis
level for 2025.  This stage applies a straightforward Iterative
Proportional Fitting (IPF) step that scales the Gemeinde-level gravity
flows so that, once re-aggregated to Kreis pairs, they match the
observed BA flow totals — while preserving the spatial heterogeneity
inside each Kreis that only the gravity model can provide.

Output has the same schema as ``bavaria.gravity.model``:
    origin_id          str   commune_id (8-digit AGS)
    destination_id     str
    weight             float row-normalised P(destination | origin)

Returned as ``(df_work_od, df_education_od)`` tuple.  Education uses the
uncalibrated gravity result (no equivalent observed data), same as
Bavaria's behaviour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


def configure(context):
    context.stage("bavaria.gravity.model")
    context.stage("bavaria.ipf.attributed")
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.data.census.employment")
    context.stage("braunschweig.data.external_workplaces")
    context.config("bavaria.political_prefix")


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Strip a commune_id (8-digit AGS) down to a 5-digit Kreis ARS."""
    return series.astype(str).str[:5]


def _synthesise_intra_kreis(df_pendler: pd.DataFrame,
                            df_employment: pd.DataFrame,
                            scope: list[str]) -> pd.DataFrame:
    """Inject intra-Kreis SvB flows (``K -> K``) into the Pendler frame.

    The BA Pendleratlas publishes only inter-Kreis flows, but for the
    gravity calibration we need intra-Kreis totals too — without them,
    the Gemeinde-pair cells where origin and destination share a Kreis
    fall into the un-calibrated ``df_rest`` bucket and retain their
    population-unit magnitudes, which completely overwhelms the
    SvB-calibrated inter-Kreis cells and collapses the external-commute
    share to a few percent instead of the BA-reported ~28 %.

    We reconstruct intra-Kreis flow as

        intra(K) = SvB_Wohnort(K) - sum_{E != K} BA_flow(K -> E)

    using ``braunschweig.data.census.employment`` (SvB am Wohnort) as
    the ground truth for the residents-with-job total.
    """
    wohnort = (df_employment.groupby("departement_id")["weight"]
                            .sum()
                            .rename("svb_wohnort")
                            .reset_index()
                            .rename(columns={"departement_id": "kreis"}))
    wohnort["kreis"] = wohnort["kreis"].astype(str)
    wohnort = wohnort[wohnort["kreis"].isin(scope)]

    auspendler = (df_pendler[df_pendler["orig_ars"].isin(scope)]
                  .groupby("orig_ars")["flow"].sum()
                  .rename("auspendler")
                  .reset_index()
                  .rename(columns={"orig_ars": "kreis"}))

    merged = wohnort.merge(auspendler, on="kreis", how="left")
    merged["auspendler"] = merged["auspendler"].fillna(0)
    merged["intra"] = (merged["svb_wohnort"] - merged["auspendler"]).clip(lower=0)

    intra = pd.DataFrame({
        "orig_ars": merged["kreis"],
        "dest_ars": merged["kreis"],
        "flow": merged["intra"].astype(int),
    })

    print(
        "[braunschweig.gravity.model] synthesised intra-Kreis flows: "
        + ", ".join(f"{r.orig_ars}={int(r.flow):,}" for r in intra.itertuples())
    )
    return pd.concat([df_pendler, intra], ignore_index=True)


def _calibrate(df_od: pd.DataFrame,
               df_population: pd.DataFrame,
               df_pendler: pd.DataFrame) -> pd.DataFrame:
    """IPF-scale Gemeinde-level OD so that Kreis aggregates match BA Pendler.

    ``df_od`` has columns ``origin_id, destination_id, weight`` where
    weight is already row-normalised (conditional probabilities).  We
    temporarily convert it into absolute flows using ``df_population``
    and scale cells by a Kreis-level factor until Kreis row/column totals
    converge to the observed BA values.  Rows/columns not covered by BA
    data keep their original magnitude.

    Returns a frame with an **absolute** ``flow`` column (SvB units for
    calibrated cells, origin-population units for uncovered cells).
    The downstream concatenation step renormalises into probabilities.
    """
    # Attach Kreis identifiers.
    df = df_od.copy()
    df["orig_kreis"] = _gemeinde_to_kreis(df["origin_id"])
    df["dest_kreis"] = _gemeinde_to_kreis(df["destination_id"])

    # Lift conditional probabilities to absolute person-flows by
    # multiplying by the working-age population of the origin Gemeinde.
    pop = (
        df_population.groupby("commune_id")["weight"].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={"commune_id": "origin_id"})
    )
    df = pd.merge(df, pop, on="origin_id", how="left")
    df["pop"] = df["pop"].fillna(0.0)
    df["flow"] = df["weight"] * df["pop"]

    # Observed Kreis-pair flows.
    obs = (
        df_pendler.rename(columns={
            "orig_ars": "orig_kreis",
            "dest_ars": "dest_kreis",
        })
        .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
        .rename("obs")
        .reset_index()
    )

    # Current gravity Kreis-pair flows.
    def kreis_flows(frame):
        return (
            frame.groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
                 .rename("cur")
                 .reset_index()
        )

    # Only scale cells whose (orig_kreis, dest_kreis) is observed.
    scope_pairs = obs[["orig_kreis", "dest_kreis"]].drop_duplicates()
    df_scope = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"], how="inner")
    df_rest = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"],
                       how="left", indicator=True)
    df_rest = df_rest[df_rest["_merge"] == "left_only"].drop(columns=["_merge"])

    if len(df_scope) == 0:
        # No Kreis pair in gravity matches BA scope — nothing to calibrate.
        print("[braunschweig.gravity.model] no scope overlap; returning raw gravity")
        return df_od

    # IPF loop: alternately match row sums and column sums per Kreis pair.
    for it in range(MAX_IPF_ITERATIONS):
        cur = kreis_flows(df_scope)
        merged = cur.merge(obs, on=["orig_kreis", "dest_kreis"], how="inner")
        merged["ratio"] = np.where(
            merged["cur"] > 0, merged["obs"] / merged["cur"], 1.0
        )

        df_scope = df_scope.merge(
            merged[["orig_kreis", "dest_kreis", "ratio"]],
            on=["orig_kreis", "dest_kreis"], how="left",
        )
        df_scope["ratio"] = df_scope["ratio"].fillna(1.0)
        df_scope["flow"] = df_scope["flow"] * df_scope["ratio"]

        max_delta = float(np.abs(merged["ratio"] - 1.0).max())
        df_scope = df_scope.drop(columns=["ratio"])

        if max_delta < IPF_TOLERANCE:
            print(f"[braunschweig.gravity.model] IPF converged after {it+1} iter (delta={max_delta:.4g})")
            break
    else:
        print(f"[braunschweig.gravity.model] IPF stopped at {MAX_IPF_ITERATIONS} iter (delta={max_delta:.4g})")

    # Combine calibrated + untouched cells.
    df_out = pd.concat([df_scope, df_rest], ignore_index=True)

    # Return absolute flows; row-normalisation happens later, once the
    # external outbound edges have been concatenated so all cells live
    # on the same scale (SvB units).
    return df_out[["origin_id", "destination_id", "flow"]]


def _append_outbound_flows(df_od: pd.DataFrame,
                           df_population: pd.DataFrame,
                           df_pendler: pd.DataFrame,
                           df_external: pd.DataFrame,
                           scope: list[str]) -> pd.DataFrame:
    """Add rows ``(origin_gemeinde, synthetic_external_commune, weight)``.

    The gravity matrix only connects ZGB-8 Gemeinden among themselves, so
    we inject the BA Pendler outbound flows (ZGB → external Kreis) into
    the OD explicitly. For every ZGB-8 origin Gemeinde G in Kreis K we
    emit one row per external Kreis E with absolute SvB flow

        flow(G → EXT_E) = pop(G) / pop(K) × BA_flow(K → E)

    so the Kreis-level outbound totals match BA while the distribution
    across Gemeinden inside each ZGB Kreis is proportional to the
    working-age population. ``df_external["ars5"]`` filters the Pendler
    outbound list to the actually-materialised external workplaces.

    ``df_od`` is expected to carry the **absolute SvB flow** column
    produced by ``_calibrate``. After concatenating the external rows
    we renormalise per origin to probabilities.
    """
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

    # Gemeinde-level population weights per origin Kreis.
    pop = (
        df_population.groupby("commune_id")["weight"].sum()
                     .rename("pop").reset_index()
    )
    pop["orig_ars"] = pop["commune_id"].astype(str).str[:5]
    pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
    pop["share"] = np.where(pop["kreis_total"] > 0,
                            pop["pop"] / pop["kreis_total"], 0.0)
    pop = pop[pop["orig_ars"].isin(scope)]

    n_ext_rows = 0
    ext_svb = 0.0
    if df_out_pendler.empty:
        print("[braunschweig.gravity.model] no outbound flows to inject")
        df_all = df_od.copy()
    else:
        df_inj = pop.merge(df_out_pendler, on="orig_ars", how="inner")
        df_inj["flow"] = df_inj["share"] * df_inj["flow"].astype(float)
        df_inj = df_inj[df_inj["flow"] > 0]
        df_inj["destination_id"] = "EXT" + df_inj["dest_ars"].astype(str)
        df_inj = df_inj.rename(columns={"commune_id": "origin_id"})
        df_inj = df_inj[["origin_id", "destination_id", "flow"]]

        n_ext_rows = len(df_inj)
        ext_svb = float(df_inj["flow"].sum())

        df_all = pd.concat([df_od, df_inj], ignore_index=True)

    # Renormalise per origin into P(destination | origin). Origins with
    # zero total flow (e.g. uncovered rest-of-Germany cells) fall back
    # to a self-loop to keep downstream sampling deterministic.
    totals = df_all.groupby("origin_id")["flow"].sum().rename("total").reset_index()
    df_all = df_all.merge(totals, on="origin_id", how="left")
    f_missing = df_all["total"] <= 0.0
    df_all.loc[f_missing & (df_all["origin_id"] == df_all["destination_id"]), "flow"] = 1.0
    df_all.loc[f_missing, "total"] = 1.0
    df_all["weight"] = df_all["flow"] / df_all["total"]

    if n_ext_rows:
        print(
            "[braunschweig.gravity.model] "
            f"injected {n_ext_rows:,} outbound rows "
            f"({ext_svb:,.0f} synthetic SvB distributed to external Kreise)"
        )
    return df_all[["origin_id", "destination_id", "weight"]]


def execute(context):
    df_work_od, df_education_od = context.stage("bavaria.gravity.model")
    df_population = context.stage("bavaria.ipf.attributed")
    df_pendler = context.stage("braunschweig.data.census.pendler")
    df_employment = context.stage("braunschweig.data.census.employment")
    df_external = context.stage("braunschweig.data.external_workplaces")

    # Pre-filter Pendler to pairs where at least one side is inside the
    # configured scope — cross-Germany flows are irrelevant for our
    # synthetic population.
    scope = [str(p) for p in context.config("bavaria.political_prefix")]
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    df_pendler = df_pendler[mask].copy()

    # Add intra-Kreis SvB rows so the IPF can calibrate K->K cells too.
    df_pendler = _synthesise_intra_kreis(df_pendler, df_employment, scope)

    print(
        "[braunschweig.gravity.model] calibrating {:,} Gemeinde-pairs "
        "against {:,} BA Kreis-pair flows".format(len(df_work_od), len(df_pendler))
    )

    df_work_calibrated = _calibrate(df_work_od, df_population, df_pendler)
    df_work_extended = _append_outbound_flows(
        df_work_calibrated, df_population, df_pendler, df_external, scope,
    )

    # Education: leave uncalibrated (no empirical dataset available).
    return df_work_extended, df_education_od
