"""Seed fills resolved ONCE per donor before PopulationSim (issue #647, WP3).

MiD sex codes 3 (diverse) and 9 (no entry), tenure codes other than 1 / 2 and building type
95 count in no PopulationSim control. The stage resolves them once per donor person or
household (the ADR-0192 pattern), so the seed and every expanded clone carry the same value.
Tiny synthetic data only.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import assembly, attributes, expand, missing, sources
from braunschweig.popsim import stage as popsim_stage


def _persons(sex, band):
    n = len(sex)
    return pd.DataFrame({
        "H_ID": [f"h{i}" for i in range(n)], "P_ID": 1, "HP_ALTER": 40,
        "HP_SEX": sex, "alter_gr1": band,
    })


def test_resolve_binary_sex_draws_from_band_pool():
    # Band 1 holds only valid women, band 2 only valid men: a band draw is deterministic.
    sex = [2, 2, 2, 3, 9, 1, 1, 1, 3]
    band = [1, 1, 1, 1, 1, 2, 2, 2, 2]
    out = attributes.resolve_binary_sex(_persons(sex, band), rng=np.random.RandomState(0))
    assert out["HP_SEX"].tolist() == [2, 2, 2, 2, 2, 1, 1, 1, 1]
    assert out["HP_SEX_raw"].tolist() == sex
    # Explicit dtype: a platform int (int32 on Windows) would differ between CI platforms.
    assert out["HP_SEX"].dtype == np.dtype("int64")


def test_resolve_binary_sex_without_band_uses_global_pool(caplog):
    # Band 3 and a NaN band have no valid person: they draw from the global pool (all male).
    sex = [1, 1, 1, 3, 9]
    band = [1, 1, 1, 3, np.nan]
    with caplog.at_level(logging.INFO):
        out = attributes.resolve_binary_sex(_persons(sex, band), rng=np.random.RandomState(0))
    assert out["HP_SEX"].tolist() == [1, 1, 1, 1, 1]
    assert out["HP_SEX_raw"].tolist() == sex
    assert any("popsim.seed.sex" in r.getMessage() for r in caplog.records)


def test_resolve_binary_sex_is_seeded():
    rng_data = np.random.RandomState(3)
    sex = list(rng_data.choice([1, 2], size=60)) + [3] * 40
    band = [1] * 100
    frame = _persons(sex, band)
    a = attributes.resolve_binary_sex(frame, rng=np.random.RandomState(11))
    b = attributes.resolve_binary_sex(frame, rng=np.random.RandomState(11))
    c = attributes.resolve_binary_sex(frame, rng=np.random.RandomState(12))
    assert a["HP_SEX"].tolist() == b["HP_SEX"].tolist()
    assert a["HP_SEX"].tolist() != c["HP_SEX"].tolist()
    assert set(a["HP_SEX"]) <= {1, 2}


def test_resolve_binary_sex_no_valid_person_raises():
    with pytest.raises(ValueError, match="popsim.seed.sex"):
        attributes.resolve_binary_sex(_persons([3, 9], [1, 1]), rng=np.random.RandomState(0))


def test_resolve_binary_sex_is_idempotent(caplog):
    once = attributes.resolve_binary_sex(
        _persons([1, 2, 3], [1, 1, 1]), rng=np.random.RandomState(0))
    with caplog.at_level(logging.INFO):
        twice = attributes.resolve_binary_sex(once, rng=np.random.RandomState(99))
    pd.testing.assert_frame_equal(once, twice)
    # The early return is visible in the log, not silent.
    assert any("popsim.seed.sex" in r.getMessage() and "already" in r.getMessage()
               for r in caplog.records)


class _NoDrawRng:
    def randint(self, *args, **kwargs):  # pragma: no cover - failing is the point
        raise AssertionError("map_demographics drew again on a resolved person frame")


def test_map_demographics_after_resolution_imputes_nothing():
    # Donor h0 (raw code 3) is placed twice; both clones must carry the SAME resolved sex.
    persons = attributes.resolve_binary_sex(
        _persons([2, 2, 3, 9], [1, 1, 1, 1]), rng=np.random.RandomState(5))
    merged = pd.DataFrame({
        "ZENSUS100m": ["A", "A", "B"], "ZENSUS1km": ["KA", "KA", "KB"],
        "H_ID": ["h2", "h2", "h3"],
    })
    clones = expand.expand_to_persons(expand.assign_synthetic_household_ids(merged), persons)
    out = expand.map_demographics(clones, rng=_NoDrawRng())
    resolved = persons.set_index("H_ID")["HP_SEX"].map({1: "male", 2: "female"})
    assert out["sex"].tolist() == [resolved["h2"], resolved["h2"], resolved["h3"]]
    assert out["sex_raw"].tolist() == ["diverse", "diverse", "not_specified"]


class _FakeContext:
    def __init__(self, donor):
        self._donor = donor
        self.info = {}

    def stage(self, name):
        assert name == "completed_donor"
        return self._donor

    def set_info(self, key, value):
        self.info[key] = value


def _donor(n=40):
    cols = sources.get_source("mid").seed_columns()
    ids = [f"h{i}" for i in range(n)]
    households = pd.DataFrame({
        cols.household_id: ids, cols.household_weight: 1.0, "H_GR": 1, "H_MIETE": 1,
        "haustyp": 1, "RegioStaR7": 73, "hhgr_gr": 1, "oek_status": 3, "hheink_gr1": 4,
        "H_ANZAUTO": 1, "H_ANZRAD": 1, "anzpedrad": 1, "H_ANZPED": 0,
    })
    sex = [2 if i % 2 else 1 for i in range(n)]
    sex[0], sex[1] = 3, 9
    persons = pd.DataFrame({
        cols.person_household_id: ids, cols.person_id: 1, cols.person_weight: 1.0,
        cols.age: 40, cols.sex: sex, "anzwege1": 2, "alter_gr1": 5,
        "P_TAET": 1, "P_FSCHEIN": 1, "P_FKARTE": 3, "P_BKAT": 1,
    })
    return SimpleNamespace(
        households=households, persons=persons,
        completeness_report=SimpleNamespace(completeness_rate=1.0),
        completion_report=SimpleNamespace(n_households_filled=0, n_persons_added=0),
    ), cols


def _build(donor, **kwargs):
    return popsim_stage._build_populationsim_seed(
        _FakeContext(donor), sources.get_source("mid"), "mid", None, True, None,
        [], np.random.RandomState(7), "H_ANZPED", **kwargs)


def test_sex_key_on_gives_seed_and_donor_the_same_binary_sex():
    donor, cols = _donor()
    don_hh, don_p, seed_hh, seed_p = _build(
        donor, resolve_binary_sex=True, seed_fill_rng=np.random.RandomState(1))
    assert set(seed_p[cols.sex]) <= {1, 2}
    assert seed_p["HP_SEX_raw"].iloc[:2].tolist() == [3, 9]
    merged = seed_p.merge(don_p, on=[cols.person_household_id], suffixes=("_s", "_d"))
    assert (merged[f"{cols.sex}_s"] == merged[f"{cols.sex}_d"]).all()


def test_sex_key_off_is_byte_identical_and_silent(caplog):
    donor, cols = _donor()
    fill_rng = np.random.RandomState(1)
    state_before = fill_rng.get_state()[1].copy()
    with caplog.at_level(logging.DEBUG):
        _, don_p, _, seed_p = _build(donor, resolve_binary_sex=False, seed_fill_rng=fill_rng)
    # Output unchanged: the raw codes survive and no *_raw column appears.
    assert seed_p[cols.sex].iloc[:2].tolist() == [3, 9]
    assert "HP_SEX_raw" not in seed_p.columns and "HP_SEX_raw" not in don_p.columns
    # No counter moves: the dedicated stream is untouched.
    assert (fill_rng.get_state()[1] == state_before).all()
    # No log line (any level) mentions the feature.
    assert not [r for r in caplog.records if "popsim.seed.sex" in r.getMessage()]


@pytest.mark.parametrize("key", ["resolve_binary_sex", "resolve_tenure_building_type"])
def test_fill_key_on_without_fill_rng_raises(key):
    donor, _ = _donor()
    with pytest.raises(ValueError, match="seed_fill_rng"):
        _build(donor, **{key: True})


def test_configure_declares_the_seed_fill_keys_default_on():
    declared = {}

    class _Recorder:
        def config(self, key, default=None, **kwargs):
            declared.setdefault(key, default)
            return default

        def stage(self, *args, **kwargs):
            return None

    popsim_stage.configure(_Recorder())
    assert declared[popsim_stage.KEY_SEED_RESOLVE_BINARY_SEX] is True
    assert declared[popsim_stage.KEY_SEED_RESOLVE_TENURE_BUILDING_TYPE] is True


def test_resolve_binary_sex_gives_each_row_its_own_draw_on_a_duplicated_index():
    # One band of 4 observed men and 4 observed women; 40 unresolved rows. The index repeats
    # every label twice (a concatenated donor table). Label-based assignment would hand
    # both rows of a label the same list of draws or raise; positional assignment resolves
    # every row from its own draw, identically to the same frame with a unique index.
    sex = [1, 1, 1, 1, 2, 2, 2, 2] + [3] * 40
    unique = _persons(sex, [1] * len(sex))
    dup = unique.copy()
    dup.index = [i % 24 for i in range(len(dup))]
    out_unique = attributes.resolve_binary_sex(unique, rng=np.random.RandomState(4))
    out_dup = attributes.resolve_binary_sex(dup, rng=np.random.RandomState(4))
    assert out_dup.index.tolist() == dup.index.tolist()
    assert set(out_dup["HP_SEX"]) <= {1, 2}
    assert out_dup["HP_SEX"].tolist() == out_unique["HP_SEX"].tolist()
    assert out_dup["HP_SEX_raw"].tolist() == sex


# --------------------------------------------------------------------------- #
# Tenure (H_MIETE) and building type (haustyp), resolved once per donor household.
# --------------------------------------------------------------------------- #


def _deck_frame(index=None):
    # Cell "x" holds one donor (value 5); cell "z" one donor (value 7). Missing rows: one in
    # cell "x" (same-cell pool), one in cell "y" (no donor there), one with a NaN cell.
    frame = pd.DataFrame({
        "g": ["x", "z", "x", "y", np.nan],
        "v": [5, 7, 0, 0, 0],
        "w": [1.0, 1.0, 1.0, 1.0, 1.0],
    })
    if index is not None:
        frame.index = index
    return frame, pd.Series([False, False, True, True, True], index=frame.index)


def test_weighted_hot_deck_follows_weights():
    frame = pd.DataFrame({"g": "x", "v": [1, 2] + [0] * 2000, "w": [9.0, 1.0] + [1.0] * 2000})
    mask = pd.Series([False, False] + [True] * 2000)
    filled, counts = missing.weighted_hot_deck(
        frame, value_col="v", missing_mask=mask, weight_col="w", tiers=[("g",)],
        rng=np.random.RandomState(0), name="test")
    share_one = (filled[mask] == 1).mean()
    assert 0.87 <= share_one <= 0.93
    assert counts == {"g": 2000}


@pytest.mark.parametrize("index", [None, [0, 1, 0, 1, 0]], ids=["unique", "duplicated"])
def test_weighted_hot_deck_tier_fallback_counted(index):
    frame, mask = _deck_frame(index)
    filled, counts = missing.weighted_hot_deck(
        frame, value_col="v", missing_mask=mask, weight_col="w", tiers=[("g",), ()],
        rng=np.random.RandomState(0), name="test")
    assert filled.index.tolist() == frame.index.tolist()
    values = filled.tolist()
    assert values[:3] == [5, 7, 5]               # donors untouched; same-cell draw (primary)
    assert values[3] in {5, 7} and values[4] in {5, 7}
    # Cell "y" has no donor and a NaN cell cannot use the tier: both fall to "all".
    assert counts == {"g": 1, "all": 2}


def test_weighted_hot_deck_zero_weight_pool_falls_back():
    # Cell "x" donors carry weight 0 and NaN: no positive pool there, so the global tier.
    frame = pd.DataFrame({"g": ["x", "x", "z", "x"], "v": [5, 6, 7, 0],
                          "w": [0.0, np.nan, 1.0, 1.0]})
    mask = pd.Series([False, False, False, True])
    filled, counts = missing.weighted_hot_deck(
        frame, value_col="v", missing_mask=mask, weight_col="w", tiers=[("g",), ()],
        rng=np.random.RandomState(0), name="test")
    assert filled.iloc[3] == 7
    assert counts == {"g": 0, "all": 1}


def test_weighted_hot_deck_unfillable_raises():
    frame = pd.DataFrame({"g": ["x", "x"], "v": [5, 0], "w": [0.0, 1.0]})
    with pytest.raises(ValueError, match="deck_name"):
        missing.weighted_hot_deck(
            frame, value_col="v", missing_mask=pd.Series([False, True]), weight_col="w",
            tiers=[("g",), ()], rng=np.random.RandomState(0), name="deck_name")


def _housing(miete, haustyp, rs7=None):
    n = len(miete)
    households = pd.DataFrame({
        "H_ID": [f"h{i}" for i in range(n)], "H_GEW": 1.0, "H_MIETE": miete,
        "haustyp": haustyp, "RegioStaR7": rs7 if rs7 is not None else [71] * n, "hhgr_gr": 1,
    })
    persons = pd.DataFrame({"H_ID": households["H_ID"], "HP_ALTER": 40})
    return households, persons


def _resolve(households, persons, rng):
    out = attributes.resolve_tenure_and_building_type(households, persons, rng=rng)
    return attributes.map_building_type_3class(attributes.map_housing_tenure(out))


@pytest.fixture
def deck_counts(monkeypatch):
    """Record the per-tier counts every weighted_hot_deck call returns, keyed by its name."""
    recorded = {}
    real = missing.weighted_hot_deck

    def spy(*args, **kwargs):
        filled, counts = real(*args, **kwargs)
        recorded[kwargs["name"]] = counts
        return filled, counts

    monkeypatch.setattr(missing, "weighted_hot_deck", spy)
    return recorded


_FIRST_TIER = "RegioStaR7+hhgr_gr+hh_type5"


def test_tenure_other_and_non_private_recoded_to_non_owner():
    households, persons = _housing([1, 2, 3, 309], [1, 1, 1, 1])
    rng = np.random.RandomState(0)
    state_before = rng.get_state()[1].copy()
    out = _resolve(households, persons, rng)
    assert out["H_MIETE"].tolist() == [1, 2, 1, 1]
    assert out["H_MIETE_raw"].tolist() == [1, 2, 3, 309]
    assert out["housing_tenure"].tolist() == ["renter", "owner", "renter", "renter"]
    assert out["housing_tenure_imputed"].tolist() == [False, False, True, True]
    assert (rng.get_state()[1] == state_before).all()   # re-coded, not drawn
    assert "hh_type5" not in out.columns


def test_tenure_no_answer_drawn(caplog, deck_counts):
    # The no-answer household sits in RegioStaR7 71, whose donors are all owners; the
    # RegioStaR7 77 donors are all renters. A same-cell (primary) draw gives an owner.
    households, persons = _housing([2, 2, 1, 1, 9], [1] * 5, rs7=[71, 71, 77, 77, 71])
    with caplog.at_level(logging.INFO):
        out = _resolve(households, persons, np.random.RandomState(0))
    # The draw used the full cell (a broken hh_type5 join would push it to a coarser tier).
    assert deck_counts["popsim.seed.tenure"][_FIRST_TIER] == 1
    assert sum(deck_counts["popsim.seed.tenure"].values()) == 1
    assert out["H_MIETE"].dtype == "int64" and out["haustyp"].dtype == "int64"
    assert out["H_MIETE"].tolist() == [2, 2, 1, 1, 2]
    assert out["H_MIETE_raw"].iloc[4] == 9
    assert out["housing_tenure"].iloc[4] == "owner"
    assert out["housing_tenure_imputed"].tolist() == [False] * 4 + [True]
    assert any("popsim.seed.tenure" in r.getMessage() for r in caplog.records)


def test_building_type_95_drawn_and_flagged(deck_counts):
    households, persons = _housing([1] * 5, [3, 3, 1, 1, 95], rs7=[71, 71, 77, 77, 71])
    out = _resolve(households, persons, np.random.RandomState(0))
    assert deck_counts["popsim.seed.building_type"][_FIRST_TIER] == 1
    assert sum(deck_counts["popsim.seed.building_type"].values()) == 1
    assert out["haustyp"].tolist() == [3, 3, 1, 1, 3]     # raw detail kept (3, not 2)
    assert out["haustyp_raw"].tolist() == [3, 3, 1, 1, 95]
    assert out["building_type_3class"].notna().all()
    assert out["building_type_3class"].iloc[4] == "mehrfamilienhaus"
    assert out["building_type_imputed"].tolist() == [False] * 4 + [True]


def test_valid_codes_untouched_flags_false():
    households, persons = _housing([1, 2, 1, 2], [1, 2, 3, 4])
    rng = np.random.RandomState(0)
    state_before = rng.get_state()[1].copy()
    out = _resolve(households, persons, rng)
    assert out["H_MIETE"].tolist() == [1, 2, 1, 2]
    assert out["haustyp"].tolist() == [1, 2, 3, 4]
    assert not out["housing_tenure_imputed"].any() and not out["building_type_imputed"].any()
    assert (rng.get_state()[1] == state_before).all()


@pytest.mark.parametrize("miete, haustyp", [([1, 5], [1, 1]), ([1, 1], [1, 7]),
                                            ([1, np.nan], [1, 1])])
def test_unenumerated_code_raises(miete, haustyp):
    households, persons = _housing(miete, haustyp)
    with pytest.raises(ValueError, match="unenumerated"):
        attributes.resolve_tenure_and_building_type(
            households, persons, rng=np.random.RandomState(0))


def _housing_donor():
    donor, cols = _donor()
    households = donor.households.copy()
    households["H_MIETE"] = [9, 3, 309] + [1 if i % 2 else 2 for i in range(3, len(households))]
    households["haustyp"] = [95, 1] + [1 + i % 4 for i in range(2, len(households))]
    donor.households = households
    return donor, cols


def test_tenure_key_on_gives_seed_and_donor_the_same_values():
    donor, cols = _housing_donor()
    don_hh, _, seed_hh, _ = _build(
        donor, resolve_binary_sex=True, resolve_tenure_building_type=True,
        seed_fill_rng=np.random.RandomState(1))
    assert set(seed_hh["H_MIETE"]) <= {1, 2} and set(seed_hh["haustyp"]) <= {1, 2, 3, 4}
    assert seed_hh["H_MIETE_raw"].iloc[:3].tolist() == [9, 3, 309]
    assert seed_hh["haustyp_raw"].iloc[0] == 95
    merged = seed_hh.merge(don_hh, on=cols.household_id, suffixes=("_s", "_d"))
    assert (merged["H_MIETE_s"] == merged["H_MIETE_d"]).all()
    assert (merged["haustyp_s"] == merged["haustyp_d"]).all()
    assert "hh_type5" not in don_hh.columns


def test_tenure_key_off_is_byte_identical_and_silent(caplog):
    donor, cols = _housing_donor()
    fill_rng = np.random.RandomState(1)
    state_before = fill_rng.get_state()[1].copy()
    with caplog.at_level(logging.DEBUG):
        don_hh, _, seed_hh, _ = _build(
            donor, resolve_binary_sex=False, resolve_tenure_building_type=False,
            seed_fill_rng=fill_rng)
    # Output unchanged: the raw codes survive and no *_raw column appears.
    assert seed_hh["H_MIETE"].iloc[:3].tolist() == [9, 3, 309]
    assert seed_hh["haustyp"].iloc[0] == 95
    for col in ("H_MIETE_raw", "haustyp_raw"):
        assert col not in seed_hh.columns and col not in don_hh.columns
    # No counter moves: the dedicated stream is untouched.
    assert (fill_rng.get_state()[1] == state_before).all()
    # No log line (any level) mentions the feature.
    assert not [r for r in caplog.records
                if "popsim.seed.tenure" in r.getMessage()
                or "popsim.seed.building_type" in r.getMessage()]


def test_clone_inherits_resolved_tenure_and_building_type():
    households, persons = _housing([9, 2], [95, 2])
    households = households.assign(oek_status=3, hheink_gr1=4, H_ANZAUTO=1, H_ANZRAD=1,
                                   anzpedrad=1, H_ANZPED=0)
    resolved = attributes.resolve_tenure_and_building_type(
        households, persons, rng=np.random.RandomState(0))
    mid_persons = persons.assign(P_ID=1, HP_SEX=1, P_TAET=1, P_FSCHEIN=1, P_FKARTE=3, P_BKAT=1)
    merged = pd.DataFrame({
        "ZENSUS100m": ["A", "B", "C"], "ZENSUS1km": ["KA", "KB", "KC"],
        "H_ID": ["h0", "h0", "h1"], "RegionalSchlussel_ARS": "031010000000",
    })
    out, _ = assembly.build_persons(merged, resolved, mid_persons)
    clones = out[out["ZENSUS100m"].isin(["A", "B"])]
    assert len(clones) == 2
    # Donor h0 (raw 9 / 95) draws from its only donor h1 (owner, haustyp 2); both clones
    # carry that value and the imputed flag, the reported donor h1 carries no flag.
    assert clones["housing_tenure"].tolist() == ["owner", "owner"]
    assert clones["housing_tenure_imputed"].tolist() == [True, True]
    assert clones["building_type_3class"].tolist() == ["mehrfamilienhaus"] * 2
    assert clones["building_type_imputed"].tolist() == [True, True]
    reported = out[out["ZENSUS100m"] == "C"].iloc[0]
    assert not reported["housing_tenure_imputed"] and not reported["building_type_imputed"]
