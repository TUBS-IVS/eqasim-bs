"""OFF-equivalence guard for the A-REORDER refactor in
``braunschweig.synthesis.population.enriched``.

The A-REORDER moves the MiD-H7 / H12.3 vehicle-count sampling
(:func:`_sample_vehicle_counts`, RNG offset +91731) from the OUTER ``execute``
(historically AFTER the car/bike availability binarisation) to INSIDE
``_execute_base`` (BEFORE the binarisation), so the A5 consistent-car_availability
feature can condition on the household car count and the licence.

This re-order is only valid if it is OUTPUT-IDENTICAL with all new flags OFF: the
vehicle-count stream (+91731) and the binarisation stream (+23761) are
independent, so swapping their call order must not change ANY of the four columns
they produce (number_of_cars, number_of_bicycles, car_availability,
bicycle_availability). The seeded synthesis is not re-baselined, so any value /
RNG change would invalidate cached results.

These tests pin that equivalence on a synthetic population by running BOTH call
orders and asserting byte-identical output for every produced column.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _sample_vehicle_counts,
    _binarise_availability,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

# Two in-scope Kreis ARS-5 codes + their inside_<kreis> flags so
# _derive_kreis_ars5 maps the synthetic persons to a Kreis present in the H7 /
# H12.3 share CSVs written below.
_KREIS_A = "03101"  # braunschweig
_KREIS_B = "03151"  # gifhorn


def _write_share_csvs(tmp_path: Path) -> str:
    """Write minimal H7 / H12.3 Kreis-share CSVs under <data_path>/braunschweig/mid."""
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True, exist_ok=True)

    # Cars: 4 buckets (0,1,2,3). Region row "Gesamt" is the fallback.
    cars = pd.DataFrame(
        {
            "ars5": ["Gesamt", _KREIS_A, _KREIS_B],
            "0": [0.10, 0.20, 0.05],
            "1": [0.50, 0.45, 0.40],
            "2": [0.30, 0.25, 0.40],
            "3": [0.10, 0.10, 0.15],
        }
    )
    cars.to_csv(mid_dir / "mid2023_H7_cars_by_kreis.csv", index=False)

    bikes = pd.DataFrame(
        {
            "ars5": ["Gesamt", _KREIS_A, _KREIS_B],
            "0": [0.15, 0.10, 0.20],
            "1": [0.40, 0.45, 0.35],
            "2": [0.30, 0.30, 0.30],
            "3": [0.15, 0.15, 0.15],
        }
    )
    bikes.to_csv(mid_dir / "mid2023_H12_3_bikes_by_kreis.csv", index=False)
    return str(tmp_path)


def _make_persons(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Build a synthetic enriched-stage frame at the point just before the
    vehicle-count / availability blocks (fractional IPF availability weights,
    inside_<kreis> flags, ages, sexes, licence, household_id)."""
    rng = np.random.RandomState(seed)
    inside_a = rng.random_sample(n) < 0.5
    df = pd.DataFrame(
        {
            "household_id": rng.randint(0, n // 2, size=n),
            "age": rng.randint(0, 90, size=n),
            "sex": np.where(rng.random_sample(n) < 0.5, "male", "female"),
            "has_license": rng.random_sample(n) < 0.7,
            # Fractional IPF availability weights in [0, 1] (P19 / P22 output).
            "car_availability": rng.random_sample(n),
            "bicycle_availability": rng.random_sample(n),
            "inside_braunschweig": inside_a,
            "inside_gifhorn": ~inside_a,
        }
    )
    return df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_reorder_off_equivalence_all_columns(tmp_path):
    """Swapping the vehicle-count / binarisation call order leaves all four
    produced columns byte-identical (A5 OFF path)."""
    data_path = _write_share_csvs(tmp_path)
    seed = 1234

    # NEW order (re-order): vehicle counts first, then binarisation.
    df_new = _make_persons()
    _sample_vehicle_counts(df_new, data_path, seed)
    _binarise_availability(df_new, seed, apply_car=True)

    # LEGACY order: binarisation first, then vehicle counts (the historical
    # outer-execute ordering).
    df_old = _make_persons()
    _binarise_availability(df_old, seed, apply_car=True)
    _sample_vehicle_counts(df_old, data_path, seed)

    for col in [
        "number_of_cars", "number_of_bicycles",
        "car_availability", "bicycle_availability",
    ]:
        pd.testing.assert_series_equal(
            df_old[col].reset_index(drop=True),
            df_new[col].reset_index(drop=True),
            check_names=True,
            obj=f"{col} (legacy order vs re-order)",
        )


def test_vehicle_counts_independent_of_binarisation_state(tmp_path):
    """The +91731 vehicle-count stream is independent of the +23761 binarisation
    stream: number_of_cars / number_of_bicycles are identical whether or not the
    binarisation ran first (proves no shared RNG state)."""
    data_path = _write_share_csvs(tmp_path)
    seed = 99

    df_a = _make_persons()
    _sample_vehicle_counts(df_a, data_path, seed)

    df_b = _make_persons()
    _binarise_availability(df_b, seed, apply_car=True)
    _sample_vehicle_counts(df_b, data_path, seed)

    np.testing.assert_array_equal(
        df_a["number_of_cars"].to_numpy(), df_b["number_of_cars"].to_numpy()
    )
    np.testing.assert_array_equal(
        df_a["number_of_bicycles"].to_numpy(), df_b["number_of_bicycles"].to_numpy()
    )


def test_bicycle_binarisation_identical_with_and_without_car_apply(tmp_path):
    """With A5 ON the car Bernoulli result is discarded (apply_car=False) but the
    car uniform is still consumed first, so the BICYCLE binarisation is
    byte-identical to the legacy apply_car=True path. This is what keeps the OFF
    bicycle_availability unchanged when A5 is enabled."""
    seed = 555

    df_on = _make_persons()
    _binarise_availability(df_on, seed, apply_car=False)

    df_off = _make_persons()
    _binarise_availability(df_off, seed, apply_car=True)

    pd.testing.assert_series_equal(
        df_off["bicycle_availability"].reset_index(drop=True),
        df_on["bicycle_availability"].reset_index(drop=True),
        check_names=True,
        obj="bicycle_availability (apply_car True vs False)",
    )
    # And with apply_car=False the car column is left as the fractional weights
    # (not binarised), confirming the car result was not written.
    assert df_on["car_availability"].dtype != "category"
