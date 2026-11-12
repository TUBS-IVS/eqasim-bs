"""The validation driver must read the directories the canonical overlays write."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_bs_validation import CANONICAL_RATES, LEGACY_FIXTURES, rate_layout

REPO = Path(__file__).resolve().parents[1]
OVERLAY = {1: "test_1pct.yml", 25: "test_25pct.yml", 100: "test_100pct.yml"}


@pytest.mark.parametrize("rate_pct", CANONICAL_RATES)
def test_layout_matches_the_canonical_overlay(rate_pct: int):
    overlay = yaml.safe_load(
        (REPO / "configs" / "overlays" / OVERLAY[rate_pct]).read_text(encoding="utf-8"))
    out_dir, cache_dir, prefix = rate_layout(rate_pct, REPO)

    assert out_dir == REPO / overlay["config"]["output_path"]
    assert cache_dir == REPO / overlay["working_directory"]
    assert prefix == overlay["config"]["output_prefix"]


@pytest.mark.parametrize("rate_pct, layout", [(1, "legacy"), (10, "auto"), (25, "legacy")])
def test_legacy_layout_matches_the_legacy_fixture(rate_pct: int, layout: str):
    """10 % has no overlay; --layout legacy reads a run of the legacy fixture (the analysis README recipe)."""
    fixture = yaml.safe_load(
        (REPO / "configs" / "fixtures" / LEGACY_FIXTURES[rate_pct]).read_text(encoding="utf-8"))
    out_dir, _, prefix = rate_layout(rate_pct, REPO, layout)

    assert out_dir == REPO / fixture["config"]["output_path"]
    assert prefix == fixture["config"]["output_prefix"]


def test_legacy_layout_refuses_a_rate_without_fixture():
    with pytest.raises(ValueError, match="no legacy fixture"):
        rate_layout(100, REPO, "legacy")


def test_canonical_layout_refuses_a_rate_without_overlay():
    with pytest.raises(ValueError, match="no canonical overlay"):
        rate_layout(10, REPO, "canonical")
