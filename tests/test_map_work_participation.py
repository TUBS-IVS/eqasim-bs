"""Tests for the MiD-side ``work_participation`` seed (Task 3, feature #224).

Adds ``attributes.map_work_participation`` (mirrors ``attributes.map_trip_class``
exactly: same ``missing.AttributeSpec`` / ``imputation_group_cols`` / ``missing.resolve``
pattern) and ``mid.compute_has_work_trip`` + ``mid.derive_work_participation_seed``
(mirrors ``mid.derive_trip_class_seed``: seeds ``work_participation`` from the person's
REALISED weekday plan SOURCE via ``source_H_ID``/``source_P_ID``, with the same fail-loud
guard on an unresolved plan source and the same no-source fallback branch).

``has_work_trip`` is not a pre-computed MiD column (unlike ``anzwege1`` for
``trip_class``); it is derived per donor person from the Wege
(``braunschweig/popsim/trips.py::PURPOSE_BY_W_ZWECK``): >=1 Weg with ``W_ZWECK`` in
{1 Arbeit, 2 dienstlich} -> 1, else 0 -- UNLESS the person's own ``anzwege1`` is a
diary non-response code (803/804), in which case the code is carried through so the
downstream ``missing.AttributeSpec(impute_codes=(803, 804))`` imputes it, exactly as
``map_trip_class`` does. Never force an unknown-diary person to 0.

The eventual per-Kreis control registration + pipeline wiring is Task 4; this task
only delivers the seed-derivation machinery + its unit tests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes
from braunschweig.popsim import mid


# --- attributes.map_work_participation ------------------------------------------


def test_map_work_participation_binary_and_impute():
    persons = pd.DataFrame({
        "work_participation_src": [0, 1, 1, 0, 803],
        "alter_gr1": [1, 1, 1, 1, 1],
    })
    out = attributes.map_work_participation(persons, rng=np.random.RandomState(0))
    assert out["work_participation"].dtype.kind == "i"
    assert set(out["work_participation"].unique()) <= {0, 1}
    # The 803 (diary non-response) row must be imputed to a valid {0, 1} value from the
    # age-band pool -- never dropped, never left as 803, never forced to a fixed value.
    imputed = out["work_participation"].iloc[4]
    assert imputed in (0, 1)


def test_map_work_participation_imputation_is_seeded_and_deterministic():
    persons = pd.DataFrame({
        "work_participation_src": [0, 1, 1, 0, 803, 804],
        "alter_gr1": [1, 1, 1, 1, 1, 1],
    })
    out_a = attributes.map_work_participation(persons, rng=np.random.RandomState(42))
    out_b = attributes.map_work_participation(persons, rng=np.random.RandomState(42))
    assert out_a["work_participation"].tolist() == out_b["work_participation"].tolist()


def test_map_work_participation_fails_on_absent_column():
    persons = pd.DataFrame({"alter_gr1": [1, 2]})
    with pytest.raises(KeyError):
        attributes.map_work_participation(persons)


# --- mid.compute_has_work_trip ---------------------------------------------------


def test_compute_has_work_trip():
    # p1: has a W_ZWECK=1 (Arbeit) Weg -> 1. p2: only a W_ZWECK=7 (Freizeit) Weg -> 0.
    # p3: own anzwege1=803 (diary non-response) -> carried through unchanged.
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2", "h3"],
        "P_ID": ["p1", "p2", "p3"],
        "anzwege1": [2, 1, 803],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2", "h2", "h3"],
        "P_ID": ["p1", "p2", "p2", "p3"],
        "W_ZWECK": [1, 7, 7, 7],
    })
    out = mid.compute_has_work_trip(persons, wege)
    by_pid = dict(zip(persons["P_ID"], out))
    assert by_pid["p1"] == 1
    assert by_pid["p2"] == 0
    assert by_pid["p3"] == 803


def test_compute_has_work_trip_dienstlich_counts_as_work():
    # W_ZWECK=2 (dienstlich) must count as a work trip, same as W_ZWECK=1 (Arbeit).
    persons = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "anzwege1": [1]})
    wege = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "W_ZWECK": [2]})
    out = mid.compute_has_work_trip(persons, wege)
    assert out.iloc[0] == 1


def test_compute_has_work_trip_fails_on_absent_columns():
    persons = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"]})
    wege = pd.DataFrame({"H_ID": ["h1"], "P_ID": ["p1"], "W_ZWECK": [1]})
    with pytest.raises(KeyError):
        mid.compute_has_work_trip(persons, wege)


# --- mid.derive_work_participation_seed: realised-plan-source remapping ---------
# Mirrors tests/test_popsim_seed_kreis_columns.py's _plan_source_persons /
# test_derive_trip_class_seed_* trio for the analogous work_participation seed.


def _plan_source_persons_and_wege():
    """A real weekday donor (p1, has a work Weg) and a weekend reporter (p2, own Wege
    have no work trip) whose plan source was remapped to the weekday donor (source ->
    h1/p1)."""
    persons = pd.DataFrame({
        "H_ID": ["h1", "h2"],
        "P_ID": ["p1", "p2"],
        "anzwege1": [5, 1],
        "alter_gr1": [5, 5],
        "member_imputed": [False, False],
        "source_H_ID": ["h1", "h1"],        # p2's realised plan is the weekday donor p1
        "source_P_ID": ["p1", "p1"],
    })
    wege = pd.DataFrame({
        "H_ID": ["h1", "h2"],
        "P_ID": ["p1", "p2"],
        "W_ZWECK": [1, 7],                  # p1 has a work Weg; p2's own Weg is leisure only
    })
    return persons, wege


def test_derive_work_participation_seed_uses_realised_plan_source_not_own():
    persons, wege = _plan_source_persons_and_wege()
    out = mid.derive_work_participation_seed(persons, wege, rng=np.random.RandomState(0))
    by_pid = dict(zip(out["P_ID"], out["work_participation"]))
    # p1 (weekday donor, work Weg) -> 1; p2's realised plan is p1's -> 1, NOT 0 from its
    # own weekend leisure-only Weg. This is the whole fix (mirrors the trip_class fix).
    assert by_pid["p1"] == 1
    assert by_pid["p2"] == 1
    # The temporary plan-source column must not leak into the seed.
    assert "_plan_source_has_work_trip" not in out.columns


def test_derive_work_participation_seed_falls_back_to_own_without_source_columns():
    # No plan-source columns (no member completion / weekend match) -> the seed is
    # already weekday-filtered, so has_work_trip comes from the person's own Wege.
    persons, wege = _plan_source_persons_and_wege()
    persons = persons.drop(columns=["source_H_ID", "source_P_ID", "member_imputed"])
    out = mid.derive_work_participation_seed(persons, wege, rng=np.random.RandomState(0))
    by_pid = dict(zip(out["P_ID"], out["work_participation"]))
    assert by_pid["p1"] == 1   # own work Weg -> 1
    assert by_pid["p2"] == 0   # own leisure-only Weg -> 0 (own-Wege fallback branch)


def test_derive_work_participation_seed_raises_on_unresolved_plan_source():
    persons, wege = _plan_source_persons_and_wege()
    persons.loc[persons["P_ID"] == "p2", "source_P_ID"] = "does_not_exist"
    with pytest.raises(ValueError, match="absent from the donor frame"):
        mid.derive_work_participation_seed(persons, wege, rng=np.random.RandomState(0))
