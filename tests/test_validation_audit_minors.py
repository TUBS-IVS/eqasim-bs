"""Regression tests for the 2026-07-12 validation-audit MINOR fixes (issue #159).

Second wave (the majors landed in PR #164). Each test pins one remaining
audited defect that PR #165 did not already cover:

  1. age-based control fails fast when the 'age' column is absent (no silent
     full-population fallback -- #97-class universe guard).
  2. _bev_not_bev maps NaN technology to not_bev (and the caller logs it).
  3. trip_coherence_by_kreis excludes persons without a Kreis (no NaN 9th row).
  4. realised_counts returns resolved control fields; a no-expression control
     is absent from them, so cell_error_table can exclude it instead of
     scoring a fabricated 100% error.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.integerizer_quality import cell_error as ce
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation import trip_coherence as TC

# Committed MiD reference tables (the trip-coherence targets read from here).
_DATA_PATH = Path(__file__).resolve().parents[1] / "eqasim-data" / "data"
_HAS_MID = (_DATA_PATH / "braunschweig" / "mid" / "mid2023_P36_1.csv").is_file()


def test_age_control_raises_when_age_column_absent():
    ctrl = C.categorical_person_control(
        "empl", "mid_person", "kreis", "employed", ("employed", "not_employed"),
        target=lambda dp: pd.DataFrame(), age_min=14)
    persons = pd.DataFrame({"household_id": [1, 2], "employed": [True, False]})  # no 'age'
    geo = pd.DataFrame({"household_id": [1, 2], "ars5": ["03101", "03101"]})

    class F:  # minimal frames stand-in
        pass
    frames = F(); frames.persons = persons
    with pytest.raises(KeyError, match="no 'age' column"):
        ctrl.realized(frames, geo)


def test_bev_not_bev_maps_nan_to_not_bev():
    out = C._bev_not_bev(pd.Series(["bev", "diesel", np.nan]))
    assert list(out) == ["bev", "not_bev", "not_bev"]


@pytest.mark.skipif(not _HAS_MID, reason="committed MiD reference tables not present")
def test_trip_coherence_excludes_persons_without_kreis():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "ars5": ["03101", "03101", np.nan],  # person 3 has no Kreis
    })
    trips = pd.DataFrame({
        "person_id": [1, 2, 3],
        "following_purpose": ["work", "shop", "work"],
    })
    out = TC.trip_coherence_by_kreis(persons, trips, data_path=str(_DATA_PATH), geo_col="ars5")
    # No NaN-keyed row (the pre-fix dropna=False emitted a 9th ars5=NaN row);
    # only the real Kreis remains.
    assert out["ars5"].isna().sum() == 0
    assert set(out["ars5"]) == {"03101"}


def _no_expr_control():
    class _C:
        name = "dummy"
        geography = ce._CELL
        seed_table = "households"
        def expression_for(self, _lang):
            return None
    return _C()


def test_realised_counts_reports_resolved_fields_excluding_skipped():
    syn_hh = pd.DataFrame({ce._CELL: ["cellA"], "H_ID": [10]})
    donor_hh = pd.DataFrame({"H_ID": [10], "H_ANZAUTO": [1]})
    donor_p = pd.DataFrame({"H_ID": [10], "HP_ALTER": [40]})
    frame, n_resolved, n_skipped, resolved = ce.realised_counts(
        syn_hh, donor_hh, donor_p, [_no_expr_control()])
    assert n_resolved == 0
    assert n_skipped == 1
    # The skipped control must NOT appear as a resolved field, so the caller
    # excludes its target instead of scoring realised=0 (fabricated 100%).
    assert resolved == set()
