"""synpp stage: popsim_mid trips in the eqasim synthesis.population.trips contract.

Aliased to synthesis.population.trips for the popsim_mid workflow. Builds the
validated MiD trip table via braunschweig.popsim.trips.build_validated_trip_table,
applies the SAME per-person departure-time jitter as synthesis/population/trips.py
(one random offset per person, bounded by min(1800, first_departure_time), applied
identically to every trip in that person's chain), and derives euclidean_distance
from MiD wegkm_imp using the ENTD detour factor 1.3.

Per-person jitter formula (matches synthesis/population/trips.py exactly):
    interval = min(1800.0, first_departure_time_per_person)
    offset    = random_sample_per_person * interval * 2.0 - interval
                -> range [-interval, +interval)
    All times for a person are shifted by the SAME offset to preserve ordering.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import logging

import numpy as np
import pandas as pd

from braunschweig.popsim import closure_dwell as _closure_dwell
from braunschweig.popsim import diary_facts as _diary_facts
from braunschweig.popsim import escort_pairing as _escort_pairing
from braunschweig.popsim import plan_validation as _plan_validation
from braunschweig.popsim import trips as popsim_trips
from braunschweig.popsim.closure_dwell import CLOSURE_SEED_OFFSET, ClosureDwellModel
# The passive-escort pairing gap default lives with the trip build (braunschweig.popsim.trips)
# and is re-exported through it here rather than re-typed, so this stage and map_purpose can
# never disagree on the threshold a run uses when the config leaves it unset.
from braunschweig.popsim.trips import DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES
from braunschweig.popsim.plan_validation import HOME_CLOSURE_DWELL_S
# Authoritative plan-time bound lives in plan_validation (where bound-exceeding
# persons are classified unfixable + resampled); re-exported here for the final
# backstop assertion and for tests referencing trips_stage.MAX_PLAN_TIME_SECONDS.
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

# synpp hashes only THIS file's source; every helper that shapes the trip table must
# therefore be folded into validate()'s token or an edit to it silently reuses the stale
# cached trips. This stage had NO token at all until the 2026-08-19 hazard: the W_ZWECK
# purpose fix changed trips.py and this stage's cache would never have noticed
# (docs/runs/smoke-control-fit-03101-v2-2026-08-19.yml). Boundary semantics follow the
# canonical statement in braunschweig.popsim.stage.validate(); the deferred names cover
# the function-level `from braunschweig.popsim import sources` plus the source adapters
# one level deep, whose build_trips() IS the trip construction for the active donor.
# closure_dwell shapes the synthesised return-home trip's dwell time (and therefore
# every closed chain's times), so it belongs in the token exactly like trips.py and
# plan_validation.py. diary_facts is hashed although this stage never CALLS it: its
# facts decide (in completed_donor) which donor diaries are realisable plan sources
# and therefore which diaries this stage builds trips from, so a change there changes
# this stage's input semantics; over-hashing only costs a cache rebuild, while
# under-hashing silently serves stale trips (the 2026-08-19 hazard above).
_HELPER_MODULES = (
    popsim_trips,
    _plan_validation,
    _closure_dwell,
    _diary_facts,
    # escort_pairing decides WHICH adult leg each passive escort leg (W_ZWECK 13) is paired
    # with, and therefore the purpose the child's leg receives under escort_passive_from_adult
    # (issue #372); trips.py only applies the mapping. A change to the pairing rule changes this
    # stage's trip purposes without changing trips.py, so it is hashed for exactly the reason
    # trips.py itself is.
    _escort_pairing,
)
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.popsim.sources",
    "braunschweig.popsim.sources.base",
    "braunschweig.popsim.sources.entd",
    "braunschweig.popsim.sources.entd_diary_matching",
    "braunschweig.popsim.sources.entd_trips",
    "braunschweig.popsim.sources.mid",
    # Leaf module holding this stage's config-key constants (imported inside
    # configure()/execute() to avoid a heavy top-level import of the popsim stage
    # package). Hashed so a key rename or a changed declared default cannot serve
    # a cached trip table built under the old option surface.
    "braunschweig.popsim.stage.config_keys",
)


def validate(context):
    """synpp validation token: md5 over the helper modules above.

    Same mechanism and boundary semantics as ``braunschweig.popsim.stage.validate()``
    (the single canonical statement); kept minimal here because this stage's helper
    surface is small. A deferred module that fails to import raises rather than being
    skipped -- dropping it would keep the stale cache alive exactly when the code is
    broken.
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
                f"trips_stage validate(): cannot hash the deferred helper module "
                f"{module_name!r} ({type(error).__name__}: {error}); it must not be "
                "skipped, because skipping it would silently reuse stale cached output."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()

# The 11-column output contract from synthesis/population/trips.py; downstream
# stages (synthesis/population/activities.py) expect exactly these column names.
CONTRACT = [
    "person_id",
    "trip_index",
    "departure_time",
    "arrival_time",
    "preceding_purpose",
    "following_purpose",
    "is_first_trip",
    "is_last_trip",
    "trip_duration",
    "activity_duration",
    "mode",
]

# ENTD straight-line detour factor: routed distance / straight-line distance.
# Canonical project-wide constant (braunschweig.constants); local alias kept
# for the existing references.
from braunschweig.constants import ROUTED_DETOUR_FACTOR as DETOUR_FACTOR

# Maximum forward shift of the per-person departure-time jitter (interval is
# min(1800, first_departure), offset in [-interval, +interval)). The PlanValidator
# enforces MAX_PLAN_TIME_SECONDS BEFORE the jitter, so a chain ending just below
# the bound may legitimately cross it by up to this margin after jittering; the
# post-jitter backstop therefore asserts against bound + margin.
JITTER_MAX_SECONDS = 1800.0


def _assert_time_bound(table, *, max_seconds=MAX_PLAN_TIME_SECONDS + JITTER_MAX_SECONDS):
    for col in ("departure_time", "arrival_time"):
        n_bad = int((table[col] > max_seconds).sum())
        assert n_bad == 0, (
            f"[trips_stage] {n_bad} rows have {col} exceeding {max_seconds}s "
            f"(~{max_seconds/3600:.1f}h, = {MAX_PLAN_TIME_SECONDS}s validator bound "
            f"+ {JITTER_MAX_SECONDS}s jitter margin); coded times must be NaN'd + resampled.")


def _resolve_resample_cell_col(persons: pd.DataFrame) -> str | None:
    """Pick the cell column for the LEGACY same-cell resample fallback.

    Stage B (build_validated_trip_table) replaces unfixable persons by
    ATTRIBUTE-matched donor chains and only falls back to the legacy same-cell
    resample when the persons frame carries no ``sex`` column; this cell
    column parameterises that fallback path.  popsim_mid persons (from
    braunschweig.popsim.stage via synthesis.population.sampled, which passes
    all columns through) carry ``ZENSUS100m``; other producers may only carry
    ``commune_id``.  The choice is logged so a silent downgrade to the coarser
    column is impossible.
    """
    for candidate in ("ZENSUS100m", "commune_id"):
        if candidate in persons.columns:
            logger.info("[trips_stage] resample cell column: %s", candidate)
            return candidate
    logger.warning(
        "[trips_stage] persons frame carries neither ZENSUS100m nor commune_id; "
        "should the legacy same-cell resample fallback be taken (persons frame "
        "without 'sex'), unfixable persons would become home-only (trip-less)."
    )
    return None


# Accepted values of the closure_dwell_model option (config key
# braunschweig.population.popsim.closure_dwell_model).
CLOSURE_DWELL_MODELS = ("empirical", "fixed_1h")

# Default minimum observations per (purpose x arrival band) cell of the empirical
# closure-dwell model (config key
# braunschweig.population.popsim.closure_dwell_min_obs). Mirrors
# ClosureDwellModel.from_trips' own default; declared here because this module owns
# the stage-level default the config key is registered with.
DEFAULT_CLOSURE_DWELL_MIN_OBS = 30


def _donor_diary_frame(persons: pd.DataFrame) -> pd.DataFrame:
    """Return one surrogate person per distinct donor diary used by ``persons``.

    The empirical closure dwell model must be estimated on the DONOR diaries, not
    on the synthetic population: a donor diary executed by 500 synthetic persons
    is still ONE observation of that day's activity durations, and pooling it 500
    times would weight the empirical distribution by the synthetic expansion
    factor instead of the survey.  This helper therefore reduces ``persons`` to
    its unique donor-key pairs and gives each a surrogate ``person_id`` (0..n-1)
    for :func:`braunschweig.popsim.trips.build_trip_table`.

    The donor keys are ``(source_H_ID, source_P_ID)`` when present -- the plan
    source a synthetic person actually executes after member completion / the
    weekend and diary plan matches -- and ``(H_ID, P_ID)`` otherwise; the same
    precedence ``build_trip_table`` itself applies, so the pooled diaries are
    exactly the diaries the trip table is built from.  The path taken is logged
    (no silent key downgrade).

    Parameters
    ----------
    persons:
        Synthetic persons with ``H_ID`` / ``P_ID`` (optionally ``source_H_ID`` /
        ``source_P_ID``).

    Returns
    -------
    pd.DataFrame
        Columns ``person_id`` (surrogate, 0..n-1), ``H_ID``, ``P_ID``; one row per
        distinct donor diary.
    """
    has_source = "source_H_ID" in persons.columns and "source_P_ID" in persons.columns
    household_key = persons["source_H_ID"] if has_source else persons["H_ID"]
    person_key = persons["source_P_ID"] if has_source else persons["P_ID"]
    logger.info(
        "[trips_stage] closure dwell donor diaries keyed by %s",
        "(source_H_ID, source_P_ID)" if has_source else "(H_ID, P_ID)",
    )
    pairs = (
        pd.DataFrame({"H_ID": household_key.to_numpy(), "P_ID": person_key.to_numpy()})
        .drop_duplicates()
        .reset_index(drop=True)
    )
    pairs.insert(0, "person_id", np.arange(len(pairs)))
    n_persons = len(persons)
    n_diaries = len(pairs)
    logger.info(
        "[trips_stage] closure dwell donor diaries: %d unique donor diaries / %d persons (%.1f%%)",
        n_diaries, n_persons, 100.0 * n_diaries / max(n_persons, 1),
    )
    return pairs


def build_closure_dwell_model(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    closure_dwell_model: str,
    random_seed: int,
    closure_dwell_min_obs: int = DEFAULT_CLOSURE_DWELL_MIN_OBS,
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    explicit_round_trip_purposes: bool = True,
    exclude_rbw_legs: bool = False,
    drop_leading_arrive_home_leg: bool = False,
    w_zweck_10_as_leisure: bool = False,
    escort_passive_from_adult: bool = False,
    passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
):
    """Build the :class:`ClosureDwellModel` selected by ``closure_dwell_model``.

    ``"fixed_1h"`` returns the constant-dwell model (``HOME_CLOSURE_DWELL_S``),
    i.e. the behaviour that predates issue #367.  ``"empirical"`` estimates the
    dwell distribution from the DONOR diaries this stage builds trips from (see
    :func:`_donor_diary_frame`), pooled by (following purpose, arrival band).

    The donor trip table is built with the SAME purpose/leg flags as the main
    trip table, because the empirical pools are stratified by
    ``following_purpose``: building the pools with a different purpose
    vocabulary would send every draw of a flag-specific purpose (e.g.
    ``"escort"``) into the model's purpose-marginal fallback.

    Parameters
    ----------
    persons:
        Synthetic persons (donor keys only are used).
    mid_wege:
        The donor MiD Wege table.
    closure_dwell_model:
        One of :data:`CLOSURE_DWELL_MODELS`.
    random_seed:
        Base seed; the model draws from
        ``RandomState(random_seed + CLOSURE_SEED_OFFSET)``, a child stream
        decorrelated from the jitter / resample / imputation streams.
    closure_dwell_min_obs:
        Minimum observations a ``(purpose, arrival band)`` cell must hold to be
        drawn from directly; a thinner cell falls back to the purpose marginal
        (config key ``braunschweig.population.popsim.closure_dwell_min_obs``).
        Must be a positive integer. Inert for ``"fixed_1h"``.
    w_zweck_10_as_leisure:
        If True (issue #373, ADR-0111), remap W_ZWECK 10 ("anderer Zweck") to
        ``"leisure"`` (forwarded to the donor trip table build). Must match the
        value the main trip table is built with -- otherwise the empirical
        dwell pools are stratified by a DIFFERENT purpose vocabulary than the
        table they are drawn into, sending every W_ZWECK-10-following draw into
        the wrong purpose's pool. Default False keeps the OFF path
        byte-identical.
    escort_passive_from_adult:
        If True (issue #372, ADR-0112), a paired passive escort leg takes the
        accompanying adult's purpose (forwarded to the donor trip table build).
        Must match the value the main trip table is built with, for exactly the
        reason ``w_zweck_10_as_leisure`` must: the empirical pools are
        stratified by ``following_purpose``, so a donor table built with a
        different passive-escort vocabulary would send those draws into the
        wrong purpose's pool. Default False keeps the OFF path byte-identical.
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for the pairing (forwarded to
        the donor trip table build). Inert unless
        ``escort_passive_from_adult`` is True.

    Returns
    -------
    ClosureDwellModel

    Raises
    ------
    ValueError
        If ``closure_dwell_model`` is not one of :data:`CLOSURE_DWELL_MODELS`,
        or if ``closure_dwell_min_obs`` is not a positive integer.
    """
    if int(closure_dwell_min_obs) < 1:
        raise ValueError(
            f"[trips_stage] closure_dwell_min_obs={closure_dwell_min_obs!r} must be a "
            "positive integer (config key "
            "braunschweig.population.popsim.closure_dwell_min_obs); a cell threshold "
            "below 1 would make the purpose-marginal fallback unreachable.")
    if closure_dwell_model == "fixed_1h":
        return ClosureDwellModel.fixed(HOME_CLOSURE_DWELL_S)
    if closure_dwell_model != "empirical":
        raise ValueError(
            f"[trips_stage] closure_dwell_model={closure_dwell_model!r} is not supported; "
            f"expected one of {list(CLOSURE_DWELL_MODELS)} (config key "
            f"braunschweig.population.popsim.closure_dwell_model)."
        )

    donor_trips = popsim_trips.build_trip_table(
        _donor_diary_frame(persons), mid_wege,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
    )
    return ClosureDwellModel.from_trips(
        donor_trips, rng=np.random.RandomState(random_seed + CLOSURE_SEED_OFFSET),
        min_obs=int(closure_dwell_min_obs),
    )


def _log_closure_share(table: pd.DataFrame) -> None:
    """Log how many trips / persons carry a SYNTHESISED chain closure.

    The closure is a modelling assumption, not observed behaviour, so its share is
    reported as an explicit rate (CLAUDE.md fallback transparency) rather than left
    implicit in the trip count.
    """
    if "is_synthetic_closure" not in table.columns:
        logger.warning(
            "[trips_stage] no 'is_synthetic_closure' column in the trip table; the "
            "synthetic-closure share cannot be reported (PlanValidator.repair_trips "
            "always adds it -- a missing column means repair was skipped)."
        )
        return
    closure = table["is_synthetic_closure"].fillna(False).astype(bool)
    n_trips = len(table)
    n_persons = table["person_id"].nunique()
    n_closure = int(closure.sum())
    n_persons_closed = table.loc[closure, "person_id"].nunique()
    logger.info(
        "[trips_stage] synthetic closure trips: %d/%d trips (%.1f%%); persons closed: %d/%d (%.1f%%)",
        n_closure, n_trips, 100.0 * n_closure / max(n_trips, 1),
        n_persons_closed, n_persons, 100.0 * n_persons_closed / max(n_persons, 1),
    )


def apply_per_person_jitter(table: pd.DataFrame, random_seed: int) -> pd.DataFrame:
    """Apply the eqasim per-person departure-time jitter to a trips table.

    Replicates the jitter formula from ``synthesis/population/trips.py`` exactly:

      interval = min(1800.0, first_departure_time_per_person)
      offset   = random_sample * interval * 2.0 - interval
                 (one draw per person, repeated for every trip in the chain)

    Both ``departure_time`` and ``arrival_time`` are shifted by the same offset
    (preserving within-chain ordering) and rounded to integer seconds.

    This function is factored out of :func:`run` so that the ENTD donor adapter
    (``braunschweig.popsim.sources.entd.EntdSource.build_trips``) can apply the
    identical jitter without duplicating the formula.

    Parameters
    ----------
    table:
        Trip table with at least ``person_id``, ``departure_time``,
        ``arrival_time`` (numeric, seconds).  Should be sorted by
        ``(person_id, departure_time)`` before calling so that ``trip_index``
        order is preserved.
    random_seed:
        Integer seed for ``np.random.RandomState``.

    Returns
    -------
    pd.DataFrame
        The input table (modified in-place) with jittered and rounded
        departure/arrival times.  The table is returned for chaining.
    """
    random = np.random.RandomState(random_seed)

    person_order = table["person_id"].unique()

    counts = (
        table[["person_id"]]
        .groupby("person_id", sort=False)
        .size()
        .reindex(person_order)
        .values
    )
    interval = (
        table[["person_id", "departure_time"]]
        .groupby("person_id", sort=False)["departure_time"]
        .min()
        .reindex(person_order)
        .values
    )
    interval = np.minimum(1800.0, interval)

    per_person_raw = random.random_sample(size=(len(counts),))
    per_person_offset = per_person_raw * interval * 2.0 - interval
    offset = np.repeat(per_person_offset, counts)

    table["departure_time"] = np.round(table["departure_time"] + offset)
    table["arrival_time"] = np.round(table["arrival_time"] + offset)

    assert (table["departure_time"] >= 0.0).all(), (
        "departure_time must be non-negative after jitter; "
        "check that min(1800, first_departure) clipping is correct."
    )
    assert (table["arrival_time"] >= 0.0).all(), (
        "arrival_time must be non-negative after jitter."
    )
    return table


def run(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    random_seed: int,
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    explicit_round_trip_purposes: bool = True,
    exclude_rbw_legs: bool = False,
    drop_leading_arrive_home_leg: bool = False,
    closure_dwell_model: str = "fixed_1h",
    closure_dwell_min_obs: int = DEFAULT_CLOSURE_DWELL_MIN_OBS,
    w_zweck_10_as_leisure: bool = False,
    escort_passive_from_adult: bool = False,
    passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
) -> pd.DataFrame:
    """Build popsim_mid trips in the synthesis.population.trips 11-column contract.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id``, ``H_ID``, ``P_ID``.
    mid_wege:
        MiD 2023 Wege with at least ``H_ID``, ``P_ID``, ``W_ID``, ``W_ZWECK``,
        ``hvm_imp``, ``W_SZS``, ``W_SZM``, ``W_AZS``, ``W_AZM``. Optional:
        ``wegkm_imp`` (routed km; used to derive ``euclidean_distance``).
    random_seed:
        Integer seed for the per-person departure-time jitter RNG.
    escort_purpose:
        map MiD W_ZWECK {6, 13} to the dedicated 'escort' purpose (issue #201).
    escort_passive_education:
        map the passive escort leg (MiD W_ZWECK 13) to 'education' instead of
        'escort' (issue #256). Requires escort_purpose=True.
    exclude_rbw_legs:
        drop rbW legs (``W_RBW == 1``, the regelmaessiger-beruflicher-Weg summary
        records) before the join (issue #366). Requires the ``W_RBW`` column.
        Default False keeps the OFF path byte-identical.
    drop_leading_arrive_home_leg:
        drop a donor's leading "arrive home from elsewhere" leg (``W_SO1 == 2``
        with a home ``W_ZWECK``), which precedes the observed diary window
        (issue #366). Requires the ``W_SO1`` column. Default False keeps the OFF
        path byte-identical.
    closure_dwell_model:
        dwell-time model for the SYNTHESISED return-home trip that closes a chain
        not ending at home (issue #367): ``"empirical"`` draws the dwell from the
        donor diaries' observed activity durations (per following purpose and
        arrival band), ``"fixed_1h"`` (default) keeps the constant
        ``HOME_CLOSURE_DWELL_S``. Any other value raises ``ValueError``.
    closure_dwell_min_obs:
        minimum observations a (purpose x arrival band) cell of the EMPIRICAL
        dwell model must hold to be drawn from directly; thinner cells fall back
        to the purpose marginal (rate logged). Positive integer, default
        :data:`DEFAULT_CLOSURE_DWELL_MIN_OBS`; inert for ``"fixed_1h"``.
    w_zweck_10_as_leisure:
        remap MiD W_ZWECK 10 ("anderer Zweck") to the ``"leisure"`` purpose
        instead of ``"other"`` (issue #373, ADR-0111), following MiD's own
        hwzweck1 fold (100% of code-10 legs fold to 6 Freizeit; see the
        committed evidence table ``mid2023_w_zweck_by_hwzweck1.csv``). Forwarded
        to ``build_closure_dwell_model`` and ``build_validated_trip_table`` /
        ``map_purpose``. Default False keeps the OFF path byte-identical; the
        production default is configured by ``braunschweig.popsim.stage.
        config_keys.DEFAULT_W_ZWECK_10_AS_LEISURE``.
    escort_passive_from_adult:
        give a PAIRED passive escort leg (MiD W_ZWECK 13) the purpose derived
        from the accompanying adult's W_ZWECK instead of the flat
        ``escort_passive_education`` relabel (issue #372, ADR-0112); an
        UNPAIRED one keeps that relabel. Requires ``escort_purpose=True`` and
        the MiD Wege columns ``H_ID``/``P_ID``/``W_ID``/``W_SZS``/``W_SZM``/
        ``HP_ALTER``. Forwarded to ``build_closure_dwell_model`` and
        ``build_validated_trip_table`` / ``map_purpose``. Default False keeps
        the OFF path byte-identical; the production default is configured by
        ``braunschweig.popsim.stage.config_keys.
        DEFAULT_ESCORT_PASSIVE_FROM_ADULT``.
    passive_pair_max_gap_minutes:
        maximum |departure-time gap| in MINUTES for a passive leg to count as
        paired (config key
        ``escort_passive_pair_max_gap_minutes``); inert while
        ``escort_passive_from_adult`` is False. Default
        :data:`DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES`.

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, MiD trip), columns: the 11-column
        synthesis.population.trips contract + ``euclidean_distance`` (metres,
        straight-line = wegkm_imp * 1000 / DETOUR_FACTOR) when wegkm_imp is
        present, plus ``trip_key`` (traceability) and any remaining MiD extras.
    """
    # Resample unfixable persons (e.g. NaN-time rbW/kA records, MiD codes 701/99)
    # from same-cell donors so no NaN time and no multi-day timestamp survives.
    resample_cell_col = _resolve_resample_cell_col(persons)
    # Built BEFORE the trip table: the empirical model must see the donor diaries
    # as reported, i.e. before any synthetic closure has been appended to them.
    dwell_model = build_closure_dwell_model(
        persons, mid_wege,
        closure_dwell_model=closure_dwell_model,
        random_seed=random_seed,
        closure_dwell_min_obs=closure_dwell_min_obs,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
    )
    table, report = popsim_trips.build_validated_trip_table(
        persons, mid_wege,
        resample=True,
        resample_cell_col=resample_cell_col,
        random_seed=random_seed,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
        dwell_model=dwell_model,
    )

    if "is_synthetic_closure" in table.columns:
        # Keep the flag a clean boolean: the stage-B chain replacement concatenates
        # donor rows that need not carry the column, which would leave NaN in an
        # object column and break boolean indexing downstream.
        table["is_synthetic_closure"] = (
            table["is_synthetic_closure"].fillna(False).astype(bool)
        )

    _log_closure_share(table)
    # A SNAPSHOT (dict(...)), never the live mapping: ClosureDwellModel.report is one dict
    # that draw() mutates in place, and logging keeps the ARGUMENT in LogRecord.args and
    # renders it lazily (LogRecord.getMessage() recomputes msg % args on every call), so a
    # late-formatting handler would render the counters as they are at FORMAT time rather
    # than at EMIT time. The same defect was found and fixed in the Phase B home-office
    # donor pool (braunschweig/synthesis/commute_day/donor_pool.py, #374 fix round 2),
    # where it was ACTIVE; here the dict is already final when this line runs, so the
    # hazard is latent -- the snapshot keeps it that way for any later edit or deferred
    # handler.
    logger.info("[trips_stage] closure dwell model (%s) report: %s",
                closure_dwell_model, dict(dwell_model.report))

    logger.info(
        "[trips_stage] trip table built: %d trips for %d persons; "
        "valid=%s home_closed_rate=%.1f%%",
        len(table),
        table["person_id"].nunique(),
        report.is_valid,
        (report.home_end_closure_rate * 100.0) if hasattr(report, "home_end_closure_rate") else float("nan"),
    )

    # Sort by (person_id, trip_index) to match the ordering assumed by downstream
    # stages (synthesis/population/activities.py uses trip_index, not trip_id).
    table = table.sort_values(["person_id", "trip_index"]).reset_index(drop=True)

    # NaN times must never reach the jitter: the resample above replaces every
    # NaN-time (coded-time) person and drops home-only fallback placeholders.
    for col in ("departure_time", "arrival_time"):
        n_nan = int(table[col].isna().sum())
        assert n_nan == 0, (
            f"[trips_stage] {n_nan} rows have NaN {col} after resample; the "
            f"resample in build_validated_trip_table must replace every "
            f"coded-time person (and drop home-only fallback rows) before the jitter."
        )

    # --------------------------------------------------------------------------
    # Per-person departure-time jitter.
    # Delegates to the shared apply_per_person_jitter (factored so the ENTD
    # adapter can call the same formula without duplication).
    # --------------------------------------------------------------------------
    table = apply_per_person_jitter(table, random_seed=random_seed)

    # Absolute plan-time bound (final backstop): midnight-crossing repairs may
    # legitimately push times past 24h, but PlanValidator classifies any person
    # exceeding MAX_PLAN_TIME_SECONDS as unfixable and the resample above
    # replaces them — anything still beyond the bound here is a bug.
    _assert_time_bound(table)

    # --------------------------------------------------------------------------
    # Euclidean distance from MiD wegkm_imp (routed km -> straight-line metres).
    # wegkm_imp is the MiD imputed routed trip length in kilometres; dividing by
    # the ENTD detour factor gives the straight-line distance in km; * 1000 -> m.
    # --------------------------------------------------------------------------
    if "wegkm_imp" in table.columns:
        table["euclidean_distance"] = (
            table["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR
        )

    # Build final column order: CONTRACT first, then extras (euclidean_distance,
    # trip_key, and all remaining MiD columns) so downstream code that selects
    # CONTRACT columns works without needing to know about extras.
    extras_ordered = [
        c for c in ("euclidean_distance", "trip_key")
        if c in table.columns
    ]
    remaining = [
        c for c in table.columns
        if c not in CONTRACT and c not in extras_ordered
    ]
    return table[CONTRACT + extras_ordered + remaining]


def configure(context):
    # The shared key/default constants every stage that reads these flags declares them
    # with (braunschweig.popsim.stage.config_keys is the single home for both halves).
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_CLOSURE_DWELL_MODEL, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG,
        DEFAULT_ESCORT_PASSIVE_EDUCATION, DEFAULT_ESCORT_PASSIVE_FROM_ADULT,
        DEFAULT_EXCLUDE_RBW_LEGS, DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
        DEFAULT_W_ZWECK_10_AS_LEISURE, KEY_CLOSURE_DWELL_MIN_OBS,
        KEY_CLOSURE_DWELL_MODEL, KEY_DROP_LEADING_ARRIVE_HOME_LEG,
        KEY_ESCORT_PASSIVE_EDUCATION, KEY_ESCORT_PASSIVE_FROM_ADULT,
        KEY_EXCLUDE_RBW_LEGS, KEY_PASSIVE_PAIR_MAX_GAP_MINUTES,
        KEY_W_ZWECK_10_AS_LEISURE,
    )
    # Read from synthesis.population.sampled (not the raw producer): sampled carries the
    # reassigned integer person_id and the preserved donor keys H_ID/P_ID, so the trip
    # table is built against the already-sampled and id-remapped synthetic population.
    context.stage("synthesis.population.sampled", alias="persons")
    context.config("random_seed")
    escort_purpose = context.config("escort_purpose", False)
    # escort_passive_education is declared with the SHARED key/default constants (final fix
    # wave, item 3), like the plan-structure flags below: the popsim stage reads the same
    # key so its education_flag control seed counts the same W_ZWECK codes as education
    # that THIS trip build realises (issue #368), and a second literal spelling of key or
    # default here is exactly the drift that would reopen the seed-vs-plan education
    # mismatch this package exists to remove.
    context.config(KEY_ESCORT_PASSIVE_EDUCATION, DEFAULT_ESCORT_PASSIVE_EDUCATION)
    # Explicit W_ZWECK purposes (issue #241): default True maps codes 14/15/16
    # (Sport / Freunde besuchen / Unterricht nicht Schule) to "leisure" instead of letting
    # them reach "other" through the silent fallback. False reproduces the pre-#241
    # assignment for the A/B.
    context.config("explicit_round_trip_purposes", True)
    # Plan-structure flags (issues #366 / #367). The first two mirror the keys
    # completed_donor declares for the plan-source side of the same decision (a
    # diary is only realisable if it still has legs after these drops), so both
    # stages MUST be configured consistently -- the trip build warns when a donor
    # is emptied by a drop that the plan match should have handled upstream.
    # Defaults ON / "empirical" per the project rule (new features default on);
    # False / False / "fixed_1h" is the byte-identical pre-#366 path.
    context.config(KEY_EXCLUDE_RBW_LEGS, DEFAULT_EXCLUDE_RBW_LEGS)
    context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG)
    context.config(KEY_CLOSURE_DWELL_MODEL, DEFAULT_CLOSURE_DWELL_MODEL)
    context.config(KEY_CLOSURE_DWELL_MIN_OBS, DEFAULT_CLOSURE_DWELL_MIN_OBS)
    # W_ZWECK 10 "anderer Zweck" -> leisure (issue #373, ADR-0111): a TRIP-BUILD flag
    # declared with the SHARED key/default constants, like escort_passive_education
    # above -- the popsim stage's leisure_participation KREIS-control seed and the
    # distance-distribution layers must see the SAME value this trip build uses, or
    # they disagree on which W_ZWECK codes mean leisure (the seed-vs-plan mismatch
    # class this package exists to remove).
    context.config(KEY_W_ZWECK_10_AS_LEISURE, DEFAULT_W_ZWECK_10_AS_LEISURE)
    # Passive escort leg -> the accompanying adult's purpose (issue #372, ADR-0112): declared
    # with the SHARED key/default constants for the same reason escort_passive_education is --
    # the popsim stage's education_flag KREIS-control seed, the distance layers and the
    # commute-day donor pool must all see the SAME value this trip build uses, or they disagree
    # on which code-13 legs are education and the seed describes a different day than the plan.
    # Declared default False; the production true is added to configs/base_bs.yml by task 7
    # (see config_keys.KEY_ESCORT_PASSIVE_FROM_ADULT for the ONE statement of both defaults).
    escort_passive_from_adult = context.config(
        KEY_ESCORT_PASSIVE_FROM_ADULT, DEFAULT_ESCORT_PASSIVE_FROM_ADULT)
    context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES)
    # Ruling C-R22 (issue #373 cleanup wave, item 6): trips.map_purpose already raises this
    # exact contradiction ("escort_passive_from_adult requires escort_purpose"), but only at
    # TRIP-BUILD time -- i.e. after synpp has already resolved and run every UPSTREAM stage,
    # including the full PopulationSim balancing (potentially hours of compute). Repeating the
    # check here, at CONFIGURE time, lets synpp fail the whole DAG before any stage executes,
    # mirroring the config-time contradiction guard braunschweig.analysis.synthesis.
    # plan_structure_vs_srv.configure() already uses for its own trips_view key. The
    # map_purpose check is KEPT (not removed): it is the last line of defense for any caller
    # that builds a trip table directly, outside this stage's configure()/execute() contract.
    if escort_passive_from_adult and not escort_purpose:
        raise ValueError(
            f"[trips_stage] {KEY_ESCORT_PASSIVE_FROM_ADULT}=True requires escort_purpose=True "
            "(without a dedicated escort purpose there is no passive side to re-derive from "
            "the accompanying adult's leg); set both keys consistently."
        )
    context.config("braunschweig.population.popsim.mid_dir")
    # Donor source identifier: must match the value configured in popsim.stage
    # (default "mid" -> MidSource -> mid.load_mid_wege + trips_stage.run, byte-identical).
    context.config("braunschweig.population.popsim.source", "mid")

    source_name = context.config("braunschweig.population.popsim.source", "mid")
    if source_name == "entd":
        # popsim_open: the full-composition ENTD frames (including all trips) come
        # from data.hts.entd.FILTERED, NOT data.hts.selected (= reweighted, which
        # keeps one person per household for IPF matching). Must match the seed
        # donor used in braunschweig.popsim.stage. Alias to "hts_donor".
        context.stage("data.hts.entd.filtered", alias="hts_donor")


def execute(context):
    from braunschweig.popsim import sources

    persons = context.stage("persons")
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    # synpp's ExecuteContext.config() takes only the key; the default ("mid") is
    # registered in configure().
    source_name = context.config("braunschweig.population.popsim.source")

    source = sources.get_source(source_name)
    logger.info("[trips_stage] active donor source: %s", source.name)

    # Load donor tables through the source adapter.
    # For source="mid": reads MiD CSV files from mid_dir (byte-identical).
    # For source="entd": receives the cleaned ENTD frames from the synpp DAG
    # (registered in configure as alias "hts_donor") and injects them.
    if source_name == "entd":
        hts_hh, hts_persons, hts_trips = context.stage("hts_donor")
        _donor_households, _donor_persons, donor_trips = source.load_donor(
            mid_dir, injected=(hts_hh, hts_persons, hts_trips)
        )
    else:
        # popsim_mid (default): reads MiD CSV files directly; households and
        # persons tables are not needed here (trips only).
        _donor_households, _donor_persons, donor_trips = source.load_donor(mid_dir)

    # Declared in configure(); ExecuteContext.config() takes the key alone.
    from braunschweig.popsim.stage.config_keys import (
        KEY_CLOSURE_DWELL_MIN_OBS, KEY_CLOSURE_DWELL_MODEL,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_ESCORT_PASSIVE_EDUCATION,
        KEY_ESCORT_PASSIVE_FROM_ADULT, KEY_EXCLUDE_RBW_LEGS,
        KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, KEY_W_ZWECK_10_AS_LEISURE,
    )
    escort_purpose = bool(context.config("escort_purpose"))
    escort_passive_education = bool(context.config(KEY_ESCORT_PASSIVE_EDUCATION))
    explicit_round_trip_purposes = bool(context.config("explicit_round_trip_purposes"))
    exclude_rbw_legs = bool(context.config(KEY_EXCLUDE_RBW_LEGS))
    drop_leading_arrive_home_leg = bool(context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG))
    closure_dwell_model = str(context.config(KEY_CLOSURE_DWELL_MODEL))
    closure_dwell_min_obs = int(context.config(KEY_CLOSURE_DWELL_MIN_OBS))
    w_zweck_10_as_leisure = bool(context.config(KEY_W_ZWECK_10_AS_LEISURE))
    escort_passive_from_adult = bool(context.config(KEY_ESCORT_PASSIVE_FROM_ADULT))
    passive_pair_max_gap_minutes = float(context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES))
    return source.build_trips(
        persons, donor_trips,
        random_seed=int(context.config("random_seed")),
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        closure_dwell_model=closure_dwell_model,
        closure_dwell_min_obs=closure_dwell_min_obs,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
    )
