"""Memory protection for the forked chainsolver worker pool.

The chainsolver stage runs the pipeline's only ``fork`` process pool (``parallel_solving``): each
worker inherits the driver's whole address space copy-on-write, i.e. it shares every page with the
driver until one of the two writes to it. The 100 % production run of 2026-09-25/28 on the felix
server (94.28 GiB) exposed three ways this goes wrong; one protection per defect lives here
(evidence: run manifest ``docs/runs/chainsolver-pool-memory-2026-09-28.yml``, decision: ADR-0135):

1. :func:`frozen_heap_for_fork` (driver side). A worker's private memory grew by 0.62 GiB while
   its RSS grew by 0.09 GiB, i.e. mostly pages it shared with the driver became private
   copies. CPython's cyclic garbage collector writes into the header of every
   container object it visits, including the millions the driver built before forking, so a
   worker's first full collection copies every page that holds one. ``gc.freeze()`` right before
   the fork moves those objects into the permanent generation, which no collection visits -- the
   use the CPython documentation of ``gc.freeze`` names for it.
2. :func:`prefer_oom_kill_of_current_process` (worker side). With 56 workers the kernel OOM killer
   took the DRIVER first, which ended the stage for good. A killed worker only costs the shards of
   its executor generation, which are retried (``_run_shards_with_recovery``), so a worker must
   always be the first victim.
3. :func:`exit_when_parent_dies` (worker side). The 52 workers that survived the killed driver were
   re-parented to init and held their memory for two days: an idle worker blocks on the call-queue
   pipe, whose write end it inherited itself, so no end-of-file ever arrives.

None of this changes a result: it decides which process holds or loses memory, never what a shard
computes. Deliberately a module of this stage package rather than of ``braunschweig.parallelism``:
that module is hashed by the PopulationSim stage as well, so editing it would re-run PopulationSim
for a change confined to this pool.
"""
from __future__ import annotations

import contextlib
import gc
import logging
import os
import threading
import time
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

#: Linux's per-process bias of the OOM killer, from -1000 (never kill) to 1000 (kill first). Any
#: process may RAISE its own value; only lowering it needs a privilege.
OOM_SCORE_ADJ_PATH = "/proc/self/oom_score_adj"

#: The maximum, so a pool worker always outranks the driver (0) and every other process on the box.
WORKER_OOM_SCORE_ADJ = 1000

#: How often a pool worker checks that the driver that forked it is still alive, in seconds. An
#: orphaned worker therefore holds its memory for at most about this long; the check itself is one
#: ``getppid`` system call.
PARENT_WATCH_INTERVAL_SECONDS = 5.0

#: Name of the watch thread, so a thread dump (and a test) can identify it.
PARENT_WATCH_THREAD_NAME = "chainsolver-parent-watch"

#: Exit status of a worker that ended itself because its driver was gone.
ORPHANED_WORKER_EXIT_CODE = 1


@contextlib.contextmanager
def frozen_heap_for_fork() -> Iterator[int]:
    """Freeze every object the cyclic garbage collector tracks while forked workers are alive.

    Enter it in the driver BEFORE the pool forks its workers and leave it after the pool is shut
    down; yields the number of frozen objects. Frozen objects are skipped by every collection: in
    the workers, which inherit the permanent generation, so their collections no longer write into
    (and thereby copy) the pages they share with the driver; and in the driver, whose own
    collections would otherwise copy the same pages on the driver's side. The only cost is that a
    reference cycle among objects that existed before the pool is reclaimed after the pool rather
    than during it.

    An existing freeze (``gc.get_freeze_count() > 0`` on entry) is left in place on exit, because
    ``gc.unfreeze()`` releases every frozen object, not only the ones frozen here.
    """
    already_frozen = gc.get_freeze_count() > 0
    gc.freeze()
    try:
        yield gc.get_freeze_count()
    finally:
        if not already_frozen:
            gc.unfreeze()


def oom_score_adjustment_supported(path: str = OOM_SCORE_ADJ_PATH) -> bool:
    """Whether this platform has an OOM-killer bias to raise (Linux ``/proc``).

    Checked once in the driver, which logs the answer; :func:`prefer_oom_kill_of_current_process`
    stays quiet about a missing file so an unsupported platform is not reported once per worker.
    """
    return os.path.exists(path)


def prefer_oom_kill_of_current_process(path: str = OOM_SCORE_ADJ_PATH) -> bool:
    """Make THIS process the kernel OOM killer's first victim. Call it inside a pool worker only.

    Returns True when the bias was raised to :data:`WORKER_OOM_SCORE_ADJ`, False when the platform
    has no such file (see :func:`oom_score_adjustment_supported`) or the write failed; a failed
    write to an existing file is logged as a warning, because the driver is then exposed again.
    """
    if not oom_score_adjustment_supported(path):
        return False
    try:
        with open(path, "w", encoding="ascii") as handle:
            handle.write(f"{WORKER_OOM_SCORE_ADJ}\n")
    except OSError as error:
        logger.warning(
            "[braunschweig.secondary_chainsolvers] pool worker %d could not raise its "
            "oom_score_adj (%s) to %d: %s -- the kernel OOM killer may take the driver "
            "first instead of this worker.", os.getpid(), path, WORKER_OOM_SCORE_ADJ, error)
        return False
    return True


def parent_death_watch_supported() -> bool:
    """Whether an orphan can see its parent die: POSIX re-parents it, so ``getppid()`` changes.

    On Windows ``os.getppid()`` keeps returning the dead parent's pid, so the watch could never
    fire there (a spawned Windows worker also inherits no pipe end, so it is not trapped).
    """
    return os.name == "posix"


def exit_when_parent_dies(parent_pid: int,
                          interval_seconds: float = PARENT_WATCH_INTERVAL_SECONDS
                          ) -> Optional[threading.Thread]:
    """End THIS process as soon as ``parent_pid`` is no longer its parent. Pool workers only.

    Starts a daemon thread that compares ``os.getppid()`` with ``parent_pid`` every
    ``interval_seconds``; the driver passes its own pid, so a driver that died before this call is
    caught on the first check. Busy or idle makes no difference: the thread runs while the worker's
    main thread computes a shard (the GIL is handed over every few milliseconds) or waits for one.
    Returns the thread, or None where :func:`parent_death_watch_supported` is False.
    """
    if not parent_death_watch_supported():
        return None
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be positive, got {interval_seconds!r}.")

    def watch() -> None:
        while os.getppid() == parent_pid:
            time.sleep(interval_seconds)
        # A raw write, not logging: the worker's main thread may hold a logging or stdout lock
        # at this moment, and nothing may stand between an orphan and its exit.
        message = (
            f"[braunschweig.secondary_chainsolvers] pool worker {os.getpid()} exits: its driver "
            f"(pid {parent_pid}) is gone, and an orphaned worker would keep its memory forever.\n")
        try:
            os.write(2, message.encode("ascii", "replace"))
        except OSError:
            pass
        os._exit(ORPHANED_WORKER_EXIT_CODE)

    thread = threading.Thread(target=watch, name=PARENT_WATCH_THREAD_NAME, daemon=True)
    thread.start()
    return thread
