"""Control fit of one PopulationSim batch and the split of its misfit into balancing and rounding.

Two views of the same question "how far is the synthetic population from the control totals":

* ``control_fit`` counts the synthetic households of the expanded output per zone and compares
  them with the control totals, per control (the final, integer result).
* ``misfit_split`` reads the checkpoint store and separates the misfit into the part that the
  balancing (float weights vs targets) leaves and the part that the integerizing adds
  (integer vs float weights).  ``rounding_shift`` applies design goal G4 to the latter.

All error measures are sums over zones of absolute errors divided by the control's target sum
(WAPE, weighted absolute percentage error); they are not computed on the totals, because
errors of opposite sign in different zones must not cancel.  A control with target sum zero has
no defined ratio and gets NaN.

Weights of the store join the store's own incidence table by household group id (``H_ID``);
they never join the seed households of the batch folder, whose ids are different when
PopulationSim groups households by incidence signature.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (
    BatchFolder, control_totals, household_incidence)

logger = logging.getLogger(__name__)

_TAG = "[popsim_batch_diagnostics]"
_STORE = Path("output") / "pipeline.h5"
_CHECKPOINTS_KEY = "/checkpoints"
_WEIGHT_COLUMNS = ("balanced_weight", "integer_weight")
DEFAULT_LEVELS = ("STAAT", "KREIS", "ZENSUS1km", "ZENSUS100m")
_SPLIT_COLUMNS = ["level", "control", "control_geography", "target_sum", "float_sum",
                  "integer_sum", "balance_wape", "round_wape", "total_wape",
                  "bias_float", "bias_int"]


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Element-wise ``numerator / denominator``, NaN where the denominator is zero."""
    safe = np.where(denominator == 0, np.nan, denominator)
    return numerator / safe


def _check_overlap(label: str, n_matched: int, n_total: int, what: str) -> None:
    """Refuse a join in which nothing matched: almost surely a key or dtype mismatch."""
    if n_total > 0 and n_matched == 0:
        raise ValueError(f"{label}: none of the {n_total} {what} matches; the zone ids of the "
                         "two tables differ in value or dtype")


def control_fit(batch: BatchFolder) -> pd.DataFrame:
    """Fit of the expanded synthetic population to the control totals, per control.

    Synthetic totals come from the expanded household ids and the household incidence, per
    zone of the control's geography.  ``abs_error`` is the sum over zones of
    ``|synthetic - target|``; ``wape = abs_error / target_sum`` and
    ``bias = synthetic_sum / target_sum - 1`` (both NaN for ``target_sum == 0``).  A zone that
    has synthetic households but no target row counts with target 0; a zone with targets and
    no synthetic household counts with synthetic 0; both are logged.
    A geography without a ``control_totals_<geography>.csv`` raises ``FileNotFoundError``.
    """
    spec = batch.control_spec
    incidence = household_incidence(batch)
    expanded = batch.expanded
    id_col = batch.household_id_col
    if expanded[id_col].isna().any():
        raise ValueError(f"batch {batch.name}: {int(expanded[id_col].isna().sum())} expanded "
                         f"row(s) have a missing {id_col}")
    household_pos = incidence.index.get_indexer(expanded[id_col])
    if (household_pos < 0).any():
        unknown = expanded[id_col][household_pos < 0].unique()[:10].tolist()
        raise ValueError(f"batch {batch.name}: expanded household id(s) {unknown} are not in "
                         "the seed households")

    parts = []
    for geography in spec["geography"].unique():
        label = f"batch {batch.name} geography {geography}"
        if geography not in expanded.columns:
            raise ValueError(f"{label}: the expanded output has no column '{geography}'")
        if expanded[geography].isna().any():
            raise ValueError(f"{label}: the expanded output has missing '{geography}' values")
        controls = spec[spec["geography"] == geography]
        targets = control_totals(batch, geography)[controls["target"].tolist()]
        if targets.isna().any().any():
            raise ValueError(f"{label}: control totals contain missing values")

        zones = targets.index.union(pd.Index(expanded[geography].unique()))
        _check_overlap(label, int(expanded[geography].isin(targets.index).sum()),
                       len(expanded), "expanded households")
        zone_pos = zones.get_indexer(expanded[geography])
        counts = sparse.csr_matrix(
            (np.ones(len(expanded)), (zone_pos, household_pos)),
            shape=(len(zones), len(incidence)))
        synthetic = counts @ incidence[controls["target"]].to_numpy(dtype=float)
        target = targets.reindex(zones).fillna(0.0).to_numpy(dtype=float)

        zones_without_target = int((~zones.isin(targets.index)).sum())
        zones_without_rows = int((~zones.isin(pd.Index(expanded[geography].unique()))).sum())
        logger.info("%s control_fit %s: %d zones, %d without targets, %d without synthetic "
                    "households", _TAG, label, len(zones), zones_without_target,
                    zones_without_rows)
        if zones_without_target:
            logger.warning("%s control_fit %s: %d zone(s) have synthetic households but no "
                           "control totals (counted with target 0)", _TAG, label,
                           zones_without_target)

        target_sum = target.sum(axis=0)
        synthetic_sum = synthetic.sum(axis=0)
        abs_error = np.abs(synthetic - target).sum(axis=0)
        parts.append(pd.DataFrame({
            "control": controls["target"].to_numpy(), "geography": geography,
            "importance": controls["importance"].to_numpy(),
            "target_sum": target_sum, "synthetic_sum": synthetic_sum, "abs_error": abs_error,
            "bias": _ratio(synthetic_sum, target_sum) - 1.0,
            "wape": _ratio(abs_error, target_sum)}))
    fit = pd.concat(parts, ignore_index=True)
    # Keep the control order of controls.csv.
    order = {target: i for i, target in enumerate(spec["target"])}
    return fit.sort_values("control", key=lambda s: s.map(order), kind="stable",
                           ignore_index=True)


def _table_keys(store: pd.HDFStore, table: str) -> list:
    """Keys ``/<table>/<checkpoint>`` of the store (``<table>_sparse`` is a different table)."""
    prefix = f"/{table}/"
    return sorted(key for key in store.keys() if key.startswith(prefix))


def _current_key(store: pd.HDFStore, table: str) -> str:
    """Key of the version of ``table`` that was written last.

    PopulationSim keeps one copy of a table per checkpoint.  ``/checkpoints`` holds one row
    per checkpoint and one column per table; the value of column ``table`` in the LAST row
    names the checkpoint under which the current version lives.  Without ``/checkpoints`` (or
    a value for the table) exactly one version is accepted; several versions are refused
    instead of guessed.
    """
    keys = _table_keys(store, table)
    if not keys:
        raise ValueError(f"the store has no table '{table}'")
    if _CHECKPOINTS_KEY in store:
        checkpoints = store.get(_CHECKPOINTS_KEY)
        if table in checkpoints.columns and len(checkpoints):
            named = checkpoints[table].iloc[-1]
            if isinstance(named, str) and named:
                key = f"/{table}/{named}"
                if key not in keys:
                    raise ValueError(f"'{_CHECKPOINTS_KEY}' names checkpoint '{named}' as the "
                                     f"last version of '{table}', but the store has only {keys}")
                return key
    if len(keys) == 1:
        return keys[0]
    raise ValueError(f"the store has {len(keys)} versions of '{table}' ({keys}) and "
                     f"'{_CHECKPOINTS_KEY}' does not say which one is the last")


def _read_weights(store: pd.HDFStore, level: str, chunk_rows: int) -> tuple:
    """Current ``<level>_weights`` of an open store, positive rows only; returns (table, n_rows)."""
    if chunk_rows <= 0:
        raise ValueError(f"chunk_rows must be positive, got {chunk_rows}")
    key = _current_key(store, f"{level}_weights")
    n_rows = int(store.get_storer(key).shape[0])
    wanted = ["H_ID", level, *_WEIGHT_COLUMNS]
    kept = []
    for start in range(0, n_rows, chunk_rows):
        chunk = store.select(key, start=start, stop=min(start + chunk_rows, n_rows))
        # STAAT weights carry H_ID as index name and as column: use the column only.
        chunk = chunk.reset_index(drop=True)
        missing = [c for c in wanted if c not in chunk.columns]
        if missing:
            raise ValueError(f"{key}: lacks column(s) {missing}")
        chunk = chunk[wanted]
        weights = chunk[list(_WEIGHT_COLUMNS)]
        if weights.isna().any().any():
            raise ValueError(f"{key}: missing weight values in rows {start}..{start + len(chunk)}")
        if (weights < 0).any().any():
            raise ValueError(f"{key}: negative weights in rows {start}..{start + len(chunk)}")
        kept.append(chunk[(chunk["balanced_weight"] > 0) | (chunk["integer_weight"] > 0)])
    table = (pd.concat(kept, ignore_index=True) if kept
             else pd.DataFrame(columns=wanted))
    logger.info("%s %s: kept %d of %d rows (%.1f%%) with a positive weight", _TAG, key,
                len(table), n_rows, 100.0 * len(table) / n_rows if n_rows else 0.0)
    return table, n_rows


def read_weight_table(store_path, level: str, *, chunk_rows: int = 5_000_000) -> pd.DataFrame:
    """Weights of one geography level from the last checkpoint of ``/<level>_weights``.

    Columns ``H_ID, <level>, balanced_weight, integer_weight`` with a RangeIndex.  The table is
    read in row chunks of ``chunk_rows`` and only rows with a positive balanced or integer
    weight are kept (the 100 m table of a full batch has about 25 million rows).
    """
    store_path = Path(store_path)
    if not store_path.is_file():
        raise FileNotFoundError(f"checkpoint store not found: {store_path}")
    with pd.HDFStore(str(store_path), mode="r") as store:
        table, _ = _read_weights(store, level, chunk_rows)
    return table


def _level_split(store: pd.HDFStore, level: str, incidence: pd.DataFrame, geography_of: dict,
                 chunk_rows: int) -> pd.DataFrame:
    """Misfit split of one level (see ``misfit_split``)."""
    controls = store.get(_current_key(store, f"{level}_controls"))
    if not controls.index.is_unique:
        raise ValueError(f"{level}_controls: zone ids are not unique")
    columns = [c for c in controls.columns if c in geography_of and c in incidence.columns]
    ignored = [c for c in controls.columns if c not in columns]
    if ignored:
        logger.warning("%s %s: %d control column(s) are not in control_spec and incidence "
                       "and are left out: %s", _TAG, level, len(ignored), ignored)
    if not columns:
        raise ValueError(f"{level}_controls: no control column is also a column of the "
                         "incidence table")
    target = controls[columns].to_numpy(dtype=float)
    if np.isnan(target).any():
        raise ValueError(f"{level}_controls: control totals contain missing values")

    weights, _ = _read_weights(store, level, chunk_rows)
    household_pos = incidence.index.get_indexer(weights["H_ID"])
    if (household_pos < 0).any():
        unknown = weights["H_ID"][household_pos < 0].unique()[:10].tolist()
        raise ValueError(f"{level}_weights: household id(s) {unknown} are not in the incidence "
                         "table of the store")
    zone_pos = controls.index.get_indexer(weights[level])
    in_controls = zone_pos >= 0
    _check_overlap(f"{level}_weights", int(in_controls.sum()), len(weights), "weight rows")
    if not in_controls.all():
        orphan_zones = weights.loc[~in_controls, level].unique()
        logger.warning("%s %s: %d weight row(s) in %d zone(s) not in %s_controls are left out "
                       "(e.g. %s)", _TAG, level, int((~in_controls).sum()), len(orphan_zones),
                       level, orphan_zones[:3].tolist())

    shape = (len(controls), len(incidence))
    rows, cols = zone_pos[in_controls], household_pos[in_controls]
    # Duplicate (zone, household) entries are summed by the CSR constructor.
    float_matrix = sparse.csr_matrix((weights["balanced_weight"].to_numpy(dtype=float)[in_controls],
                                      (rows, cols)), shape=shape)
    integer_matrix = sparse.csr_matrix((weights["integer_weight"].to_numpy(dtype=float)[in_controls],
                                        (rows, cols)), shape=shape)
    incidence_matrix = incidence[columns].to_numpy(dtype=float)
    float_total = float_matrix @ incidence_matrix
    integer_total = integer_matrix @ incidence_matrix

    has_rows = np.zeros(len(controls), dtype=bool)
    has_rows[rows] = True
    without_rows = ~has_rows
    with_targets = np.abs(target).sum(axis=1) > 0
    logger.info("%s %s: %d zones, %d without weight rows (%d of them with targets; their float "
                "and integer sums are 0)", _TAG, level, len(controls), int(without_rows.sum()),
                int((without_rows & with_targets).sum()))

    target_sum = target.sum(axis=0)
    float_sum = float_total.sum(axis=0)
    integer_sum = integer_total.sum(axis=0)
    return pd.DataFrame({
        "level": level, "control": columns,
        "control_geography": [geography_of[c] for c in columns],
        "target_sum": target_sum, "float_sum": float_sum, "integer_sum": integer_sum,
        "balance_wape": _ratio(np.abs(float_total - target).sum(axis=0), target_sum),
        "round_wape": _ratio(np.abs(integer_total - float_total).sum(axis=0), target_sum),
        "total_wape": _ratio(np.abs(integer_total - target).sum(axis=0), target_sum),
        "bias_float": _ratio(float_sum, target_sum) - 1.0,
        "bias_int": _ratio(integer_sum, target_sum) - 1.0})[_SPLIT_COLUMNS]


def misfit_split(batch: BatchFolder, levels=DEFAULT_LEVELS, *,
                 chunk_rows: int = 5_000_000) -> pd.DataFrame:
    """Split the control misfit of a batch into the balancing and the rounding part.

    For each level present in the checkpoint store, per control column of ``/<level>_controls``
    (the level's own targets and those of all lower levels aggregated up):
    ``balance_wape = sum_z |float - target| / target_sum`` (float weights vs targets),
    ``round_wape = sum_z |integer - float| / target_sum`` (what the integerizing adds) and
    ``total_wape = sum_z |integer - target| / target_sum``; ``bias_float`` and ``bias_int`` are
    ``sum / target_sum - 1``.  Incidence, targets and weights all come from the store (last
    checkpoint of each table); weights join the store's incidence by household group id.
    A level missing from the store is skipped and logged; a batch without a store raises
    ``FileNotFoundError``.
    """
    store_path = batch.path / _STORE
    if not store_path.is_file():
        raise FileNotFoundError(f"batch {batch.name}: checkpoint store not found: {store_path}")
    parts, skipped = [], []
    with pd.HDFStore(str(store_path), mode="r") as store:
        incidence = store.get(_current_key(store, "incidence_table"))
        if not incidence.index.is_unique:
            raise ValueError(f"batch {batch.name}: incidence table household ids are not unique")
        spec = store.get(_current_key(store, "control_spec"))
        geography_of = dict(zip(spec["target"], spec["geography"]))
        for level in levels:
            if not _table_keys(store, f"{level}_weights") or \
                    not _table_keys(store, f"{level}_controls"):
                skipped.append(level)
                continue
            parts.append(_level_split(store, level, incidence, geography_of, chunk_rows))
    logger.info("%s batch %s misfit_split: %d of %d level(s) computed, %d skipped (not in the "
                "store): %s", _TAG, batch.name, len(parts), len(levels), len(skipped), skipped)
    if not parts:
        raise ValueError(f"batch {batch.name}: none of the levels {list(levels)} is in the "
                         f"checkpoint store {store_path}")
    return pd.concat(parts, ignore_index=True)


def rounding_shift(split: pd.DataFrame, total_hh_control: str, *, level: str = "ZENSUS100m",
                   min_target: float = 40.0, bound: float = 0.10) -> pd.DataFrame:
    """Rounding shift per control at ``level`` against the bound of design goal G4.

    Rows of ``split`` at ``level`` whose ``control_geography`` is that level, without the
    household-total control and with ``target_sum >= min_target``.
    ``shift = |integer_sum - float_sum| / target_sum`` and ``within_bound = shift <= bound``.
    """
    rows = split[(split["level"] == level) & (split["control_geography"] == level)
                 & (split["control"] != total_hh_control)
                 & (split["target_sum"] >= min_target)]
    shift = (rows["integer_sum"] - rows["float_sum"]).abs() / rows["target_sum"]
    logger.info("%s rounding_shift %s: %d control(s) with target_sum >= %g, %d beyond the "
                "bound %g", _TAG, level, len(rows), min_target, int((shift > bound).sum()), bound)
    return pd.DataFrame({"control": rows["control"].to_numpy(),
                         "target_sum": rows["target_sum"].to_numpy(),
                         "shift": shift.to_numpy(),
                         "within_bound": (shift <= bound).to_numpy()})
