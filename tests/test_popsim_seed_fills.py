"""Seed fills resolved ONCE per donor person before PopulationSim (issue #647, WP3).

MiD sex codes 3 (diverse) and 9 (no entry) count in no PopulationSim sex control. The stage
resolves them to male/female once per donor person (the ADR-0192 pattern), so the seed and
every expanded clone carry the same value. Tiny synthetic data only.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes, expand, sources
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


def test_resolve_binary_sex_is_idempotent():
    once = attributes.resolve_binary_sex(
        _persons([1, 2, 3], [1, 1, 1]), rng=np.random.RandomState(0))
    twice = attributes.resolve_binary_sex(once, rng=np.random.RandomState(99))
    pd.testing.assert_frame_equal(once, twice)


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


def test_sex_key_on_without_fill_rng_raises():
    donor, _ = _donor()
    with pytest.raises(ValueError, match="seed_fill_rng"):
        _build(donor, resolve_binary_sex=True)


def test_configure_declares_the_sex_key_default_on():
    declared = {}

    class _Recorder:
        def config(self, key, default=None, **kwargs):
            declared.setdefault(key, default)
            return default

        def stage(self, *args, **kwargs):
            return None

    popsim_stage.configure(_Recorder())
    assert declared[popsim_stage.KEY_SEED_RESOLVE_BINARY_SEX] is True


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
