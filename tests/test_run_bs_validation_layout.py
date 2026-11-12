"""The validation driver must read the directories the canonical overlays write."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_bs_validation import CANONICAL_RATES, rate_layout

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


def test_ten_percent_keeps_the_legacy_layout():
    out_dir, cache_dir, prefix = rate_layout(10, REPO)
    assert out_dir.name == "output_bs_10pct" and cache_dir.name == "cache_bs_10pct"
    assert prefix == "braunschweig_10pct_"
