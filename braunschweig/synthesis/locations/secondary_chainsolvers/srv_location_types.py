"""SrV-2023 location-type vocabulary, pinned-table loader and per-leg decider.

Issue #262: when ``secondary_srv_location_types`` is ON, every leisure /
other leg draws an OBSERVED SrV-2023-BS+RGB destination category
conditioned on (mode, euclidean distance band) from the pinned probability
table, and that category decides where the leg is placed. This module owns
the category vocabulary, the offer/potential column naming, the fail-fast
prerequisite guard, the pinned-CSV loader and the decider builder.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from .candidate_columns import VISIT_OFFER_COLUMN, VISIT_POTENTIAL_COLUMN
from .deciders import _inverse_cdf_choice


# ---------------------------------------------------------------------------
# Issue #262 (Task 7): pinned SrV-2023 location-type probabilities for
# leisure / other legs, and the per-leg decider that draws from them.
# ---------------------------------------------------------------------------

# One further dedicated RNG-stream offset than the escort location decider, so
# this decider's draws cannot perturb the shop/leisure/other subtype streams,
# the escort location stream, or the distance-sampling RNG (``random``) --
# turning ``secondary_srv_location_types`` ON/OFF (or any sibling flag) never
# perturbs another stream, and the OFF path stays byte-identical.
SRV_LOCATION_SEED_OFFSET = 90215  # SHOP_SUBTYPE_SEED_OFFSET + 4

# Location-category vocabulary for the two SrV-covered purposes (Task 1,
# scripts/derive_srv_location_types.py); must match the pinned CSV's
# "category" column exactly -- checked at load time by
# ``load_srv_location_type_probs``.
SRV_LEISURE_CATEGORIES = ("leisure_culture", "leisure_gastronomy", "leisure_misc",
                          "leisure_outdoor", "leisure_sports", "leisure_visit")
SRV_OTHER_CATEGORIES = ("errand_authority_medical", "errand_service", "other_misc")

# Categories whose eqasim placement_act stays the AGGREGATE purpose (i.e. they
# do not correspond to a more specific facility type than plain
# "leisure"/"other" -- consulted by the stage that resolves a drawn category
# to a candidate-search activity).
SRV_AGGREGATE_PLACEMENT = {"leisure_misc": "leisure", "other_misc": "other"}

# The two eqasim purposes the SrV location-type table covers (Task 8). Shop legs
# and escort legs have their own dedicated deciders and must never reach the SrV
# decider (which raises on any other purpose, see _build_srv_location_decider).
SRV_LOCATION_PURPOSES = ("leisure", "other")

# The drawn categories that become their OWN chainsolver placement activity
# (everything except the two aggregate-placement categories above). Each one
# needs an ``offers_<category>`` / ``pot_<category>`` candidate column pair --
# except "leisure_visit", which reuses the residential visit machinery's
# ``offers_visit`` / ``pot_visit`` columns (Task 5, issue #127).
SRV_PLACEMENT_CATEGORIES = tuple(
    name for name in SRV_LEISURE_CATEGORIES + SRV_OTHER_CATEGORIES
    if name not in SRV_AGGREGATE_PLACEMENT
)

# Categories the external Gemeinde centroids act as long-distance escapes for
# (see append_external_category_escapes): every placement category that has its
# own ``pot_<category>`` column. "leisure_visit" is excluded because its pool is
# the residential building stock (offers_visit / pot_visit) and external
# centroids never offered that on the OFF path either.
EXTERNAL_CATEGORY_ESCAPE_CATEGORIES = tuple(
    name for name in SRV_PLACEMENT_CATEGORIES if name != "leisure_visit"
)

# ``subtype_stats`` key namespace for the SrV draws. A prefix is REQUIRED, not
# cosmetic: "leisure_visit" is simultaneously a MiD distance subtype (see
# LEISURE_SUBTYPE_ACTIVITIES) and an SrV location category, so unprefixed
# counters would be incremented by both deciders and BOTH log lines (the MiD
# subtype labelling line and the SrV draw line) would report inflated counts.
SRV_LOCATION_STAT_PREFIX = "srv_location_"

# Marginal-fallback counters are kept PER PURPOSE, not pooled (review finding):
# leisure legs outnumber "other" legs several times over, so a pooled rate lets a
# badly covered purpose hide behind a well covered one (e.g. 45% fallback on
# "other" reads as ~5% pooled). Both the reported rate and the escalation
# threshold below therefore apply per purpose.
SRV_LOCATION_MARGINAL_FALLBACK_STAT_PREFIX = "srv_location_marginal_fallback_"

# Pinned probability table produced by scripts/derive_srv_location_types.py
# (Task 1). Committed reference data -- regenerate there, never edit by hand.
DEFAULT_SRV_LOCATION_TYPE_PROBS_PATH = (
    "eqasim-data/data/braunschweig/srv/srv2023_location_type_by_distance.csv"
)

# HEURISTIC escalation threshold, applied PER PURPOSE (share of that purpose's
# drawn legs resolved from its MARGINAL distribution instead of its (mode, band)
# cell). Mirrors the escort distance-by-type precedent: above this share the
# conditional table is effectively not doing its job, which is a failure signal
# rather than a tolerated cost (CLAUDE.md fallback-transparency rule 2). Not a
# scientifically derived bound.
SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE = 0.2


def srv_location_marginal_fallback_stat(purpose: str) -> str:
    """``subtype_stats`` key counting ``purpose``'s marginal-fallback draws."""
    return SRV_LOCATION_MARGINAL_FALLBACK_STAT_PREFIX + purpose


def srv_category_offer_column(category: str) -> str:
    """Candidate offer column advertising ``category`` as a placement activity.

    "leisure_visit" is served by the residential visit candidates
    (``VISIT_OFFER_COLUMN``, appended by
    :func:`append_residential_visit_candidates`); every other placement category
    carries its own ``offers_<category>`` column from
    :func:`append_location_category_columns` (buildings) or
    :func:`append_landuse_candidates` (ATKIS grid points).
    """
    return VISIT_OFFER_COLUMN if category == "leisure_visit" else "offers_" + category


def srv_category_potential_column(category: str) -> str:
    """Candidate potential column for ``category`` (see
    :func:`srv_category_offer_column` for the leisure_visit exception)."""
    return VISIT_POTENTIAL_COLUMN if category == "leisure_visit" else "pot_" + category


def _validate_srv_location_type_prerequisites(*, srv_location_types: bool,
                                              secondary_building_potentials: bool,
                                              leisure_subtype_split: bool,
                                              other_subtype_split: bool,
                                              leisure_visit_building_potential: bool) -> None:
    """Fail fast when ``secondary_srv_location_types`` is ON without the flags it
    is built on top of (issue #262, Task 8).

    Mirrors the identical guard in
    ``braunschweig.synthesis.locations.secondary_candidates.execute`` (the
    candidate set and the chainsolver must agree on the feature's preconditions):

    * ``secondary_building_potentials`` -- the per-category candidate columns
      only exist on the building-potential candidate set,
    * ``secondary_leisure_subtype_split`` / ``secondary_other_subtype_split`` --
      the MiD subtypes still supply the DISTANCE layers the category is drawn
      from (A2 draws the type conditioned on the sampled desired distance),
    * ``leisure_visit_building_potential`` -- the drawn ``leisure_visit``
      category is placed on the residential ``pot_visit`` candidates.

    Raises ``RuntimeError`` naming every missing flag; a no-op when the feature
    is OFF (no silent fallback to a partial category set).
    """
    if not srv_location_types:
        return
    missing = [
        name for name, enabled in (
            ("secondary_building_potentials", secondary_building_potentials),
            ("secondary_leisure_subtype_split", leisure_subtype_split),
            ("secondary_other_subtype_split", other_subtype_split),
            ("leisure_visit_building_potential", leisure_visit_building_potential),
        )
        if not enabled
    ]
    if missing:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] secondary_srv_location_types requires "
            f"{', '.join(missing)} to be ON (the SrV location-category placement is built "
            "on top of the building-potential candidate set, the MiD leisure/other "
            "subtype distance layers, and the residential visit machinery the drawn "
            "'leisure_visit' category is placed on). Enable the flag(s) above or disable "
            "secondary_srv_location_types."
        )


_SRV_LOCATION_TYPE_REQUIRED_COLUMNS = (
    "purpose", "mode", "band_lower_km", "band_upper_km", "is_marginal",
    "category", "probability", "n_legs_unweighted",
)
_SRV_LOCATION_TYPE_CATEGORIES_BY_PURPOSE = {
    "leisure": frozenset(SRV_LEISURE_CATEGORIES),
    "other": frozenset(SRV_OTHER_CATEGORIES),
}
_SRV_LOCATION_TYPE_PROB_TOLERANCE = 1e-6


def load_srv_location_type_probs(path: str) -> Dict[str, Dict[str, Any]]:
    """Load and validate the pinned SrV-2023-BS+RGB location-type probability
    table (Task 1, ``scripts/derive_srv_location_types.py`` ->
    ``eqasim-data/data/braunschweig/srv/srv2023_location_type_by_distance.csv``).

    Returns ``{purpose: {"band_edges_km": tuple, "cells": {(mode, band_idx):
    {category: probability}}, "marginal": {category: probability}}}`` for the
    two SrV-covered purposes, ``"leisure"`` and ``"other"``. Distance bands are
    EUCLIDEAN-equivalent kilometres: the pinned CSV converts the survey's
    routed GIS distance via ``euclid_km = routed_km / DETOUR_FACTOR`` with
    DETOUR_FACTOR=1.3 -- the same routed->euclidean assumption already used for
    the MiD distance layers (see the CSV's header comment). ``band_idx`` is the
    0-based index of a row's ``(band_lower_km, band_upper_km)`` pair within the
    shared, purpose-independent ``band_edges_km`` tuple.

    The pinned CSV omits cells thinner than its derive-script ``min_obs``
    threshold (see its header comment), so a given purpose's non-marginal rows
    may not cover every ``(mode, band)`` combination, and may not even mention
    every band. The band schedule itself, however, is common to both purposes
    (one derive-script run over one shared banding) -- so the edge tuple is
    reconstructed from the UNION of non-marginal ``(band_lower_km,
    band_upper_km)`` pairs across BOTH purposes, validated for monotonicity and
    contiguity over that union, and then reused for both purposes: even a
    purpose with zero non-marginal rows of its own gets the other purpose's
    edges, and a ``ValueError`` is raised only if NEITHER purpose has any
    non-marginal rows at all.

    Raises ``ValueError`` on: a missing required column; an unknown category
    for a purpose (i.e. not in ``SRV_LEISURE_CATEGORIES`` /
    ``SRV_OTHER_CATEGORIES``); cell or marginal probabilities that do not sum
    to 1 within ``1e-6``; a missing marginal row set for a purpose; or
    non-monotonic/non-contiguous band edges reconstructed from the rows.
    """
    frame = pd.read_csv(path, comment="#")

    missing_columns = sorted(set(_SRV_LOCATION_TYPE_REQUIRED_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise ValueError(
            f"[braunschweig.secondary_chainsolvers] {path}: missing required "
            f"column(s) {missing_columns}; expected "
            f"{sorted(_SRV_LOCATION_TYPE_REQUIRED_COLUMNS)}."
        )

    non_marginal = frame[frame["is_marginal"] == 0]
    if non_marginal.empty:
        raise ValueError(
            f"[braunschweig.secondary_chainsolvers] {path}: no non-marginal "
            "rows found for either purpose; cannot reconstruct band edges."
        )
    edge_pairs = sorted(set(
        (float(lower), float(upper))
        for lower, upper in zip(non_marginal["band_lower_km"], non_marginal["band_upper_km"])
    ))
    band_lowers = [lower for lower, _ in edge_pairs]
    band_uppers = [upper for _, upper in edge_pairs]
    if any(next_lower != upper for next_lower, upper in zip(band_lowers[1:], band_uppers[:-1])):
        raise ValueError(
            f"[braunschweig.secondary_chainsolvers] {path}: non-contiguous band "
            f"edges reconstructed from the non-marginal rows: lowers={band_lowers}, "
            f"uppers={band_uppers}."
        )
    band_edges = tuple([band_lowers[0]] + band_uppers)
    if any(a >= b for a, b in zip(band_edges, band_edges[1:])):
        raise ValueError(
            f"[braunschweig.secondary_chainsolvers] {path}: non-monotonic band "
            f"edges reconstructed from the non-marginal rows: {band_edges}."
        )

    result: Dict[str, Dict[str, Any]] = {}
    for purpose, allowed_categories in _SRV_LOCATION_TYPE_CATEGORIES_BY_PURPOSE.items():
        purpose_rows = frame[frame["purpose"] == purpose]

        unknown_categories = sorted(set(purpose_rows["category"]) - allowed_categories)
        if unknown_categories:
            raise ValueError(
                f"[braunschweig.secondary_chainsolvers] {path}: unknown "
                f"category(ies) {unknown_categories} for purpose {purpose!r}; "
                f"allowed: {sorted(allowed_categories)}."
            )

        marginal_rows = purpose_rows[purpose_rows["is_marginal"] == 1]
        if marginal_rows.empty:
            raise ValueError(
                f"[braunschweig.secondary_chainsolvers] {path}: missing marginal "
                f"row(s) for purpose {purpose!r}."
            )
        marginal = {
            str(category): float(probability)
            for category, probability in zip(marginal_rows["category"], marginal_rows["probability"])
        }
        marginal_sum = sum(marginal.values())
        if abs(marginal_sum - 1.0) > _SRV_LOCATION_TYPE_PROB_TOLERANCE:
            raise ValueError(
                f"[braunschweig.secondary_chainsolvers] {path}: marginal "
                f"probabilities for purpose {purpose!r} sum to {marginal_sum}, "
                f"expected 1.0 (tolerance {_SRV_LOCATION_TYPE_PROB_TOLERANCE})."
            )

        cells: Dict[Tuple[str, int], Dict[str, float]] = {}
        cell_rows = purpose_rows[purpose_rows["is_marginal"] == 0]
        for (mode, lower, upper), group in cell_rows.groupby(
            ["mode", "band_lower_km", "band_upper_km"], sort=False
        ):
            band_idx = band_edges.index(float(lower))
            cell_probs = {
                str(category): float(probability)
                for category, probability in zip(group["category"], group["probability"])
            }
            cell_sum = sum(cell_probs.values())
            if abs(cell_sum - 1.0) > _SRV_LOCATION_TYPE_PROB_TOLERANCE:
                raise ValueError(
                    f"[braunschweig.secondary_chainsolvers] {path}: cell "
                    f"probabilities for purpose {purpose!r}, mode {mode!r}, band "
                    f"[{lower}, {upper}) sum to {cell_sum}, expected 1.0 "
                    f"(tolerance {_SRV_LOCATION_TYPE_PROB_TOLERANCE})."
                )
            cells[(str(mode), band_idx)] = cell_probs

        result[purpose] = {
            "band_edges_km": band_edges,
            "cells": cells,
            "marginal": marginal,
        }

    return result


def _build_srv_location_decider(context, random_seed: int):
    """Build the per-leg SrV-2023 location-category decider, or ``None`` when
    OFF.

    Issue #262 (Task 7): for ``"leisure"``/``"other"`` legs, draws a
    SrV-2023-BS+RGB observed destination category conditioned on ``(mode,
    euclidean distance band)`` from the pinned probability table (Task 1;
    see ``load_srv_location_type_probs``). Returns a callable ``(purpose: str,
    mode: str, distance_m: float) -> (category: str, used_marginal: bool)``
    when ``secondary_srv_location_types`` is ON, else ``None`` (the
    byte-identical OFF path).

    ``distance_m`` must be the same EUCLIDEAN-equivalent distance used
    elsewhere in this stage's distance-band lookups, in METRES -- it is
    converted to euclidean km internally (``distance_m / 1000.0``) to match
    the pinned table's ``band_edges_km``; see ``load_srv_location_type_probs``
    for the routed->euclidean (DETOUR_FACTOR=1.3) assumption behind those
    bands. A distance that lands exactly on a band edge is assigned to the
    UPPER band (``np.searchsorted(band_edges[1:], ..., side="right")``),
    matching the pinned CSV's half-open ``[lower, upper)`` bands.

    ``purpose`` must be ``"leisure"`` or ``"other"`` -- any other value raises
    ``ValueError`` immediately, since escort and shop legs have their own
    dedicated deciders (``_build_escort_location_decider`` /
    ``_build_shop_subtype_decider``) and must never reach this one.

    A ``(mode, band)`` cell absent from the pinned table (thinner than the
    derive script's ``min_obs`` threshold) falls back to the purpose's
    marginal distribution; the call reports this via the returned
    ``used_marginal`` flag so callers can log the fallback rate explicitly
    rather than let it happen silently (CLAUDE.md "Fallback transparency").

    Every call draws exactly ONE uniform sample from a dedicated seeded RNG,
    ``np.random.RandomState(random_seed + SRV_LOCATION_SEED_OFFSET)``, resolved
    via ``_inverse_cdf_choice`` over the SORTED category names of the resolved
    probability vector. This stream is separate from the distance-sampling RNG
    (``random``), the shop/leisure/other subtype streams, and the escort
    location stream, so enabling/disabling ``secondary_srv_location_types`` (or
    any sibling flag) never perturbs another decider's draws -- the OFF path
    stays byte-identical.
    """
    if not context.config("secondary_srv_location_types"):
        return None

    path = context.config("srv_location_type_probs_path")
    tables = load_srv_location_type_probs(path)

    rng = np.random.RandomState(int(random_seed) + SRV_LOCATION_SEED_OFFSET)

    def decide(purpose: str, mode: str, distance_m: float) -> Tuple[str, bool]:
        table = tables.get(purpose)
        if table is None:
            raise ValueError(
                "[braunschweig.secondary_chainsolvers] _build_srv_location_decider: "
                f"unknown purpose {purpose!r}; expected one of {sorted(tables)} "
                "(escort/shop legs must not reach the SrV location decider)."
            )
        band_edges = table["band_edges_km"]
        distance_km = distance_m / 1000.0
        band_idx = int(np.searchsorted(band_edges[1:], distance_km, side="right"))
        cell_probs = table["cells"].get((mode, band_idx))
        used_marginal = cell_probs is None
        probs = table["marginal"] if used_marginal else cell_probs
        group_names = tuple(sorted(probs))
        category = _inverse_cdf_choice(probs, group_names, rng.random_sample())
        return category, used_marginal

    return decide
