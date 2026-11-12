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


def test_below_expectation_samples_reproduce_the_full_distribution_over_buildings():
    """Realistic sample sizes: the cell's sample is Bernoulli, so it is often SMALLER than
    round(n_full * rate). Surplus kept slots would let the size-ordered matcher hand the
    few households the largest building again (review of TUBS-IVS/eqasim-bs#605).
    Only the below-expectation samples (1..4 households for an expected 5) are drawn,
    because that is where surplus slots arise."""
    n_full, rate = 100, 0.05
    # Dwelling sizes spread over four bins: the builder hands the largest sizes to the
    # largest building's slots, so the size-ordered matcher would reach for it first.
    cells = _cells(n_full)
    for prefix in ("50bis59", "70bis79", "90bis99", "110bis119"):
        cells[f"{prefix}_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter"] = n_full / 4
    full, _ = home_cell.assign_homes_typed(
        _households(n_full), _buildings(), cells, random_seed=1)
    p_full = _largest_share(full)
    placed, in_largest = 0, 0
    for seed in range(160):
        n_sample = 1 + seed % 4
        out, _ = home_cell.assign_homes_typed(
            _households(n_sample), _buildings(), cells, random_seed=seed,
            sampling_rate=rate)
        placed += len(out)
        in_largest += int((out["home_location_id"] == 0).sum())
    bound = 4.0 * np.sqrt(p_full * (1.0 - p_full) / placed)
    assert abs(in_largest / placed - p_full) < bound


@pytest.mark.parametrize("batch", [True, False])
def test_households_whose_type_has_too_few_slots_still_get_a_building(batch):
    """8 efh slots and no mfh slot, 1 mfh household; and 8 mfh households for 2 mfh +
    20 efh slots. A per-type floor that ignores spill-over keeps 0 resp. 3 slots and
    leaves households without a building (review of TUBS-IVS/eqasim-bs#605)."""
    hh = _households(1)
    hh["building_type_3class"] = ["mehrfamilienhaus"]
    out, rep = home_cell.assign_homes_typed(
        hh, _buildings(), _cells(8), random_seed=2, sampling_rate=0.05,
        batch_coordinates=batch)
    assert out["home_location_id"].notna().all() and rep.n_overcapacity == 0

    cells = _cells(20)
    cells["MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = 1.0
    cells["FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = float(len(_AREAS) - 1)
    cells["MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = 2.0
    cells["BewohntWhg_Leerstand_100m_Gitter"] = 22.0
    hh = _households(8)
    hh["building_type_3class"] = ["mehrfamilienhaus"] * 8
    out, rep = home_cell.assign_homes_typed(
        hh, _buildings(), cells, random_seed=2, sampling_rate=0.05, batch_coordinates=batch)
    assert out["home_location_id"].notna().all() and rep.n_overcapacity == 0
    full, rep_full = home_cell.assign_homes_typed(hh, _buildings(), cells, random_seed=2)
    assert rep.type_match_rate == rep_full.type_match_rate


def test_sampled_households_meet_the_type_contention_of_the_full_cell():
    """2 mfh dwellings for 10 mfh-type households (plus 12 efh households, 20 efh
    dwellings): at 100 % only 2 of the 10 get the mfh building. A sampled mfh household
    must get it equally rarely, not always (review of TUBS-IVS/eqasim-bs#605)."""
    cells = _cells(20)
    cells["MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = 1.0
    cells["FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = float(len(_AREAS) - 1)
    cells["MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = 2.0
    cells["BewohntWhg_Leerstand_100m_Gitter"] = 22.0
    full_hh = _households(22)
    full_hh["building_type_3class"] = ["mehrfamilienhaus"] * 10 + ["ein_zweifamilienhaus"] * 12
    full, _ = home_cell.assign_homes_typed(full_hh, _buildings(), cells, random_seed=1)
    # The largest footprint is the one typed mfh; share of the mfh households living there.
    p_full = float((full["home_location_id"].iloc[:10] == 0).mean())
    assert 0.0 < p_full < 0.5
    by_cell_type = home_cell.full_households_per_cell_type(full_hh)
    assert by_cell_type == {_CELL: {"mfh": 10.0, "efh_zfh": 12.0}}

    one = _households(1)
    one["building_type_3class"] = ["mehrfamilienhaus"]
    n_seeds = 200
    hits = sum(
        int(home_cell.assign_homes_typed(
            one, _buildings(), cells, random_seed=seed, sampling_rate=0.05,
            full_households_by_cell_type=by_cell_type)[0]["home_location_id"].iloc[0] == 0)
        for seed in range(n_seeds))
    bound = 4.0 * np.sqrt(p_full * (1.0 - p_full) / n_seeds)
    assert abs(hits / n_seeds - p_full) < bound
    # Negative control: without the full cell mix (the fallback) the sampled mfh
    # household meets no contention and always gets the mfh building.
    uncontested = [
        home_cell.assign_homes_typed(
            one, _buildings(), cells, random_seed=seed,
            sampling_rate=0.05)[0]["home_location_id"].iloc[0] == 0
        for seed in range(10)]
    assert all(uncontested)


def test_cells_missing_from_the_full_population_table_are_counted(caplog):
    """Fallback transparency: without the full cell mix the sampled mix is used, loudly."""
    with caplog.at_level("INFO", logger=home_cell.logger.name):
        home_cell.assign_homes_typed(
            _households(3), _buildings(), _cells(40), random_seed=1, sampling_rate=0.05,
            full_households_by_cell_type={})
    record = next(r for r in caplog.records if "type routing" in r.msg)
    assert record.levelname == "WARNING" and record.args[3] == 3   # 3 households on fallback
    caplog.clear()
    with caplog.at_level("INFO", logger=home_cell.logger.name):
        home_cell.assign_homes_typed(
            _households(3), _buildings(), _cells(40), random_seed=1, sampling_rate=0.05,
            full_households_by_cell_type={_CELL: {"efh_zfh": 40.0}})
    record = next(r for r in caplog.records if "type routing" in r.msg)
    assert record.levelname == "INFO" and record.args[3] == 0      # primary path taken


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
