"""Tests for the presence-based MiD W1 purpose-mix baseline in the
Braunschweig 10 % validation report (issue #201, T10 fix).

The static ``purpose_mix_w1`` baseline splits Begleitung (escort, 8/99) out of
Erledigung (other, 11/99). That split is only apples-to-apples with a synthetic
population built with ``escort_purpose`` ON, which carries a dedicated
``escort`` purpose. On a flag-OFF population Begleitung is folded into ``other``
(eqasim has no ``escort``), so the escort share must be folded back into
``other`` in the baseline too — otherwise the flag-OFF ``other`` value already
contains Begleitung but is compared against 11/99, inflating its deviation by
~8 pp and reporting a spurious escort gap. This mirrors the presence-based
``scored_mid_purposes`` selection in ``trip_coherence``.
"""
from __future__ import annotations

import math

from scripts.validate_bs_10pct import metrics
from scripts.validate_bs_10pct.config import MID_BASELINE


def test_baseline_keeps_escort_split_when_escort_present():
    """Flag-ON population (``escort`` present): the baseline keeps the
    Begleitung/Erledigung split verbatim, so both purposes are scored
    separately against their W1 shares."""
    present = {"work", "education", "shop", "other", "escort", "leisure"}

    baseline = metrics.purpose_mix_w1_baseline(present)

    assert baseline == MID_BASELINE["purpose_mix_w1"]
    assert math.isclose(baseline["escort"], 8 / 99)
    assert math.isclose(baseline["other"], 11 / 99)


def test_baseline_folds_escort_into_other_when_escort_absent():
    """Flag-OFF population (no ``escort``): Begleitung is folded back into
    ``other`` so the synthetic ``other`` (which already contains Begleitung)
    is compared against the combined 19/99 share, and no spurious ``escort``
    row is scored."""
    present = {"work", "education", "shop", "other", "leisure"}

    baseline = metrics.purpose_mix_w1_baseline(present)

    assert "escort" not in baseline
    assert math.isclose(baseline["other"], (11 + 8) / 99)
    # The other purposes are untouched.
    for key in ("work", "education", "shop", "leisure"):
        assert math.isclose(baseline[key], MID_BASELINE["purpose_mix_w1"][key])


def test_baseline_does_not_mutate_static_config():
    """The helper must return a copy; the shared static baseline dict stays
    intact across calls (folding escort must not corrupt later reports)."""
    before_escort = MID_BASELINE["purpose_mix_w1"]["escort"]
    before_other = MID_BASELINE["purpose_mix_w1"]["other"]

    metrics.purpose_mix_w1_baseline({"work", "other"})  # escort-absent branch

    assert MID_BASELINE["purpose_mix_w1"]["escort"] == before_escort
    assert MID_BASELINE["purpose_mix_w1"]["other"] == before_other
