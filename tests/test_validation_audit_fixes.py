"""Regression tests for the 2026-07-12 validation-layer audit fixes (issue #159).

Each test pins one audited defect:
  1. fleet_filter must exclude routing vehicles even when they carry a filler
     household_id (the kreis5 failure: constant 287972.5 defeated notna()).
  2. P36.1 mobility target excludes 'unbekannt' from the denominator.
  3. MiD H7/H12.3 target rows are renormalised to sum 1.
  4. P9 employed share divides by the substantive row total, not literal 100.
  5. T43 education age bands equal the synthesis assignment bands.
  6. quality assessment carries the independence class; fit checks are rolled
     up separately from independent references.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import fleet_filter
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation import quality_assessment as QA
from braunschweig.analysis.population_validation.trip_coherence import _p36_mobile_share


def test_fleet_filter_excludes_routing_with_filler_household_id():
    vehicles = pd.DataFrame({
        "vehicle_id": ["10:car:0", "10:car:1", "5:car", "8:car"],
        "mode": ["car"] * 4,
        # Routing rows carry a CONSTANT filler household_id (kreis5 run) --
        # nullability alone must not be trusted.
        "household_id": [10.0, 10.0, 287972.5, 287972.5],
        "segment": ["kompaktklasse", "suv", np.nan, np.nan],
        "brand": ["VW", "AUDI", np.nan, np.nan],
    })
    fleet = fleet_filter.fleet_vehicles(vehicles, context="test")
    assert list(fleet["vehicle_id"]) == ["10:car:0", "10:car:1"]
    assert fleet["brand"].notna().all()


def test_fleet_filter_vehicle_id_shape_fallback_without_segment():
    vehicles = pd.DataFrame({
        "vehicle_id": ["10:car:0", "5:car"],
        "mode": ["car", "car"],
        "household_id": [10.0, 287972.5],
    })
    fleet = fleet_filter.fleet_vehicles(vehicles, context="test")
    assert list(fleet["vehicle_id"]) == ["10:car:0"]


def test_p36_mobile_share_excludes_unbekannt():
    row = {"mobil": 85.0, "nicht_mobil": 12.0, "unbekannt": 3.0}
    # 85 / (85 + 12), NOT 85 / 100.
    assert _p36_mobile_share(row) == pytest.approx(85.0 / 97.0)


def test_renormalized_by_kreis_sums_to_one():
    by_kreis = {"03101": np.array([0.50, 0.30, 0.19]),   # sums 0.99 (rounded pub.)
                "03103": np.array([0.40, 0.35, 0.26])}   # sums 1.01
    out = C._renormalized_by_kreis(by_kreis, "toy.csv")
    for shares in out.values():
        assert np.isclose(shares.sum(), 1.0)


def test_employment_target_divides_by_substantive_row_total(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    # Row sums to 99 (integer-rounded publication); keine_angabe excluded.
    (mid_dir / "mid2023_P9.csv").write_text(
        "kreis,ars5,n_weighted,n_unweighted,vollzeit,teilzeit,geringfuegig,"
        "sonstiges,erwerbstaetig_unspec,in_ausbildung,nicht_erwerbstaetig,keine_angabe\n"
        "Gesamt,03ZGB,1,1,35,12,3,1,0,2,46,0\n"
        "A,03101,1,1,35,12,3,1,0,2,46,0\n", encoding="utf-8")
    target = C.employment_target(str(tmp_path))
    employed = target[(target["geo_id"] == "03101")
                      & (target["category"] == "employed")]["target_share"].iloc[0]
    # employed = vollzeit+teilzeit+geringfuegig+sonstiges+erwerbstaetig_unspec
    # + in_ausbildung = 35+12+3+1+0+2 = 53, over the substantive total 99 (NOT
    # /100, and in_ausbildung IS on the employed side per issue #169).
    assert employed == pytest.approx(53.0 / 99.0)


def test_employment_target_counts_in_ausbildung_as_employed(tmp_path):
    """issue #169: Azubi (in_ausbildung) is EMPLOYED, matching the MiD erwerb
    definition + the realized `employed` flag; else a ~1-3pp bias."""
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    # 60 vollzeit + 5 in_ausbildung employed, 35 nicht_erwerbstaetig -> 65/100.
    (mid_dir / "mid2023_P9.csv").write_text(
        "kreis,ars5,n_weighted,n_unweighted,vollzeit,teilzeit,geringfuegig,"
        "sonstiges,erwerbstaetig_unspec,in_ausbildung,nicht_erwerbstaetig,keine_angabe\n"
        "A,03101,1,1,60,0,0,0,0,5,35,0\n", encoding="utf-8")
    target = C.employment_target(str(tmp_path))
    employed = target[(target["geo_id"] == "03101")
                      & (target["category"] == "employed")]["target_share"].iloc[0]
    assert employed == pytest.approx(65.0 / 100.0)


def test_education_bands_match_synthesis_bands():
    from braunschweig.analysis.run_mid_validation import _EDU_AGE_LEVELS
    from braunschweig.synthesis.locations.education_gravity import _SCHOOL_BANDS
    synthesis = {name: (lo, hi) for name, lo, hi in _SCHOOL_BANDS}
    validation = {level: (lo, hi) for lo, hi, level in _EDU_AGE_LEVELS}
    # oberstufe (T43 naming) corresponds to the synthesis upper_secondary band.
    assert validation["kindergarten"] == synthesis["kindergarten"]
    assert validation["grundschule"] == synthesis["grundschule"]
    assert validation["sekundar_1"] == synthesis["sekundar_1"]
    assert validation["oberstufe"] == synthesis["upper_secondary"]


def _toy_long(independence):
    return pd.DataFrame({
        "control": ["c1"] * 2, "family": ["f"] * 2,
        "independence": [independence] * 2,
        "geo_id": ["g1", "g1"], "category": ["a", "b"],
        "synthetic_count": [60.0, 40.0], "synthetic_pct": [60.0, 40.0],
        "target_pct": [50.0, 50.0], "target_count": [50.0, 50.0],
        "delta_pp": [10.0, -10.0], "pct_diff": [20.0, -20.0],
    })


def test_assess_carries_independence_and_rollup_separates_classes():
    long = pd.concat([_toy_long("fit_check"),
                      _toy_long("independent").assign(control="c2")],
                     ignore_index=True)
    quality = QA.assess(long)
    assert set(quality["independence"]) == {"fit_check", "independent"}
    rollup = QA.independence_scores(quality)
    assert set(rollup["independence"]) == {"fit_check", "independent"}
    assert (rollup["n_controls"] == 1).all()


def test_pt_ticket_target_excludes_no_answer(tmp_path):
    # issue #329: the synthesis can never produce "no_answer" (MiD code 99 is
    # imputed pool-proportionally), so scoring against it compares a structural
    # zero to survey nonresponse mass. pt_ticket_target must renormalize it out.
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    # PT_RAW_FIXTURE_OK -- this fixture intentionally mirrors the raw committed
    # CSV headers; the German-literal guard test skips files carrying this marker.
    (mid_dir / "mid2023_P24_1.csv").write_text(
        "ars5,n_unweighted,einzelfahrschein,mehrfachkarte,deutschlandticket,"
        "wochen_monat_ohne_abo,monat_abo_jahreskarte,jobticket_semesterticket,"
        "anderes,fahre_nie,keine_angabe\n"
        "03101,100,20,20,10,5,5,5,5,20,10\n"
        "03ZGB,800,20,20,10,5,5,5,5,20,10\n",
        encoding="utf-8",
    )
    target = C.pt_ticket_target(str(tmp_path))
    assert "no_answer" not in set(target["category"]), (
        "no_answer must be renormalized out of the pt_ticket_target frame")
    # The 03101 shares must be the raw shares renormalized over the 8 producible
    # categories: never_pt = 20 / 90 (100 - 10 no_answer).
    never_pt = target[(target["geo_id"] == "03101") & (target["category"] == "never_pt")]
    assert len(never_pt) == 1
    assert never_pt["target_share"].iloc[0] == pytest.approx(20.0 / 90.0)


def test_pt_ticket_target_raises_for_kreis_with_zero_mass_outside_no_answer(tmp_path):
    # issue #329 Item 5: a Kreis row where the ENTIRE MiD P24.1 mass sits in
    # 'keine_angabe' (nonresponse) leaves zero mass to renormalize over the 8
    # producible categories. pt_ticket_target must fail fast with a ValueError
    # naming the offending Kreis, not divide by zero silently.
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    # PT_RAW_FIXTURE_OK -- this fixture intentionally mirrors the raw committed
    # CSV headers; the German-literal guard test skips files carrying this marker.
    (mid_dir / "mid2023_P24_1.csv").write_text(
        "ars5,n_unweighted,einzelfahrschein,mehrfachkarte,deutschlandticket,"
        "wochen_monat_ohne_abo,monat_abo_jahreskarte,jobticket_semesterticket,"
        "anderes,fahre_nie,keine_angabe\n"
        "03101,100,0,0,0,0,0,0,0,0,100\n"
        "03ZGB,800,20,20,10,5,5,5,5,20,10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="03101"):
        C.pt_ticket_target(str(tmp_path))


def test_registry_labels_fit_checks(tmp_path):
    # Static expectation of the audited independence classes.
    expected = {
        "driving_license_type": "fit_check",
        "pt_ticket_type": "partially_independent",
        "cars_per_hh": "partially_independent",
        "bicycles_per_hh": "partially_independent",
        "household_size": "fit_check",
        "age_group": "independent",
        "sex": "independent",
        "employment": "independent",
        "employment_status": "partially_independent",
        "bev_share": "fit_check",
    }
    # build_registry needs the H7/H12.3 CSVs to size the buckets; synthesise them.
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    (mid_dir / "mid2023_H7_cars_by_kreis.csv").write_text(
        "ars5,0,1,2,3\nGesamt,0.2,0.5,0.25,0.05\n03101,0.2,0.5,0.25,0.05\n",
        encoding="utf-8")
    (mid_dir / "mid2023_H12_3_bikes_by_kreis.csv").write_text(
        "ars5,0,1,2,3\nGesamt,0.2,0.5,0.25,0.05\n03101,0.2,0.5,0.25,0.05\n",
        encoding="utf-8")
    registry = C.build_registry(str(tmp_path))
    got = {c.name: c.independence for c in registry}
    assert got == expected
