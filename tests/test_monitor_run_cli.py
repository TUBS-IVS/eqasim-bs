"""Tests for the scripts/monitor_run.py CLI (issue #350).

The pipeline records itself, but the standalone entry point is what answers a
question about a run that is ALREADY in flight (attach to its pid) and what turns
a finished series into the block a run manifest wants (``summarize``).
"""
from __future__ import annotations

import importlib.util
import json
import os

from tests.fake_proc import write_process, write_system

_CLI_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "monitor_run.py")


def _load_cli():
    spec = importlib.util.spec_from_file_location("monitor_run_cli", _CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_series(path):
    rows = [
        {"sample_index": 0, "timestamp": "2026-08-24T07:00:00", "unix_time": 0.0,
         "stage": "stage.a", "tree_cpu_seconds": 0.0, "process_count": 1,
         "cpu_count": 64, "source": "proc", "memory_used_kb": 1000,
         "memory_available_kb": 2000, "log_size_bytes": 10, "filesystems": []},
        {"sample_index": 1, "timestamp": "2026-08-24T07:00:30", "unix_time": 30.0,
         "stage": "stage.a", "tree_cpu_seconds": 900.0, "process_count": 4,
         "cpu_count": 64, "source": "proc", "memory_used_kb": 4000,
         "memory_available_kb": 500, "log_size_bytes": 4096, "filesystems": []},
    ]
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_summarize_writes_the_two_artifacts_next_to_the_series(tmp_path):
    series = tmp_path / "resource_series_20260824T070000.jsonl"
    _write_series(series)

    exit_code = _load_cli().main(["summarize", str(series)])

    assert exit_code == 0
    assert (tmp_path / "resource_series_20260824T070000.summary.json").exists()
    assert (tmp_path / "resource_series_20260824T070000.summary.md").exists()


def test_summarize_prints_the_markdown_block_for_a_run_manifest(tmp_path, capsys):
    series = tmp_path / "series.jsonl"
    _write_series(series)

    _load_cli().main(["summarize", str(series)])

    assert "Resource record" in capsys.readouterr().out


def test_summarize_reports_a_missing_series_as_an_error_not_a_traceback(tmp_path):
    exit_code = _load_cli().main(["summarize", str(tmp_path / "absent.jsonl")])

    assert exit_code == 1


def test_record_requires_either_a_pid_or_a_command_pattern(tmp_path):
    exit_code = _load_cli().main(["record", "--out", str(tmp_path / "series.jsonl")])

    assert exit_code == 2


def test_record_resolves_the_pid_from_a_command_line_pattern(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    write_system(proc_root)
    write_process(proc_root, pid=100, ppid=1, cmdline="bash -c something")
    write_process(proc_root, pid=4242, ppid=1, cmdline="python -m populationsim.run")

    resolved = _load_cli().resolve_pid_by_pattern("populationsim",
                                                  proc_root=str(proc_root))

    assert resolved == 4242


def test_an_unmatched_pattern_resolves_to_nothing_rather_than_to_a_guess(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    write_system(proc_root)
    write_process(proc_root, pid=100, ppid=1, cmdline="bash -c something")

    assert _load_cli().resolve_pid_by_pattern("populationsim",
                                              proc_root=str(proc_root)) is None


def test_record_writes_one_sample_and_stops_at_the_configured_duration(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    write_system(proc_root)
    write_process(proc_root, pid=100, ppid=1, utime_ticks=100, rss_kb=1000,
                  peak_rss_kb=2000)
    series = tmp_path / "series.jsonl"

    exit_code = _load_cli().main([
        "record", "--pid", "100", "--proc-root", str(proc_root),
        "--out", str(series), "--interval-seconds", "0", "--duration-seconds", "0",
        "--no-kernel-events",
    ])

    assert exit_code == 0
    lines = series.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["tree_cpu_seconds"] is not None
    assert (tmp_path / "series.summary.md").exists()
