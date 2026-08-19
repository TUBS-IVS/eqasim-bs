"""Unit tests for the 1km ownership-grid control helpers (issue #240)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import ownership_grid as og


def test_load_ownership_conditionals_validates_and_indexes(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 0.25, "cars_1": 0.25, "cars_2": 0.25,
             "cars_3plus": 0.25, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 0.2, "bikes_1": 0.2, "bikes_2": 0.2,
              "bikes_3": 0.2, "bikes_4plus": 0.2, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    cars, bikes = og.load_ownership_conditionals(str(tmp_path))
    assert cars.loc[(71, 1), "cars_0"] == pytest.approx(0.25)
    assert bikes.loc[(77, 4), "bikes_4plus"] == pytest.approx(0.2)


def test_load_ownership_conditionals_rejects_bad_row_sum(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 0.5, "cars_1": 0.5, "cars_2": 0.5,
             "cars_3plus": 0.5, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    pd.DataFrame(rows).rename(columns={"cars_0": "bikes_0", "cars_1": "bikes_1", "cars_2": "bikes_2",
                                       "cars_3plus": "bikes_3"}).assign(bikes_4plus=0.0).to_csv(
        mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="sum"):
        og.load_ownership_conditionals(str(tmp_path))


def test_load_ownership_conditionals_rejects_incomplete_grid(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 1.0, "cars_1": 0.0, "cars_2": 0.0,
             "cars_3plus": 0.0, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)][:-1]  # drop (77, 4)
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 1.0, "bikes_1": 0.0, "bikes_2": 0.0,
              "bikes_3": 0.0, "bikes_4plus": 0.0, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="incomplete|missing"):
        og.load_ownership_conditionals(str(tmp_path))


def _uniform_conditional(share_columns):
    rows = []
    for r in range(71, 78):
        for h in (1, 2, 3, 4):
            row = {"rs7": r, "ht": h, "n_unweighted": 100 if h == 1 else 50}
            # ht=1 gets all mass on category 0; other ht all mass on the last category.
            for i, c in enumerate(share_columns):
                row[c] = 1.0 if ((h == 1 and i == 0) or (h != 1 and i == len(share_columns) - 1)) else 0.0
            rows.append(row)
    return pd.DataFrame(rows).set_index(["rs7", "ht"]).sort_index()


def test_prior_mixes_by_dwelling_composition():
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    rs7 = np.array([71])
    dwellings = np.array([[3.0, 1.0, 0.0, 0.0]])  # 75% ht=1, 25% ht=2
    prior = og.per_cell_ownership_priors(rs7, dwellings, cond, og._CARS_SHARE_COLUMNS, "cars")
    assert prior[0, 0] == pytest.approx(0.75)
    assert prior[0, -1] == pytest.approx(0.25)


def test_prior_falls_back_to_n_weighted_rs7_marginal_and_logs(caplog):
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    rs7 = np.array([71, 71])
    dwellings = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]])  # second cell: no info
    with caplog.at_level("INFO"):
        prior = og.per_cell_ownership_priors(rs7, dwellings, cond, og._CARS_SHARE_COLUMNS, "cars")
    # n-weighted marginal: ht1 n=100 on cat0, ht2..4 n=50 each on last cat -> 100/250 vs 150/250.
    assert prior[1, 0] == pytest.approx(0.4)
    assert prior[1, -1] == pytest.approx(0.6)
    assert "fallback" in caplog.text.lower()
    assert "1/2" in caplog.text or "50.0" in caplog.text


def test_prior_all_fallback_raises():
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    with pytest.raises(ValueError, match="100"):
        og.per_cell_ownership_priors(np.array([71]), np.array([[0.0, 0.0, 0.0, 0.0]]),
                                     cond, og._CARS_SHARE_COLUMNS, "cars")
