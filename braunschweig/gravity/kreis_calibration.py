"""BA-Pendleratlas Kreis-level calibration of the gravity work OD.

Five pieces of the ``braunschweig.gravity.model`` stage live here:

- ``_zone_to_kreis`` / ``_gemeinde_to_kreis`` -- map a zone id (commune_id or
  taz_id) to its 5-digit Kreis ARS.
- ``_synthesise_intra_kreis`` -- inject intra-Kreis (``K -> K``) SvB flows into
  the BA Pendleratlas frame before it is used as an IPF control.
- ``_calibrate`` -- IPF-scale the zone-level gravity OD so Kreis-pair
  aggregates match the observed BA Pendleratlas flows.
- ``_append_outbound_flows`` -- add ``(origin_zone, synthetic_external_commune)``
  rows for BA Kreis-pair flows leaving the study area, split per-Gemeinde by
  employee share.

Together these implement the BA-Pendleratlas calibration layer described in
the ``braunschweig.gravity.model`` module docstring as "Kreis-level
calibration": the layer that scales the Gemeinde/TAZ gravity result so Kreis
aggregates match observed SvB-Pendlerstroeme, and injects outbound flows to
external Kreise.

Extracted verbatim from ``braunschweig.gravity.model`` (issue #267 split): the
functions, their signatures, their arithmetic and their log lines -- including
the ``[braunschweig.gravity.model]`` message prefixes and the module-level
``print`` calls -- are unchanged, so the model output and the console/log
output are byte-identical to the pre-split stage. The one function that logs
via a ``logging.Logger`` (``_append_outbound_flows``) binds to the literal
``"braunschweig.gravity.model"`` name (not ``__name__``, which would resolve
to ``"braunschweig.gravity.kreis_calibration"`` here) so every
``LogRecord.name`` emitted by the moved code is unchanged.

``braunschweig.gravity.model`` re-exports every public and private name defined
here, so existing imports of the stage module path (including the test suite,
which imports several of these names directly) keep working. This module must
NEVER depend on ``braunschweig.gravity.model`` in any direction other than
downward (that would close an import cycle): the dependency runs strictly
model -> kreis_calibration.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.gravity.od import ZERO_TOTAL_SELF_LOOP_WARN_PERCENT

# Bound to the literal facade stage name (not __name__, which would resolve to
# "braunschweig.gravity.kreis_calibration" here) so every LogRecord emitted by
# the moved _append_outbound_flows code keeps the pre-split
# "braunschweig.gravity.model" logger name -- see the module docstring.
logger = logging.getLogger("braunschweig.gravity.model")


# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


def _zone_to_kreis(series: pd.Series, lookup: dict | None = None) -> pd.Series:
    """Map a zone id to its 5-digit Kreis ARS. lookup is None -> legacy commune_id
    str[:5] (byte-identical). lookup given -> map each taz_id, raise on unmapped.

    On the ON path an unmapped taz_id is a TAZ coverage gap (the lookup must map
    every zone). Raise a descriptive ``RuntimeError`` naming the offending ids
    instead of letting a bare ``KeyError`` from the dict lookup bubble up, so the
    failure is actionable (consistent with the other explicit guards here and the
    no-silent-fallback contract)."""
    if lookup is None:
        return series.astype(str).str[:5]
    zones = series.astype(str)
    unmapped = sorted(set(zones) - set(lookup))
    if unmapped:
        raise RuntimeError(
            "%d zone id(s) have no Kreis in the taz->kreis lookup, e.g. %s "
            "(TAZ coverage gap; the lookup must map every taz_id)"
            % (len(unmapped), ", ".join(unmapped[:5]))
        )
    return zones.map(lookup)


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Backwards-compatible shim (tests/braunschweig/test_stages.py imports this)."""
    return _zone_to_kreis(series)


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
               df_pendler: pd.DataFrame,
               zone_to_kreis: dict | None = None,
               population_key: str = "commune_id",
               population_value: str = "weight") -> pd.DataFrame:
    """IPF-scale zone-level OD so Kreis aggregates match BA Pendler.

    OFF path (zone_to_kreis=None): uses legacy commune_id[:5] -> byte-identical.
    ON path (zone_to_kreis=dict): maps each taz_id via the lookup, raises if no
    in-scope flows are found after mapping (silent BA-skip guard).

    Parameters
    ----------
    df_od:
        Origin-destination frame with columns ``origin_id``, ``destination_id``, ``weight``.
    df_population:
        Population frame; grouped by ``population_key``, summed on ``population_value``.
    df_pendler:
        BA Pendleratlas Kreis-pair flows (columns ``orig_ars``, ``dest_ars``, ``flow``).
    zone_to_kreis:
        None -> legacy str[:5] mapping (OFF path). dict -> explicit taz_id->Kreis map (ON path).
    population_key:
        Column to group ``df_population`` by. OFF: ``"commune_id"``; ON: ``"taz_id"``.
    population_value:
        Column to sum from ``df_population``. OFF: ``"weight"``; ON: ``"population"``.
    """
    df = df_od.copy()
    df["orig_kreis"] = _zone_to_kreis(df["origin_id"], zone_to_kreis)
    df["dest_kreis"] = _zone_to_kreis(df["destination_id"], zone_to_kreis)

    pop = (
        df_population.groupby(population_key)[population_value].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={population_key: "origin_id"})
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
        if zone_to_kreis is not None:
            raise RuntimeError(
                "[gravity TAZ] no in-scope flow after taz->kreis mapping; "
                "BA calibration would be silently skipped"
            )
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
                           scope: list[str],
                           zone_to_kreis: dict | None = None,
                           population_key: str = "commune_id",
                           population_value: str = "weight") -> pd.DataFrame:
    """Add rows ``(origin_zone, synthetic_external_commune, weight)``.

    Each outbound BA Kreis flow is split across the per-Gemeinde EXT points
    in ``df_external`` proportional to their ``employees`` share, so the
    emitted ``destination_id`` equals the per-Gemeinde ``commune_id``
    (``"EXT" + gem_ags``, 8-digit AGS).  This ensures exact-string matching
    against the work-pool ``commune_id`` in the downstream candidate sampler
    (``synthesis/population/spatial/primary/candidates.py``).

    Mass is conserved: for every (origin, Kreis) pair the sum of per-zone
    flows equals the original Kreis-level outbound flow.

    OFF path (zone_to_kreis=None): groups ``df_population`` by ``commune_id``,
    derives Kreis via ``str[:5]`` -- byte-identical to the prior behaviour.
    ON path (zone_to_kreis=dict): groups by ``taz_id`` (population_key), maps
    each taz_id to its Kreis via the lookup; origin_id in injected rows is the
    taz_id so the key space is consistent with the calibrated work OD.
    """
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

    if zone_to_kreis is None:
        # OFF path: classic commune_id[:5] grouping and origin key.
        pop = (
            df_population.groupby("commune_id")["weight"].sum()
                         .rename("pop").reset_index()
        )
        pop["orig_ars"] = pop["commune_id"].astype(str).str[:5]
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        # origin_id for injected rows is the Gemeinde commune_id (OFF behaviour).
        pop_origin_col = "commune_id"
    else:
        # ON path: group by taz_id, map to Kreis via lookup; origin_id = taz_id.
        pop = (
            df_population.groupby(population_key)[population_value].sum()
                         .rename("pop").reset_index()
                         .rename(columns={population_key: "taz_id"})
        )
        pop["orig_ars"] = pop["taz_id"].astype(str).map(zone_to_kreis)
        if pop["orig_ars"].isna().any():
            missing_n = int(pop["orig_ars"].isna().sum())
            raise RuntimeError(
                "[gravity TAZ _append_outbound_flows] %d taz_id values have no "
                "Kreis mapping in zone_to_kreis; cannot build outbound shares" % missing_n
            )
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        pop_origin_col = "taz_id"

    # Build per-Gemeinde employee shares keyed by Kreis ars5, carrying commune_id.
    # gem_share = employees / Sigma_{Gemeinde in Kreis} employees.
    gem_emp = (
        df_external[["ars5", "commune_id", "employees"]]
        .copy()
        .astype({"ars5": str, "commune_id": str, "employees": float})
    )
    kreis_total_emp = gem_emp.groupby("ars5")["employees"].transform("sum")
    gem_emp["gem_share"] = np.where(
        kreis_total_emp > 0, gem_emp["employees"] / kreis_total_emp, 0.0
    )

    n_ext_rows = 0
    ext_svb = 0.0
    if df_out_pendler.empty:
        print("[braunschweig.gravity.model] no outbound flows to inject")
        df_all = df_od.copy()
    else:
        # origin_zone x dest_kreis rows, with each origin's flow share.
        df_inj = pop.merge(df_out_pendler, on="orig_ars", how="inner")
        df_inj["flow"] = df_inj["share"] * df_inj["flow"].astype(float)
        df_inj = df_inj[df_inj["flow"] > 0]
        # df_inj now has columns: <pop_origin_col>, orig_ars, dest_ars, flow (Kreis-level split).

        # Expand each Kreis-level flow to per-Gemeinde rows via the employee share.
        # Join on ars5 == dest_ars (many-to-many: one origin->Kreis row becomes N rows).
        df_inj = df_inj.merge(
            gem_emp[["ars5", "commune_id", "gem_share"]].rename(
                columns={"commune_id": "destination_id"}
            ),
            left_on="dest_ars",
            right_on="ars5",
            how="inner",
        )

        # Guard: warn if any Kreis present in outbound had no Gemeinde points.
        kreise_in_inj_before = set(df_out_pendler["dest_ars"].astype(str).unique())
        kreise_expanded = set(gem_emp["ars5"].unique())
        kreise_missing = kreise_in_inj_before - kreise_expanded
        if kreise_missing:
            print(
                "WARNING: [braunschweig.gravity.model] "
                f"{len(kreise_missing)} Kreis(e) in outbound have NO per-Gemeinde "
                f"EXT point in df_external and their flow is lost: "
                + ", ".join(sorted(kreise_missing))
            )

        df_inj["flow"] = df_inj["flow"] * df_inj["gem_share"]
        df_inj = df_inj[df_inj["flow"] > 0]
        df_inj = df_inj.rename(columns={pop_origin_col: "origin_id"})
        df_inj = df_inj[["origin_id", "destination_id", "flow"]]

        n_ext_rows = len(df_inj)
        ext_svb = float(df_inj["flow"].sum())

        df_all = pd.concat([df_od, df_inj], ignore_index=True)

    totals = df_all.groupby("origin_id")["flow"].sum().rename("total").reset_index()
    df_all = df_all.merge(totals, on="origin_id", how="left")
    f_missing = df_all["total"] <= 0.0
    # No-silent-fallback (CLAUDE.md): an origin with zero total flow (no BA-Pendler
    # row, no EXT injection) would otherwise divide 0/0 below; the self-loop is
    # forced to weight 1.0 instead. Count and log the rate so a systematically
    # empty outbound-flow set (e.g. a broken scope/ars join) is visible.
    n_missing_origins = int(df_all.loc[f_missing, "origin_id"].nunique())
    n_total_origins = int(df_all["origin_id"].nunique())
    if n_missing_origins:
        share = 100.0 * n_missing_origins / max(n_total_origins, 1)
        level = logger.warning if share > ZERO_TOTAL_SELF_LOOP_WARN_PERCENT else logger.info
        level(
            "[gravity] zero-total origins forced to self-loop: %d/%d (%.1f%%)",
            n_missing_origins, n_total_origins, share,
        )
    df_all.loc[f_missing & (df_all["origin_id"] == df_all["destination_id"]), "flow"] = 1.0
    df_all.loc[f_missing, "total"] = 1.0
    df_all["weight"] = df_all["flow"] / df_all["total"]

    if n_ext_rows:
        print(
            "[braunschweig.gravity.model] "
            f"injected {n_ext_rows:,} outbound rows (per-Gemeinde EXT destinations) "
            f"({ext_svb:,.0f} synthetic SvB distributed to external Gemeinden)"
        )
    return df_all[["origin_id", "destination_id", "weight"]]
