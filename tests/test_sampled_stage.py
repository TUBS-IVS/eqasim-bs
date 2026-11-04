"""Tests for the ``synthesis.population.sampled`` stage: household replication and sampling.

The stage replicates every census household by its stochastically rounded weight and then keeps a random share
of the replicated households. Its replication built a ragged numpy array (C5d, found in a 100 % production run;
numpy 1.23 only warns, numpy 1.24 and later raise a ValueError, so the first numpy upgrade would have stopped the
pipeline at this stage). Evidence that the output did not change: run manifest
``docs/runs/prod100-sampled-ragged-array-2026-11-04.yml``.

The member order inside a household is the input order (the stage sorts stably); one test pins that, the others
compare members as sorted lists so that each keeps to its own contract.
"""
from __future__ import annotations

import warnings

try:  # numpy 1.25 moved the warning classes into numpy.exceptions; the locked numpy 1.23 has them at the top level
    from numpy.exceptions import VisibleDeprecationWarning
except ImportError:
    from numpy import VisibleDeprecationWarning

import pandas as pd

from synthesis.population import sampled


class _ExecuteContext:
    """The part of synpp's ExecuteContext the stage uses: ``config(name)`` takes the key ALONE, ``stage(name)``."""

    def __init__(self, source, **config):
        self._source = source
        self._config = config

    def config(self, name):
        return self._config[name]

    def stage(self, name):
        assert name == "source", name
        return self._source


def _census(households):
    """One row per person, sorted by household_id, as ``data.census.filtered`` delivers it.

    ``households`` is a list of ``(household_id, household_size, weight)``. The person id is
    ``10 * household_id + member index``, so the replicated rows can be read off the ids.
    """
    rows = []
    for household_id, household_size, weight in households:
        for member in range(household_size):
            rows.append({
                "household_id": household_id,
                "person_id": 10 * household_id + member,
                "weight": weight,
                "household_size": household_size,
                "age": 20 + member,
            })
    return pd.DataFrame(rows)


def _members_by_replica(result):
    """The census person ids of every replicated household, in the order the replicas appear."""
    return result.groupby("household_id", sort=False)["census_person_id"].apply(list)


def test_households_are_replicated_block_wise_without_a_ragged_array():
    """Households of sizes 1, 3, 2 and 2, replicated 2, 1, 0 and 3 times.

    The weights are whole numbers, so the replication count is floor(weight) whatever the seed, and the expected
    result is hand-computed: every household appears that many times, each replica a household of its own with
    all the members of the original, the replicas of one household next to each other and listing the same
    members in the same order. The household sizes DIFFER on purpose: with equal sizes numpy builds a regular
    2-D array and never warns, so only unequal sizes expose the construction. The numpy warning is an error
    here, as it is from numpy 1.24 on.
    """
    census = _census([(10, 1, 2.0), (11, 3, 1.0), (12, 2, 0.0), (13, 2, 3.0)])

    with warnings.catch_warnings():
        warnings.simplefilter("error", VisibleDeprecationWarning)
        # pandas announces behaviour changes with DeprecationWarning (the .loc column assignment did), so they fail too.
        warnings.simplefilter("error", DeprecationWarning)
        result = sampled.execute(_ExecuteContext(census, random_seed=0, sampling_rate=1.0))

    members = _members_by_replica(result)
    assert [sorted(ids) for ids in members] == [[100], [100], [110, 111, 112], [130, 131], [130, 131], [130, 131]]
    assert members.iloc[0] == members.iloc[1]
    assert members.iloc[3] == members.iloc[4] == members.iloc[5]
    assert result["household_id"].tolist() == [0, 1, 2, 2, 2, 3, 3, 4, 4, 5, 5]
    assert result["census_household_id"].tolist() == [10, 10, 11, 11, 11, 13, 13, 13, 13, 13, 13]
    assert result["person_id"].tolist() == list(range(11))
    assert "weight" not in result.columns


def test_members_keep_their_input_order_when_the_input_is_not_sorted_by_household():
    """The members of a household keep the order they have in the input, whatever numpy's sort algorithm.

    The production input is not sorted by household_id, and numpy's default sort is not stable: it reorders persons
    of the same household, differently in another numpy version. The input below interleaves 40 households (sizes
    2 to 4, more than numpy's insertion-sort threshold of 16 rows) with members numbered in increasing order.
    """
    households = [(100 + (7 * index) % 40, 2 + index % 3, 1.0) for index in range(40)]
    census = _census(households)
    # Interleave the households: the first member of every household, then the second, ...
    census = census.assign(_member=census.groupby("household_id").cumcount()).sort_values(
        "_member", kind="stable").drop(columns="_member").reset_index(drop=True)

    result = sampled.execute(_ExecuteContext(census, random_seed=0, sampling_rate=1.0))

    members = _members_by_replica(result)
    assert len(members) == 40
    assert all(ids == sorted(ids) for ids in members)


# Fractional weights, a sampling rate below 1 and the random seed of the production configuration.
_SEEDED_HOUSEHOLDS = [
    (21, 1, 1.4), (22, 2, 0.6), (23, 3, 2.5), (24, 1, 1.0), (25, 2, 1.7),
    (26, 4, 0.3), (27, 2, 2.2), (28, 1, 0.9), (29, 3, 1.2), (30, 2, 0.5),
]
# Replicated household id -> census person ids that are left after the random rounding and the random sampling.
_SEEDED_SAMPLE = {
    0: [210], 1: [210], 4: [230, 231, 232], 5: [240], 6: [250, 251], 7: [260, 261, 262, 263], 10: [280],
}


def test_seeded_rounding_and_selection_keep_drawing_the_same_sample():
    """The same seed must keep producing the same sample: downstream stages and their caches rely on it.

    ``_SEEDED_SAMPLE`` was produced by the implementation that built the ragged array, before it was replaced, on
    this input and seed. ``RandomState`` streams are fixed by numpy's compatibility promise, so the numbers do
    not depend on the platform.
    """
    result = sampled.execute(_ExecuteContext(_census(_SEEDED_HOUSEHOLDS), random_seed=1234, sampling_rate=0.6))

    sample = {int(household_id): sorted(ids) for household_id, ids in _members_by_replica(result).items()}
    assert sample == _SEEDED_SAMPLE
