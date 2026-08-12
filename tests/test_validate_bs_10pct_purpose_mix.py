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

import pandas as pd
import pytest

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


# ---------------------------------------------------------------------------
# Active-adjusted W1 baseline (issue #256, escort_passive_education).
#
# ``config.py`` stays static (no CSV loading at import time -- see
# CLAUDE.md "Paths and file handling" / reproducibility rules): the derived
# dict is therefore NOT a literal ``MID_BASELINE["purpose_mix_w1_active"]``
# key, but is constructed lazily by
# ``metrics.purpose_mix_w1_active_baseline()`` from the static
# ``purpose_mix_w1`` baseline plus ``references.escort_active_share()``
# (which reads the pinned ``mid2023_escort_w_zweck_split.csv``). The pinning
# assertions below are otherwise identical to the brief.
#
# ``purpose_mix_w1_active_baseline`` takes the same ``present_purposes``
# presence guard as its sibling ``purpose_mix_w1_baseline`` (review finding,
# 2026-08-11): without it, a synthetic population that carries no ``escort``
# purpose at all would still be scored against an active-adjusted baseline
# that assumes an active-only escort category exists, silently comparing
# ``other``/``education`` against numbers that do not apply.
# ---------------------------------------------------------------------------
PRESENT_WITH_ESCORT = {"work", "education", "shop", "other", "escort", "leisure"}
PRESENT_WITHOUT_ESCORT = {"work", "education", "shop", "other", "leisure"}


def test_active_baseline_derivation_and_pinning():
    """The active-adjusted baseline must scale ``escort`` by the pinned
    active share and fold the passive remainder into ``education``, while
    leaving all other purposes untouched and preserving total mass --
    when ``escort`` is present in the synthetic distribution."""
    import csv
    import pathlib

    csv_path = pathlib.Path(__file__).resolve().parents[1] / "eqasim-data" / "data" \
        / "braunschweig" / "mid" / "mid2023_escort_w_zweck_split.csv"
    with open(csv_path, encoding="utf-8") as handle:
        rows = {r["w_zweck"]: r for r in csv.DictReader(
            line for line in handle if not line.startswith("#"))}
    active = float(rows["code_6"]["share_weighted"])

    raw = MID_BASELINE["purpose_mix_w1"]
    adj = metrics.purpose_mix_w1_active_baseline(PRESENT_WITH_ESCORT)

    assert adj["escort"] == pytest.approx(raw["escort"] * active, abs=1e-6)
    assert adj["education"] == pytest.approx(
        raw["education"] + raw["escort"] * (1.0 - active), abs=1e-6)
    for key in ("work", "shop", "other", "leisure"):
        assert adj[key] == pytest.approx(raw[key])
    assert sum(adj.values()) == pytest.approx(sum(raw.values()))


def test_active_baseline_degrades_to_folded_baseline_when_escort_absent():
    """Escort-absent population: the active-adjusted baseline must NOT apply
    the active/passive split (there is no active-only escort category to
    adjust for), and instead degrade exactly to the presence-folded
    ``purpose_mix_w1_baseline`` result -- so section 5.3b renders the same
    numbers as section 5.3 instead of a spurious active-adjusted reference."""
    expected = metrics.purpose_mix_w1_baseline(PRESENT_WITHOUT_ESCORT)

    adj = metrics.purpose_mix_w1_active_baseline(PRESENT_WITHOUT_ESCORT)

    assert adj == expected
    assert "escort" not in adj


# ---------------------------------------------------------------------------
# I/O-path coverage (deferred review D14): purpose_mix_no_home[_active]
# themselves -- as opposed to the pure baseline-selection helpers exercised
# above -- are not called by any other test in this module. This closes that
# gap cheaply via monkeypatch, without new fixture infrastructure.
# ---------------------------------------------------------------------------
def test_purpose_mix_no_home_variants_exclude_home_and_use_presence_baseline(monkeypatch):
    """Monkeypatch ``metrics.io.trips_full`` with a small synthetic frame
    (escort, home, and three other purposes) and check both
    ``purpose_mix_no_home`` and ``purpose_mix_no_home_active`` end to end:
    ``home`` legs are excluded, ``mid_share`` matches the presence-based
    baseline (checked against the baseline helper itself, not a hardcoded
    number, so this stays correct if MID_BASELINE ever changes), and
    ``synth_share`` sums to 1."""
    synthetic_trips = pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "following_purpose": [
            "home", "home", "escort", "escort", "escort",
            "work", "work", "shop", "leisure", "leisure",
        ],
    })
    monkeypatch.setattr(metrics.io, "trips_full", lambda: synthetic_trips)

    for table_fn, baseline_fn in (
        (metrics.purpose_mix_no_home, metrics.purpose_mix_w1_baseline),
        (metrics.purpose_mix_no_home_active, metrics.purpose_mix_w1_active_baseline),
    ):
        table = table_fn()

        assert "home" not in set(table["purpose"])

        expected_baseline = baseline_fn(set(table["purpose"]))
        for _, row in table.iterrows():
            assert row["mid_share"] == pytest.approx(expected_baseline[row["purpose"]])

        assert table["synth_share"].sum() == pytest.approx(1.0)
