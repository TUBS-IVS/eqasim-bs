"""Per-1km-cell car/bike ownership targets for PopulationSim (issue #240).

SHAPE  = MiD 2023 B1 conditionals P(count | RegioStaR7 x haustyp)
         (committed aggregates mid2023_{cars,bikes}_by_rs7_haustyp.csv),
         mixed per 1km cell by its Zensus dwelling composition.
LEVEL  = the blended target2026_{number_of_cars,number_of_bicycles} KREIS tables
         (the SAME anchors the KREIS ownership controls consume) -- the per-cell
         priors are IPF-raked per Kreis so the 1km layer aggregates exactly to
         the KREIS layer (asserted; no second anchor truth).
OUTPUT = 9 per-100m-cell columns OWN_CARS_{0,1,2,3plus}_agg +
         OWN_BIKES_{0,1,2,3,4plus}_agg, back-distributed from the raked 1km
         targets proportional to the 100m household totals, so the existing
         per-geography aggregation reproduces the 1km targets bit-for-bit.

The per-cell prior is a MODELLED reference (ASSUMPTION: the national MiD
ownership <-> RS7 x building-type relationship holds spatially within the ZGB;
the LEVEL deliberately does not rest on it). See the #240 ADR.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RS7_CLASSES = tuple(range(71, 78))
HAUSTYP_CLASSES = (1, 2, 3, 4)

CARS_CATEGORIES = ("0", "1", "2", "3plus")
BIKES_CATEGORIES = ("0", "1", "2", "3", "4plus")
CARS_COLUMNS = tuple(f"OWN_CARS_{c}_agg" for c in CARS_CATEGORIES)
BIKES_COLUMNS = tuple(f"OWN_BIKES_{c}_agg" for c in BIKES_CATEGORIES)
OWNERSHIP_COLUMNS = CARS_COLUMNS + BIKES_COLUMNS

_CARS_SHARE_COLUMNS = tuple(f"cars_{c}" for c in CARS_CATEGORIES)
_BIKES_SHARE_COLUMNS = tuple(f"bikes_{c}" for c in BIKES_CATEGORIES)

# Cleaned prepared-cell dwelling columns -> MiD haustyp class, per the repo's
# building_type_3class convention (braunschweig.popsim.donor: 1 = EFH/ZFH,
# 2 = MFH 3-12 dwellings, 3 = Geschosswohnungsbau 13+, 4 = sonstiges).
_DW = "_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"
DWELLING_COLUMNS_BY_HAUSTYP: dict[int, tuple[str, ...]] = {
    1: ("FreiEFH" + _DW, "EFH_DHH" + _DW, "EFH_Reihenhaus" + _DW,
        "Freist_ZFH" + _DW, "ZFH_DHH" + _DW, "ZFH_Reihenhaus" + _DW),
    2: ("MFH_3bis6Wohnungen" + _DW, "MFH_7bis12Wohnungen" + _DW),
    3: ("MFH_13undmehrWohnungen" + _DW,),
    4: ("AndererGebaeudetyp" + _DW,),
}
DWELLING_INPUT_COLUMNS: tuple[str, ...] = tuple(
    c for cols in DWELLING_COLUMNS_BY_HAUSTYP.values() for c in cols)


def _dwelling_columns_in_haustyp_order() -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Return the (haustyp class, dwelling columns) pairs in HAUSTYP_CLASSES order.

    The dwelling-matrix COLUMN ORDER is a contract between the two halves of this
    module: per_cell_ownership_priors reads matrix column j as haustyp
    HAUSTYP_CLASSES[j]. Building the matrix from DWELLING_COLUMNS_BY_HAUSTYP's dict
    literal order instead would make that contract implicit, so a future reordering of
    the literal would silently swap conditional rows between building types (a
    wrong-but-green result). Hence the explicit HAUSTYP_CLASSES ordering here plus a
    loud check that both constants describe the same class set.
    """
    mapped = set(DWELLING_COLUMNS_BY_HAUSTYP)
    expected = set(HAUSTYP_CLASSES)
    if mapped != expected:
        raise ValueError(
            "ownership_grid: DWELLING_COLUMNS_BY_HAUSTYP and HAUSTYP_CLASSES describe "
            f"different haustyp classes (only in the column mapping: {sorted(mapped - expected)}; "
            f"only in HAUSTYP_CLASSES: {sorted(expected - mapped)}). The dwelling matrix column "
            "order is a contract with per_cell_ownership_priors (column j == haustyp "
            "HAUSTYP_CLASSES[j]) and must not be built from a divergent mapping; update both "
            "constants (and the committed conditional CSVs) together.")
    return tuple((ht, DWELLING_COLUMNS_BY_HAUSTYP[ht]) for ht in HAUSTYP_CLASSES)


def _load_one_conditional(data_path: str, filename: str, share_columns: tuple[str, ...]) -> pd.DataFrame:
    path = f"{data_path}/braunschweig/mid/{filename}"
    df = pd.read_csv(path, comment="#")
    missing = [c for c in ("rs7", "ht", *share_columns, "n_unweighted") if c not in df.columns]
    if missing:
        raise ValueError(f"{filename}: missing columns {missing}.")
    df = df.astype({"rs7": int, "ht": int}).set_index(["rs7", "ht"]).sort_index()
    duplicated = df.index[df.index.duplicated()].unique().tolist()
    if duplicated:
        raise ValueError(
            f"{filename}: duplicate (rs7, ht) rows {duplicated}; each conditional cell "
            "must appear exactly once (a duplicate makes the per-cell lookup ambiguous "
            "and would surface far downstream as a frame where a row is expected).")
    expected = [(r, h) for r in RS7_CLASSES for h in HAUSTYP_CLASSES]
    absent = [k for k in expected if k not in df.index]
    if absent:
        raise ValueError(f"{filename}: conditional grid incomplete, missing (rs7, ht) cells {absent}.")
    sums = df[list(share_columns)].sum(axis=1)
    bad = sums[(sums - 1.0).abs() > 1e-6]
    if not bad.empty:
        raise ValueError(
            f"{filename}: share rows must sum to 1 (tolerance 1e-6); offending (rs7, ht): "
            f"{bad.index.tolist()} with sums {bad.round(6).tolist()}.")
    if (df[list(share_columns)] < 0).any().any():
        raise ValueError(f"{filename}: negative share values.")
    return df


def load_ownership_conditionals(data_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load + validate the two committed RS7 x haustyp conditionals, indexed by (rs7, ht)."""
    cars = _load_one_conditional(data_path, "mid2023_cars_by_rs7_haustyp.csv", _CARS_SHARE_COLUMNS)
    bikes = _load_one_conditional(data_path, "mid2023_bikes_by_rs7_haustyp.csv", _BIKES_SHARE_COLUMNS)
    return cars, bikes


def per_cell_ownership_priors(rs7, dwellings, conditional, share_columns, label):
    """Mix the conditional per cell by dwelling composition; RS7-marginal fallback.

    Parameters: rs7 (n,) int array of cell RS7 codes (71..77, already validated);
    dwellings (n, len(HAUSTYP_CLASSES)) float array of dwelling counts per haustyp class,
    column j being haustyp HAUSTYP_CLASSES[j] (the contract enforced by
    _dwelling_columns_in_haustyp_order on the producing side and by the shape check
    below on this side); conditional indexed by (rs7, ht); share_columns the category
    columns. Returns (n, n_cats) priors, rows summing to 1.

    Fallback transparency (CLAUDE.md MANDATORY): cells without any dwelling info use
    the n_unweighted-weighted RS7 marginal; the primary/fallback split is logged and
    a 100 % fallback rate raises (it means the dwelling columns are broken, not that
    every cell genuinely lacks buildings).

    Also raises if any RS7 class has zero total n_unweighted across its haustyp strata:
    that marginal would require a 0/0 division (NaN), and a NaN prior would silently
    poison the downstream IPF rake instead of failing loudly (no-silent-fallback rule).
    """
    n = len(rs7)
    n_cats = len(share_columns)
    lut = {key: conditional.loc[key, list(share_columns)].to_numpy(dtype=float)
           for key in conditional.index}
    marginal = {}
    for r in RS7_CLASSES:
        sub = conditional.loc[r]
        w = sub["n_unweighted"].to_numpy(dtype=float)
        w_sum = w.sum()
        if w_sum <= 0:
            raise ValueError(
                f"per_cell_ownership_priors[{label}]: RS7 class {r} has zero total "
                "n_unweighted across all haustyp strata; cannot compute an n-weighted "
                "marginal fallback for this RS7 class (check the conditional input for "
                "an unpopulated or corrupted survey-count column).")
        marginal[r] = (sub[list(share_columns)].to_numpy(dtype=float) * w[:, None]).sum(axis=0) / w_sum

    dw = np.asarray(dwellings, dtype=float)
    # The column count is part of the haustyp contract: a wider array would have its
    # extra classes counted in the dwelling TOTAL but never mixed into the prior, i.e.
    # silently drop that class's mass; a narrower one would mis-address the conditional.
    n_haustyp = len(HAUSTYP_CLASSES)
    if dw.ndim != 2 or dw.shape[1] != n_haustyp:
        raise ValueError(
            f"per_cell_ownership_priors[{label}]: dwellings must be a (n, {n_haustyp}) array "
            f"with column j holding haustyp HAUSTYP_CLASSES[j] = {HAUSTYP_CLASSES}, got shape "
            f"{dw.shape}; build it with _dwelling_columns_in_haustyp_order to keep the column "
            "order and the conditional's haustyp index aligned.")
    dw_tot = dw.sum(axis=1)
    prior = np.zeros((n, n_cats))
    n_fallback = 0
    for i in range(n):
        r = int(rs7[i])
        if dw_tot[i] > 0:
            shares = dw[i] / dw_tot[i]
            prior[i] = sum(shares[j] * lut[(r, HAUSTYP_CLASSES[j])] for j in range(n_haustyp))
        else:
            prior[i] = marginal[r]
            n_fallback += 1
    if n and n_fallback == n:
        raise ValueError(
            f"per_cell_ownership_priors[{label}]: ALL {n} cells hit the RS7-marginal "
            "fallback (no dwelling composition anywhere). This is a 100% fallback rate "
            "and indicates broken/unloaded dwelling columns, not a data property.")
    logger.info(
        "[ownership_grid] %s prior: primary (dwelling-mixed) %d/%d (%.1f%%), "
        "RS7-marginal fallback %d (%.1f%%)",
        label, n - n_fallback, n, 100.0 * (n - n_fallback) / max(n, 1),
        n_fallback, 100.0 * n_fallback / max(n, 1))
    return prior


def rake_ownership_targets(prior, hh, kreis, target_shares, share_columns, label,
                           *, tol=1e-9, max_iter=500):
    """IPF the per-cell priors to the per-Kreis target shares, in household counts.

    Rows (cells) keep their household total; columns (categories) hit
    share x Kreis-household-total. Raises on a Kreis absent from target_shares and on
    non-convergence within max_iter (e.g. a category the prior cannot supply) -- an
    unconverged rake would silently ship a wrong level (no-silent-fallback rule).

    Also raises immediately if the margin error becomes non-finite (NaN/Inf): under
    IEEE-754 semantics both `err < tol` and `err >= tol` are False for NaN, so without
    this explicit check a non-finite state (e.g. a NaN prior propagated from an
    upstream zero-weight RS7 stratum) would silently exhaust max_iter and fall through
    to returning NaN-poisoned output.
    """
    hh = np.asarray(hh, dtype=float)
    out = np.zeros_like(prior, dtype=float)
    for ars5 in pd.unique(kreis):
        if ars5 not in target_shares.index:
            raise ValueError(
                f"rake_ownership_targets[{label}]: Kreis {ars5} has cells but no row in "
                "the target table; refusing a silent skip.")
        m = kreis == ars5
        hh_k = hh[m]
        shares = target_shares.loc[ars5, list(share_columns)].to_numpy(dtype=float)
        shares = shares / shares.sum()  # renormalise the integer-rounded published row
        target_counts = shares * hh_k.sum()
        M = prior[m] * hh_k[:, None]
        err = np.inf
        for _ in range(max_iter):
            col = M.sum(axis=0)
            M *= np.where(col > 0, target_counts / np.maximum(col, 1e-300), 1.0)[None, :]
            M *= (hh_k / np.maximum(M.sum(axis=1), 1e-300))[:, None]
            err = float(np.max(np.abs(M.sum(axis=0) - target_counts)
                               / np.maximum(target_counts, 1.0)))
            if err < tol:
                break
        if not np.isfinite(err):
            raise ValueError(
                f"rake_ownership_targets[{label}]: Kreis {ars5} produced a non-finite "
                "relative margin error during the rake (NaN/Inf entered the IPF from a "
                "non-finite prior or target, e.g. a zero-weight RS7 stratum upstream in "
                "per_cell_ownership_priors); refusing to silently return NaN-poisoned output.")
        if err >= tol:
            raise ValueError(
                f"rake_ownership_targets[{label}]: Kreis {ars5} did not converge within "
                f"{max_iter} iterations (relative margin error {err:.2e}); the prior cannot "
                "supply the target margin (check for structurally-zero categories).")
        out[m] = M
    return out


def select_load_columns(load_cols, available_parquet_cols):
    """Extend the parquet load set with the dwelling composition columns that exist.

    ONLY the dwelling columns need requesting: the loader always derives ZENSUS1km
    from the 100m id and always loads RegionalSchlussel_ARS / RegioStaR7
    (mid.load_control_cells _EXTRA_CELL_COLUMNS), and the HH_TOTAL census column is
    part of the tier0 base columns. The OWN_* output columns are computed by
    add_ownership_grid_columns, never loaded; missing runtime inputs fail fast there.
    """
    available = set(available_parquet_cols)
    result, seen = [], set()
    for col in list(load_cols) + [c for c in DWELLING_INPUT_COLUMNS if c in available]:
        if col not in seen:
            seen.add(col)
            result.append(col)
    return result


def add_ownership_grid_columns(cells, cars_targets, bikes_targets, cars_cond, bikes_cond,
                               *, kreis_per_cell, hh_col=None):
    """Compute and join the 9 OWN_* per-100m target columns (see module docstring).

    ``kreis_per_cell`` MUST be the pipeline's own per-cell Kreis resolution
    (``mid.resolved_kreis_per_cell``: population-weighted, highest-id tie-break,
    zfill(12) ARS), which is constant within each ZENSUS1km parent. Reusing it is
    what guarantees the OWN 1km columns aggregate EXACTLY to the KREIS anchor
    counts (attribute_kreis_count_table groups by the same resolution); a second,
    drifting dominance rule here would let the two layers mildly conflict on
    straddling parents. A mixed parent therefore raises.
    """
    from braunschweig.popsim import cells as _cells
    from braunschweig.popsim.control_spec import HH_TOTAL_CENSUS_COLUMN

    # Resolve the dwelling-matrix column order (and validate the two haustyp constants
    # against each other) BEFORE touching data: a divergent mapping is a programming
    # error and must not be discoverable only via a subtly wrong prior.
    dwelling_order = _dwelling_columns_in_haustyp_order()
    hh_col = hh_col or HH_TOTAL_CENSUS_COLUMN
    for col in ("ZENSUS1km", "RegioStaR7", hh_col):
        if col not in cells.columns:
            raise ValueError(f"add_ownership_grid_columns: required column {col!r} absent from cells.")
    out = cells.copy()
    hh_raw = pd.to_numeric(out[hh_col], errors="coerce")
    n_negative = int((hh_raw < 0).sum())
    if n_negative:
        raise ValueError(
            f"add_ownership_grid_columns: {n_negative} cell(s) have a NEGATIVE {hh_col}; "
            "household counts cannot be negative and would produce negative ownership "
            "targets (CLAUDE.md validation rule). Check the prepared-cell source.")
    n_hh_nan = int(hh_raw.isna().sum())
    if n_hh_nan:
        logger.info("[ownership_grid] %d/%d cells have NaN %s -> treated as 0 households",
                    n_hh_nan, len(out), hh_col)
    hh100 = hh_raw.fillna(0.0).to_numpy(dtype=float)
    kreis100 = pd.Series(kreis_per_cell, index=out.index).astype(str).to_numpy()
    rs7_100 = pd.to_numeric(out["RegioStaR7"], errors="coerce")
    bad_rs7 = ~rs7_100.isin(RS7_CLASSES)
    if bad_rs7.any():
        raise ValueError(
            f"add_ownership_grid_columns: {int(bad_rs7.sum())} cells have RegioStaR7 outside "
            f"{RS7_CLASSES} (incl. NaN/unmapped); the ZGB prepared cells are expected to carry "
            "a valid RS7 everywhere (fix the parquet, no fallback).")

    # Per-class dwelling sums over the columns PRESENT, in HAUSTYP_CLASSES order (the
    # column-j == HAUSTYP_CLASSES[j] contract per_cell_ownership_priors relies on), with
    # NaN suppression made observable via the issue-#150 helper. An absent expected
    # column is logged by name (a partial class is data, not an all-or-nothing miss); a
    # fully absent dwelling set raises -- it would mean 100% RS7-marginal fallback.
    class_sums = []
    n_present_total = 0
    for ht_class, class_cols in dwelling_order:
        present = [c for c in class_cols if c in out.columns]
        absent = [c for c in class_cols if c not in out.columns]
        if absent:
            logger.warning(
                "[ownership_grid] dwelling class %d: %d/%d expected columns absent from the "
                "cells frame: %s", ht_class, len(absent), len(class_cols), absent)
        n_present_total += len(present)
        class_sums.append(_cells.sum_columns_logging_nan(
            out, present, f"ownership-grid dwelling sum haustyp {ht_class}").to_numpy(dtype=float))
    if n_present_total == 0:
        raise ValueError(
            "add_ownership_grid_columns: NONE of the ten dwelling composition columns is "
            "present on the cells frame; the prior would be a 100% RS7-marginal fallback. "
            "Check the parquet load-column selection (select_load_columns).")
    dw100 = np.column_stack(class_sums)

    grp = pd.DataFrame({"parent": out["ZENSUS1km"].astype(str), "kreis": kreis100,
                        "rs7": rs7_100.astype(int), "hh": hh100})
    kreis_nunique = grp.groupby("parent")["kreis"].nunique()
    if (kreis_nunique > 1).any():
        broken = kreis_nunique[kreis_nunique > 1].index.tolist()[:5]
        raise ValueError(
            "add_ownership_grid_columns: kreis_per_cell is not constant within ZENSUS1km "
            f"parents (e.g. {broken}); pass mid.resolved_kreis_per_cell (parent-atomic) -- "
            "a mixed parent would break the KREIS-layer aggregation identity.")
    # Household-weighted dominant RS7 per parent (RS7 has no cross-layer identity to
    # preserve, unlike the Kreis); deterministic tie-break, mix rate logged.
    dom_rs7 = (grp.groupby(["parent", "rs7"], as_index=False)["hh"].sum()
                  .sort_values(["hh", "rs7"], ascending=[False, True])
                  .drop_duplicates("parent").set_index("parent")["rs7"])
    parent_kreis = grp.drop_duplicates("parent").set_index("parent")["kreis"]
    n_parent = len(dom_rs7)
    n_multi_rs7 = int((grp.groupby("parent")["rs7"].nunique() > 1).sum())
    logger.info(
        "[ownership_grid] %d 1km parents; RS7-mixing %d (%.1f%%) -> household-weighted "
        "dominant RS7 assigned", n_parent, n_multi_rs7, 100.0 * n_multi_rs7 / max(n_parent, 1))

    hh1 = grp.groupby("parent")["hh"].sum()
    dw1 = (pd.DataFrame(dw100, index=grp["parent"].to_numpy()).groupby(level=0).sum()
             .reindex(hh1.index))
    parents = hh1.index.to_numpy()
    active = hh1.to_numpy() > 0
    rs7_p = dom_rs7.reindex(hh1.index).to_numpy()
    kreis_p = parent_kreis.reindex(hh1.index).to_numpy()
    logger.info("[ownership_grid] %d/%d 1km parents carry households", int(active.sum()), n_parent)

    parent_targets = {}
    for label, cond, share_cols, targets in (
            ("cars", cars_cond, _CARS_SHARE_COLUMNS, cars_targets),
            ("bikes", bikes_cond, _BIKES_SHARE_COLUMNS, bikes_targets)):
        prior = per_cell_ownership_priors(rs7_p[active], dw1.to_numpy()[active], cond,
                                          share_cols, label)
        raked = rake_ownership_targets(prior, hh1.to_numpy()[active], kreis_p[active],
                                       targets, share_cols, label)
        full = np.zeros((n_parent, len(share_cols)))
        full[active] = raked
        parent_targets[label] = pd.DataFrame(full, index=parents)

    # Back-distribute household-proportionally to the 100m member cells: any
    # within-parent split aggregates back identically; household shares keep the
    # 100m values consistent with the HH_TOTAL anchor.
    parent_hh = hh1.reindex(grp["parent"]).to_numpy(dtype=float)
    frac = np.divide(hh100, parent_hh, out=np.zeros_like(hh100), where=parent_hh > 0)
    for label, columns in (("cars", CARS_COLUMNS), ("bikes", BIKES_COLUMNS)):
        vals = parent_targets[label].reindex(grp["parent"]).to_numpy(dtype=float)
        for j, col in enumerate(columns):
            out[col] = vals[:, j] * frac
    return out
