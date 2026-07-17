"""Unit tests for the pure analysis functions in scripts/gate_placement_income.py.

These tests verify the placement_income OFF/ON gate's invariant-checking code on small
synthetic frames, independently of any pipeline run or local data. They must pass in CI.

Coverage:
  - household_level: household collapse (household_id, H_ID, ZENSUS100m, departement_id,
    economic_status, number_of_cars, household_income_eur), missing-column guard,
    within-household primary-donor H_ID consistency guard, and the regression case that
    a varying PER-PERSON source_household_id within one household_id (member completion
    borrowing from another donor) is FINE and never checked here.
  - compare_counts: aligned-reindex diff arithmetic (equal keys, value mismatch,
    key-only-on-one-side, both-empty)
  - realized_invariants: household- and person-level realized-aggregate comparison
    (identical frames -> all equal; a perturbed attribute -> only the affected
    invariants flip; member-completion-style source_household_id variation and the
    absence of the imputed binary sex column do not affect the result)
  - decide_gate: PASS/FAIL verdict + reasons from the realized invariants plus the
    ON-present / OFF-absent diag-CSV checks
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

# Make the repo root importable so the scripts/ module can be found.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import the pure analysis functions from scripts/gate_placement_income.py.
# We do a direct module import (not as a package) because scripts/ is not a Python
# package (no __init__.py). This mirrors tests/test_gate_income_tilt.py exactly.
import importlib.util as _ilu

_gate_spec = _ilu.spec_from_file_location(
    "gate_placement_income",
    str(_REPO_ROOT / "scripts" / "gate_placement_income.py"),
)
_gate_mod = _ilu.module_from_spec(_gate_spec)  # type: ignore[arg-type]
_gate_spec.loader.exec_module(_gate_mod)  # type: ignore[union-attr]

household_level = _gate_mod.household_level
compare_counts = _gate_mod.compare_counts
realized_invariants = _gate_mod.realized_invariants
decide_gate = _gate_mod.decide_gate


# ---------------------------------------------------------------------------
# household_level
# ---------------------------------------------------------------------------

class TestHouseholdLevel:
    def test_basic_collapse_takes_first_per_household(self) -> None:
        """One row per household_id; household-level columns carried through unchanged;
        source_household_id (a per-person attribute) is not part of the output."""
        persons = pd.DataFrame({
            "household_id": [1, 1, 2, 2],
            "H_ID": [10, 10, 20, 20],
            "source_household_id": [10, 10, 20, 20],
            "ZENSUS100m": ["c1", "c1", "c2", "c2"],
            "departement_id": ["03101", "03101", "03102", "03102"],
            "economic_status": ["low", "low", "high", "high"],
            "number_of_cars": [1, 1, 2, 2],
            "household_income_eur": [2000.0, 2000.0, 3500.0, 3500.0],
        })
        hh = household_level(persons)
        assert len(hh) == 2, f"expected 1 row per household_id, got {len(hh)}"
        assert set(hh["household_id"]) == {1, 2}
        assert "source_household_id" not in hh.columns
        row1 = hh.loc[hh["household_id"] == 1].iloc[0]
        assert row1["H_ID"] == 10
        assert row1["ZENSUS100m"] == "c1"
        assert row1["departement_id"] == "03101"
        assert row1["economic_status"] == "low"
        assert row1["number_of_cars"] == 1
        assert row1["household_income_eur"] == pytest.approx(2000.0)
        row2 = hh.loc[hh["household_id"] == 2].iloc[0]
        assert row2["household_income_eur"] == pytest.approx(3500.0)

    def test_missing_required_column_raises(self) -> None:
        """A frame missing a required household-level column must fail fast."""
        persons = pd.DataFrame({"household_id": [1], "H_ID": [10]})
        with pytest.raises(ValueError, match="requires columns"):
            household_level(persons)

    def test_inconsistent_h_id_raises(self) -> None:
        """Members of the SAME synthetic household disagreeing on the PRIMARY donor
        H_ID indicates a corrupted expansion (household_id is built as
        '<cell>_<H_ID>_<occurrence>', so every person in it must share one H_ID) --
        this must fail fast, never silently pick a value."""
        persons = pd.DataFrame({
            "household_id": [1, 1],
            "H_ID": [10, 11],  # inconsistent within household_id == 1
            "ZENSUS100m": ["c1", "c1"],
            "departement_id": ["03101", "03101"],
            "economic_status": ["low", "low"],
            "number_of_cars": [1, 1],
            "household_income_eur": [2000.0, 2000.0],
        })
        with pytest.raises(ValueError, match="H_ID"):
            household_level(persons)

    def test_varying_source_household_id_within_household_is_fine(self) -> None:
        """Regression test for the L2 harness bug: member completion (D3) fills a
        synthetic household with members borrowed from OTHER donor households, so
        source_household_id (a PER-PERSON surrogate) legitimately varies within one
        household_id even though the PRIMARY donor H_ID (which household_id is built
        from) stays constant. household_level must NOT raise on this, and must not
        require or carry source_household_id at all."""
        persons = pd.DataFrame({
            "household_id": [1, 1, 2],
            "H_ID": [10, 10, 20],  # constant per household_id
            "source_household_id": [10, 77, 20],  # varies within household_id == 1
            "ZENSUS100m": ["c1", "c1", "c2"],
            "departement_id": ["03101", "03101", "03102"],
            "economic_status": ["low", "low", "high"],
            "number_of_cars": [1, 1, 2],
            "household_income_eur": [2000.0, 2000.0, 3500.0],
        })
        hh = household_level(persons)  # must not raise
        assert len(hh) == 2
        assert "source_household_id" not in hh.columns


# ---------------------------------------------------------------------------
# compare_counts
# ---------------------------------------------------------------------------

class TestCompareCounts:
    def test_identical_series_equal(self) -> None:
        off = pd.Series([2, 3], index=["a", "b"])
        on = pd.Series([2, 3], index=["a", "b"])
        result = compare_counts(off, on)
        assert result["equal"] is True
        assert result["n_diff_keys"] == 0
        assert result["max_abs_diff"] == 0
        assert result["n_keys_off"] == 2
        assert result["n_keys_on"] == 2

    def test_value_mismatch_at_shared_key(self) -> None:
        off = pd.Series([2, 3], index=["a", "b"])
        on = pd.Series([2, 5], index=["a", "b"])
        result = compare_counts(off, on)
        assert result["equal"] is False
        assert result["n_diff_keys"] == 1
        assert result["max_abs_diff"] == 2

    def test_key_only_in_off_counts_as_diff(self) -> None:
        """A key present only in OFF is treated as (value, 0) after reindex, not ignored."""
        off = pd.Series([2, 3], index=["a", "b"])
        on = pd.Series([2], index=["a"])
        result = compare_counts(off, on)
        assert result["equal"] is False
        assert result["n_diff_keys"] == 1
        assert result["max_abs_diff"] == 3
        assert result["n_keys_off"] == 2
        assert result["n_keys_on"] == 1

    def test_key_only_in_on_counts_as_diff(self) -> None:
        off = pd.Series([2], index=["a"])
        on = pd.Series([2, 7], index=["a", "b"])
        result = compare_counts(off, on)
        assert result["equal"] is False
        assert result["n_diff_keys"] == 1
        assert result["max_abs_diff"] == 7

    def test_both_empty_equal(self) -> None:
        off = pd.Series([], dtype=np.int64)
        on = pd.Series([], dtype=np.int64)
        result = compare_counts(off, on)
        assert result["equal"] is True
        assert result["n_keys_off"] == 0
        assert result["n_keys_on"] == 0
        assert result["n_diff_keys"] == 0
        assert result["max_abs_diff"] == 0


# ---------------------------------------------------------------------------
# realized_invariants
# ---------------------------------------------------------------------------

_REALIZED_INVARIANT_NAMES = {
    "economic_status_x_departement_id",
    "number_of_cars_x_departement_id",
    "economic_status_x_ZENSUS100m",
    "ZENSUS100m_household_count",
    "age_x_sex_raw_x_ZENSUS100m",
    "age_x_ZENSUS100m",
    "clone_counts_by_H_ID",
}


def _sample_persons() -> pd.DataFrame:
    """Four persons across three synthetic households (two donors, one cloned once),
    two 100 m cells, two Kreise -- enough structure to exercise every realized
    invariant without any real pipeline data."""
    return pd.DataFrame({
        "household_id": ["c1_10_0", "c1_10_0", "c2_20_0", "c2_30_0"],
        "H_ID": [10, 10, 20, 30],
        "ZENSUS100m": ["c1", "c1", "c2", "c2"],
        "departement_id": ["03101", "03101", "03102", "03102"],
        "economic_status": ["low", "low", "high", "medium"],
        "number_of_cars": [1, 1, 2, 0],
        "household_income_eur": [2000.0, 2000.0, 3500.0, 1200.0],
        "age": [40, 12, 55, 30],
        "sex_raw": ["male", "female", "female", "male"],
    })


class TestRealizedInvariants:
    def test_identical_frames_all_equal(self) -> None:
        off = _sample_persons()
        on = off.copy()
        result = realized_invariants(off, on)
        assert set(result) == _REALIZED_INVARIANT_NAMES
        assert all(cmp_["equal"] for cmp_ in result.values()), result

    def test_economic_status_perturbation_flags_only_the_affected_invariants(self) -> None:
        """Changing one household's economic_status must flip exactly the invariants
        that cross economic_status with a geography, and leave every other realized
        invariant (cars, cell counts, clone counts, age/sex) equal."""
        off = _sample_persons()
        on = off.copy()
        on.loc[on["household_id"] == "c2_20_0", "economic_status"] = "medium"
        result = realized_invariants(off, on)
        assert result["economic_status_x_departement_id"]["equal"] is False
        assert result["economic_status_x_ZENSUS100m"]["equal"] is False
        assert result["number_of_cars_x_departement_id"]["equal"] is True
        assert result["ZENSUS100m_household_count"]["equal"] is True
        assert result["age_x_sex_raw_x_ZENSUS100m"]["equal"] is True
        assert result["age_x_ZENSUS100m"]["equal"] is True
        assert result["clone_counts_by_H_ID"]["equal"] is True

    def test_donor_relocation_flags_clone_and_cell_invariants(self) -> None:
        """Moving a donor H_ID's household to a different cell/Kreis (simulating a
        BROKEN reallocation that changes WHERE a donor is used, not just which
        equal-signature donor fills a slot) must be caught by the cell/Kreis and
        clone-count invariants."""
        off = _sample_persons()
        on = off.copy()
        on.loc[on["household_id"] == "c2_30_0", "ZENSUS100m"] = "c1"
        result = realized_invariants(off, on)
        assert result["ZENSUS100m_household_count"]["equal"] is False
        assert result["economic_status_x_ZENSUS100m"]["equal"] is False
        # H_ID 30 still exists exactly once overall -- only its CELL moved -- so the
        # region-wide clone profile (households per H_ID, no geography) is unaffected.
        assert result["clone_counts_by_H_ID"]["equal"] is True

    def test_member_completion_source_household_id_variation_is_transparent(self) -> None:
        """The bug this rework fixes: a per-person source_household_id that varies
        within one household_id (member completion borrowing from another donor) must
        not raise and must not affect any realized invariant, since H_ID (what the
        invariants and household_level key on) stays constant per household_id."""
        off = _sample_persons()
        off["source_household_id"] = [10, 999, 20, 30]  # varies within c1_10_0
        on = off.copy()
        result = realized_invariants(off, on)  # must not raise
        assert all(cmp_["equal"] for cmp_ in result.values()), result

    def test_realized_invariants_does_not_require_the_imputed_binary_sex_column(self) -> None:
        """sex_raw (not the imputed binary sex) drives the person-level checks: a frame
        without a 'sex' column at all must still work, matching the documented reason
        the harness does not use it (HP_SEX 3/9 get a seeded order-dependent binary-sex
        imputation that reallocation reordering can perturb; sex_raw cannot)."""
        off = _sample_persons()
        assert "sex" not in off.columns
        on = off.copy()
        result = realized_invariants(off, on)  # must not raise despite no 'sex' column
        assert result["age_x_sex_raw_x_ZENSUS100m"]["equal"] is True

    def test_missing_person_level_column_raises(self) -> None:
        """A frame missing a required person-level column (sex_raw/age/ZENSUS100m)
        must fail fast, naming which of the two frames is at fault."""
        off = _sample_persons().drop(columns=["sex_raw"])
        on = _sample_persons()
        with pytest.raises(ValueError, match="off_persons"):
            realized_invariants(off, on)


# ---------------------------------------------------------------------------
# decide_gate
# ---------------------------------------------------------------------------

class TestDecideGate:
    """Tests for the decide_gate() pure function in isolation from any pipeline run."""

    @staticmethod
    def _equal_cmp(n_keys: int = 5) -> dict:
        return {
            "equal": True, "n_keys_off": n_keys, "n_keys_on": n_keys,
            "n_diff_keys": 0, "max_abs_diff": 0,
        }

    @classmethod
    def _all_equal_invariants(cls) -> dict:
        return {
            "economic_status_x_departement_id": cls._equal_cmp(),
            "number_of_cars_x_departement_id": cls._equal_cmp(),
            "clone_counts_by_H_ID": cls._equal_cmp(),
        }

    def test_all_pass_returns_pass_empty_reasons(self) -> None:
        verdict, reasons = decide_gate(self._all_equal_invariants(), True, True)
        assert verdict == "PASS"
        assert reasons == []

    def test_invariant_mismatch_fails_with_reason(self) -> None:
        invariants = self._all_equal_invariants()
        invariants["economic_status_x_departement_id"] = {
            **self._equal_cmp(), "equal": False, "n_diff_keys": 3, "max_abs_diff": 4,
        }
        verdict, reasons = decide_gate(invariants, True, True)
        assert verdict == "FAIL"
        assert any("economic_status_x_departement_id" in r for r in reasons), reasons

    def test_on_diag_missing_fails_with_reason(self) -> None:
        verdict, reasons = decide_gate(self._all_equal_invariants(), False, True)
        assert verdict == "FAIL"
        assert any("MISSING" in r for r in reasons), reasons

    def test_off_diag_present_fails_with_reason(self) -> None:
        verdict, reasons = decide_gate(self._all_equal_invariants(), True, False)
        assert verdict == "FAIL"
        assert any("PRESENT" in r for r in reasons), reasons

    def test_multiple_failures_all_reported(self) -> None:
        """Every violated condition contributes its own reason -- none are hidden by an
        early return once any single condition fails."""
        invariants = self._all_equal_invariants()
        invariants["clone_counts_by_H_ID"] = {**self._equal_cmp(), "equal": False}
        verdict, reasons = decide_gate(invariants, False, False)
        assert verdict == "FAIL"
        # 1 invariant mismatch + ON-missing + OFF-present = 3 independent reasons.
        assert len(reasons) == 3, reasons
