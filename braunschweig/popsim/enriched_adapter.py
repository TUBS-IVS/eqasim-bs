"""synpp stage: adapt popsim persons to the MATSim writer PERSON_FIELDS schema.

Aliased to synthesis.population.enriched for popsim_mid. Maps the producer-agnostic
source_* provenance to the writer's id fields (the writer stays unchanged). No HTS
matching, no synthesis.population.matched dependency.

The MiD donor surrogates (source_person_id / source_household_id, pseudonymised by
braunschweig.popsim.assembly.assign_donor_surrogates) fill the hts_id /
hts_household_id writer slots; the synthetic integer ids (person_id / household_id
assigned by synthesis.population.sampled) fill census_person_id / census_household_id.

Data-protection note: popsim_mid 100 m cell ids embed the raw MiD H_ID and P_ID
in the popsim string format ``<cell>_<H_ID>_<occurrence>[_<P_ID>]``.
synthesis.population.sampled copies those embedding strings to census_*
BEFORE reassigning integer ids. To prevent the embedding strings from reaching
the output, this adapter ALWAYS overwrites census_person_id and census_household_id
with the current synthetic integer ids (person_id / household_id). For popsim_mid
there is no separate per-person census record, so the synthetic integer id is the
natural non-leaking record id. This replaces the earlier "only-if-absent" guard.
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
        Typically called after ``synthesis.population.sampled``, which has
        already assigned integer ``person_id`` / ``household_id`` and copied
        the original (leaking) popsim string ids to ``census_*``.

    Returns
    -------
    pandas.DataFrame
        Input frame with:
        - ``hts_id`` / ``hts_household_id`` set from ``source_*`` (always) --
          these are numeric donor surrogates (java.lang.Long).
        - ``census_person_id`` / ``census_household_id`` set from the current
          integer ``person_id`` / ``household_id`` (ALWAYS overwritten so the
          embedding strings from sampled do not reach the output).
    """
    out = persons.copy()
    # hts_id / hts_household_id: the MiD donor surrogate is the analog of the HTS
    # donor id.  Always (re-)set so callers that provide custom source_* values
    # are honoured.  Surrogates are integers so long_or_string_type will emit
    # java.lang.Long (clean, no re-identification risk).
    out["hts_id"] = out["source_person_id"]
    out["hts_household_id"] = out["source_household_id"]
    # census_person_id / census_household_id: ALWAYS set to the current integer
    # person_id / household_id.  synthesis.population.sampled has previously
    # copied the leaking popsim embedding strings to these columns; we overwrite
    # them here so the output carries only the non-leaking synthetic integer ids.
    # For popsim_mid the synthetic integer id is the natural census record id.
    out["census_person_id"] = out["person_id"]
    out["census_household_id"] = out["household_id"]
    return out


def configure(context):
    # Read from synthesis.population.sampled (not the raw producer): sampled
    # carries the reassigned integer person_id + the preserved census_* ids,
    # so the writer receives the fully id-remapped and sampling-applied frame.
    context.stage("synthesis.population.sampled", alias="persons")


def execute(context):
    return run(context.stage("persons"))
