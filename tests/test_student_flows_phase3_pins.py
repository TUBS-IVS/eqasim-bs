"""Pins of the constants that ``student_flows_inputs`` repeats from Phase 3 (issue #140, Phase 4).

The flows module is a true leaf (it loads no popsim, calibration or analysis module; global
constraints, "Import boundary"), so the name and default of the home-area key, the header key of
the education-leg share, the age groups of the level, the student status and no-answer codes, the region and the
Wolfsburg codes are repeated there as literals.
This file is the only place that imports both sides: it fails when Phase 3 changes one of them and
the flows module does not follow.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from braunschweig.calibration.mid_student_reference import STUDENT_P_TAET
from braunschweig.calibration import srv_distance_targets, srv_student_reference
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.calibration.srv_student_reference import AREA_WOLFSBURG
from braunschweig.data.education import student_flows_inputs as SFI
from braunschweig.data.education.student_od_table import T_COLUMNS
from braunschweig.popsim import student_level, student_targets
from braunschweig.popsim.attributes import STUDENT_TAET
from braunschweig.popsim.stage import config_keys

DATA = Path(__file__).resolve().parents[1] / "eqasim-data" / "data"


def test_home_area_key_and_default_equal_the_stage_constants():
    assert SFI.KEY_HOME_AREA_KREISE == config_keys.KEY_STUDENT_HOME_AREA_KREISE
    assert SFI.default_home_area_kreise() == config_keys.DEFAULT_STUDENT_HOME_AREA_KREISE


_SURROUNDING = ["03102", "03151", "03153", "03154", "03157", "03158"]
_ACCEPTED = {
    "default": {"braunschweig": ["03101"], "surrounding": _SURROUNDING},
    "split_differently": {"west": ["03101", "03102", "03151"], "east": ["03153", "03154", "03157", "03158"]},
}
_REJECTED = {
    "wolfsburg_in_an_area": {"braunschweig": ["03101"], "surrounding": [*_SURROUNDING, "03103"]},
    "a_kreis_in_no_area": {"braunschweig": ["03101"], "surrounding": _SURROUNDING[1:]},
    "a_kreis_in_two_areas": {"braunschweig": ["03101"], "surrounding": ["03101", *_SURROUNDING]},
    "a_kreis_outside_the_region": {"braunschweig": ["03101"], "surrounding": [*_SURROUNDING, "03999"]},
    "value_is_no_list": {"braunschweig": "03101", "surrounding": _SURROUNDING},
    "empty_map": {},
    "no_mapping": ["03101", *_SURROUNDING],
}


@pytest.mark.parametrize("areas", list(_ACCEPTED.values()), ids=list(_ACCEPTED))
def test_both_validators_accept_the_same_area_maps_and_agree_on_the_result(areas):
    phase3 = student_targets.area_of_kreis_from_home_areas(areas, ZGB_KREISE)
    ours = SFI.area_by_kreis(areas, ZGB_KREISE)
    assert set(ours) == set(phase3)
    for kreis, area in phase3.items():
        # Phase 3 maps Wolfsburg to None (the value of the region); the flows name it.
        assert ours[kreis] == (AREA_WOLFSBURG if area is None else area)


@pytest.mark.parametrize("areas", list(_REJECTED.values()), ids=list(_REJECTED))
def test_both_validators_reject_the_same_area_maps(areas):
    with pytest.raises(ValueError):
        student_targets.area_of_kreis_from_home_areas(areas, ZGB_KREISE)
    with pytest.raises(ValueError):
        SFI.area_by_kreis(areas, ZGB_KREISE)


def test_the_repeated_literals_equal_their_owners():
    assert SFI.ZGB_KREISE == srv_distance_targets.ZGB_KREISE
    assert SFI.WOLFSBURG_KREIS == srv_distance_targets.WOLFSBURG_KREIS
    assert SFI.AREA_WOLFSBURG == srv_student_reference.AREA_WOLFSBURG
    assert SFI.default_home_area_kreise() == {area: list(kreise) for area, kreise
                                              in srv_student_reference.HOME_AREAS.items()}
    assert SFI.STUDENT_P_TAET == STUDENT_TAET
    assert SFI.T_COLUMNS == T_COLUMNS


def test_student_status_code_of_the_leaf_module_equals_the_popsim_constant():
    assert STUDENT_P_TAET == STUDENT_TAET == SFI.STUDENT_P_TAET


def test_the_no_answer_code_equals_the_mid_code_list_and_the_calibration_constant():
    """The owner is the MiD P_TAET code list of popsim.attributes, a comment without a constant: its
    "99 keine Angabe" line is read from the source; the calibration constant must agree too."""
    import inspect
    import re

    from braunschweig.calibration.mid_student_reference import P_TAET_NO_ANSWER
    from braunschweig.popsim import attributes

    listed = re.findall(r"^#\s+(\d+)\s+keine Angabe", inspect.getsource(attributes), flags=re.MULTILINE)
    assert listed[:1] == [str(SFI.P_TAET_NO_ANSWER)]          # the first such line is the P_TAET list
    assert SFI.P_TAET_NO_ANSWER == P_TAET_NO_ANSWER
    unknown = SFI.status_unknown(pd.Series([10, 99, None, "x", 4.0], dtype=object))
    assert unknown.tolist() == [False, True, True, True, False]


def test_education_leg_share_header_key_equals_the_one_of_the_stage():
    assert SFI.EDUCATION_LEG_SHARE_HEADER_KEY == student_targets.EDUCATION_SHARE_HEADER_KEY


def test_age_groups_and_sexes_of_the_level_equal_the_stage_ones():
    assert SFI.LEVEL_AGE_GROUPS == student_level.AGE_LABELS
    assert SFI.LEVEL_SEXES == student_level.SEXES
    assert set(SFI.YOUNG_AGE_GROUPS) == set(student_level.YOUNG_AGE_LABELS)


def test_the_summed_level_equals_the_level_the_stage_reads():
    ours = SFI.load_level(DATA / student_level.STUDENT_LEVEL_TABLE_RELPATH, ZGB_KREISE)
    table = student_level.read_student_level_table(DATA, ZGB_KREISE).table
    table = table[table["code"].isin(ZGB_KREISE)].copy()
    table["age_range"] = table["age_group"].map(
        lambda age: "18_29" if age in student_level.YOUNG_AGE_LABELS else age)
    theirs = table.groupby(["code", "age_range"])["students_persons"].sum()
    merged = ours.set_index(["code", "age_range"])["students_persons"]
    assert set(merged.index) == set(theirs.index)
    assert merged.loc[theirs.index].to_numpy() == pytest.approx(theirs.to_numpy())
