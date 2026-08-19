"""W_ZWECK purpose coverage (issue #241).

``map_purpose`` mapped only W_ZWECK 1-12 and sent everything else to ``"other"`` through a
silent ``fillna``. The raw MiD 2023 Wege file carries five further codes -- about 3 % of all
donor legs, weighted -- so that fallback was neither rare nor visible: no counter, no log
line, and a future MiD edition adding a code would be swallowed the same way.

The semantic labels come from the codeplan (``MiD2023_Codeplaene_B1_Standard_v1.1.xlsx``,
sheet ``Wege``, variable ``W_ZWECK``), not from inference:

    13  Begleitung Erwachsener
    14  Sport/Sportverein
    15  Freunde besuchen/treffen
    16  Unterricht (nicht Schule)
    99  keine Angabe
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import trips as T


def _wege(codes, weights=None) -> pd.DataFrame:
    frame = pd.DataFrame({"W_ZWECK": codes})
    if weights is not None:
        frame["W_GEW"] = weights
    return frame


def test_round_trip_leisure_codes_map_explicitly_to_leisure():
    """14 Sport/Sportverein, 15 Freunde besuchen, 16 Unterricht (nicht Schule) -> leisure.

    16 is the interesting one: its codeplan label is educational, but eqasim's ``education``
    purpose means the person's ASSIGNED educational facility (school / Kita), which the
    primary-location machinery anchors. Evening classes are not that, so following MiD's own
    ``zweck`` derivation (-> 7 Freizeit) is right for the model -- see the ADR.
    """
    out = T.map_purpose(_wege([14, 15, 16]))
    assert out["purpose"].tolist() == ["leisure", "leisure", "leisure"]


def test_non_response_and_adult_escort_map_explicitly_to_other():
    """99 (keine Angabe) and 13 (Begleitung Erwachsener) resolve to ``other`` EXPLICITLY.

    Same value the old fallback produced, so this half changes no output -- the point is that
    the mapping is now stated rather than implied. Under the escort flag, 13 becomes
    ``escort``; that override belongs to #201 and is unaffected here.
    """
    out = T.map_purpose(_wege([13, 99]))
    assert out["purpose"].tolist() == ["other", "other"]


def test_unmapped_code_is_reported_not_absorbed(caplog):
    """An unknown code must produce a WARNING naming it and its share.

    This is the actual defect: MiD 2023 introduced codes 13-16 and the model absorbed them
    without a word. A future code 17 must not repeat that.
    """
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.trips"):
        out = T.map_purpose(_wege([1, 4, 17, 17], weights=[1.0, 1.0, 1.0, 1.0]))
    assert out["purpose"].tolist() == ["work", "shop", "other", "other"]
    # caplog.text carries the FORMATTED records; reading record.message directly would miss
    # the lazy %-args this module logs with.
    assert "17" in caplog.text
    assert "50.00" in caplog.text, caplog.text


def test_full_coverage_logs_no_warning(caplog):
    """A frame using only known codes must stay quiet, or the warning becomes noise nobody
    reads."""
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.trips"):
        T.map_purpose(_wege([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 99]))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_every_documented_code_is_mapped():
    """The mapping table must cover every W_ZWECK code the codeplan documents, so the guard
    above can only ever fire on a genuinely NEW code."""
    documented = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 99}
    assert documented <= set(T.PURPOSE_BY_W_ZWECK)


def test_leisure_remap_can_be_switched_off_for_the_ab_run():
    """The 14/15/16 -> leisure change moves ~1 % of legs out of ``other``, so it is
    flag-gated: OFF reproduces the pre-#241 assignment for an A/B."""
    out = T.map_purpose(_wege([14, 15, 16]), explicit_round_trip_purposes=False)
    assert out["purpose"].tolist() == ["other", "other", "other"]
