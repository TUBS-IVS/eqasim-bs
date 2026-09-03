"""Tests for the pre-registered SrV distance gap rule (spec Section 5.4)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import decision as D


def test_classify_cell():
    assert D.classify_cell(0.12, 0.03, 500) == "gap"
    assert D.classify_cell(0.05, 0.03, 500) == "ok"
    assert D.classify_cell(0.12, 0.15, 500) == "within_noise"     # above 0.08 but inside noise
    assert D.classify_cell(0.12, 0.03, 0) == "no_reference"
    assert D.classify_cell(float("nan"), 0.03, 500) == "no_reference"


def test_classify_cell_boundary():
    # EMD == 0.08 exactly is the pre-registered boundary: should be "ok"
    assert D.classify_cell(0.08, 0.03, 500) == "ok"


def test_classify_cell_nan_noise_floor():
    # NaN noise floor makes the cell unusable
    assert D.classify_cell(0.10, float("nan"), 500) == "no_reference"


def test_classify_cell_none_noise_floor():
    # None noise floor makes the cell unusable
    assert D.classify_cell(0.10, None, 500) == "no_reference"


def test_classify_cell_numpy_float_nan():
    # numpy float NaN should be handled by pd.isna
    assert D.classify_cell(np.float32("nan"), 0.02, 500) == "no_reference"


def test_classify_cell_nan_reference_persons():
    # NaN n_reference_persons means no_reference
    assert D.classify_cell(0.10, 0.02, np.nan) == "no_reference"


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
    out = D.decide_layer(cells)
    assert out["build"] is False
    # Verify the small Kreis is classified as gap but not in gap_codes
    assert out["classification"]["03153"] == "gap"
    assert "03153" not in out["gap_codes"]

    cells.loc[1, "emd"] = 0.10
    out = D.decide_layer(cells)
    assert out["build"] is True and out["gap_codes"] == ["zgb"]


def test_decide_layer_all_ok():
    cells = _cells([("03101", 1200, 0.04, 0.03, False), ("zgb", 4300, 0.03, 0.02, True)])
    out = D.decide_layer(cells)
    assert out["build"] is False and out["gap_codes"] == [] and "no gap" in out["reason"]


def test_decide_layer_within_noise_alone_no_build():
    # A within_noise cell (even if decisive) should not trigger build
    cells = _cells([("03101", 1200, 0.12, 0.15, False), ("zgb", 4300, 0.03, 0.02, True)])
    out = D.decide_layer(cells)
    assert out["build"] is False
    assert out["classification"]["03101"] == "within_noise"


def test_decide_layer_custom_threshold_and_min_persons():
    # Test non-default emd_threshold=0.05 and min_persons=100
    cells = _cells([("03101", 150, 0.06, 0.03, False), ("03102", 80, 0.06, 0.03, False),
                    ("zgb", 400, 0.06, 0.03, True)])
    # With default (threshold=0.08, min_persons=200): no build (no decisive gaps)
    assert D.decide_layer(cells)["build"] is False
    # With threshold=0.05, min_persons=100: 03101 is gap and decisive -> build
    out = D.decide_layer(cells, emd_threshold=0.05, min_persons=100)
    assert out["build"] is True
    assert "03101" in out["gap_codes"]
    assert "03102" not in out["gap_codes"]  # only 80 persons, below 100


def test_decide_layer_numpy_bool_aggregate():
    # is_aggregate can be a numpy bool
    cells = _cells([("03101", 1200, 0.04, 0.03, np.bool_(False)),
                    ("zgb", 4300, 0.03, 0.02, np.bool_(True))])
    out = D.decide_layer(cells)
    assert out["build"] is False


def test_decide_layer_empty_frame():
    cells = _cells([])
    with pytest.raises(ValueError, match="no cells to decide"):
        D.decide_layer(cells)


def test_decide_layer_missing_column():
    cells = _cells([("03101", 1200, 0.15, 0.03, False)])
    # Remove a required column
    cells_bad = cells[["code", "n_reference_persons", "emd", "is_aggregate"]]
    with pytest.raises(ValueError, match="Missing required columns"):
        D.decide_layer(cells_bad)


def test_decide_layer_zero_aggregate_rows():
    cells = _cells([("03101", 1200, 0.15, 0.03, False), ("03102", 370, 0.05, 0.05, False)])
    with pytest.raises(ValueError, match="Expected exactly 1 aggregate row, found 0"):
        D.decide_layer(cells)


def test_decide_layer_two_aggregate_rows():
    cells = _cells([("03101", 1200, 0.15, 0.03, True), ("zgb", 4300, 0.06, 0.02, True)])
    with pytest.raises(ValueError, match="Expected exactly 1 aggregate row, found 2"):
        D.decide_layer(cells)


def test_decide_layer_duplicate_codes():
    cells = _cells([("03101", 1200, 0.15, 0.03, False), ("03101", 370, 0.05, 0.05, False),
                    ("zgb", 4300, 0.06, 0.02, True)])
    with pytest.raises(ValueError, match="Duplicate codes"):
        D.decide_layer(cells)
