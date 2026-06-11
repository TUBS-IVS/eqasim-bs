"""Shared exclusion of injected freight agents from person-travel analyses.

Freight agents (german-wide-freight injection, person ids prefixed
``freight_``, subpopulation ``freight``) are trip creators, not residents:
every modal-split / distance / demographic KPI compares the simulated
RESIDENT population against MiD/Zensus references, so freight trips must be
removed before aggregation. The exclusion is logged (never silent).
"""
import logging

logger = logging.getLogger(__name__)

FREIGHT_PERSON_PREFIX = "freight_"


def drop_freight_agents(df, person_column="person_id", label="trips"):
    if person_column not in df.columns:
        # No-silent-fallback rule: if the person column disappears (e.g. an
        # upstream rename), freight agents would silently leak into every KPI.
        # Warn loudly instead of skipping quietly.
        logger.warning(
            "[%s] column '%s' not found -- freight agents NOT filtered from this table",
            label, person_column)
        return df
    mask = df[person_column].astype(str).str.startswith(FREIGHT_PERSON_PREFIX)
    excluded = int(mask.sum())
    if excluded > 0:
        logger.info("[%s] excluded %d freight agents (%.2f%%) from person-travel analysis",
                    label, excluded, 100.0 * excluded / len(df))
    return df.loc[~mask].copy()
