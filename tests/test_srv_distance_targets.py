"""Unit tests for braunschweig.calibration.srv_distance_targets (synthetic rows only)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_distance_targets as T


def test_band_constants_align_with_gravity_edges():
    from braunschweig.gravity.friction import BAND_EDGES_KM
    assert T.WORK_BAND_EDGES_KM == BAND_EDGES_KM
    assert T.WORK_BAND_EDGES_KM == (0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, float("inf"))
    assert len(T.WORK_BAND_LABELS) == len(BAND_EDGES_KM) - 1
    assert T.WORK_BAND_LABELS[0] == "0_5" and T.WORK_BAND_LABELS[-1] == "100_plus"
    assert T.EDUCATION_BAND_EDGES_KM == (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, float("inf"))
    assert T.EDUCATION_BAND_LABELS == ("0_1", "1_2", "2_5", "5_10", "10_20", "20_plus")


@pytest.mark.parametrize("purpose, age, level", [
    (3, 4, "kindergarten"),
    (3, 6, "kindergarten"),
    (3, 40, None),          # kita at 40: out of model age band, excluded
    (4, 8, "grundschule"),
    (4, 5, "grundschule"),
    (4, 14, None),          # grundschule at 14: out of model age band, excluded
    (5, 12, "sekundar_1"),
    (5, 15.7, "sekundar_1"),  # float age truncated to int
    (5, 17, "upper_secondary"),
    (6, 18, "upper_secondary"),
    (6, 24, "university"),
    (5, 25, None),          # secondary school at 25: not comparable, excluded
    (6, 14, None),          # tertiary at 14: implausible, excluded
    (7, 10, None),          # other education institution: excluded by design
    (1, 40, None),          # work is not an education level
    (3, np.nan, None),      # kita with NaN age
    (np.nan, 10, None),     # NaN purpose code
])
def test_education_level(purpose, age, level):
    assert T.education_level(purpose, age) == level


def test_education_level_descriptive_splits_upper_secondary():
    assert T.education_level_descriptive(5, 17) == "oberstufe"
    assert T.education_level_descriptive(6, 17) == "bbs"
    assert T.education_level_descriptive(4, 8) == "grundschule"


@pytest.mark.parametrize("age, level", [
    (3, "kindergarten"), (7, "grundschule"), (12, "sekundar_1"),
    (16, "upper_secondary"), (19, "upper_secondary"), (22, "university"),
    (15.7, "sekundar_1"),    # float age truncated to int
    (np.nan, None),
    (None, None),
])
def test_model_education_level_by_age(age, level):
    assert T.model_education_level(age) == level


def _raw_frames():
    """Two households (BS, GF), three persons, six trips."""
    households = pd.DataFrame({"HHNR": [1, 2], "AGS": [3101000, 3151005]})
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2], "PNR": [1, 2, 1], "V_ALTER": [40, 17, 8],
    })
    trips = pd.DataFrame({
        "HHNR":            [1,   1,   1,   1,   2,   2],
        "PNR":             [1,   1,   2,   2,   1,   1],
        "WNR":             [1,   2,   1,   2,   1,   2],
        "V_ZWECK":         [1,   19,  5,   19,  4,   19],   # work, home, school, home, grundschule, home
        "E_START_ZWECK":   [19,  1,   19,  5,   19,  4],
        "V_START_LAGE":    [1,   4,   1,   3,   1,   3],    # 1 = start at own home
        "V_ZIEL_LAGE":     [4,   1,   3,   1,   3,   1],
        "V_START_AGS":     [3101000, 3151005, 3101000, 3101000, 3151005, 3151005],
        "V_ZIEL_AGS":      [3151005, 3101000, 3101000, 3101000, 3151005, 3151005],
        "GIS_LAENGE":      [22.0, 22.5, 3.1, 3.0, -7.0, 1.2],
        "GIS_LAENGE_GUELTIG": [22.0, 22.5, 3.1, 3.0, -7.0, 1.2],
        "GEWICHT_W_ZENSUS": [10.0, 10.0, 5.0, 5.0, 7.0, 7.0],
        "REGIOSTAR7":      [72, 72, 72, 72, 74, 74],
    })
    return trips, persons, households


def test_select_person_observations_work_first_home_to_purpose():
    trips, persons, households = _raw_frames()
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert len(obs) == 1
    row = obs.iloc[0]
    assert row["hhnr"] == 1 and row["pnr"] == 1
    assert row["kreis"] == "03101"
    assert row["distance_km"] == pytest.approx(22.0)     # the home->work trip, not work->home
    assert bool(row["intra_gemeinde"]) is False
    assert row["age"] == 40 and row["regiostar7"] == 72
    assert log["n_persons_selected"] == 1


def test_select_person_observations_education_marks_intra_and_excludes_gis_invalid():
    trips, persons, households = _raw_frames()
    obs, log = T.select_person_observations(trips, persons, households, T.EDUCATION_PURPOSES)
    # person (1,2): school trip home->school, intra-Gemeinde; person (2,1): GIS invalid on the
    # home->school leg -> falls back to the school->home leg (1.2 km, valid).
    assert set(zip(obs["hhnr"], obs["pnr"])) == {(1, 2), (2, 1)}
    school = obs[(obs["hhnr"] == 1) & (obs["pnr"] == 2)].iloc[0]
    assert bool(school["intra_gemeinde"]) is True
    assert school["purpose_code"] == 5 and school["age"] == 17
    back = obs[(obs["hhnr"] == 2) & (obs["pnr"] == 1)].iloc[0]
    assert back["distance_km"] == pytest.approx(1.2)
    assert back["purpose_code"] == T.PURPOSE_GRUNDSCHULE  # sourced from E_START_ZWECK, inbound leg
    assert log["n_excluded_gis_invalid"] == 1


def test_select_person_observations_drops_negative_weight_and_over_cap():
    trips, persons, households = _raw_frames()
    trips.loc[0, "GEWICHT_W_ZENSUS"] = -9.0
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert obs.empty and log["n_excluded_weight_negative"] == 1
    trips, persons, households = _raw_frames()
    trips.loc[0, ["GIS_LAENGE", "GIS_LAENGE_GUELTIG"]] = 450.0
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert obs.empty and log["n_excluded_over_cap"] == 1


def test_select_person_observations_reports_household_vs_start_ags_agreement():
    trips, persons, households = _raw_frames()
    _, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert log["share_start_ags_equals_household_ags"] == pytest.approx(1.0)


def test_select_person_observations_excludes_missing_household_ags():
    """A household with NaN AGS must be excluded via the Kreis filter, not admitted as a
    plausible-looking garbage key (CRITICAL-1: pd.NA used to stringify to "<NA>", which
    passed notna() and produced a bogus Kreis)."""
    households = pd.DataFrame({"HHNR": [1, 3], "AGS": [3101000, np.nan]})
    persons = pd.DataFrame({"HHNR": [1, 3], "PNR": [1, 1], "V_ALTER": [40, 30]})
    trips = pd.DataFrame({
        "HHNR": [1, 3],
        "PNR": [1, 1],
        "WNR": [1, 1],
        "V_ZWECK": [1, 1],
        "E_START_ZWECK": [19, 19],
        "V_START_LAGE": [1, 1],
        "V_ZIEL_LAGE": [4, 4],
        "V_START_AGS": [3101000, 3199999],
        "V_ZIEL_AGS": [3151005, 3151005],
        "GIS_LAENGE": [22.0, 15.0],
        "GIS_LAENGE_GUELTIG": [22.0, 15.0],
        "GEWICHT_W_ZENSUS": [10.0, 8.0],
        "REGIOSTAR7": [72, 72],
    })
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert len(obs) == 1 and obs.iloc[0]["hhnr"] == 1
    assert log["n_excluded_no_kreis"] == 1


def test_select_person_observations_sentinel_trip_ags_marks_intra_false_and_excludes_from_share():
    """A -9 (SrV missing-data sentinel) on the SELECTED trip's own AGS must not silently
    stringify into a garbage key that compares equal to itself (CRITICAL-1: -9 used to
    become "-0000009", which could match another garbage key and inflate intra/share)."""
    trips, persons, households = _raw_frames()
    trips.loc[0, "V_START_AGS"] = -9  # the home->work leg selected for person (1,1)
    obs, log = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,))
    assert len(obs) == 1
    row = obs.iloc[0]
    assert bool(row["intra_gemeinde"]) is False
    assert log["n_missing_trip_ags"] == 1
    assert pd.isna(log["share_start_ags_equals_household_ags"])


def test_select_person_observations_gis_fallback_then_weight_exclusion_counted_once():
    """HH2/PNR1's outbound leg is GIS-invalid (falls back to the inbound leg per the
    fallback rule); once the inbound leg ALSO fails the weight check, the person is
    excluded outright (no further fallback) and counted exactly once."""
    trips, persons, households = _raw_frames()
    trips.loc[5, "GEWICHT_W_ZENSUS"] = -3.0  # HH2/PNR1 inbound (fallback) leg now invalid too
    obs, log = T.select_person_observations(trips, persons, households, T.EDUCATION_PURPOSES)
    assert set(zip(obs["hhnr"], obs["pnr"])) == {(1, 2)}
    assert log["n_excluded_weight_negative"] == 1
    assert log["n_excluded_gis_invalid"] == 1


def test_select_person_observations_empty_candidates_returns_full_key_set():
    trips, persons, households = _raw_frames()
    obs, log = T.select_person_observations(trips, persons, households, (99,))
    assert obs.empty
    expected_keys = {
        "n_candidate_trips", "n_persons_selected", "n_excluded_gis_invalid",
        "n_excluded_weight_negative", "n_excluded_over_cap", "n_excluded_no_kreis",
        "n_pool_weight_negative", "n_pool_over_cap", "n_missing_trip_ags",
        "n_missing_age", "n_missing_regiostar7", "share_start_ags_equals_household_ags",
    }
    assert expected_keys <= set(log.keys())
    assert log["n_candidate_trips"] == 0 and log["n_persons_selected"] == 0
    assert pd.isna(log["share_start_ags_equals_household_ags"])


def test_gis_validity_bias_check_medians_and_ratio():
    """Synthetic frame: 3 home-based work candidate trips are GIS-invalid, 3 are GIS-valid
    (one of the valid trips has V_LAENGE == 0 and must be excluded from every median/mean/
    ratio, per R27); a work->home candidate trip is included via E_START_ZWECK; a GIS-invalid
    trip carries the SrV "-5 weiss nicht" missing-length sentinel and must be excluded from
    the invalid median/mean too, while still counting toward n_gis_invalid and the new
    without-self-reported diagnostics (ruling R27); non-work/non-home-based trips are
    excluded from the candidate pool entirely."""
    trips = pd.DataFrame({
        "V_ZWECK":            [1,    1,    1,    1,    19,   5,    1],
        "E_START_ZWECK":      [19,   19,   19,   19,   1,    19,   19],
        "V_START_LAGE":       [1,    1,    1,    1,    3,    1,    1],   # 1 = start at own home
        "V_ZIEL_LAGE":        [4,    4,    4,    4,    1,    3,    4],   # 1 = destination at own home
        "V_LAENGE":           [10.0, 20.0, 5.0,  0.0,  8.0,  99.0, -5.0],
        "GIS_LAENGE":         [9.5,  100.0, 4.5, 3.0,  8.2,  1.0,  50.0],
        "GIS_LAENGE_GUELTIG": [9.5,  -1.0, 4.5,  3.0,  8.2,  1.0,  -1.0],
    })
    result = T.gis_validity_bias_check(trips)
    # candidate pool = rows 0-3 (home->work) + row 4 (work->home, via E_START_ZWECK) + row 6
    # (home->work, GIS-invalid, missing self-reported length); row 5 is a school trip
    # (V_ZWECK 5) and must be excluded.
    assert result["n_gis_invalid"] == 2     # rows 1 and 6 (GIS_LAENGE_GUELTIG <= 0)
    assert result["n_gis_valid"] == 4       # rows 0, 2, 3, 4
    # row 6 (V_LAENGE=-5, the "weiss nicht" sentinel) has no usable self-reported length.
    assert result["n_gis_invalid_without_self_reported"] == 1
    assert result["share_gis_invalid_without_self_reported"] == pytest.approx(0.5)
    # invalid median/mean drop row 6 -> only row 1 (V_LAENGE=20.0) remains.
    assert result["median_self_reported_km_gis_invalid"] == pytest.approx(20.0)
    assert result["mean_self_reported_km_gis_invalid"] == pytest.approx(20.0)
    # valid median/mean drop row 3 (V_LAENGE == 0.0, no usable self-reported length) -> rows
    # 0 (10.0), 2 (5.0), 4 (8.0) remain: sorted 5, 8, 10 -> median 8.0, mean 7.6667.
    assert result["median_self_reported_km_gis_valid"] == pytest.approx(8.0)
    assert result["mean_self_reported_km_gis_valid"] == pytest.approx(23.0 / 3.0)
    # ratio subset is the same V_LAENGE>0 valid rows: 9.5/10.0=0.95, 4.5/5.0=0.9, 8.2/8.0=1.025
    assert result["median_gis_over_self_reported"] == pytest.approx(0.95)


def test_gis_validity_bias_check_empty_candidate_pool_is_all_nan():
    trips = pd.DataFrame({
        "V_ZWECK": [2], "E_START_ZWECK": [19], "V_START_LAGE": [1], "V_ZIEL_LAGE": [4],
        "V_LAENGE": [5.0], "GIS_LAENGE": [5.0], "GIS_LAENGE_GUELTIG": [5.0],
    })
    result = T.gis_validity_bias_check(trips)
    assert result["n_gis_invalid"] == 0 and result["n_gis_valid"] == 0
    assert result["n_gis_invalid_without_self_reported"] == 0
    assert pd.isna(result["share_gis_invalid_without_self_reported"])
    assert pd.isna(result["median_self_reported_km_gis_invalid"])
    assert pd.isna(result["median_self_reported_km_gis_valid"])
    assert pd.isna(result["mean_self_reported_km_gis_invalid"])
    assert pd.isna(result["mean_self_reported_km_gis_valid"])
    assert pd.isna(result["median_gis_over_self_reported"])


def test_weighted_band_shares_sum_to_one_and_respect_weights():
    d = np.array([1.0, 7.0, 7.5, 150.0])
    w = np.array([1.0, 1.0, 2.0, 1.0])
    shares = T.weighted_band_shares(d, w, T.WORK_BAND_EDGES_KM)
    assert shares.shape == (7,)
    assert shares.sum() == pytest.approx(1.0)
    assert shares[0] == pytest.approx(0.2)   # 1.0 km
    assert shares[1] == pytest.approx(0.6)   # 7.0 + 7.5 km weighted 3 of 5
    assert shares[6] == pytest.approx(0.2)   # 150 km
    assert T.weighted_band_shares(np.array([]), np.array([]), T.WORK_BAND_EDGES_KM).sum() == 0.0
    # Zero weights -> all zeros, correct shape
    shares_zero = T.weighted_band_shares(np.array([1.0, 7.0]), np.zeros(2), T.WORK_BAND_EDGES_KM)
    assert shares_zero.shape == (7,) and np.allclose(shares_zero, 0.0)
    # Band-edge boundary conditions
    b0 = T.weighted_band_shares(np.array([4.999]), np.array([1.0]), T.WORK_BAND_EDGES_KM); assert b0[0] == 1.0
    b1 = T.weighted_band_shares(np.array([5.0]), np.array([1.0]), T.WORK_BAND_EDGES_KM); assert b1[1] == 1.0
    b6 = T.weighted_band_shares(np.array([100.0]), np.array([1.0]), T.WORK_BAND_EDGES_KM); assert b6[6] == 1.0
    # NaN and negative -> raise ValueError
    with pytest.raises(ValueError, match="NaN or negative"):
        T.weighted_band_shares(np.array([1.0, np.nan]), np.array([1.0, 1.0]), T.WORK_BAND_EDGES_KM)
    with pytest.raises(ValueError, match="NaN or negative"):
        T.weighted_band_shares(np.array([1.0, -5.0]), np.array([1.0, 1.0]), T.WORK_BAND_EDGES_KM)


def test_weighted_quantiles_matches_unweighted_median_for_equal_weights():
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    q = T.weighted_quantiles(v, np.ones(5), np.array([0.5]))
    assert q[0] == pytest.approx(3.0)
    q2 = T.weighted_quantiles(np.array([1.0, 10.0]), np.array([3.0, 1.0]), np.array([0.5]))
    assert 1.0 <= q2[0] < 10.0  # heavy weight on 1.0 pulls the median down
    # Hazen midpoint-CDF convention differs from np.quantile away from the median by design
    mono = T.weighted_quantiles(v, np.ones(5), np.linspace(0.01, 0.99, 99))
    assert np.all(np.diff(mono) >= 0)
    # Empty or zero-weight input -> all NaN
    q_empty = T.weighted_quantiles(np.array([]), np.array([]), np.array([0.5, 0.95]))
    assert np.all(np.isnan(q_empty)) and q_empty.shape == (2,)
    q_zero = T.weighted_quantiles(np.array([1.0, 2.0]), np.zeros(2), np.array([0.5]))
    assert np.all(np.isnan(q_zero))
    # NaN in values -> raise ValueError
    with pytest.raises(ValueError, match="NaN"):
        T.weighted_quantiles(np.array([1.0, np.nan, 3.0]), np.array([1.0, 1.0, 1.0]), np.array([0.5]))


def test_shrink_toward_pool_limits():
    kreis = np.array([0.8, 0.2]); pool = np.array([0.4, 0.6])
    np.testing.assert_allclose(T.shrink_toward_pool(kreis, 0, pool, 100.0), pool)
    np.testing.assert_allclose(T.shrink_toward_pool(kreis, 100, pool, 100.0), [0.6, 0.4])
    np.testing.assert_allclose(T.shrink_toward_pool(kreis, 1e9, pool, 100.0), kreis, atol=1e-6)


def test_emd_on_shares_and_noise_floor():
    p = np.array([1.0, 0, 0]); q = np.array([0, 0, 1.0])
    assert T.emd_on_shares(p, q) == pytest.approx(1.0)   # normalised to [0,1] by (n_bands-1)
    rng = np.random.default_rng(1)
    d = rng.uniform(0, 60, 400); w = np.ones(400)
    floor_small = T.bootstrap_emd_noise_floor(d[:40], w[:40], T.WORK_BAND_EDGES_KM, n_bootstrap=200, seed=0)
    floor_large = T.bootstrap_emd_noise_floor(d, w, T.WORK_BAND_EDGES_KM, n_bootstrap=200, seed=0)
    assert floor_large > 0 and floor_small > floor_large   # noise shrinks with n
    assert T.bootstrap_emd_noise_floor(d, w, T.WORK_BAND_EDGES_KM, n_bootstrap=50, seed=0) == \
        T.bootstrap_emd_noise_floor(d, w, T.WORK_BAND_EDGES_KM, n_bootstrap=50, seed=0)  # seeded
    # n_bootstrap < 1 -> raise ValueError
    with pytest.raises(ValueError, match="n_bootstrap must be >= 1"):
        T.bootstrap_emd_noise_floor(d, w, T.WORK_BAND_EDGES_KM, n_bootstrap=0, seed=0)


def _obs(n_bs=300, n_gf=60, seed=3):
    rng = np.random.default_rng(seed)
    bs = pd.DataFrame({
        "hhnr": np.arange(n_bs), "pnr": 1, "kreis": "03101", "regiostar7": 72,
        "purpose_code": 1, "age": 40,
        "distance_km": rng.gamma(2.0, 3.0, n_bs), "weight": 1.0,
        "intra_gemeinde": rng.random(n_bs) < 0.6,
    })
    gf = pd.DataFrame({
        "hhnr": 10000 + np.arange(n_gf), "pnr": 1, "kreis": "03151", "regiostar7": 74,
        "purpose_code": 1, "age": 40,
        "distance_km": rng.gamma(2.0, 9.0, n_gf), "weight": 1.0,
        "intra_gemeinde": rng.random(n_gf) < 0.3,
    })
    return pd.concat([bs, gf], ignore_index=True)


def test_build_commute_table_rows_shares_and_proxy():
    table = T.build_commute_table(_obs(), n_bootstrap=50)
    kreis_rows = table[table["level_geo"] == "kreis"].set_index("code")
    assert set(kreis_rows.index) == set(T.ZGB_KREISE)           # all 8 incl. Wolfsburg proxy
    assert kreis_rows.loc["03103", "source"] == "proxy_rs7_72"
    assert kreis_rows.loc["03101", "source"] == "srv"
    for scope in ("all", "inter", "intra"):
        cols = [f"share_{scope}_{lbl}" for lbl in T.WORK_BAND_LABELS]
        sums = kreis_rows.loc[["03101", "03151"], cols].sum(axis=1)
        assert np.allclose(sums, 1.0)
        shr = [f"share_{scope}_shrunk_{lbl}" for lbl in T.WORK_BAND_LABELS]
        assert np.allclose(kreis_rows.loc[["03101", "03151"], shr].sum(axis=1), 1.0)
    # proxy row equals the RS7-72 pool row (BS only in this fixture)
    rs72 = table[(table["level_geo"] == "rs7") & (table["code"] == "72")].iloc[0]
    assert kreis_rows.loc["03103", "share_all_0_5"] == pytest.approx(rs72["share_all_0_5"])
    # shrinkage pulls the small Kreis (n=60) more than the large one (n=300)
    zgb = table[table["level_geo"] == "zgb"].iloc[0]
    gap_gf = abs(kreis_rows.loc["03151", "share_all_shrunk_0_5"] - kreis_rows.loc["03151", "share_all_0_5"])
    gap_bs = abs(kreis_rows.loc["03101", "share_all_shrunk_0_5"] - kreis_rows.loc["03101", "share_all_0_5"])
    assert gap_gf >= gap_bs
    assert (kreis_rows["emd_noise_95_all"] >= 0).all()
    assert 0.0 <= kreis_rows.loc["03101", "share_intra"] <= 1.0
    assert zgb["n_persons"] == 360


def test_build_education_table_levels_and_comparable_flag():
    obs = _obs()
    obs["purpose_code"] = np.where(obs.index % 2 == 0, 5, 6)
    obs["age"] = np.where(obs.index % 4 == 0, 12, 17)
    table = T.build_education_table(obs, n_bootstrap=20)
    levels = set(table["education_level"])
    assert {"sekundar_1", "upper_secondary", "oberstufe", "bbs"} <= levels
    comp = table[table["comparable"]]
    assert set(comp["education_level"]) <= set(T.COMPARABLE_LEVELS)
    cols = [f"share_{lbl}" for lbl in T.EDUCATION_BAND_LABELS]
    shrunk_cols = [f"share_shrunk_{lbl}" for lbl in T.EDUCATION_BAND_LABELS]
    # Raw shares sum to 1.0 only where the cell actually has persons: an empty Kreis x
    # level cell carries all-zero raw shares by design (ruling R1), not a share vector.
    populated = comp[comp["n_persons"] > 0]
    assert np.allclose(populated[cols].sum(axis=1), 1.0)
    # Shrunk shares sum to 1.0 for every row of a level that has any data at all: an
    # empty Kreis cell's shrunk shares equal its pool, which itself sums to 1.0.
    zgb_by_level = comp[comp["level_geo"] == "zgb"].set_index("education_level")
    for level in set(comp["education_level"]):
        if zgb_by_level.loc[level, "n_persons"] > 0:
            level_rows = comp[comp["education_level"] == level]
            assert np.allclose(level_rows[shrunk_cols].sum(axis=1), 1.0)
    # Pin the R1 contract explicitly: an empty cell has all-zero raw shares and a zero
    # bootstrap noise floor (there are no observations to resample).
    empty = comp[comp["n_persons"] == 0]
    assert (empty[cols] == 0.0).all(axis=None)
    assert (empty["emd_noise_95"] == 0.0).all()
    assert set(table[table["level_geo"] == "kreis"]["code"]) == set(T.ZGB_KREISE)


def test_build_quantile_table_is_long_monotone_and_euclidean():
    table = T.build_quantile_table(_obs(), detour_factor=1.3)
    bs = table[(table["level_geo"] == "kreis") & (table["code"] == "03101")].sort_values("percentile")
    assert list(bs["percentile"]) == list(range(1, 100))
    assert np.all(np.diff(bs["distance_km_euclid_raw"]) >= 0)
    assert np.all(np.diff(bs["distance_km_euclid_shrunk"]) >= 0)
    # euclidean = routed / 1.3: the raw median must be below the routed median
    obs = _obs(); routed_median = np.median(obs[obs["kreis"] == "03101"]["distance_km"])
    assert bs[bs["percentile"] == 50]["distance_km_euclid_raw"].iloc[0] == pytest.approx(routed_median / 1.3, rel=0.05)
    assert "03103" in set(table["code"])


def test_loaders_round_trip(tmp_path):
    obs = _obs()
    T.build_commute_table(obs, n_bootstrap=10).to_csv(tmp_path / T.COMMUTE_TABLE, index=False)
    obs_e = obs.assign(purpose_code=3, age=4)
    T.build_education_table(obs_e, n_bootstrap=10).to_csv(tmp_path / T.EDUCATION_TABLE, index=False)
    T.build_quantile_table(obs).to_csv(tmp_path / T.QUANTILE_TABLE, index=False)
    c = T.load_commute_targets(tmp_path); e = T.load_education_targets(tmp_path); q = T.load_commute_quantiles(tmp_path)
    assert c["code"].dtype == object and "03101" in set(c["code"])
    # A value that depends on the actual data, not merely on the level's presence: with
    # purpose_code=3, age=4 every one of the 360 persons maps to "kindergarten", so the
    # ZGB row for that level must carry all of them.
    kg_zgb = e[(e["level_geo"] == "zgb") & (e["education_level"] == "kindergarten")]
    assert len(kg_zgb) == 1 and int(kg_zgb.iloc[0]["n_persons"]) == 360
    assert len(q[q["code"] == "03101"]) == 99


def test_loaders_raise_file_not_found_with_regeneration_hint(tmp_path):
    for loader in (T.load_commute_targets, T.load_education_targets, T.load_commute_quantiles):
        with pytest.raises(FileNotFoundError, match="extract_srv_primary_distance_targets"):
            loader(tmp_path)


def test_dominant_rs7_by_kreis_is_weight_modal():
    obs = pd.DataFrame({
        "kreis": ["03101"] * 5 + ["03101"] * 295,
        "regiostar7": [71] * 5 + [72] * 295,
        "weight": [1000.0] * 5 + [1.0] * 295,
    })
    assert T.dominant_rs7_by_kreis(obs)["03101"] == 71   # 5 persons @ w=1000 beats 295 @ w=1


def test_weighted_mean_median_zero_weight_cell_does_not_crash():
    """IMPORTANT-1: np.average raises ZeroDivisionError on an all-zero weight vector; a
    non-empty subset can still have zero total weight (a zero weight passes the
    ``weight >= 0`` upstream filter), so build_commute_table must not crash on it."""
    obs = _obs()
    obs.loc[obs["kreis"] == "03101", "weight"] = 0.0
    table = T.build_commute_table(obs, n_bootstrap=10)  # must not raise
    row = table[(table["level_geo"] == "kreis") & (table["code"] == "03101")].iloc[0]
    assert row["n_persons"] == 300
    assert pd.isna(row["mean_km"]) and pd.isna(row["median_km"]) and pd.isna(row["share_intra"])


def test_build_commute_table_empty_kreis_contract():
    """IMPORTANT-4a: a Kreis absent from the fixture (03102) gets a well-formed empty row
    that shrinks to the ZGB pool (it has no persons at all, hence no RS7-modal pool)."""
    table = T.build_commute_table(_obs(), n_bootstrap=50)
    kreis_rows = table[table["level_geo"] == "kreis"].set_index("code")
    row = kreis_rows.loc["03102"]
    assert row["n_persons"] == 0
    assert pd.isna(row["mean_km"]) and pd.isna(row["median_km"]) and pd.isna(row["share_intra"])
    raw_cols = [f"share_all_{lbl}" for lbl in T.WORK_BAND_LABELS]
    assert (row[raw_cols].astype(float) == 0.0).all()
    assert row["emd_noise_95_all"] == 0.0
    # "03102" never appears in the fixture at all, so dominant_rs7_by_kreis has no entry
    # for it and the ZGB pool (not an RS7 pool) is the expected fallback.
    assert "03102" not in T.dominant_rs7_by_kreis(_obs())
    zgb = table[table["level_geo"] == "zgb"].iloc[0]
    shrunk_cols = [f"share_all_shrunk_{lbl}" for lbl in T.WORK_BAND_LABELS]
    np.testing.assert_allclose(row[shrunk_cols].values.astype(float), zgb[raw_cols].values.astype(float))


def test_build_quantile_table_empty_kreis_uses_pool_quantiles_exactly():
    """IMPORTANT-4b: 03102's raw quantiles are NaN and its shrunk quantiles equal the
    fallback pool's (ZGB, since 03102 has no persons and thus no RS7-modal pool)."""
    table = T.build_quantile_table(_obs())
    row = table[(table["level_geo"] == "kreis") & (table["code"] == "03102")].sort_values("percentile")
    assert (row["n_persons"] == 0).all()
    assert row["distance_km_euclid_raw"].isna().all()
    zgb = table[table["level_geo"] == "zgb"].sort_values("percentile")
    np.testing.assert_allclose(
        row["distance_km_euclid_shrunk"].values, zgb["distance_km_euclid_shrunk"].values)


def test_build_education_table_wolfsburg_proxy_falls_back_to_zgb_when_rs7_72_absent(caplog):
    """IMPORTANT-2: route all RS7-72 (BS) persons to "university" and all RS7-74 (GF)
    persons to "sekundar_1", so "sekundar_1" has region-wide persons but none in RS7-72.
    The Wolfsburg proxy row for "sekundar_1" must then use the ZGB pool (not an all-zero
    raw/shrunk row), and the substitution must be logged by name."""
    obs = _obs()
    obs["purpose_code"] = np.where(obs["regiostar7"] == 72, 6, 5)
    obs["age"] = np.where(obs["regiostar7"] == 72, 25, 12)
    caplog.set_level("WARNING")
    table = T.build_education_table(obs, n_bootstrap=10)
    sek = table[table["education_level"] == "sekundar_1"]
    wob = sek[(sek["level_geo"] == "kreis") & (sek["code"] == T.WOLFSBURG_KREIS)].iloc[0]
    zgb = sek[sek["level_geo"] == "zgb"].iloc[0]
    assert wob["n_persons"] == 0
    assert zgb["n_persons"] > 0
    cols = [f"share_{lbl}" for lbl in T.EDUCATION_BAND_LABELS]
    shrunk_cols = [f"share_shrunk_{lbl}" for lbl in T.EDUCATION_BAND_LABELS]
    np.testing.assert_allclose(wob[shrunk_cols].values.astype(float), zgb[cols].values.astype(float))
    assert any("sekundar_1" in r.getMessage() and "ZGB pool used" in r.getMessage() for r in caplog.records)


def test_builders_log_kreis_row_pool_provenance(caplog):
    """IMPORTANT-3 (fallback transparency, CLAUDE.md MANDATORY): each builder must log
    the own-pool / ZGB-fallback / proxy split of its Kreis rows, not just build silently."""
    caplog.set_level("INFO")
    T.build_commute_table(_obs(), n_bootstrap=10)
    T.build_education_table(_obs(), n_bootstrap=10)
    T.build_quantile_table(_obs())
    messages = [r.getMessage() for r in caplog.records]
    for table_name in ("commute table", "education table", "quantile table"):
        assert any(
            table_name in m and "own RS7 pool" in m and "ZGB fallback" in m and "proxy" in m
            for m in messages
        ), f"missing pool-provenance log line for {table_name}"
