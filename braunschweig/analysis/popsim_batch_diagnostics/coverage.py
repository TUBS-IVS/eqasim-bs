"""Seed-record coverage of the PopulationSim control groups and universe checks.

For every control group (``braunschweig.popsim.control_groups``) this reports the seed
records that no control category counts, and whether the household-level group targets
add up to the household total.  Read-only; works on a loaded ``BatchFolder``.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.analysis.integerizer_quality.cell_error import evaluate_expression
from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (
    BatchFolder, control_totals)
from braunschweig.popsim.control_groups import CONTROL_GROUPS, group_targets

logger = logging.getLogger(__name__)

COVERAGE_COLUMNS = ["group", "seed_table", "exhaustive", "n_categories", "n_universe",
                    "n_uncovered", "n_multi_hit", "seed_weight_share", "synthetic_share",
                    "ratio"]
UNIVERSE_COLUMNS = ["group", "geography", "target_sum", "household_total", "ratio"]


def _record_weights(batch: BatchFolder, table: str, usage: pd.Series):
    """Seed weight and synthetic copies per record of ``table`` (persons inherit)."""
    id_col = batch.household_id_col
    weight = batch.seed_households.set_index(id_col)[batch.weight_col]
    copies = usage.reindex(weight.index).fillna(0)
    if table == "households":
        return weight.to_numpy(float), copies.to_numpy(float)
    ids = batch.seed_persons[id_col]
    return weight.reindex(ids).to_numpy(float), copies.reindex(ids).to_numpy(float)


def coverage_table(batch: BatchFolder, usage: pd.Series, groups=CONTROL_GROUPS) -> pd.DataFrame:
    """Per control group: universe records matched by no / several categories.

    ``usage`` is indexed by seed household id (copies); absent households have 0.
    ``seed_weight_share`` and ``synthetic_share`` are the shares of the uncovered
    records within the universe (weighted by the household weight / copies);
    ``ratio`` is their quotient (NaN when the seed share is 0).  Groups without a
    control in the batch are skipped and counted in one log line.
    """
    frames = {"households": batch.seed_households, "persons": batch.seed_persons}
    spec = batch.control_spec
    expressions = dict(zip(spec["target"], spec["expression"]))
    rows, skipped = [], []
    for group in groups:
        targets = group_targets(group, spec, batch.total_hh_control)
        if not targets:
            skipped.append(group.name)
            continue
        frame = frames[group.seed_table]
        hits = np.zeros(len(frame), dtype=int)
        for target in targets:
            hits += np.asarray(evaluate_expression(expressions[target], frame)).astype(bool)
        if group.universe is None:
            in_universe = np.ones(len(frame), dtype=bool)
        else:
            in_universe = np.asarray(evaluate_expression(group.universe, frame)).astype(bool)
        weight, copies = _record_weights(batch, group.seed_table, usage)
        uncovered = in_universe & (hits == 0)
        weight_total, copies_total = weight[in_universe].sum(), copies[in_universe].sum()
        seed_share = weight[uncovered].sum() / weight_total if weight_total else np.nan
        synthetic_share = copies[uncovered].sum() / copies_total if copies_total else np.nan
        if np.isnan(seed_share) or seed_share == 0:
            ratio = np.nan
        else:
            ratio = synthetic_share / seed_share
        rows.append({
            "group": group.name, "seed_table": group.seed_table,
            "exhaustive": group.exhaustive, "n_categories": len(targets),
            "n_universe": int(in_universe.sum()), "n_uncovered": int(uncovered.sum()),
            "n_multi_hit": int((in_universe & (hits >= 2)).sum()),
            "seed_weight_share": seed_share, "synthetic_share": synthetic_share,
            "ratio": ratio})
    logger.info("[popsim_batch_diagnostics] coverage: %d of %d groups have no control in "
                "%s%s", len(skipped), len(groups), batch.name,
                f": {', '.join(skipped)}" if skipped else "")
    return pd.DataFrame(rows, columns=COVERAGE_COLUMNS)


def universe_table(batch: BatchFolder, groups=CONTROL_GROUPS) -> pd.DataFrame:
    """Household group targets versus the household total, per geography.

    Covers household-table groups that are exhaustive and have no universe filter;
    ``household_total`` is the sum of the ``total_hh_control`` targets over the
    batch's 100 m cells.
    """
    spec = batch.control_spec
    total_row = spec[spec["target"] == batch.total_hh_control]
    if total_row.empty:
        raise ValueError(f"batch {batch.name}: household-total control "
                         f"{batch.total_hh_control!r} is not in the control specification")
    total_geography = total_row["geography"].iloc[0]
    household_total = float(control_totals(batch, total_geography)[
        batch.total_hh_control].sum())
    geography_of = dict(zip(spec["target"], spec["geography"]))
    rows = []
    for group in groups:
        if group.seed_table != "households" or not group.exhaustive \
                or group.universe is not None:
            continue
        targets = group_targets(group, spec, batch.total_hh_control)
        for geography in batch.geographies:
            in_geography = [t for t in targets if geography_of[t] == geography]
            if not in_geography:
                continue
            target_sum = float(control_totals(batch, geography)[in_geography].to_numpy().sum())
            ratio = target_sum / household_total if household_total else np.nan
            rows.append({"group": group.name, "geography": geography,
                         "target_sum": target_sum, "household_total": household_total,
                         "ratio": ratio})
    return pd.DataFrame(rows, columns=UNIVERSE_COLUMNS)
