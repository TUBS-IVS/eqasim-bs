"""Memory protection of the forked chainsolver worker pool (100 % production run, 2026-09-25/28).

Three defects of the fork pool, each observed on the felix server:

1. Copy-on-write growth. A forked worker's private memory grew by 0.62 GiB while its RSS grew by
   0.09 GiB, i.e. mostly pages it SHARED with the driver became private copies
   (docs/runs/chainsolver-pool-memory-2026-09-28.yml). CPython's cyclic garbage collector
   writes into the header of every object it
   visits, including the millions the driver built before forking, so a worker's first full
   collection copies every page that holds one. ``gc.freeze()`` in the driver before forking
   moves those objects into the permanent generation, which no collection visits.
2. The wrong victim. With 56 workers the kernel OOM killer took the DRIVER first, which ended the
   stage for good; a killed worker only costs its shard, which is retried (issue #344).
3. Orphans. The 52 surviving workers were re-parented to init and held their memory for two
   days: they block on a pipe whose write end they inherited themselves, so no EOF ever arrives.

The fake-executor tests pin the orchestration on every platform; the fork tests prove each
premise on the run server's platform with a real process pool.
"""
import contextlib
import gc
import multiprocessing as mp
import os
import signal
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from braunschweig.synthesis.locations.secondary_chainsolvers import parallel_solving as ps
from braunschweig.synthesis.locations.secondary_chainsolvers import pool_protection as pp

FORK_AVAILABLE = "fork" in mp.get_all_start_methods()
LINUX_PROC = os.path.exists("/proc/self/smaps_rollup") and os.path.exists("/proc/self/oom_score_adj")
ON_LINUX_FORK = FORK_AVAILABLE and LINUX_PROC
LINUX_ONLY = "needs fork and /proc (smaps_rollup, oom_score_adj), i.e. the run server's platform"


# --- 1. the driver heap is frozen while workers are forked ------------------------------------

def test_the_driver_heap_is_frozen_inside_and_released_after():
    assert gc.get_freeze_count() == 0, "precondition: nothing else in the suite freezes the heap"
    with pp.frozen_heap_for_fork() as frozen_objects:
        # Not equal to each other: a frozen object freed by its reference count leaves the
        # permanent generation, so the count may drop between the two reads.
        assert frozen_objects > 0
        assert gc.get_freeze_count() > 0
    assert gc.get_freeze_count() == 0


def test_the_heap_is_released_even_when_the_pool_fails():
    with pytest.raises(RuntimeError):
        with pp.frozen_heap_for_fork():
            raise RuntimeError("the pool failed")
    assert gc.get_freeze_count() == 0


def test_a_freeze_that_was_already_in_place_is_left_alone():
    gc.freeze()
    try:
        with pp.frozen_heap_for_fork():
            pass
        assert gc.get_freeze_count() > 0, "unfreezing here would release someone else's freeze"
    finally:
        gc.unfreeze()


#: Parent heap for the copy-on-write test. Dicts that hold a list stay tracked by the collector
#: (CPython untracks a dict of atomic values, which a collection then never visits); 400,000 of
#: them put roughly 50 MB of object headers on pages the worker shares with its parent.
_PARENT_HEAP_ENTRIES = 400_000


def _private_kib():
    total_kib = 0
    with open("/proc/self/smaps_rollup", encoding="ascii") as handle:
        for line in handle:
            if line.startswith(("Private_Clean:", "Private_Dirty:")):
                total_kib += int(line.split()[1])
    return total_kib


def _private_growth_of_one_full_collection_kib(_unused):
    before_kib = _private_kib()
    gc.collect()
    return _private_kib() - before_kib


def _worker_growth_kib(freeze):
    heap = [{"index": index, "values": [index]} for index in range(_PARENT_HEAP_ENTRIES)]
    guard = pp.frozen_heap_for_fork() if freeze else contextlib.nullcontext()
    with guard:
        with ProcessPoolExecutor(1, mp_context=mp.get_context("fork")) as pool:
            growth_kib = pool.submit(_private_growth_of_one_full_collection_kib, None).result()
    del heap
    return growth_kib


@pytest.mark.skipif(not ON_LINUX_FORK, reason=LINUX_ONLY)
def test_a_forked_workers_collection_no_longer_copies_the_frozen_driver_heap():
    unfrozen_kib = _worker_growth_kib(freeze=False)
    frozen_kib = _worker_growth_kib(freeze=True)
    # The premise first: without the freeze one collection really turns the inherited heap into
    # private copies -- the production defect. Demand well under half of the ~50 MB it touches.
    assert unfrozen_kib > 20 * 1024, f"premise failed: only {unfrozen_kib} KiB copied"
    assert frozen_kib < unfrozen_kib / 10, (frozen_kib, unfrozen_kib)


# --- 2. a pool worker, not the driver, is the kernel's first OOM victim -----------------------

def test_the_oom_score_is_raised_through_the_given_file(tmp_path):
    target = tmp_path / "oom_score_adj"
    target.write_text("0\n")
    assert pp.prefer_oom_kill_of_current_process(str(target)) is True
    assert target.read_text().strip() == str(pp.WORKER_OOM_SCORE_ADJ) == "1000"


def test_a_platform_without_the_file_is_reported_as_unsupported(tmp_path):
    missing = str(tmp_path / "no_proc" / "oom_score_adj")
    assert pp.oom_score_adjustment_supported(missing) is False
    assert pp.prefer_oom_kill_of_current_process(missing) is False


def test_a_failed_write_to_an_existing_file_is_logged(tmp_path, caplog):
    directory_in_place_of_the_file = tmp_path / "oom_score_adj"
    directory_in_place_of_the_file.mkdir()
    with caplog.at_level("WARNING"):
        assert pp.prefer_oom_kill_of_current_process(str(directory_in_place_of_the_file)) is False
    assert "oom_score_adj" in caplog.text


def _raise_and_read_own_oom_score(_unused):
    applied = pp.prefer_oom_kill_of_current_process()
    with open(pp.OOM_SCORE_ADJ_PATH, encoding="ascii") as handle:
        return applied, handle.read().strip()


@pytest.mark.skipif(not ON_LINUX_FORK, reason=LINUX_ONLY)
def test_a_forked_worker_really_makes_itself_the_first_oom_victim():
    with ProcessPoolExecutor(1, mp_context=mp.get_context("fork")) as pool:
        applied, score = pool.submit(_raise_and_read_own_oom_score, None).result()
    assert applied is True
    assert score == "1000"


# --- 3. a pool worker dies with its driver -----------------------------------------------------

def _start_parent_watch(parent_pid):
    pp.exit_when_parent_dies(parent_pid, interval_seconds=0.1)


def _driver_process(connection, watch_parent):
    """Stand-in for the stage driver: forks one pool worker, reports its pid, idles until killed."""
    executor = ProcessPoolExecutor(
        1, mp_context=mp.get_context("fork"),
        initializer=_start_parent_watch if watch_parent else None,
        initargs=(os.getpid(),) if watch_parent else ())
    connection.send(executor.submit(os.getpid).result())
    # BUSY when its driver dies, as a worker mid-shard is in production.
    executor.submit(time.sleep, 600)
    time.sleep(600)


def _process_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A zombie has already exited; it only waits for its new parent to reap it.
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def _worker_gone_after_its_driver_is_killed(watch_parent, wait_seconds):
    context = mp.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    driver = context.Process(target=_driver_process, args=(sender, watch_parent))
    driver.start()
    worker_pid = None
    try:
        assert receiver.poll(60), "the driver never reported its pool worker"
        worker_pid = receiver.recv()
        assert not _process_gone(worker_pid), "the worker must be alive while its driver is"
        os.kill(driver.pid, signal.SIGKILL)
        driver.join(10)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and not _process_gone(worker_pid):
            time.sleep(0.05)
        return _process_gone(worker_pid)
    finally:
        # Never leak a process into the rest of the suite, whatever the outcome.
        if worker_pid is not None and not _process_gone(worker_pid):
            os.kill(worker_pid, signal.SIGKILL)
        if driver.is_alive():
            driver.kill()


@pytest.mark.skipif(not ON_LINUX_FORK, reason=LINUX_ONLY)
def test_without_the_watch_a_pool_worker_outlives_its_killed_driver():
    """The premise: exactly this held the felix server's memory for two days."""
    assert _worker_gone_after_its_driver_is_killed(watch_parent=False, wait_seconds=2.0) is False


@pytest.mark.skipif(not ON_LINUX_FORK, reason=LINUX_ONLY)
def test_a_watched_pool_worker_exits_once_its_driver_is_killed():
    assert _worker_gone_after_its_driver_is_killed(watch_parent=True, wait_seconds=10.0) is True


def test_the_parent_watch_is_only_offered_where_orphans_are_re_parented():
    # On Windows os.getppid() keeps returning the dead parent's pid, so the watch could never
    # fire there; claiming it runs would be a silent no-op.
    assert pp.parent_death_watch_supported() is (os.name == "posix")


# --- wiring into the chainsolver pool ------------------------------------------------------------

def _settled_future(result=None, error=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
    return future


class _RecordingExecutor:
    """Executor stand-in: scripted per-shard outcomes, records what each generation saw."""

    def __init__(self, outcomes, generations, max_workers=None, **_kwargs):
        self.outcomes = outcomes
        self.record = {"max_workers": max_workers, "freeze_at_creation": gc.get_freeze_count(),
                       "freeze_at_submit": [], "shards": []}
        generations.append(self.record)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, function, task):
        self.record["freeze_at_submit"].append(gc.get_freeze_count())
        self.record["shards"].append(task[0])
        outcome = self.outcomes(task[0], len(self.record))
        if isinstance(outcome, BaseException):
            return _settled_future(error=outcome)
        return _settled_future(result=outcome)


def _tasks(count):
    return [(index, [f"p{index}"], None, 1000 + index) for index in range(count)]


def _run_with_shard_one_broken_for(broken_generations, max_workers, generations):
    generation_count = []

    def factory(**kwargs):
        generation_count.append(None)
        return _RecordingExecutor(outcomes, generations, **kwargs)

    def outcomes(shard_index, _submitted):
        if shard_index == 1 and len(generation_count) <= broken_generations:
            return BrokenProcessPool("worker holding shard 1 was OOM-killed")
        return shard_index, f"frame-{shard_index}", []

    kwargs = {} if max_workers is None else {"max_workers": max_workers}
    return ps._run_shards_with_recovery(
        _tasks(4), executor_kwargs=kwargs, executor_factory=factory,
        max_attempts=3, progress=lambda *_args: None)


def test_every_executor_generation_forks_from_a_frozen_heap():
    generations = []
    results, _failed = _run_with_shard_one_broken_for(1, 8, generations)

    assert results == {index: f"frame-{index}" for index in range(4)}
    assert len(generations) == 2, "one lost shard, one retry generation"
    for generation in generations:
        assert generation["freeze_at_creation"] > 0
        assert all(count > 0 for count in generation["freeze_at_submit"])
    assert gc.get_freeze_count() == 0, "the driver heap must be released after the pool"


def test_a_retry_generation_runs_with_half_the_workers(capsys):
    generations = []
    _run_with_shard_one_broken_for(2, 8, generations)

    assert [generation["max_workers"] for generation in generations] == [8, 4, 2]
    assert [generation["shards"] for generation in generations][1:] == [[1], [1]]
    out = capsys.readouterr().out
    assert "with 4 instead of 8 workers" in out
    assert "with 2 instead of 4 workers" in out


def test_the_retry_worker_count_never_drops_below_one():
    generations = []
    _run_with_shard_one_broken_for(2, 1, generations)
    assert [generation["max_workers"] for generation in generations] == [1, 1, 1]


def test_the_pool_initializer_protects_the_driver_only_inside_a_pool_worker(monkeypatch):
    """The serial path calls the same initializer INSIDE the driver: raising the driver's own OOM
    score, or tying the driver's life to its shell, would turn the protection upside down."""
    calls = []
    monkeypatch.setattr(ps.parallelism, "limit_worker_blas_threads", lambda: None)
    monkeypatch.setattr(pp, "prefer_oom_kill_of_current_process", lambda: calls.append("oom"))
    monkeypatch.setattr(pp, "exit_when_parent_dies",
                        lambda parent_pid: calls.append(("watch", parent_pid)))

    ps._init_chain_worker(None, "carla", None)
    assert calls == [], "the in-process serial path must not protect against itself"

    ps._init_chain_worker(None, "carla", None, pool_parent_pid=4321)
    assert calls == ["oom", ("watch", 4321)]


def test_the_pool_hands_its_workers_the_driver_pid(monkeypatch):
    captured = []

    def fake_run_shards(tasks, executor_kwargs, progress, **kwargs):
        captured.append(executor_kwargs)
        return {index: None for index, *_rest in tasks}, []

    monkeypatch.setattr(ps, "_run_shards_with_recovery", fake_run_shards)
    import pandas as pd
    persons = [f"p{index}#{index}" for index in range(6)]
    plans = pd.DataFrame({"unique_person_id": persons, "to_act_type": ["shop"] * 6})
    ps._solve_chains_parallel(plans, persons, None, "carla", 99, 2, 0.0, n_shards=3)

    (executor_kwargs,) = captured
    assert executor_kwargs["initializer"] is ps._init_chain_worker
    assert executor_kwargs["initargs"][3] == os.getpid()


def _report_worker_protection(task):
    with open(pp.OOM_SCORE_ADJ_PATH, encoding="ascii") as handle:
        oom_score = handle.read().strip()
    watching = any(thread.name == pp.PARENT_WATCH_THREAD_NAME for thread in threading.enumerate())
    return task[0], (gc.get_freeze_count(), oom_score, watching), []


@pytest.mark.skipif(not ON_LINUX_FORK, reason=LINUX_ONLY)
def test_real_pool_workers_run_frozen_oom_preferred_and_watched():
    """End to end through the real executor and the real pool initializer."""
    results, _failed = ps._run_shards_with_recovery(
        _tasks(2),
        executor_kwargs=dict(max_workers=2, mp_context=mp.get_context("fork"),
                             initializer=ps._init_chain_worker,
                             initargs=(None, None, None, os.getpid())),
        progress=lambda *_args: None,
        worker_function=_report_worker_protection,
    )
    for frozen_objects, oom_score, watching in results.values():
        assert frozen_objects > 0
        assert oom_score == "1000"
        assert watching is True
    assert gc.get_freeze_count() == 0
