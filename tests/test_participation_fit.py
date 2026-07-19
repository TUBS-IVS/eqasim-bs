"""Tests for braunschweig/analysis/population_validation/participation_fit.py
(Task 7 of feature #224).

Covers the four public interfaces:
- ``realised_participation`` on both the eqasim purpose-string trip schema and
  the raw MiD ``W_ZWECK`` schema, on tiny synthetic fixtures with known
  per-Kreis participation/mobility rates;
- ``load_participation_targets`` against the real committed SrV target CSVs;
- ``participation_fit`` joining a realised fixture to a synthetic target,
  including the no-silent-fallback drop of realised cells without a target;
- ``donor_neff`` on a fixture with known donor duplication, and its
  no-silent-fallback raise when the donor id column is absent.
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
    participation_fit,
    realised_participation,
)

TARGETS_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "targets"

# --- Shared fixture: 4 persons in 2 Kreise, purpose-string trip schema. ---
#
# Kreis 03101: person 1 (one work round-trip), person 2 (fully immobile).
#   -> work 1/2=0.5, leisure 0/2=0.0, education 0/2=0.0, mobility 1/2=0.5
# Kreis 03102: person 3 (one leisure round-trip), person 4 (one education
# round-trip).
#   -> work 0/2=0.0, leisure 1/2=0.5, education 1/2=0.5, mobility 2/2=1.0


def _persons_kreis():
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "ars5": ["03101", "03101", "03102", "03102"],
    })


def _trips_purpose_schema():
    return pd.DataFrame({
        "person_id": [1, 1, 3, 3, 4, 4],
        "preceding_purpose": ["home", "work", "home", "leisure", "home", "education"],
        "following_purpose": ["work", "home", "leisure", "home", "education", "home"],
    })


def _trips_wzweck_schema():
    # One trip per mobile person is enough under the W_ZWECK schema: a person
    # participates in purpose P as soon as one trip carries a W_ZWECK code
    # from mid.PARTICIPATION_W_ZWECK[P]. 1 = Arbeit (work), 7 = Freizeit
    # (leisure), 3 = Ausbildung/Schule (education).
    return pd.DataFrame({
        "person_id": [1, 3, 4],
        "W_ZWECK": [1, 7, 3],
    })


_EXPECTED = {
    ("03101", "work"): 0.5,
    ("03101", "leisure"): 0.0,
    ("03101", "education"): 0.0,
    ("03101", "mobility"): 0.5,
    ("03102", "work"): 0.0,
    ("03102", "leisure"): 0.5,
    ("03102", "education"): 0.5,
    ("03102", "mobility"): 1.0,
}


def _assert_matches_expected(result: pd.DataFrame):
    assert set(result.columns) == {"ars5", "purpose", "realised_rate", "n_persons"}
    assert len(result) == 8
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
    assert set(targets["purpose"]) == {"work", "leisure", "education", "mobility"}
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
    assert len(result) == 8

    indexed = result.set_index(["ars5", "purpose"])
    expected_abs_error = {
        ("03101", "work"): 0.1,
        ("03101", "leisure"): 0.1,
        ("03101", "education"): 0.05,
        ("03101", "mobility"): 0.1,
        ("03102", "work"): 0.1,
        ("03102", "leisure"): 0.1,
        ("03102", "education"): 0.05,
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
