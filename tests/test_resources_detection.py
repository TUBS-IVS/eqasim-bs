"""Tests for machine detection in braunschweig.resources.

Detection must never read the real machine in a test: every case injects its
own reader callables, so the suite is deterministic on Linux, macOS and Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402


@pytest.mark.parametrize("text,expected_gb", [
    ("100G", 100.0),
    ("100g", 100.0),
    ("512M", 0.5),
    ("2T", 2048.0),
    ("94", 94.0),
    (94, 94.0),
    (94.5, 94.5),
])
def test_parse_memory_gb_accepts_the_java_memory_spellings(text, expected_gb):
    assert resources.parse_memory_gb(text) == pytest.approx(expected_gb)


def test_parse_memory_gb_rejects_nonsense():
    with pytest.raises(ValueError):
        resources.parse_memory_gb("plenty")


def test_parse_memory_gb_rejects_a_lone_b_suffix():
    # "5B" has no size letter (K/M/G/T); bytes are not a supported unit, and the
    # previous behaviour silently treated a lone "B" as gigabytes (the "no
    # suffix" default), which is exactly the silent misinterpretation this
    # rejection replaces.
    with pytest.raises(ValueError, match="lone 'B'"):
        resources.parse_memory_gb("5B")
    with pytest.raises(ValueError, match="lone 'B'"):
        resources.parse_memory_gb("5b")


def test_parse_memory_gb_still_accepts_a_size_letter_plus_b():
    assert resources.parse_memory_gb("100GB") == pytest.approx(100.0)
    assert resources.parse_memory_gb("512MB") == pytest.approx(0.5)


def test_format_memory_gb_rounds_down_to_whole_gigabytes():
    # Rounding UP could hand the JVM more heap than the machine has.
    assert resources.format_memory_gb(86.9) == "86G"
    assert resources.format_memory_gb(0.4) == "1G"  # never below 1G


def test_detect_cores_prefers_affinity_over_cpu_count():
    cores, source = resources.detect_cores(
        affinity_reader=lambda: 8, cpu_count_reader=lambda: 64,
    )
    assert cores == 8
    assert source == "sched_getaffinity"


def test_detect_cores_falls_back_to_cpu_count_and_says_so():
    cores, source = resources.detect_cores(
        affinity_reader=None, cpu_count_reader=lambda: 64,
    )
    assert cores == 64
    assert source == "cpu_count"


def test_detect_cores_raises_when_nothing_reports_cores():
    with pytest.raises(resources.ResourceDetectionError):
        resources.detect_cores(affinity_reader=None, cpu_count_reader=lambda: None)


def test_detect_memory_prefers_psutil():
    memory_gb, source = resources.detect_memory_gb(
        psutil_reader=lambda: 94.0, meminfo_reader=lambda: 128.0,
    )
    assert memory_gb == pytest.approx(94.0)
    assert source == "psutil"


def test_detect_memory_falls_back_to_meminfo_and_says_so():
    memory_gb, source = resources.detect_memory_gb(
        psutil_reader=None, meminfo_reader=lambda: 128.0,
    )
    assert memory_gb == pytest.approx(128.0)
    assert source == "proc_meminfo"


def test_detect_memory_raises_instead_of_defaulting():
    # CLAUDE.md: no silent fallbacks. An undetectable machine must fail loudly,
    # because a guessed RAM figure would silently size every run wrongly.
    with pytest.raises(resources.ResourceDetectionError):
        resources.detect_memory_gb(psutil_reader=None, meminfo_reader=None)


def test_detect_machine_records_both_sources():
    machine = resources.detect_machine(
        affinity_reader=lambda: 64,
        cpu_count_reader=lambda: 64,
        psutil_reader=lambda: 94.0,
        meminfo_reader=None,
    )
    assert machine.cores == 64
    assert machine.memory_gb == pytest.approx(94.0)
    assert machine.cores_source == "sched_getaffinity"
    assert machine.memory_source == "psutil"


def test_detect_machine_rejects_a_misspelled_reader_argument():
    # A typo'd injection must not silently fall through to the real machine.
    with pytest.raises(TypeError):
        resources.detect_machine(afinity_reader=lambda: 999)


# ---------------------------------------------------------------------------
# current_process_rss_gb (ADR-0126 amendment): the chainsolver worker pool's
# memory bound needs THIS process's own RSS, read with the same
# primary/fallback discipline as detect_cores / detect_memory_gb above.
# ---------------------------------------------------------------------------

def test_current_process_rss_gb_prefers_psutil():
    rss_gb = resources.current_process_rss_gb(
        psutil_reader=lambda: 12.5, status_reader=lambda: 99.0,
    )
    assert rss_gb == pytest.approx(12.5)


def test_current_process_rss_gb_falls_back_to_proc_self_status_and_says_so():
    rss_gb = resources.current_process_rss_gb(
        psutil_reader=None, status_reader=lambda: 8.0,
    )
    assert rss_gb == pytest.approx(8.0)


def test_current_process_rss_gb_returns_none_when_neither_source_is_available():
    assert resources.current_process_rss_gb(psutil_reader=None, status_reader=None) is None
