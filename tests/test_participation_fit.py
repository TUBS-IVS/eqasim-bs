"""Tests for braunschweig/analysis/population_validation/participation_fit.py
(Task 7 of feature #224; universe-control additions Task 8 of Plan B, issue #368).

Covers the four purpose-only public interfaces:
- ``realised_participation`` on both the eqasim purpose-string trip schema and
  the raw MiD ``W_ZWECK`` schema, on tiny synthetic fixtures with known
  per-Kreis participation/mobility rates;
- ``load_participation_targets`` against the real committed SrV target CSVs;
- ``participation_fit`` joining a realised fixture to a synthetic target,
  including the no-silent-fallback drop of realised cells without a target;
- ``donor_neff`` on a fixture with known donor duplication, and its
  no-silent-fallback raise when the donor id column is absent.

Plus the three participation-UNIVERSE interfaces (Plan B, issue #368):
- ``realised_universe_participation`` on both trip schemas, with dedicated
  boundary tests per control proving each universe filter (age band,
  employment-status class) actually restricts the denominator -- a person
  inside the frame but outside a control's own universe must not move that
  control's share (see kreis_attribute_control.WORK_BY_EMPLOYMENT_MIN_AGE_YEARS
  / EDUCATION_AGE_BOUNDS, the same bounds the registry entries declare);
- ``load_universe_targets`` against the real committed target2026_work_by_
  employment / target2026_education_{0_5,6_17,18plus} files;
- ``universe_participation_fit`` joining a realised fixture to a synthetic
  target, including the same no-silent-fallback drop behaviour.
"""
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.analysis.population_validation.participation_fit import (  # noqa: E402
    donor_neff,
    load_participation_targets,
    load_universe_targets,
    participation_fit,
    realised_participation,
    realised_universe_participation,
    universe_participation_fit,
)
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    EDUCATION_AGE_BOUNDS, WORK_BY_EMPLOYMENT_CATEGORIES, WORK_BY_EMPLOYMENT_MIN_AGE_YEARS)

TARGETS_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "targets"

# --- Shared fixture: 4 persons in 2 Kreise, purpose-string trip schema. ---
#
# Kreis 03101: person 1 (one work round-trip PLUS one escort round-trip),
# person 2 (fully immobile).
#   -> work 1/2=0.5, leisure 0/2=0.0, education 0/2=0.0, escort 1/2=0.5,
#      mobility 1/2=0.5
# Kreis 03102: person 3 (one leisure round-trip), person 4 (one education
# round-trip).
#   -> work 0/2=0.0, leisure 1/2=0.5, education 1/2=0.5, escort 0/2=0.0,
#      mobility 2/2=1.0


def _persons_kreis():
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "ars5": ["03101", "03101", "03102", "03102"],
    })


def _trips_purpose_schema():
    return pd.DataFrame({
        "person_id": [1, 1, 1, 1, 3, 3, 4, 4],
        "preceding_purpose": ["home", "work", "home", "escort",
                              "home", "leisure", "home", "education"],
        "following_purpose": ["work", "home", "escort", "home",
                              "leisure", "home", "education", "home"],
    })


def _trips_wzweck_schema():
    # One trip per mobile person is enough under the W_ZWECK schema: a person
    # participates in purpose P as soon as one trip carries a W_ZWECK code
    # from mid.PARTICIPATION_W_ZWECK[P]. 1 = Arbeit (work), 7 = Freizeit
    # (leisure), 3 = Ausbildung/Schule (education), 6 = Bringen/Holen (the
    # ACTIVE escort leg, issue #227).
    return pd.DataFrame({
        "person_id": [1, 1, 3, 4],
        "W_ZWECK": [1, 6, 7, 3],
    })


_EXPECTED = {
    ("03101", "work"): 0.5,
    ("03101", "leisure"): 0.0,
    ("03101", "education"): 0.0,
    ("03101", "escort"): 0.5,
    ("03101", "mobility"): 0.5,
    ("03102", "work"): 0.0,
    ("03102", "leisure"): 0.5,
    ("03102", "education"): 0.5,
    ("03102", "escort"): 0.0,
    ("03102", "mobility"): 1.0,
}


def _assert_matches_expected(result: pd.DataFrame):
    assert set(result.columns) == {"ars5", "purpose", "realised_rate", "n_persons"}
    assert len(result) == 10
    indexed = result.set_index(["ars5", "purpose"])
    for (ars5, purpose), expected_rate in _EXPECTED.items():
        assert indexed.loc[(ars5, purpose), "realised_rate"] == pytest.approx(expected_rate)
        assert indexed.loc[(ars5, purpose), "n_persons"] == 2


def test_realised_participation_purpose_string_schema():
    result = realised_participation(_trips_purpose_schema(), _persons_kreis())
    _assert_matches_expected(result)


def test_realised_participation_wzweck_schema():
    result = realised_participation(_trips_wzweck_schema(), _persons_kreis())
    _assert_matches_expected(result)


def test_realised_participation_raises_on_unknown_trip_schema():
    trips = pd.DataFrame({"person_id": [1, 2], "some_other_column": ["a", "b"]})
    with pytest.raises(KeyError, match="following_purpose"):
        realised_participation(trips, _persons_kreis())


def test_load_participation_targets_real_committed_files():
    targets = load_participation_targets(TARGETS_DIR)
    assert set(targets.columns) == {"ars5", "purpose", "target_rate"}
    assert set(targets["purpose"]) == {"work", "leisure", "education", "escort", "mobility"}
    assert ((targets["target_rate"] >= 0.0) & (targets["target_rate"] <= 1.0)).all()
    # Every Kreis present in the work-participation target must also carry a
    # mobility row derived from the trip-class target.
    work_ars5 = set(targets.loc[targets["purpose"] == "work", "ars5"])
    mobility_ars5 = set(targets.loc[targets["purpose"] == "mobility", "ars5"])
    assert work_ars5 <= mobility_ars5


def test_load_participation_targets_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_participation_targets(tmp_path)


def _write_synthetic_targets(tmp_path: Path) -> Path:
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "target2026_work_participation_by_kreis.csv").write_text(
        "ars5,source,n_effective,work_yes,work_no\n"
        "03101,srv,100,0.4,0.6\n"
        "03102,srv,100,0.1,0.9\n",
        encoding="utf-8")
    (targets_dir / "target2026_leisure_participation_by_kreis.csv").write_text(
        "ars5,source,n_effective,leisure_yes,leisure_no\n"
        "03101,srv,100,0.1,0.9\n"
        "03102,srv,100,0.6,0.4\n",
        encoding="utf-8")
    (targets_dir / "target2026_education_participation_by_kreis.csv").write_text(
        "ars5,source,n_effective,education_yes,education_no\n"
        "03101,srv,100,0.05,0.95\n"
        "03102,srv,100,0.55,0.45\n",
        encoding="utf-8")
    (targets_dir / "target2026_escort_participation_by_kreis.csv").write_text(
        "ars5,source,n_effective,escort_yes,escort_no\n"
        "03101,srv,100,0.2,0.8\n"
        "03102,srv,100,0.05,0.95\n",
        encoding="utf-8")
    (targets_dir / "target2026_trip_class_by_kreis.csv").write_text(
        "ars5,source,n_effective,trips_0,trips_1_2,trips_3_4,trips_5plus\n"
        "03101,srv,100,0.4,0.3,0.2,0.1\n"
        "03102,srv,100,0.0,0.3,0.3,0.4\n",
        encoding="utf-8")
    return targets_dir


def test_participation_fit_abs_error_on_fixture(tmp_path):
    targets_dir = _write_synthetic_targets(tmp_path)
    result = participation_fit(_trips_purpose_schema(), _persons_kreis(), targets_dir)

    assert set(result.columns) == {"ars5", "purpose", "realised_rate", "target_rate", "abs_error"}
    assert len(result) == 10

    indexed = result.set_index(["ars5", "purpose"])
    expected_abs_error = {
        ("03101", "work"): 0.1,
        ("03101", "leisure"): 0.1,
        ("03101", "education"): 0.05,
        ("03101", "escort"): 0.3,   # realised 0.5 vs synthetic target 0.2
        ("03101", "mobility"): 0.1,
        ("03102", "work"): 0.1,
        ("03102", "leisure"): 0.1,
        ("03102", "education"): 0.05,
        ("03102", "escort"): 0.05,  # realised 0.0 vs synthetic target 0.05
        ("03102", "mobility"): 0.0,
    }
    for key, expected in expected_abs_error.items():
        assert indexed.loc[key, "abs_error"] == pytest.approx(expected, abs=1e-9)


def test_participation_fit_drops_realised_cells_without_target(tmp_path, caplog):
    targets_dir = _write_synthetic_targets(tmp_path)
    persons_kreis = pd.concat([
        _persons_kreis(),
        pd.DataFrame({"person_id": [5], "ars5": ["03999"]}),
    ], ignore_index=True)
    trips = _trips_purpose_schema()

    with caplog.at_level(logging.WARNING):
        result = participation_fit(trips, persons_kreis, targets_dir)

    assert "03999" not in set(result["ars5"])
    assert any("03999" in record.message or "no matching" in record.message
               for record in caplog.records)


def test_donor_neff_known_duplication():
    # Donor "A" copied 3x, "B" copied 2x, "C" copied 1x -> N=6.
    persons = pd.DataFrame({
        "person_id": range(6),
        "source_P_ID": ["A", "A", "A", "B", "B", "C"],
    })
    result = donor_neff(persons, "source_P_ID")

    n = 6
    sum_sq_copies = 3 ** 2 + 2 ** 2 + 1 ** 2  # 14
    expected_n_eff = (n ** 2) / sum_sq_copies  # 36/14
    assert result["n"] == n
    assert result["n_eff"] == pytest.approx(expected_n_eff)
    assert result["n_eff_fraction"] == pytest.approx(expected_n_eff / n)
    assert result["max_copies_over_median"] == pytest.approx(3 / 2.0)


def test_donor_neff_raises_on_missing_donor_id_column():
    persons = pd.DataFrame({"person_id": [1, 2, 3]})
    with pytest.raises(KeyError, match="source_P_ID"):
        donor_neff(persons, "source_P_ID")


# ---------------------------------------------------------------------------
# Participation-UNIVERSE controls (Plan B, issue #368, Task 8):
# realised_universe_participation / load_universe_targets / universe_participation_fit
# ---------------------------------------------------------------------------
#
# Shared fixture: 10 persons in 2 Kreise, purpose-string trip schema.
#
# Kreis 03101 (four working-age adults, age 20 -- inside work_by_employment AND
# education_18plus, outside both education_0_5 and education_6_17):
#   P1 vollzeit  + work leg    -> work_by_employment employed_work
#   P2 nicht_erwerbstaetig + work leg -> nonemployed_work
#   P3 vollzeit  + no trip     -> employed_nowork
#   P4 nicht_erwerbstaetig + no trip  -> nonemployed_nowork
#   None of the four have an education leg -> education_18plus all noedu.
#   -> work_by_employment: each of the 4 categories 1/4 = 0.25, n=4.
#   -> education_18plus:   edu 0/4=0.0, noedu 4/4=1.0, n=4.
#   -> education_0_5 / education_6_17: universe EMPTY at 03101 (no row).
#
# Kreis 03102 (two Kita-age, two school-age, two working-age-but-young/senior):
#   P5 age 4  + education leg -> education_0_5 edu
#   P6 age 4  + no trip       -> education_0_5 noedu
#   P7 age 12 + education leg -> education_6_17 edu (also < 14: OUTSIDE
#       work_by_employment -- proves that control's own universe excludes them)
#   P8 age 12 + no trip       -> education_6_17 noedu (same age-14 exclusion)
#   P9 age 25 teilzeit (employed) + no trip -> work_by_employment employed_nowork;
#       education_18plus noedu (no education leg)
#   P10 age 25 nicht_erwerbstaetig + work leg -> work_by_employment nonemployed_work;
#       education_18plus noedu
#   -> education_0_5:      edu 1/2=0.5, noedu 1/2=0.5, n=2.
#   -> education_6_17:     edu 1/2=0.5, noedu 1/2=0.5, n=2.
#   -> work_by_employment: employed_nowork 1/2=0.5, nonemployed_work 1/2=0.5,
#      employed_work 0.0, nonemployed_nowork 0.0, n=2 (P7/P8 excluded by age).
#   -> education_18plus:   edu 0/2=0.0, noedu 2/2=1.0, n=2 (P5-P8 excluded by age).


def _persons_kreis_universe():
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "ars5": ["03101", "03101", "03101", "03101",
                 "03102", "03102", "03102", "03102", "03102", "03102"],
        "age": [20, 20, 20, 20, 4, 4, 12, 12, 25, 25],
        "employment_status": [
            "vollzeit", "nicht_erwerbstaetig", "vollzeit", "nicht_erwerbstaetig",
            "nicht_erwerbstaetig", "nicht_erwerbstaetig",
            "nicht_erwerbstaetig", "nicht_erwerbstaetig",
            "teilzeit", "nicht_erwerbstaetig",
        ],
    })


def _trips_purpose_schema_universe():
    return pd.DataFrame({
        "person_id": [1, 1, 2, 2, 5, 5, 7, 7, 10, 10],
        "preceding_purpose": ["home", "work", "home", "work",
                              "home", "education", "home", "education", "home", "work"],
        "following_purpose": ["work", "home", "work", "home",
                              "education", "home", "education", "home", "work", "home"],
    })


def _trips_wzweck_schema_universe():
    # W_ZWECK 1 = Arbeit (work, in PARTICIPATION_W_ZWECK["work"] = {1, 2}); 3 =
    # Ausbildung/Schule (education, in PARTICIPATION_W_ZWECK["education"] = {3, 11, 12}).
    # One trip per participating person, exactly like _trips_wzweck_schema above.
    return pd.DataFrame({
        "person_id": [1, 2, 5, 7, 10],
        "W_ZWECK": [1, 1, 3, 3, 1],
    })


_EXPECTED_UNIVERSE = {
    ("03101", "work_by_employment", "employed_work"): (0.25, 4),
    ("03101", "work_by_employment", "employed_nowork"): (0.25, 4),
    ("03101", "work_by_employment", "nonemployed_work"): (0.25, 4),
    ("03101", "work_by_employment", "nonemployed_nowork"): (0.25, 4),
    ("03101", "education_18plus", "edu"): (0.0, 4),
    ("03101", "education_18plus", "noedu"): (1.0, 4),
    ("03102", "education_0_5", "edu"): (0.5, 2),
    ("03102", "education_0_5", "noedu"): (0.5, 2),
    ("03102", "education_6_17", "edu"): (0.5, 2),
    ("03102", "education_6_17", "noedu"): (0.5, 2),
    ("03102", "work_by_employment", "employed_work"): (0.0, 2),
    ("03102", "work_by_employment", "employed_nowork"): (0.5, 2),
    ("03102", "work_by_employment", "nonemployed_work"): (0.5, 2),
    ("03102", "work_by_employment", "nonemployed_nowork"): (0.0, 2),
    ("03102", "education_18plus", "edu"): (0.0, 2),
    ("03102", "education_18plus", "noedu"): (1.0, 2),
}


def _assert_matches_expected_universe(result: pd.DataFrame):
    assert set(result.columns) == {"ars5", "control", "category", "realised_share", "n_persons"}
    assert len(result) == 16
    indexed = result.set_index(["ars5", "control", "category"])
    for key, (expected_share, expected_n) in _EXPECTED_UNIVERSE.items():
        assert indexed.loc[key, "realised_share"] == pytest.approx(expected_share)
        assert indexed.loc[key, "n_persons"] == expected_n
    # A Kreis with NO member of a control's universe (03101 has nobody aged 0-17)
    # must produce NO row for that (ars5, control) pair -- not a defaulted 0 share,
    # which would hide a broken/absent age filter behind a plausible-looking number.
    present = set(zip(result["ars5"], result["control"]))
    assert ("03101", "education_0_5") not in present
    assert ("03101", "education_6_17") not in present


def test_realised_universe_participation_purpose_string_schema():
    result = realised_universe_participation(_trips_purpose_schema_universe(), _persons_kreis_universe())
    _assert_matches_expected_universe(result)


def test_realised_universe_participation_wzweck_schema():
    result = realised_universe_participation(_trips_wzweck_schema_universe(), _persons_kreis_universe())
    _assert_matches_expected_universe(result)


def test_realised_universe_participation_raises_on_unknown_trip_schema():
    trips = pd.DataFrame({"person_id": [1, 2], "some_other_column": ["a", "b"]})
    persons = pd.DataFrame({
        "person_id": [1, 2], "ars5": ["03101", "03101"],
        "age": [20, 20], "employment_status": ["vollzeit", "nicht_erwerbstaetig"],
    })
    with pytest.raises(KeyError, match="following_purpose"):
        realised_universe_participation(trips, persons)


def test_realised_universe_participation_raises_on_missing_persons_columns():
    # No 'employment_status' / 'age': a realised share computed without them would
    # silently answer a different (undifferentiated) universe question.
    persons = pd.DataFrame({"person_id": [1], "ars5": ["03101"]})
    trips = pd.DataFrame({
        "person_id": [1], "preceding_purpose": ["home"], "following_purpose": ["work"]})
    with pytest.raises(KeyError, match="employment_status"):
        realised_universe_participation(trips, persons)


# --- Universe-boundary discrimination tests -------------------------------
#
# Each test below includes at least one person INSIDE the frame but OUTSIDE the
# control's own universe, and gives that person the OPPOSITE category from the
# in-universe person(s) -- so a universe filter that silently failed (or was
# off-by-one at a bound) would visibly shift both realised_share AND n_persons
# away from the expected value. A test where the excluded person shares the
# same category as everyone else could not tell a working filter from a
# missing one; these are built so it always can.

def test_realised_universe_participation_work_by_employment_boundary_is_inclusive_from_min_age():
    persons = pd.DataFrame({
        "person_id": [1, 2],
        "ars5": ["03101", "03101"],
        # Person 1 is exactly at the minimum age (must be INCLUDED); person 2 is
        # one year younger (must be EXCLUDED) -- tests the inclusive lower bound.
        "age": [WORK_BY_EMPLOYMENT_MIN_AGE_YEARS, WORK_BY_EMPLOYMENT_MIN_AGE_YEARS - 1],
        "employment_status": ["vollzeit", "nicht_erwerbstaetig"],
    })
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2],
        "preceding_purpose": ["home", "work", "home", "work"],
        "following_purpose": ["work", "home", "work", "home"],
    })
    result = realised_universe_participation(trips, persons)
    wbe = result[result["control"] == "work_by_employment"].set_index("category")
    # If person 2 (non-employed, has a work leg) were wrongly included, this
    # would drop from 1.0 (1/1) to 0.5 (1/2) and n_persons would read 2, not 1.
    assert wbe.loc["employed_work", "realised_share"] == pytest.approx(1.0)
    assert wbe.loc["employed_work", "n_persons"] == 1
    assert set(wbe.index) == set(WORK_BY_EMPLOYMENT_CATEGORIES)


def test_realised_universe_participation_education_0_5_boundaries_are_inclusive():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "ars5": ["03101", "03101", "03101"],
        "age": [0, 5, 6],  # 0 and 5 are the inclusive band edges; 6 is just outside
        "employment_status": ["nicht_erwerbstaetig"] * 3,
    })
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2],
        "preceding_purpose": ["home", "education", "home", "education"],
        "following_purpose": ["education", "home", "education", "home"],
    })
    result = realised_universe_participation(trips, persons)
    e05 = result[result["control"] == "education_0_5"].set_index("category")
    # Person 3 (age 6, no education leg) must be excluded: if included, edu
    # would drop from 1.0 (2/2) to 0.667 (2/3) and n_persons would read 3.
    assert e05.loc["edu", "realised_share"] == pytest.approx(1.0)
    assert e05.loc["edu", "n_persons"] == 2


def test_realised_universe_participation_education_6_17_boundaries_are_inclusive():
    lo, hi = EDUCATION_AGE_BOUNDS["education_6_17"]
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "ars5": ["03101"] * 4,
        # 1 and 3 sit exactly on the inclusive edges (must be INCLUDED); 2 and 4
        # sit one year outside each edge (must be EXCLUDED).
        "age": [lo, lo - 1, hi, hi + 1],
        "employment_status": ["nicht_erwerbstaetig"] * 4,
    })
    trips = pd.DataFrame({
        "person_id": [1, 1, 3, 3],
        "preceding_purpose": ["home", "education", "home", "education"],
        "following_purpose": ["education", "home", "education", "home"],
    })
    result = realised_universe_participation(trips, persons)
    e617 = result[result["control"] == "education_6_17"].set_index("category")
    # If either out-of-band neighbour (age lo-1 or hi+1, neither with an
    # education leg) were wrongly included, edu would drop below 1.0 and
    # n_persons would exceed 2.
    assert e617.loc["edu", "realised_share"] == pytest.approx(1.0)
    assert e617.loc["edu", "n_persons"] == 2


def test_realised_universe_participation_education_18plus_boundary_and_no_upper_bound():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "ars5": ["03101"] * 3,
        # 18 is the inclusive lower edge; 95 proves there is genuinely NO upper
        # bound; 17 is one year below the edge and must be EXCLUDED.
        "age": [18, 95, 17],
        "employment_status": ["nicht_erwerbstaetig"] * 3,
    })
    # Only person 3 (excluded, age 17) has an education leg.
    trips = pd.DataFrame({
        "person_id": [3, 3],
        "preceding_purpose": ["home", "education"],
        "following_purpose": ["education", "home"],
    })
    result = realised_universe_participation(trips, persons)
    e18 = result[result["control"] == "education_18plus"].set_index("category")
    # If person 3 were wrongly included, noedu would drop from 1.0 (2/2) to
    # 0.667 (2/3) and n_persons would read 3, not 2.
    assert e18.loc["noedu", "realised_share"] == pytest.approx(1.0)
    assert e18.loc["noedu", "n_persons"] == 2


# --- load_universe_targets --------------------------------------------------

def test_load_universe_targets_real_committed_files():
    targets = load_universe_targets(TARGETS_DIR)
    assert set(targets.columns) == {"ars5", "control", "category", "target_share"}
    assert set(targets["control"]) == {
        "work_by_employment", "education_0_5", "education_6_17", "education_18plus"}
    assert ((targets["target_share"] >= 0.0) & (targets["target_share"] <= 1.0)).all()
    # Every committed file carries the 7 SrV Kreise + 03103 + Gesamt = 9 rows.
    for control in ("work_by_employment", "education_0_5", "education_6_17", "education_18plus"):
        n_categories = 4 if control == "work_by_employment" else 2
        assert (targets["control"] == control).sum() == 9 * n_categories, control


def test_load_universe_targets_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_universe_targets(tmp_path)


def test_load_universe_targets_missing_column_raises(tmp_path):
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "target2026_work_by_employment_by_kreis.csv").write_text(
        # nonemployed_nowork column missing.
        "ars5,source,n_effective,employed_work,employed_nowork,nonemployed_work\n"
        "03101,x,100,0.3,0.2,0.1\n",
        encoding="utf-8")
    with pytest.raises(KeyError, match="nonemployed_nowork"):
        load_universe_targets(targets_dir)


def _write_synthetic_universe_targets(tmp_path: Path) -> Path:
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "target2026_work_by_employment_by_kreis.csv").write_text(
        "ars5,source,n_effective,employed_work,employed_nowork,nonemployed_work,nonemployed_nowork\n"
        "03101,x,100,0.3,0.2,0.1,0.4\n"
        "03102,x,100,0.25,0.25,0.25,0.25\n",
        encoding="utf-8")
    (targets_dir / "target2026_education_0_5_by_kreis.csv").write_text(
        "ars5,source,n_effective,edu,noedu\n"
        "03101,x,100,0.6,0.4\n"
        "03102,x,100,0.5,0.5\n",
        encoding="utf-8")
    (targets_dir / "target2026_education_6_17_by_kreis.csv").write_text(
        "ars5,source,n_effective,edu,noedu\n"
        "03101,x,100,0.9,0.1\n"
        "03102,x,100,0.8,0.2\n",
        encoding="utf-8")
    (targets_dir / "target2026_education_18plus_by_kreis.csv").write_text(
        "ars5,source,n_effective,edu,noedu\n"
        "03101,x,100,0.05,0.95\n"
        "03102,x,100,0.1,0.9\n",
        encoding="utf-8")
    return targets_dir


def test_load_universe_targets_synthetic_files(tmp_path):
    targets_dir = _write_synthetic_universe_targets(tmp_path)
    targets = load_universe_targets(targets_dir)

    assert set(targets.columns) == {"ars5", "control", "category", "target_share"}
    assert len(targets) == 4 * 2 + 2 * 2 * 3  # work_by_employment (4 cats) + 3 education controls (2 cats)
    indexed = targets.set_index(["ars5", "control", "category"])
    assert indexed.loc[("03101", "work_by_employment", "employed_work"), "target_share"] == pytest.approx(0.3)
    assert indexed.loc[("03102", "education_18plus", "edu"), "target_share"] == pytest.approx(0.1)


# --- universe_participation_fit ---------------------------------------------

def test_universe_participation_fit_abs_error_on_fixture(tmp_path):
    targets_dir = _write_synthetic_universe_targets(tmp_path)
    result = universe_participation_fit(
        _trips_purpose_schema_universe(), _persons_kreis_universe(), targets_dir)

    assert set(result.columns) == {
        "ars5", "control", "category", "realised_share", "target_share", "abs_error"}
    indexed = result.set_index(["ars5", "control", "category"])
    # 03101 work_by_employment: realised 0.25 each vs synthetic target 0.3/0.2/0.1/0.4.
    assert indexed.loc[("03101", "work_by_employment", "employed_work"), "abs_error"] \
        == pytest.approx(0.05)
    assert indexed.loc[("03101", "work_by_employment", "nonemployed_nowork"), "abs_error"] \
        == pytest.approx(0.15)
    # 03102 education_0_5: realised 0.5/0.5 vs synthetic target 0.5/0.5 -> exact fit.
    assert indexed.loc[("03102", "education_0_5", "edu"), "abs_error"] == pytest.approx(0.0)
    # 03102 education_6_17: realised 0.5/0.5 vs synthetic target 0.8/0.2.
    assert indexed.loc[("03102", "education_6_17", "noedu"), "abs_error"] == pytest.approx(0.3)
    # 03101 education_18plus: realised 0.0/1.0 vs synthetic target 0.05/0.95.
    assert indexed.loc[("03101", "education_18plus", "noedu"), "abs_error"] == pytest.approx(0.05)


def test_universe_participation_fit_drops_realised_cells_without_target(tmp_path, caplog):
    targets_dir = _write_synthetic_universe_targets(tmp_path)
    persons = pd.concat([
        _persons_kreis_universe(),
        pd.DataFrame({
            "person_id": [11], "ars5": ["03999"], "age": [20],
            "employment_status": ["vollzeit"],
        }),
    ], ignore_index=True)
    trips = _trips_purpose_schema_universe()

    with caplog.at_level(logging.WARNING):
        result = universe_participation_fit(trips, persons, targets_dir)

    assert "03999" not in set(result["ars5"])
    assert any("03999" in record.message or "no matching" in record.message
               for record in caplog.records)
