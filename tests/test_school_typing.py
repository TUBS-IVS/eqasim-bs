import numpy as np
from braunschweig.data.schools.typing import (
    sgl_to_level, capacity_by_level_from_sgl, nds_ags8, nds_kreis5,
    SCHOOL_LEVELS,
)


def test_sgl_to_level_primary_secondary():
    assert sgl_to_level("01") == "grundschule"
    assert sgl_to_level("04") == "grundschule"
    assert sgl_to_level("11") == "sekundar_1"
    assert sgl_to_level("14") == "sekundar_1"
    assert sgl_to_level("63") == "sekundar_1"   # Foerderschule folded in
    assert sgl_to_level("23") == "sekundar_2"
    assert sgl_to_level("29") == "sekundar_2"
    assert sgl_to_level("30") is None           # Abendgymnasium ignored
    assert sgl_to_level("31") is None           # Kolleg ignored
    # full Sekundarbereich-I 40-69 range maps to sekundar_1 (regression: 55/57/58/59
    # were previously omitted)
    for code in ("40", "55", "57", "58", "59", "69"):
        assert sgl_to_level(code) == "sekundar_1"


def test_capacity_by_level_splits_a_kgs_across_two_levels():
    sgl = ["04", "16", "28"]
    sch = [100, 50, 30]
    cap = capacity_by_level_from_sgl(sgl, sch)
    assert cap == {"grundschule": 100.0, "sekundar_1": 50.0, "sekundar_2": 30.0}


def test_capacity_by_level_sums_same_level_and_skips_blanks():
    sgl = ["01", "01", None, "30"]
    sch = [120, 80, np.nan, 40]
    cap = capacity_by_level_from_sgl(sgl, sch)
    assert cap == {"grundschule": 200.0}


def test_nds_code_to_official_ids():
    assert nds_ags8("241001") == "03241001"
    assert nds_kreis5(101) == "03101"
    assert nds_kreis5("158") == "03158"


def test_levels_constant():
    assert SCHOOL_LEVELS == ("grundschule", "sekundar_1", "sekundar_2")
