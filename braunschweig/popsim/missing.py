"""Consistent, observable handling of MiD item non-response + structural missings.

Grounded in the MiD 2023 Handbuch zur Datennutzung (Tab. 2 antwortbedingt, Tab. 3
designbedingt; Kap. 6.3). See docs/data/MID2023_HANDBOOK_REFERENCE.md. One uniform
policy for every attribute: structural design-missings are mapped deterministically by
the registry; random item non-response is imputed from comparable respondents (within a
conditioning group), seeded; every attribute's structural/nonresponse RATE is logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

# MiD antwortbedingt (random item non-response) codes (Handbuch Tab. 2): keine Angabe
# 9/99/999..., unplausibel 94/994..., nicht berechenbar 95/995...
NONRESPONSE_CODES = frozenset({9, 99, 999, 9999, 94, 994, 9994, 95, 995, 9995})


def classify_code(code, structural, nonresponse=NONRESPONSE_CODES) -> str:
    """Classify a MiD code as 'structural', 'nonresponse', or 'valid_or_unknown'.

    ``nonresponse`` is the effective item-nonresponse set (the global
    ``NONRESPONSE_CODES`` by default, optionally widened by a spec's
    ``impute_codes`` so individual attributes can mark extra codes as
    impute-from-pool without polluting the global constant). A structural code
    takes precedence over a nonresponse code if both sets contain it.

    The 'valid_or_unknown' bucket is split in ``resolve``: codes present in the
    spec's ``value_map`` are valid; any remaining code is unenumerated and raised
    on (rather than silently becoming NaN, which ``.astype(bool)`` coerces to True).
    """
    if code in structural:
        return "structural"
    if code in nonresponse:
        return "nonresponse"
    return "valid_or_unknown"


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    source_col: str
    value_map: dict
    structural: dict = field(default_factory=dict)
    # Extra MiD codes to treat as item non-response (impute from the valid pool)
    # without adding them to the global NONRESPONSE_CODES constant. Used e.g. for
    # adult coverage / interview-mode codes that are not "no licence" but must be
    # imputed rather than forced to a deterministic value.
    impute_codes: tuple = ()
    group_cols: tuple = ()
    default: object = None


@dataclass(frozen=True)
class MissingReport:
    name: str
    n_total: int
    n_valid: int
    n_structural: int
    n_nonresponse: int

    @property
    def nonresponse_share(self) -> float:
        return self.n_nonresponse / self.n_total if self.n_total else 0.0


def resolve(df: pd.DataFrame, spec: AttributeSpec, *, rng) -> tuple[pd.Series, MissingReport]:
    """Resolve ``spec.source_col`` into ``spec.name`` under the uniform policy.

    valid code -> value_map; structural code -> deterministic structural value;
    nonresponse -> a draw from the valid values of the same conditioning group
    (``group_cols``), else ``spec.default``. Logs the structural + nonresponse rate.
    """
    src = df[spec.source_col]
    structural_codes = set(spec.structural)
    # Effective item-nonresponse set for THIS attribute:
    #   1. Start from the generic NONRESPONSE_CODES ({9, 99, 999, ...}).
    #   2. Remove any code that this spec explicitly enumerates in value_map. MiD
    #      missing codes are FIELD-WIDTH dependent (Handbuch Kap. 5.1: the index
    #      digit 9 is prefixed to the field width, so bare '9' is keine Angabe only
    #      for a single-digit field). For a two-digit field an enumerated '9' is a
    #      substantive category -- e.g. P_TAET=9 'Schueler/in' (keine Angabe = 99)
    #      or hheink_gr1=9 '4000-4600 EUR'. Without this removal the generic set
    #      shadows the value_map and every such row is wrongly imputed instead of
    #      mapped (issue #96: pupils imputed to employed=True). An explicit
    #      value_map entry always wins over the generic convention.
    #   3. Re-add the spec's per-attribute impute_codes so a deliberate per-spec
    #      override still forces imputation (impute_codes wins over value_map),
    #      and those codes are classified as nonresponse BEFORE the
    #      valid_or_unknown check so they never trigger the unenumerated raise.
    # The global NONRESPONSE_CODES constant itself is left untouched.
    nonresponse_set = (NONRESPONSE_CODES - set(spec.value_map)) | set(spec.impute_codes)

    # Vectorised classification, same precedence as classify_code (structural
    # wins over nonresponse). isin matches the `code in set` semantics of
    # classify_code (NaN -> neither set -> valid_or_unknown bucket).
    is_structural = src.isin(structural_codes)
    is_nonresponse = ~is_structural & src.isin(nonresponse_set)
    is_valid_or_unknown = ~is_structural & ~is_nonresponse

    valid_codes = set(spec.value_map)
    is_valid = is_valid_or_unknown & src.isin(valid_codes)
    is_unknown = is_valid_or_unknown & ~src.isin(valid_codes)
    if is_unknown.any():
        bad = src[is_unknown].value_counts().to_dict()
        raise ValueError(
            f"[popsim.missing] {spec.name}: {int(is_unknown.sum())} rows carry "
            f"codes that are neither in value_map, structural, nor NONRESPONSE_CODES "
            f"(unenumerated): {bad}. Map them explicitly (no silent NaN->True)."
        )

    out = pd.Series(index=df.index, dtype=object)
    out[is_valid] = src[is_valid].map(spec.value_map)
    out[is_structural] = src[is_structural].map(spec.structural)

    valid_pool = out[is_valid]
    nonresp_idx = out.index[is_nonresponse]

    # Pre-group the valid pool ONCE per conditioning key instead of rebuilding an
    # O(n_valid) boolean mask per nonresponse row (the old loop was
    # O(n_nonresponse x n_valid), minutes per attribute on the full MiD frame).
    # Semantics are identical to the per-row equality mask: groupby(dropna=True)
    # excludes NaN-keyed valid rows (NaN == x is always False), empty groups are
    # skipped (-> global-pool fallback), and within-group order is the original
    # valid_pool order, so the same rng draw selects the same donor value.
    grouped_pools: dict = {}
    if len(nonresp_idx) > 0 and spec.group_cols:
        group_cols = list(spec.group_cols)
        valid_keys = df.loc[valid_pool.index, group_cols]
        # Group by the column name (not a 1-list) to avoid the pandas 2.x
        # FutureWarning about length-1 list groupers; keys are normalised to
        # tuples either way so the per-row lookup below is uniform.
        grouper = group_cols[0] if len(group_cols) == 1 else group_cols
        for key, group in valid_keys.groupby(grouper, sort=False, dropna=True):
            if len(group) > 0:
                key_tuple = key if isinstance(key, tuple) else (key,)
                grouped_pools[key_tuple] = valid_pool.loc[group.index]

    if len(nonresp_idx) > 0 and spec.group_cols:
        nonresp_keys = list(
            df.loc[nonresp_idx, list(spec.group_cols)].itertuples(index=False, name=None)
        )
    else:
        nonresp_keys = [None] * len(nonresp_idx)

    for idx, key in zip(nonresp_idx, nonresp_keys):
        pool = valid_pool
        if spec.group_cols:
            grouped = grouped_pools.get(key)
            if grouped is not None:
                pool = grouped
        out.at[idx] = pool.iloc[rng.randint(len(pool))] if len(pool) > 0 else spec.default

    n_struct = int(is_structural.sum())
    n_nonresp = int(is_nonresponse.sum())
    report = MissingReport(spec.name, len(df), int(is_valid.sum()), n_struct, n_nonresp)
    logger.info(
        "[popsim.missing] %s: %d/%d structural (deterministic), %d (%.2f%%) item-nonresponse "
        "(imputed from %s group)",
        spec.name, n_struct, len(df), n_nonresp, 100.0 * report.nonresponse_share,
        spec.group_cols or "global",
    )
    return out, report
