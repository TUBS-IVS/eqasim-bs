"""PopulationSim batch engine: split a region into runnable batches.

PopulationSim does not scale to a whole region in one run, so the 100 m target
cells are split into batches of whole 1 km parent cells. A 1 km cell is the
ATOMIC unit -- it (with its <=100 nested 100 m cells) is never split across
batches -- so household ids stay globally unique as ``(ZENSUS100m, H_ID)`` without
any cross-batch renumbering (see ``braunschweig.popsim.merge``).

This module provides:
- ``partition_by_1km`` -- deterministic greedy bin-packing of 1 km cells into
  partitions of at most ``max_cells`` 100 m cells (atomic 1 km cells);
- a content-hash ``SplitManifest`` so a split folder can be recognised as
  up-to-date (or stale) without re-running -- deterministic, no time/randomness;
- a runner abstraction (``run_batches`` + injectable ``run_one``) so the real
  PopulationSim subprocess stays out of the tested path.

Clean port of the popsimprep ``batch_run_popsim.py`` script.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from braunschweig import parallelism
from braunschweig.progress import progress_parallel
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# The output file PopulationSim writes; its presence marks a finished batch.
COMPLETION_MARKER = "output/final_expanded_household_ids.csv"

# Valid batch run statuses.
_VALID_STATUSES = frozenset({"succeeded", "failed", "skipped"})


def count_100m_cells(cell_groups: Mapping[str, Sequence[str]]) -> int:
    """Total number of 100 m cells across all 1 km parents."""
    return sum(len(children) for children in cell_groups.values())


def partition_by_1km(
    cell_groups: Mapping[str, Sequence[str]],
    max_cells: int,
) -> list[list[str]]:
    """Greedy-pack whole 1 km cells into partitions of <= ``max_cells`` 100 m cells.

    Parents are processed in SORTED id order. A new partition is started whenever
    adding the next parent's 100 m children would push the current partition's
    100 m-cell count above ``max_cells``. A single 1 km parent is always kept
    whole (atomic): if one parent alone exceeds ``max_cells`` it occupies its own
    partition and is never split.

    Parameters
    ----------
    cell_groups:
        Mapping ``{1km_id: [100m_ids]}``.
    max_cells:
        Soft upper bound on the number of 100 m cells per partition (a single
        oversize parent may exceed it).

    Returns
    -------
    list[list[str]]
        Partitions, each the sorted list of 1 km ids it contains.

    Raises
    ------
    ValueError
        If ``max_cells`` is not positive.
    """
    if max_cells <= 0:
        raise ValueError(f"max_cells must be positive, got {max_cells}.")

    partitions: list[list[str]] = []
    current: list[str] = []
    current_count = 0

    for parent in sorted(cell_groups):
        size = len(cell_groups[parent])
        if current and current_count + size > max_cells:
            partitions.append(current)
            current = []
            current_count = 0
        current.append(parent)
        current_count += size

    if current:
        partitions.append(current)
    return partitions


def sha1_of_cells(cell_ids: Sequence[str]) -> str:
    """Order-independent, deterministic SHA-1 hex digest of a set of cell ids.

    The ids are sorted, then each is fed to the hash followed by a newline byte
    (including a trailing newline after the last id). This is byte-for-byte the
    same digest as popsimprep ``batch_run_popsim.py``'s ``sha1_of_list`` on the
    same sorted ids, so manifests are comparable across both implementations. The
    digest depends only on the SET of ids, not their input order; no time or
    randomness is used, so it is stable across runs.
    """
    hasher = hashlib.sha1()
    for cell_id in sorted(cell_ids):
        hasher.update(cell_id.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


@dataclass(frozen=True)
class SplitManifest:
    """Identity record of one batch split, used to detect staleness.

    Two manifests describe the same batch content iff their ``schema_version``,
    ``max_cells``, ``km_cells``, ``num_1km``, ``num_100m`` and ``cells_100m_sha1``
    match (``source_folder`` and ``split_index`` are bookkeeping, not identity).
    """

    source_folder: str
    max_cells: int
    split_index: int
    km_cells: tuple[str, ...]
    num_1km: int
    num_100m: int
    cells_100m_sha1: str
    schema_version: int = 1


def build_manifest(
    source_folder: str,
    max_cells: int,
    split_index: int,
    km_cells: Sequence[str],
    cell_groups: Mapping[str, Sequence[str]],
) -> SplitManifest:
    """Build a :class:`SplitManifest` for the given 1 km cells.

    ``km_cells`` are stored sorted; the 100 m counts and content hash are computed
    from ``cell_groups``.

    Raises
    ------
    ValueError
        If any id in ``km_cells`` is absent from ``cell_groups`` (fail-fast: a
        manifest must describe real cells).
    """
    sorted_km = tuple(sorted(km_cells))
    missing = [k for k in sorted_km if k not in cell_groups]
    if missing:
        raise ValueError(f"km_cells not present in cell_groups: {missing}")

    all_100m = [child for k in sorted_km for child in cell_groups[k]]
    return SplitManifest(
        source_folder=source_folder,
        max_cells=max_cells,
        split_index=split_index,
        km_cells=sorted_km,
        num_1km=len(sorted_km),
        num_100m=len(all_100m),
        cells_100m_sha1=sha1_of_cells(all_100m),
        schema_version=1,
    )


def manifests_equivalent(a: SplitManifest, b: SplitManifest) -> bool:
    """Return ``True`` iff two manifests describe the same split.

    Matches popsimprep ``batch_run_popsim.py``'s ``manifests_equivalent``: it
    compares ``schema_version``, ``source_folder``, ``max_cells``,
    ``split_index``, ``km_cells`` and the 100 m counts + content hash. Including
    ``source_folder`` and ``split_index`` is deliberate -- the reuse-or-rebuild
    decision is made for one specific split folder, so a differing source or
    index means a different split and must trigger a rebuild rather than a reuse.
    The ``created_at`` timestamp present in the original manifest is intentionally
    NOT compared (and not stored here) so it never affects equivalence.
    """
    keys = ("schema_version", "source_folder", "max_cells", "split_index",
            "km_cells", "num_1km", "num_100m", "cells_100m_sha1")
    return all(getattr(a, k) == getattr(b, k) for k in keys)


def write_manifest(manifest: SplitManifest, path: Union[str, Path]) -> None:
    """Write a manifest as JSON. Callers should write this LAST in a split so a
    partial split never carries a valid manifest."""
    Path(path).write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")


def read_manifest(path: Union[str, Path]) -> SplitManifest:
    """Read a manifest written by :func:`write_manifest` (km_cells -> tuple)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["km_cells"] = tuple(data["km_cells"])
    return SplitManifest(**data)


def is_completed(folder: Union[str, Path]) -> bool:
    """Return ``True`` iff ``folder`` already holds the PopulationSim output."""
    return (Path(folder) / COMPLETION_MARKER).is_file()


@dataclass(frozen=True)
class BatchResult:
    """Outcome of running one batch folder.

    ``status`` must be one of ``succeeded`` / ``failed`` / ``skipped``; an invalid
    status is a programming error and raises at construction (fail-fast).
    """

    folder: str
    status: str
    message: str
    duration_s: float | None

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid BatchResult status {self.status!r}; expected one of "
                f"{sorted(_VALID_STATUSES)}."
            )


@dataclass(frozen=True)
class RunSummary:
    """Aggregate outcome of running a set of batches."""

    n_total: int
    n_succeeded: int
    n_failed: int
    n_skipped: int
    results: list[BatchResult] = field(default_factory=list)


def _error_detail(result, *, max_chars: int = 800) -> str:
    """Summarise a failed subprocess result's stderr/stdout for the BatchResult.

    PopulationSim writes its real error to stderr (and progress to stdout); the
    subprocess result captures both. Surfacing the tail makes a batch failure
    diagnosable instead of an opaque "exit code N" (no-silent-fallback policy).
    """
    stderr = (getattr(result, "stderr", "") or "").strip()
    stdout = (getattr(result, "stdout", "") or "").strip()
    detail = stderr if stderr else stdout
    if not detail:
        return "(no stderr/stdout captured)"
    lines = detail.splitlines()[-12:]
    joined = " | ".join(line.strip() for line in lines if line.strip())
    return joined[-max_chars:]


def run_batches(
    folders: Sequence[str],
    run_one: Callable[[str], BatchResult],
    *,
    num_workers: int = 3,
) -> RunSummary:
    """Run each batch folder through ``run_one`` concurrently; summarise outcomes.

    ``run_one`` is injected so the real PopulationSim subprocess is decoupled from
    callers and tests. Each PopulationSim run is its own process, so a thread pool
    (bounded by ``num_workers``) is the right concurrency primitive. Exceptions
    raised by ``run_one`` (e.g. an invalid status -- a programming error) propagate
    rather than being silently classified as failures.

    Returns
    -------
    RunSummary
        Counts plus the individual :class:`BatchResult` objects.
    """
    results: list[BatchResult] = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(run_one, f) for f in folders]
        for result in progress_parallel(futures, "popsim batches", total=len(folders)):
            results.append(result)

    n_succeeded = sum(1 for r in results if r.status == "succeeded")
    n_failed = sum(1 for r in results if r.status == "failed")
    n_skipped = sum(1 for r in results if r.status == "skipped")
    summary = RunSummary(
        n_total=len(folders),
        n_succeeded=n_succeeded,
        n_failed=n_failed,
        n_skipped=n_skipped,
        results=results,
    )
    logger.info(
        "[popsim.batch] ran %d batches: %d succeeded, %d failed, %d skipped",
        summary.n_total, n_succeeded, n_failed, n_skipped,
    )
    # Surface each failure's captured error (no-silent-fallback): a swallowed
    # PopulationSim error would otherwise only show as a downstream "missing
    # batch outputs" / empty-merge crash.
    for result in results:
        if result.status == "failed":
            logger.warning(
                "[popsim.batch] batch failed: %s -- %s",
                result.folder, result.message,
            )
    return summary


# Default PopulationSim invocation, matching popsimprep batch_run_popsim.py:
# ``uv run populationsim -w <folder>`` with cwd = the folder's parent.
DEFAULT_POPSIM_COMMAND = ("uv", "run", "populationsim")
DEFAULT_POPSIM_TIMEOUT_S = 3600

# Thread caps applied to every PopulationSim subprocess. numpy/OpenBLAS (and OpenMP /
# MKL / numexpr) otherwise spawn one thread per CPU core PER PROCESS, so running many
# batches in parallel oversubscribes the machine by orders of magnitude (e.g. 16
# batches x 64 OpenBLAS threads = 1024 threads on 64 cores). On the run server that
# oversubscription crashed numpy with segfaults in libc (12 batches lost in one 25%
# run). Pinning each subprocess to a single BLAS/OpenMP thread removes the
# oversubscription; batch-level parallelism is then governed solely by num_workers.
# Single source of truth in braunschweig.parallelism (shared with the chainsolvers
# worker pool, issue #122); the old private name is kept as an alias.
_SINGLE_THREAD_BLAS_ENV = parallelism.SINGLE_THREAD_BLAS_ENV


def make_populationsim_run_one(
    *,
    command_prefix: Sequence[str] = DEFAULT_POPSIM_COMMAND,
    timeout_s: Optional[int] = DEFAULT_POPSIM_TIMEOUT_S,
    cwd: Optional[Union[str, Path]] = None,
    dry_run: bool = False,
    subprocess_run: Callable = subprocess.run,
) -> Callable[[str], BatchResult]:
    """Build a concrete ``run_one`` that runs PopulationSim on a batch folder.

    Faithful to popsimprep ``batch_run_popsim.py``'s ``run_popsim``: a completed
    folder (output already present) is skipped without spawning a process; the
    command is ``[*command_prefix, "-w", <folder>]`` run with ``cwd`` defaulting to
    the folder's parent and a hard ``timeout_s``; a non-zero exit, a missing output
    file, a timeout, or any other exception classify the batch as ``failed``
    (never raised, so one bad batch does not abort the run -- the run summary keeps
    the failure visible).

    ``subprocess_run`` is injectable so the runner is unit-testable without
    PopulationSim installed. The returned callable is suitable as ``run_one`` for
    :func:`run_batches`.

    ``timeout_s <= 0`` (or ``None``) disables the per-batch timeout entirely: the
    PopulationSim subprocess runs to completion no matter how long it takes (use this
    for heavy control sets where convergence matters more than wall-clock).
    """
    effective_timeout = timeout_s if (timeout_s is None or timeout_s > 0) else None

    def run_one(folder: str) -> BatchResult:
        folder_path = Path(folder)
        start = time.monotonic()

        if is_completed(folder_path):
            return BatchResult(str(folder), "skipped", "already completed",
                               time.monotonic() - start)
        if dry_run:
            return BatchResult(str(folder), "succeeded", "dry run",
                               time.monotonic() - start)

        command = [*command_prefix, "-w", str(folder)]
        run_cwd = str(cwd) if cwd is not None else str(folder_path.parent)
        # Pin BLAS/OpenMP to a single thread per subprocess (see _SINGLE_THREAD_BLAS_ENV)
        # on top of the inherited environment, so parallel batches do not oversubscribe
        # the CPU and crash numpy.
        run_env = {**os.environ, **_SINGLE_THREAD_BLAS_ENV}
        try:
            result = subprocess_run(
                command, cwd=run_cwd, capture_output=True, text=True,
                timeout=effective_timeout, env=run_env,
            )
        except subprocess.TimeoutExpired:
            return BatchResult(str(folder), "failed", f"timeout after {timeout_s}s",
                               time.monotonic() - start)
        except Exception as error:  # surface, never abort the whole run
            return BatchResult(str(folder), "failed", str(error),
                               time.monotonic() - start)

        if result.returncode != 0:
            return BatchResult(str(folder), "failed",
                               f"exit code {result.returncode}: "
                               f"{_error_detail(result)}",
                               time.monotonic() - start)
        if not is_completed(folder_path):
            return BatchResult(str(folder), "failed",
                               f"no output file created; {_error_detail(result)}",
                               time.monotonic() - start)
        return BatchResult(str(folder), "succeeded", "completed",
                           time.monotonic() - start)

    return run_one
