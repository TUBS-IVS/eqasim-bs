"""Unit tests for the pure analysis functions in scripts/gate_placement_income.py.

These tests verify the placement_income OFF/ON gate's invariant-checking code on small
synthetic frames, independently of any pipeline run or local data. They must pass in CI.

Coverage:
  - household_level: household collapse, missing-column guard, within-household
    source_household_id consistency guard
  - signature_group_counts: per-(cell, signature) / per-(Kreis, signature) counting,
    fail-fast on unmapped surrogate or unmapped signature
  - clone_profile: per-donor clone counting, fail-fast on unmapped surrogate
  - compare_counts: aligned-reindex diff arithmetic (equal keys, value mismatch,
    key-only-on-one-side, both-empty)
  - decide_gate: PASS/FAIL verdict + reasons for each of the five hard invariants
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
signature_group_counts = _gate_mod.signature_group_counts
clone_profile = _gate_mod.clone_profile
compare_counts = _gate_mod.compare_counts
decide_gate = _gate_mod.decide_gate


# ---------------------------------------------------------------------------
# household_level
# ---------------------------------------------------------------------------

class TestHouseholdLevel:
    def test_basic_collapse_takes_first_per_household(self) -> None:
        """One row per household_id; household-level columns carried through unchanged."""
        persons = pd.DataFrame({
            "household_id": [1, 1, 2, 2],
            "source_household_id": [10, 10, 20, 20],
            "ZENSUS100m": ["c1", "c1", "c2", "c2"],
            "departement_id": ["03101", "03101", "03102", "03102"],
            "household_income_eur": [2000.0, 2000.0, 3500.0, 3500.0],
            "economic_status": ["low", "low", "high", "high"],
            "number_of_cars": [1, 1, 2, 2],
        })
        hh = household_level(persons)
        assert len(hh) == 2, f"expected 1 row per household_id, got {len(hh)}"
        assert set(hh["household_id"]) == {1, 2}
        row1 = hh.loc[hh["household_id"] == 1].iloc[0]
        assert row1["source_household_id"] == 10
        assert row1["ZENSUS100m"] == "c1"
        assert row1["departement_id"] == "03101"
        assert row1["household_income_eur"] == pytest.approx(2000.0)
        assert row1["economic_status"] == "low"
        assert row1["number_of_cars"] == 1
        row2 = hh.loc[hh["household_id"] == 2].iloc[0]
        assert row2["household_income_eur"] == pytest.approx(3500.0)

    def test_missing_required_column_raises(self) -> None:
        """A frame missing a required household-level column must fail fast."""
        persons = pd.DataFrame({"household_id": [1], "source_household_id": [10]})
        with pytest.raises(ValueError, match="requires columns"):
            household_level(persons)

    def test_inconsistent_source_household_id_raises(self) -> None:
        """Members of the SAME synthetic household disagreeing on source_household_id
        indicates a corrupted expansion (every person in a household must share one
        donor household) -- this must fail fast, never silently pick a value."""
        persons = pd.DataFrame({
            "household_id": [1, 1],
            "source_household_id": [10, 11],  # inconsistent within household_id == 1
            "ZENSUS100m": ["c1", "c1"],
            "departement_id": ["03101", "03101"],
            "household_income_eur": [2000.0, 2000.0],
            "economic_status": ["low", "low"],
            "number_of_cars": [1, 1],
        })
        with pytest.raises(ValueError, match="source_household_id"):
            household_level(persons)


# ---------------------------------------------------------------------------
# signature_group_counts
# ---------------------------------------------------------------------------

class TestSignatureGroupCounts:
    def test_basic_counts(self) -> None:
        """Households are grouped correctly per (cell, signature) and (Kreis, signature)."""
        hh = pd.DataFrame({
            "household_id": [1, 2, 3, 4],
            "source_household_id": [10, 20, 10, 30],
            "ZENSUS100m": ["c1", "c1", "c2", "c2"],
            "departement_id": ["03101", "03101", "03102", "03102"],
        })
        raw_id_by_surrogate = {10: "H10", 20: "H20", 30: "H30"}
        signature_by_raw_id = {"H10": (1, 0), "H20": (1, 0), "H30": (0, 1)}
        cell_counts, kreis_counts = signature_group_counts(
            hh, raw_id_by_surrogate, signature_by_raw_id
        )
        # .to_dict() on a MultiIndex Series yields {(level0, level1): value, ...} --
        # avoids any ambiguity of bracket-indexing a MultiIndex whose second level is
        # itself tuple-valued (the control signature).
        cell_dict = cell_counts.to_dict()
        kreis_dict = kreis_counts.to_dict()
        assert cell_dict[("c1", (1, 0))] == 2
        assert cell_dict[("c2", (1, 0))] == 1
        assert cell_dict[("c2", (0, 1))] == 1
        assert kreis_dict[("03101", (1, 0))] == 2
        assert kreis_dict[("03102", (1, 0))] == 1
        assert kreis_dict[("03102", (0, 1))] == 1

    def test_missing_surrogate_mapping_raises(self) -> None:
        """A household surrogate absent from the pseudonym map must fail fast (a
        silently-dropped household would understate its control contribution)."""
        hh = pd.DataFrame({
            "household_id": [1],
            "source_household_id": [999],
            "ZENSUS100m": ["c1"],
            "departement_id": ["03101"],
        })
        with pytest.raises(ValueError, match="raw-id mapping"):
            signature_group_counts(hh, {}, {})

    def test_missing_signature_raises(self) -> None:
        """A raw donor id absent from the signature map must fail fast."""
        hh = pd.DataFrame({
            "household_id": [1],
            "source_household_id": [10],
            "ZENSUS100m": ["c1"],
            "departement_id": ["03101"],
        })
        raw_id_by_surrogate = {10: "H10"}
        with pytest.raises(ValueError, match="control signature"):
            signature_group_counts(hh, raw_id_by_surrogate, {})


# ---------------------------------------------------------------------------
# clone_profile
# ---------------------------------------------------------------------------

class TestCloneProfile:
    def test_basic_clone_counts(self) -> None:
        """Per raw donor id, the number of synthetic households cloned from it."""
        hh = pd.DataFrame({
            "household_id": [1, 2, 3, 4],
            "source_household_id": [10, 10, 20, 30],
        })
        mapping = {10: "H10", 20: "H20", 30: "H30"}
        result = clone_profile(hh, mapping)
        assert result["H10"] == 2
        assert result["H20"] == 1
        assert result["H30"] == 1

    def test_missing_surrogate_raises(self) -> None:
        """A household surrogate absent from the pseudonym map must fail fast."""
        hh = pd.DataFrame({"household_id": [1, 2], "source_household_id": [10, 99]})
        mapping = {10: "H10"}
        with pytest.raises(ValueError, match="raw-id mapping"):
            clone_profile(hh, mapping)


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

    def test_all_pass_returns_pass_empty_reasons(self) -> None:
        cell_cmp, kreis_cmp, clone_cmp = self._equal_cmp(), self._equal_cmp(), self._equal_cmp()
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, True, True)
        assert verdict == "PASS"
        assert reasons == []

    def test_cell_mismatch_fails_with_reason(self) -> None:
        cell_cmp = {**self._equal_cmp(), "equal": False, "n_diff_keys": 3, "max_abs_diff": 4}
        kreis_cmp, clone_cmp = self._equal_cmp(), self._equal_cmp()
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, True, True)
        assert verdict == "FAIL"
        assert any("cell" in r for r in reasons), reasons

    def test_kreis_mismatch_fails_with_reason(self) -> None:
        cell_cmp, clone_cmp = self._equal_cmp(), self._equal_cmp()
        kreis_cmp = {**self._equal_cmp(), "equal": False, "n_diff_keys": 2}
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, True, True)
        assert verdict == "FAIL"
        assert any("Kreis" in r for r in reasons), reasons

    def test_clone_mismatch_fails_with_reason(self) -> None:
        cell_cmp, kreis_cmp = self._equal_cmp(), self._equal_cmp()
        clone_cmp = {**self._equal_cmp(), "equal": False, "n_diff_keys": 1}
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, True, True)
        assert verdict == "FAIL"
        assert any(("clone" in r or "donor" in r) for r in reasons), reasons

    def test_on_diag_missing_fails_with_reason(self) -> None:
        cell_cmp, kreis_cmp, clone_cmp = self._equal_cmp(), self._equal_cmp(), self._equal_cmp()
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, False, True)
        assert verdict == "FAIL"
        assert any("MISSING" in r for r in reasons), reasons

    def test_off_diag_present_fails_with_reason(self) -> None:
        cell_cmp, kreis_cmp, clone_cmp = self._equal_cmp(), self._equal_cmp(), self._equal_cmp()
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, True, False)
        assert verdict == "FAIL"
        assert any("PRESENT" in r for r in reasons), reasons

    def test_multiple_failures_all_reported(self) -> None:
        """Every violated condition contributes its own reason -- none are hidden by an
        early return once any single condition fails."""
        cell_cmp = {**self._equal_cmp(), "equal": False}
        kreis_cmp = self._equal_cmp()
        clone_cmp = {**self._equal_cmp(), "equal": False}
        verdict, reasons = decide_gate(cell_cmp, kreis_cmp, clone_cmp, False, False)
        assert verdict == "FAIL"
        # cell + clone + ON-missing + OFF-present = 4 independent reasons.
        assert len(reasons) == 4, reasons
