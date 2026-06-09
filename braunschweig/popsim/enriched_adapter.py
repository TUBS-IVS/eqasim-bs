"""synpp stage: adapt popsim persons to the MATSim writer PERSON_FIELDS schema.

Aliased to synthesis.population.enriched for popsim_mid. Maps the producer-agnostic
source_* provenance to the writer's id fields (the writer stays unchanged). No HTS
matching, no synthesis.population.matched dependency.

The MiD donor person/household ids (source_person_id / source_household_id) fill the
hts_id / hts_household_id writer slots; the popsim synthetic ids (person_id /
household_id) fill the census_person_id / census_household_id slots. This preserves
the writer's traceability contract without requiring an HTS survey match step.
"""
from __future__ import annotations

import pandas as pd


def run(persons: pd.DataFrame) -> pd.DataFrame:
    """Map source_* provenance columns to the writer's id fields.

    Parameters
    ----------
    persons:
        Popsim persons frame carrying at minimum ``source_person_id``,
        ``source_household_id``, ``person_id``, and ``household_id``.
        When called after ``synthesis.population.sampled`` the frame already
        carries ``census_person_id`` / ``census_household_id`` (the original
        popsim ids preserved by sampled); those must NOT be overwritten so that
        the provenance chain remains intact.

    Returns
    -------
    pandas.DataFrame
        Input frame with ``hts_id`` / ``hts_household_id`` set from
        ``source_*`` (always), and ``census_person_id`` /
        ``census_household_id`` set from ``person_id`` / ``household_id``
        only when absent (i.e. not already supplied by sampled).
    """
    out = persons.copy()
    # hts_id / hts_household_id: the MiD donor is the analog of the HTS donor.
    # Always (re-)set so callers that provide custom source_* values are honoured.
    out["hts_id"] = out["source_person_id"]
    out["hts_household_id"] = out["source_household_id"]
    # census_person_id / census_household_id: set only when absent.
    # synthesis.population.sampled sets them to the ORIGINAL popsim ids before
    # reassigning new integer ids; overwriting them here would destroy provenance.
    if "census_person_id" not in out.columns:
        out["census_person_id"] = out["person_id"].astype("string")
    if "census_household_id" not in out.columns:
        out["census_household_id"] = out["household_id"].astype("string")
    return out


def configure(context):
    # Read from synthesis.population.sampled (not the raw producer): sampled
    # carries the reassigned integer person_id + the preserved census_* ids,
    # so the writer receives the fully id-remapped and sampling-applied frame.
    context.stage("synthesis.population.sampled", alias="persons")


def execute(context):
    return run(context.stage("persons"))
