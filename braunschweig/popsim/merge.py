"""Merge PopulationSim batch outputs back into one population.

Each batch writes ``output/final_expanded_household_ids.csv`` with at least a
``ZENSUS100m`` cell column and a household id ``H_ID``. Because the batching is
cell-disjoint (every 100 m cell belongs to exactly one batch), household ids stay
globally unique as the PAIR ``(ZENSUS100m, H_ID)`` and must NOT be renumbered
across batches. This module enforces that invariant: it fails fast on any
cross-batch duplicate cell (a partitioning bug -- hard corruption) and surfaces
missing batch outputs (recoverable) rather than silently skipping them.

Clean port of the merge half of popsimprep's ``batch_run_popsim.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_NAME = "final_expanded_household_ids.csv"


def verify_unique_cells(
    frames: Sequence[pd.DataFrame],
    *,
    cell_col: str = "ZENSUS100m",
) -> None:
    """Assert every 100 m cell appears in at most ONE batch frame.

    A cell may legitimately repeat WITHIN one batch (one row per household in the
    cell); only CROSS-batch duplication is an error, because it means a 100 m cell
    was assigned to more than one batch and the ``(cell, H_ID)`` uniqueness
    invariant is broken.

    Raises
    ------
    ValueError
        Naming the offending cell id(s) so the partitioning bug is traceable.
    """
    seen_in: dict[str, int] = {}
    offenders: set[str] = set()
    for frame in frames:
        if cell_col not in frame.columns:
            raise ValueError(f"Batch frame is missing the cell column {cell_col!r}.")
        for cell in frame[cell_col].unique():
            seen_in[cell] = seen_in.get(cell, 0) + 1
            if seen_in[cell] > 1:
                offenders.add(cell)
    if offenders:
        raise ValueError(
            "Cross-batch duplicate 100 m cells (each cell must belong to exactly "
            f"one batch): {sorted(offenders)}."
        )


def merge_results(
    frames: Sequence[pd.DataFrame],
    *,
    cell_col: str = "ZENSUS100m",
    source_label_col: str = "source_batch",
    labels: Optional[Sequence[object]] = None,
) -> pd.DataFrame:
    """Concatenate cell-disjoint batch frames, tagging each with its source.

    Verifies cell-disjointness first (fail-fast), then tags each frame with a
    ``source_batch`` label (the provided label or the frame's index) and
    concatenates. Household ids are never renumbered -- the same ``H_ID`` may
    legitimately recur across batches because uniqueness is the pair
    ``(cell_col, H_ID)``.

    Raises
    ------
    ValueError
        If any cell appears in more than one frame.
    """
    verify_unique_cells(frames, cell_col=cell_col)

    if labels is None:
        labels = list(range(len(frames)))
    if len(labels) != len(frames):
        raise ValueError(
            f"labels length {len(labels)} does not match number of frames "
            f"{len(frames)}."
        )

    tagged = []
    for frame, label in zip(frames, labels):
        copy = frame.copy()
        copy[source_label_col] = label
        tagged.append(copy)

    if not tagged:
        return pd.DataFrame()
    return pd.concat(tagged, ignore_index=True)


def load_batch_outputs(
    folders: Sequence[Union[str, Path]],
    *,
    output_name: str = DEFAULT_OUTPUT_NAME,
) -> tuple[list[pd.DataFrame], list[str]]:
    """Read each batch's output table; report folders whose output is missing.

    Returns ``(frames, missing_folders)``. Missing outputs are surfaced (never
    silently skipped) and the found-vs-missing counts are logged.
    """
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for folder in folders:
        path = Path(folder) / "output" / output_name
        if path.is_file():
            frames.append(pd.read_csv(path))
        else:
            missing.append(str(folder))

    log = logger.warning if missing else logger.info
    log(
        "[popsim.merge] loaded %d/%d batch outputs (%d missing)",
        len(frames), len(folders), len(missing),
    )
    if missing:
        logger.warning("[popsim.merge] missing batch outputs: %s", missing)
    return frames, missing


@dataclass(frozen=True)
class MergeReport:
    """Outcome of merging a set of batch folders."""

    combined: pd.DataFrame
    n_folders: int
    n_loaded: int
    n_missing: int
    missing_folders: list[str]
    n_rows: int
    n_cells: int


def merge_batch_folders(
    folders: Sequence[Union[str, Path]],
    *,
    output_name: str = DEFAULT_OUTPUT_NAME,
    cell_col: str = "ZENSUS100m",
) -> MergeReport:
    """Load, verify cell-disjointness, and merge a set of batch folders.

    Missing batch outputs are reported in ``missing_folders`` but do NOT raise (a
    missing batch is recoverable -- re-run it). A cross-batch duplicate cell DOES
    raise (hard corruption of the disjoint-partition invariant).
    """
    frames, missing = load_batch_outputs(folders, output_name=output_name)
    combined = merge_results(frames, cell_col=cell_col)
    n_cells = int(combined[cell_col].nunique()) if cell_col in combined.columns else 0
    return MergeReport(
        combined=combined,
        n_folders=len(folders),
        n_loaded=len(frames),
        n_missing=len(missing),
        missing_folders=missing,
        n_rows=int(len(combined)),
        n_cells=n_cells,
    )
