"""Formulas of scripts/backtest_wolfsburg_kreis_transfer.py, the evidence of ADR-0205.

Defects caught: a leave-one-out that removes Kreis k's share instead of its weighted mass, an
odds transfer that multiplies shares instead of odds, a class transfer that is not
renormalised, and an immobile transfer that moves the mobile classes off the anchor's pattern.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.backtest_wolfsburg_kreis_transfer import (  # noqa: E402
    class_transfer, immobile_transfer, leave_one_out, odds_transfer)


def test_transfer_formulas_on_hand_computed_cases():
    # Two Kreise: 30 % on weight 100 and 10 % on weight 300 make 15 % on 400.
    assert leave_one_out(0.15, 400, 0.30, 100) == pytest.approx(0.10)
    # Anchor odds 1/3, MiD odds ratio 1 / (1/3) = 3 -> odds 1 -> 50 %.
    assert odds_transfer(0.25, 0.5, 0.25) == pytest.approx(0.5)
    # Ratios 2 and 1 turn [0.2, 0.8] into [0.4, 0.8], renormalised [1/3, 2/3].
    assert class_transfer([0.2, 0.8], [0.4, 0.6], [0.2, 0.6]) == pytest.approx([1 / 3, 2 / 3])
    # trips_0: odds 1/9 x (0.21/0.79) / (0.18/0.82) = 0.134552 -> 0.118595; the rest
    # 0.881405 split 1:2 like the anchor's mobile classes.
    assert immobile_transfer([0.1, 0.3, 0.6], 0.21, 0.18) == pytest.approx(
        [0.118595, 0.293802, 0.587603], abs=1e-6)
