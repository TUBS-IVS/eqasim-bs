"""Tests for the electric drivetrain euro_class marker (Task 6 / A4).

Asserts:
  * ``hbefa.NON_COMBUSTION_EURO == "na"`` exists and has the right value.
  * Non-combustion powertrains (bev/phev/hybrid/hydrogen) are NOT in
    ``COMBUSTION_POWERTRAINS``; combustion powertrains (petrol/diesel/gas/other)
    ARE in ``COMBUSTION_POWERTRAINS``.
  * A small seeded ``sample_fleet`` run (both consistency_v2=True and =False)
    produces ``euro_class == "na"`` for every non-combustion row and a real
    Euro-N label for every combustion row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

H = pytest.importorskip("braunschweig.synthesis.vehicles.hbefa")


# --------------------------------------------------------------------------- #
# Constant / membership assertions (run on import — no data needed).
# --------------------------------------------------------------------------- #
def test_non_combustion_euro_marker_exists():
    assert H.NON_COMBUSTION_EURO == "na"
    for pt in ("bev", "phev", "hybrid", "hydrogen"):
        assert pt not in H.COMBUSTION_POWERTRAINS
    for pt in ("petrol", "diesel", "gas", "other"):
        assert pt in H.COMBUSTION_POWERTRAINS


# --------------------------------------------------------------------------- #
# Fleet-level assertion: every non-combustion row carries euro_class == "na",
# every combustion row carries a real euroN / "other" label.
# --------------------------------------------------------------------------- #
DATA_PATH = str(DATA)

NON_COMBUSTION_POWERTRAINS = ("bev", "phev", "hybrid", "hydrogen")
COMBUSTION_POWERTRAINS_LOCAL = ("petrol", "diesel", "gas", "other")
VALID_COMBUSTION_EUROS = {"euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"}


def _make_small_cars(n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Return a small synthetic fleet frame covering several Kreise and statuses."""
    from braunschweig.data.kba import fleet_tables as ft

    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    kreise = list(ft.ZGB_KREISE_AGS5)[:4]  # 4 Kreise x n cars
    rows = []
    for kreis in kreise:
        for _ in range(n):
            rows.append({
                "economic_status": rng.choice(statuses),
                "kreis_ags5": kreis,
                "gemeinde": float("nan"),
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def small_sampler():
    """Pre-built FleetSampler shared between the two fleet-level tests."""
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    if not DATA.exists():
        pytest.skip(f"eqasim-data not available at {DATA}")
    return fs.FleetSampler.from_data_path(DATA_PATH)


def _assert_electric_euro_na(df_spec: pd.DataFrame) -> None:
    """Assert that non-combustion rows carry 'na' and combustion rows carry valid euros."""
    non_comb = df_spec[df_spec["powertrain"].isin(NON_COMBUSTION_POWERTRAINS)]
    bad_non_comb = non_comb[non_comb["euro_class"] != "na"]
    assert bad_non_comb.empty, (
        f"{len(bad_non_comb)} non-combustion vehicles have a non-'na' euro_class:\n"
        f"{bad_non_comb[['powertrain', 'euro_class']].value_counts().to_string()}"
    )

    comb = df_spec[df_spec["powertrain"].isin(COMBUSTION_POWERTRAINS_LOCAL)]
    bad_comb = comb[~comb["euro_class"].isin(VALID_COMBUSTION_EUROS)]
    assert bad_comb.empty, (
        f"{len(bad_comb)} combustion vehicles have an invalid euro_class:\n"
        f"{bad_comb[['powertrain', 'euro_class']].value_counts().to_string()}"
    )


def test_non_combustion_euro_na_consistency_v2(small_sampler):
    """consistency_v2=True path: non-combustion rows must carry euro_class == 'na'."""
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    df_cars = _make_small_cars()
    result = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=17, sampler=small_sampler,
        consistency_v2=True, model_brands=True,
    )
    df_spec = result[0]
    _assert_electric_euro_na(df_spec)


def test_non_combustion_euro_na_legacy(small_sampler):
    """consistency_v2=False (legacy) path: non-combustion rows must carry euro_class == 'na'."""
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    df_cars = _make_small_cars()
    result = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=17, sampler=small_sampler,
        consistency_v2=False, model_brands=True,
    )
    # Legacy path returns a 2-tuple.
    df_spec = result[0]
    _assert_electric_euro_na(df_spec)
