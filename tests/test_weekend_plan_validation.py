import pandas as pd
from braunschweig.analysis import weekend_plan_validation as wpv


def _trace():
    return pd.DataFrame({
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1],
        "donor_day_type": ["weekday", "weekend", "weekend"],
        "resolution": ["own_plan", "hh_match", "person_fallback"],
        "match_level": [float("nan"), 0, 2],
    })


def test_resolution_funnel_counts_each_path():
    funnel = wpv.resolution_funnel(_trace())
    counts = dict(zip(funnel["resolution"], funnel["n"]))
    assert counts["own_plan"] == 1
    assert counts["hh_match"] == 1
    assert counts["person_fallback"] == 1


def test_source_origin_breakdown_weekend_only():
    bd = wpv.source_origin_breakdown(_trace())
    shares = dict(zip(bd["resolution"], bd["share"]))
    assert abs(shares["hh_match"] - 0.5) < 1e-9
    assert abs(shares["person_fallback"] - 0.5) < 1e-9


def test_hh_match_level_funnel_counts_levels():
    trace = pd.DataFrame({
        "H_ID": [1, 2, 3, 4, 5], "P_ID": [1, 1, 1, 1, 1],
        "donor_day_type": ["weekend"] * 5,
        "resolution": ["hh_match", "hh_match", "hh_match",
                       "person_fallback", "own_plan"],
        "match_level": [0, 0, 2, 1, float("nan")],
    })
    funnel = wpv.hh_match_level_funnel(trace)
    counts = dict(zip(funnel["match_level"], funnel["n"]))
    # only hh_match rows are counted: level 0 twice, level 2 once
    assert counts[0] == 2
    assert counts[2] == 1
    # person_fallback (level 1) is NOT in the hh_match funnel
    assert 1 not in counts


def test_behavioural_sanity_fails_loud_without_donor_day_type():
    import pytest
    persons = pd.DataFrame({"person_id": ["a", "b"]})  # no donor_day_type
    trips = pd.DataFrame({"person_id": ["a", "a", "b"]})
    with pytest.raises(ValueError, match="donor_day_type"):
        wpv.behavioural_sanity(persons, trips)


def test_behavioural_sanity_computes_trips_per_person_per_cohort():
    persons = pd.DataFrame({
        "person_id": ["a", "b", "c"],
        "donor_day_type": ["weekday", "weekend", "weekend"],
    })
    trips = pd.DataFrame({"person_id": ["a", "a", "b"]})  # a:2, b:1, c:0
    out = wpv.behavioural_sanity(persons, trips)
    tpp = dict(zip(out["donor_day_type"], out["trips_per_person"]))
    assert tpp["weekday"] == 2.0
    assert tpp["weekend"] == 0.5  # (1 + 0) / 2
