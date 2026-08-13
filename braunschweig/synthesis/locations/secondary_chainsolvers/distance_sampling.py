"""Desired-distance sampling from the MiD distance-distribution CDFs.

Mirrors the legacy stage's ``CustomDistanceSampler`` semantics 1:1
(CDF resampling tweaks, leisure-correction factor, purpose-layer
auto-detection) so the chainsolvers path stays apples-to-apples with the
eqasim RDA path on the input-distribution side. All functions are pure
(no synpp context); the RNG is always passed in explicitly.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

import numpy as np


def _resample_cdf(cdf, factor):
    if factor >= 0.0:
        cdf = cdf * (1.0 + factor * np.arange(1, len(cdf) + 1) / len(cdf))
    else:
        cdf = cdf * (
            1.0 + abs(factor) - abs(factor) * np.arange(1, len(cdf) + 1) / len(cdf)
        )
    cdf /= cdf[-1]
    return cdf


def _resample_distributions(distributions, factors):
    """Return a resampled deep copy of ``distributions``; never mutate the input.

    The ``distance_distributions`` stage object is synpp-cached and shared with
    the legacy locations stage. Resampling in place would compound the resample
    factors if the same cached object were resampled twice (double-resample
    contamination across consumers). We therefore deep-copy the nested dict and
    mutate only the copy; the returned object carries the resampled CDFs while
    the original cached object stays untouched. The deep copy is cheap (a
    handful of small distribution dicts per mode).

    Handles BOTH the legacy per-mode structure ``{mode: {bounds, distributions}}``
    and the purpose-layered structure ``{purpose: {mode: {bounds, distributions}}}``
    (Tier 1, built when ``secondary_shop_daily_split`` adds ``shop_daily`` /
    ``shop_non_daily`` distribution layers). The per-mode resample ``factors`` are
    applied within each mode regardless of the layer structure. Detection mirrors
    ``_sample_leg_distance``: a mode-level dict carries a ``"distributions"`` key;
    a purpose-level dict maps purpose -> mode-dict (no ``"distributions"`` key
    at the top level).
    """
    distributions = copy.deepcopy(distributions)
    # Guard against empty dict: next(iter(...)) raises StopIteration on empty.
    if not distributions:
        return distributions
    # Detect whether the top level is a purpose layer or a mode layer. A mode-level
    # dict always carries a "distributions" key; a purpose-level dict does not
    # (its values are mode dicts, each of which carries "distributions" one level
    # deeper). Modes and purposes are disjoint vocabularies, so an ambiguous top-
    # level key cannot occur.
    sample_value = next(iter(distributions.values()))
    is_purpose_layered = "distributions" not in sample_value
    if is_purpose_layered:
        for purpose, mode_dict in distributions.items():
            for mode, mode_distributions in mode_dict.items():
                for distribution in mode_distributions["distributions"]:
                    distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])
    else:
        for mode, mode_distributions in distributions.items():
            for distribution in mode_distributions["distributions"]:
                distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])
    return distributions


def _sample_leg_distance(distributions, mode, travel_time, purpose,
                         leisure_correction_factor, random):
    """Replicates ``CustomDistanceSampler.sample_distances`` for one leg.

    Auto-detects whether ``distributions`` is the legacy per-mode structure
    ``{mode: ...}`` or a purpose-layered one ``{purpose: {mode: ...}}``.
    Purposes (shop/leisure/other/work/education) and modes (car/walk/pt/
    bicycle/car_passenger) are disjoint vocabularies, so a top-level key equal
    to ``mode`` means the legacy per-mode structure; otherwise a purpose layer
    is expected and ``distributions[purpose]`` is selected. If ``purpose`` is
    absent from the purpose-layered dict the resulting KeyError surfaces
    immediately (no silent fallback -- a wiring bug should not be hidden).
    """
    # Auto-detect structure by checking whether the mode key is present at the
    # top level. Since purposes and modes are disjoint vocabularies, this is
    # unambiguous: a top-level "car"/"walk"/... key means legacy; a top-level
    # "shop"/"leisure"/... key means purpose-layered.
    legacy_mode_keyed = mode in distributions
    if legacy_mode_keyed:
        mode_distributions = distributions
    else:
        mode_distributions = distributions[purpose]
    mode_distribution = mode_distributions[mode]
    bound_index = int(np.count_nonzero(travel_time > mode_distribution["bounds"]))
    mode_distribution = mode_distribution["distributions"][bound_index]
    distance = mode_distribution["values"][
        int(np.count_nonzero(random.random_sample() > mode_distribution["cdf"]))
    ]
    # The leisure-correction factor is a LEGACY mode-only heuristic: on the
    # per-mode distribution leisure trips are diluted by the shorter shop/other
    # legs sharing the same mode, so leisure distances were scaled up to
    # compensate. With the Tier-1 purpose-resolved distributions
    # (secondary_distance_by_purpose: true) the leisure distance is sourced
    # DIRECTLY from the per-purpose MiD CDF, so applying the factor on top
    # double-counts and inflates the leisure far-tail (~2x). Apply it ONLY on the
    # legacy per-mode structure; on the purpose-layered structure it is a no-op.
    if purpose == "leisure" and legacy_mode_keyed:
        distance *= leisure_correction_factor
    return float(distance)


def _rda_sample_distances(distributions, problem, leisure_correction_factor, random):
    """Per-leg desired distances for the rda fallback's distance sampler.

    The rda fallback (``_rda_fallback_place``) receives the same distribution
    object as the carla path. With the Tier-1 purpose-resolved feature ON that
    object is ``{purpose: {mode: ...}}``, but eqasim's stock
    ``CustomDistanceSampler.sample_distances`` indexes it by ``mode`` and raises
    ``KeyError: '<mode>'`` -- which is why the fallback placed nothing for the
    long-distance / unbounded chains it is meant to catch. Reuse the
    purpose-aware ``_sample_leg_distance`` (which auto-detects the layout) so the
    fallback samples distances exactly like the carla path. The legacy
    ``{mode: ...}`` layout stays byte-identical (auto-detected).

    Mirrors ``CustomDistanceSampler.sample_distances`` EXACTLY: a
    length-``len(modes)`` array is zero-initialised and filled by ``zip`` over
    (modes, travel_times, purposes). When the chain has more legs than secondary
    purposes (the trailing leg returns to a primary anchor), ``zip`` truncates to
    the purposes length and those trailing legs keep distance 0 -- the relaxation
    solver requires one distance per leg, so the returned length MUST equal
    ``len(modes)``.
    """
    distances = np.zeros((len(problem["modes"]),))
    for index, (mode, travel_time, purpose) in enumerate(zip(
            problem["modes"], problem["travel_times"], problem["purposes"])):
        distances[index] = _sample_leg_distance(
            distributions, mode, travel_time, purpose,
            leisure_correction_factor, random,
        )
    return distances


def _purpose_in_distributions(distributions: Dict[str, Any], purpose: str) -> bool:
    """True iff ``distributions`` is purpose-layered AND carries ``purpose``.

    A purpose-layered structure is ``{purpose: {mode: ...}}``; the legacy
    per-mode structure is ``{mode: ...}``. Modes and purposes are disjoint
    vocabularies, so a top-level key equal to a known mode (e.g. ``"car"``)
    means the legacy structure, in which no purpose sub-keying exists (returns
    False). Used by the Tier-2 shop subtype routing to decide whether a
    ``shop_daily`` / ``shop_non_daily`` distance layer exists or the aggregate
    ``"shop"`` layer must be used as a logged fallback.
    """
    _MODE_KEYS = {"car", "car_passenger", "pt", "bicycle", "walk"}
    if not distributions:
        return False
    # Legacy per-mode structure: a top-level mode key is present.
    if any(k in distributions for k in _MODE_KEYS):
        return False
    return purpose in distributions


def _synthesize_escort_type_layers(distributions, factor_by_activity):
    """Add per-destination-type escort distance layers (A3, issue #201 follow-up).

    For every entry of ``factor_by_activity`` (activity name -> SrV structure
    factor) a deep copy of the aggregate ``escort`` layer is added under the
    activity name with every distance ``values`` array multiplied by the factor
    (exact multiplicative semantics: P(D_type <= x) = P(D <= x/factor)). Neutral
    factors (1.0) get an identical copy ON PURPOSE: the per-type fallback counter
    in the leg loop must stay a true failure signal, so a factor-neutral category
    must not read as a missing layer. The caller passes the PRIVATE deep copy
    returned by ``_resample_distributions``; this function mutates and returns it.
    Legacy mode-keyed structures (or a missing ``escort`` layer) are returned
    unchanged with a WARNING -- the leg loop's counted fallback then surfaces the
    rate (no silent fallback).
    """
    if not _purpose_in_distributions(distributions, "escort"):
        print(
            "[braunschweig.secondary_chainsolvers] WARNING: escort_distance_by_type "
            "is ON but the distributions carry no 'escort' purpose layer (legacy "
            "mode-keyed structure?); per-type layers NOT synthesized -- the leg "
            "loop will count every escort leg as distance-layer fallback."
        )
        return distributions
    base = distributions["escort"]
    for activity, factor in factor_by_activity.items():
        layer = copy.deepcopy(base)
        for mode_distribution in layer.values():
            for distribution in mode_distribution["distributions"]:
                distribution["values"] = distribution["values"] * float(factor)
        distributions[activity] = layer
    return distributions
