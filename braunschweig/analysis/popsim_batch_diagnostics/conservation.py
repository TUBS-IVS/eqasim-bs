"""Household weight conservation from one PopulationSim level to the next.

PopulationSim integerizes the weights of a sub level so that, per household group, they should add
up to the integer weight that the parent level gave the same household group in the parent zone.
``household_conservation`` measures how far that holds, per consecutive pair of levels: a
household group placed in two sub zones of a parent with weight 1 (sum 2) or dropped from all of
them (sum 0) both count as a deviation of its own size.

Weights come from the private helpers of ``fit`` (``_read_weights`` reads one level's weight
table from the open store in chunks and keeps the positive rows; ``_table_keys`` and
``_current_key`` find the current checkpoint of a table).  The map from a sub zone to its parent
zone is the store's own ``geo_cross_walk`` table when the store holds one (the primary source);
otherwise the batch folder's ``data/geo_cross_walk.csv`` (the file PopulationSim loads it from;
the fallback).  The log says which source each batch used, ``household_conservation`` records it
in ``.attrs["crosswalk_source"]`` of its result and ``log_crosswalk_sources`` reports the rate
over all batches.  Zone ids are compared as text, so an integer id in one table and a text id in the other
(a CSV round trip) still match.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics import fit
from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import BatchFolder

logger = logging.getLogger(__name__)

_TAG = "[popsim_batch_diagnostics]"
_CROSSWALK_TABLE = "geo_cross_walk"
_CROSSWALK_CSV = "geo_cross_walk.csv"
SOURCE_STORE = "store"
SOURCE_CSV = "csv"
CONSERVATION_COLUMNS = [
    "level", "parent_level", "n_pairs", "parent_weight_sum", "sub_weight_sum",
    "abs_deviation_sum", "abs_deviation_share", "conserved_pair_share",
    "distinct_rows_parent", "distinct_rows_sub"]


def _crosswalk(store: pd.HDFStore, batch: BatchFolder) -> tuple:
    """The batch's geography crosswalk, its source kind and a label of the source.

    The kind is ``SOURCE_STORE`` (the store table, primary) or ``SOURCE_CSV`` (the batch's
    ``data/geo_cross_walk.csv``, fallback); the label names the source in messages.
    """
    if fit._table_keys(store, _CROSSWALK_TABLE):
        return (store.get(fit._current_key(store, _CROSSWALK_TABLE)), SOURCE_STORE,
                "store table")
    path = batch.path / "data" / _CROSSWALK_CSV
    if not path.is_file():
        raise FileNotFoundError(f"batch {batch.name}: neither the store nor {path} has the "
                                "geography crosswalk")
    return pd.read_csv(path), SOURCE_CSV, str(path)


def log_crosswalk_sources(sources: Sequence[Optional[str]]) -> None:
    """Log how many batches took the crosswalk from the store (primary) or from the CSV (fallback).

    ``sources`` holds one ``crosswalk_source`` attribute of ``household_conservation`` per batch
    (``None``: the batch had no level pair to compute and read no crosswalk; those batches are
    named but not in the rate).  A WARNING when no batch used the store and some used the CSV:
    the primary source never worked.
    """
    used = [source for source in sources if source is not None]
    from_store = sum(source == SOURCE_STORE for source in used)
    from_csv = sum(source == SOURCE_CSV for source in used)
    unused = len(sources) - len(used)
    if not used:
        logger.info("%s crosswalk not needed: no batch had a level pair to compute", _TAG)
        return
    message = (f"{_TAG} crosswalk from the store {from_store}/{len(used)} batches "
               f"({100.0 * from_store / len(used):.1f}%), from data/{_CROSSWALK_CSV} "
               f"{from_csv} ({100.0 * from_csv / len(used):.1f}%)")
    if unused:
        message += f"; no crosswalk needed {unused}"
    (logger.warning if from_store == 0 and from_csv else logger.info)(message)


def _zone_to_parent(crosswalk: pd.DataFrame, level: str, parent: str, label: str) -> pd.Series:
    """Text zone id of ``level`` -> text zone id of ``parent``; every zone has one parent."""
    missing = [c for c in (level, parent) if c not in crosswalk.columns]
    if missing:
        raise ValueError(f"crosswalk ({label}) lacks column(s) {missing}")
    pairs = crosswalk[[level, parent]].astype(str).drop_duplicates()
    if pairs[level].duplicated().any():
        raise ValueError(f"crosswalk ({label}): {int(pairs[level].duplicated().sum())} "
                         f"{level} zone(s) have more than one {parent} parent")
    return pairs.set_index(level)[parent]


def _pair_conservation(store: pd.HDFStore, crosswalk: pd.DataFrame, source: str, parent: str,
                       level: str, chunk_rows: int) -> dict:
    """One output row for the sub level ``level`` below ``parent``."""
    parent_weights, _ = fit._read_weights(store, parent, chunk_rows)
    sub_weights, _ = fit._read_weights(store, level, chunk_rows)
    # Zero integer weights do not take part (a pair needs a positive parent or sub sum).
    parent_weights = parent_weights[parent_weights["integer_weight"] > 0]
    sub_weights = sub_weights[sub_weights["integer_weight"] > 0].copy()

    parent_of = _zone_to_parent(crosswalk, level, parent, source)
    mapped = sub_weights[level].astype(str).map(parent_of)
    unmapped = mapped.isna()
    if unmapped.any():
        raise ValueError(f"{level}: {int(sub_weights.loc[unmapped, level].nunique())} zone(s) "
                         f"with a positive integer weight have no {parent} parent in the "
                         f"crosswalk ({source}), e.g. "
                         f"{sub_weights.loc[unmapped, level].unique()[:3].tolist()}")
    sub_weights[parent] = mapped
    sub_sum = sub_weights.groupby(["H_ID", parent])["integer_weight"].sum()
    parent_weights = parent_weights.assign(**{parent: parent_weights[parent].astype(str)})
    parent_sum = parent_weights.groupby(["H_ID", parent])["integer_weight"].sum()

    pairs = pd.concat([parent_sum.rename("parent"), sub_sum.rename("sub")], axis=1).fillna(0.0)
    deviation = (pairs["sub"] - pairs["parent"]).abs()
    n_pairs = len(pairs)
    parent_total = float(pairs["parent"].sum())
    row = {
        "level": level, "parent_level": parent, "n_pairs": n_pairs,
        "parent_weight_sum": parent_total, "sub_weight_sum": float(pairs["sub"].sum()),
        "abs_deviation_sum": float(deviation.sum()),
        "abs_deviation_share": float(deviation.sum()) / parent_total if parent_total else np.nan,
        "conserved_pair_share": float((deviation == 0).sum()) / n_pairs if n_pairs else np.nan,
        "distinct_rows_parent": int(parent_weights["H_ID"].nunique()),
        "distinct_rows_sub": int(sub_weights["H_ID"].nunique())}
    logger.info("%s %s below %s: conserved_pair_share %.4f (%d pairs), abs_deviation_share %.4f",
                _TAG, level, parent, row["conserved_pair_share"], n_pairs,
                row["abs_deviation_share"])
    return row


def household_conservation(batch: BatchFolder, levels=fit.DEFAULT_LEVELS, *,
                           chunk_rows: int = 5_000_000) -> pd.DataFrame:
    """Per sub level: how well its integer weights add up to the parent level's.

    For every consecutive pair ``(parent, sub)`` of ``levels`` whose weight tables are both in
    the batch's checkpoint store, the sub ``integer_weight`` is summed per household group
    (``H_ID``) and parent zone and compared with the parent's ``integer_weight`` of the same
    group and zone.  One row per sub level: ``n_pairs`` (pairs with a positive parent or summed
    sub weight), ``parent_weight_sum``, ``sub_weight_sum``, ``abs_deviation_sum`` (sum over pairs
    of ``|sub sum - parent|``), ``abs_deviation_share`` (that over ``parent_weight_sum``; NaN
    for 0), ``conserved_pair_share`` (pairs with equal weights over ``n_pairs``) and the number
    of household groups with a positive integer weight at the parent and at the sub level.
    The zone-to-parent map is described in the module docstring; its source (``"store"`` or
    ``"csv"``, ``None`` when no pair was computed) is in ``result.attrs["crosswalk_source"]``.
    A pair with a table missing
    from the store is skipped and logged; a batch without a store raises ``FileNotFoundError``;
    a sub zone with a positive weight that the crosswalk cannot map raises ``ValueError``.
    """
    store_path = batch.path / fit._STORE
    if not store_path.is_file():
        raise FileNotFoundError(f"batch {batch.name}: checkpoint store not found: {store_path}")
    rows, skipped = [], []
    with pd.HDFStore(str(store_path), mode="r") as store:
        crosswalk, kind, source = None, None, None
        for parent, level in zip(levels[:-1], levels[1:]):
            if not fit._table_keys(store, f"{parent}_weights") or \
                    not fit._table_keys(store, f"{level}_weights"):
                skipped.append(f"{parent}>{level}")
                continue
            if crosswalk is None:
                crosswalk, kind, source = _crosswalk(store, batch)
                logger.info("%s batch %s household_conservation: crosswalk from %s", _TAG,
                            batch.name, source)
            rows.append(_pair_conservation(store, crosswalk, source, parent, level, chunk_rows))
    logger.info("%s batch %s household_conservation: %d level pair(s) computed, %d skipped "
                "(a weight table is not in the store): %s", _TAG, batch.name, len(rows),
                len(skipped), skipped)
    table = pd.DataFrame(rows, columns=CONSERVATION_COLUMNS)
    # An empty table must keep numeric dtypes so that sums over batches still work.
    table = table.astype({c: float for c in CONSERVATION_COLUMNS[2:]}
                         | {"n_pairs": "int64", "distinct_rows_parent": "int64",
                            "distinct_rows_sub": "int64"})
    # ``None``: no level pair was computed, so no crosswalk was read.
    table.attrs["crosswalk_source"] = kind
    return table
