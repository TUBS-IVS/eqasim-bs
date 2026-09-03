"""Tests for the pre-registered SrV distance gap rule (spec Section 5.4)."""
import pandas as pd

from braunschweig.calibration import decision as D


def test_classify_cell():
    assert D.classify_cell(0.12, 0.03, 500) == "gap"
    assert D.classify_cell(0.05, 0.03, 500) == "ok"
    assert D.classify_cell(0.12, 0.15, 500) == "within_noise"     # above 0.08 but inside noise
    assert D.classify_cell(0.12, 0.03, 0) == "no_reference"
    assert D.classify_cell(float("nan"), 0.03, 500) == "no_reference"


def _cells(rows):
    return pd.DataFrame(rows, columns=["code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"])


def test_decide_layer_builds_on_large_kreis_gap():
    cells = _cells([("03101", 1200, 0.15, 0.03, False), ("03102", 370, 0.05, 0.05, False),
                    ("zgb", 4300, 0.06, 0.02, True)])
    out = D.decide_layer(cells)
    assert out["build"] is True and out["gap_codes"] == ["03101"]
    assert "03101" in out["reason"]


def test_decide_layer_ignores_small_kreis_gap_but_not_aggregate():
    cells = _cells([("03153", 150, 0.20, 0.06, False), ("zgb", 4300, 0.06, 0.02, True)])
    assert D.decide_layer(cells)["build"] is False
    cells.loc[1, "emd"] = 0.10
    out = D.decide_layer(cells)
    assert out["build"] is True and out["gap_codes"] == ["zgb"]


def test_decide_layer_all_ok():
    cells = _cells([("03101", 1200, 0.04, 0.03, False), ("zgb", 4300, 0.03, 0.02, True)])
    out = D.decide_layer(cells)
    assert out["build"] is False and out["gap_codes"] == [] and "no gap" in out["reason"]
