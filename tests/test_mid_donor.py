"""Tests for braunschweig.data.hts.mid_donor (MiD in-commuter HTS donor stage).

See .superpowers/sdd/task-1-brief.md and
docs/superpowers/specs/2026-07-18-incommuter-mid-donor-design.md.

Step 1 investigation (braunschweig/popsim/attributes.py + braunschweig/popsim/
trips.py) confirmed the exact columns the reused mapping functions require from
the raw MiD persons/wege frames; the synthetic fixture below carries exactly
those columns, extended by ONE beyond the brief's draft:

- ``attributes.map_employed`` / ``map_studies`` read ``P_TAET`` (int code).
  ``map_employed`` additionally conditions item-nonresponse imputation on
  ``alter_gr1`` (+ ``RegioStaR7`` when present, via ``imputation_group_cols``);
  both are present on the fixture even though this fixture has no missing
  codes (99) to impute.
- ``trips.build_trip_table`` (via ``expand_persons_to_trips``) requires
  ``H_ID``, ``P_ID``, ``W_ID`` (join/sort/trip-key keys); ``W_ZWECK`` (mapped
  by ``trips.map_purpose``); and the hardcoded clock columns ``W_SZS``/
  ``W_SZM`` (departure) and ``W_AZS``/``W_AZM`` (arrival) read directly by
  ``trips.mid_time_seconds`` inside ``build_trip_table``.
- DEVIATION from the brief's draft fixture: ``trips.expand_persons_to_trips``
  unconditionally calls ``trips.map_mode(mid_wege)``, which reads ``hvm_imp``
  (MiD imputed main mode, codes 1..5) and RAISES ``ValueError`` if the column
  is absent or carries an unmapped code -- there is no silent walk/pt default.
  The brief's draft ``_synthetic_mid()`` omitted ``hvm_imp``; it is added below
  (valid codes only) so the pure transform actually exercises the real
  ``build_trip_table``, not a stubbed one.
"""
import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.hts import mid_donor


def _synthetic_mid():
    # Minimal MiD-shaped frames. Columns confirmed against map_employed /
    # map_studies / build_trip_table in Step 1 (see module docstring).
    households = pd.DataFrame({"H_ID": [1, 2]})
    persons = pd.DataFrame({
        "H_ID": [1, 2], "P_ID": [1, 1],
        "P_TAET": [1, 10],          # 1 -> employed, 10 -> Student -> studies
        "alter_gr1": [5, 3], "RegioStaR7": [71, 71],
        "HP_ALTER": [45, 22], "HP_SEX": [1, 2],
    })
    # Person 1: home(0)->work(1)->home; Person 2: home->education->home.
    wege = pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 1, 1, 1], "W_ID": [1, 2, 1, 2],
        "W_ZWECK": [1, 8, 3, 8],    # 1 -> work, 8 -> home, 3 -> education
        # hvm_imp (MiD imputed main mode): required by trips.map_mode, which
        # raises on any code outside 1..5 (no unmapped-code silent default).
        "hvm_imp": [4, 4, 5, 5],    # 4 -> car, 5 -> pt
        # Departure (W_SZS/W_SZM) + arrival (W_AZS/W_AZM) hour/minute columns
        # read directly by trips.mid_time_seconds inside build_trip_table.
        "W_SZS": [8, 17, 8, 16], "W_SZM": [0, 0, 30, 0],
        "W_AZS": [8, 17, 9, 16], "W_AZM": [30, 30, 0, 30],
    })
    return households, persons, wege


def test_build_mid_donor_frames_schema_and_flags():
    hh, persons, wege = _synthetic_mid()
    ho, po, to = mid_donor.build_mid_donor_frames(
        hh, persons, wege, rng=np.random.RandomState(0))
    # persons: donor schema + flags
    assert {"person_id", "employed", "studies"} <= set(po.columns)
    assert po["person_id"].is_unique
    m = po.set_index("person_id")
    assert m.loc["1_1", "employed"] and not m.loc["1_1", "studies"]
    assert m.loc["2_1", "studies"]
    # trips: eqasim schema + purposes + float times
    assert {"person_id", "preceding_purpose", "following_purpose",
            "departure_time", "arrival_time"} <= set(to.columns)
    assert to["departure_time"].dtype == float
    # first trip of each person departs from home (build_trip_table home-first rule)
    firsts = to.sort_values("person_id").groupby("person_id").first()
    assert (firsts["preceding_purpose"] == "home").all()


def test_build_mid_donor_frames_rejects_person_id_collision():
    hh, persons, wege = _synthetic_mid()
    # Force a (H_ID, P_ID) collision: two rows share the same household/person id.
    colliding_persons = persons.copy()
    colliding_persons.loc[1, ["H_ID", "P_ID"]] = [1, 1]
    with pytest.raises(ValueError, match="person_id collision"):
        mid_donor.build_mid_donor_frames(
            hh, colliding_persons, wege, rng=np.random.RandomState(0))


class _RecordingCtx:
    """Minimal synpp-context stub that records every (key, default) pair
    ``config`` is called with, and only returns a value for keys it knows
    about -- mirroring synpp's real behaviour of raising/KeyErroring on a
    config() call for an unknown key with no default supplied.
    """

    def __init__(self, known=None):
        self._known = dict(known or {})
        self.calls = []

    def config(self, key, default=None):
        self.calls.append((key, default))
        if key in self._known:
            return self._known[key]
        if default is None:
            raise KeyError(f"no default supplied for unset config key {key!r}")
        return default


def test_configure_defaults_mid_raw_path_when_unset():
    # A cordon-enabled config that never sets mid_raw_path (e.g. an
    # ENTD-resident config with population.method != mid) must not hard-fail
    # at synpp graph-build (FIX 1, #140 follow-up). random_seed is supplied
    # (unrelated to this fix) so only the KEY_MID resolution is under test.
    ctx = _RecordingCtx(known={"random_seed": 0})
    mid_donor.configure(ctx)
    assert (mid_donor.KEY_MID, mid_donor.DEFAULT_MID_RAW_PATH) in ctx.calls


def test_configure_respects_explicit_mid_raw_path():
    explicit = "eqasim-data/data/braunschweig/popsim/mid2023_raw_custom"
    ctx = _RecordingCtx(known={mid_donor.KEY_MID: explicit, "random_seed": 0})
    mid_donor.configure(ctx)
    assert ctx.config(mid_donor.KEY_MID, mid_donor.DEFAULT_MID_RAW_PATH) == explicit


_MID_DIR = "eqasim-data/data/braunschweig/popsim/mid2023_raw"
#: The three CSVs ``MidSource.load_donor`` actually reads (see its docstring). Guarding on
#: the DIRECTORY was not enough: the raw MiD B1 microdata is gitignored, so on every checkout
#: without it the directory exists (empty, or holding only the codebook) while the CSVs do
#: not, and this test FAILED deep inside the loader instead of skipping -- a data gap that
#: read as a code defect. Naming the files individually also makes the skip reason say which
#: one is missing.
_MID_RAW_FILES = ("MiD2023_Haushalte.csv", "MiD2023_Personen.csv", "MiD2023_Wege.csv")
_MISSING_MID_RAW_FILES = tuple(
    name for name in _MID_RAW_FILES if not os.path.isfile(os.path.join(_MID_DIR, name)))


@pytest.mark.skipif(
    bool(_MISSING_MID_RAW_FILES),
    reason=f"needs the MiD 2023 raw survey, missing: {_MISSING_MID_RAW_FILES}")
def test_execute_on_real_mid_yields_commute_donors():
    class Ctx:
        def config(self, k, d=None):
            return {"braunschweig.population.popsim.mid_raw_path": _MID_DIR,
                    mid_donor.KEY_DEMOGRAPHICS: True,
                    "random_seed": 1234}.get(k, d)
    hh, persons, trips = mid_donor.execute(Ctx())
    assert persons["person_id"].is_unique
    # nationwide MiD must supply real donor pools for both in-commuter kinds
    assert trips[trips["following_purpose"] == "work"]["person_id"].nunique() > 100
    assert trips[trips["following_purpose"] == "education"]["person_id"].nunique() > 10
    # ADR-0191: nearly every donor carries its household income and economic status (primary
    # path on the real survey; item non-response is imputed). A person without a household row
    # stays NaN, so the share is bounded, not 1 (attach_donor_household counts and warns).
    assert persons["household_income_eur"].notna().mean() > 0.99
    assert persons["economic_status"].notna().mean() > 0.99
    for column in ("household_size", "consumption_units", "donor_weight"):
        assert persons[column].notna().mean() > 0.99
    assert persons["weekday_reporting_day"].mean() > 0.5


def _donor_households():
    absent = {f"HP_ALTER_{i}": [3300 + i, 3300 + i] for i in (4, 5, 6)}
    return pd.DataFrame({
        "H_ID": [1, 2], "hhgr_gr": [3, 3], "H_GR": [3, 3],
        "HP_ALTER_1": [45, 22], "HP_ALTER_2": [43, 20],
        "HP_ALTER_3": [9, 999],          # a child of 9; a member without a valid age
        "hheink_gr1": [6, 99],           # 6 -> 2600-3000 EUR; 99 -> imputed from household 1
        "oek_status": [4, 9],            # 4 -> high; 9 -> imputed from household 1
        **absent,
    })


def _donors():
    _, persons, wege = _synthetic_mid()
    return mid_donor.build_mid_donor_frames(
        pd.DataFrame({"H_ID": [1, 2]}), persons, wege, rng=np.random.RandomState(0))[1]


def test_donor_carries_income_status_size_and_consumption_units_of_its_household():
    def attach(seed):
        return mid_donor.attach_donor_household(
            _donors(), _donor_households(), np.random.RandomState(seed),
            age_unknown_raise_share=0.5).set_index("person_id")

    out = attach(7)
    assert out["economic_status"].to_dict() == {"1_1": "high", "2_1": "high"}
    assert out["household_income_eur"].between(2600.0, 3000.0).all()
    assert out["household_size"].to_dict() == {"1_1": 3, "2_1": 3}
    # OECD-modified: 1.0 + 0.5 (second adult) + 0.3 (child of 9) = 1.8; the member without a
    # valid age counts as 14 or older: 1.0 + 0.5 + 0.5 = 2.0
    assert out["consumption_units"].to_dict() == pytest.approx({"1_1": 1.8, "2_1": 2.0})
    pd.testing.assert_frame_equal(out, attach(7))


@pytest.mark.parametrize("households, message", [
    (_donor_households().drop(columns="hheink_gr1"), "hheink_gr1"),
    (_donor_households().drop(columns="HP_ALTER_3"), "HP_ALTER_3"),
    (_donor_households().iloc[:1], "household record"),     # half of the donors without a household
    # every member without a valid age: a broken join with the age file, not item non-response
    (_donor_households().assign(HP_ALTER_1=999, HP_ALTER_2=999, HP_ALTER_3=999), "valid age"),
])
def test_donor_household_fails_on_a_missing_column_or_a_broken_join(households, message):
    with pytest.raises(ValueError, match=message):
        mid_donor.attach_donor_household(_donors(), households, np.random.RandomState(0))


def test_donor_draw_attributes_are_weight_weekday_and_first_home_legs():
    _, persons, wege = _synthetic_mid()
    persons = persons.assign(P_GEW=[1.5, 0.5], kernwo=[2, 4])        # 4 = weekend
    wege = wege.assign(wegkm_imp=[12.5, 11.0, 3.5, 3.0])
    _, donors, trips = mid_donor.build_mid_donor_frames(
        pd.DataFrame({"H_ID": [1, 2]}), persons, wege, rng=np.random.RandomState(0))
    out = mid_donor.attach_donor_draw_attributes(donors, persons, trips).set_index("person_id")
    assert out["donor_weight"].to_dict() == {"1_1": 1.5, "2_1": 0.5}
    assert out["weekday_reporting_day"].to_dict() == {"1_1": True, "2_1": False}
    assert out.loc["1_1", "commute_distance_km"] == 12.5     # home -> work, not the 11.0 km return leg
    assert np.isnan(out.loc["2_1", "commute_distance_km"])   # a student: no leg between home and work
    assert out.loc["2_1", "education_distance_km"] == 3.5    # home -> education, not the 3.0 km return leg
    assert np.isnan(out.loc["1_1", "education_distance_km"])


def test_a_trip_table_without_any_home_education_leg_gives_nan_education_distances():
    _, persons, wege = _synthetic_mid()
    persons = persons.assign(P_GEW=[1.5, 0.5], kernwo=[2, 4])
    wege = wege.assign(wegkm_imp=[12.5, 11.0, 3.5, 3.0])
    _, donors, trips = mid_donor.build_mid_donor_frames(
        pd.DataFrame({"H_ID": [1, 2]}), persons, wege, rng=np.random.RandomState(0))
    no_education = trips.assign(
        preceding_purpose=trips["preceding_purpose"].replace("education", "other"),
        following_purpose=trips["following_purpose"].replace("education", "other"))
    assert not ((no_education["preceding_purpose"] == "education")
                | (no_education["following_purpose"] == "education")).any()
    out = mid_donor.attach_donor_draw_attributes(donors, persons, no_education)
    assert out["education_distance_km"].isna().all()
    assert out["commute_distance_km"].notna().any()     # the work legs are unaffected
