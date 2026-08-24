"""Reduction of a recorded series to the fields a run manifest wants (issue #350).

The acceptance criterion of issue #350 is that after a run one can state, without
further measurement: the peak per-worker RSS during balancing, whether a silent
phase was working, and the wall/CPU split per phase. That is exactly what
:func:`braunschweig.monitoring.summary.summarize` must produce.

One subtlety drives several tests here: the tree CPU total is the sum over the
processes ALIVE at that instant, so it is NOT monotonic -- when a PopulationSim
worker exits, its accumulated CPU time leaves the sum. Summing naive between-sample
differences would therefore lose every exited worker's work, and a drop would look
like a stall. The summary accounts per pid where the series carries process rows,
and says which accounting it used.
"""
from __future__ import annotations

import json

import pytest

from braunschweig.monitoring import summary


def _row(index, unix_time, stage="stage.a", tree_cpu=0.0, processes=None,
         process_count=None, thread_count=8, memory_available_kb=60_000_000,
         memory_used_kb=70_000_000, swap_used_kb=0, log_size_bytes=1000,
         disk_read_bytes=0, disk_write_bytes=0, oom_kill_count=0, segfault_count=0,
         filesystems=None, source="proc"):
    """One recorded sample, with only the fields the summary reads."""
    row = {
        "sample_index": index,
        "timestamp": "2026-08-24T07:%02d:00" % index,
        "unix_time": unix_time,
        "stage": stage,
        "tree_cpu_seconds": tree_cpu,
        "process_count": len(processes) if processes is not None and process_count is None
                         else (process_count if process_count is not None else 1),
        "thread_count": thread_count,
        "memory_available_kb": memory_available_kb,
        "memory_used_kb": memory_used_kb,
        "swap_used_kb": swap_used_kb,
        "log_size_bytes": log_size_bytes,
        "disk_read_bytes": disk_read_bytes,
        "disk_write_bytes": disk_write_bytes,
        "oom_kill_count": oom_kill_count,
        "segfault_count": segfault_count,
        "cpu_count": 64,
        "source": source,
        "filesystems": filesystems if filesystems is not None else
        [{"path": "/home", "free_bytes": 500_000_000_000, "total_bytes": 1_000_000_000_000}],
    }
    if processes is not None:
        row["processes"] = processes
        row["peak_process_rss_kb"] = max(
            [p["peak_rss_kb"] for p in processes if p.get("peak_rss_kb") is not None],
            default=None)
        peak = max((p for p in processes if p.get("peak_rss_kb") is not None),
                   key=lambda p: p["peak_rss_kb"], default=None)
        row["peak_process_rss_pid"] = None if peak is None else peak["pid"]
        row["peak_process_rss_tag"] = None if peak is None else peak["tag"]
    return row


def _process(pid, cpu_seconds, peak_rss_kb, tag="python -m populationsim"):
    return {"pid": pid, "ppid": 1, "state": "R", "tag": tag,
            "cpu_seconds": cpu_seconds, "rss_kb": peak_rss_kb,
            "peak_rss_kb": peak_rss_kb, "thread_count": 1}


def test_peak_worker_memory_reports_the_value_the_pid_and_the_tag(tmp_path):
    rows = [
        _row(0, 1000.0, processes=[_process(200, 10.0, 20_000_000)]),
        _row(1, 1030.0, processes=[_process(200, 20.0, 29_900_000,
                                            tag="python -m populationsim.run shard7")]),
    ]

    result = summary.summarize(rows)

    assert result["peak_process_rss_kb"] == 29_900_000
    assert result["peak_process_rss_pid"] == 200
    assert "populationsim" in result["peak_process_rss_tag"]


def test_system_memory_extremes_come_from_the_whole_series(tmp_path):
    rows = [
        _row(0, 1000.0, memory_available_kb=60_000_000, memory_used_kb=70_000_000),
        _row(1, 1030.0, memory_available_kb=2_000_000, memory_used_kb=128_000_000,
             swap_used_kb=4_000_000),
    ]

    result = summary.summarize(rows)

    assert result["min_memory_available_kb"] == 2_000_000
    assert result["peak_memory_used_kb"] == 128_000_000
    assert result["peak_swap_used_kb"] == 4_000_000


def test_wall_and_cpu_are_reported_per_stage(tmp_path):
    rows = [
        _row(0, 0.0, stage="stage.a", processes=[_process(100, 0.0, 1000)]),
        _row(1, 100.0, stage="stage.a", processes=[_process(100, 60.0, 1000)]),
        _row(2, 200.0, stage="stage.b", processes=[_process(100, 60.0, 1000)]),
        _row(3, 300.0, stage="stage.b", processes=[_process(100, 260.0, 1000)]),
    ]

    stages = {stage["stage"]: stage for stage in summary.summarize(rows)["stages"]}

    assert stages["stage.a"]["wall_seconds"] == pytest.approx(100.0)
    assert stages["stage.a"]["cpu_seconds"] == pytest.approx(60.0)
    assert stages["stage.b"]["wall_seconds"] == pytest.approx(100.0)
    assert stages["stage.b"]["cpu_seconds"] == pytest.approx(200.0)


def test_cpu_efficiency_is_cpu_seconds_over_wall_clock_times_cpu_count(tmp_path):
    """The number that says whether a parallel stage actually used the box."""
    rows = [
        _row(0, 0.0, processes=[_process(100, 0.0, 1000)]),
        _row(1, 100.0, processes=[_process(100, 3200.0, 1000)]),
    ]

    stage = summary.summarize(rows)["stages"][0]

    # 3200 CPU seconds in 100 s wall clock on 64 cores = half the machine.
    assert stage["cpu_efficiency"] == pytest.approx(0.5)


def test_cpu_of_a_worker_that_exits_is_kept_by_per_pid_accounting(tmp_path):
    """A naive tree difference would report NEGATIVE work when a worker exits."""
    rows = [
        _row(0, 0.0, processes=[_process(100, 10.0, 1000), _process(200, 500.0, 1000)]),
        _row(1, 100.0, processes=[_process(100, 20.0, 1000)]),
    ]

    result = summary.summarize(rows)

    assert result["cpu_accounting"] == summary.CPU_ACCOUNTING_PER_PID
    assert result["cpu_seconds"] == pytest.approx(10.0)
    assert result["stages"][0]["cpu_seconds"] == pytest.approx(10.0)


def test_a_series_without_process_rows_falls_back_to_tree_deltas_and_names_it(tmp_path):
    rows = [
        _row(0, 0.0, tree_cpu=100.0),
        _row(1, 100.0, tree_cpu=400.0),
        _row(2, 200.0, tree_cpu=350.0),
    ]

    result = summary.summarize(rows)

    assert result["cpu_accounting"] == summary.CPU_ACCOUNTING_TREE_DELTA
    # Only the positive increment is counted; the drop is a worker exiting, not
    # negative work, and the summary must say the number is a lower bound.
    assert result["cpu_seconds"] == pytest.approx(300.0)


def test_a_series_without_any_cpu_signal_reports_no_cpu_rather_than_zero(tmp_path):
    """Found by the first end-to-end smoke: an unreadable tree reported 0 CPU seconds,
    which reads as "the machine did nothing" instead of "this was not measurable"."""
    rows = [_row(0, 0.0, tree_cpu=None, processes=[], source="unavailable:no_proc"),
            _row(1, 100.0, tree_cpu=None, processes=[], source="unavailable:no_proc")]

    result = summary.summarize(rows)

    assert result["cpu_accounting"] == summary.CPU_ACCOUNTING_NONE
    assert result["cpu_seconds"] is None
    assert result["cpu_efficiency"] is None
    assert result["stages"][0]["cpu_seconds"] is None


def test_an_unmeasurable_series_is_not_reported_as_a_stall(tmp_path):
    """"Unmeasurable" and "not working" are different statements; only one may be a stall."""
    rows = [_row(index, index * 600.0, tree_cpu=None, processes=[])
            for index in range(5)]

    assert summary.summarize(rows, stall_min_seconds=600.0)["stalls"] == []


def test_a_stall_is_reported_when_neither_cpu_nor_the_log_advanced(tmp_path):
    rows = [
        _row(index, index * 600.0, stage="stage.stuck",
             processes=[_process(100, 50.0, 1000)], log_size_bytes=4096)
        for index in range(5)
    ]

    stalls = summary.summarize(rows, stall_min_seconds=600.0)["stalls"]

    assert len(stalls) == 1
    assert stalls[0]["stage"] == "stage.stuck"
    assert stalls[0]["seconds"] == pytest.approx(2400.0)


def test_a_finished_worker_pool_is_not_reported_as_a_stall(tmp_path):
    """The misdiagnosis case: idle-by-design workers while the log keeps moving."""
    rows = [
        _row(0, 0.0, processes=[_process(100, 50.0, 1000), _process(200, 90.0, 1000)],
             log_size_bytes=4096),
        _row(1, 600.0, processes=[_process(100, 50.0, 1000)], log_size_bytes=8192),
        _row(2, 1200.0, processes=[_process(100, 50.0, 1000)], log_size_bytes=9000),
    ]

    assert summary.summarize(rows, stall_min_seconds=600.0)["stalls"] == []


def test_disk_and_kernel_event_counters_are_reported_as_differences(tmp_path):
    rows = [
        _row(0, 0.0, disk_read_bytes=1_000, disk_write_bytes=2_000,
             oom_kill_count=3, segfault_count=1),
        _row(1, 100.0, disk_read_bytes=5_000, disk_write_bytes=9_000,
             oom_kill_count=10, segfault_count=1),
    ]

    result = summary.summarize(rows)

    assert result["disk_read_bytes"] == 4_000
    assert result["disk_write_bytes"] == 7_000
    assert result["oom_kill_count"] == 7
    assert result["segfault_count"] == 0


def test_a_counter_that_wrapped_or_reset_is_reported_as_unknown(tmp_path):
    """The dmesg ring buffer wraps and disk counters reset; a negative difference is
    not a negative event count, so it must be reported as unknown."""
    rows = [_row(0, 0.0, oom_kill_count=10, disk_read_bytes=5_000),
            _row(1, 100.0, oom_kill_count=2, disk_read_bytes=1_000)]

    result = summary.summarize(rows)

    assert result["oom_kill_count"] is None
    assert result["disk_read_bytes"] is None


def test_unreadable_kernel_counters_stay_unknown_rather_than_zero(tmp_path):
    rows = [_row(0, 0.0, oom_kill_count=None), _row(1, 100.0, oom_kill_count=None)]

    assert summary.summarize(rows)["oom_kill_count"] is None


def test_minimum_free_space_is_reported_per_filesystem(tmp_path):
    rows = [
        _row(0, 0.0, filesystems=[{"path": "/home", "free_bytes": 900,
                                   "total_bytes": 1000}]),
        _row(1, 100.0, filesystems=[{"path": "/home", "free_bytes": 100,
                                     "total_bytes": 1000}]),
    ]

    filesystems = summary.summarize(rows)["filesystems"]

    assert filesystems == [{"path": "/home", "min_free_bytes": 100,
                            "total_bytes": 1000}]


def test_an_empty_series_reports_no_samples_without_inventing_values(tmp_path):
    result = summary.summarize([])

    assert result["sample_count"] == 0
    assert result["peak_process_rss_kb"] is None
    assert result["stages"] == []
    assert result["wall_seconds"] is None


def test_the_rendered_markdown_states_the_fields_a_run_manifest_needs(tmp_path):
    # 31_352_320 kB is 29.9 GiB: /proc reports kB as 1024-byte units, so the
    # rendered figure must be binary GiB, not decimal GB (a 7 % difference).
    rows = [
        _row(0, 0.0, stage="stage.a", processes=[_process(100, 0.0, 20_000_000)]),
        _row(1, 100.0, stage="stage.a", processes=[_process(100, 3200.0, 31_352_320)]),
    ]

    text = summary.render_markdown(summary.summarize(rows))

    assert "peak process RSS" in text
    assert "29.9 GiB" in text  # the per-worker peak issue #281 needs
    assert "stage.a" in text


def test_a_truncated_last_line_of_a_killed_run_is_skipped(tmp_path):
    series = tmp_path / "series.jsonl"
    with series.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_row(0, 0.0)) + "\n")
        handle.write('{"sample_index": 1, "unix_ti')  # the kill landed mid-write

    rows = summary.load_series(str(series))

    assert len(rows) == 1
    assert rows[0]["sample_index"] == 0


def test_writing_the_summary_produces_both_a_json_and_a_markdown_artifact(tmp_path):
    series = tmp_path / "series.jsonl"
    with series.open("w", encoding="utf-8") as handle:
        for index, cpu in enumerate((0.0, 100.0)):
            handle.write(json.dumps(
                _row(index, index * 100.0,
                     processes=[_process(100, cpu, 1_000_000)])) + "\n")

    result = summary.write_summary(str(series))

    assert (tmp_path / "series.summary.json").exists()
    assert (tmp_path / "series.summary.md").exists()
    assert result["sample_count"] == 2
