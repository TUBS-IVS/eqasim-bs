"""F4 (repository audit B4): slot thinning for sampled households (sampling_rate < 1).

home_cell builds slots from the FULL census occupied count, but the sampled households
are only a Bernoulli sample at ``sampling_rate``. Without thinning, the few sampled
households of a cell take the first slots of the size-ordered queue, i.e. the slots of
the largest building, which biases the home distribution toward it.
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import home_cell

_CELL = "CRS3035RES100mN2689100E4337000"
_COMMUNE = "031010000000"
# One cell, six footprints of very different area (=> very different slot capacity).
_AREAS = [1200.0, 400.0, 150.0, 90.0, 70.0, 50.0]


def _buildings():
    pts = [Point(4337050 + i, 2689150) for i in range(len(_AREAS))]
    return gpd.GeoDataFrame(
        {"building_id": list(range(len(_AREAS))), "weight": _AREAS, "area_m2": _AREAS,
         "commune_id": [_COMMUNE] * len(_AREAS)},
        geometry=pts, crs="EPSG:3035").to_crs("EPSG:25832")


def _cells(n_dwellings):
    return pd.DataFrame([{
        "ZENSUS100m": _CELL,
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": float(len(_AREAS)),
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": float(n_dwellings),
        "BewohntWhg_Leerstand_100m_Gitter": float(n_dwellings),
        "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": float(n_dwellings),
    }])


def _households(n):
    return pd.DataFrame({
        "household_id": [f"h{i}" for i in range(n)], "commune_id": [_COMMUNE] * n,
        "ZENSUS100m": [_CELL] * n, "building_type_3class": ["ein_zweifamilienhaus"] * n,
        "household_size": [2] * n})


def _largest_share(result):
    return float((result["home_location_id"] == 0).mean())


def test_thinned_sample_reproduces_the_full_distribution_over_buildings():
    n_full, rate = 100, 0.05
    full, _ = home_cell.assign_homes_typed(
        _households(n_full), _buildings(), _cells(n_full), random_seed=1)
    p_full = _largest_share(full)
    assert 0.3 < p_full < 0.95          # precondition: the largest building is not trivial
    n_sample, n_seeds = int(n_full * rate), 60
    shares = []
    for seed in range(n_seeds):
        out, _ = home_cell.assign_homes_typed(
            _households(n_sample), _buildings(), _cells(n_full), random_seed=seed,
            sampling_rate=rate)
        shares.append(_largest_share(out))
    # 4 binomial standard errors of the pooled share, derived from the 100 % share
    bound = 4.0 * np.sqrt(p_full * (1.0 - p_full) / (n_sample * n_seeds))
    assert abs(float(np.mean(shares)) - p_full) < bound

    # Negative control: with the thinning step bypassed the same sample is biased toward
    # the largest building, far outside the bound -- the test can detect the defect.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(home_cell, "_thin_slots_by_type", lambda slots, *args, **kwargs: slots)
        unthinned = [_largest_share(home_cell.assign_homes_typed(
            _households(n_sample), _buildings(), _cells(n_full), random_seed=seed,
            sampling_rate=rate)[0]) for seed in range(n_seeds)]
    assert abs(float(np.mean(unthinned)) - p_full) > bound


def test_thinning_is_per_building_type_and_keeps_type_match():
    """20 efh + 2 mfh slots, 2 mfh + 1 efh sampled households at r = 0.05.

    A type-blind thinning keeps ~3 of the 22 slots at random and almost always loses the
    two mfh slots; per-type thinning keeps every mfh slot the mfh households need."""
    cells = _cells(20)
    cells["MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = 1.0
    cells["FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = float(len(_AREAS) - 1)
    cells["MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = 2.0
    cells["BewohntWhg_Leerstand_100m_Gitter"] = 22.0
    hh = _households(3)
    hh["building_type_3class"] = ["mehrfamilienhaus", "mehrfamilienhaus", "ein_zweifamilienhaus"]
    full, rep_full = home_cell.assign_homes_typed(
        hh, _buildings(), cells, random_seed=1)
    assert rep_full.type_match_rate == 1.0           # reference: all 3 matched at r = 1
    for seed in range(30):
        _, rep = home_cell.assign_homes_typed(
            hh, _buildings(), cells, random_seed=seed, sampling_rate=0.05)
        assert rep.type_match_rate == rep_full.type_match_rate, seed


@pytest.mark.parametrize("batch", [True, False])
def test_sampling_rate_one_is_byte_identical(batch):
    base, _ = home_cell.assign_homes_typed(
        _households(40), _buildings(), _cells(40), random_seed=3, batch_coordinates=batch)
    same, _ = home_cell.assign_homes_typed(
        _households(40), _buildings(), _cells(40), random_seed=3, batch_coordinates=batch,
        sampling_rate=1.0)
    pd.testing.assert_frame_equal(base, same)


@pytest.mark.parametrize("batch", [True, False])
def test_thinning_never_drops_below_household_count_and_both_paths_agree(batch):
    out, rep = home_cell.assign_homes_typed(
        _households(30), _buildings(), _cells(100), random_seed=5, batch_coordinates=batch,
        sampling_rate=0.05)
    assert len(out) == 30 and out["home_location_id"].notna().all()
    assert rep.n_overcapacity == 0      # >= n_households slots are always kept
    other, _ = home_cell.assign_homes_typed(
        _households(30), _buildings(), _cells(100), random_seed=5,
        batch_coordinates=not batch, sampling_rate=0.05)
    assert list(out["home_location_id"]) == list(other["home_location_id"])


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_invalid_sampling_rate_raises(bad):
    with pytest.raises(ValueError, match="sampling_rate"):
        home_cell.assign_homes_typed(
            _households(3), _buildings(), _cells(3), random_seed=1, sampling_rate=bad)
