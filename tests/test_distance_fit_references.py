import numpy as np
import pathlib
import pytest
from braunschweig.calibration.distance_fit import references as R

MID = str(pathlib.Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "mid")


def test_w12_targets_sum_to_one_and_tagged_input_reproduction():
    targets, edges, tag = R.secondary_w12(MID)
    assert tag == "input_reproduction"
    assert "shop" in targets and "leisure" in targets and "other" in targets
    for purpose, shares in targets.items():
        assert abs(float(np.sum(shares)) - 1.0) < 1e-6
    assert len(edges) - 1 == len(targets["shop"])


def test_t43_targets_are_mean_km_keyed_rs7_ageband_in_sample():
    targets, edges, tag = R.education_t43(MID)
    assert tag == "in_sample"
    assert edges is None
    assert "72|km_0_6" in targets
    assert targets["72|km_0_6"] > 0


def test_p13_rs7_targets_out_of_sample():
    targets, edges, tag = R.work_p13_rs7(MID)
    assert tag == "out_of_sample"
    assert 72 in targets or "72" in targets


def test_p38_2_targets_per_kreis_out_of_sample():
    targets, edges, tag = R.work_p38_2(MID)
    assert tag == "out_of_sample"
    assert len(targets) >= 1
    # each target is a band-share vector summing to ~1
    any_key = next(iter(targets))
    assert abs(float(np.sum(targets[any_key])) - 1.0) < 1e-6
