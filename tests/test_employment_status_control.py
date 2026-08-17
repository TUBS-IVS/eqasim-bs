"""Tests for the Task-3 per-Kreis employment_status validation control.

Phase 0 adds a genuine per-Kreis reference (MiD 2023 P9, seven employment-extent
classes) plus a registered validation control that compares it against the
synthetic ``employment_status`` person attribute (Task 1/2). The control is
registered ``independence="partially_independent"``: popsim now steers the
synthetic ``employment_status`` attribute per Kreis (feature #172, tasks 3/4)
via the ``target2026_employment_status_by_kreis.csv`` blend, of which this pure
MiD P9 table is only ONE input -- so a deviation here mostly measures the
blend/shrinkage distance, not fully independent agreement with reality (same
framing as the ``cars_per_hh`` / ``bicycles_per_hh`` controls; CLAUDE.md:
convergence is not validation).
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation.population_source import PopulationFrames
from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES

DATA = "eqasim-data/data"

_P9_HEADER = (
    "kreis,ars5,n_weighted,n_unweighted,vollzeit,teilzeit,geringfuegig,"
    "sonstiges,erwerbstaetig_unspec,in_ausbildung,nicht_erwerbstaetig,keine_angabe\n"
)


def _write_p9(tmp_path, body: str) -> str:
    mid = tmp_path / "braunschweig" / "mid"
    mid.mkdir(parents=True)
    (mid / "mid2023_P9.csv").write_text(_P9_HEADER + body, encoding="utf-8")
    return str(tmp_path)


def test_employment_status_target_shares_sum_to_one(tmp_path):
    data_path = _write_p9(tmp_path, "A,03101,1,1,35,12,3,1,0,2,46,0\n")
    tgt = C.employment_status_target(data_path)
    s = tgt[tgt["geo_id"] == "03101"]["target_share"].sum()
    assert abs(s - 1.0) < 1e-9
    assert set(tgt["category"]) == set(C._EMPLOYMENT_STATUS_CATEGORIES)


def test_employment_status_target_excludes_zgb_aggregate_row(tmp_path):
    data_path = _write_p9(
        tmp_path,
        "Gesamt,03ZGB,1,1,35,12,3,1,0,2,46,0\n"
        "A,03101,1,1,35,12,3,1,0,2,46,0\n",
    )
    tgt = C.employment_status_target(data_path)
    assert "03ZGB" not in set(tgt["geo_id"])


def test_employment_status_target_class_share_excludes_keine_angabe_denominator(tmp_path):
    """Target share per class = class / substantive-row-total (the sum of the
    seven class columns), EXCLUDING keine_angabe -- same denominator convention
    as the existing employment_target."""
    # denom = 35+12+3+1+0+2+46 = 99 (keine_angabe=5 excluded from the denominator).
    data_path = _write_p9(tmp_path, "A,03101,1,1,35,12,3,1,0,2,46,5\n")
    tgt = C.employment_status_target(data_path)
    row = tgt[(tgt["geo_id"] == "03101") & (tgt["category"] == "vollzeit")]
    assert row["target_share"].iloc[0] == pytest.approx(35.0 / 99.0)


def test_employment_status_target_raises_on_non_positive_class_total(tmp_path):
    """No-silent-fallback (CLAUDE.md): a Kreis row whose seven class columns sum
    to <= 0 raises instead of silently producing a NaN/inf share."""
    data_path = _write_p9(tmp_path, "A,03101,1,1,0,0,0,0,0,0,0,0\n")
    with pytest.raises(ValueError, match="03101"):
        C.employment_status_target(data_path)


def test_employment_status_categories_match_attributes_module_order():
    """controls._EMPLOYMENT_STATUS_CATEGORIES must mirror
    braunschweig.popsim.attributes.EMPLOYMENT_STATUS_CATEGORIES (Task 1) in the
    SAME order -- both are keyed to the MiD P_BKAT codebook order 1..7, and a
    silent divergence would misalign realized vs. target categories."""
    assert C._EMPLOYMENT_STATUS_CATEGORIES == EMPLOYMENT_STATUS_CATEGORIES


def test_employment_status_target_on_real_p9_data_sums_to_one_per_kreis():
    """End-to-end against the real committed MiD 2023 P9 table (not a synthetic
    fixture): every Kreis row's seven class shares sum to 1, proving the PRIMARY
    loader path actually works on representative input, not just a toy fixture."""
    tgt = C.employment_status_target(DATA)
    assert not tgt.empty
    sums = tgt.groupby("geo_id")["target_share"].sum()
    assert (abs(sums - 1.0) < 1e-9).all()
    assert set(tgt["category"]) == set(C._EMPLOYMENT_STATUS_CATEGORIES)


def test_registry_registers_employment_status_control_as_partially_independent():
    """The employment_status control must be registered as family=mid_person,
    geography=kreis, independence='partially_independent': popsim steers
    employment_status per Kreis via the target2026 blend, of which this pure
    MiD P9 table is only one input (same framing as cars_per_hh/bicycles_per_hh)."""
    reg = {c.name: c for c in C.build_registry(DATA)}
    assert "employment_status" in reg
    ctrl = reg["employment_status"]
    assert ctrl.family == "mid_person"
    assert ctrl.geography == "kreis"
    assert ctrl.independence == "partially_independent"
    assert ctrl.categories == C._EMPLOYMENT_STATUS_CATEGORIES


def test_registry_employment_status_realized_extractor_respects_age_and_column():
    """The registered control's realized extractor keys on
    frames.persons['employment_status'], restricted to age >= 14 (the P9 person
    base), consistent with the existing 'employment' control's age_min=14 filter."""
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "household_id": [10, 10, 20],
        "age": [40, 10, 30],  # person 2 is below the age_min=14 P9 base
        "employment_status": ["vollzeit", "nicht_erwerbstaetig", "in_ausbildung"],
    })
    frames = PopulationFrames(persons, pd.DataFrame(), None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                        "commune_id": ["03101000", "03101000"]})
    reg = {c.name: c for c in C.build_registry(DATA)}
    long = reg["employment_status"].realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # Person 2 (age 10) is excluded by age_min=14; persons 1 and 3 remain.
    assert got == {"vollzeit": 1, "in_ausbildung": 1}
