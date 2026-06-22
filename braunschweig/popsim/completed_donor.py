"""The MiD completed-donor build, extracted from ``braunschweig.popsim.stage``.

Member completion + weekend-plan match are a sampling-independent, controls-
independent piece of the popsim_mid workflow: they depend ONLY on the MiD donor
data, the random seed, the seed day-filter, and the weekend-plan-match flag. This
module isolates that build so it can run as its own synpp stage (Tier B2) and be
shared across ALL runs via the cache_share store, instead of being recomputed
inside ``popsim.stage`` on every fresh cache.

Byte-identity is mandatory: member completion (``mid.load_completed_donor``) and
weekend-plan match (``weekend_plan_match.reassign_weekend_plan_sources``) share
ONE seeded RNG instance ``np.random.RandomState(random_seed + 74513)`` and MUST be
called in this exact order with that exact instance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
import pandas as pd

from braunschweig.popsim import mid
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim import weekend_plan_match

logger = logging.getLogger(__name__)

# RNG offset shared by member completion and weekend-plan match. Kept disjoint from
# the +74511 attribute-imputation stream in popsim.stage.build_persons. Must NOT
# change: it defines the donor draw (byte-identity contract).
COMPLETION_RNG_OFFSET = 74513

# Filename of the weekend-plan-match trace persisted into this stage's cache dir.
WEEKEND_TRACE_FILE = "weekend_plan_match_trace.parquet"


@dataclass
class CompletedDonor:
    """The completed MiD donor frames plus the build reports.

    ``households`` / ``persons`` are the attribute donor tables that feed BOTH the
    PopulationSim seed (via ``mid.project_completed_seed``) and the expansion (via
    ``assembly.build_persons``) -- ONE completion pass, so seed and expansion share
    the same fillers. The reports are returned so the consumer can ``set_info`` the
    fill rates on the run even when this stage is served from cache.
    """
    households: pd.DataFrame
    persons: pd.DataFrame
    completeness_report: seedmod.CompletenessReport
    completion_report: object
    weekend_report: Optional[object]


def build_completed_donor(
    mid_dir: Union[str, Path],
    *,
    random_seed: int,
    seed_day_filter: Optional[Sequence[int]],
    weekend_plan_match_on: bool,
    trace_path: Optional[Union[str, Path]] = None,
) -> CompletedDonor:
    """Build the completed MiD donor frames (member completion + weekend match).

    Parameters
    ----------
    mid_dir:
        Directory with ``MiD2023_Haushalte.csv`` / ``MiD2023_Personen.csv``.
    random_seed:
        Pipeline random seed. The single completion RNG is seeded with
        ``random_seed + COMPLETION_RNG_OFFSET``.
    seed_day_filter:
        The seed day filter when weekend-plan match is OFF (``None`` -> the loader's
        weekday default (1,2,3); an empty iterable -> no day filter). IGNORED when
        ``weekend_plan_match_on`` is True (that forces ALL reporting days, because
        the match needs weekend reporters in the donor).
    weekend_plan_match_on:
        When True, keep ALL reporting days and remap weekend reporters' plan sources
        to a matched weekday household; persist the trace to ``trace_path``.
    trace_path:
        Where to write the weekend-plan-match trace parquet (only when matching is
        on and a path is given). ``None`` -> trace not persisted (e.g. unit tests).
    """
    # ONE seeded RNG, shared by member completion and weekend match (byte-identity).
    completion_rng = np.random.RandomState(random_seed + COMPLETION_RNG_OFFSET)
    # Weekend-plan match needs weekend reporters in the donor, so it forces ALL kernwo
    # days, overriding seed_day_filter (mirrors stage.execute exactly).
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend_plan_match_on else seed_day_filter

    households, persons, completeness_report, completion_report = mid.load_completed_donor(
        mid_dir, completion_rng=completion_rng, day_filter_values=day_filter,
    )

    weekend_report = None
    if weekend_plan_match_on:
        # completion_rng is DELIBERATELY shared with member completion above: the two
        # draws form one entangled seeded stream -- do NOT reseed it.
        persons, weekend_trace, weekend_report = weekend_plan_match.reassign_weekend_plan_sources(
            households, persons, rng=completion_rng,
        )
        if trace_path is not None:
            weekend_trace.to_parquet(trace_path)
        logger.info("[completed_donor] weekend_plan_match: %s", weekend_report)

    logger.info(
        "[completed_donor] built %d households / %d persons "
        "(member completion: %d households filled, %d persons added; completeness %.3f).",
        len(households), len(persons),
        completion_report.n_households_filled, completion_report.n_persons_added,
        completeness_report.completeness_rate,
    )
    return CompletedDonor(
        households=households,
        persons=persons,
        completeness_report=completeness_report,
        completion_report=completion_report,
        weekend_report=weekend_report,
    )
