"""Tests for the eqasim-bs colour theme + stage→phase map (mirror of cleancensus)."""
from __future__ import annotations

from braunschweig import theme
from braunschweig.progress import format_rate


def test_phase_of_popsim_loggers():
    assert theme.phase_of("popsim.seed") == "acquire"
    assert theme.phase_of("popsim.mid") == "transform"
    assert theme.phase_of("popsim.income_spatial_tilt") == "controls"
    assert theme.phase_of("synpp") == "orchestrate"


def test_phase_of_unmapped_is_misc():
    assert theme.phase_of("census.filtered") == "misc"


def test_stage_color_returns_phase_ansi():
    assert theme.stage_color("popsim.seed") == theme.PHASE_COLOR["acquire"]
    assert theme.stage_color("popsim.seed").startswith("\x1b[38;5;")


def test_phase_colors_distinct_from_severity():
    severity = set(theme.LEVEL_COLOR.values())
    for hue in theme.PHASE_COLOR.values():
        assert hue not in severity


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
