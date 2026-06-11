"""Tests for ENTD diary-donor chain matching in EntdSource.build_trips (Task 4.2).

ENTD records a travel diary only for ONE selected person per household, while
popsim_open expands FULL households -- so only ~40% of synthetic persons inherit
trips from their attribute donor. build_trips must therefore match every
trip-less synthetic person to an ENTD DIARY donor (a donor that HAS trips) via
the legacy statistical matching (synthesis.population.matched.match_donors) and
copy that donor's trip chain.

Verifies:
- trip-less persons receive a full matched chain (person present in the output);
- matched trip rows carry ``chain_donor_id`` (the diary donor whose chain was
  copied) for traceability;
- direct-chain persons are unchanged (no chain_donor_id, same trip count);
- the combined output still satisfies the downstream invariants (trip_index
  cumcount, unique global trip_id, euclidean_distance derivation).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim.sources.entd import EntdSource


def _persons():
    # Two synthetic persons; donor 10 has a diary, donor 20 does not.
    # The persons carry the mapped attribute schema (map_person_attributes
    # output) that the chain matching uses as matching keys.
    return pd.DataFrame({
        "person_id": [1, 2],
        "source_person_id": [10, 20],
        "source_household_id": [100, 200],
        "sex": pd.Categorical(["male", "male"]),
        "age": [35, 36],
        "employed": [True, True],
        "socioprofessional_class": [3, 3],
        "household_size": [2, 2],
    })


def _donor_trips():
    # ENTD trips in canonical eqasim schema (post data.hts.entd.cleaned /
    # filtered): the filtered donor carries routed_distance but NOT
    # euclidean_distance (derived inside build_trips as routed / 1.3).
    return pd.DataFrame({
        "person_id": [10, 10],
        "departure_time": [28800.0, 61200.0],
        "arrival_time": [30600.0, 63000.0],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
        "is_first_trip": [True, False],
        "is_last_trip": [False, True],
        "trip_duration": [1800.0, 1800.0],
        "activity_duration": [30600.0, float("nan")],
        "mode": ["car", "car"],
        "routed_distance": [5000.0, 5000.0],
        "trip_index": [0, 1],
    })


def test_trip_less_persons_get_matched_chains():
    out = EntdSource().build_trips(_persons(), _donor_trips(), random_seed=0)
    assert set(out["person_id"].unique()) == {1, 2}            # both have chains
    p2 = out[out["person_id"] == 2]
    assert len(p2) == 2                                         # inherited the 2-trip chain
    assert (p2["chain_donor_id"] == 10).all()                   # traceability


def test_direct_chains_unchanged_and_no_double_assignment():
    out = EntdSource().build_trips(_persons(), _donor_trips(), random_seed=0)
    p1 = out[out["person_id"] == 1]
    assert len(p1) == 2
    assert "chain_donor_id" not in p1.dropna(axis=1).columns or p1["chain_donor_id"].isna().all()


def test_matched_chain_provenance_keeps_attribute_donor():
    """source_person_id on matched trip rows stays the person's ATTRIBUTE donor;
    the chain donor is recorded separately in chain_donor_id."""
    out = EntdSource().build_trips(_persons(), _donor_trips(), random_seed=0)
    p2 = out[out["person_id"] == 2]
    assert (p2["source_person_id"] == 20).all()
    assert (p2["chain_donor_id"] == 10).all()


def test_combined_output_keeps_downstream_invariants():
    """trip_index cumcount, unique global trip_id and euclidean_distance must
    hold across the COMBINED (direct + matched) trips."""
    out = EntdSource().build_trips(_persons(), _donor_trips(), random_seed=0)

    assert out["trip_id"].is_unique
    for _pid, grp in out.groupby("person_id"):
        assert sorted(grp["trip_index"].tolist()) == list(range(len(grp)))
    assert "euclidean_distance" in out.columns
    assert np.allclose(out["euclidean_distance"], 5000.0 / 1.3)


def test_no_trip_less_persons_means_no_chain_donor_column():
    """When every person has a direct donor chain, no matching runs and the
    output schema is unchanged (no chain_donor_id column)."""
    persons = _persons()
    persons = persons[persons["source_person_id"] == 10]
    out = EntdSource().build_trips(persons, _donor_trips(), random_seed=0)
    assert set(out["person_id"].unique()) == {1}
    assert "chain_donor_id" not in out.columns


def test_unmatchable_person_left_trip_less_loudly(caplog):
    """A person whose FIRST matching-key value has no feasible diary donors is
    left trip-less (eqasim stay-home convention) and reported, instead of
    aborting the whole matching."""
    persons = _persons()
    # Make person 2 female: the pool (donor 10, male) has zero donors for the
    # first key value "female", which match_donors can never relax.
    persons["sex"] = pd.Categorical(["male", "female"])

    import logging
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.sources.entd"):
        out = EntdSource().build_trips(persons, _donor_trips(), random_seed=0)

    assert set(out["person_id"].unique()) == {1}
    assert any("trip-less" in rec.message or "unmatch" in rec.message.lower()
               for rec in caplog.records)
