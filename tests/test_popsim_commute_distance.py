import pandas as pd
from braunschweig.popsim import commute_distance as cd


def test_commute_distance_from_trips():
    trips = pd.DataFrame({
        "person_id": ["p", "p", "q"],
        "following_purpose": ["work", "home", "education"],
        "euclidean_distance": [5000.0, 5000.0, 2000.0],
    })
    out = cd.run(trips)
    assert set(out) == {"work", "education"}
    assert out["work"].set_index("person_id").loc["p", "commute_distance"] == 5000.0
    assert out["education"].set_index("person_id").loc["q", "commute_distance"] == 2000.0
    assert list(out["work"].columns) == ["person_id", "commute_distance"]
