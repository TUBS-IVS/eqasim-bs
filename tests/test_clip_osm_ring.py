"""Unit tests for scripts/clip_osm_to_cordon_ring.py.

Tests the .poly writer in isolation (no osmosis binary or real data required).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from shapely.geometry import Polygon  # noqa: E402
from scripts.clip_osm_to_cordon_ring import _write_poly  # noqa: E402


def test_write_poly_roundtrip(tmp_path):
    poly = Polygon([(10.0, 52.0), (11.0, 52.0), (11.0, 53.0), (10.0, 53.0)])
    p = tmp_path / "ring.poly"
    _write_poly(poly, str(p))
    text = p.read_text()
    assert text.splitlines()[0] == "zgb_ring"
    assert text.strip().endswith("END")
    assert "  10.0000000  52.0000000" in text


def test_write_poly_name_parameter(tmp_path):
    """The poly file header must use the supplied name argument."""
    poly = Polygon([(10.0, 52.0), (11.0, 52.0), (11.0, 53.0), (10.0, 53.0)])
    p = tmp_path / "ring.poly"
    _write_poly(poly, str(p), name="test_ring")
    text = p.read_text()
    assert text.splitlines()[0] == "test_ring"


def test_write_poly_ends_with_two_end_lines(tmp_path):
    """Osmosis .poly format requires an END line per ring and a final END line."""
    poly = Polygon([(10.0, 52.0), (11.0, 52.0), (11.0, 53.0), (10.0, 53.0)])
    p = tmp_path / "ring.poly"
    _write_poly(poly, str(p))
    lines = p.read_text().splitlines()
    # Last line is the file-level END; second-to-last or earlier is ring-level END.
    assert lines[-1] == "END"
    # There must be at least two END lines (one per ring + file-level).
    assert lines.count("END") >= 2


def test_write_poly_coordinate_format(tmp_path):
    """Coordinates must be written with 7 decimal places, lon before lat."""
    poly = Polygon([(9.1234567, 51.9876543), (10.0, 51.9876543),
                    (10.0, 52.5), (9.1234567, 52.5)])
    p = tmp_path / "ring.poly"
    _write_poly(poly, str(p))
    text = p.read_text()
    # The first coordinate must appear with exactly 7 decimal places.
    assert "  9.1234567  51.9876543" in text


def test_write_poly_uses_unix_lf_line_endings(tmp_path):
    """The .poly must use LF (not CRLF) on every platform.

    The osmosis/osmconvert bounding-polygon parsers expect Unix LF line endings;
    on CRLF they silently match NOTHING and produce an empty clip. On Windows the
    default text mode would emit CRLF, so the writer must force LF explicitly.
    """
    poly = Polygon([(10.0, 52.0), (11.0, 52.0), (11.0, 53.0), (10.0, 53.0)])
    p = tmp_path / "ring.poly"
    _write_poly(poly, str(p))
    raw = p.read_bytes()
    assert b"\r\n" not in raw, "poly must not contain CRLF line endings"
    assert b"\n" in raw
