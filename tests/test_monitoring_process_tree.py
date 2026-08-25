"""Whole-process-tree resource primitives (issue #350).

Two production failures motivate these tests, both from the 2026-08-23/24 100 %
run recorded in ``docs/runs/``:

1. A healthy ``secondary_chainsolvers`` stage was declared deadlocked and killed
   because ``ps -C python --sort=-rss | head -6`` sampled six finished-by-design
   idle workers out of ~62 forked processes. A number summed over the WHOLE tree
   is one a busy worker cannot hide outside the window.
2. The peak memory per PopulationSim worker (the datum needed to tune
   ``num_workers``, issue #281) was unrecoverable once the workers had exited.
   ``VmHWM`` answers it exactly, but only while the process lives.

Everything is exercised against a fake ``/proc`` root (``tests/fake_proc.py``), so
the tests are deterministic and run on the Windows development machines too.
"""
from __future__ import annotations

import pytest

from braunschweig.monitoring import process_tree
from tests.fake_proc import CLOCK_TICKS_PER_SECOND, write_process, write_system


@pytest.fixture
def proc_root(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    write_system(root)
    return root


def test_tree_pids_include_transitive_children_but_not_unrelated_processes(proc_root):
    write_process(proc_root, pid=100, ppid=1)
    write_process(proc_root, pid=200, ppid=100)
    write_process(proc_root, pid=201, ppid=100)
    write_process(proc_root, pid=300, ppid=200)
    write_process(proc_root, pid=999, ppid=1)

    assert process_tree.iter_tree_pids(100, proc_root=str(proc_root)) == [100, 200, 201, 300]


def test_tree_cpu_seconds_sums_user_and_system_time_over_the_whole_tree(proc_root):
    write_process(proc_root, pid=100, ppid=1, utime_ticks=100, stime_ticks=50)
    write_process(proc_root, pid=200, ppid=100, utime_ticks=400, stime_ticks=0)

    sample = process_tree.sample_tree(100, proc_root=str(proc_root),
                                     clock_ticks_per_second=CLOCK_TICKS_PER_SECOND)

    assert sample.cpu_seconds == pytest.approx(5.5)
    assert sample.source == process_tree.SOURCE_PROC


def test_a_busy_child_of_an_idle_parent_is_visible_in_the_tree_total(proc_root):
    """The misdiagnosis case: the parent looks frozen, the tree is working."""
    write_process(proc_root, pid=100, ppid=1, utime_ticks=10, stime_ticks=0, state="S")
    write_process(proc_root, pid=200, ppid=100, utime_ticks=100000, stime_ticks=0)

    parent_only = process_tree.read_process_cpu_seconds(100, proc_root=str(proc_root),
                                                        clock_ticks_per_second=100)
    whole_tree = process_tree.read_tree_cpu_seconds(100, proc_root=str(proc_root),
                                                    clock_ticks_per_second=100)

    assert parent_only == pytest.approx(0.1)
    assert whole_tree == pytest.approx(1000.1)


def test_threads_count_towards_the_thread_total_not_the_process_count(proc_root):
    """Counting a process AND its threads double-counts CPU; issue #350 warns about it."""
    write_process(proc_root, pid=100, ppid=1, threads=8, utime_ticks=100)
    write_process(proc_root, pid=200, ppid=100, threads=64, utime_ticks=100)

    sample = process_tree.sample_tree(100, proc_root=str(proc_root),
                                      clock_ticks_per_second=100)

    assert sample.process_count == 2
    assert sample.thread_count == 72
    assert sample.cpu_seconds == pytest.approx(2.0)


def test_peak_rss_is_reported_even_when_current_rss_has_already_dropped(proc_root):
    """A PopulationSim worker past its peak still knows its high-water mark."""
    write_process(proc_root, pid=100, ppid=1, rss_kb=1_000_000, peak_rss_kb=29_900_000)

    sample = process_tree.sample_tree(100, proc_root=str(proc_root))
    process = sample.processes[0]

    assert process.rss_kb == 1_000_000
    assert process.peak_rss_kb == 29_900_000


def test_an_unreadable_memory_line_yields_none_not_zero(proc_root):
    """An absent VmHWM must never be reported as a measured 0 (no invented values)."""
    write_process(proc_root, pid=100, ppid=1, rss_kb=None, peak_rss_kb=None)

    process = process_tree.sample_tree(100, proc_root=str(proc_root)).processes[0]

    assert process.rss_kb is None
    assert process.peak_rss_kb is None


def test_a_process_that_disappears_mid_sample_is_skipped_without_failing(proc_root):
    """Sampling a live tree races with exiting workers; the survivors must still be reported."""
    write_process(proc_root, pid=100, ppid=1, utime_ticks=100)
    vanishing = write_process(proc_root, pid=200, ppid=100, utime_ticks=100)
    (vanishing / "stat").unlink()
    (vanishing / "status").unlink()

    sample = process_tree.sample_tree(100, proc_root=str(proc_root),
                                      clock_ticks_per_second=100)

    assert [process.pid for process in sample.processes] == [100]
    assert sample.cpu_seconds == pytest.approx(1.0)


def test_the_process_tag_carries_the_command_line_so_a_pid_can_be_attributed(proc_root):
    write_process(proc_root, pid=100, ppid=1, cmdline="python -m populationsim.run")

    process = process_tree.sample_tree(100, proc_root=str(proc_root)).processes[0]

    assert "populationsim" in process.tag


def test_per_process_io_bytes_are_read_when_the_kernel_exposes_them(proc_root):
    write_process(proc_root, pid=100, ppid=1, read_bytes=4096, write_bytes=8192)

    process = process_tree.sample_tree(100, proc_root=str(proc_root)).processes[0]

    assert process.read_bytes == 4096
    assert process.write_bytes == 8192


def test_an_absent_proc_root_reports_no_cpu_signal_instead_of_no_progress(tmp_path):
    """No silent fallback: an unmeasurable tree says so, it never claims 0 CPU."""
    missing = tmp_path / "no-proc"

    sample = process_tree.sample_tree(100, proc_root=str(missing), allow_psutil=False)

    assert sample.cpu_seconds is None
    assert sample.source.startswith(process_tree.SOURCE_UNAVAILABLE)
    assert sample.processes == []


def test_psutil_is_used_as_a_named_fallback_when_proc_is_absent(tmp_path):
    """Windows development machines have no /proc; the source string must say so."""
    pytest.importorskip("psutil")
    import os

    sample = process_tree.sample_tree(os.getpid(), proc_root=str(tmp_path / "no-proc"))

    assert sample.source == process_tree.SOURCE_PSUTIL
    assert sample.cpu_seconds is not None and sample.cpu_seconds >= 0.0
    assert sample.process_count >= 1


def test_tree_cpu_of_a_childless_process_equals_its_own_cpu(proc_root):
    """Pins the watchdog-equivalence: swapping in the tree reader cannot change a
    single-process verdict (matsim/runtime/process_watchdog.py)."""
    write_process(proc_root, pid=4242, ppid=1, utime_ticks=700, stime_ticks=300)

    own = process_tree.read_process_cpu_seconds(4242, proc_root=str(proc_root),
                                                clock_ticks_per_second=100)
    tree = process_tree.read_tree_cpu_seconds(4242, proc_root=str(proc_root),
                                              clock_ticks_per_second=100)

    assert own == tree == pytest.approx(10.0)
