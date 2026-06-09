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

    Returns
    -------
    pandas.DataFrame
        Input frame with four additional columns:
        ``hts_id``, ``hts_household_id``, ``census_person_id``,
        ``census_household_id``.
    """
    out = persons.copy()
    # provenance -> writer id fields (integration spec Section 4):
    # the MiD donor is the analog of the HTS donor; popsim's own ids
    # stand in for the census ids.
    out["hts_id"] = out["source_person_id"]
    out["hts_household_id"] = out["source_household_id"]
    out["census_person_id"] = out["person_id"].astype("string")
    out["census_household_id"] = out["household_id"].astype("string")
    return out


def configure(context):
    context.stage("data.census.filtered", alias="persons")


def execute(context):
    return run(context.stage("persons"))
