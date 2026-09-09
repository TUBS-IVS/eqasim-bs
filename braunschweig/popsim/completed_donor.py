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

The diary plan match (``diary_plan_match.reassign_diaryless_plan_sources``, issue
#365, plan-structure-fix Task 3) and the plan-source diary fact attachment
(``diary_facts.attach_plan_source_facts``) run AFTER member completion + weekend
match, in that order, CONTINUING the same seeded RNG instance -- they are new
steps appended to the byte-identity contract above, never inserted before or
between the existing two. The stage now also depends on the MiD Wege (trip)
table (``mid.load_mid_wege``) and five additional flags (diary_plan_match,
exclude_holiday_plan_sources, exclude_rbw_legs, drop_leading_arrive_home_leg,
diary_match_hard_employment);
it remains sampling- and controls-independent, so it is still shareable across
runs via the cache_share store. The eight ``src_*`` plan-source fact columns are
attached to ``persons`` ALWAYS (even with diary_plan_match OFF) -- they are
facts about the plan source's diary, not behaviour, so the OFF path stays
byte-identical only in ``source_H_ID``/``source_P_ID``.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
import pandas as pd

from braunschweig.popsim import diary_facts, diary_plan_match
from braunschweig.popsim import mid
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim import weekend_plan_match

logger = logging.getLogger(__name__)

# RNG offset shared by member completion, weekend-plan match AND the diary plan
# match. Kept disjoint from the +74511 attribute-imputation stream in
# popsim.stage.build_persons. Must NOT change: it defines the donor draw
# (byte-identity contract).
COMPLETION_RNG_OFFSET = 74513

# Filename of the weekend-plan-match trace persisted into this stage's cache dir.
WEEKEND_TRACE_FILE = "weekend_plan_match_trace.parquet"
# Filename of the diary-plan-match trace persisted into this stage's cache dir.
DIARY_TRACE_FILE = "diary_plan_match_trace.parquet"

# synpp hashes only THIS file's own source; every own-package sibling helper that
# shapes the donor build must therefore be folded into validate()'s token, or
# editing the sibling silently reuses this stage's stale cached output (same
# hazard documented in braunschweig.popsim.trips_stage.validate(), whose pattern
# this mirrors). diary_facts / diary_plan_match / weekend_plan_match are already
# bound module-level names in this file; member_completion and mid.donor are
# reached only transitively (via mid.load_completed_donor -> member_completion.
# complete_members, and via mid.load_mid_wege internally) and are not imported
# here directly, so they are named and imported lazily inside validate().
# seedmod (braunschweig.popsim.seed) is included because it OWNS WEEKDAY_KERNWO, the
# constant diary_plan_match.build_realisable_pool filters the remap donor pool with:
# changing it changes which donors this stage can draw from, so it must devalidate
# this stage's cache like any other build input.
_HELPER_MODULES = (
    diary_facts,
    diary_plan_match,
    seedmod,
    weekend_plan_match,
    # The braunschweig.popsim.mid PACKAGE __init__, whose load_completed_donor /
    # load_mid_wege / project_completed_seed ARE this stage's build. Its submodule
    # mid.donor is covered separately below; a package entry hashes only its own
    # __init__.py, never its submodules (#327 helper-hash re-audit).
    mid,
)
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.popsim.member_completion",
    "braunschweig.popsim.mid.donor",
    # Leaf module holding the KEY_* names AND (since issue #374) the DEFAULT_* values this
    # stage's configure() declares its plan-structure options with. Imported inside
    # configure()/execute() to avoid a heavy top-level import of the popsim stage package, and
    # hashed here for the same reason braunschweig.popsim.trips_stage hashes it: a renamed key or
    # a changed declared default must not serve a donor built under the old option surface.
    "braunschweig.popsim.stage.config_keys",
)


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
    diary_report: Optional[object] = None


def build_completed_donor(
    mid_dir: Union[str, Path],
    *,
    random_seed: int,
    seed_day_filter: Optional[Sequence[int]],
    weekend_plan_match_on: bool,
    trace_path: Optional[Union[str, Path]] = None,
    diary_plan_match_on: bool = True,
    exclude_holiday_plan_sources: bool = True,
    exclude_rbw_legs: bool = True,
    drop_leading_arrive_home_leg: bool = True,
    diary_match_hard_employment: bool = True,
    diary_trace_path: Optional[Union[str, Path]] = None,
) -> CompletedDonor:
    """Build the completed MiD donor frames (member completion + weekend match +
    diary plan match), and attach the plan-source diary facts.

    Parameters
    ----------
    mid_dir:
        Directory with ``MiD2023_Haushalte.csv`` / ``MiD2023_Personen.csv`` /
        ``MiD2023_Wege.csv``.
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
    diary_plan_match_on:
        When True (default), remap the plan source of any person whose source has
        no realisable MiD diary to a matched weekday donor with one
        (``diary_plan_match.reassign_diaryless_plan_sources``). REQUIRES the MiD
        person column ``mobil`` always, and ``feiertag`` additionally when
        ``exclude_holiday_plan_sources`` is True (fails fast BEFORE the Wege table
        is loaded, see below; ``mobil_diff`` is an optional diagnostic column that
        diary_plan_match never reads, so it is NOT required). OFF is byte-identical
        in ``source_H_ID``/``source_P_ID`` to today; the ``src_*`` fact columns are
        still attached (see below).
    exclude_holiday_plan_sources:
        When True (default) and ``diary_plan_match_on``, treat a plan source
        reported on a public holiday (``feiertag == 1``) as not realisable and
        remap it (SrV reference days exclude public holidays). Ignored when
        ``diary_plan_match_on`` is False.
    exclude_rbw_legs:
        When True (default) and ``diary_plan_match_on``, treat a plan source whose
        diary consists only of rbW summary legs as not realisable and remap it.
        Ignored when ``diary_plan_match_on`` is False.
    drop_leading_arrive_home_leg:
        When True (default) and ``diary_plan_match_on``, drop a diary's leading
        arrive-home leg when counting direct legs, and remap a plan source whose
        diary becomes empty after the drop. Ignored when ``diary_plan_match_on``
        is False.
    diary_match_hard_employment:
        When True (default) and ``diary_plan_match_on``, the ``employed`` match key
        is NEVER relaxed while re-drawing a plan source, so a person can only
        inherit the diary of a donor of their own employment class (issue #368).
        Ignored when ``diary_plan_match_on`` is False. Only the diary match is
        affected: the weekend-plan match above keeps the unconstrained ladder, and
        ``match_person`` draws exactly ONE weighted value per call either way, so the
        shared completion RNG stream is unchanged (byte-identity contract above).
    diary_trace_path:
        Where to write the diary-plan-match trace parquet (only when matching is
        on and a path is given). ``None`` -> trace not persisted (e.g. unit tests).

    Notes
    -----
    The MiD Wege (trip) table is loaded (``mid.load_mid_wege``) and its per-person
    diary facts (``diary_facts.compute_diary_facts``) computed UNCONDITIONALLY,
    regardless of ``diary_plan_match_on``: the eight ``src_*`` plan-source fact
    columns (``diary_facts.FACT_COLUMNS`` prefixed ``src_``) are attached to
    ``persons`` ALWAYS -- they are facts about the plan source's diary, not
    behaviour, so the OFF path stays byte-identical only in ``source_H_ID`` /
    ``source_P_ID`` (and downstream plans), not in the input files read. On the
    real MiD delivery, loading the full Wege table takes roughly a minute and
    ~2 GB; acceptable for a once-per-run shared stage. The ``mobil``/``feiertag``
    presence check therefore runs BEFORE this load (fail fast, not after paying
    for it). The eight ``src_*`` columns propagate unchanged into the synthetic
    persons frame downstream (``assembly.build_persons``); this is intentional --
    later tasks consume them there.
    """
    # ONE seeded RNG, shared by member completion, weekend match AND the diary plan
    # match (byte-identity).
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

    # Fail fast BEFORE the ~1 min / ~2 GB Wege load below (R9): mobil is required
    # whenever the diary plan match runs; feiertag is required ONLY when holiday
    # exclusion is active (that is the only consumer of it, diary_plan_match.
    # classify_plan_sources). mobil_diff is an optional diagnostic column that
    # diary_plan_match never reads, so it is deliberately NOT required here.
    if diary_plan_match_on:
        required_mobility_cols = ["mobil"] + (["feiertag"] if exclude_holiday_plan_sources else [])
        missing_mobility_cols = [c for c in required_mobility_cols if c not in persons.columns]
        if missing_mobility_cols:
            raise KeyError(
                f"[completed_donor] diary_plan_match requires MiD person column(s) {missing_mobility_cols} "
                "(see mid.donor.MID_PERSON_OPTIONAL_COLS); the MiD delivery in "
                f"{mid_dir} lacks them -- set braunschweig.population.popsim.diary_plan_match: false "
                "to run without the diary plan match."
            )

    # Diary facts are derived from the MiD Wege table UNCONDITIONALLY (needed both
    # for the diary plan match below AND for the src_* fact columns attached
    # unconditionally further down -- see the docstring Notes).
    wege = mid.load_mid_wege(mid_dir)
    facts = diary_facts.compute_diary_facts(wege)

    diary_report = None
    if diary_plan_match_on:
        # completion_rng is DELIBERATELY shared with member completion + weekend
        # match above: all three draws form ONE entangled seeded stream -- do NOT
        # reseed it.
        persons, diary_trace, diary_report = diary_plan_match.reassign_diaryless_plan_sources(
            persons, persons, facts, rng=completion_rng,
            exclude_rbw_legs=exclude_rbw_legs,
            exclude_holidays=exclude_holiday_plan_sources,
            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
            hard_employment=diary_match_hard_employment,
        )
        if diary_trace_path is not None:
            diary_trace.to_parquet(diary_trace_path)
        logger.info("[completed_donor] diary_plan_match: %s", diary_report)

    # src_* plan-source fact columns are attached ALWAYS (facts, not behaviour).
    persons = diary_facts.attach_plan_source_facts(persons, facts)

    return CompletedDonor(
        households=households,
        persons=persons,
        completeness_report=completeness_report,
        completion_report=completion_report,
        weekend_report=weekend_report,
        diary_report=diary_report,
    )


def validate(context):
    """synpp validation token: md5 over the own-package + transitive helper modules.

    Same mechanism and boundary semantics as ``braunschweig.popsim.trips_stage.
    validate()`` (the pattern this mirrors): synpp's ``get_stage_hash`` hashes only
    THIS file's own source, so editing a sibling helper this stage's build actually
    depends on -- ``diary_facts``, ``diary_plan_match``, ``seed`` (which owns the
    ``WEEKDAY_KERNWO`` pool filter and the seed-column contract) and
    ``weekend_plan_match`` (own-package siblings imported directly above), plus
    ``member_completion`` and ``mid.donor`` (reached only transitively, via
    ``mid.load_completed_donor`` / ``mid.load_mid_wege``) -- would otherwise silently
    reuse this stage's stale cached output on a partial rerun. A deferred module that fails to import
    raises rather than being skipped -- skipping it would keep the stale cache
    alive exactly when the code is broken.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_module = importlib.import_module(module_name)
            deferred_source = inspect.getsource(deferred_module)
        except Exception as error:
            raise RuntimeError(
                f"completed_donor validate(): cannot hash the deferred helper module "
                f"{module_name!r} ({type(error).__name__}: {error}); it must not be "
                "skipped, because skipping it would silently reuse stale cached output."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    """Declare the completed-donor config dependencies.

    This stage depends ONLY on the MiD donor data, the random seed, the seed
    day-filter, the weekend-plan-match flag, and the diary-plan-match flags --
    NOT on controls, sampling, or work_dir. That narrow dependency set is what
    makes it shareable across ALL runs (incl. control-tier changes) via the
    cache_share store.
    """
    # From the LEAF module, not from the braunschweig.popsim.stage package that re-exports
    # it: the package's __init__ is this stage's own source dependency otherwise, and hashing
    # a ~3000-line downstream package would re-run this 49-minute donor build on every
    # unrelated popsim.stage edit. config_keys IS hashed (see
    # _DEFERRED_HELPER_MODULE_NAMES), so the narrower import is both cheaper and covered.
    from braunschweig.popsim.stage.config_keys import (
        KEY_DIARY_MATCH_HARD_EMPLOYMENT, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES,
        KEY_EXCLUDE_RBW_LEGS, KEY_MID, KEY_SEED_DAY_FILTER, KEY_WEEKEND_PLAN_MATCH,
    )
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_DIARY_MATCH_HARD_EMPLOYMENT, DEFAULT_DIARY_PLAN_MATCH,
        DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES,
        DEFAULT_EXCLUDE_RBW_LEGS, DEFAULT_SEED_DAY_FILTER, DEFAULT_WEEKEND_PLAN_MATCH,
    )
    context.config(KEY_MID)
    context.config("random_seed")
    context.config(KEY_SEED_DAY_FILTER, DEFAULT_SEED_DAY_FILTER)
    context.config(KEY_WEEKEND_PLAN_MATCH, DEFAULT_WEEKEND_PLAN_MATCH)
    context.config(KEY_DIARY_PLAN_MATCH, DEFAULT_DIARY_PLAN_MATCH)
    context.config(KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES)
    context.config(KEY_EXCLUDE_RBW_LEGS, DEFAULT_EXCLUDE_RBW_LEGS)
    context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG)
    # Issue #368: the un-relaxable employment boundary changes which donor a
    # diary-less plan source draws, so it belongs in THIS stage's config hash --
    # flipping it must rebuild the donor, not reuse the cached one. Declared here
    # only (like KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES): the popsim stage never reads it
    # and inherits the invalidation through its completed_donor stage dependency.
    context.config(KEY_DIARY_MATCH_HARD_EMPLOYMENT, DEFAULT_DIARY_MATCH_HARD_EMPLOYMENT)


def execute(context) -> CompletedDonor:
    """Run the MiD completed-donor build and persist the weekend + diary traces.

    Returns a :class:`CompletedDonor`; ``popsim.stage`` consumes it via
    ``context.stage("completed_donor")`` and reuses the frames for BOTH the
    PopulationSim seed and the expansion donor tables.
    """
    # From the LEAF module, not from the braunschweig.popsim.stage package that re-exports
    # it: the package's __init__ is this stage's own source dependency otherwise, and hashing
    # a ~3000-line downstream package would re-run this 49-minute donor build on every
    # unrelated popsim.stage edit. config_keys IS hashed (see
    # _DEFERRED_HELPER_MODULE_NAMES), so the narrower import is both cheaper and covered.
    from braunschweig.popsim.stage.config_keys import (
        KEY_DIARY_MATCH_HARD_EMPLOYMENT, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES,
        KEY_EXCLUDE_RBW_LEGS, KEY_MID, KEY_SEED_DAY_FILTER, KEY_WEEKEND_PLAN_MATCH,
    )
    mid_dir = context.config(KEY_MID)
    random_seed = int(context.config("random_seed"))
    # Same tri-state day-filter parsing as stage.execute: "off"/"all"/"none"/"" ->
    # no day filter (()), else None -> the loader's weekday default (1,2,3).
    _day_filter_cfg = str(context.config(KEY_SEED_DAY_FILTER)).strip().lower()
    seed_day_filter = () if _day_filter_cfg in ("off", "all", "none", "") else None
    weekend_plan_match_on = bool(context.config(KEY_WEEKEND_PLAN_MATCH))
    diary_plan_match_on = bool(context.config(KEY_DIARY_PLAN_MATCH))
    exclude_holiday_plan_sources = bool(context.config(KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES))
    exclude_rbw_legs = bool(context.config(KEY_EXCLUDE_RBW_LEGS))
    drop_leading_arrive_home_leg = bool(context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG))
    diary_match_hard_employment = bool(context.config(KEY_DIARY_MATCH_HARD_EMPLOYMENT))

    result = build_completed_donor(
        mid_dir,
        random_seed=random_seed,
        seed_day_filter=seed_day_filter,
        weekend_plan_match_on=weekend_plan_match_on,
        trace_path=Path(context.path()) / WEEKEND_TRACE_FILE if weekend_plan_match_on else None,
        diary_plan_match_on=diary_plan_match_on,
        exclude_holiday_plan_sources=exclude_holiday_plan_sources,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        diary_match_hard_employment=diary_match_hard_employment,
        diary_trace_path=Path(context.path()) / DIARY_TRACE_FILE if diary_plan_match_on else None,
    )

    # Surface the build reports as run info (also set on the consumer in popsim.stage
    # so they are present on a cache hit, where this execute does not run).
    context.set_info("member_completion_filled", result.completion_report.n_households_filled)
    context.set_info("member_completion_persons_added", result.completion_report.n_persons_added)
    context.set_info("seed_completeness_rate", result.completeness_report.completeness_rate)
    if result.diary_report is not None:
        context.set_info("diary_plan_match_remapped", result.diary_report.n_remapped)
        context.set_info("diary_plan_match_share", result.diary_report.share_remapped)
        context.set_info("diary_plan_match_crossed_employment_boundary",
                         result.diary_report.n_crossed_employment_boundary)
    return result
