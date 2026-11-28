"""The students' mode by distance band of the student in-commuters (issue #140, Phase 4, design B6).

Defects caught: a band function whose edges drift from the table's (a routed length lands in the wrong band, or a
long commute finds no band), a pooling that leaves a thin band on its own or mixes the bands unweighted, a reference
that keeps walk or bike, and a table whose header changed under the loader.
"""
from __future__ import annotations

import logging
import os
import shutil

import pandas as pd
import pytest

from braunschweig.data.education import student_mode_reference as smr

DATA = os.path.join("eqasim-data", "data")


def test_the_bands_cover_zero_to_infinity_left_inclusive():
    assert smr.student_mode_band(0.0) == "0_5"
    assert smr.student_mode_band(4.999) == "0_5"
    assert smr.student_mode_band(5.0) == "5_10"
    assert smr.student_mode_band(99.9) == "50_100"
    assert smr.student_mode_band(100.0) == "100_plus"
    assert smr.student_mode_band(1e9) == "100_plus"
    for bad in (-0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            smr.student_mode_band(bad)


def test_the_committed_table_loads_restricted_to_car_and_pt_with_the_thin_last_band_pooled(caplog):
    with caplog.at_level(logging.INFO, logger=smr.__name__):
        reference = smr.load_student_education_mode_by_distance(DATA)
    # The log shows what pooling did: 100_plus's own car share 0.7150, pooled 0.4095 (hand-computed below).
    assert "100_plus" in caplog.text and "0.7150" in caplog.text and "0.4095" in caplog.text
    assert list(reference) == list(smr.BAND_LABELS)
    for band, distribution in reference.items():
        assert set(distribution) == {"car", "pt"}, band
        assert sum(distribution.values()) == pytest.approx(1.0)
    # 100_plus holds 5 students with a car or pt leg (n_unweighted of the table), fewer than the default 30: it shares the
    # distribution of 50_100 (45 students); 25_50 (178) stays on its own.
    assert reference["100_plus"] == reference["50_100"]
    assert reference["25_50"] != reference["50_100"]
    # Pooled by the weighted mass (share_of_all): car (0.01015169802 + 0.000927135715) over car and pt of both bands.
    car = 0.01015169802 + 0.000927135715
    pt = 0.01560575857 + 0.0003696299259
    assert reference["50_100"]["car"] == pytest.approx(car / (car + pt))
    # Without a minimum no band is pooled: 100_plus keeps its own car share (0.7149601175 of car and pt).
    unpooled = smr.load_student_education_mode_by_distance(DATA, min_band_persons=0)
    assert unpooled["100_plus"]["car"] == pytest.approx(0.7149601175 / (0.7149601175 + 0.2850398825))


def test_a_header_with_other_band_edges_is_refused(tmp_path):
    source = os.path.join(DATA, smr.TABLE_RELATIVE_PATH)
    target = tmp_path / smr.TABLE_RELATIVE_PATH
    os.makedirs(target.parent)
    shutil.copyfile(source, target)
    text = target.read_text(encoding="utf-8").replace("50.0, 100.0, inf", "50.0, 120.0, inf")
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="band edges"):
        smr.load_student_education_mode_by_distance(tmp_path)


def _counts(*persons):
    """A synthetic table: per band, ``persons`` car legs and as many pt legs (car+pt = 2 x persons)."""
    rows = []
    for label, n in zip(smr.BAND_LABELS, persons):
        rows += [{"distance_band": label, "mode": "car", "n_unweighted": n},
                 {"distance_band": label, "mode": "pt", "n_unweighted": n},
                 {"distance_band": label, "mode": "walk", "n_unweighted": 100}]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("persons, minimum, expected", [
    # A thin FIRST band merges upward (walk legs do not count).
    ((2, 20, 20, 20, 20, 20), 30, [("0_5", "5_10"), ("10_25",), ("25_50",), ("50_100",), ("100_plus",)]),
    # A cascade: 50_100 merges down into 25_50, then the still thin 100_plus into that group.
    ((20, 20, 20, 20, 5, 2), 30, [("0_5",), ("5_10",), ("10_25",), ("25_50", "50_100", "100_plus")]),
    # Without a minimum a thin first band stays on its own.
    ((2, 20, 20, 20, 20, 20), 0, [(label,) for label in smr.BAND_LABELS]),
])
def test_thin_bands_pool_with_their_neighbour(persons, minimum, expected):
    assert smr.pool_thin_bands(_counts(*persons), minimum) == expected
