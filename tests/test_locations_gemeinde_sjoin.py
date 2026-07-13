"""Fallback-transparency test for the Gemeinde sjoin in
``braunschweig.data.locations`` (issue #163, item 4).

``gpd.sjoin(..., how="inner", predicate="within")`` silently drops any
candidate location whose centroid does not fall within any Gemeinde polygon
(boundary slivers, a stale zones vintage, a CRS mismatch). This is
instrumentation only -- join semantics are unchanged; the fix only makes the
drop count/rate observable (CLAUDE.md "Fallback transparency"), mirroring
``braunschweig.data.buildings.COMMUNE_AGS_FALLBACK_WARN_THRESHOLD``.
"""
from __future__ import annotations

from braunschweig.data import locations


def test_no_drop_logs_plain_message_without_warning(capsys) -> None:
    locations._log_gemeinde_sjoin_drop_rate(kept_count=100, total_count=100)
    out = capsys.readouterr().out
    assert "100/100" in out
    assert "0 dropped" in out
    assert "WARNING" not in out


def test_small_drop_below_threshold_no_warning(capsys) -> None:
    # 1 dropped of 1000 = 0.1%, well below the 1% threshold.
    locations._log_gemeinde_sjoin_drop_rate(kept_count=999, total_count=1000)
    out = capsys.readouterr().out
    assert "999/1,000" in out
    assert "1 dropped" in out
    assert "WARNING" not in out


def test_large_drop_above_threshold_warns(capsys) -> None:
    # 100 dropped of 1000 = 10%, well above the 1% threshold -- the join is
    # likely broken (CRS mismatch / stale zones), not a few boundary slivers.
    locations._log_gemeinde_sjoin_drop_rate(kept_count=900, total_count=1000)
    out = capsys.readouterr().out
    assert out.startswith("WARNING: ")
    assert "10.00%" in out


def test_no_division_by_zero_on_empty_input(capsys) -> None:
    locations._log_gemeinde_sjoin_drop_rate(kept_count=0, total_count=0)
    out = capsys.readouterr().out
    assert "0.00%" in out
    assert "WARNING" not in out
