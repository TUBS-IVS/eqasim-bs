"""Gravity model for Braunschweig - base + Kreis-level calibration.

This module is the merged successor of:
- ``bavaria/gravity/model.py`` (Origin: eqasim-bavaria @ b20fbe6) - the
  Gemeinde x Gemeinde gravity model with IDF-derived parameters.
- ``braunschweig/gravity/model.py`` - the BA Pendleratlas IPF calibration
  layer that scales the Gemeinde flows so Kreis aggregates match observed
  SvB-Pendlerstroeme.

Phase 2.11 of the eqasim-bs refactor merged both into a single module so
the BS pipeline no longer delegates through ``braunschweig.gravity.model``. The
behaviour is unchanged.

Output schema is identical to ``braunschweig.gravity.model``::

    origin_id          str   commune_id (8-digit AGS)
    destination_id     str
    weight             float row-normalised P(destination | origin)

Returned as ``(df_work_od, df_education_od)`` tuple. Education uses the
uncalibrated gravity result (no equivalent observed data).
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd


# --- Inherited from eqasim-bavaria -----------------------------------------
# Gemeinde x Gemeinde gravity model with IDF-derived defaults.

# Defaults: -0.09 came from IDF, value -2.0 has been calibrated.
DEFAULT_SLOPE = -0.2
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0


def evaluate_gravity(population, employees, friction):
    """Iterative balancing of a doubly-constrained gravity model."""
    production = np.ones((len(population),))
    attraction = np.ones((len(population),))
    flow = np.ones((len(population), len(population)))
    converged = False

    for iteration in range(int(1e6)):
        previous_production = np.copy(production)
        previous_attraction = np.copy(attraction)
        previous_flow = np.copy(flow)

        for k in range(len(population)):
            production[k] = population[k] / np.sum(attraction * friction[k, :])

        for k in range(len(population)):
            attraction[k] = employees[k] / np.sum(production * friction[:, k])

        flow = np.copy(friction)
        for i in range(len(population)):
            flow[i, :] *= production[i]
        for j in range(len(population)):
            flow[:, j] *= attraction[j]

        production_delta = np.abs(production - previous_production)
        attraction_delta = np.abs(attraction - previous_attraction)
        flow_delta = np.abs(flow - previous_flow)

        print(
            "Gravity iteration", iteration,
            "prod. max. delta:", np.max(production_delta),
            "attr. max. delta:", np.max(attraction_delta),
            "flow max. delta:", np.max(flow_delta),
        )

        if (np.max(production_delta) < 1e-3
                and np.max(attraction_delta) < 1e-3
                and np.max(flow_delta) < 1e-3):
            converged = True
            break

    assert converged
    return flow


def _build_origin_slope_vector(
    municipalities: list[str],
    default_slope: float,
    overrides: dict | None,
    df_regiostar: pd.DataFrame | None,
) -> np.ndarray:
    """Return one slope per origin, optionally overridden by RegioStaR-7.

    ``overrides`` maps RegioStaR-7 codes (int 71..77) to slope values
    (negative floats, e.g. ``-0.05``). Origins whose commune_id is not
    in ``df_regiostar`` or whose RegioStaR-7 code has no override fall
    back to ``default_slope``. The returned array has shape ``(N,)``
    aligned with ``municipalities`` and is broadcast against the
    distance matrix as ``slope[:, None] * distances``.
    """
    slope_vec = np.full(len(municipalities), float(default_slope))
    if not overrides or df_regiostar is None or df_regiostar.empty:
        return slope_vec

    typed_overrides = {int(k): float(v) for k, v in overrides.items()}
    rs7_lookup = (
        df_regiostar.set_index("commune_id")["regiostar7"]
        .astype("Int64")
        .to_dict()
    )

    def _normalize(cid: str) -> str:
        """Convert 12-digit ARS to 8-digit AGS if needed.

        ``braunschweig.ipf.attributed`` produces commune_id in the full
        12-character ARS format (Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3)),
        while ``braunschweig.data.bbsr.regiostar`` keys on the 8-digit
        AGS = ARS[0:5] + ARS[9:12]. Other consumers may already pass
        the 8-digit form; in that case the slice is a no-op.
        """
        s = str(cid)
        if len(s) == 12:
            return s[0:5] + s[9:12]
        return s

    matched = 0
    used_codes: dict[int, int] = {}
    for i, commune_id in enumerate(municipalities):
        key = _normalize(commune_id)
        rs7 = rs7_lookup.get(key)
        if rs7 is None or pd.isna(rs7):
            continue
        rs7 = int(rs7)
        if rs7 in typed_overrides:
            slope_vec[i] = typed_overrides[rs7]
            matched += 1
            used_codes[rs7] = used_codes.get(rs7, 0) + 1

    print(
        "[braunschweig.gravity.model] per-RegioStaR slope active: "
        f"{matched}/{len(municipalities)} origins overridden "
        f"(default={default_slope}; overrides per RS7 = {used_codes})"
    )
    return slope_vec


def _execute_gravity_base(context):
    """Run the bavaria-style Gemeinde x Gemeinde gravity model.

    Returns ``(df_work_od, df_education_od)`` of row-normalised
    conditional probabilities.
    """
    df_distances = context.stage("eqasim_common.gravity.distance_matrix")
    df_population = context.stage("braunschweig.ipf.attributed")
    df_employees = context.stage("braunschweig.data.census.employees")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    df_population = df_population.rename(columns={
        "commune_id": "origin_id",
        "weight": "population",
    })[["origin_id", "population"]]

    df_employees = df_employees.rename(columns={
        "commune_id": "destination_id",
        "weight": "employees",
    })[["destination_id", "employees"]]

    df_population = df_population.groupby("origin_id")["population"].sum().reset_index()

    municipalities = set(df_population["origin_id"])
    municipalities |= set(df_employees["destination_id"])
    municipalities |= set(df_distances["origin_id"])
    municipalities |= set(df_distances["destination_id"])
    municipalities = sorted(list(municipalities))

    df_population = df_population.set_index("origin_id").reindex(municipalities).fillna(0.0)
    df_employees = df_employees.set_index("destination_id").reindex(municipalities).fillna(0.0)
    df_distances = df_distances.set_index(["origin_id", "destination_id"]).reindex(
        pd.MultiIndex.from_product([municipalities, municipalities])
    )

    distances = df_distances["distance_km"].values.reshape((len(municipalities), len(municipalities)))

    population = df_population["population"]
    employees = df_employees["employees"]

    observations = min(np.sum(population), np.sum(employees))
    population *= observations / np.sum(population)
    employees *= observations / np.sum(employees)

    slope = context.config("gravity_slope")
    constant = context.config("gravity_constant")
    diagonal = context.config("gravity_diagonal")
    slope_overrides = context.config("gravity_slope_by_regiostar7")

    # Per-origin slope: defaults to scalar ``slope`` for every Gemeinde.
    # When ``gravity_slope_by_regiostar7`` is non-empty, origins whose
    # RegioStaR-7 code matches an override key receive that slope; the
    # friction matrix becomes ``exp(slope_vec[:, None] * distances + c)``
    # so each row (origin Gemeinde) decays at its own urban/rural rate.
    slope_vec = _build_origin_slope_vector(
        municipalities, slope, slope_overrides, df_regiostar,
    )

    friction = (
        np.exp(slope_vec[:, None] * distances + constant)
        + np.eye(len(municipalities)) * diagonal
    )
    flow = evaluate_gravity(population, employees, friction)

    df_matrix = pd.DataFrame({
        "weight": flow.reshape((-1,)),
    }, index=pd.MultiIndex.from_product(
        [municipalities, municipalities],
        names=["origin_id", "destination_id"],
    )).reset_index()

    df_total = (df_matrix[["origin_id", "weight"]]
                .groupby("origin_id").sum()
                .reset_index()
                .rename({"weight": "total"}, axis=1))
    df_matrix = pd.merge(df_matrix, df_total, on="origin_id")

    f_missing_total = df_matrix["total"] == 0.0
    df_matrix.loc[
        f_missing_total & (df_matrix["origin_id"] == df_matrix["destination_id"]),
        "weight",
    ] = 1.0
    df_matrix.loc[f_missing_total, "total"] = 1.0

    df_matrix["weight"] = df_matrix["weight"] / df_matrix["total"]
    df_matrix = df_matrix[["origin_id", "destination_id", "weight"]]

    return df_matrix, df_matrix


# --- Braunschweig-specific -------------------------------------------------
# BA-Pendleratlas calibration: IPF the Gemeinde-level OD so Kreis aggregates
# match observed SvB flows; inject ZGB -> external Kreis outbound rows.

# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


def configure(context):
    context.stage("eqasim_common.gravity.distance_matrix")
    context.stage("braunschweig.ipf.attributed")
    context.stage("braunschweig.data.census.employees")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("gravity_slope", DEFAULT_SLOPE)
    context.config("gravity_constant", DEFAULT_CONSTANT)
    context.config("gravity_diagonal", DEFAULT_DIAGONAL)
    # Optional dict {regiostar7_code: slope}. Empty = use scalar slope.
    context.config("gravity_slope_by_regiostar7", {})
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.data.census.employment")
    context.stage("braunschweig.data.external_workplaces")
    context.config("braunschweig.political_prefix")


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Strip a commune_id (8-digit AGS) down to a 5-digit Kreis ARS."""
    return series.astype(str).str[:5]


def _synthesise_intra_kreis(df_pendler: pd.DataFrame,
                            df_employment: pd.DataFrame,
                            scope: list[str]) -> pd.DataFrame:
    """Inject intra-Kreis SvB flows (``K -> K``) into the Pendler frame."""
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
    """IPF-scale Gemeinde-level OD so Kreis aggregates match BA Pendler."""
    df = df_od.copy()
    df["orig_kreis"] = _gemeinde_to_kreis(df["origin_id"])
    df["dest_kreis"] = _gemeinde_to_kreis(df["destination_id"])

    pop = (
        df_population.groupby("commune_id")["weight"].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={"commune_id": "origin_id"})
    )
    df = pd.merge(df, pop, on="origin_id", how="left")
    df["pop"] = df["pop"].fillna(0.0)
    df["flow"] = df["weight"] * df["pop"]

    obs = (
        df_pendler.rename(columns={
            "orig_ars": "orig_kreis",
            "dest_ars": "dest_kreis",
        })
        .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
        .rename("obs")
        .reset_index()
    )

    def kreis_flows(frame):
        return (
            frame.groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
                 .rename("cur")
                 .reset_index()
        )

    scope_pairs = obs[["orig_kreis", "dest_kreis"]].drop_duplicates()
    df_scope = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"], how="inner")
    df_rest = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"],
                       how="left", indicator=True)
    df_rest = df_rest[df_rest["_merge"] == "left_only"].drop(columns=["_merge"])

    if len(df_scope) == 0:
        print("[braunschweig.gravity.model] no scope overlap; returning raw gravity")
        return df_od

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

    df_out = pd.concat([df_scope, df_rest], ignore_index=True)
    return df_out[["origin_id", "destination_id", "flow"]]


def _append_outbound_flows(df_od: pd.DataFrame,
                           df_population: pd.DataFrame,
                           df_pendler: pd.DataFrame,
                           df_external: pd.DataFrame,
                           scope: list[str]) -> pd.DataFrame:
    """Add rows ``(origin_gemeinde, synthetic_external_commune, weight)``."""
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

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
    df_work_od, df_education_od = _execute_gravity_base(context)
    df_population = context.stage("braunschweig.ipf.attributed")
    df_pendler = context.stage("braunschweig.data.census.pendler")
    df_employment = context.stage("braunschweig.data.census.employment")
    df_external = context.stage("braunschweig.data.external_workplaces")

    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    df_pendler = df_pendler[mask].copy()

    df_pendler = _synthesise_intra_kreis(df_pendler, df_employment, scope)

    print(
        "[braunschweig.gravity.model] calibrating {:,} Gemeinde-pairs "
        "against {:,} BA Kreis-pair flows".format(len(df_work_od), len(df_pendler))
    )

    df_work_calibrated = _calibrate(df_work_od, df_population, df_pendler)
    df_work_extended = _append_outbound_flows(
        df_work_calibrated, df_population, df_pendler, df_external, scope,
    )

    return df_work_extended, df_education_od
