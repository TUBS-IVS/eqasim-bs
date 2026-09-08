"""Participation-universe Kreis controls (Plan B, issue #368): the age-range universe
mechanism (task 1) and the two seed columns built on it (task 2).

Covers the `max_age` field of `KreisAttributeControl` (Task 1's enabling mechanism for the
later education-by-age-range controls): the rendered seed expression gains an upper age
clause when `max_age` is set, and `controls_builder.person_total_by_kreis_age_range` derives
the matching per-Kreis person total from the single-year census age columns. The `max_age=None`
path must stay byte-identical to the pre-existing behaviour (no `max_age` field at all).

Task 2 covers the `work_by_employment` and `education_flag` SEED columns derived from the
plan source's legs (`mid.participation`), and their wiring into BOTH seed paths
(`load_mid_seed` / `project_completed_seed`, deliberate twins). The controls themselves,
their targets and the config toggles are later tasks.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import mid
from braunschweig.popsim.kreis_attribute_control import KreisAttributeControl
from braunschweig.popsim.stage.controls_builder import (
    age_universe_entries, person_total_by_kreis_age_range, person_total_by_kreis_min_age)


def _entry(min_age=None, max_age=None):
    return KreisAttributeControl(
        name="edu_test", seed_column="education_flag", level="person",
        categories=(("edu", "== 'edu'"), ("noedu", "== 'noedu'")),
        target_csv_relpath="x.csv", target_columns=("edu", "noedu"), tier="hard",
        min_age=min_age, max_age=max_age)


def test_max_age_renders_an_upper_age_clause_after_the_lower_one():
    exprs = [c.seed_expressions["mid"] for c in cs.attribute_kreis_controls([_entry(6, 17)])]
    assert exprs[0] == "(persons.education_flag == 'edu') & (persons.HP_ALTER >= 6) & (persons.HP_ALTER <= 17)"


def test_max_age_none_renders_byte_identical_to_before_the_field_existed():
    assert [c.seed_expressions["mid"] for c in cs.attribute_kreis_controls([_entry(14, None)])] == [
        "(persons.education_flag == 'edu') & (persons.HP_ALTER >= 14)",
        "(persons.education_flag == 'noedu') & (persons.HP_ALTER >= 14)"]
    assert cs.attribute_kreis_controls([_entry()])[0].seed_expressions["mid"] == "(persons.education_flag == 'edu')"


def _cells():
    cols = {f"{s}_AGE_{y}": [1.0, 2.0] for s in ("M", "F") for y in range(0, 101)}
    return pd.DataFrame(cols), pd.Series(["03101", "03102"])


def test_person_total_by_kreis_age_range_sums_only_the_band():
    cells, kreis = _cells()
    out = person_total_by_kreis_age_range(cells, kreis, 6, 17)
    assert out == {"03101": 2 * 12 * 1.0, "03102": 2 * 12 * 2.0}


def test_person_total_by_kreis_min_age_is_unchanged_by_the_delegation():
    cells, kreis = _cells()
    assert person_total_by_kreis_min_age(cells, kreis, 14) == person_total_by_kreis_age_range(cells, kreis, 14, 100)
    assert person_total_by_kreis_min_age(cells, kreis, 14)["03101"] == 2 * 87 * 1.0


def test_person_total_by_kreis_age_range_raises_without_any_band_column():
    cells, kreis = _cells()
    with pytest.raises(RuntimeError, match="age_range"):
        person_total_by_kreis_age_range(cells.drop(columns=[c for c in cells.columns if "_AGE_" in c]), kreis, 6, 17)


def test_person_total_by_kreis_min_age_error_names_itself_not_the_delegate():
    """person_total_by_kreis_min_age delegates to person_total_by_kreis_age_range and
    rewrites that helper's RuntimeError message to carry ITS OWN name (so a caller of the
    min_age entry point never sees an error naming a function it never called). This
    substitution is a plain string.replace on the delegate's message text: if that text is
    ever edited so the substring no longer matches, the replace silently becomes a no-op
    and the raised error would name the wrong function -- this test pins the substitution
    so such a drift fails loudly."""
    cells, kreis = _cells()
    with pytest.raises(RuntimeError, match="person_total_by_kreis_min_age") as excinfo:
        person_total_by_kreis_min_age(
            cells.drop(columns=[c for c in cells.columns if "_AGE_" in c]), kreis, 14)
    assert "person_total_by_kreis_age_range" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Task 2: the work_by_employment / education_flag SEED columns
#
# The three measured defects the new derivation must NOT reproduce (spec Plan B, #368):
#   1. compute_has_purpose_trip counts rbW legs (W_RBW == 1) the trip builder DROPS
#      whenever exclude_rbw_legs is on, so a donor with only rbW work legs was seeded
#      "has work" and realised no work trip. The new derivation's rbW filter FOLLOWS the
#      trip build's flag instead (controller ruling R8): on when the build drops them,
#      OFF -- not running at all -- when the build keeps them.
#   2. it passes the 803/804 diary non-response codes through for later age-band
#      imputation, seeding deliberately immobile plan sources as mobile;
#   3. its education code set is static, while the realised plan additionally maps
#      W_ZWECK 13 to education when escort_passive_education is on.
# --------------------------------------------------------------------------- #

def _persons_wege():
    """Five persons; only 1/1 has a DIRECT work leg.

    2/1's only work leg is an rbW summary leg (W_RBW == 1), so it counts only when the
    trip build keeps rbW legs. 3/1 borrows 1/1's diary. 4/1 is the 803 case: its diary
    trip count is the "Person ohne Wegeerfassung" non-response code and it has NO Wege
    rows at all -- the seed must read that as exactly 0, never carry the code through and
    never impute it (defect 2).
    """
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 3, 4], "P_ID": [1, 2, 1, 1, 1],
        "HP_ALTER": [40, 12, 70, 30, 50],
        "anzwege1": [2, 1, 2, 2, 803],
        "employment_status": ["vollzeit", "nicht_erwerbstaetig", "nicht_erwerbstaetig",
                              "in_ausbildung", "vollzeit"],
        "member_imputed": [False, False, False, False, False],
        "source_H_ID": [1, 1, 2, 1, 4], "source_P_ID": [1, 2, 1, 1, 1]})
    wege = pd.DataFrame({
        "H_ID": [1, 1, 1, 2, 2], "P_ID": [1, 1, 2, 1, 1], "W_ID": [1, 2, 1, 1, 2],
        "W_ZWECK": [1, 8, 3, 2, 8], "W_RBW": [0, 0, 0, 1, 1]})
    return persons, wege


def test_direct_purpose_leg_ignores_rbw_legs_and_never_passes_through_codes():
    from braunschweig.popsim.mid.participation import compute_has_direct_purpose_leg
    persons, wege = _persons_wege()
    flag = compute_has_direct_purpose_leg(persons, wege, {1, 2}, exclude_rbw_legs=True)
    # 2/1's rbW work legs do not count while the trip build drops them.
    assert flag.tolist() == [1, 0, 0, 0, 0]
    # Defect 2: the 803 source (4/1, no Wege rows) is EXACTLY 0 -- not 803, not NaN, and
    # not a value the caller could later age-band impute. The dtype must be integer, so a
    # NaN could not even be represented.
    assert flag.iloc[4] == 0
    assert flag.dtype.kind == "i"
    assert not flag.isin((803, 804)).any()
    assert not flag.isna().any()
    assert set(flag) <= {0, 1}


def test_direct_purpose_leg_follows_the_trip_builds_rbw_flag():
    """Controller ruling R8: with exclude_rbw_legs OFF the trip build KEEPS rbW legs, so
    the seed must count them -- otherwise the seed says "no work" about a plan that makes
    a work trip (arm 1 of the A/B ladder runs with the flag off)."""
    from braunschweig.popsim.mid.participation import compute_has_direct_purpose_leg
    persons, wege = _persons_wege()
    assert compute_has_direct_purpose_leg(
        persons, wege, {1, 2}, exclude_rbw_legs=False).tolist() == [1, 0, 1, 0, 0]
    # With the filter OFF the W_RBW column is never read, so it need not even be present.
    assert compute_has_direct_purpose_leg(
        persons, wege.drop(columns=["W_RBW"]), {1, 2},
        exclude_rbw_legs=False).tolist() == [1, 0, 1, 0, 0]
    # With the filter ON it IS required -- no silent fallback to "treat everything as direct".
    with pytest.raises(KeyError, match="W_RBW"):
        compute_has_direct_purpose_leg(
            persons, wege.drop(columns=["W_RBW"]), {1, 2}, exclude_rbw_legs=True)


def test_work_by_employment_labels_follow_employment_status_and_the_plan_source():
    from braunschweig.popsim.mid.participation import derive_work_by_employment_seed
    persons, wege = _persons_wege()
    out = derive_work_by_employment_seed(persons, wege, exclude_rbw_legs=True)
    assert out["work_by_employment"].tolist() == [
        "employed_work", "nonemployed_nowork", "nonemployed_nowork", "employed_work",
        "employed_nowork"]
    # 3/1 is in_ausbildung (employed by decision Q5) and borrows 1/1's direct work leg.
    # 4/1 (the 803 source, defect 2) must come out *_nowork -- the seed resolves a missing
    # diary to zero legs rather than imputing a mobile one.
    assert out["work_by_employment"].iloc[4] == "employed_nowork"


def test_work_by_employment_follows_the_trip_builds_rbw_flag():
    """Controller ruling R8, through the label: with the trip build keeping rbW legs,
    2/1's rbW work leg makes it nonemployed_WORK, not nonemployed_nowork."""
    from braunschweig.popsim.mid.participation import derive_work_by_employment_seed
    persons, wege = _persons_wege()
    out = derive_work_by_employment_seed(persons, wege, exclude_rbw_legs=False)
    assert out["work_by_employment"].tolist() == [
        "employed_work", "nonemployed_nowork", "nonemployed_work", "employed_work",
        "employed_nowork"]


def test_work_by_employment_requires_employment_status_first():
    """The guard must be the EXPLICIT one, not pandas' incidental KeyError: matching on
    'employment_status' alone passes even with the guard deleted, because
    persons["employment_status"].isin(...) raises KeyError('employment_status') by itself.
    Pinning guard-specific text pandas cannot produce keeps the test discriminating."""
    from braunschweig.popsim.mid.participation import derive_work_by_employment_seed
    persons, wege = _persons_wege()
    with pytest.raises(KeyError, match="map_employment_status") as excinfo:
        derive_work_by_employment_seed(
            persons.drop(columns=["employment_status"]), wege, exclude_rbw_legs=True)
    assert "seed_loading order" in str(excinfo.value)


def test_education_flag_counts_code_13_only_with_the_passive_escort_flag():
    from braunschweig.popsim.mid.participation import derive_education_flag_seed
    persons, wege = _persons_wege()
    wege13 = pd.concat([wege, pd.DataFrame(
        {"H_ID": [2], "P_ID": [1], "W_ID": [3], "W_ZWECK": [13], "W_RBW": [0]})])
    assert derive_education_flag_seed(
        persons, wege13, escort_passive_education=False, exclude_rbw_legs=True
    )["education_flag"].tolist() == ["noedu", "edu", "noedu", "noedu", "noedu"]
    assert derive_education_flag_seed(
        persons, wege13, escort_passive_education=True, exclude_rbw_legs=True
    )["education_flag"].tolist() == ["noedu", "edu", "edu", "noedu", "noedu"]


def test_map_flag_from_plan_source_raises_on_an_unresolved_source():
    from braunschweig.popsim.mid.participation import map_flag_from_plan_source
    persons, _ = _persons_wege()
    # Only 1/1 and 1/2 are in the donor frame; 2/1 and 4/1 are not.
    real_flag = pd.Series([1, 0], index=pd.MultiIndex.from_arrays([[1, 1], [1, 2]]))
    with pytest.raises(ValueError, match="absent from the donor frame"):
        map_flag_from_plan_source(
            persons, real_flag, household_id="H_ID", person_id="P_ID", name="work_by_employment")


def test_education_by_age_entry_names_are_derived_from_the_bounds():
    """Controller ruling R1: the education-by-age entry NAMES are derived from
    EDUCATION_AGE_BOUNDS, never re-listed, so a later task cannot register an entry name
    the bounds do not cover (or vice versa)."""
    from braunschweig.popsim.kreis_attribute_control import (
        EDUCATION_AGE_BOUNDS, EDUCATION_BY_AGE_ENTRY_NAMES, EDUCATION_FLAG_CATEGORIES,
        WORK_BY_EMPLOYMENT_CATEGORIES)
    assert EDUCATION_AGE_BOUNDS == {
        "education_0_5": (0, 5), "education_6_17": (6, 17), "education_18plus": (18, None)}
    assert EDUCATION_BY_AGE_ENTRY_NAMES == tuple(EDUCATION_AGE_BOUNDS)
    assert WORK_BY_EMPLOYMENT_CATEGORIES == (
        "employed_work", "employed_nowork", "nonemployed_work", "nonemployed_nowork")
    assert EDUCATION_FLAG_CATEGORIES == ("edu", "noedu")


def test_employed_classes_are_a_subset_of_the_employment_status_value_set():
    """Controller ruling R9: a typo in the hand-listed EMPLOYED_EMPLOYMENT_STATUS_CLASSES
    makes derive_work_by_employment_seed's isin() all-False and silently labels every
    person nonemployed_*, with a plausible-looking log line and no error. attributes.py
    raises at import time for exactly this; the pin here states the invariant where the
    seed's readers look for it."""
    from braunschweig.popsim import attributes
    assert set(attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES) <= set(
        attributes.EMPLOYMENT_STATUS_CATEGORIES)


def test_map_flag_from_plan_source_logs_which_key_the_flag_came_from(caplog):
    """Controller ruling R10: the own-key branch must not be silent.

    It is the branch that can go wrong invisibly -- a donor frame built with mismatched
    id dtypes joins to nothing, every flag comes out 0, and NOTHING raises, because
    without source_* columns the own-key choice is itself legitimate. The log line is the
    only signal, so it is pinned here: the own-key branch says so and names the columns,
    and the plan-source branch must NOT claim to be own-key.
    """
    from braunschweig.popsim.mid.participation import map_flag_from_plan_source
    persons, _ = _persons_wege()
    own = persons.drop(columns=["source_H_ID", "source_P_ID"])
    real_flag = pd.Series(
        [1, 0, 0, 0, 0],
        index=pd.MultiIndex.from_arrays([[1, 1, 2, 3, 4], [1, 2, 1, 1, 1]]))

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.mid.participation"):
        flag_own = map_flag_from_plan_source(
            own, real_flag, household_id="H_ID", person_id="P_ID", name="work_by_employment")
    assert flag_own.tolist() == [1, 0, 0, 0, 0]
    assert "work_by_employment seed: derived from each person's own key (H_ID, P_ID)" in caplog.text
    assert "no plan-source columns present" in caplog.text
    assert "5 persons" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.mid.participation"):
        map_flag_from_plan_source(
            persons, real_flag, household_id="H_ID", person_id="P_ID", name="education_flag")
    # The plan-source branch reports the SOURCE keys and never claims the own-key path.
    assert "education_flag seed: derived from the realised plan source" in caplog.text
    assert "own key" not in caplog.text


def test_employed_classes_invariant_survives_python_dash_o():
    """Controller ruling R10/item 2: the EMPLOYED-classes invariant is enforced by an
    explicit raise, not an ``assert`` -- ``assert`` statements are stripped under
    ``python -O``, which would silently remove the guard on exactly the interpreter a
    long production run is most likely to use. Pinned by reading the source, because a
    test cannot re-import the module under a different interpreter flag."""
    from pathlib import Path as _Path

    from braunschweig.popsim import attributes
    src = _Path(attributes.__file__).read_text(encoding="utf-8")
    assert "assert set(EMPLOYED_EMPLOYMENT_STATUS_CLASSES)" not in src
    assert "if not set(EMPLOYED_EMPLOYMENT_STATUS_CLASSES) <= set(EMPLOYMENT_STATUS_CATEGORIES):" in src
    assert "raise RuntimeError(" in src


# --------------------------------------------------------------------------- #
# Seed wiring: load_mid_seed / project_completed_seed (the two deliberate TWINS)
#
# Mirrors the sibling family's shape in tests/test_leisure_education_participation.py
# ("..._derives_all_three_participation_controls_when_active",
# "..._off_path_no_participation_control_touches_wege",
# "test_project_completed_seed_derives_leisure_and_education"), for the two universe
# columns. These entries are NOT in kreis_attribute_control.REGISTRY yet (a later task
# registers them with their targets), so they are constructed ad hoc here -- which also
# keeps the wiring proof independent of the registration.
#
# Every test below must FAIL if either twin loses a keyword or a derivation block, which
# is exactly what the suite could not see before: only one path was ever exercised.
# --------------------------------------------------------------------------- #

def _universe_entry(name, seed_column, categories, min_age=None, max_age=None):
    return KreisAttributeControl(
        name=name, seed_column=seed_column, level="person",
        categories=tuple((c, f"== '{c}'") for c in categories),
        target_csv_relpath="not_loaded_by_the_seed.csv", target_columns=tuple(categories),
        tier="hard", min_age=min_age, max_age=max_age)


def _universe_entries():
    """work_by_employment + one education band + the employment_status entry the first one
    DEPENDS on (attributes.map_employment_status only runs when that entry is active)."""
    from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES
    from braunschweig.popsim.kreis_attribute_control import (
        EDUCATION_FLAG_CATEGORIES, WORK_BY_EMPLOYMENT_CATEGORIES,
        WORK_BY_EMPLOYMENT_MIN_AGE_YEARS)
    return [
        _universe_entry("employment_status", "employment_status", EMPLOYMENT_STATUS_CATEGORIES,
                        min_age=WORK_BY_EMPLOYMENT_MIN_AGE_YEARS),
        _universe_entry("work_by_employment", "work_by_employment", WORK_BY_EMPLOYMENT_CATEGORIES,
                        min_age=WORK_BY_EMPLOYMENT_MIN_AGE_YEARS),
        _universe_entry("education_6_17", "education_flag", EDUCATION_FLAG_CATEGORIES,
                        min_age=6, max_age=17),
    ]


# p11: a DIRECT work leg (P_BKAT 1 -> vollzeit). p12: a passive-escort leg (W_ZWECK 13)
# and nothing else, aged 12 -> "edu" ONLY when escort_passive_education reaches the seed.
# p13: an rbW-only work leg (P_BKAT 7 -> nicht_erwerbstaetig).
_MINI_HOUSEHOLDS = (
    "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
    "1;1.0;71;1;1;1\n2;1.0;71;1;1;1\n3;1.0;71;1;1;1\n")
_MINI_PERSONS = (
    "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo;anzwege1;alter_gr1;P_BKAT\n"
    "11;1;1.0;40;1;1;1;5;1\n12;2;1.0;12;2;1;1;1;7\n13;3;1.0;50;1;1;1;5;7\n")
_MINI_WEGE = (
    "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1;W_RBW;W_SO1\n"
    "1;11;101;1;4;8;0;8;30;5.0;30;0;1\n"
    "2;12;102;13;1;9;0;9;20;2.0;20;0;1\n"
    "3;13;103;1;4;8;0;8;30;5.0;30;1;1\n")


def _write_mini_mid(tmp: Path, *, with_wege: bool = True):
    (tmp / "MiD2023_Haushalte.csv").write_text(_MINI_HOUSEHOLDS, encoding="utf-8")
    (tmp / "MiD2023_Personen.csv").write_text(_MINI_PERSONS, encoding="utf-8")
    if with_wege:
        (tmp / "MiD2023_Wege.csv").write_text(_MINI_WEGE, encoding="utf-8")


# The completed-donor twin of the same three donor persons. Its ids are STRINGS ("h1" /
# "p11"), because the projected path joins the Wege table onto in-memory frames: numeric
# ids in the CSV are read back as int64 and would not join to a string-typed frame (the
# join would then be empty and every flag silently 0 -- the sibling family's fixture is
# string-keyed for the same reason). The raw-MiD path above keeps numeric ids because
# load_mid_seed reads BOTH sides from the CSVs.
_DONOR_WEGE = (
    "H_ID;P_ID;W_ID;W_ZWECK;hvm_imp;W_SZS;W_SZM;W_AZS;W_AZM;wegkm_imp;wegmin_imp1;W_RBW;W_SO1\n"
    "h1;p11;101;1;4;8;0;8;30;5.0;30;0;1\n"
    "h2;p12;102;13;1;9;0;9;20;2.0;20;0;1\n"
    "h3;p13;103;1;4;8;0;8;30;5.0;30;1;1\n")


def _completed_donor_frames():
    from braunschweig.popsim import sources
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2", "h3"],
        cols.household_weight: [1.0, 1.0, 1.0],
        "H_GR": [1, 1, 1], "H_MIETE": [1, 1, 1], "haustyp": [1, 1, 1],
        "RegioStaR7": [71, 71, 71],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h3"],
        cols.person_id: ["p11", "p12", "p13"],
        cols.person_weight: [1.0, 1.0, 1.0],
        cols.age: [40, 12, 50],
        cols.sex: [1, 2, 1],
        "anzwege1": [1, 1, 1], "alter_gr1": [5, 1, 5], "P_BKAT": [1, 7, 7],
    })
    return cols, households, persons


def _write_wege_only(tmp: Path) -> Path:
    """Write the donor-keyed Wege table into its OWN directory, so a test may exercise
    both seed paths without the two Wege files (numeric vs string ids) colliding.
    Returns the directory to pass as ``mid_dir``."""
    donor_dir = tmp / "donor"
    donor_dir.mkdir(exist_ok=True)
    (donor_dir / "MiD2023_Wege.csv").write_text(_DONOR_WEGE, encoding="utf-8")
    return donor_dir


def test_load_mid_seed_derives_both_universe_columns_when_active(tmp_path):
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0),
        escort_passive_education=False, exclude_rbw_legs=True)
    assert "work_by_employment" in pers.columns
    assert "education_flag" in pers.columns
    by_pid = {c: dict(zip(pers["P_ID"], pers[c]))
              for c in ("work_by_employment", "education_flag")}
    assert by_pid["work_by_employment"][11] == "employed_work"
    assert by_pid["work_by_employment"][12] == "nonemployed_nowork"
    # p13's only work leg is rbW, which the trip build drops here -> nowork.
    assert by_pid["work_by_employment"][13] == "nonemployed_nowork"
    # escort_passive_education OFF -> the W_ZWECK 13 leg is not education.
    assert set(by_pid["education_flag"].values()) == {"noedu"}


def test_project_completed_seed_derives_both_universe_columns_when_active(tmp_path):
    """The TWIN of the test above: whatever load_mid_seed derives,
    project_completed_seed must derive identically (same labels, same persons)."""
    donor_dir = _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0), mid_dir=donor_dir,
        escort_passive_education=False, exclude_rbw_legs=True)
    assert "work_by_employment" in seed_p.columns
    assert "education_flag" in seed_p.columns
    by_pid = {c: dict(zip(seed_p[cols.person_id], seed_p[c]))
              for c in ("work_by_employment", "education_flag")}
    assert by_pid["work_by_employment"]["p11"] == "employed_work"
    assert by_pid["work_by_employment"]["p12"] == "nonemployed_nowork"
    assert by_pid["work_by_employment"]["p13"] == "nonemployed_nowork"
    assert set(by_pid["education_flag"].values()) == {"noedu"}


@pytest.mark.parametrize("escort_passive_education,expected", [(False, "noedu"), (True, "edu")])
def test_load_mid_seed_threads_escort_passive_education_into_the_seed(
        tmp_path, escort_passive_education, expected):
    """The flag must REACH the education_flag derivation: p12's only leg is the passive
    escort code 13, which is education exactly when the trip build maps it there."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0),
        escort_passive_education=escort_passive_education, exclude_rbw_legs=True)
    assert dict(zip(pers["P_ID"], pers["education_flag"]))[12] == expected


@pytest.mark.parametrize("escort_passive_education,expected", [(False, "noedu"), (True, "edu")])
def test_project_completed_seed_threads_escort_passive_education_into_the_seed(
        tmp_path, escort_passive_education, expected):
    """The TWIN of the test above (the two paths must not diverge on the flag)."""
    donor_dir = _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0), mid_dir=donor_dir,
        escort_passive_education=escort_passive_education, exclude_rbw_legs=True)
    assert dict(zip(seed_p[cols.person_id], seed_p["education_flag"]))["p12"] == expected


@pytest.mark.parametrize("exclude_rbw_legs,expected", [
    (True, "nonemployed_nowork"), (False, "nonemployed_work")])
def test_both_seed_paths_thread_exclude_rbw_legs_into_the_seed(
        tmp_path, exclude_rbw_legs, expected):
    """Controller ruling R8 through both twins: p13's only work leg is rbW, so its label
    must follow the trip build's flag on EITHER path."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0),
        escort_passive_education=False, exclude_rbw_legs=exclude_rbw_legs)
    assert dict(zip(pers["P_ID"], pers["work_by_employment"]))[13] == expected

    donor_dir = _write_wege_only(tmp_path)
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=_universe_entries(),
        kreis_seed_rng=np.random.RandomState(0), mid_dir=donor_dir,
        escort_passive_education=False, exclude_rbw_legs=exclude_rbw_legs)
    assert dict(zip(seed_p[cols.person_id], seed_p["work_by_employment"]))["p13"] == expected


def test_load_mid_seed_off_path_no_universe_control_touches_wege(tmp_path):
    """OFF byte-identity: with neither universe entry active, load_mid_seed must not touch
    MiD2023_Wege.csv at all -- proven by NOT writing that file."""
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path, with_wege=False)
    assert not (tmp_path / "MiD2023_Wege.csv").exists()
    _hh, pers_off, _rep = load_mid_seed(
        tmp_path, day_filter_values=(),
        kreis_control_entries=[_universe_entries()[0]],   # employment_status only
        kreis_seed_rng=np.random.RandomState(0))
    assert "work_by_employment" not in pers_off.columns
    assert "education_flag" not in pers_off.columns
    assert not (tmp_path / "MiD2023_Wege.csv").exists()


def test_project_completed_seed_off_path_needs_no_mid_dir(tmp_path):
    """The TWIN of the OFF-path test: with neither universe entry active, the projected
    path must not require mid_dir either (nothing reads the Wege table)."""
    cols, households, persons = _completed_donor_frames()
    _seed_hh, seed_p = mid.project_completed_seed(
        households, persons, cols, kreis_control_entries=[_universe_entries()[0]],
        kreis_seed_rng=np.random.RandomState(0))
    assert "work_by_employment" not in seed_p.columns
    assert "education_flag" not in seed_p.columns


def test_project_completed_seed_universe_control_requires_mid_dir():
    """No silent fallback: a universe entry active without mid_dir must fail fast, and the
    message must NAME the entries that demanded the Wege table."""
    cols, households, persons = _completed_donor_frames()
    with pytest.raises(ValueError, match="mid_dir is not set") as excinfo:
        mid.project_completed_seed(
            households, persons, cols, kreis_control_entries=_universe_entries(),
            kreis_seed_rng=np.random.RandomState(0))
    assert "work_by_employment" in str(excinfo.value)
    assert "education_6_17" in str(excinfo.value)


def test_education_flag_is_derived_once_for_every_active_age_band(tmp_path):
    """Every education-by-age entry reads the SAME education_flag column, so activating
    two bands must produce exactly one column, not two derivations that could disagree."""
    from braunschweig.popsim.kreis_attribute_control import EDUCATION_FLAG_CATEGORIES
    from braunschweig.popsim.mid import load_mid_seed
    _write_mini_mid(tmp_path)
    entries = _universe_entries() + [
        _universe_entry("education_0_5", "education_flag", EDUCATION_FLAG_CATEGORIES,
                        min_age=0, max_age=5)]
    _hh, pers, _rep = load_mid_seed(
        tmp_path, day_filter_values=(), kreis_control_entries=entries,
        kreis_seed_rng=np.random.RandomState(0),
        escort_passive_education=True, exclude_rbw_legs=True)
    assert list(pers.columns).count("education_flag") == 1
    assert set(pers["education_flag"]) <= set(EDUCATION_FLAG_CATEGORIES)


# --------------------------------------------------------------------------- #
# Task 3: registry entries, toggles, replacement rules (Plan B, issue #368,
# ADR-0109). The four target CSVs are committed (a prior task built and closed
# them), so these tests exercise the REAL registry entries and REAL config-toggle
# wiring end to end -- unlike the ad hoc entries used above for the seed-wiring
# proof, which deliberately stayed independent of the registration.
# --------------------------------------------------------------------------- #

def test_registry_carries_the_four_universe_entries_as_hard_person_controls():
    from braunschweig.popsim.kreis_attribute_control import (
        REGISTRY, EDUCATION_AGE_BOUNDS, WORK_BY_EMPLOYMENT_CATEGORIES)
    by = {c.name: c for c in REGISTRY}
    w = by["work_by_employment"]
    assert (w.level, w.tier, w.min_age, w.max_age, w.seed_column) == ("person", "hard", 14, None, "work_by_employment")
    assert [lbl for lbl, _ in w.categories] == list(WORK_BY_EMPLOYMENT_CATEGORIES)
    for name, (lo, hi) in EDUCATION_AGE_BOUNDS.items():
        e = by[name]
        assert (e.seed_column, e.tier, e.min_age, e.max_age) == ("education_flag", "hard", lo, hi)
        assert e.target_csv_relpath.endswith(f"target2026_{name}_by_kreis.csv")


def test_importance_group_is_kreis_hard_for_every_rendered_column():
    from braunschweig.popsim import control_spec as cs
    from braunschweig.popsim.kreis_attribute_control import REGISTRY, control_columns
    for c in REGISTRY:
        if c.name in ("work_by_employment", "education_0_5", "education_6_17", "education_18plus"):
            for col in control_columns(c):
                assert cs.importance_group_for_field(f"{col}_KREIS") == "kreis_hard", col


def test_toggles_registered_with_the_decided_defaults():
    from braunschweig.popsim.stage import config_keys as ck
    assert ck._KREIS_CONTROL_TOGGLE_KEY["work_by_employment"] == ck.KEY_WORK_BY_EMPLOYMENT_CONTROL
    assert all(ck._KREIS_CONTROL_TOGGLE_KEY[n] == ck.KEY_EDUCATION_BY_AGE_CONTROL
               for n in ("education_0_5", "education_6_17", "education_18plus"))
    assert ck._KREIS_CONTROL_DEFAULT["work_by_employment"] == "on" and ck._KREIS_CONTROL_DEFAULT["education_6_17"] == "on"
    assert ck._KREIS_CONTROL_DEFAULT["work_participation"] == "off" and ck._KREIS_CONTROL_DEFAULT["education_participation"] == "off"
    # The three education_by_age entries share ONE config toggle, so their DEFAULT values
    # must agree by construction (config_keys._EDUCATION_BY_AGE_DEFAULT) -- a real run only
    # ever resolves one value for all three. Pinned explicitly (not just relied on via the
    # shared constant) so a future edit that reintroduces three independent literals here
    # cannot silently make configure() (which reads the "education_6_17" entry) disagree
    # with a test double that resolves the shared key via a different one of the three names.
    assert (ck._KREIS_CONTROL_DEFAULT["education_0_5"]
            == ck._KREIS_CONTROL_DEFAULT["education_6_17"]
            == ck._KREIS_CONTROL_DEFAULT["education_18plus"])


class _Ctx:
    def __init__(self, overrides):
        from braunschweig.popsim.stage import config_keys as ck
        self._v = {ck._KREIS_CONTROL_TOGGLE_KEY[n]: d for n, d in ck._KREIS_CONTROL_DEFAULT.items()}
        self._v.update(overrides)

    def config(self, key):
        return self._v[key]


def test_default_configuration_activates_the_new_and_drops_the_replaced_controls():
    from braunschweig.popsim.stage.source_resolution import active_kreis_entries
    names = {e.name for e in active_kreis_entries(_Ctx({}), "mid")}
    assert {"work_by_employment", "education_0_5", "education_6_17", "education_18plus",
            "employment_status"} <= names
    assert not ({"work_participation", "education_participation"} & names)


@pytest.mark.parametrize("overrides, keys", [
    ({"braunschweig.population.popsim.work_participation_kreis_control": "on"},
     ("braunschweig.population.popsim.work_by_employment_kreis_control",
      "braunschweig.population.popsim.work_participation_kreis_control")),
    ({"braunschweig.population.popsim.employment_status_kreis_control": "off"},
     ("braunschweig.population.popsim.work_by_employment_kreis_control",
      "braunschweig.population.popsim.employment_status_kreis_control")),
    ({"braunschweig.population.popsim.education_participation_kreis_control": "on"},
     ("braunschweig.population.popsim.education_by_age_kreis_control",
      "braunschweig.population.popsim.education_participation_kreis_control")),
])
def test_contradictory_toggles_raise_naming_both_keys(overrides, keys):
    """Each of the three raises must name BOTH the replacement's and the legacy/dependency
    key, fully qualified, so a user can grep their config for the exact string (fix round 1
    item 6) -- asserting a single ``match`` substring left the second key name unverified."""
    from braunschweig.popsim.stage.source_resolution import active_kreis_entries
    with pytest.raises(ValueError) as excinfo:
        active_kreis_entries(_Ctx(overrides), "mid")
    message = str(excinfo.value)
    for key in keys:
        assert key in message, f"{key!r} not found in raise message: {message!r}"


def test_legacy_configuration_is_unchanged(tmp_path):
    """New toggles off + old ones on = exactly today's active set (OFF path)."""
    from braunschweig.popsim.stage.source_resolution import active_kreis_entries
    ctx = _Ctx({"braunschweig.population.popsim.work_by_employment_kreis_control": "off",
                "braunschweig.population.popsim.education_by_age_kreis_control": "off",
                "braunschweig.population.popsim.work_participation_kreis_control": "on",
                "braunschweig.population.popsim.education_participation_kreis_control": "on"})
    assert [e.name for e in active_kreis_entries(ctx, "mid")] == [
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike", "trip_class", "employment_status",
        "pt_ticket_group4", "work_participation", "leisure_participation", "education_participation", "escort_participation"]


# --------------------------------------------------------------------------- #
# Final fix wave, item 1: the PRODUCTION parquet load set must carry every active
# universe control's denominator columns.
#
# The tests above all run on `_cells()`, which hands the denominator helpers all 101
# single-year columns for both sexes. The production stage never loads that set: it
# resolves the parquet columns through `stage._resolve_cell_load_columns`, which (before
# this fix) requested single-year ages only INCIDENTALLY -- 10-19 for the fine teen bands
# and 16+ for the employment grid. `education_0_5` therefore had NO column of its band and
# `education_6_17` silently lost ages 6-9. The tests below drive that REAL resolution
# instead of a pre-populated fixture, so the same gap can never reopen unnoticed.
# --------------------------------------------------------------------------- #

# The production control configuration (configs/base_bs.yml): catalog source, MiD seed,
# all four tiers, employment grid / ownership grid / fine teen bands / income tilt ON.
_PRODUCTION_TIERS = ("tier0", "tier1", "tier2", "tier3")
_KEY_INCOME_TILT = "braunschweig.population.popsim.income_spatial_tilt"


def _write_prepared_cells_parquet(tmp_path, base_cols):
    """A parquet whose SCHEMA mirrors the production prepared-cells file for the columns
    this test is about: every catalog census-source column plus the single-year
    ``{M,F}_AGE_<year>`` columns for ages 0-100.

    The real file carries that full single-year range for both sexes (measured against its
    schema during the whole-branch review: 174/174 columns for the 14+ employment universe,
    166/166 for 18+). The defect was never a missing parquet column -- it was the load
    SELECTION -- so a schema-faithful synthetic file is what discriminates here.
    """
    from braunschweig.popsim.stage.controls_builder import SINGLE_YEAR_MAX_AGE
    columns = list(dict.fromkeys([
        "GITTER_ID_100m",
        *base_cols,
        *(f"{prefix}_AGE_{year}"
          for prefix in ("M", "F")
          for year in range(0, SINGLE_YEAR_MAX_AGE + 1)),
    ]))
    path = tmp_path / "prepared_cells.parquet"
    pd.DataFrame({name: [1.0, 2.0] for name in columns}).to_parquet(path, index=False)
    return path


def _resolve_production_load_columns(tmp_path):
    """Run the stage's OWN column resolution under the production configuration.

    Returns ``(load_cols, active_entries)``.
    """
    from braunschweig.popsim import stage

    ctx = _Ctx({_KEY_INCOME_TILT: True})
    active_entries = stage.active_kreis_entries(ctx, "mid")
    controls_df, base_cols = stage._build_control_frame(
        "catalog", None, "mid", _PRODUCTION_TIERS, True,
        tuple(entry.name for entry in active_entries), "uniform",
        fine_teen_age_bands=True, ownership_grid_on=True)
    assert not controls_df.empty
    cells_path = _write_prepared_cells_parquet(tmp_path, base_cols)
    load_cols = stage._resolve_cell_load_columns(
        ctx, "catalog", "mid", _PRODUCTION_TIERS, base_cols, True, cells_path,
        active_entries, fine_teen_age_bands=True, ownership_grid_on=True)
    return load_cols, active_entries


def test_production_load_columns_cover_every_active_universe_denominator(tmp_path):
    """Every active age-universe control's per-Kreis denominator is computable, and
    COMPLETE, from the columns the stage actually loads.

    Each cell column holds 1.0 in the first row and 2.0 in the second, so a per-Kreis total
    of ``2 * (upper - lower + 1)`` for the first Kreis proves that ALL of the band's
    ``{M,F}_AGE_<year>`` columns -- both sexes, every year -- reached the frame. A single
    missing year makes the assertion fail rather than silently shrinking the universe.
    """
    from braunschweig.popsim.stage import (
        person_total_by_kreis_age_range, person_total_by_kreis_min_age)

    load_cols, active_entries = _resolve_production_load_columns(tmp_path)
    # The production predicate itself ("which entries have an age universe, over which
    # years"), so this test cannot drift from what the load path actually derives; the
    # membership assertion below is what keeps an empty list from passing vacuously.
    universe_entries = age_universe_entries(active_entries)
    # Guard the guard: if the REGISTRY ever stops declaring age universes, an empty loop
    # below would pass while asserting nothing.
    assert {entry.name for entry, _, _ in universe_entries} >= {
        "employment_status", "work_by_employment",
        "education_0_5", "education_6_17", "education_18plus"}

    cells = pd.DataFrame({name: [1.0, 2.0] for name in load_cols})
    kreis = pd.Series(["03101", "03102"])
    for entry, lower, upper in universe_entries:
        expected_columns = [f"{prefix}_AGE_{year}"
                            for prefix in ("M", "F")
                            for year in range(lower, upper + 1)]
        missing = [c for c in expected_columns if c not in load_cols]
        assert not missing, (
            f"{entry.name}: {len(missing)} of {len(expected_columns)} denominator columns "
            f"are not in the resolved parquet load set (e.g. {missing[:6]})")
        totals = person_total_by_kreis_age_range(cells, kreis, lower, upper)
        assert totals["03101"] == pytest.approx(2 * (upper - lower + 1))
        assert totals["03102"] == pytest.approx(4 * (upper - lower + 1))
        if entry.max_age is None:
            # The stage dispatches an entry without an upper bound to the min_age entry
            # point; it must agree with the range helper on the same universe.
            assert person_total_by_kreis_min_age(cells, kreis, entry.min_age) == totals


def test_age_range_denominator_raises_on_a_partially_covered_band():
    """The complete-coverage guard: a band that is only PARTLY present raises and names the
    missing years, instead of returning a plausible total over a shorter universe.

    This is the half of the fix that catches a future load-set regression -- ``education_6_17``
    was silently computed over ages 10-17 because SOME of its columns were present, which the
    old "no columns at all" guard could not see.
    """
    cells, kreis = _cells()
    partial = cells.drop(columns=[f"{prefix}_AGE_{year}"
                                  for prefix in ("M", "F") for year in range(6, 10)])
    with pytest.raises(RuntimeError) as excinfo:
        person_total_by_kreis_age_range(partial, kreis, 6, 17)
    message = str(excinfo.value)
    assert "6, 7, 8, 9" in message
    assert "8 of the 24" in message
