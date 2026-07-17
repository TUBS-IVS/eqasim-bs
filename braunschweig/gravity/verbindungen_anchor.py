"""Inner VerBindungen calibration anchor for the work OD (#193).

Nested, block-preserving, ROW-CONDITIONAL anchoring below the Pendleratlas:
the 2025 Pendleratlas WINS across Kreise (outer ``_calibrate`` block scaling,
untouched); the 2019 VerBindungen QZM only refines destination structure
WITHIN a Kreis pair. For each origin comparison zone ``o`` and destination
Kreis ``K``, the flows to the OBSERVED destination zones are re-weighted so
their conditional shares match the reference, PRESERVING the row-observed
mass -- censored (unobserved, < 10 in 2019) relations and the observed-vs-
censored split stay gravity-driven (censoring rule A). Block totals are
conserved exactly, so the outer anchor cannot be violated; the procedure is
one-shot and idempotent.

Division of labour: production margins belong to popsim (validated r 0.997);
this anchor only reshapes P(destination | origin) -- exactly the quantity the
downstream location choice consumes and check B measures.

Fit-vs-independent: with the anchor ON, the VerBindungen validation (check B)
is a FIT metric; independent validation moves to the MiD distance axes.

All fallbacks/skips are counted and logged (CLAUDE.md fallback transparency);
conservation violations RAISE (never warn).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Relative tolerance for the mass-conservation assertions.
CONSERVATION_RTOL = 1e-9
# Model mass below this is "zero" for the zero-mass guard (flows are floats).
ZERO_MASS_EPS = 1e-12
# TRANSPARENCY HEURISTIC (CLAUDE.md fallback transparency), NOT a scientific
# reference/target value: warn when more than this fraction of (origin,
# dest-Kreis) rows are dropped by the coverage guard. A high coverage-skip
# rate is a data-quality signal (e.g. a stale reference year or an id-join
# mismatch), not something to compare model output against; it also fires
# near a ~100% skip rate, the case CLAUDE.md calls out explicitly.
HIGH_COVERAGE_SKIP_WARN_FRACTION = 0.5
# TRANSPARENCY HEURISTIC (CLAUDE.md fallback transparency), NOT a scientific
# reference/target value: warn when more than this fraction of ANCHORED rows
# needed the partial-zero renormalisation fallback (some observed destination
# zone carried zero model mass, see apply_inner_anchor). A high rate means the
# gravity model is routinely missing QZM-observed relations -- a data-quality
# signal worth investigating, not something to compare model output against.
HIGH_PARTIAL_ZERO_WARN_FRACTION = 0.5


def collapse_od_to_zones(df_od_cells: pd.DataFrame,
                         df_cell_zone: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a cell-level OD to comparison zones; raise on unmapped ids."""
    zone = df_cell_zone.set_index("cell_id")["zone_id"]
    out = df_od_cells.copy()
    out["origin_zone_id"] = out["origin_cell_id"].map(zone)
    out["destination_zone_id"] = out["destination_cell_id"].map(zone)
    unmapped = out["origin_zone_id"].isna() | out["destination_zone_id"].isna()
    if unmapped.any():
        bad = pd.concat([
            out.loc[out["origin_zone_id"].isna(), "origin_cell_id"],
            out.loc[out["destination_zone_id"].isna(), "destination_cell_id"],
        ]).unique()
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] unmapped cell id(s) in "
            f"OD frame: {sorted(map(str, bad))[:5]}"
        )
    return (out.groupby(["origin_zone_id", "destination_zone_id"])["commuters"]
            .sum().reset_index())


def build_anchor_targets(df_ref_od_zones: pd.DataFrame,
                         df_zones,
                         min_observed_commuters: float):
    """Row-conditional targets per (origin zone, destination Kreis).

    Rows whose observed reference mass is below *min_observed_commuters* are
    excluded (coverage guard against censoring-edge noise) and counted.
    Returns ``(df_targets, stats)``; ``target_share`` sums to 1.0 within each
    anchorable row.
    """
    kreis = df_zones.set_index("zone_id")["kreis_id"]
    ref = df_ref_od_zones.copy()
    ref["dest_kreis"] = ref["destination_zone_id"].map(kreis)
    if ref["dest_kreis"].isna().any():
        bad = ref.loc[ref["dest_kreis"].isna(), "destination_zone_id"].unique()
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] reference destination "
            f"zone(s) without a Kreis: {sorted(map(str, bad))[:5]}"
        )

    row_mass = (ref.groupby(["origin_zone_id", "dest_kreis"])["commuters"]
                .sum().rename("row_observed_commuters"))
    ref = ref.merge(row_mass.reset_index(),
                    on=["origin_zone_id", "dest_kreis"], how="left")

    n_rows_total = len(row_mass)
    anchorable = ref["row_observed_commuters"] >= float(min_observed_commuters)
    targets = ref[anchorable].copy()
    targets["target_share"] = targets["commuters"] / targets["row_observed_commuters"]

    n_rows_anchorable = targets.groupby(
        ["origin_zone_id", "dest_kreis"]).ngroups
    stats = dict(
        n_rows_total=int(n_rows_total),
        n_rows_anchorable=int(n_rows_anchorable),
        n_rows_skipped_coverage=int(n_rows_total - n_rows_anchorable),
        observed_commuters_quantiles={
            q: float(row_mass.quantile(q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
        } if n_rows_total else {},
    )
    skip_fraction = (stats["n_rows_skipped_coverage"] / n_rows_total
                     if n_rows_total else 0.0)
    warn_prefix = "WARNING: " if skip_fraction > HIGH_COVERAGE_SKIP_WARN_FRACTION else ""
    print(
        f"[braunschweig.gravity.verbindungen_anchor] {warn_prefix}targets: "
        f"{stats['n_rows_anchorable']}/{stats['n_rows_total']} rows anchorable "
        f"(min_observed_commuters={min_observed_commuters}), "
        f"{stats['n_rows_skipped_coverage']} skipped by coverage"
    )
    return (targets[["origin_zone_id", "dest_kreis", "destination_zone_id",
                     "target_share", "row_observed_commuters"]]
            .reset_index(drop=True), stats)


def apply_inner_anchor(df_od: pd.DataFrame,
                       df_zone_map: pd.DataFrame,
                       df_zones,
                       df_targets: pd.DataFrame):
    """One-shot row-conditional anchoring of the calibrated Gemeinde OD.

    For every target row ``(origin zone o, dest Kreis K)``: scale the flows to
    each observed destination zone so the conditional shares match
    ``target_share`` while the row-observed mass ``M(o, K)`` is preserved;
    Gemeinde-level flows inside a zone pair scale proportionally (pass-down).
    Rows where the model carries (near-)zero observed mass are skipped and
    counted (never force mass from nothing). Censored zone pairs and
    unmapped (external) flows are untouched.

    When SOME observed destination zones of an anchorable row carry zero model
    mass (the gravity model routed nothing there although the reference sees
    commuters), those pairs cannot be scaled up from zero. Their target share
    is redistributed over the pairs the model CAN fill by renormalising the
    fillable shares to sum to one, so the FULL row-observed mass stays on the
    row and the Kreis-pair block total is preserved exactly (no invented mass
    on the zero pair). With no zero pair the fillable shares already sum to
    1.0 and this reduces to ``share * m / cur``. Rows that hit this
    renormalisation path are a FALLBACK (CLAUDE.md fallback transparency):
    they are counted in ``n_rows_partial_zero_renorm`` and named in the log.

    Conservation is asserted (relative 1e-9): per-row observed mass and every
    Kreis-pair block total. Violations raise RuntimeError.
    """
    zmap = df_zone_map.set_index("commune_id")["zone_id"]
    kreis = df_zones.set_index("zone_id")["kreis_id"]

    out = df_od.copy()
    out["_oz"] = out["origin_id"].astype(str).map(zmap)
    out["_dz"] = out["destination_id"].astype(str).map(zmap)
    out["_dk"] = out["_dz"].map(kreis)

    # Current model mass per zone pair, per (origin zone, dest Kreis) row.
    in_scope = out["_oz"].notna() & out["_dz"].notna()
    pair_mass = (out[in_scope]
                 .groupby(["_oz", "_dk", "_dz"])["flow"].sum())

    total_flow = float(out["flow"].sum())
    block_before = out[in_scope].groupby(
        [out.loc[in_scope, "_oz"].map(kreis), "_dk"])["flow"].sum()

    n_rows_anchored = 0
    n_rows_skipped_zero_mass = 0
    n_rows_partial_zero_renorm = 0
    anchored_mass = 0.0
    lambda_max = 1.0
    factors = {}   # (oz, dz) -> lambda
    # (oz, dk) -> (m, dest_zone_ids): pre-scaling row-observed mass and its
    # target destination zones, kept for the post-scaling per-row assertion.
    row_observed_mass = {}
    # Rows that hit the partial-zero renormalisation fallback, and rows that
    # hit the pathological all-observed-dests-zero-model skip; kept only for
    # the log line below (CLAUDE.md fallback transparency: never let a
    # fallback or a skip vanish silently, even one folded into another stat).
    partial_zero_rows = []
    fillable_zero_skip_rows = []

    for (oz, dk), row_targets in df_targets.groupby(
            ["origin_zone_id", "dest_kreis"]):
        obs_pairs = [(oz, dk, dz) for dz in row_targets["destination_zone_id"]]
        m = float(sum(pair_mass.get(p, 0.0) for p in obs_pairs))
        if m <= ZERO_MASS_EPS:
            n_rows_skipped_zero_mass += 1
            continue
        # Fillable pairs = observed destination zones the model actually
        # reaches (cur > 0). A reference-observed pair the model routed
        # (near-)zero to cannot be scaled up from nothing; renormalising the
        # fillable target shares to sum to one redistributes such a pair's
        # share over the fillable pairs. This keeps the WHOLE row mass m on the
        # row (so the Kreis-pair block total is conserved) instead of leaking
        # the zero pair's share out of the block. With no zero pair this sum is
        # 1.0 and the scaling below reduces to ``share * m / cur``.
        zero_model_dzs = [
            dz for dz in row_targets["destination_zone_id"]
            if float(pair_mass.get((oz, dk, dz), 0.0)) <= ZERO_MASS_EPS
        ]
        fillable_share_sum = float(sum(
            share
            for dz, share in zip(row_targets["destination_zone_id"],
                                 row_targets["target_share"])
            if float(pair_mass.get((oz, dk, dz), 0.0)) > ZERO_MASS_EPS))
        if fillable_share_sum <= 0.0:
            # No observed destination zone carries usable model mass: the row
            # cannot be anchored without inventing flow -> skip and count it.
            # This is the pathological case where the row-level mass m barely
            # clears ZERO_MASS_EPS but every individual observed pair is still
            # at or below it (only reachable with near-degenerate float
            # inputs). Folded into n_rows_skipped_zero_mass for schema
            # stability, but tracked separately so it stays visible in the log
            # below (CLAUDE.md fallback transparency: a skip folded into
            # another stat must still never vanish silently).
            n_rows_skipped_zero_mass += 1
            fillable_zero_skip_rows.append((oz, dk))
            continue
        n_rows_anchored += 1
        anchored_mass += m
        row_observed_mass[(oz, dk)] = (
            m, tuple(row_targets["destination_zone_id"]))
        if zero_model_dzs:
            # At least one observed destination zone of this ANCHORED row was
            # zero-model and had its share renormalised onto the fillable
            # pairs above: this is the FALLBACK path (CLAUDE.md fallback
            # transparency) -- count and log it, never let it fire silently.
            n_rows_partial_zero_renorm += 1
            partial_zero_rows.append((oz, dk, zero_model_dzs))
        for dz, share in zip(row_targets["destination_zone_id"],
                             row_targets["target_share"]):
            cur = float(pair_mass.get((oz, dk, dz), 0.0))
            if cur <= ZERO_MASS_EPS:
                # Observed in the reference but zero in the model on THIS pair;
                # its share was excluded from fillable_share_sum above, so the
                # row's mass is renormalised onto the fillable pairs. No mass
                # is invented here -- the pair stays zero.
                continue
            factors[(oz, dz)] = (share / fillable_share_sum) * m / cur
            lambda_max = max(lambda_max, factors[(oz, dz)])

    if factors:
        keys = list(zip(out["_oz"], out["_dz"]))
        lam = np.array([factors.get(k, 1.0) for k in keys])
        out["flow"] = out["flow"].to_numpy() * lam

    # --- conservation assertions (raise, never warn) ------------------------
    if not np.isclose(float(out["flow"].sum()), total_flow,
                      rtol=CONSERVATION_RTOL, atol=0.0):
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] total mass not "
            f"conserved: {total_flow} -> {float(out['flow'].sum())}"
        )

    # Per-row observed mass: recompute the post-scaling flow summed over each
    # ANCHORED row's own observed destination zones and compare against the
    # pre-scaling mass `m` recorded for that row (explicit relative diff, not
    # np.isclose with a hidden atol, matching the block-check style below).
    # This is a safety net against accounting bugs local to a single row --
    # e.g. a duplicate df_targets row silently overwriting a factor -- that
    # could otherwise cancel out in the coarser block total and go unnoticed.
    if row_observed_mass:
        pair_mass_after = (out[in_scope]
                           .groupby(["_oz", "_dk", "_dz"])["flow"].sum())
        row_bad = {}
        for (oz, dk), (m, dest_zone_ids) in row_observed_mass.items():
            post = float(sum(pair_mass_after.get((oz, dk, dz), 0.0)
                             for dz in dest_zone_ids))
            rel_diff = abs(post - m) / m
            if rel_diff > CONSERVATION_RTOL:
                row_bad[(oz, dk)] = (m, post, rel_diff)
        if row_bad:
            top = dict(sorted(row_bad.items(), key=lambda kv: -kv[1][2])[:5])
            raise RuntimeError(
                "[braunschweig.gravity.verbindungen_anchor] per-row observed "
                f"mass not conserved for row(s) (m, post, rel_diff): {top}"
            )

    block_after = out[in_scope].groupby(
        [out.loc[in_scope, "_oz"].map(kreis), "_dk"])["flow"].sum()
    diff = (block_after - block_before).abs()
    rel = diff / block_before.replace(0.0, np.nan)
    if (rel.dropna() > CONSERVATION_RTOL).any():
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] Kreis-block total(s) "
            "not conserved: "
            + str(rel.dropna().sort_values(ascending=False).head(3).to_dict())
        )

    stats = dict(
        n_rows_anchored=int(n_rows_anchored),
        n_rows_skipped_zero_mass=int(n_rows_skipped_zero_mass),
        n_rows_partial_zero_renorm=int(n_rows_partial_zero_renorm),
        anchored_mass_share=float(anchored_mass / total_flow) if total_flow else 0.0,
        lambda_max=float(lambda_max),
    )
    # See HIGH_PARTIAL_ZERO_WARN_FRACTION above: escalate the log when the
    # partial-zero-renorm fallback dominates the anchored rows (heuristic,
    # not a scientific reference -- CLAUDE.md fallback transparency).
    partial_zero_fraction = (n_rows_partial_zero_renorm / n_rows_anchored
                             if n_rows_anchored else 0.0)
    warn_prefix = ("WARNING: "
                  if partial_zero_fraction > HIGH_PARTIAL_ZERO_WARN_FRACTION
                  else "")
    detail = ""
    if partial_zero_rows:
        # Name the affected (origin_zone, dest_kreis) rows and their
        # zero-model destination zones so the fallback is debuggable, not
        # just counted; truncate like the id lists elsewhere in this module.
        rows_repr = [f"({r_oz}, {r_dk}) zero-model dests {r_dzs}"
                    for r_oz, r_dk, r_dzs in partial_zero_rows[:5]]
        detail += f"; partial-zero renorm fallback rows: {rows_repr}"
    if fillable_zero_skip_rows:
        detail += ("; pathological fillable-zero skips (row mass > eps but "
                  f"every observed pair <= eps): {fillable_zero_skip_rows[:5]}")
    print(
        f"[braunschweig.gravity.verbindungen_anchor] {warn_prefix}anchored rows "
        f"{stats['n_rows_anchored']}, skipped zero-mass "
        f"{stats['n_rows_skipped_zero_mass']}, partial-zero renorm "
        f"{stats['n_rows_partial_zero_renorm']}, anchored mass share "
        f"{100.0 * stats['anchored_mass_share']:.1f}%, lambda_max "
        f"{stats['lambda_max']:.2f}" + detail
    )
    return out.drop(columns=["_oz", "_dz", "_dk"]), stats


def run_inner_anchor(df_od: pd.DataFrame,
                     df_cells,
                     df_cell_commune: pd.DataFrame,
                     df_ref_od_cells: pd.DataFrame,
                     min_observed_commuters: float):
    """Orchestrator: the single call the gravity stage makes when the flag is ON."""
    from braunschweig.data.verbindungen.zones import build_comparison_zones

    df_zone_map, df_cell_zone, df_zones = build_comparison_zones(
        df_cells, df_cell_commune)
    df_ref_zones = collapse_od_to_zones(df_ref_od_cells, df_cell_zone)
    df_targets, target_stats = build_anchor_targets(
        df_ref_zones, df_zones, min_observed_commuters)
    df_out, apply_stats = apply_inner_anchor(
        df_od, df_zone_map, df_zones, df_targets)
    return df_out, {**target_stats, **apply_stats}


CENSORING_BOUND_COMMUTERS = 10.0  # QZM removes relations < 10 (2019 counts)


def censored_bound_diagnostic(df_od_zones_model: pd.DataFrame,
                              df_ref_od_zones: pd.DataFrame):
    """Censored means "< 10 commuters (2019)" -- an upper bound the anchor
    itself never uses. Report how strongly model mass on censored zone pairs
    violates the 10-commuter-equivalent bound after scaling model mass to the
    reference universe via the GLOBAL observed ratio (sum of observed
    reference commuters / model mass on the observed pairs). A single global
    ratio keeps the diagnostic assumption-light; per-block ratios were
    considered and rejected (blocks with little observed mass would produce
    unstable ratios). Diagnostic only; a candidate soft cap if violations
    are large."""
    obs_keys = set(zip(df_ref_od_zones["origin_zone_id"],
                       df_ref_od_zones["destination_zone_id"]))
    model = df_od_zones_model.copy()
    model["_obs"] = [
        (o, d) in obs_keys
        for o, d in zip(model["origin_zone_id"], model["destination_zone_id"])
    ]
    obs_model = float(model.loc[model["_obs"], "commuters"].sum())
    obs_ref = float(df_ref_od_zones["commuters"].sum())
    global_ratio = obs_ref / obs_model if obs_model > 0 else np.nan

    cens = model[~model["_obs"]].copy()
    cens["ref_equivalent"] = cens["commuters"] * global_ratio
    cens["ratio_to_bound"] = cens["ref_equivalent"] / CENSORING_BOUND_COMMUTERS
    cens = cens.rename(columns={"commuters": "model_flow"})[
        ["origin_zone_id", "destination_zone_id", "model_flow",
         "ref_equivalent", "ratio_to_bound"]]

    total = float(model["commuters"].sum())
    mass = float(cens["model_flow"].sum())
    summary = dict(
        censored_mass_share=mass / total if total else 0.0,
        share_ratio_gt_1=float(cens.loc[cens["ratio_to_bound"] > 1.0,
                                        "model_flow"].sum() / total) if total else 0.0,
        share_ratio_gt_5=float(cens.loc[cens["ratio_to_bound"] > 5.0,
                                        "model_flow"].sum() / total) if total else 0.0,
        ratio_p50=float(cens["ratio_to_bound"].quantile(0.5)) if len(cens) else 0.0,
        ratio_p90=float(cens["ratio_to_bound"].quantile(0.9)) if len(cens) else 0.0,
    )
    print(
        "[braunschweig.gravity.verbindungen_anchor] censored-bound diagnostic: "
        f"censored mass {100.0 * summary['censored_mass_share']:.1f}%, "
        f"mass with ratio>1 {100.0 * summary['share_ratio_gt_1']:.1f}%, "
        f">5 {100.0 * summary['share_ratio_gt_5']:.1f}% "
        f"(p50 {summary['ratio_p50']:.2f}, p90 {summary['ratio_p90']:.2f})"
    )
    return cens.reset_index(drop=True), summary


def ao_margin_diagnostic(df_od_zones_before: pd.DataFrame,
                         df_od_zones_after: pd.DataFrame,
                         df_margins: pd.DataFrame,
                         df_cell_zone: pd.DataFrame) -> dict:
    """Workplace-inflow shares per zone vs the observed Statisch_AO margins,
    before vs after anchoring (side-effect check on the attraction axis)."""
    from braunschweig.analysis.verbindungen_validation import margin_check

    zone = df_cell_zone.set_index("cell_id")["zone_id"]
    m = df_margins.copy()
    m["zone_id"] = m["cell_id"].map(zone)
    ao = (m.dropna(subset=["zone_id"])
          .groupby("zone_id")["workers_at_workplace"].sum(min_count=1))

    out = {}
    for label, od in (("before", df_od_zones_before),
                      ("after", df_od_zones_after)):
        inflow = od.groupby("destination_zone_id")["commuters"].sum()
        idx = ao.index
        out[label] = margin_check(inflow.reindex(idx).fillna(0.0),
                                  ao.astype("Float64"))
    print(
        "[braunschweig.gravity.verbindungen_anchor] AO-margin diagnostic: "
        f"srmse before {out['before']['srmse']:.4f} -> after "
        f"{out['after']['srmse']:.4f} (n={out['after']['n_cells']})"
    )
    return out


def intra_kreis_diagnostic(df_od_gemeinde: pd.DataFrame,
                           df_ref_od_zones: pd.DataFrame,
                           df_zones,
                           df_zone_map: pd.DataFrame) -> pd.DataFrame:
    """Synthesised intra-Kreis outer targets vs QZM-OBSERVED intra shares.

    The outer anchor's intra-Kreis targets come from ``_synthesise_intra_kreis``
    (the Pendleratlas has no intra rows); the QZM actually observes intra-Kreis
    commuting. Per Kreis this compares the model's realised intra share (which
    equals the synthesised target after calibration) with the QZM share --
    a health check on the weakest outer-anchor component. Diagnostic only
    (vintage/universe forbid replacing the outer target)."""
    zmap = df_zone_map.set_index("commune_id")["zone_id"]
    kreis = df_zones.set_index("zone_id")["kreis_id"]

    od = df_od_gemeinde.copy()
    od["ok"] = od["origin_id"].astype(str).map(zmap).map(kreis)
    od["dk"] = od["destination_id"].astype(str).map(zmap).map(kreis)
    od = od.dropna(subset=["ok", "dk"])
    row_total = od.groupby("ok")["flow"].sum()
    intra = od[od["ok"] == od["dk"]].groupby("ok")["flow"].sum()

    ref = df_ref_od_zones.copy()
    ref["ok"] = ref["origin_zone_id"].map(kreis)
    ref["dk"] = ref["destination_zone_id"].map(kreis)
    ref_total = ref.groupby("ok")["commuters"].sum()
    ref_intra = ref[ref["ok"] == ref["dk"]].groupby("ok")["commuters"].sum()

    out = pd.DataFrame({
        "kreis_id": row_total.index,
        "model_intra_flow": intra.reindex(row_total.index).fillna(0.0).to_numpy(),
        "model_row_total": row_total.to_numpy(),
    })
    out["model_intra_share"] = out["model_intra_flow"] / out["model_row_total"]
    out["qzm_intra_share"] = (
        ref_intra.reindex(out["kreis_id"]).fillna(0.0)
        / ref_total.reindex(out["kreis_id"])
    ).to_numpy()
    out["share_delta"] = out["model_intra_share"] - out["qzm_intra_share"]
    print(
        "[braunschweig.gravity.verbindungen_anchor] intra-Kreis diagnostic: "
        f"max |share delta| {out['share_delta'].abs().max():.4f} over "
        f"{len(out)} Kreise"
    )
    return out.reset_index(drop=True)
