"""Donor reuse and weight-class representativeness of one PopulationSim batch.

A synthetic household is a copy of one seed household (its ``H_ID`` in the expanded
output).  This module measures how often seed households are copied, and whether the
copies follow the seed survey weights (``H_GEW``) across weight classes and attributes.
Shares are computed on households; all numbers are derived from the batch inputs and
output and nothing is assumed about the reference population.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.analysis.population_validation.participation_fit import donor_neff

logger = logging.getLogger(__name__)

WEIGHT_CLASS_EDGES = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, float("inf"))
WEIGHT_CLASS_LABELS = ("<1", "1-2", "2-3", "3-5", "5-8", "8+")  # left-closed classes
TOP_N_SHARES = (100, 1000, 5000)
_DEFAULT_HOUSEHOLD_ID = "H_ID"


def usage_counts(expanded: pd.DataFrame, household_id_col: str = _DEFAULT_HOUSEHOLD_ID) -> pd.Series:
    """Number of synthetic copies per seed household, indexed by seed household id.

    A missing household id raises ``ValueError``; ``value_counts`` would drop it silently.
    """
    missing = int(expanded[household_id_col].isna().sum())
    if missing:
        raise ValueError(f"usage_counts: {missing} expanded row(s) have a missing "
                         f"{household_id_col}")
    counts = expanded[household_id_col].value_counts().sort_index()
    counts.index.name = household_id_col
    counts.name = "copies"
    return counts


def reuse_summary(usage: pd.Series, n_seed_households: int,
                  household_id_col: str = _DEFAULT_HOUSEHOLD_ID) -> dict:
    """Reuse statistics of one batch.

    ``top<N>_share`` is the share of synthetic households that are copies of the N most
    used seed households; with fewer than N distinct seed households it is 1.0.
    ``kish_n_eff`` is the effective sample size of the copies (see ``donor_neff``).
    """
    if usage.empty:
        raise ValueError("reuse_summary: no synthetic households (usage is empty)")
    if n_seed_households <= 0:
        raise ValueError("reuse_summary: n_seed_households must be positive")
    n_synthetic = int(usage.sum())
    # One row per synthetic household, so that donor_neff counts copies correctly.
    one_row_per_copy = pd.DataFrame({household_id_col: usage.index.repeat(usage.to_numpy())})
    summary = {
        "n_synthetic_households": n_synthetic,
        "n_seed_households": int(n_seed_households),
        "n_distinct_used": int(len(usage)),
        "distinct_share_of_seed": len(usage) / n_seed_households,
        "kish_n_eff": donor_neff(one_row_per_copy, household_id_col)["n_eff"],
        "max_copies": int(usage.max()),
    }
    for top_n in TOP_N_SHARES:
        summary[f"top{top_n}_share"] = float(usage.nlargest(top_n).sum()) / n_synthetic
    logger.info("[popsim_batch_diagnostics] reuse: %d synthetic households, %d of %d seed "
                "households used, max copies %d", n_synthetic, len(usage), n_seed_households,
                summary["max_copies"])
    return summary


def _seed_with_copies(seed_households: pd.DataFrame, usage: pd.Series, weight_col: str,
                      household_id_col: str) -> tuple[np.ndarray, np.ndarray]:
    """Seed weights and copies per seed household, aligned row by row.

    Raises ``ValueError`` for a missing, non-finite or negative seed weight and for a usage
    id that is not a seed household; nothing is dropped silently.
    """
    if usage.empty:
        raise ValueError("no synthetic households (usage is empty); weight shares are undefined")
    weights = seed_households[weight_col].to_numpy(dtype=float)
    invalid = ~np.isfinite(weights) | (weights < 0)
    if invalid.any():
        raise ValueError(
            f"seed households with missing, non-finite or negative {weight_col}: "
            f"{seed_households.loc[invalid, household_id_col].tolist()[:10]}")
    seed_ids = pd.Index(seed_households[household_id_col])
    unknown = usage.index[~usage.index.isin(seed_ids)]
    if len(unknown):
        raise ValueError(f"usage refers to household id(s) not in the seed households: "
                         f"{unknown.tolist()[:10]}")
    copies = usage.reindex(seed_ids).fillna(0).to_numpy(dtype=float)
    if weights.sum() <= 0:
        raise ValueError(f"seed {weight_col} sums to zero; weight shares are undefined")
    return weights, copies


def _shares(weights: np.ndarray, copies: np.ndarray, member: np.ndarray) -> tuple[float, float]:
    """Seed weight share and synthetic copy share of the households flagged by ``member``."""
    seed_share = float(weights[member].sum() / weights.sum())
    total_copies = copies.sum()
    synthetic_share = float(copies[member].sum() / total_copies) if total_copies > 0 else float("nan")
    return seed_share, synthetic_share


def weight_class_shares(seed_households: pd.DataFrame, usage: pd.Series,
                        weight_col: str = "H_GEW",
                        household_id_col: str = _DEFAULT_HOUSEHOLD_ID) -> pd.DataFrame:
    """Seed weight share versus synthetic copy share per household weight class.

    All six classes of ``WEIGHT_CLASS_LABELS`` appear in order, also when empty.
    ``difference_pp`` is ``(synthetic_share - seed_weight_share) * 100``.
    """
    weights, copies = _seed_with_copies(seed_households, usage, weight_col, household_id_col)
    rows = []
    for label, lower, upper in zip(WEIGHT_CLASS_LABELS, WEIGHT_CLASS_EDGES[:-1],
                                   WEIGHT_CLASS_EDGES[1:]):
        member = (weights >= lower) & (weights < upper)
        seed_share, synthetic_share = _shares(weights, copies, member)
        rows.append({"weight_class": label, "n_seed": int(member.sum()),
                     "seed_weight_share": seed_share, "synthetic_share": synthetic_share,
                     "difference_pp": (synthetic_share - seed_share) * 100})
    return pd.DataFrame(rows, columns=["weight_class", "n_seed", "seed_weight_share",
                                       "synthetic_share", "difference_pp"])


def shares_by_attribute(seed_households: pd.DataFrame, usage: pd.Series, column: str,
                        weight_col: str = "H_GEW",
                        household_id_col: str = _DEFAULT_HOUSEHOLD_ID) -> pd.DataFrame:
    """Seed weight share versus synthetic copy share per value of ``column``.

    NaN values form their own row (counted, not dropped).  ``ratio`` is
    ``synthetic_share / seed_weight_share`` and NaN where the seed weight share is 0.
    """
    if column not in seed_households.columns:
        raise KeyError(f"shares_by_attribute: column {column!r} is absent from the seed households")
    weights, copies = _seed_with_copies(seed_households, usage, weight_col, household_id_col)
    values = seed_households[column]
    present = sorted(v for v in pd.unique(values) if not pd.isna(v))
    keys = present + ([np.nan] if values.isna().any() else [])
    rows = []
    for key in keys:
        member = (values.isna() if pd.isna(key) else values == key).to_numpy()
        seed_share, synthetic_share = _shares(weights, copies, member)
        ratio = synthetic_share / seed_share if seed_share != 0 else float("nan")
        rows.append({"attribute": column, "value": key, "n_seed": int(member.sum()),
                     "seed_weight_share": seed_share, "synthetic_share": synthetic_share,
                     "ratio": ratio})
    return pd.DataFrame(rows, columns=["attribute", "value", "n_seed", "seed_weight_share",
                                       "synthetic_share", "ratio"])


def reuse_by_zone(expanded: pd.DataFrame, zone_col: str,
                  household_id_col: str = _DEFAULT_HOUSEHOLD_ID) -> pd.DataFrame:
    """Reuse statistics per zone value (e.g. the placement Kreis) of the expanded output.

    A missing ``zone_col`` raises ``KeyError``; NaN zones form their own group.
    """
    if zone_col not in expanded.columns:
        raise KeyError(f"reuse_by_zone: zone column {zone_col!r} is absent from the expanded output")
    rows = []
    for zone, group in expanded.groupby(zone_col, dropna=False, sort=True):
        ids = group[[household_id_col]]
        rows.append({"zone": zone, "n_synthetic_households": len(group),
                     "n_distinct_used": int(group[household_id_col].nunique()),
                     "kish_n_eff": donor_neff(ids, household_id_col)["n_eff"]})
    return pd.DataFrame(rows, columns=["zone", "n_synthetic_households", "n_distinct_used",
                                       "kish_n_eff"])
