import numpy as np
import pandas as pd
from braunschweig.data.education import student_origins as so


def test_age_band_sums_destatis_classes():
    # 18-29 spans DESTATIS lower bounds {18, 20, 25}. Both sexes summed.
    df = pd.DataFrame({
        "kreis": ["03241", "03241", "03241", "03241", "01001"],
        "sex":   ["male", "female", "male", "male", "male"],
        "age_class": [18, 20, 25, 30, 20],
        "weight": [10, 20, 30, 999, 5],
    })
    pop = so._student_age_pop(df, 18, 29)
    assert pop.loc["03241"] == 60          # 10+20+30, the 30-class excluded
    assert pop.loc["01001"] == 5


def test_draw_origin_kreise_prefers_close_and_populous():
    # Two candidate Kreise; A is close+populous, B far+small. Expect mostly A.
    dest = {"03101": (0.0, 0.0)}
    counts = pd.DataFrame({"commune_ars5": ["03101"], "in_commuters": [1000]})
    kreis_xy = pd.DataFrame({
        "ars5": ["09999", "16077"],
        "x": [5000.0, 120000.0], "y": [0.0, 0.0]})
    kreis_pop = pd.Series({"09999": 100000.0, "16077": 2000.0})
    rng = np.random.default_rng(42)
    out = so.draw_origin_kreise(dest, counts, kreis_xy, kreis_pop,
                                slope=-0.1415, max_radius_km=150.0, rng=rng)
    assert len(out) == 1000
    assert set(out["dest_commune"]) == {"03101"}
    assert (out["orig_ars5"] == "09999").mean() > 0.95


def test_radius_excludes_far_kreise():
    dest = {"03101": (0.0, 0.0)}
    counts = pd.DataFrame({"commune_ars5": ["03101"], "in_commuters": [200]})
    # Only a >150km Kreis exists -> nearest-fallback still assigns it (logged).
    kreis_xy = pd.DataFrame({"ars5": ["16077"], "x": [200000.0], "y": [0.0]})
    kreis_pop = pd.Series({"16077": 5000.0})
    rng = np.random.default_rng(1)
    out = so.draw_origin_kreise(dest, counts, kreis_xy, kreis_pop,
                                slope=-0.1415, max_radius_km=150.0, rng=rng)
    assert (out["orig_ars5"] == "16077").all()  # nearest fallback
