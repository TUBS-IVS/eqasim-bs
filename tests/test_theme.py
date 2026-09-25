"""Tests for the eqasim-bs colour theme + stage→phase map (mirror of cleancensus)."""
from __future__ import annotations

from braunschweig import theme
from braunschweig.progress import format_rate


def test_a_stage_is_coloured_by_its_phase_and_never_like_a_severity_level():
    """The phase map itself is cosmetic and not re-listed here; what matters is that a
    mapped logger gets its phase's colour, an unmapped one falls back to "misc", and no
    phase colour can be mistaken for a log severity."""
    phase = theme.phase_of("popsim.seed")
    assert theme.stage_color("popsim.seed") == theme.PHASE_COLOR[phase]
    assert theme.phase_of("census.filtered") == "misc"
    severity = set(theme.LEVEL_COLOR.values())
    assert not severity & set(theme.PHASE_COLOR.values())


def test_want_color_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.want_color("auto") is False
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert theme.want_color(True) is True
    assert theme.want_color(False) is False


def test_format_rate_adaptive():
    assert format_rate(12.3) == "12.3/s"
    assert format_rate(0.75) == "45.0/min"
    assert format_rate(1.0 / 3120.0) == "~52:00/it"
    assert format_rate(0.0) is None
