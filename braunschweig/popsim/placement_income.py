"""placement_income (L2 of #108): donor-coherent income via signature-preserving reallocation.

Each synthetic household keeps its OWN MiD income (a seeded draw within its own
hheink_gr1 codebook bracket); the per-Kreis INKAR income relativity is approached by
permuting WHICH real donors sit in which Kreis, strictly inside exact control-signature
groups so every PopulationSim control aggregate (cell and Kreis) and every donor's
clone count are preserved. Pure module: no file I/O; the stage passes frames in.

Spec: docs/superpowers/specs/2026-07-17-placement-income-l2-design.md.
"""
from __future__ import annotations

import logging
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
# Deliberate same-package reuse of the redraw's bracket machinery (DRY): the own-income
# draw must use the SAME floor / open-top treatment so OFF vs ON differ only by design.
from braunschweig.popsim.income_kreis_control import (
    INCOME_MIN_EUR,
    INCOME_OPEN_TOP_MAX_EUR,
    INCOME_OPEN_TOP_PARETO_ALPHA,
    _draw_truncated_pareto,
    _truncated_pareto_mean,
)

logger = logging.getLogger(__name__)

# Dedicated RNG offset for the own-income within-bracket draw (disjoint from the
# build_persons +74511, kreis-seed +24680, tenure +83947 and redraw +91237 streams).
PLACEMENT_INCOME_RNG_OFFSET = 31771

# MiD hheink_gr1 codebook EUR ranges (monthly net household income). These are the
# bracket BOUNDS of the same codebook whose midpoints are committed in
# attributes.INCOME_GROUP_MIDPOINT_EUR; a unit test ties the two representations
# together so they cannot drift apart. The top bracket is open-ended.
INCOME_LABEL_BOUNDS_EUR: dict[str, tuple[float, float | None]] = {
    "under_500": (0.0, 500.0),
    "500_900": (500.0, 900.0),
    "900_1500": (900.0, 1500.0),
    "1500_2000": (1500.0, 2000.0),
    "2000_2600": (2000.0, 2600.0),
    "2600_3000": (2600.0, 3000.0),
    "3000_3600": (3000.0, 3600.0),
    "3600_4000": (3600.0, 4000.0),
    "4000_4600": (4000.0, 4600.0),
    "4600_5000": (4600.0, 5000.0),
    "5000_5600": (5000.0, 5600.0),
    "5600_6000": (5600.0, 6000.0),
    "6000_6600": (6000.0, 6600.0),
    "6600_7000": (6600.0, 7000.0),
    "over_7000": (7000.0, None),
}


def label_expected_eur(
    *,
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> dict[str, float]:
    """Expected EUR per income label, matching what draw_own_income_eur realises.

    Closed brackets: uniform on [max(low, INCOME_MIN_EUR), high) -> mean of the two.
    Open top: truncated-Pareto mean on [low, INCOME_OPEN_TOP_MAX_EUR] (default), else
    the exponential-tail mean used by the redraw path. Used for reallocation targeting
    so target and draw agree in expectation.
    """
    out: dict[str, float] = {}
    for label, (low, high) in INCOME_LABEL_BOUNDS_EUR.items():
        if high is None:
            if open_top_pareto:
                out[label] = _truncated_pareto_mean(low, INCOME_OPEN_TOP_MAX_EUR, pareto_alpha)
            else:
                out[label] = low * 1.4
        else:
            out[label] = (max(low, INCOME_MIN_EUR) + high) / 2.0
    return out


def draw_own_income_eur(
    labels: pd.Series,
    rng,
    *,
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> np.ndarray:
    """Seeded continuous EUR within each household's OWN income label bracket.

    NaN / unknown labels stay NaN (the caller keeps today's NaN shielding). Closed
    brackets draw uniform on [max(low, INCOME_MIN_EUR), high); the open top draws a
    truncated Pareto on [7000, INCOME_OPEN_TOP_MAX_EUR] - identical tail treatment to
    the income_kreis_control redraw, so distributions stay comparable OFF vs ON.
    Returns values rounded to whole EUR.
    """
    values = labels.astype("object").to_numpy()
    n = len(values)
    eur = np.full(n, np.nan, dtype=float)
    known = np.array([v in INCOME_LABEL_BOUNDS_EUR for v in values], dtype=bool)
    n_unknown_nonnull = int(sum(1 for v in values[~known] if isinstance(v, str)))
    if n_unknown_nonnull:
        logger.warning(
            "[placement_income] %d/%d labels are non-null but outside the codebook "
            "vocabulary; their income stays NaN (investigate upstream mapping).",
            n_unknown_nonnull, n,
        )
    # Draw label by label (15 labels max) so each subgroup consumes its own RNG block
    # deterministically in label order.
    for label in INCOME_LABEL_BOUNDS_EUR:
        mask = known & (values == label)
        m = int(mask.sum())
        if m == 0:
            continue
        low, high = INCOME_LABEL_BOUNDS_EUR[label]
        if high is None:
            if open_top_pareto:
                eur[mask] = _draw_truncated_pareto(m, low, INCOME_OPEN_TOP_MAX_EUR, pareto_alpha, rng)
            else:
                eur[mask] = np.minimum(low * (1.0 + rng.exponential(scale=0.4, size=m)),
                                       INCOME_OPEN_TOP_MAX_EUR)
        else:
            lo = max(low, INCOME_MIN_EUR)
            eur[mask] = lo + rng.random_sample(m) * (high - lo)
    return np.round(eur, 0)


def donor_expected_income_eur(
    donor_households: pd.DataFrame,
    *,
    group_col: str = "hheink_gr1",
    donor_col: str = "H_ID",
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> pd.Series:
    """Per-donor expected own income (EUR) from the RAW hheink_gr1 code.

    Codes outside 1..15 (missing / refused) -> NaN: such donors get a NEUTRAL weight in
    the reallocation and are excluded from means; their share is logged by the caller.
    Uses the raw code (no imputation) so targeting never invents an income.
    """
    if group_col not in donor_households.columns or donor_col not in donor_households.columns:
        raise ValueError(
            f"[placement_income] donor_expected_income_eur needs columns "
            f"{[donor_col, group_col]}; got {sorted(donor_households.columns)[:20]}."
        )
    exp = label_expected_eur(open_top_pareto=open_top_pareto, pareto_alpha=pareto_alpha)
    codes = pd.to_numeric(donor_households[group_col], errors="coerce")
    labels = codes.map(INCOME_CLASS_BY_GROUP)
    values = labels.map(exp)
    out = pd.Series(values.to_numpy(dtype=float), index=donor_households[donor_col].to_numpy())
    out.index.name = donor_col
    return out


def donor_control_signatures(
    controls: Sequence,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    *,
    seed: str = "mid",
    donor_col: str = "H_ID",
) -> pd.Series:
    """Per-donor exact control signature: one integer per active control.

    Household controls contribute the 0/1 indicator of the donor household; person
    controls contribute the COUNT of matching members. Two donors with equal
    signatures contribute identically to every control at every geography, so
    swapping their slots provably preserves all control aggregates.

    Expressions are the trusted repo-authored CatalogControl strings (the same ones
    PopulationSim evaluates); they are evaluated with plain eval over the seed frames
    (pd.eval cannot handle `.isin`). An inexpressible (None) or failing expression
    raises - a silently skipped control would break the exactness guarantee.
    """
    hh = seed_households.reset_index(drop=True)
    pp = seed_persons.reset_index(drop=True)
    donors = pd.Index(hh[donor_col].to_numpy(), name=donor_col)
    namespace = {"households": hh, "persons": pp, "np": np}
    columns: list[np.ndarray] = []
    for control in controls:
        expr = control.expression_for(seed)
        if expr is None:
            raise ValueError(
                f"[placement_income] control {control.name!r} is not expressible by seed "
                f"{seed!r}; signatures must cover EVERY active control (no silent drop)."
            )
        try:
            mask = eval(expr, {"__builtins__": {}}, namespace)  # noqa: S307 (trusted repo-authored expressions)
        except Exception as error:
            raise ValueError(
                f"[placement_income] control {control.name!r} expression {expr!r} failed "
                f"to evaluate on the seed frames: {error}"
            ) from error
        mask = pd.Series(np.asarray(mask, dtype=bool))
        if control.seed_table == "households":
            if len(mask) != len(hh):
                raise ValueError(
                    f"[placement_income] household control {control.name!r} produced "
                    f"{len(mask)} values for {len(hh)} households."
                )
            columns.append(mask.to_numpy(dtype=np.int64))
        elif control.seed_table == "persons":
            if len(mask) != len(pp):
                raise ValueError(
                    f"[placement_income] person control {control.name!r} produced "
                    f"{len(mask)} values for {len(pp)} persons."
                )
            counts = (
                pd.Series(mask.to_numpy(dtype=np.int64))
                .groupby(pp[donor_col].to_numpy()).sum()
                .reindex(donors, fill_value=0)
            )
            columns.append(counts.to_numpy(dtype=np.int64))
        else:
            raise ValueError(
                f"[placement_income] control {control.name!r} has unknown seed_table "
                f"{control.seed_table!r} (expected 'households' or 'persons')."
            )
    matrix = np.column_stack(columns) if columns else np.zeros((len(donors), 0), dtype=np.int64)
    signature = pd.Series([tuple(row) for row in matrix], index=donors)
    n_groups = signature.nunique()
    logger.info(
        "[placement_income] signatures: %d donors, %d controls, %d distinct signature groups.",
        len(donors), len(columns), n_groups,
    )
    return signature


def slots_kreis_stats(
    slots: pd.DataFrame,
    donor_households: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
    kreis_col: str = "ars5",
    size_col: str = "H_GR",
) -> pd.DataFrame:
    """Per-Kreis clone-weighted household stats of the CURRENT allocation.

    Feeds income_kreis_control.build_kreis_income_targets (columns ars5, mean_size,
    hh_count) so the INKAR construct correction uses the same population the
    reallocation acts on.
    """
    size_by_donor = pd.Series(
        pd.to_numeric(donor_households[size_col], errors="coerce").to_numpy(),
        index=donor_households[donor_col].to_numpy(),
    )
    df = slots[[donor_col, kreis_col]].copy()
    df["_size"] = df[donor_col].map(size_by_donor)
    grouped = df.groupby(kreis_col)
    out = pd.DataFrame({
        "ars5": [str(k) for k in grouped.groups],
        "mean_size": grouped["_size"].mean().to_numpy(dtype=float),
        "hh_count": grouped.size().to_numpy(dtype=float),
    })
    return out.reset_index(drop=True)


def _sinkhorn_realized(lam, row_of, col_of, c_row, n_col, kreis_of_col, y_of_row,
                       n_kreise, iters=25):
    """One Sinkhorn balance for the stacked (group, donor, kreis) triplets, then the
    per-Kreis realized income means and variances over the FREE mass. All arrays are
    triplet-aligned; deterministic. y_of_row is the raw EUR per donor row (NaN allowed:
    NaN rows get a NEUTRAL weight and are excluded from mean/variance)."""
    y_row_std = y_of_row.copy()
    valid_row = ~np.isnan(y_row_std)
    mu = float(y_row_std[valid_row].mean()) if valid_row.any() else 0.0
    sd = float(y_row_std[valid_row].std()) or 1.0
    ys = np.where(valid_row, (y_row_std - mu) / sd, 0.0)
    w = np.exp(np.clip(lam[kreis_of_col[col_of]] * ys[row_of], -60.0, 60.0))
    a = np.ones(len(c_row))
    b = np.ones(len(n_col))
    for _ in range(iters):
        t = w * a[row_of] * b[col_of]
        row_sum = np.bincount(row_of, weights=t, minlength=len(c_row))
        a *= np.where(row_sum > 0, c_row / np.maximum(row_sum, 1e-300), 1.0)
        t = w * a[row_of] * b[col_of]
        col_sum = np.bincount(col_of, weights=t, minlength=len(n_col))
        b *= np.where(col_sum > 0, n_col / np.maximum(col_sum, 1e-300), 1.0)
    t = w * a[row_of] * b[col_of]
    y_trip = y_of_row[row_of]
    valid = ~np.isnan(y_trip)
    kreis_trip = kreis_of_col[col_of]
    num = np.bincount(kreis_trip[valid], weights=(t * y_trip)[valid], minlength=n_kreise)
    den = np.bincount(kreis_trip[valid], weights=t[valid], minlength=n_kreise)
    mean = np.divide(num, den, out=np.full(n_kreise, np.nan), where=den > 0)
    centered = y_trip - np.where(np.isnan(mean[kreis_trip]), 0.0, mean[kreis_trip])
    var_num = np.bincount(kreis_trip[valid], weights=(t * centered ** 2)[valid],
                          minlength=n_kreise)
    var = np.divide(var_num, den, out=np.ones(n_kreise), where=den > 0)
    return t, mean, var, den


def reallocate_slots(
    slots: pd.DataFrame,
    *,
    signatures: pd.Series,
    expected_income_eur: pd.Series,
    target_factor: Mapping[str, float],
    donor_col: str = "H_ID",
    kreis_col: str = "ars5",
    max_sweeps: int = 60,
    tol: float = 1e-3,
) -> tuple[pd.Series, dict]:
    """Permute donor slots within exact signature groups toward per-Kreis income targets.

    Preserves exactly: every donor's total clone count, every slot's cell/Kreis, and the
    per-(Kreis, signature) composition - hence every control aggregate. Moves only WHICH
    equal-signature donor occupies which slot. Entropic transport: weights exp(lambda_k *
    standardized income), Sinkhorn to the (clone count, slot count) margins, damped
    Newton on lambda per Kreis (d mean / d lambda = within-Kreis income variance),
    deterministic greedy integerization (largest transport mass first, ties by donor id).
    Non-convergence and unreachable (clamped) targets are LOGGED, never silent.
    """
    df = slots[[donor_col, kreis_col]].copy()
    df[kreis_col] = df[kreis_col].astype(str)
    df["_sig"] = df[donor_col].map(signatures)
    if df["_sig"].isna().any():
        missing = sorted(df.loc[df["_sig"].isna(), donor_col].unique()[:10])
        raise ValueError(f"[placement_income] {int(df['_sig'].isna().sum())} slots reference donors "
                         f"without a signature (e.g. {missing}); the seed must cover every donor.")
    y_slot = df[donor_col].map(expected_income_eur)
    n_slots = len(df)
    nan_income_slot_share = float(y_slot.isna().mean())
    region_mean = float(y_slot.mean())

    kreise = sorted(df[kreis_col].unique())
    kreis_index = {k: i for i, k in enumerate(kreise)}
    y_valid = y_slot.dropna()
    y_lo, y_hi = (float(y_valid.min()), float(y_valid.max())) if len(y_valid) else (region_mean, region_mean)
    target_mean, clamped = {}, {}
    for k in kreise:
        raw = region_mean * float(target_factor.get(k, 1.0))
        t = min(max(raw, y_lo), y_hi)
        target_mean[k] = t
        clamped[k] = abs(t - raw) > 1e-9 * max(abs(raw), 1.0)

    realized_before = df.assign(_y=y_slot).groupby(kreis_col)["_y"].mean().to_dict()

    def _noop_diag(converged: bool, no_freedom_share: float, n_free: int) -> dict:
        return {
            "region_mean": region_mean, "kreis_target_mean": target_mean,
            "kreis_realized_before": realized_before, "kreis_realized_after": realized_before,
            "kreis_lambda": {k: 0.0 for k in kreise}, "kreis_clamped": clamped,
            "converged": converged, "sweeps_used": 0, "n_slots": n_slots, "n_moved": 0,
            "moved_share": 0.0, "n_groups": int(df["_sig"].nunique()), "n_free_groups": n_free,
            "no_freedom_slot_share": no_freedom_share,
            "nan_income_slot_share": nan_income_slot_share,
        }

    if len(kreise) < 2:
        logger.info("[placement_income] single Kreis in scope -> reallocation is a structural no-op.")
        return df[donor_col].copy(), _noop_diag(True, 1.0, 0)

    # --- Stacked triplet structure over FREE groups ----------------------------------
    # A group is free iff it spans >= 2 Kreise AND has >= 2 distinct non-NaN incomes.
    rows_c: list[float] = []      # donor clone count per (group, donor) row
    rows_donor: list = []         # donor id per row
    rows_y: list[float] = []      # donor expected income per row (NaN ok)
    cols_n: list[float] = []      # slot count per (group, kreis) column
    cols_kreis: list[int] = []    # kreis index per column
    cols_group: list[int] = []    # group index per column
    row_of_trip: list[int] = []
    col_of_trip: list[int] = []
    free_group_ids: list = []
    counts = df.groupby(["_sig", donor_col, kreis_col]).size()
    for sig, sub in counts.groupby(level=0):
        donors_g = sorted(sub.index.get_level_values(1).unique())
        kreise_g = sorted(sub.index.get_level_values(2).unique())
        incomes_g = expected_income_eur.reindex(donors_g)
        distinct = incomes_g.dropna().nunique()
        if len(kreise_g) < 2 or distinct < 2:
            continue
        g = len(free_group_ids)
        free_group_ids.append(sig)
        c_by_donor = sub.groupby(level=1).sum()
        n_by_kreis = sub.groupby(level=2).sum()
        row_base = len(rows_c)
        col_base = len(cols_n)
        for d in donors_g:
            rows_c.append(float(c_by_donor.loc[d]))
            rows_donor.append(d)
            rows_y.append(float(incomes_g.loc[d]) if not pd.isna(incomes_g.loc[d]) else np.nan)
        for k in kreise_g:
            cols_n.append(float(n_by_kreis.loc[k]))
            cols_kreis.append(kreis_index[k])
            cols_group.append(g)
        for i in range(len(donors_g)):
            for j in range(len(kreise_g)):
                row_of_trip.append(row_base + i)
                col_of_trip.append(col_base + j)

    n_free_groups = len(free_group_ids)
    free_sigs = set(free_group_ids)
    free_slot_mask = df["_sig"].isin(free_sigs)
    no_freedom_slot_share = float(1.0 - free_slot_mask.mean())
    if n_free_groups == 0:
        logger.warning(
            "[placement_income] PRIMARY path has no freedom: 0 free signature groups "
            "(no-freedom slot share 100.0%%) -> allocation unchanged. The signature set "
            "may be too fine for reallocation; consider the B' escalation (spec section 7).")
        return df[donor_col].copy(), _noop_diag(False, no_freedom_slot_share, 0)

    row_of = np.asarray(row_of_trip, dtype=np.int64)
    col_of = np.asarray(col_of_trip, dtype=np.int64)
    c_row = np.asarray(rows_c, dtype=float)
    n_col = np.asarray(cols_n, dtype=float)
    kreis_of_col = np.asarray(cols_kreis, dtype=np.int64)
    y_of_row = np.asarray(rows_y, dtype=float)

    # Frozen (non-free) slots contribute fixed sums to each Kreis mean.
    frozen = df.loc[~free_slot_mask].assign(_y=y_slot[~free_slot_mask])
    frozen_valid = frozen.dropna(subset=["_y"])
    frozen_num = frozen_valid.groupby(kreis_col)["_y"].sum()
    frozen_den = frozen_valid.groupby(kreis_col).size()

    y_all_valid = y_slot.dropna()
    y_sd = float(y_all_valid.std()) or 1.0

    lam = np.zeros(len(kreise))
    converged = False
    sweeps_used = 0
    for sweep in range(max_sweeps):
        sweeps_used = sweep + 1
        t, free_mean, free_var, free_den = _sinkhorn_realized(
            lam, row_of, col_of, c_row, n_col, kreis_of_col, y_of_row, len(kreise))
        realized = {}
        err = 0.0
        for k in kreise:
            i = kreis_index[k]
            f_mean = 0.0 if np.isnan(free_mean[i]) else float(free_mean[i])
            num = f_mean * float(free_den[i]) + float(frozen_num.get(k, 0.0))
            den = float(free_den[i]) + float(frozen_den.get(k, 0.0))
            realized[k] = num / den if den > 0 else float("nan")
            if den > 0 and not np.isnan(realized[k]):
                err = max(err, abs(realized[k] - target_mean[k]) / max(region_mean, 1.0))
        if err < tol:
            converged = True
            break
        for k in kreise:
            i = kreis_index[k]
            if np.isnan(realized.get(k, float("nan"))):
                continue
            var = max(float(free_var[i]), (0.05 * y_sd) ** 2)
            step = 0.8 * (target_mean[k] - realized[k]) / var * y_sd
            lam[i] = float(np.clip(lam[i] + step / max(y_sd, 1.0), -50.0, 50.0))
    if not converged:
        logger.warning(
            "[placement_income] lambda sweep did NOT converge in %d sweeps "
            "(worst relative residual above tol=%.4f); keeping the best allocation and "
            "reporting residuals honestly.", max_sweeps, tol)

    # --- Deterministic integerization: greedy largest transport mass first ------------
    t_final, _, _, _ = _sinkhorn_realized(
        lam, row_of, col_of, c_row, n_col, kreis_of_col, y_of_row, len(kreise))
    assignment = df[donor_col].copy()
    n_moved = 0
    donor_key = np.asarray([str(rows_donor[r]) for r in row_of])
    order = np.lexsort((col_of, donor_key, -t_final))
    row_rem = c_row.copy().astype(np.int64)
    col_rem = n_col.copy().astype(np.int64)
    take = np.zeros(len(t_final), dtype=np.int64)
    for idx in order:
        q = int(min(row_rem[row_of[idx]], col_rem[col_of[idx]]))
        if q > 0:
            take[idx] = q
            row_rem[row_of[idx]] -= q
            col_rem[col_of[idx]] -= q
    if row_rem.sum() != 0 or col_rem.sum() != 0:
        raise AssertionError(
            "[placement_income] integerization failed to exhaust margins "
            f"(row_rem={int(row_rem.sum())}, col_rem={int(col_rem.sum())}).")

    # Materialize: per (group, kreis) column, write the assigned donor multiset onto the
    # group's slot rows in that Kreis (rows in stable original order; donors sorted).
    df["_pos"] = np.arange(n_slots)
    cols_group_arr = np.asarray(cols_group, dtype=np.int64)
    for g, sig in enumerate(free_group_ids):
        for j in np.flatnonzero(cols_group_arr == g):
            k = kreise[kreis_of_col[j]]
            donor_list: list = []
            for idx in np.flatnonzero((col_of == j) & (take > 0)):
                donor_list.extend([rows_donor[row_of[idx]]] * int(take[idx]))
            donor_list.sort(key=str)
            rows = df[(df["_sig"] == sig) & (df[kreis_col] == k)].sort_values("_pos").index
            if len(rows) != len(donor_list):
                raise AssertionError(
                    f"[placement_income] group/kreis slot mismatch for signature group {g}, "
                    f"Kreis {k}: {len(rows)} rows vs {len(donor_list)} assigned donors.")
            before = assignment.loc[rows].to_numpy()
            assignment.loc[rows] = donor_list
            n_moved += int((assignment.loc[rows].to_numpy() != before).sum())

    out = df.assign(**{donor_col: assignment})
    realized_after = out.assign(_y=out[donor_col].map(expected_income_eur)).groupby(kreis_col)["_y"].mean().to_dict()
    moved_share = n_moved / n_slots if n_slots else 0.0
    logger.info(
        "[placement_income] reallocation: primary (free) slots %d/%d (%.1f%%), "
        "no-freedom %d (%.1f%%); moved %d slots (%.1f%%); converged=%s after %d sweeps; "
        "clamped targets: %s",
        int(free_slot_mask.sum()), n_slots, 100.0 * float(free_slot_mask.mean()),
        int((~free_slot_mask).sum()), 100.0 * no_freedom_slot_share,
        n_moved, 100.0 * moved_share, converged, sweeps_used,
        sorted([k for k, v in clamped.items() if v]) or "none",
    )
    if no_freedom_slot_share > 0.9:
        logger.warning(
            "[placement_income] no-freedom slot share %.1f%% > 90%% -- the PRIMARY "
            "reallocation barely acts; treat as a failure signal (spec section 7, B' escalation).",
            100.0 * no_freedom_slot_share)
    diag = {
        "region_mean": region_mean, "kreis_target_mean": target_mean,
        "kreis_realized_before": realized_before, "kreis_realized_after": realized_after,
        "kreis_lambda": {k: float(lam[kreis_index[k]]) for k in kreise},
        "kreis_clamped": clamped, "converged": converged, "sweeps_used": sweeps_used,
        "n_slots": n_slots, "n_moved": int(n_moved), "moved_share": float(moved_share),
        "n_groups": int(df["_sig"].nunique()), "n_free_groups": n_free_groups,
        "no_freedom_slot_share": no_freedom_slot_share,
        "nan_income_slot_share": nan_income_slot_share,
    }
    return assignment, diag
