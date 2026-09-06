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

import logging

import pandas as pd

logger = logging.getLogger(__name__)

#: Plan-source diary facts (attached by braunschweig.popsim.diary_facts.
#: attach_plan_source_facts) exported as public person attributes, mapped to their
#: output column name. ``n_rbw_legs`` / ``rbw_distance_km`` describe the donor's
#: regular work-related trips (regelmaessige berufliche Wege, MiD W_RBW): they
#: explain why a person's realised plan can be short or empty, so they must be
#: analysable in persons.csv and readable in the MATSim population.
RBW_SOURCE_FACT_COLUMNS = {
    "src_n_rbw_legs": "rbw_legs_count",
    "src_rbw_distance_km": "rbw_distance_km",
}

#: Structural default for a person whose plan source carries no diary facts (no
#: rbW leg reported). Also the dtype selector for the exported column.
_RBW_FILL = {"rbw_legs_count": 0, "rbw_distance_km": 0.0}


def _attach_rbw_attributes(out: pd.DataFrame) -> pd.DataFrame:
    """Copy the present rbW plan-source facts to their public column names.

    ADDITIVE: a fact column that is absent (non-MiD producer, or a popsim run
    without the diary-facts attachment) produces no output column at all, so the
    output stays byte-identical to the legacy output. The number of rows filled
    with the structural default is logged as an explicit rate -- the fill applies
    to rows whose source fact is missing (e.g. agents injected onto the resident
    column set by the cordon merge) and must never fire silently.
    """
    for source_column, output_column in RBW_SOURCE_FACT_COLUMNS.items():
        if source_column not in out.columns:
            continue
        values = out[source_column]
        n_filled = int(values.isna().sum())
        fill = _RBW_FILL[output_column]
        out[output_column] = values.fillna(fill).astype(type(fill))
        logger.info("[enriched_adapter] %s -> %s: %d/%d rows (%.2f%%) filled with the "
                    "default %r (source fact missing)",
                    source_column, output_column, n_filled, len(out),
                    100.0 * n_filled / max(len(out), 1), fill)
    return out


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
        - ``rbw_legs_count`` (int) / ``rbw_distance_km`` (float) copied from the
          plan-source facts ``src_n_rbw_legs`` / ``src_rbw_distance_km`` ONLY
          when those are present; absent facts leave the output unchanged.
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
    # rbW plan-source facts -> public person attributes (only when attached).
    out = _attach_rbw_attributes(out)
    return out


def configure(context):
    # Read from synthesis.population.sampled (not the raw producer): sampled
    # carries the reassigned integer person_id + the preserved census_* ids,
    # so the writer receives the fully id-remapped and sampling-applied frame.
    context.stage("synthesis.population.sampled", alias="persons")


def execute(context):
    return run(context.stage("persons"))
