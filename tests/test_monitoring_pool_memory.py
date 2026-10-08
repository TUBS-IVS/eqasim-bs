"""Private memory of a stage's worker pool, from a recorded resource series (ADR-0135).

Under ``fork`` a worker's RSS counts every page it shares with the driver, so on the 94 GiB run
server each of 16 chainsolver workers read about 36 GiB RSS. What the pool really costs is what the
machine lost beyond the driver:

    pool private = used - driver RSS - (used - driver RSS in the last sample before the pool),
    used         = MemTotal - MemAvailable + swap used,

divided by the live worker count. The worker RSS still carries one signal: a page copied on write
REPLACES a page the worker already mapped (its RSS is unchanged), a new allocation ADDS one (its RSS
grows). Private growth without RSS growth therefore identifies copy-on-write -- the evidence behind
the chainsolver pool's ``gc.freeze`` (docs/runs/chainsolver-pool-memory-2026-09-28.yml).
"""
from __future__ import annotations

from braunschweig.monitoring import summary

ROOT_PID = 100
MIB_KB = 1024
GIB_KB = 1024 * 1024
TOTAL_KB = 100 * GIB_KB


def _sample(index, workers, driver_rss_kb, used_kb, worker_rss_kb=None, stage="stage.pool",
            extra_children=(), available_kb="derived"):
    processes = [{"pid": ROOT_PID, "ppid": 1, "tag": "python run_synpp.py", "rss_kb": driver_rss_kb,
                  "peak_rss_kb": driver_rss_kb, "cpu_seconds": 10.0 * index}]
    for number in range(workers):
        rss_kb = driver_rss_kb if worker_rss_kb is None else worker_rss_kb
        processes.append({"pid": 1000 + number, "ppid": ROOT_PID, "tag": "python run_synpp.py",
                          "rss_kb": rss_kb, "peak_rss_kb": rss_kb, "cpu_seconds": 5.0 * index})
    processes.extend(extra_children)
    return {
        "sample_index": index, "timestamp": "2026-09-28T10:%02d:00" % index,
        "unix_time": 1000.0 + 30.0 * index, "stage": stage, "root_pid": ROOT_PID,
        "memory_total_kb": TOTAL_KB,
        "memory_available_kb": TOTAL_KB - used_kb if available_kb == "derived" else available_kb,
        "swap_used_kb": 0, "processes": processes, "process_count": len(processes),
    }


def _pool_rows():
    """Driver 36 GiB; 2 GiB of other memory before the pool; 4 workers at 700, then 1100 MiB
    each, the step being copy-on-write (worker RSS unchanged); the driver grows by 512 MiB
    meanwhile, which must not be charged to the workers."""
    return [
        _sample(0, 0, 36 * GIB_KB, 38 * GIB_KB),
        _sample(1, 4, 36 * GIB_KB, 38 * GIB_KB + 4 * 700 * MIB_KB, worker_rss_kb=36 * GIB_KB),
        _sample(2, 4, 36 * GIB_KB + 512 * MIB_KB, 38 * GIB_KB + 512 * MIB_KB + 4 * 1100 * MIB_KB,
                worker_rss_kb=36 * GIB_KB),
        _sample(3, 0, 36 * GIB_KB, 38 * GIB_KB),
    ]


def test_private_memory_per_worker_is_what_the_machine_lost_beyond_the_driver():
    (episode,) = summary.pool_memory_episodes(_pool_rows())

    assert episode["baseline_kb"] == 2 * GIB_KB
    assert episode["max_workers"] == 4
    assert episode["peak_private_per_worker_kb"] == 1100 * MIB_KB
    assert episode["workers_at_peak"] == 4
    assert episode["peak_timestamp"] == "2026-09-28T10:02:00"
    assert episode["peak_pool_private_kb"] == 4 * 1100 * MIB_KB
    assert episode["driver_rss_at_peak_kb"] == 36 * GIB_KB + 512 * MIB_KB


def test_private_growth_without_worker_rss_growth_is_the_copy_on_write_signature():
    (episode,) = summary.pool_memory_episodes(_pool_rows())

    assert episode["private_per_worker_growth_kb"] == 400 * MIB_KB
    # The copied pages replaced pages the workers already mapped: their RSS did not move.
    assert episode["mean_worker_rss_growth_kb"] == 0


def test_a_new_allocation_grows_the_worker_rss_as_well():
    rows = [
        _sample(0, 0, 36 * GIB_KB, 38 * GIB_KB),
        _sample(1, 2, 36 * GIB_KB, 38 * GIB_KB + 2 * 700 * MIB_KB, worker_rss_kb=36 * GIB_KB),
        _sample(2, 2, 36 * GIB_KB, 38 * GIB_KB + 2 * 1100 * MIB_KB,
                worker_rss_kb=36 * GIB_KB + 400 * MIB_KB),
    ]
    (episode,) = summary.pool_memory_episodes(rows)

    assert episode["private_per_worker_growth_kb"] == 400 * MIB_KB
    assert episode["mean_worker_rss_growth_kb"] == 400 * MIB_KB


def test_each_pool_gets_the_baseline_of_the_sample_right_before_it():
    rows = [
        _sample(0, 0, 36 * GIB_KB, 38 * GIB_KB),
        _sample(1, 2, 36 * GIB_KB, 38 * GIB_KB + 2 * 700 * MIB_KB),
        _sample(2, 0, 30 * GIB_KB, 35 * GIB_KB),
        _sample(3, 3, 30 * GIB_KB, 35 * GIB_KB + 3 * 200 * MIB_KB),
    ]
    first, second = summary.pool_memory_episodes(rows)

    assert first["baseline_kb"] == 2 * GIB_KB
    assert first["peak_private_per_worker_kb"] == 700 * MIB_KB
    assert second["baseline_kb"] == 5 * GIB_KB
    assert second["peak_private_per_worker_kb"] == 200 * MIB_KB


def test_a_pool_that_dips_to_one_worker_between_batches_stays_one_episode():
    """The PopulationSim pattern: one batch finishes before the next starts, so for a sample only
    one worker (holding ~28 GiB) is alive. That sample must not become the next batch's baseline:
    it would hide the surviving worker's memory and understate the pool by half."""
    rows = [
        _sample(0, 0, 3 * GIB_KB, 5 * GIB_KB),
        _sample(1, 2, 3 * GIB_KB, 5 * GIB_KB + 2 * 28 * GIB_KB),
        _sample(2, 1, 3 * GIB_KB, 5 * GIB_KB + 28 * GIB_KB),
        _sample(3, 2, 3 * GIB_KB, 5 * GIB_KB + 2 * 29 * GIB_KB),
        _sample(4, 0, 3 * GIB_KB, 5 * GIB_KB),
    ]
    (episode,) = summary.pool_memory_episodes(rows)

    assert episode["baseline_kb"] == 2 * GIB_KB
    assert episode["max_workers"] == 2
    assert episode["peak_private_per_worker_kb"] == 29 * GIB_KB


def test_a_pool_running_from_the_first_sample_gets_no_invented_number():
    rows = _pool_rows()[1:]
    (episode,) = summary.pool_memory_episodes(rows)

    assert episode["baseline_kb"] is None
    assert episode["peak_private_per_worker_kb"] is None
    assert episode["measured_sample_count"] == 0


def test_multiprocessing_helper_processes_are_not_workers():
    """Both helpers stay alive from the stage that started them to the end of the run (the 100 %
    run of 2026-09-25 had both from PopulationSim on); counted as workers, they would leave no
    sample without a child, so no pool after them would ever get a baseline."""
    python = "/home/felix/miniforge3/envs/eqasim/bin/python"
    helpers = [
        {"pid": 998, "ppid": ROOT_PID, "tag": f"{python} -c from multiprocessing.resource_tracker "
         "import main;main(15)", "rss_kb": 8 * MIB_KB, "peak_rss_kb": 8 * MIB_KB, "cpu_seconds": 0.0},
        {"pid": 999, "ppid": ROOT_PID, "tag": f"{python} -c from multiprocessing.forkserver import "
         "main; main(20, 21, ['__main__'],", "rss_kb": 9 * MIB_KB, "peak_rss_kb": 9 * MIB_KB,
         "cpu_seconds": 0.0},
    ]
    rows = [
        _sample(0, 0, 36 * GIB_KB, 38 * GIB_KB, extra_children=helpers),
        _sample(1, 2, 36 * GIB_KB, 38 * GIB_KB + 2 * 700 * MIB_KB, extra_children=helpers),
    ]
    (episode,) = summary.pool_memory_episodes(rows)

    assert episode["max_workers"] == 2
    assert episode["baseline_kb"] == 2 * GIB_KB
    assert episode["peak_private_per_worker_kb"] == 700 * MIB_KB


def test_a_single_child_process_is_not_a_pool():
    rows = [_sample(0, 0, 36 * GIB_KB, 38 * GIB_KB), _sample(1, 1, 36 * GIB_KB, 60 * GIB_KB)]
    assert summary.pool_memory_episodes(rows) == []


def test_a_sample_with_unreadable_memory_is_skipped_rather_than_read_as_zero():
    rows = _pool_rows()
    rows[2] = _sample(2, 4, 36 * GIB_KB, 0, available_kb=None)
    (episode,) = summary.pool_memory_episodes(rows)

    assert episode["measured_sample_count"] == 1
    assert episode["peak_private_per_worker_kb"] == 700 * MIB_KB


def test_the_summary_reports_pools_per_stage_and_renders_them():
    rows = _pool_rows()
    record = summary.summarize(rows)

    (stage,) = [entry for entry in record["stages"] if entry["stage"] == "stage.pool"]
    (episode,) = stage["pool_memory"]
    assert episode["peak_private_per_worker_kb"] == 1100 * MIB_KB

    text = summary.render_markdown(record)
    assert "Worker pools" in text
    assert "`stage.pool`" in text
    assert "1.07 GiB private per worker" in text
