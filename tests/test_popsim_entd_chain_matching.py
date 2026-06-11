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


# ---------------------------------------------------------------------------
# is_kish-aware diary-donor pool (immobility reproduction; correctness fix)
# ---------------------------------------------------------------------------
#
# ENTD records a travel diary for only ONE selected person per household
# (is_kish=True). A diary respondent is EITHER mobile (has trips) OR genuinely
# immobile (is_kish=True, 0 trips). Non-diary members (is_kish=False) have no
# travel information and must be matched to a diary donor.
#
# The pool MUST include the immobile diary donors so that the matched
# population inherits the diary population's true mobile:immobile mix instead of
# forcing 100% mobility. A person matched to an immobile donor naturally
# inherits ZERO trips (no donor_trips rows for that hts_id) -> stays home.


def _persons_with_is_kish():
    """Three synthetic persons:

    - person 1 / donor 10: is_kish=True, MOBILE  (has a 2-trip diary chain).
    - person 2 / donor 20: is_kish=True, IMMOBILE (diary respondent, 0 trips) ->
      a genuinely-immobile diary respondent. NOT a target; stays home by itself.
    - person 3 / donor 30: is_kish=False, NO trips -> a non-diary member that
      must be matched (a target). Same attributes as both diary donors.
    """
    return pd.DataFrame({
        "person_id": [1, 2, 3],
        "source_person_id": [10, 20, 30],
        "source_household_id": [100, 200, 300],
        "sex": pd.Categorical(["male", "male", "male"]),
        "age": [35, 35, 35],
        "employed": [True, True, True],
        "socioprofessional_class": [3, 3, 3],
        "household_size": [2, 2, 2],
        "is_kish": [True, True, False],
    })


def test_immobile_diary_donor_in_pool_yields_stay_home():
    """A non-diary target whose ONLY available diary donor is immobile must be
    matched to it and end up trip-less (stays home), not forced mobile.

    The pool contains exactly one diary donor and it is immobile -> the matched
    non-diary person inherits zero trips."""
    persons = _persons_with_is_kish()
    # Pool must contain only the IMMOBILE diary donor (person 2 / donor 20).
    # Drop the mobile diary donor (person 1 / donor 10) so the only possible
    # match for the non-diary target (person 3) is the immobile donor.
    persons = persons[persons["person_id"].isin([2, 3])].reset_index(drop=True)

    out = EntdSource().build_trips(persons, _donor_trips(), random_seed=0)

    # The non-diary target was matched to the immobile diary donor and inherited
    # ZERO trips -> it has no rows in the output (stays home).
    assert 3 not in set(out["person_id"].unique())
    # The immobile diary respondent (person 2) also has no trips.
    assert 2 not in set(out["person_id"].unique())
    # No mobile donor existed; the whole output is empty.
    assert len(out) == 0


def test_immobile_diary_person_not_matched(caplog):
    """A person with is_kish=True and 0 trips is genuinely immobile: it stays
    trip-less BY ITSELF and is NOT treated as a match target."""
    persons = _persons_with_is_kish()

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.sources.entd"):
        out = EntdSource().build_trips(persons, _donor_trips(), random_seed=0)

    # Person 2 (is_kish=True, 0 trips) is immobile: no trips in the output.
    assert 2 not in set(out["person_id"].unique())
    # The coverage log must account for it as genuinely-immobile diary, not as a
    # matched person.
    assert any("genuinely-immobile" in rec.message or "immobile diary" in rec.message.lower()
               for rec in caplog.records)


def test_non_diary_person_matched_to_mobile_donor_gets_chain():
    """A non-diary target whose ONLY feasible diary donor is mobile inherits the
    chain (existing matched-to-mobile behaviour preserved).

    The pool deliberately contains both a mobile (donor 10) and an immobile
    (donor 20) diary donor with IDENTICAL attributes; were they distinguishable
    the matcher could not prefer one. To assert the matched-to-mobile path we
    make the only feasible donor the mobile one: drop the immobile diary donor
    (person 2) so the non-diary target (person 3) must match donor 10."""
    persons = _persons_with_is_kish()
    persons = persons[persons["person_id"].isin([1, 3])].reset_index(drop=True)
    out = EntdSource().build_trips(persons, _donor_trips(), random_seed=0)

    # Person 1 has its own direct chain; person 3 (non-diary) is matched to the
    # mobile diary donor (donor 10) and inherits the 2-trip chain.
    p3 = out[out["person_id"] == 3]
    assert len(p3) == 2
    assert (p3["chain_donor_id"] == 10).all()
    # Person 1 keeps its direct chain (no chain_donor_id).
    p1 = out[out["person_id"] == 1]
    assert len(p1) == 2
    assert "chain_donor_id" not in p1.dropna(axis=1).columns or p1["chain_donor_id"].isna().all()


def test_is_kish_absent_falls_back_with_warning(caplog):
    """Frames WITHOUT an is_kish column fall back to the legacy pool
    (persons-with-trips), warn-logged, so the MiD path and existing minimal
    fixtures are unaffected."""
    import logging
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.sources.entd"):
        out = EntdSource().build_trips(_persons(), _donor_trips(), random_seed=0)

    # Legacy behaviour: the trip-less person (donor 20) is matched to the
    # mobile diary donor (donor 10).
    assert set(out["person_id"].unique()) == {1, 2}
    assert any("is_kish" in rec.message and "fall" in rec.message.lower()
               for rec in caplog.records)
