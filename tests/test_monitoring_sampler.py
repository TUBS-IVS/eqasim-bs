"""One resource sample: process tree + system state + liveness of the work (issue #350).

Every field here answers a failure class this project has actually hit on the run
server, so a series of these samples answers hours later what a hand-taken
snapshot could not:

- RAM / swap: the kernel OOM killer removed 7 processes (20-30 GB anon-rss each)
  during the 2026-08-20 run; swap growth is the signal that precedes it.
- thread total and segfault count: ~4000 oversubscribed threads produced libc
  segfaults and cost 12 PopulationSim batches (``braunschweig/parallelism.py``).
- free disk space: ENOSPC is a documented failure class of this pipeline.
- log size / mtime age plus the current stage: tells whether a silent phase is
  working, and attributes every sample to the stage that produced it.

Values that cannot be read are ``None`` and the source string says so -- an
unreadable counter must never look like a measured zero.
"""
from __future__ import annotations

import pytest

from braunschweig.monitoring import sampler
from tests.fake_proc import write_process, write_system


@pytest.fixture
def proc_root(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    write_system(root, mem_total_kb=131_072_000, mem_available_kb=65_536_000,
                 mem_free_kb=32_768_000, swap_total_kb=8_192_000, swap_free_kb=6_144_000,
                 load=(12.5, 8.0, 4.0), cpu_count=64,
                 sectors_read=1000, sectors_written=2000)
    write_process(root, pid=100, ppid=1, utime_ticks=100, stime_ticks=0,
                  threads=4, rss_kb=1_000_000, peak_rss_kb=2_000_000,
                  cmdline="python -m synpp")
    return root


def test_memory_and_swap_are_read_from_meminfo(proc_root):
    memory = sampler.read_memory(proc_root=str(proc_root))

    assert memory["memory_total_kb"] == 131_072_000
    assert memory["memory_available_kb"] == 65_536_000
    assert memory["memory_used_kb"] == 131_072_000 - 32_768_000
    assert memory["swap_used_kb"] == 8_192_000 - 6_144_000


def test_absent_meminfo_reports_none_rather_than_zero(tmp_path):
    memory = sampler.read_memory(proc_root=str(tmp_path / "no-proc"))

    assert memory["memory_total_kb"] is None
    assert memory["memory_available_kb"] is None


def test_load_average_and_cpu_count_are_read_from_proc(proc_root):
    load = sampler.read_load(proc_root=str(proc_root))

    assert load["load_average_1min"] == pytest.approx(12.5)
    assert load["cpu_count"] == 64


def test_disk_io_bytes_convert_diskstats_sectors_at_512_bytes(proc_root):
    disk_io = sampler.read_disk_io(proc_root=str(proc_root))

    assert disk_io["disk_read_bytes"] == 1000 * 512
    assert disk_io["disk_write_bytes"] == 2000 * 512


def test_filesystem_free_space_is_reported_per_configured_path(tmp_path):
    filesystems = sampler.read_filesystems([str(tmp_path)])

    assert filesystems[0]["path"] == str(tmp_path)
    assert filesystems[0]["free_bytes"] > 0
    assert filesystems[0]["total_bytes"] >= filesystems[0]["free_bytes"]


def test_an_unreadable_filesystem_path_is_reported_as_such(tmp_path):
    filesystems = sampler.read_filesystems([str(tmp_path / "does-not-exist")])

    assert filesystems[0]["free_bytes"] is None


def test_kernel_events_count_oom_kills_and_segfaults(proc_root):
    dmesg = (
        "[12345.6] Out of memory: Killed process 4242 (populationsim) total-vm:...\n"
        "[12399.9] Out of memory: Killed process 4243 (populationsim) total-vm:...\n"
        "[12400.1] populationsim[4321]: segfault at 0 ip 00007f rip error 4\n"
    )

    events = sampler.read_kernel_events(dmesg_reader=lambda: dmesg)

    assert events["oom_kill_count"] == 2
    assert events["segfault_count"] == 1
    assert events["source"] == sampler.SOURCE_DMESG


def test_unavailable_dmesg_reports_no_counts_instead_of_zero_counts():
    """Zero OOM kills and "cannot read the kernel log" are different statements."""
    events = sampler.read_kernel_events(dmesg_reader=lambda: None)

    assert events["oom_kill_count"] is None
    assert events["segfault_count"] is None
    assert events["source"].startswith(sampler.SOURCE_UNAVAILABLE)


def test_log_tail_reports_the_stage_the_pipeline_is_currently_executing(tmp_path):
    log_path = tmp_path / "run.log"
    log_path.write_text(
        "2026-08-24T07:00:00 INFO synpp Executing stage braunschweig.data.first\n"
        "2026-08-24T07:10:00 INFO synpp Finished running braunschweig.data.first.\n"
        "2026-08-24T07:10:01 INFO synpp Executing stage synthesis.locations.secondary\n",
        encoding="utf-8")

    state = sampler.LogTail(str(log_path)).poll()

    assert state["stage"] == "synthesis.locations.secondary"
    assert state["stage_finished"] is False
    assert state["log_size_bytes"] == log_path.stat().st_size


def test_log_tail_marks_a_stage_that_has_finished(tmp_path):
    log_path = tmp_path / "run.log"
    log_path.write_text(
        "2026-08-24T07:00:00 INFO synpp Executing stage braunschweig.data.first\n"
        "2026-08-24T07:10:00 INFO synpp Finished running braunschweig.data.first.\n",
        encoding="utf-8")

    state = sampler.LogTail(str(log_path)).poll()

    assert state["stage"] == "braunschweig.data.first"
    assert state["stage_finished"] is True


def test_log_tail_reads_only_the_bytes_appended_since_the_previous_poll(tmp_path):
    """A 100 % run log grows to hundreds of MB; re-reading it every 30 s is not an option."""
    log_path = tmp_path / "run.log"
    log_path.write_text("Executing stage first\n", encoding="utf-8")
    tail = sampler.LogTail(str(log_path))
    tail.poll()
    bytes_after_first_poll = tail.bytes_read_total
    size_before_append = log_path.stat().st_size

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("Executing stage second\n")
    state = tail.poll()

    # Compared against the on-disk growth, not against the string length: the line
    # separator is platform-dependent, the appended byte count is not.
    appended_bytes = log_path.stat().st_size - size_before_append
    assert state["stage"] == "second"
    assert tail.bytes_read_total - bytes_after_first_poll == appended_bytes


def test_log_tail_recovers_when_the_log_is_truncated_or_rotated(tmp_path):
    log_path = tmp_path / "run.log"
    log_path.write_text("Executing stage first\n" * 10, encoding="utf-8")
    tail = sampler.LogTail(str(log_path))
    tail.poll()

    log_path.write_text("Executing stage after_rotation\n", encoding="utf-8")
    state = tail.poll()

    assert state["stage"] == "after_rotation"


def test_a_missing_log_is_reported_without_inventing_an_age(tmp_path):
    state = sampler.LogTail(str(tmp_path / "absent.log")).poll()

    assert state["log_size_bytes"] is None
    assert state["log_mtime_age_seconds"] is None
    assert state["stage"] is None


def test_one_sample_carries_the_tree_the_system_state_and_the_stage(proc_root, tmp_path):
    log_path = tmp_path / "run.log"
    log_path.write_text("Executing stage braunschweig.popsim.batch\n", encoding="utf-8")
    resource_sampler = sampler.ResourceSampler(
        root_pid=100, proc_root=str(proc_root), log_path=str(log_path),
        filesystem_paths=[str(tmp_path)], dmesg_reader=lambda: "",
        clock_ticks_per_second=100)

    row = resource_sampler.sample()

    assert row["stage"] == "braunschweig.popsim.batch"
    assert row["tree_cpu_seconds"] == pytest.approx(1.0)
    assert row["process_count"] == 1
    assert row["thread_count"] == 4
    assert row["peak_process_rss_kb"] == 2_000_000
    assert row["memory_available_kb"] == 65_536_000
    assert row["cpu_count"] == 64
    assert row["source"] == "proc"
    assert row["processes"][0]["tag"].endswith("synpp")
    assert row["timestamp"]


def test_successive_samples_carry_an_increasing_index(proc_root):
    resource_sampler = sampler.ResourceSampler(root_pid=100, proc_root=str(proc_root),
                                               dmesg_reader=lambda: "")

    first = resource_sampler.sample()
    second = resource_sampler.sample()

    assert (first["sample_index"], second["sample_index"]) == (0, 1)


def test_per_process_rows_can_be_switched_off_to_keep_a_long_series_small(proc_root):
    resource_sampler = sampler.ResourceSampler(root_pid=100, proc_root=str(proc_root),
                                               dmesg_reader=lambda: "",
                                               include_process_rows=False)

    row = resource_sampler.sample()

    assert "processes" not in row
    assert row["peak_process_rss_kb"] == 2_000_000
