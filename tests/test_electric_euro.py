"""Tests for the electric drivetrain euro_class category (A4-revised).

Asserts:
  * ``hbefa.ELECTRIC_EURO == "electric"`` and
    ``hbefa.ELECTRIC_EURO_POWERTRAINS == {"bev", "hydrogen"}``.
  * Non-combustion powertrains (bev/phev/hybrid/hydrogen) are NOT in
    ``COMBUSTION_POWERTRAINS``; combustion powertrains (petrol/diesel/gas/other)
    ARE in ``COMBUSTION_POWERTRAINS``.
  * On the consistency_v2=True path:
    - every BEV / hydrogen row has ``euro_class == "electric"`` (pure-electric
      category, no combustion Euro stage);
    - every PHEV / hybrid row has a REAL combustion Euro (euro1..euro6/other,
      NOT "electric" or "na" -- these powertrains have a combustion engine);
    - every combustion (petrol/diesel/gas/other) row has a real Euro too.
  * On the legacy (consistency_v2=False) path, the output is byte-identical to
    the pre-feature frozen path: electric rows carry a DRAWN euro (whatever the
    joint yielded), NOT "electric" or "na" -- the legacy path has no override.
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
# Constant / membership assertions (run on import -- no data needed).
# --------------------------------------------------------------------------- #
def test_electric_euro_constants():
    """ELECTRIC_EURO is "electric" and ELECTRIC_EURO_POWERTRAINS is {bev, hydrogen}."""
    assert H.ELECTRIC_EURO == "electric"
    assert H.ELECTRIC_EURO_POWERTRAINS == frozenset({"bev", "hydrogen"})


def test_combustion_powertrain_membership():
    """PHEV and hybrid are NOT in COMBUSTION_POWERTRAINS; petrol/diesel/gas/other are."""
    for pt in ("bev", "phev", "hybrid", "hydrogen"):
        assert pt not in H.COMBUSTION_POWERTRAINS, (
            f"Expected '{pt}' NOT in COMBUSTION_POWERTRAINS"
        )
    for pt in ("petrol", "diesel", "gas", "other"):
        assert pt in H.COMBUSTION_POWERTRAINS, (
            f"Expected '{pt}' IN COMBUSTION_POWERTRAINS"
        )


# --------------------------------------------------------------------------- #
# Fleet-level assertions: euro_class category assignment per powertrain group.
# --------------------------------------------------------------------------- #
DATA_PATH = str(DATA)

PURE_ELECTRIC_POWERTRAINS = ("bev", "hydrogen")
HYBRID_POWERTRAINS = ("phev", "hybrid")  # have combustion engine -> real Euro
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
    """Pre-built FleetSampler shared between the fleet-level tests."""
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    if not DATA.exists():
        pytest.skip(f"eqasim-data not available at {DATA}")
    return fs.FleetSampler.from_data_path(DATA_PATH)


def _assert_electric_euro_categories(df_spec: pd.DataFrame) -> None:
    """Assert correct euro_class categories for all powertrain groups.

    * Pure-electric (bev/hydrogen): must have euro_class == "electric".
    * Hybrid (phev/hybrid): must have a real combustion Euro (NOT "electric"/"na").
    * Combustion (petrol/diesel/gas/other): must have a real combustion Euro.
    """
    # Pure-electric: must carry the "electric" category.
    pure_elec = df_spec[df_spec["powertrain"].isin(PURE_ELECTRIC_POWERTRAINS)]
    bad_elec = pure_elec[pure_elec["euro_class"] != "electric"]
    assert bad_elec.empty, (
        f"{len(bad_elec)} BEV/hydrogen vehicles do NOT have euro_class == 'electric':\n"
        f"{bad_elec[['powertrain', 'euro_class']].value_counts().to_string()}"
    )

    # PHEV/hybrid: must carry a REAL combustion Euro (not "electric" or "na").
    hybrid = df_spec[df_spec["powertrain"].isin(HYBRID_POWERTRAINS)]
    bad_hybrid = hybrid[~hybrid["euro_class"].isin(VALID_COMBUSTION_EUROS)]
    assert bad_hybrid.empty, (
        f"{len(bad_hybrid)} PHEV/hybrid vehicles have a non-combustion euro_class "
        f"(they should keep their drawn combustion Euro):\n"
        f"{bad_hybrid[['powertrain', 'euro_class']].value_counts().to_string()}"
    )

    # Combustion: must carry a valid combustion Euro.
    comb = df_spec[df_spec["powertrain"].isin(COMBUSTION_POWERTRAINS_LOCAL)]
    bad_comb = comb[~comb["euro_class"].isin(VALID_COMBUSTION_EUROS)]
    assert bad_comb.empty, (
        f"{len(bad_comb)} combustion vehicles have an invalid euro_class:\n"
        f"{bad_comb[['powertrain', 'euro_class']].value_counts().to_string()}"
    )


def test_electric_euro_consistency_v2(small_sampler):
    """consistency_v2=True: bev/hydrogen get 'electric', phev/hybrid/comb get real Euro."""
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    df_cars = _make_small_cars()
    result = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=17, sampler=small_sampler,
        consistency_v2=True, model_brands=True,
    )
    df_spec = result[0]
    _assert_electric_euro_categories(df_spec)


def test_legacy_path_no_electric_override(small_sampler):
    """consistency_v2=False (legacy) path: no "electric"/"na" override at all.

    The legacy path is byte-identical to the pre-feature frozen output and must
    NOT apply any euro override for electric rows.  All rows (including bev /
    hydrogen) keep their drawn combustion euro from the KBA joint.
    """
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    df_cars = _make_small_cars()
    result = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=17, sampler=small_sampler,
        consistency_v2=False, model_brands=True,
    )
    # Legacy path returns a 2-tuple.
    df_spec = result[0]

    # No row should carry "electric" or "na" -- those are consistency_v2-only.
    bad_electric = df_spec[df_spec["euro_class"] == "electric"]
    assert bad_electric.empty, (
        f"Legacy path must not produce euro_class='electric'; "
        f"found {len(bad_electric)} rows:\n"
        f"{bad_electric[['powertrain', 'euro_class']].value_counts().to_string()}"
    )
    bad_na = df_spec[df_spec["euro_class"] == "na"]
    assert bad_na.empty, (
        f"Legacy path must not produce euro_class='na'; "
        f"found {len(bad_na)} rows"
    )

    # All rows must carry a real combustion Euro (the drawn value from the joint).
    bad_euro = df_spec[~df_spec["euro_class"].isin(VALID_COMBUSTION_EUROS)]
    assert bad_euro.empty, (
        f"Legacy path: {len(bad_euro)} rows have an unexpected euro_class value:\n"
        f"{bad_euro[['powertrain', 'euro_class']].value_counts().to_string()}"
    )
