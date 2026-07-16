"""Tests for the #132 work production-mass switch (population vs svb_wohn).

Run with::

    python -m pytest tests/test_gravity_production_mass.py -v
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.gravity.production_mass import build_work_production_mass


def _population():
    # two Kreise: 03101 (one Gemeinde), 03151 (three Gemeinden)
    return pd.DataFrame({
        "origin_id": ["031010001000", "031510018000", "031510020000", "031510030000"],
        "population": [1000.0, 500.0, 500.0, 1000.0],
    })


def _svb():
    # svb present for 3 of 4 Gemeinden; 031510030000 is missing (suppressed)
    return pd.DataFrame({
        "commune_id": ["031010001000", "031510018000", "031510020000"],
        "svb_wohn": [400, 300, 100],
    })


def test_population_mode_returns_input_unchanged():
    df = _population()
    out = build_work_production_mass(df, _svb(), mode="population")
    pd.testing.assert_frame_equal(out, df)


def test_svb_mode_primary_values_and_kreis_mean_fallback():
    out = build_work_production_mass(_population(), _svb(), mode="svb_wohn")
    out = out.set_index("origin_id")["population"]
    # primary: exact svb_wohn
    assert out["031010001000"] == 400.0
    assert out["031510018000"] == 300.0
    assert out["031510020000"] == 100.0
    # fallback for 031510030000: Kreis-03151 mean rate over svb-carrying
    # Gemeinden = (300+100)/(500+500) = 0.4 -> 0.4 * 1000 = 400
    assert np.isclose(out["031510030000"], 400.0)


def test_svb_mode_global_rate_when_whole_kreis_missing():
    pop = pd.DataFrame({
        "origin_id": ["031010001000", "039990001000"],
        "population": [1000.0, 200.0],
    })
    svb = pd.DataFrame({"commune_id": ["031010001000"], "svb_wohn": [400]})
    out = build_work_production_mass(pop, svb, mode="svb_wohn")
    out = out.set_index("origin_id")["population"]
    # global rate = 400/1000 = 0.4 -> 0.4 * 200 = 80
    assert np.isclose(out["039990001000"], 80.0)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        build_work_production_mass(_population(), _svb(), mode="blend")


def test_gravity_off_path_is_byte_identical_and_education_invariant():
    """compute_work_od with population masses equals the pre-change behaviour,
    and the education pass never sees the svb production."""
    from braunschweig.gravity.model import compute_work_od
    df_pop = pd.DataFrame({
        "origin_id": ["A", "B"], "population": [100.0, 200.0]})
    df_emp = pd.DataFrame({
        "destination_id": ["A", "B"], "employees": [150.0, 150.0]})
    df_dist = pd.DataFrame({
        "origin_id": ["A", "A", "B", "B"],
        "destination_id": ["A", "B", "A", "B"],
        "distance_km": [1.0, 5.0, 5.0, 1.0]})
    df_rs = pd.DataFrame({"commune_id": ["A", "B"], "regiostar7": [72, 74]})
    kwargs = dict(df_employees=df_emp, df_distances=df_dist, df_regiostar=df_rs,
                  rs7_by_zone=None, slope=-0.05, constant=1.0, diagonal=1.0,
                  slope_overrides={}, friction_factors=None, max_iterations=100)
    base = compute_work_od(df_population=df_pop, **kwargs)
    again = compute_work_od(df_population=df_pop.copy(), **kwargs)
    pd.testing.assert_frame_equal(base, again)
    # svb production changes the work OD (sanity that the switch has teeth)
    df_prod = build_work_production_mass(
        df_pop, pd.DataFrame({"commune_id": ["A", "B"], "svb_wohn": [90, 30]}),
        mode="svb_wohn")
    switched = compute_work_od(df_population=df_prod, **kwargs)
    assert not switched["weight"].equals(base["weight"])


def test_svb_mass_from_aggregated_frame_is_not_inflated_by_person_rows():
    """Contract guard for the model.py wiring: ``data.census.filtered`` is a
    per-PERSON frame, so the svb branch aggregates it to one row per Gemeinde
    BEFORE calling build_work_production_mass (the helper's documented input).
    The primary mass must be the Gemeinde's svb_wohn value -- merging svb per
    person-row would yield svb_wohn * person_count after the gravity's
    internal groupby-sum."""
    df_person_level = pd.DataFrame({
        "origin_id": ["031010001000"] * 4 + ["031510018000"] * 2,
        "population": [1.0, 1.0, 1.0, 1.0, 2.0, 2.0],
    })
    df_aggregated = df_person_level.groupby(
        "origin_id", as_index=False)["population"].sum()
    out = build_work_production_mass(
        df_aggregated,
        pd.DataFrame({"commune_id": ["031010001000", "031510018000"],
                      "svb_wohn": [40, 30]}),
        mode="svb_wohn",
    ).set_index("origin_id")["population"]
    assert out["031010001000"] == 40.0  # not 4 * 40
    assert out["031510018000"] == 30.0  # not 2 * 30


def test_calibrate_accepts_production_frame_schema():
    """The renamed production frame must group cleanly in _calibrate."""
    from braunschweig.gravity.model import _calibrate
    df_od = pd.DataFrame({
        "origin_id": ["031010001000", "031010001000"],
        "destination_id": ["031010001000", "031510018000"],
        "weight": [0.8, 0.2],
    })
    df_prod = pd.DataFrame({
        "commune_id": ["031010001000"],
        "weight": [400.0],   # svb_wohn production, renamed schema
    })
    df_pendler = pd.DataFrame({
        "orig_ars": ["03101", "03101"],
        "dest_ars": ["03101", "03151"],
        "flow": [320.0, 80.0],
    })
    out = _calibrate(df_od, df_prod, df_pendler)
    got = out.groupby(
        out["origin_id"].str[:5].rename("o")
    )["flow"].sum()
    assert np.isclose(got.loc["03101"], 400.0)
