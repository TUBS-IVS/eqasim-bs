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
