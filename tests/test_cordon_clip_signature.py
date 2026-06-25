"""Tests for the cordon osm/cordon clip freshness guard (write/verify_clip_signature).

The osm/cordon ring is a manually pre-clipped artifact (scripts/clip_osm_to_cordon_ring.py)
outside synpp's stage cache. If cordon_network_source_buffer_m changes, the stale ring
would silently give a wrong road extent. write_clip_signature records the buffer used;
verify_clip_signature fails loud on a mismatch (and warns -- never raises -- when no
signature exists, for backward compatibility with rings clipped before this guard).
"""
import os

import pytest

from braunschweig.data.cordon import network


def test_write_then_verify_match_ok(tmp_path):
    out = tmp_path / "cordon"
    network.write_clip_signature(str(out), 45000.0, "germany-latest.osm.pbf")
    assert (out / network.CLIP_SIGNATURE_FILE).is_file()
    # Matching configured buffer -> no exception.
    network.verify_clip_signature(str(out), 45000.0)


def test_verify_raises_on_buffer_mismatch(tmp_path):
    out = tmp_path / "cordon"
    network.write_clip_signature(str(out), 30000.0, "germany-latest.osm.pbf")
    with pytest.raises(RuntimeError, match="STALE"):
        network.verify_clip_signature(str(out), 45000.0)


def test_verify_within_tolerance_ok(tmp_path):
    out = tmp_path / "cordon"
    network.write_clip_signature(str(out), 45000.0, "g.pbf")
    network.verify_clip_signature(str(out), 45000.4)  # < 1 m tolerance


def test_verify_without_signature_warns_not_raises(tmp_path, caplog):
    out = tmp_path / "cordon"
    os.makedirs(out, exist_ok=True)  # ring dir exists but no .clip_signature
    # Must NOT raise (backward-compatible); it warns instead.
    network.verify_clip_signature(str(out), 45000.0)
