"""Integration test: weekend_plan_match wired into the donor-build seam.

Verifies that after ``reassign_weekend_plan_sources`` a weekend person's
``source_H_ID``/``source_P_ID`` is remapped to the weekday donor, AND that
``trips.build_trip_table`` therefore gives the weekend person a trip row via
the source-id join.

The plan (Task 7 Step 1) specifies a full ``build_trip_table`` path if feasible,
or a direct remap + source-id join assertion if end-to-end is too brittle.
We go the full path: the Wege frame carries the MiD time columns
(W_SZS/W_SZM/W_AZS/W_AZM) that ``build_trip_table`` requires.  Columns are
chosen to be minimally valid: single trip, all times in the valid clock range,
no midnight crossing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import weekend_plan_match as wpm
from braunschweig.popsim import trips as trips_mod


def _make_households():
    """One weekday donor HH (id=1, size=1) and one weekend HH (id=2, size=1)."""
    return pd.DataFrame({
        "H_ID": [1, 2],
        "H_GR": [1, 1],
        "hh_type5": ["single", "single"],
        "oek_status": [2, 2],
        "RegioStaR7": [71, 71],
        "H_ANZAUTO": [1, 1],
    })


def _make_persons():
    """Weekday person (H_ID=1, kernwo=2) and weekend person (H_ID=2, kernwo=6)."""
    return pd.DataFrame({
        "H_ID": [1, 2],
        "P_ID": [1, 1],
        "HP_ALTER": [40, 41],
        "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1],
        "P_TAET": [1, 1],
        "P_FKARTE": [1, 1],
        "kernwo": [2, 6],
        "source_H_ID": [1, 2],
        "source_P_ID": [1, 1],
        "member_imputed": [False, False],
        "person_id": ["c_1_1", "c_2_1"],
    })


def _make_wege():
    """Minimal valid MiD Wege row for the weekday donor (H_ID=1, P_ID=1).

    Columns required by ``build_trip_table``/``expand_persons_to_trips``:
    H_ID, P_ID, W_ID, W_ZWECK, hvm_imp  (for purpose + mode mapping)
    W_SZS, W_SZM, W_AZS, W_AZM          (for time conversion in mid_time_seconds)

    One trip: depart 8:00, arrive 8:30, purpose=work (W_ZWECK=1), mode=car (hvm_imp=4).
    """
    return pd.DataFrame({
        "H_ID": [1],
        "P_ID": [1],
        "W_ID": [1],
        "W_ZWECK": [1],   # Arbeit -> "work"
        "hvm_imp": [4],   # MIV-Fahrer -> "car"
        "W_SZS": [8],     # departure hour
        "W_SZM": [0],     # departure minute
        "W_AZS": [8],     # arrival hour
        "W_AZM": [30],    # arrival minute
    })


def test_weekend_person_source_remapped_to_weekday_donor():
    """After reassign, the weekend person's source_H_ID points at the weekday HH."""
    households = _make_households()
    persons = _make_persons()

    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0)
    )

    # weekday person (H_ID=1) is untouched
    wd_row = out[out["H_ID"] == 1].iloc[0]
    assert wd_row["source_H_ID"] == 1

    # weekend person (H_ID=2) is now sourced from weekday HH 1
    we_row = out[out["H_ID"] == 2].iloc[0]
    assert we_row["source_H_ID"] == 1, (
        f"Weekend person source_H_ID should be 1 (weekday donor), got {we_row['source_H_ID']}"
    )

    assert report.n_weekend_households == 1
    assert report.n_hh_matched == 1


def test_weekend_persons_receive_weekday_wege_through_trips_join():
    """After remap, build_trip_table gives the weekend person a trip row via the
    source-id join — the core end-to-end assertion of the Task 7 spec.
    """
    households = _make_households()
    persons = _make_persons()
    wege = _make_wege()

    out, _trace, _report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0)
    )

    # Confirm the remap happened so the trip assertion is meaningful.
    we_row = out[out["H_ID"] == 2].iloc[0]
    assert we_row["source_H_ID"] == 1, "Precondition: weekend person must be remapped"

    table = trips_mod.build_trip_table(out, wege)

    # The weekend person (person_id="c_2_1") must appear in the trip table because
    # build_trip_table overwrites H_ID/P_ID from source_H_ID/source_P_ID before
    # joining — so it joins on (H_ID=1, P_ID=1) and finds the weekday Wege.
    assert (table["person_id"] == "c_2_1").any(), (
        "Weekend person 'c_2_1' has no trip row; source-id join via source_H_ID "
        "did not propagate the weekday donor's Wege to the weekend person."
    )
