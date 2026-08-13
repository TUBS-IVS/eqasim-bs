"""Per-leg subtype / location-type deciders (shop, leisure, other, escort).

Each ``_build_*_decider`` returns ``None`` when its feature flag is OFF (the
byte-identical OFF path) or a closure drawing from its own dedicated seeded
RNG stream (``random_seed + <offset>``), so enabling one flag never perturbs
another decider's draws. MiD-based probability tables are loaded lazily
inside the builders so configs with all flags OFF never require the
local-only MiD delivery.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from .activity_types import ESCORT_CATEGORY_TO_ACTIVITY


# ---------------------------------------------------------------------------
# Tier 2: daily / non-daily shop subtype decider
# ---------------------------------------------------------------------------

# Deterministic offset added to random_seed for the subtype-imputation RNG, so
# the subtype draws use a SEPARATE stream from the distance-sampling RNG
# (``random``) and therefore never perturb the distance draws -> the OFF path
# stays byte-identical.
SHOP_SUBTYPE_SEED_OFFSET = 90211

# Task 4 (issue #127): one further dedicated offset per NEW subtype decider,
# each one more than the last, so leisure and other each draw from their own
# separate RNG stream -- distinct from SHOP_SUBTYPE_SEED_OFFSET, from
# ``random`` (distance sampling), and from each other. None of the three
# subtype streams can perturb another, so any subset of {shop, leisure, other}
# splits being ON/OFF leaves the others' draws (and the distance RNG / OFF
# path) unchanged.
LEISURE_SUBTYPE_SEED_OFFSET = 90212  # SHOP_SUBTYPE_SEED_OFFSET + 1
OTHER_SUBTYPE_SEED_OFFSET = 90213    # SHOP_SUBTYPE_SEED_OFFSET + 2

# Issue #201: the escort location-type decider gets its own dedicated stream
# too, one more than the last subtype offset, so it cannot perturb the shop /
# leisure / other subtype draws, the distance RNG, or the OFF path.
ESCORT_LOCATION_SEED_OFFSET = 90214  # SHOP_SUBTYPE_SEED_OFFSET + 3


def _build_shop_subtype_decider(context, random_seed: int):
    """Build the per-leg shop daily/non-daily decider, or return None when OFF.

    Returns a callable ``(mode: str, travel_time_s: float) -> "shop_daily" |
    "shop_non_daily"`` (Tier 2) when ``secondary_shop_daily_split`` is ON, else
    ``None`` (the byte-identical OFF path). The conditional daily probability
    ``P(daily | mode, travel-time band)`` is estimated from the MiD 2023 Wege
    survey (labelled CATI/CAWI shop legs only) via
    ``braunschweig.popsim.shop_subtype.estimate_daily_probability``; per leg the
    subtype is drawn ``~ Bernoulli(P(daily | covariates))`` with a dedicated
    seeded RNG. ``secondary_shop_daily_share`` (a float in [0, 1]) overrides the
    MiD-estimated table with a flat marginal share (used to pin the share); when
    None the MiD conditional table is used. The labelled fraction is logged (no
    silent fallback).
    """
    if not context.config("secondary_shop_daily_split"):
        return None

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.shop_subtype import (
        SHOP_DAILY_W_ZWD,
        SHOP_NONDAILY_W_ZWD,
        estimate_daily_probability,
        impute_subtype,
        tt_band,
    )
    from braunschweig.popsim.trips import map_mode, mid_time_seconds

    pinned_share = context.config("secondary_shop_daily_share")
    min_obs = int(context.config("secondary_distance_min_obs"))

    if pinned_share is not None:
        # Flat marginal share: no covariate conditioning. ASSUMPTION-free in the
        # sense that the caller explicitly pinned it via config.
        marginal = float(pinned_share)
        prob: Dict[Any, float] = {("__marginal__", -1): marginal}
        print(
            "[braunschweig.secondary_chainsolvers] shop daily subtype: using "
            f"pinned flat daily share {marginal:.3f} "
            "(secondary_shop_daily_share set; MiD estimation skipped, "
            "labelled-fraction diagnostic N/A)."
        )
    else:
        # Estimate the conditional P(daily | mode, tt_band) from MiD Wege.
        mid_dir = context.config("braunschweig.population.popsim.mid_dir")
        mid_wege = mid_module.load_mid_wege(mid_dir)
        # estimate_daily_probability needs columns: W_ZWECK, mode, travel_time,
        # W_ZWD, W_GEW. map_mode derives "mode" from hvm_imp; travel_time is
        # arrival - departure in seconds (the same derivation the distance
        # distributions stage uses).
        mid_wege = map_mode(mid_wege)
        dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
        arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
        tt = arr - dep
        tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
        mid_wege = mid_wege.assign(travel_time=tt)

        n_shop = int((mid_wege["W_ZWECK"] == 4).sum())
        labelled_mask = (
            (mid_wege["W_ZWECK"] == 4)
            & mid_wege["W_ZWD"].isin(SHOP_DAILY_W_ZWD | SHOP_NONDAILY_W_ZWD)
        )
        n_labelled = int(labelled_mask.sum())
        prob = estimate_daily_probability(mid_wege, min_obs=min_obs)
        marginal = float(prob[("__marginal__", -1)])
        n_cells = sum(1 for k in prob if k != ("__marginal__", -1))
        print(
            "[braunschweig.secondary_chainsolvers] shop daily subtype: MiD "
            f"labelled shop legs {n_labelled:,}/{n_shop:,} "
            f"({100.0 * n_labelled / n_shop if n_shop else 0.0:.1f}%); "
            f"marginal daily share {marginal:.3f}; "
            f"{n_cells} (mode, tt_band) cells >= min_obs={min_obs} "
            "(thinner cells use the marginal)."
        )

    rng = np.random.RandomState(int(random_seed) + SHOP_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        # impute_subtype is vectorised; call it on a 1-element batch so the
        # estimation/imputation logic is shared (no duplicated probability
        # lookup). The dedicated rng keeps this independent of the distance RNG.
        is_daily = impute_subtype([mode], [travel_time_s], prob, marginal, rng)[0]
        return "shop_daily" if is_daily else "shop_non_daily"

    return decide


# ---------------------------------------------------------------------------
# Task 4 (issue #127): leisure / other subtype imputation deciders
# ---------------------------------------------------------------------------

def _inverse_cdf_choice(probs: Dict[str, float], group_names, draw: float) -> str:
    """Return the name in ``group_names`` whose cumulative probability first
    exceeds ``draw`` (standard inverse-CDF sampling): walk ``group_names`` in
    the given fixed order while accumulating a running sum, and pick the first
    entry whose cumulative probability exceeds ``draw``.

    This is exactly the per-leg selection rule
    ``braunschweig.popsim.purpose_subtype.impute_groups`` applies internally
    (see that function's determinism note) -- reused here as a plain one-leg
    helper INSTEAD OF calling ``impute_groups`` once per leg, because
    ``impute_groups`` is designed for a single BATCHED call over many legs and
    logs an aggregate marginal-fallback-rate message on every invocation.
    Calling it with a length-1 batch (as the per-leg decider architecture
    requires, mirroring ``_build_shop_subtype_decider``) would therefore emit
    one log line per fallback leg -- log spam at population scale (millions of
    legs). This helper performs the identical maths (one draw, a fixed-order
    cumulative sum, first-exceeding-index selection) without that per-call
    logging; the MODEL-level fallback rate (how many (mode, tt_band) cells got
    their own estimate vs. the marginal) is already logged once, at
    decider-build time, by ``estimate_group_probabilities`` itself -- so no
    fallback-rate signal is lost, only the per-leg spam.
    """
    cumulative = np.cumsum([probs.get(name, 0.0) for name in group_names])
    choice = int(np.clip(np.searchsorted(cumulative, draw, side="right"), 0, len(group_names) - 1))
    return group_names[choice]


def _build_leisure_subtype_decider(context, random_seed: int):
    """Build the per-leg leisure subtype decider, or return None when OFF.

    Sibling to ``_build_shop_subtype_decider``. Returns a callable
    ``(mode: str, travel_time_s: float) -> str``, one of
    ``LEISURE_SUBTYPE_ACTIVITIES`` (the ``purpose_subtype.LEISURE_GROUPS``
    keys), when ``secondary_leisure_subtype_split`` is ON, else ``None`` (the
    byte-identical OFF path). ``P(group | mode, tt_band)`` is estimated from
    the MiD 2023 Wege survey via
    ``braunschweig.popsim.purpose_subtype.estimate_group_probabilities`` (Task
    2, issue #127); that call logs the labelled-leg share and the (mode,
    tt_band) cell coverage ONCE, here, at decider-build time. Per leg the
    decider draws exactly one uniform sample from a dedicated seeded RNG
    (``LEISURE_SUBTYPE_SEED_OFFSET``, NOT ``random``) and resolves it via
    ``_inverse_cdf_choice`` -- see that helper's docstring for why the per-leg
    draw is done inline rather than via a per-leg call to ``impute_groups``.
    """
    if not context.config("secondary_leisure_subtype_split"):
        return None

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.purpose_subtype import (
        LEISURE_SPEC,
        estimate_group_probabilities,
        tt_band,
    )
    from braunschweig.popsim.trips import map_mode, mid_time_seconds

    min_obs = int(context.config("secondary_distance_min_obs"))
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    mid_wege = mid_module.load_mid_wege(mid_dir)
    # estimate_group_probabilities needs W_ZWECK, mode, travel_time, W_GEW,
    # W_ZWD. map_mode derives "mode" from hvm_imp; travel_time is arrival -
    # departure in seconds (the same derivation as the shop decider / the
    # distance distributions stage).
    mid_wege = map_mode(mid_wege)
    dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
    arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
    tt = arr - dep
    tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
    mid_wege = mid_wege.assign(travel_time=tt)

    cell_probs, marginal = estimate_group_probabilities(mid_wege, LEISURE_SPEC, min_obs=min_obs)
    group_names = sorted(marginal)
    print(
        "[braunschweig.secondary_chainsolvers] leisure subtype: marginal shares "
        + ", ".join(f"{name}={marginal[name]:.3f}" for name in group_names)
    )

    rng = np.random.RandomState(int(random_seed) + LEISURE_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        probs = cell_probs.get((mode, tt_band(travel_time_s)), marginal)
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide


def _build_other_subtype_decider(context, random_seed: int):
    """Build the per-leg "other" errand/escort/rest subtype decider, or None
    when OFF.

    Sibling to ``_build_shop_subtype_decider`` / ``_build_leisure_subtype_decider``.
    Returns a callable ``(mode: str, travel_time_s: float) -> str``, one of
    ``OTHER_SUBTYPE_ACTIVITIES`` (``"other_errand_short"``/``"other_errand_long"``/
    ``"other_escort"``) or ``"other_rest"``, when ``secondary_other_subtype_split``
    is ON, else ``None`` (the byte-identical OFF path).

    The MiD "other" umbrella (following_purpose == "other", i.e. raw W_ZWECK in
    {5, 6, 10}) is split in TWO composed stages, mirroring how the
    distance-distribution layer treats it (Task 3, issue #127 --
    ``braunschweig.popsim.distance_distributions``):

    1. A coarse, ALWAYS-labelled 3-way split {errand, escort, rest} estimated
       directly from the raw W_ZWECK code via a local
       ``purpose_subtype.SubtypeSpec`` with ``group_col="W_ZWECK"``: escort =
       W_ZWECK in ``purpose_subtype.OTHER_ESCORT_ZWECK`` ({6}); errand =
       W_ZWECK in ``purpose_subtype.OTHER_ERRAND_ZWECK`` ({5}); rest = the
       remaining "other" W_ZWECK codes. "rest" is derived from
       ``braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK`` -- the single source of
       truth for which raw W_ZWECK codes map to the eqasim "other" purpose --
       rather than hardcoded, so it can never silently drift from that
       mapping. No W_ZWD is needed for this split, so it is never thinned by a
       missing detail code (escort legs in particular carry no W_ZWD at all).
    2. Within errand (W_ZWECK == 5) legs only, the existing W_ZWD-based
       short/long split (``purpose_subtype.OTHER_ERRAND_SPEC``, Task 2), with
       its own marginal fallback for unlabelled-W_ZWD errand legs.

    Both stages are estimated conditionally on (mode, tt_band); each logs its
    own labelled-leg share and cell coverage ONCE, here, at decider-build time.
    Per leg the two stages are composed into ONE 4-outcome probability vector
    -- P(escort), P(rest), P(errand) * P(short | errand), P(errand) * P(long |
    errand) -- and exactly one uniform draw from a dedicated seeded RNG
    (``OTHER_SUBTYPE_SEED_OFFSET``, NOT ``random``) selects the outcome via
    ``_inverse_cdf_choice``, so every "other" leg -- errand, escort, or rest --
    consumes the same single draw per leg as the shop/leisure deciders.

    When ``escort_purpose`` is ON (issue #201) escort is realised as its own
    plan-level purpose upstream (see ``_build_escort_location_decider``), so no
    leg with ``following_purpose == "other"`` can carry a raw W_ZWECK in
    ``OTHER_ESCORT_ZWECK`` any more. Stage 1 then collapses to a 2-way
    {errand, rest} split estimated only on the remaining "other" W_ZWECK codes,
    and the outcome vocabulary drops ``"other_escort"`` accordingly. With
    ``escort_purpose`` OFF this is value-identical to the previous 3-way split
    (same ``group_names`` tuple, same probability composition, same single
    draw).
    """
    if not context.config("secondary_other_subtype_split"):
        return None

    escort_purpose_on = bool(context.config("escort_purpose"))  # one-arg: execute-context read; key declared in configure()

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.purpose_subtype import (
        OTHER_ERRAND_SPEC,
        OTHER_ERRAND_ZWECK,
        OTHER_ESCORT_ZWECK,
        SubtypeSpec,
        estimate_group_probabilities,
        tt_band,
    )
    from braunschweig.popsim.trips import PURPOSE_BY_W_ZWECK, map_mode, mid_time_seconds

    min_obs = int(context.config("secondary_distance_min_obs"))
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    mid_wege = mid_module.load_mid_wege(mid_dir)
    mid_wege = map_mode(mid_wege)
    dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
    arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
    tt = arr - dep
    tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
    mid_wege = mid_wege.assign(travel_time=tt)

    # Stage 1: coarse errand/(escort/)rest split, labelled directly by the raw
    # W_ZWECK code (never thinned by a missing W_ZWD).
    other_zweck = frozenset(
        code for code, purpose in PURPOSE_BY_W_ZWECK.items() if purpose == "other"
    )
    if escort_purpose_on:
        # Issue #201: with escort as a dedicated plan-level purpose no escort
        # leg reaches following_purpose == "other" any more, so the coarse
        # split estimates only {errand, rest} on the remaining "other" codes.
        other_zweck = other_zweck - OTHER_ESCORT_ZWECK
        coarse_groups = {"errand": OTHER_ERRAND_ZWECK,
                         "rest": other_zweck - OTHER_ERRAND_ZWECK}
    else:
        coarse_groups = {"errand": OTHER_ERRAND_ZWECK,
                         "escort": OTHER_ESCORT_ZWECK,
                         "rest": other_zweck - OTHER_ERRAND_ZWECK - OTHER_ESCORT_ZWECK}
    coarse_spec = SubtypeSpec(
        purpose_label="other_coarse",
        zweck_values=other_zweck,
        groups=coarse_groups,
        sentinels=frozenset(),
        group_col="W_ZWECK",
    )
    coarse_cell_probs, coarse_marginal = estimate_group_probabilities(
        mid_wege, coarse_spec, min_obs=min_obs)

    # Stage 2: within errand legs, the existing W_ZWD-based short/long split.
    errand_cell_probs, errand_marginal = estimate_group_probabilities(
        mid_wege, OTHER_ERRAND_SPEC, min_obs=min_obs)

    # Issue #201: "escort" is only a coarse_marginal key when escort_purpose is
    # OFF (Stage 1 above only builds that group in the 3-way OFF-path spec) --
    # include it in the summary line only when present, rather than a KeyError
    # or a fabricated 0.000 entry for a group that was never estimated.
    escort_share = coarse_marginal.get("escort")
    escort_summary = f"escort={escort_share:.3f}, " if escort_share is not None else ""
    print(
        "[braunschweig.secondary_chainsolvers] other subtype: coarse marginal shares "
        f"{escort_summary}errand={coarse_marginal['errand']:.3f}, "
        f"rest={coarse_marginal['rest']:.3f}; errand marginal shares "
        f"other_errand_short={errand_marginal['other_errand_short']:.3f}, "
        f"other_errand_long={errand_marginal['other_errand_long']:.3f}"
    )

    outcome_names = ["other_errand_short", "other_errand_long", "other_rest"]
    if not escort_purpose_on:
        outcome_names.append("other_escort")
    group_names = tuple(sorted(outcome_names))
    rng = np.random.RandomState(int(random_seed) + OTHER_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        band = tt_band(travel_time_s)
        coarse = coarse_cell_probs.get((mode, band), coarse_marginal)
        errand = errand_cell_probs.get((mode, band), errand_marginal)
        p_errand = coarse.get("errand", 0.0)
        probs = {
            "other_rest": coarse.get("rest", 0.0),
            "other_errand_short": p_errand * errand.get("other_errand_short", 0.0),
            "other_errand_long": p_errand * errand.get("other_errand_long", 0.0),
        }
        if not escort_purpose_on:
            probs["other_escort"] = coarse.get("escort", 0.0)
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide


def _build_escort_location_decider(context, random_seed: int):
    """Build the per-leg escort location-TYPE decider, or None when OFF.

    Issue #201: every plan-level "escort" leg draws ONE location category
    (education by school type / other / leisure / residential / shop) from the
    configured weight vector -- no covariate conditioning; the weights are the
    SrV-2023-BS+RGB observed destination-type shares
    (scripts/derive_escort_location_weights.py). Returns a callable
    ``() -> str`` yielding one of ESCORT_LOCATION_ACTIVITIES, consuming exactly
    one uniform draw per call from a dedicated seeded RNG
    (ESCORT_LOCATION_SEED_OFFSET), so the distance RNG and the three subtype
    decider streams stay untouched (OFF path byte-identical).
    """
    if not context.config("escort_purpose"):
        return None

    # Execute-context config() takes the key alone (declared defaults live in
    # configure(), wired by the next task); see
    # tests/test_execute_context_config_contract.py for the two-argument
    # crash this avoids.
    activities = list(context.config("escort_locations_activities"))
    weights = [float(w) for w in context.config("escort_locations_weights")]

    if len(activities) != len(weights):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_activities and "
            f"escort_locations_weights must have the same length, got "
            f"{len(activities)} and {len(weights)}."
        )
    if len(set(activities)) != len(activities):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_activities "
            f"contains duplicate escort location categories: {activities}."
        )
    unknown = sorted(set(activities) - set(ESCORT_CATEGORY_TO_ACTIVITY))
    if unknown:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] unknown escort location "
            f"category(ies) {unknown}; allowed: {sorted(ESCORT_CATEGORY_TO_ACTIVITY)}."
        )
    if any(w < 0.0 for w in weights) or sum(weights) <= 0.0:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_weights must be "
            "non-negative with a positive sum."
        )

    total = float(sum(weights))
    probs = {
        ESCORT_CATEGORY_TO_ACTIVITY[category]: weight / total
        for category, weight in zip(activities, weights)
    }
    group_names = tuple(ESCORT_CATEGORY_TO_ACTIVITY[c] for c in activities)
    print(
        "[braunschweig.secondary_chainsolvers] escort location draw: "
        + ", ".join(f"{c}={w / total:.3f}" for c, w in zip(activities, weights))
        + " (SrV 2023 BS+RGB derived defaults; see "
          "srv2023_escort_destination_types.csv)"
    )

    rng = np.random.RandomState(int(random_seed) + ESCORT_LOCATION_SEED_OFFSET)

    def decide() -> str:
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide
