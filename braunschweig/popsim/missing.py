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
    # Widen the item-nonresponse set with the spec's per-attribute impute_codes so
    # those codes are imputed from the valid pool (and crucially classified as
    # nonresponse BEFORE the valid_or_unknown check, so they never trigger the
    # unenumerated raise). The global NONRESPONSE_CODES constant is left untouched.
    nonresponse_set = NONRESPONSE_CODES | set(spec.impute_codes)
    klass = src.map(lambda c: classify_code(c, structural_codes, nonresponse_set))

    valid_codes = set(spec.value_map)
    is_valid = (klass == "valid_or_unknown") & src.isin(valid_codes)
    is_unknown = (klass == "valid_or_unknown") & ~src.isin(valid_codes)
    if is_unknown.any():
        bad = src[is_unknown].value_counts().to_dict()
        raise ValueError(
            f"[popsim.missing] {spec.name}: {int(is_unknown.sum())} rows carry "
            f"codes that are neither in value_map, structural, nor NONRESPONSE_CODES "
            f"(unenumerated): {bad}. Map them explicitly (no silent NaN->True)."
        )

    out = pd.Series(index=df.index, dtype=object)
    out[is_valid] = src[is_valid].map(spec.value_map)
    out[klass == "structural"] = src[klass == "structural"].map(spec.structural)

    valid_pool = out[is_valid]
    nonresp_idx = out.index[klass == "nonresponse"]
    for idx in nonresp_idx:
        pool = valid_pool
        if spec.group_cols:
            mask = pd.Series(True, index=valid_pool.index)
            for col in spec.group_cols:
                mask &= df.loc[valid_pool.index, col].values == df.at[idx, col]
            grouped = valid_pool[mask.values]
            if len(grouped) > 0:
                pool = grouped
        out.at[idx] = pool.iloc[rng.randint(len(pool))] if len(pool) > 0 else spec.default

    n_struct = int((klass == "structural").sum())
    n_nonresp = int((klass == "nonresponse").sum())
    report = MissingReport(spec.name, len(df), int(is_valid.sum()), n_struct, n_nonresp)
    logger.info(
        "[popsim.missing] %s: %d/%d structural (deterministic), %d (%.2f%%) item-nonresponse "
        "(imputed from %s group)",
        spec.name, n_struct, len(df), n_nonresp, 100.0 * report.nonresponse_share,
        spec.group_cols or "global",
    )
    return out, report
