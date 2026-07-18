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
        "age": [45, 22], "sex": ["male", "female"],
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


_MID_DIR = "eqasim-data/data/braunschweig/popsim/mid2023_raw"


@pytest.mark.skipif(not os.path.isdir(_MID_DIR),
                    reason="needs the committed MiD 2023 raw survey (data-complete env)")
def test_execute_on_real_mid_yields_commute_donors():
    class Ctx:
        def config(self, k, d=None):
            return {"braunschweig.population.popsim.mid_raw_path": _MID_DIR,
                    "random_seed": 1234}.get(k, d)
    hh, persons, trips = mid_donor.execute(Ctx())
    assert persons["person_id"].is_unique
    # nationwide MiD must supply real donor pools for both in-commuter kinds
    assert trips[trips["following_purpose"] == "work"]["person_id"].nunique() > 100
    assert trips[trips["following_purpose"] == "education"]["person_id"].nunique() > 10
