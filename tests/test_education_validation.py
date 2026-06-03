import numpy as np
import pandas as pd
from braunschweig.analysis.run_education_validation import (
    enrollment_vs_capacity, level_summary,
)


def test_enrollment_vs_capacity_scales_to_full_population():
    assignments = pd.DataFrame({
        "person_id": [1, 2, 3],
        "location_id": ["g1", "g1", "s1"],
        "level": ["grundschule", "grundschule", "sekundar_1"],
        "commute_km": [1.0, 2.0, 5.0],
    })
    facilities = pd.DataFrame({
        "school_id": ["g1", "s1"],
        "name": ["A", "B"],
        "level": ["grundschule", "sekundar_1"],
        "capacity": [200.0, 100.0],
    })
    out = enrollment_vs_capacity(assignments, facilities, sampling_rate=0.5)
    g1 = out[out["school_id"] == "g1"].iloc[0]
    assert g1["assigned_pupils"] == 4.0
    assert np.isclose(g1["fill_ratio"], 4.0 / 200.0)


def test_level_summary_reports_distance_and_counts():
    assignments = pd.DataFrame({
        "person_id": [1, 2, 3],
        "location_id": ["g1", "g1", "s1"],
        "level": ["grundschule", "grundschule", "sekundar_1"],
        "commute_km": [1.0, 3.0, 5.0],
    })
    out = level_summary(assignments).set_index("level")
    assert out.loc["grundschule", "n_pupils"] == 2
    assert np.isclose(out.loc["grundschule", "mean_commute_km"], 2.0)
    assert np.isclose(out.loc["sekundar_1", "median_commute_km"], 5.0)
