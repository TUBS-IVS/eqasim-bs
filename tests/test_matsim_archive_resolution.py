# tests/test_matsim_archive_resolution.py
"""Resolution of MATSim simulation outputs without a stage dependency (#354).

Covers three layers:
  * ``braunschweig.analysis.matsim_archive.resolve_matsim_archive`` -- the
    config-derived lookup of the ``<output_path>/matsim_output`` archive that
    ``matsim.output`` writes (``archive_matsim_output``, ADR-0064).
  * ``run_metrics._find_sim_output`` -- must accept BOTH the historical synpp
    cache-root layout (``matsim.simulation.run__*.cache/simulation_output/``)
    and a directory that itself IS a simulation output (the archive).
  * ``run_mid_validation._find_sim_trips`` -- same dual layout for
    ``eqasim_trips.csv``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# resolve_matsim_archive
# ---------------------------------------------------------------------------

def _write_archive(output_path, with_info=True):
    archive = output_path / "matsim_output"
    archive.mkdir(parents=True)
    (archive / "output_events.xml.gz").write_bytes(b"")
    if with_info:
        (archive / "ARCHIVE_INFO.json").write_text(json.dumps({
            "source_hash_dir": "cache/matsim.simulation.run__abc.cache",
            "created": "2026-09-03T00:00:00+00:00",
            "file_count": 1, "hardlink_count": 1, "copy_count": 0,
        }))
    return archive


def test_resolve_returns_archive_when_complete(tmp_path):
    from braunschweig.analysis.matsim_archive import resolve_matsim_archive
    archive = _write_archive(tmp_path)
    assert resolve_matsim_archive(tmp_path) == archive


def test_resolve_works_without_archive_info(tmp_path):
    # ARCHIVE_INFO.json is provenance, not a gate: archives written before it
    # existed must still resolve.
    from braunschweig.analysis.matsim_archive import resolve_matsim_archive
    archive = _write_archive(tmp_path, with_info=False)
    assert resolve_matsim_archive(tmp_path) == archive


def test_resolve_returns_none_when_directory_absent(tmp_path):
    from braunschweig.analysis.matsim_archive import resolve_matsim_archive
    assert resolve_matsim_archive(tmp_path) is None


def test_resolve_returns_none_when_sentinel_missing(tmp_path):
    # A directory without output_events.xml.gz is a half-written archive
    # (matsim.output asserts the sentinel); treat it as absent, never consume it.
    from braunschweig.analysis.matsim_archive import resolve_matsim_archive
    (tmp_path / "matsim_output").mkdir()
    assert resolve_matsim_archive(tmp_path) is None


def test_missing_reason_names_the_expected_path(tmp_path):
    from braunschweig.analysis.matsim_archive import archive_missing_reason
    reason = archive_missing_reason(tmp_path)
    assert "no MATSim output archive" in reason
    assert str(tmp_path / "matsim_output") in reason


# ---------------------------------------------------------------------------
# _find_sim_output (dashboard / simwrapper spatial export)
# ---------------------------------------------------------------------------

def test_find_sim_output_cache_root_layout(tmp_path):
    RM = pytest.importorskip("braunschweig.analysis.dashboard.run_metrics")
    sim_out = tmp_path / "matsim.simulation.run__abc.cache" / "simulation_output"
    sim_out.mkdir(parents=True)
    assert RM._find_sim_output(tmp_path) == sim_out


def test_find_sim_output_direct_archive_layout(tmp_path):
    RM = pytest.importorskip("braunschweig.analysis.dashboard.run_metrics")
    (tmp_path / "output_events.xml.gz").write_bytes(b"")
    assert RM._find_sim_output(tmp_path) == tmp_path


def test_find_sim_output_none_when_neither_layout(tmp_path):
    RM = pytest.importorskip("braunschweig.analysis.dashboard.run_metrics")
    assert RM._find_sim_output(tmp_path) is None


# ---------------------------------------------------------------------------
# _find_sim_trips (mid validation)
# ---------------------------------------------------------------------------

_TRIPS_CSV = "person_id;mode\n1;car\n2;pt\n"


def test_find_sim_trips_cache_root_layout(tmp_path):
    MID = pytest.importorskip("braunschweig.analysis.run_mid_validation")
    sim_out = tmp_path / "matsim.simulation.run__abc.cache" / "simulation_output"
    sim_out.mkdir(parents=True)
    (sim_out / "eqasim_trips.csv").write_text(_TRIPS_CSV)
    df = MID._find_sim_trips(tmp_path)
    assert df is not None and len(df) == 2


def test_find_sim_trips_direct_archive_layout(tmp_path):
    MID = pytest.importorskip("braunschweig.analysis.run_mid_validation")
    (tmp_path / "eqasim_trips.csv").write_text(_TRIPS_CSV)
    df = MID._find_sim_trips(tmp_path)
    assert df is not None and len(df) == 2


def test_find_sim_trips_none_when_absent(tmp_path):
    MID = pytest.importorskip("braunschweig.analysis.run_mid_validation")
    assert MID._find_sim_trips(tmp_path) is None
