"""All-employed primary-location assignment + export (#203, Approach A).

Assigns work/education primary locations to the "extra" employed/studying persons
who have NO reference-day trip (and therefore no observed commute distance), and
exports the result. The trip-haver assignment
(``synthesis/population/spatial/primary/locations.py``) is untouched, so the
MATSim plans stay byte-identical; this module only produces the additional
person -> workplace/education-place records for the commuter-OD validation
universe and a standalone export file.

Because the extras have no observed commute distance, their per-commute candidates
(``origin_id -> destination_id -> location_id``, from the gravity OD via
``candidates.py``'s isolated all-employed pass) are assigned to the extra persons
by RANDOM ordering within each origin zone -- what matters for the commuter-OD
validation is the home-zone -> work-zone flow the candidates already carry, not
the exact building.

This module hosts the pure assignment helper (unit-tested) plus, later, the synpp
stage that wires it to the pipeline and writes the export CSV.
"""

from __future__ import annotations

import pandas as pd


def assign_extras_random(df_persons, df_candidates, origin_zone_col, rng):
    """Assign per-commute extra candidates to extra persons by random ordering.

    Within each origin zone the candidate rows are randomly permuted and zipped to
    the persons of that zone, so the assignment is bijective (every candidate used
    once, every person gets exactly one location) and depends only on *rng*.

    Parameters
    ----------
    df_persons : pandas.DataFrame
        Extra persons; must contain ``person_id`` and *origin_zone_col*.
    df_candidates : pandas.DataFrame
        Per-commute extra candidates; must contain ``origin_id``,
        ``destination_id``, ``location_id`` (one row per assigned commute, as
        produced by the all-employed pass in ``candidates.py``).
    origin_zone_col : str
        Column in *df_persons* holding the origin zone key (``commune_id`` or
        ``home_taz_id``), matched against ``origin_id`` in *df_candidates*.
    rng : numpy.random.RandomState
        Random state driving the within-zone permutation (deterministic per seed).

    Returns
    -------
    pandas.DataFrame
        Columns ``person_id``, ``destination_id``, ``location_id`` -- one row per
        extra person.

    Raises
    ------
    ValueError
        If, for any origin zone, the person count differs from the candidate count
        (mirrors the equal-count contract of the trip-haver assignment).
    """
    parts = []
    for zone, persons_zone in df_persons.groupby(origin_zone_col):
        candidates_zone = df_candidates[df_candidates["origin_id"] == zone]
        if len(persons_zone) != len(candidates_zone):
            raise ValueError(
                "all-employed extra assignment: origin zone %r has %d persons but "
                "%d candidates (counts must match)."
                % (zone, len(persons_zone), len(candidates_zone)))
        order = rng.permutation(len(candidates_zone))
        candidates_shuffled = candidates_zone.iloc[order].reset_index(drop=True)
        parts.append(pd.DataFrame({
            "person_id": persons_zone["person_id"].values,
            "destination_id": candidates_shuffled["destination_id"].values,
            "location_id": candidates_shuffled["location_id"].values,
        }))
    return pd.concat(parts, ignore_index=True)
