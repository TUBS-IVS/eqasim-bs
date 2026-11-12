import numpy as np
import pandas as pd
from braunschweig.popsim import commute_distance as cd


def test_commute_distance_from_trips():
    # preceding_purpose added: the corrected stage selects home<->purpose legs
    # (mirroring data/hts/commute_distance.py), not all purpose-bound trips.
    trips = pd.DataFrame({
        "person_id": ["p", "p", "q"],
        "preceding_purpose": ["home", "work", "home"],
        "following_purpose": ["work", "home", "education"],
        "euclidean_distance": [5000.0, 5000.0, 2000.0],
    })
    out = cd.run(trips)
    assert set(out) == {"work", "education"}
    assert out["work"].set_index("person_id").loc["p", "commute_distance"] == 5000.0
    assert out["education"].set_index("person_id").loc["q", "commute_distance"] == 2000.0
    assert list(out["work"].columns) == ["person_id", "commute_distance"]


def test_commute_distance_uses_home_leg_not_business_max():
    # MiD W_ZWECK=2 (dienstlich/business) also maps to "work", so the previous
    # max-over-all-work-trips semantics let a single long business trip (work->work,
    # up to ~730 km) become the person's commute distance. The corrected stage takes
    # the FIRST home<->work leg per person, like the legacy HTS stage.
    trips = pd.DataFrame({
        "person_id":          [1, 1],
        "preceding_purpose":  ["home", "work"],
        "following_purpose":  ["work", "work"],
        "euclidean_distance": [5000.0, 300000.0],
    })
    out = cd.run(trips)
    assert out["work"].set_index("person_id").loc[1, "commute_distance"] == 5000.0


def test_commute_distance_imputes_missing_from_cdf():
    # persons 1-3 have valid home->work legs; person 4 has a work activity but a
    # NaN distance -> must receive an imputed (non-NaN) distance from the pool.
    trips = pd.DataFrame({
        "person_id":          [1, 2, 3, 4],
        "preceding_purpose":  ["home"] * 4,
        "following_purpose":  ["work"] * 4,
        "euclidean_distance": [1000.0, 2000.0, 3000.0, np.nan],
    })
    out = cd.run(trips, random_seed=0)
    work = out["work"].set_index("person_id")
    assert not np.isnan(work.loc[4, "commute_distance"])
    assert work.loc[4, "commute_distance"] in {1000.0, 2000.0, 3000.0}


def test_commute_distance_rate_logged_primary_when_distances_observed(caplog):
    import logging
    n = 20
    trips = pd.DataFrame({
        "person_id": list(range(n)),
        "preceding_purpose": ["home"] * n,
        "following_purpose": ["work"] * n,
        "euclidean_distance": [1000.0 + i for i in range(n - 1)] + [np.nan],
    })
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.commute_distance"):
        out = cd.run(trips)
    recs = [r for r in caplog.records
            if "[popsim.commute_distance.work] primary 19/20" in r.message]
    assert len(recs) == 1 and recs[0].levelno == logging.INFO
    assert out["work"]["commute_distance"].notna().all()
