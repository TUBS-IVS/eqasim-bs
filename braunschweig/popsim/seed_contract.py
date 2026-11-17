"""Contract check before PopulationSim: seed records and household targets fit their groups.

Two checks, both over the control groups of ``braunschweig.popsim.control_groups`` (the
same table the WP1 batch diagnostics use after the run), so the stage fails before the
expensive solver run instead of reporting the defect afterwards (issue #647, goal G6):

* :func:`check_seed_coverage` -- every seed record in the universe of an EXHAUSTIVE group
  is counted by exactly one category of that group.  A record counted by none (a MiD code
  such as sex 3 or haustyp 95 that no control expression names) is silently dropped by
  PopulationSim; a record counted twice is double-weighted.
* :func:`check_household_group_totals` -- the household-level group targets of the cells
  add up to the household total, per Kreis and over all cells, within a ratio tolerance.
  Per-cell equality is not demanded: the per-column rounding of the 1 km integerisation
  can leave a small gap by design.

``mode="error"`` raises one ``ValueError`` naming every failing group; ``mode="warn"``
logs one WARNING per failing group and returns the table.  A passing check logs one INFO
summary line.  Kept to numpy / pandas imports so no other stage's hash closure grows.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.control_groups import (
    CONTROL_GROUPS, UNIVERSE_RATIO_TOLERANCE, group_targets, seed_group_hits)

logger = logging.getLogger(__name__)

_TAG = "[popsim.seed_contract]"
MODES = ("error", "warn")
COVERAGE_COLUMNS = ["group", "n_universe", "n_uncovered", "n_multi_hit"]
TOTALS_COLUMNS = ["group", "kreis", "target_sum", "household_total", "ratio"]
ALL_CELLS = "ALL"
_GRID_GEOGRAPHIES = ("ZENSUS100m", "ZENSUS1km")
_MAX_IDS = 5
_REMEDY = ("Remedy: switch on the seed fills (braunschweig.population.popsim."
           "seed_resolve_binary_sex / seed_resolve_tenure_building_type) and the household "
           "rescale (braunschweig.population.popsim.building_type_targets_to_households), or "
           "fix the seed or control source; braunschweig.population.popsim.seed_contract_mode "
           "'warn' only downgrades this to a WARNING")


def validate_mode(mode: str) -> str:
    """Return ``mode`` if it is a known contract mode, else raise ``ValueError``."""
    if mode not in MODES:
        raise ValueError(f"{_TAG} seed_contract_mode must be one of {MODES}, got {mode!r}")
    return mode


def _report(failures: list, mode: str) -> None:
    """Raise one error with all failures (mode error) or log one WARNING each (mode warn)."""
    if not failures:
        return
    if mode == "error":
        raise ValueError(f"{_TAG} contract violated in {len(failures)} group(s): "
                         + " | ".join(failures) + ". " + _REMEDY)
    for message in failures:
        logger.warning("%s %s. %s", _TAG, message, _REMEDY)


def _record_ids(frame: pd.DataFrame, table: str, mask: np.ndarray) -> list:
    """Up to five ids of the flagged records: the id column of the table, else the index."""
    candidates = ("P_ID", "H_ID") if table == "persons" else ("H_ID",)
    for column in candidates:
        if column in frame.columns:
            return frame.loc[mask, column].head(_MAX_IDS).tolist()
    return frame.index[mask][:_MAX_IDS].tolist()


def check_seed_coverage(seed_households: pd.DataFrame, seed_persons: pd.DataFrame,
                        control_spec: pd.DataFrame, total_hh_control: str, *,
                        mode: str = "error", groups=CONTROL_GROUPS) -> pd.DataFrame:
    """Count universe records matched by no / several categories of each control group.

    Columns: ``group, n_universe, n_uncovered, n_multi_hit`` (groups without a control in
    ``control_spec`` are skipped and counted in one INFO line).  For an EXHAUSTIVE group
    with ``n_uncovered + n_multi_hit > 0`` the check raises (``mode="error"``) or logs a
    WARNING (``mode="warn"``); the message names the group, both counts and up to five
    record ids.  Non-exhaustive groups are reported only.
    """
    validate_mode(mode)
    frames = {"households": seed_households, "persons": seed_persons}
    rows, failures, skipped = [], [], []
    for group in groups:
        if not group_targets(group, control_spec, total_hh_control):
            skipped.append(group.name)
            continue
        frame = frames[group.seed_table]
        hits, in_universe = seed_group_hits(group, control_spec, frame, total_hh_control)
        uncovered = in_universe & (hits == 0)
        multi = in_universe & (hits >= 2)
        rows.append({"group": group.name, "n_universe": int(in_universe.sum()),
                     "n_uncovered": int(uncovered.sum()), "n_multi_hit": int(multi.sum())})
        if group.exhaustive and (uncovered.any() or multi.any()):
            failures.append(
                f"group {group.name} ({group.seed_table}): {int(uncovered.sum())} of "
                f"{int(in_universe.sum())} universe record(s) match no category, "
                f"{int(multi.sum())} match several; first uncovered ids "
                f"{_record_ids(frame, group.seed_table, uncovered)}, first multi-hit ids "
                f"{_record_ids(frame, group.seed_table, multi)}")
    table = pd.DataFrame(rows, columns=COVERAGE_COLUMNS)
    logger.info("%s coverage: %d group(s) checked, %d without a control (%s), %d failing "
                "(mode %s)", _TAG, len(rows), len(skipped), ", ".join(skipped) or "-",
                len(failures), mode)
    _report(failures, mode)
    return table


def _base_column(controls_df: pd.DataFrame, target: str) -> str:
    """Cells column that a control target counts: ``control_field`` without its geography."""
    row = controls_df.loc[controls_df["target"] == target].iloc[0]
    suffix = f"_{row['geography']}"
    field = row["control_field"]
    return field[: -len(suffix)] if field.endswith(suffix) else field


def _household_total_groups(groups) -> list:
    """The groups the household-total check covers; one owner for the selection rule."""
    return [group for group in groups
            if group.seed_table == "households" and group.exhaustive
            and group.universe is None]


def household_group_columns(controls_df: pd.DataFrame, total_hh_control: str,
                            groups=CONTROL_GROUPS) -> list:
    """Cells columns :func:`check_household_group_totals` reads (total first, no duplicates).

    Lets the caller hand over a narrow frame instead of copying every cells column.
    """
    columns = [_base_column(controls_df, total_hh_control)]
    geography = dict(zip(controls_df["target"], controls_df["geography"]))
    for group in _household_total_groups(groups):
        for target in group_targets(group, controls_df, total_hh_control):
            if geography[target] in _GRID_GEOGRAPHIES:
                columns.append(_base_column(controls_df, target))
    return list(dict.fromkeys(columns))


def check_household_group_totals(cells: pd.DataFrame, controls_df: pd.DataFrame,
                                 total_hh_control: str, *, kreis_col: str,
                                 tolerance: float = UNIVERSE_RATIO_TOLERANCE,
                                 mode: str = "error", groups=CONTROL_GROUPS) -> pd.DataFrame:
    """Household-level group target sums versus the household total, per Kreis and overall.

    Covers household groups that are exhaustive, have no universe filter and whose targets
    all name a grid-geography column of ``cells``.  Other groups (KREIS-level controls,
    which are partitioned to the household total elsewhere and reported by the WP1 tool
    after the run, and groups whose column is absent from ``cells``) are skipped and
    counted in one INFO line.  ``ratio = target_sum / household_total``; a Kreis (or the
    overall row, ``kreis == "ALL"``) with ``|ratio - 1| > tolerance`` raises or warns.
    """
    validate_mode(mode)
    if kreis_col not in cells.columns:
        raise ValueError(f"{_TAG} cells frame has no Kreis column {kreis_col!r}")
    total_column = _base_column(controls_df, total_hh_control)
    if total_column not in cells.columns:
        raise ValueError(f"{_TAG} household-total column {total_column!r} is not in the cells")
    # Cells without a resolved Kreis (NaN key) are left out of the per-Kreis groups, as the
    # caller logs, and stay in the overall (ALL) row. astype(str) would turn NaN into the
    # pseudo-Kreis "nan" and check it against the tolerance.
    resolved = cells[kreis_col].notna().to_numpy()
    kreis = cells[kreis_col].astype(str).to_numpy()
    total = pd.to_numeric(cells[total_column], errors="coerce").fillna(0.0).to_numpy(float)
    rows, failures, skipped, checked = [], [], [], 0
    for group in _household_total_groups(groups):
        targets = group_targets(group, controls_df, total_hh_control)
        if not targets:
            continue
        checked += 1
        geography = dict(zip(controls_df["target"], controls_df["geography"]))
        columns = list(dict.fromkeys(_base_column(controls_df, t) for t in targets))
        if any(geography[t] not in _GRID_GEOGRAPHIES for t in targets) \
                or any(c not in cells.columns for c in columns):
            skipped.append(group.name)
            continue
        values = cells[columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        per_cell = values.to_numpy(float).sum(axis=1)
        frame = pd.DataFrame({"kreis": kreis, "target_sum": per_cell, "household_total": total})
        sums = frame[resolved].groupby("kreis", sort=True)[["target_sum", "household_total"]].sum()
        sums.loc[ALL_CELLS] = [per_cell.sum(), total.sum()]
        for label, item in sums.iterrows():
            ratio = item["target_sum"] / item["household_total"] \
                if item["household_total"] else np.nan
            rows.append({"group": group.name, "kreis": label,
                         "target_sum": float(item["target_sum"]),
                         "household_total": float(item["household_total"]),
                         "ratio": float(ratio)})
            bad = (item["target_sum"] != 0) if np.isnan(ratio) else abs(ratio - 1.0) > tolerance
            if bad:
                failures.append(
                    f"group {group.name}, Kreis {label}: target sum {item['target_sum']:.1f} "
                    f"vs household total {item['household_total']:.1f} "
                    f"(ratio {ratio:.4f}, tolerance {tolerance})")
    table = pd.DataFrame(rows, columns=TOTALS_COLUMNS)
    logger.info("%s household totals: %d group(s) checked, %d skipped (%s), %d failing "
                "Kreis/group pair(s) (mode %s)", _TAG, checked - len(skipped), len(skipped),
                ", ".join(skipped) or "-", len(failures), mode)
    _report(failures, mode)
    return table
