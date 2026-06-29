import pandas as pd
from braunschweig.calibration.distance_fit import work_decomposition as W


def test_between_gemeinde_uses_centroids():
    df = pd.DataFrame({"person_id": [1], "home_commune_id": ["A"], "work_commune_id": ["B"]})
    centroids = {"A": (0.0, 0.0), "B": (4000.0, 0.0)}  # 4 km apart
    out = W.between_gemeinde_distances(df, centroids, detour_factor=1.0)
    assert abs(out.iloc[0]["between_km"] - 4.0) < 1e-9


def test_consistency_ratios():
    out = W.jobs_attraction_consistency(
        assigned_jobs_by_commune={"A": 120.0},
        employees_by_commune={"A": 100.0},
        potential_by_commune={"A": 200.0},
    )
    row = out.iloc[0]
    assert abs(row["assigned_vs_employees_ratio"] - 1.2) < 1e-9
    assert abs(row["potential_vs_employees_ratio"] - 2.0) < 1e-9
