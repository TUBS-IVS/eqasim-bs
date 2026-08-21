"""A killed shard worker must fail loudly and be retried, never hang (issue #344).

The night run of 2026-08-20 lost one of 62 shard workers to the kernel OOM killer
(two heavy runs competed for memory) and then sat at zero CPU for four hours:
``multiprocessing.Pool.imap_unordered`` delivers the surviving n-1 results and
then waits forever for a task that died with its worker, because Pool never
re-queues it. Reproduced on the server's interpreter: 3 of 4 results delivered,
then an indefinite hang, while ``concurrent.futures.ProcessPoolExecutor`` raises
``BrokenProcessPool`` on the identical scenario.

These tests drive the orchestration through an injected executor factory, so they
exercise the retry logic deterministically without spawning processes or relying
on a platform-specific kill.
"""
import multiprocessing as mp
import os
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

import pytest

from braunschweig.synthesis.locations.secondary_chainsolvers import parallel_solving as ps


def _settled_future(result=None, error=None):
    """A REAL, already-settled Future, so the production code path runs the real
    ``concurrent.futures.as_completed`` rather than a stand-in for it."""
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
    return future


class _FakeExecutor:
    """Executor stand-in whose per-task outcome is scripted by shard index.

    ``outcomes`` maps shard index -> either a return value or an exception
    instance. Records which shard indices each executor generation received, so a
    test can assert that a retry resubmits ONLY the missing shards.
    """

    def __init__(self, outcomes, submitted):
        self.outcomes = outcomes
        self.submitted = submitted
        self.generation = []
        submitted.append(self.generation)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, function, task):
        shard_index = task[0]
        self.generation.append(shard_index)
        outcome = self.outcomes[shard_index]
        if isinstance(outcome, BaseException):
            return _settled_future(error=outcome)
        return _settled_future(result=outcome)


def _executor_factory(outcomes, submitted):
    def factory(**_kwargs):
        return _FakeExecutor(outcomes, submitted)
    return factory


def _tasks(count):
    """Task tuples shaped like the real ones: (shard_index, uids, frame, seed)."""
    return [(index, [f"p{index}"], None, 1000 + index) for index in range(count)]


def _shard_result(shard_index):
    return (shard_index, f"frame-{shard_index}", [shard_index])


def test_all_shards_delivered_are_returned_keyed_by_shard_index():
    submitted = []
    outcomes = {index: _shard_result(index) for index in range(4)}
    results, failed = ps._run_shards_with_recovery(
        _tasks(4), executor_factory=_executor_factory(outcomes, submitted),
        executor_kwargs={}, progress=lambda *_a: None)

    assert results == {index: f"frame-{index}" for index in range(4)}
    assert sorted(failed) == [0, 1, 2, 3]
    assert len(submitted) == 1, "no retry generation should be needed"


def test_a_killed_worker_is_retried_and_only_the_missing_shard_is_resubmitted():
    """The whole point: one dead worker costs one shard, not the whole stage."""
    submitted = []
    outcomes = {index: _shard_result(index) for index in range(4)}
    outcomes[2] = BrokenProcessPool("worker 2 was killed")

    def factory(**_kwargs):
        # Heal shard 2 only from the SECOND generation on: mutating at the first
        # executor's creation would make it succeed straight away (the fake's
        # outcome is read at submit time), so the retry would never be exercised.
        if submitted:
            outcomes[2] = _shard_result(2)
        return _FakeExecutor(outcomes, submitted)

    results, _failed = ps._run_shards_with_recovery(
        _tasks(4), executor_factory=factory, executor_kwargs={},
        progress=lambda *_a: None)

    assert results == {index: f"frame-{index}" for index in range(4)}
    assert len(submitted) == 2, submitted
    assert submitted[1] == [2], (
        f"the retry must resubmit only the missing shard, got {submitted[1]}")


def test_retries_are_bounded_and_the_failure_names_the_missing_shards():
    submitted = []
    outcomes = {index: _shard_result(index) for index in range(4)}
    outcomes[1] = BrokenProcessPool("worker 1 keeps dying")
    outcomes[3] = BrokenProcessPool("worker 3 keeps dying")

    with pytest.raises(RuntimeError) as excinfo:
        ps._run_shards_with_recovery(
            _tasks(4), executor_factory=_executor_factory(outcomes, submitted),
            executor_kwargs={}, max_attempts=3, progress=lambda *_a: None)

    message = str(excinfo.value)
    assert "1" in message and "3" in message, message
    assert "3 attempt" in message or "attempts" in message, message
    assert len(submitted) == 3, "must stop after max_attempts generations"


def test_a_task_level_exception_is_not_treated_as_a_worker_death():
    """A bug inside a shard must surface, not be masked by a retry loop."""
    submitted = []
    outcomes = {index: _shard_result(index) for index in range(3)}
    outcomes[1] = ValueError("bad shard input")

    with pytest.raises(ValueError):
        ps._run_shards_with_recovery(
            _tasks(3), executor_factory=_executor_factory(outcomes, submitted),
            executor_kwargs={}, progress=lambda *_a: None)

    assert len(submitted) == 1, "a task exception must not trigger a retry"


def test_progress_is_reported_per_delivered_shard():
    submitted = []
    outcomes = {index: _shard_result(index) for index in range(3)}
    seen = []
    ps._run_shards_with_recovery(
        _tasks(3), executor_factory=_executor_factory(outcomes, submitted),
        executor_kwargs={}, progress=lambda done, total: seen.append((done, total)))

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_the_module_no_longer_consumes_results_through_imap_unordered():
    """Pin the actual defect: imap_unordered is what waits forever for a dead
    worker's result. A future refactor must not quietly reintroduce it."""
    source = ps.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    # Match the CALL, not the word: the module deliberately names the API in a
    # comment to explain why it is gone.
    assert "imap_unordered(" not in text, (
        "parallel_solving must not consume shard results via Pool.imap_unordered: "
        "it never notices a worker killed mid-task and blocks forever (#344)")


# --- integration: a real executor and a really dying worker ------------------

def _abrupt_first_time(task):
    """Die the way an OOM-killed worker dies (no unwinding) on the first visit.

    Coordination through a marker file, because the attempts run in different
    processes: shard 1 exits abruptly until the marker exists, then succeeds.
    """
    shard_index, _uids, marker_dir, _seed = task
    if shard_index == 1:
        marker = os.path.join(marker_dir, "shard1_has_died_once")
        if not os.path.exists(marker):
            with open(marker, "w") as handle:
                handle.write("died")
            os._exit(1)
    return shard_index, f"frame-{shard_index}", []


@pytest.mark.skipif(not hasattr(os, "fork"),
                    reason="uses the fork start method, i.e. the run server's platform")
def test_a_real_executor_recovers_from_a_worker_that_dies(tmp_path):
    """End-to-end through concurrent.futures with a process that really dies.

    The fake-executor tests above pin the orchestration; this one proves the
    premise they rest on -- that ProcessPoolExecutor turns an abruptly dead
    worker into a raised BrokenProcessPool rather than the indefinite wait
    multiprocessing.Pool produces.
    """
    tasks = [(index, [f"p{index}"], str(tmp_path), 1000 + index) for index in range(3)]

    results, _failed = ps._run_shards_with_recovery(
        tasks,
        executor_kwargs=dict(max_workers=3, mp_context=mp.get_context("fork")),
        progress=lambda *_a: None,
        worker_function=_abrupt_first_time,
    )

    assert results == {index: f"frame-{index}" for index in range(3)}
    assert os.path.exists(tmp_path / "shard1_has_died_once"), (
        "the worker must really have died, otherwise this proves nothing")


# --- config wiring -----------------------------------------------------------

def test_the_stage_declares_the_shard_attempts_option_with_its_default():
    """synpp scopes config per stage: execute() may only read what configure()
    declared, so a missing declaration crashes the run on the next cache
    devalidation (the #229 class)."""
    from braunschweig.synthesis.locations import secondary_chainsolvers as stage

    declared = {}

    class _Context:
        def config(self, name, *args, **kwargs):
            default = args[0] if args else kwargs.get("default")
            declared[name] = default
            return default

        def stage(self, *_args, **_kwargs):
            return None

    stage.configure(_Context())

    assert declared["braunschweig.chainsolvers.shard_attempts"] == ps.DEFAULT_SHARD_ATTEMPTS
    assert ps.DEFAULT_SHARD_ATTEMPTS > 1, (
        "the guard must retry by default: the observed cause is transient memory "
        "pressure from a concurrent run, which a retry survives")
