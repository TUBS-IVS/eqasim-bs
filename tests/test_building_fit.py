"""Tests for the building-potential fit report (braunschweig.calibration.building_fit).

The report measures whether the realised within-zone distribution of activities
over buildings follows the building activity potentials. Because the potentials
are a 100% reference and a run is sampled (e.g. 25%), the report works in
sampling-rate-invariant within-zone SHARES, not absolute counts.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration.building_fit import build_fit_report, within_zone_fit


def test_realised_proportional_to_potential_is_perfect_fit():
    # Realised counts exactly 10x the potential weights -> identical shares.
    potential = np.array([1.0, 2.0, 3.0, 4.0])
    realised = np.array([10.0, 20.0, 30.0, 40.0])

    m = within_zone_fit(realised, potential)

    assert m["n_buildings"] == 4
    assert m["pearson"] == pytest.approx(1.0)
    assert m["tv_distance"] == pytest.approx(0.0, abs=1e-9)


def test_realised_uniform_vs_skewed_potential_has_positive_tv_distance():
    # Potential is concentrated in one building; realised spreads uniformly ->
    # the within-zone share distributions disagree.
    potential = np.array([97.0, 1.0, 1.0, 1.0])
    realised = np.array([25.0, 25.0, 25.0, 25.0])

    m = within_zone_fit(realised, potential)

    # potential shares = [0.97,0.01,0.01,0.01], realised = [0.25]*4
    # TV = 0.5 * (|0.72| + 3*|0.24|) = 0.5 * (0.72 + 0.72) = 0.72
    assert m["tv_distance"] == pytest.approx(0.72, abs=1e-9)
    # A constant realised vector has no variance -> correlation is undefined.
    assert np.isnan(m["pearson"])


def test_anticorrelated_realised_has_negative_pearson():
    # Realised is the reverse ranking of the potential -> strong anti-correlation.
    potential = np.array([1.0, 2.0, 3.0, 4.0])
    realised = np.array([40.0, 30.0, 20.0, 10.0])

    m = within_zone_fit(realised, potential)

    assert m["pearson"] == pytest.approx(-1.0)
    assert m["tv_distance"] > 0.0


def _potential_df():
    # Zone A: 3 buildings; Zone B: 2 buildings.
    return pd.DataFrame({
        "building_id": [1, 2, 3, 10, 11],
        "zone": ["A", "A", "A", "B", "B"],
        "potential": [1.0, 2.0, 3.0, 5.0, 5.0],
    })


def test_report_has_one_row_per_zone_and_perfect_fit_in_proportional_zone():
    potential = _potential_df()
    # Zone A realised exactly proportional to potential (5x); zone B all on one building.
    # One row per realised activity:
    rows = (
        [1] * 5 + [2] * 10 + [3] * 15   # zone A: 5:10:15 == potential 1:2:3
        + [10] * 8 + [11] * 0            # zone B: all on building 10
    )
    realised = pd.DataFrame({"building_id": rows})

    rep = build_fit_report(realised, potential, sampling_rate=0.25)

    per_zone = rep["per_zone"].set_index("zone")
    assert set(per_zone.index) == {"A", "B"}
    assert per_zone.loc["A", "tv_distance"] == pytest.approx(0.0, abs=1e-9)
    assert per_zone.loc["A", "pearson"] == pytest.approx(1.0)
    assert per_zone.loc["B", "tv_distance"] > 0.0
    # realised activities counted (raw, unscaled) per zone
    assert per_zone.loc["A", "realised_activities"] == 30
    assert per_zone.loc["B", "realised_activities"] == 8


def test_report_counts_fallback_activities_on_buildings_without_potential():
    potential = _potential_df()
    # building 999 is NOT in the potential set -> fallback (no silent fallback).
    realised = pd.DataFrame({"building_id": [1, 2, 3, 999, 999]})

    rep = build_fit_report(realised, potential, sampling_rate=1.0)

    cov = rep["coverage"]
    # 3 of 5 realised activities hit a potential building -> 60% primary.
    assert cov["realised_total"] == 5
    assert cov["on_potential_building"] == 3
    assert cov["primary_rate"] == pytest.approx(0.6)
    assert cov["fallback_rate"] == pytest.approx(0.4)


def test_per_building_residuals_gives_within_zone_shares_and_residual():
    from braunschweig.calibration.building_fit import per_building_residuals

    potential = _potential_df()  # zone A: ids 1,2,3 (pot 1,2,3); zone B: 10,11 (pot 5,5)
    realised = pd.DataFrame({"building_id": [1]*5 + [2]*10 + [3]*15 + [10]*8})

    out = per_building_residuals(realised, potential).set_index("building_id")

    # Zone A building 3: potential 3/6=0.5; realised 15/30=0.5 -> residual 0.
    assert out.loc[3, "potential_share"] == pytest.approx(0.5)
    assert out.loc[3, "realised_share"] == pytest.approx(0.5)
    assert out.loc[3, "share_residual"] == pytest.approx(0.0)
    # Zone B building 11: potential 5/10=0.5; realised 0/8=0 -> under-filled, residual -0.5.
    assert out.loc[11, "realised_count"] == 0
    assert out.loc[11, "realised_share"] == pytest.approx(0.0)
    assert out.loc[11, "share_residual"] == pytest.approx(-0.5)


def test_prepare_frames_excludes_fake_candidates_so_they_count_as_fallback():
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.calibration.run_building_fit import _prepare_frames

    candidates = gpd.GeoDataFrame({
        "employees": [10.0, 5.0, 1.0],
        "fake": [False, False, True],          # work_2 is a synthetic-centroid fallback
        "commune_id": ["A", "A", "B"],
        "location_id": ["work_0", "work_1", "work_2"],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
    }, crs="EPSG:25832")
    df_work = pd.DataFrame({"location_id": ["work_0", "work_0", "work_1", "work_2"]})

    realised, potential, support = _prepare_frames(df_work, candidates)

    # The fake candidate is excluded from the potential support ...
    assert set(potential["building_id"]) == {"work_0", "work_1"}
    assert len(support) == 2
    assert len(realised) == 4
    # ... so the worker placed on it is reported as a fallback, not silently absorbed.
    rep = build_fit_report(realised, potential, sampling_rate=1.0)
    assert rep["coverage"]["off_potential_building"] == 1
    assert rep["coverage"]["primary_rate"] == pytest.approx(0.75)


def test_multinomial_tv_floor_is_zero_for_large_sample_and_high_for_n1():
    from braunschweig.calibration.building_fit import multinomial_tv_floor

    potential_share = np.array([0.25, 0.25, 0.25, 0.25])
    # N=1: a single activity lands on one building -> realised share [1,0,0,0],
    # TV vs uniform = 0.5*(0.75 + 3*0.25) = 0.75 for every possible draw.
    floor_n1 = multinomial_tv_floor(potential_share, 1, n_draws=50, seed=0)
    assert floor_n1 == pytest.approx(0.75, abs=1e-9)

    # Large N: a perfect multinomial sample tracks the shares -> floor -> ~0.
    floor_big = multinomial_tv_floor(potential_share, 5000, n_draws=50, seed=0)
    assert floor_big < 0.05


def test_excess_tv_is_near_zero_when_realised_is_a_clean_sample_of_potential():
    # When the realised counts ARE a multinomial sample of the potential, the
    # observed TV sits at the noise floor, so excess_tv ~ 0 (the discreteness
    # 0-effect is netted out -- the "smart" sampling-rate-fair signal).
    from braunschweig.calibration.building_fit import build_fit_report

    n_buildings, n_activities = 40, 120  # sub-1 density, like the real 25% run
    potential = pd.DataFrame({
        "building_id": list(range(n_buildings)),
        "zone": ["Z"] * n_buildings,
        "potential": [1.0] * n_buildings,
    })
    rng = np.random.RandomState(0)
    counts = rng.multinomial(n_activities, [1.0 / n_buildings] * n_buildings)
    rows = sum(([b] * int(c) for b, c in enumerate(counts)), [])
    realised = pd.DataFrame({"building_id": rows})

    rep = build_fit_report(realised, potential, sampling_rate=0.25)
    z = rep["per_zone"].set_index("zone").loc["Z"]
    assert "tv_floor" in rep["per_zone"].columns and "excess_tv" in rep["per_zone"].columns
    assert z["tv_distance"] > 0.0       # raw TV is inflated by discreteness ...
    assert z["tv_floor"] > 0.0          # ... and so is the floor ...
    assert abs(z["excess_tv"]) < 0.05   # ... so the excess (real misfit) is ~0.


def test_secondary_potential_support_selects_per_purpose_potential_column():
    from braunschweig.calibration.run_building_fit_secondary import secondary_potential_support

    # The reconstructed chainsolver candidate table (build_secondary_candidates):
    # shop/leisure rows are gpkg buildings (sec_b_*), other rows are legacy catalog.
    candidates = pd.DataFrame({
        "location_id": ["sec_b_1", "sec_b_2", "sec_7"],
        "commune_id": ["A", "A", "B"],
        "pot_shop": [3.0, 0.0, 0.0],
        "pot_leisure": [0.0, 5.0, 0.0],
        "pot_other": [0.0, 0.0, 2.0],
    })
    # shop -> pot_shop; only candidates with potential>0 form the support.
    shop = secondary_potential_support(candidates, "shop").set_index("building_id")
    assert set(shop.index) == {"sec_b_1"}
    assert shop.loc["sec_b_1", "potential"] == pytest.approx(3.0)
    assert shop.loc["sec_b_1", "zone"] == "A"
    # leisure -> pot_leisure
    assert set(secondary_potential_support(candidates, "leisure")["building_id"]) == {"sec_b_2"}
    # other -> pot_other
    assert set(secondary_potential_support(candidates, "other")["building_id"]) == {"sec_7"}
