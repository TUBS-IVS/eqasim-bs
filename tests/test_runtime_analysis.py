"""Tests for the per-stage synpp runtime parser.

Parses a timestamped pipeline log into per-stage wall-clock durations so we can
see which synpp stages dominate the run (e.g. location choice) and tune settings.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis.runtime import parse_stage_runtimes  # noqa: E402

LOG = """\
2026-06-05T08:00:00 INFO synpp Executing stage braunschweig.gravity.model__abc123 ...
2026-06-05T08:00:00 INFO synpp some inner line
2026-06-05T08:02:30 INFO synpp Finished running braunschweig.gravity.model__abc123.
2026-06-05T08:02:30 INFO synpp Executing stage synthesis.population.spatial.secondary__def456 ...
2026-06-05T08:12:30 INFO synpp Finished running synthesis.population.spatial.secondary__def456.
2026-06-05T08:12:30 INFO synpp Loading cache for braunschweig.data.mid.data__cached0 ...
"""


def test_parses_durations_per_executed_stage():
    df = parse_stage_runtimes(LOG)
    by = dict(zip(df["stage_short"], df["duration_s"]))
    assert by["braunschweig.gravity.model"] == 150.0          # 2m30s
    assert by["synthesis.population.spatial.secondary"] == 600.0  # 10m
    # cached stage (only "Loading cache") is not an executed stage -> not timed
    assert "braunschweig.data.mid.data" not in by


def test_sorted_by_duration_descending():
    df = parse_stage_runtimes(LOG)
    assert list(df["duration_s"]) == sorted(df["duration_s"], reverse=True)
    assert df.iloc[0]["stage_short"] == "synthesis.population.spatial.secondary"


def test_strips_hash_suffix_but_keeps_full_stage():
    df = parse_stage_runtimes(LOG)
    row = df[df["stage_short"] == "braunschweig.gravity.model"].iloc[0]
    assert row["stage"].endswith("__abc123")


def test_empty_log_returns_empty_frame():
    df = parse_stage_runtimes("no stages here\n")
    assert len(df) == 0
    assert list(df.columns) == ["stage", "stage_short", "start", "end", "duration_s"]
