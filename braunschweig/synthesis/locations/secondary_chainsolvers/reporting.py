"""Run transparency reporting: fallback accounting, clip shares, draw summary.

Implements the CLAUDE.md fallback-transparency requirements for this stage:
the primary-vs-fallback accounting summary, the excursion boundary-clip
transparency lines (issue #127 Task 6), the SrV location-type draw-summary
lines and per-run CSV artifact (issue #262 Task 9). All output is logged
explicitly -- a fallback or clip that fires must never be silent.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from braunschweig.calibration.secondary_measurement import boundary_clip_share

from .candidate_columns import _ACTIVITY_POTENTIAL_COLUMN
from .plans import DISTANCE_LABEL_COLUMN
from .srv_location_types import (
    SRV_LEISURE_CATEGORIES,
    SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE,
    SRV_LOCATION_PURPOSES,
    SRV_LOCATION_STAT_PREFIX,
    SRV_OTHER_CATEGORIES,
    SRV_PLACEMENT_CATEGORIES,
    srv_category_potential_column,
    srv_location_marginal_fallback_stat,
)


# ---------------------------------------------------------------------------
# Primary-vs-fallback accounting (fallback transparency)
#
# The PRIMARY method is the chainsolvers carla solver (cs.solve). Problems carla
# cannot place -- unbounded chains (no anchored origin and/or destination) plus
# bounded problems carla raised on -- fall through to the RDA / random FALLBACK.
# A high fallback share means the primary solver is effectively not working, so
# the share must be observable as an explicit rate rather than hidden inside the
# separate per-stage prints.
# ---------------------------------------------------------------------------

# Fallback share above which the summary line is flagged. A fallback share over
# this threshold means a large fraction of secondary trips are placed by the
# lower-quality fallback rather than the carla primary solver, i.e. carla is
# effectively not working and the result should not be trusted without
# investigation.
DEFAULT_FALLBACK_WARNING_SHARE = 0.20


def _fallback_accounting_summary(n_total_problems: int,
                                 n_unbounded: int,
                                 n_failed_bounded: int,
                                 warning_share: float = DEFAULT_FALLBACK_WARNING_SHARE) -> str:
    """Build the one-line PRIMARY (carla) vs FALLBACK accounting summary.

    Pure (no I/O, no randomness, no side effects) so it can be unit-tested
    without the optional ``chainsolvers`` package. It only counts; it never
    influences solving, fallback selection, the RNG, or any placed result.

    Args:
        n_total_problems: total number of assignment problems enumerated
            (bounded + unbounded). Equals ``len(problems)``.
        n_unbounded: unbounded problems routed straight to the fallback
            (no anchored origin and/or destination). Equals ``len(unbounded_idx)``.
        n_failed_bounded: bounded problems carla raised on, routed to the
            fallback. Equals ``len(failed_problem_idx)``.
        warning_share: fallback share (in [0, 1]) at or above which the line is
            prefixed with ``"WARNING: "``.

    Returns:
        A single human-readable log line. ``n_fallback = n_unbounded +
        n_failed_bounded`` is the FALLBACK count; the remainder
        (``n_total_problems - n_fallback``) is the PRIMARY (carla) count. The
        fallback share is reported as a percentage of all problems; when it is
        at or above ``warning_share`` the line is prefixed with ``"WARNING: "``.
    """
    n_fallback = n_unbounded + n_failed_bounded
    n_primary = n_total_problems - n_fallback
    if n_total_problems > 0:
        fallback_share = n_fallback / n_total_problems
    else:
        fallback_share = 0.0

    prefix = "WARNING: " if fallback_share >= warning_share else ""
    return (
        f"[braunschweig.secondary_chainsolvers] {prefix}primary/fallback split: "
        f"primary (carla) placed {n_primary:,}/{n_total_problems:,} problems "
        f"({(1.0 - fallback_share) * 100.0:.1f}%); "
        f"fallback placed {n_fallback:,}/{n_total_problems:,} "
        f"({fallback_share * 100.0:.1f}%) "
        f"[unbounded={n_unbounded:,}, carla-failed-bounded={n_failed_bounded:,}]"
    )


# ---------------------------------------------------------------------------
# Excursion boundary-clip transparency (issue #127, Task 6)
#
# The measured "leisure_excursion" MiD donor distances (45-100 km, design
# spec Taxonomy table) may exceed the farthest candidate actually available
# to a given leg's anchor -- buildings plus the external Gemeinde centroids
# appended by build_secondary_candidates "so carla can match desired
# distances beyond the study area instead of truncating to the area edge"
# (see the comment there). When the desired distance exceeds even that
# farthest candidate, the leg cannot be placed at its desired distance and
# necessarily clips to the edge of the candidate universe. This is measured
# and logged ONLY -- it changes no placement, sampling, or RNG draw.
# ---------------------------------------------------------------------------

# Clip share above which the summary line is flagged. Mirrors
# DEFAULT_FALLBACK_WARNING_SHARE's role: a "leisure_excursion" clip share at
# or above this fraction means most excursion legs cannot reach their
# measured donor distance with the current candidate set (region extent /
# external-candidate reach), and the resulting realised distances should not
# be read as if they matched the MiD donor tail without noting this.
DEFAULT_EXCURSION_CLIP_WARNING_SHARE = 0.50


def _excursion_desired_distances_and_anchors_m(plans_df: pd.DataFrame,
                                                problems: List[Dict[str, Any]],
                                                row_mask=None
                                                ) -> Tuple[np.ndarray, np.ndarray]:
    """Desired distances (metres) and anchors for the selected excursion legs.

    ``plans_df`` must still carry ``_problem_idx`` (i.e. be the frame returned
    by ``_build_plans_df``, before the caller drops the helper columns for
    ``cs.solve()``). Every BOUNDED problem has both ``origin`` and
    ``destination`` fixed -- ``_build_plans_df`` routes any problem missing
    either anchor to ``unbounded_idx`` before this frame is built -- so
    ``problem["origin"]`` is always available here. The fixed origin (the
    person's actual anchor for that chain, e.g. home) is used as the leg's
    reference point for the candidate-reach ceiling: it is always available,
    unlike an intermediate, still-unresolved secondary location.

    ``row_mask`` selects the rows to measure. ``None`` (default) keeps the legacy
    selection -- ``to_act_type == "leisure_excursion"``, i.e. the placement
    activity, which IS the MiD subtype whenever ``secondary_srv_location_types``
    is OFF. With that flag ON the placement activity is the drawn SrV category
    instead, so the caller passes a boolean mask built from
    ``DISTANCE_LABEL_COLUMN`` (optionally intersected with a placement category);
    the coordinate/anchor logic below is shared by both paths rather than
    duplicated (issue #262).

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        ``(desired_m, anchors_xy)``, parallel arrays of length
        n_selected_legs; ``anchors_xy`` has shape ``(n, 2)``. Both are empty
        when the selection matches no row (e.g. the flag is OFF and no bounded
        leg happened to draw that group).

    Raises
    ------
    ValueError
        If ``row_mask`` length does not match ``plans_df``.
    """
    if plans_df.empty or "to_act_type" not in plans_df.columns:
        return np.array([], dtype=float), np.empty((0, 2), dtype=float)
    if row_mask is None:
        mask = (plans_df["to_act_type"] == "leisure_excursion").to_numpy()
    else:
        mask = np.asarray(row_mask, dtype=bool)
        if mask.shape != (len(plans_df),):
            raise ValueError(
                "[braunschweig.secondary_chainsolvers] row_mask must have one entry "
                f"per plans_df row: got {mask.shape}, expected ({len(plans_df)},)."
            )
    if not mask.any():
        return np.array([], dtype=float), np.empty((0, 2), dtype=float)
    desired_m = plans_df.loc[mask, "distance_meters"].to_numpy(dtype=float)
    prob_idx = plans_df.loc[mask, "_problem_idx"].to_numpy()
    anchors_xy = np.array(
        [
            (float(problems[p]["origin"][0, 0]), float(problems[p]["origin"][0, 1]))
            for p in prob_idx
        ],
        dtype=float,
    )
    return desired_m, anchors_xy


def _candidate_reach_ceiling_m(anchors_xy: np.ndarray, candidate_xy: np.ndarray) -> np.ndarray:
    """Per-anchor farthest-candidate distance (metres): the candidate-radius ceiling.

    For each anchor in ``anchors_xy`` (shape ``(n, 2)``), returns the maximum
    Euclidean distance to any row of ``candidate_xy`` (shape ``(m, 2)``, both
    in the same projected CRS, e.g. EPSG:25832 metres). This is a hard upper
    bound on what any placement could achieve from that anchor: no candidate
    lies farther away, so a desired distance exceeding it can never be
    matched. This does NOT model chainsolvers' own internal candidate-search
    radius (an implementation detail of the third-party ``chainsolvers``
    package, which adaptively widens its search) -- it is a purely
    geometric, data-driven ceiling derived from the candidate coordinates we
    actually feed into the solver, independent of solver internals.

    Raises
    ------
    ValueError
        If ``candidate_xy`` is empty (no ceiling can be computed).
    """
    if len(candidate_xy) == 0:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] cannot compute the "
            "leisure_excursion candidate-reach ceiling: the candidate "
            "coordinate set is empty."
        )
    # A small per-anchor loop rather than materialising the full (n x m)
    # distance matrix at once: anchor counts are the (comparatively small)
    # 'leisure_excursion' leg count, so this stays cheap even against the
    # full candidate set (tens of thousands of buildings + external
    # centroids).
    ceilings = np.empty(len(anchors_xy), dtype=float)
    for i in range(len(anchors_xy)):
        dx = candidate_xy[:, 0] - anchors_xy[i, 0]
        dy = candidate_xy[:, 1] - anchors_xy[i, 1]
        ceilings[i] = np.sqrt(dx * dx + dy * dy).max()
    return ceilings


def _excursion_boundary_clip_summary(n_clipped: int, n_total: int,
                                     warning_share: float = DEFAULT_EXCURSION_CLIP_WARNING_SHARE,
                                     placement_category: str = None) -> str:
    """Build the one-line ``"leisure_excursion"`` boundary-clip transparency summary.

    Pure (no I/O, no randomness, no side effects) -- mirrors
    ``_fallback_accounting_summary``'s style. ``n_clipped`` counts
    ``"leisure_excursion"`` legs whose sampled desired distance exceeds the
    candidate-radius ceiling (``_candidate_reach_ceiling_m``): these legs
    cannot be placed at their desired distance and clip to the edge of the
    candidate universe. Measurement only; never influences placement,
    sampling, or the RNG. Logged even when ``n_clipped`` is 0 (CLAUDE.md
    "fallback transparency": the rate must always be observable, not only
    when it is non-zero).

    Args:
        n_clipped: legs whose desired distance exceeds the ceiling.
        n_total: total ``"leisure_excursion"`` legs measured this run.
        warning_share: clip share (in [0, 1]) at or above which the line is
            prefixed with ``"WARNING: "``.
        placement_category: with ``secondary_srv_location_types`` ON the same
            measurement is resolved per DRAWN placement category (each category
            has its own candidate pool, hence its own reach ceiling); naming it
            here tags the line ``[placement=<category>]``. ``None`` (default)
            produces the legacy, placement-agnostic wording unchanged.
    """
    scope = f" [placement={placement_category}]" if placement_category else ""
    if n_total == 0:
        return (
            "[braunschweig.secondary_chainsolvers] leisure_excursion "
            f"boundary-clip{scope}: 0 bounded 'leisure_excursion' legs this run "
            "(nothing to measure)."
        )
    share = n_clipped / n_total
    prefix = "WARNING: " if share >= warning_share else ""
    return (
        f"[braunschweig.secondary_chainsolvers] {prefix}leisure_excursion "
        f"boundary-clip{scope}: {n_clipped:,}/{n_total:,} ({share * 100.0:.1f}%) "
        "bounded excursion legs sample a desired distance beyond the "
        "farthest available candidate and clip to the region edge."
    )


def _srv_placement_potential_column(placement_activity: str) -> str:
    """Candidate potential column backing a drawn SrV placement activity.

    The drawn category is the chainsolver activity, so its candidate pool is
    exactly the rows the emission in :func:`_build_locations_df` offers it to:
    a category activity maps to its own ``pot_<category>`` (``pot_visit`` for
    ``leisure_visit``, see :func:`srv_category_potential_column`), while the two
    aggregate-placement categories resolve to the plain purpose and therefore to
    ``pot_leisure`` / ``pot_other`` via ``_ACTIVITY_POTENTIAL_COLUMN``. One
    mapping, used by both the emission and this diagnostic.
    """
    if placement_activity in SRV_PLACEMENT_CATEGORIES:
        return srv_category_potential_column(placement_activity)
    return _ACTIVITY_POTENTIAL_COLUMN[placement_activity]


def _srv_excursion_boundary_clip_lines(plans_df: pd.DataFrame,
                                       problems: List[Dict[str, Any]],
                                       df_secondary,
                                       warning_share: float = DEFAULT_EXCURSION_CLIP_WARNING_SHARE
                                       ) -> List[str]:
    """Excursion boundary-clip lines under SrV placement, PER DRAWN CATEGORY.

    Restores the Task-6 diagnostic (issue #127) for the
    ``secondary_srv_location_types`` ON path, where it had gone structurally
    inert: the diagnostic used to select its legs by placement activity
    ``== "leisure_excursion"``, but with SrV placement the activity is the drawn
    category and the MiD subtype survives only as ``DISTANCE_LABEL_COLUMN``.
    Legs are therefore selected by that label, and then grouped by the drawn
    placement category -- each category is placed on its OWN candidate pool
    (landuse points for ``leisure_outdoor``, residential rows for
    ``leisure_visit``, the aggregate buildings for ``leisure_misc``, plus the
    external Gemeinde centroids, which carry every category potential since the
    escape step), so each has its own reach ceiling and must be measured
    separately. Measurement only: reads the already-sampled desired distances
    and the already-assembled candidate set, places nothing, draws no random
    number.

    Returns one line per drawn category (alphabetically, for a deterministic
    log) plus one aggregate total line, or a single "nothing to measure" line
    when no bounded excursion leg exists.

    Raises
    ------
    RuntimeError
        If a drawn placement category has zero positive-potential candidates --
        broken wiring rather than thin data, mirroring the legacy
        ``pot_leisure`` fail-fast.
    """
    if plans_df.empty or DISTANCE_LABEL_COLUMN not in plans_df.columns:
        return [_excursion_boundary_clip_summary(0, 0)]
    excursion_mask = (plans_df[DISTANCE_LABEL_COLUMN] == "leisure_excursion").to_numpy()
    if not excursion_mask.any():
        return [_excursion_boundary_clip_summary(0, 0)]

    lines = []
    n_clipped_all = 0
    n_total_all = 0
    placement_acts = plans_df.loc[excursion_mask, "to_act_type"]
    for category in sorted(set(placement_acts)):
        category_mask = excursion_mask & (plans_df["to_act_type"] == category).to_numpy()
        desired_m, anchors_xy = _excursion_desired_distances_and_anchors_m(
            plans_df, problems, row_mask=category_mask)
        if desired_m.size == 0:
            continue
        potential_column = _srv_placement_potential_column(category)
        if potential_column not in df_secondary.columns:
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] leisure_excursion boundary-clip: "
                f"placement category {category!r} needs candidate column "
                f"'{potential_column}', which the candidate set does not carry -- the "
                "SrV candidate wiring is broken."
            )
        category_candidates = df_secondary.loc[df_secondary[potential_column] > 0.0]
        if len(category_candidates) == 0:
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] leisure_excursion boundary-clip "
                f"found zero candidates with {potential_column} > 0, but bounded "
                f"'leisure_excursion' legs were placed on {category!r} -- the candidate "
                "wiring is broken (this is not an expected empty-candidate run)."
            )
        candidate_xy = np.column_stack((
            category_candidates.geometry.x.to_numpy(),
            category_candidates.geometry.y.to_numpy(),
        ))
        ceiling_m = _candidate_reach_ceiling_m(anchors_xy, candidate_xy)
        _, n_clipped, n_total = boundary_clip_share(desired_m, ceiling_m)
        n_clipped_all += n_clipped
        n_total_all += n_total
        lines.append(_excursion_boundary_clip_summary(
            n_clipped, n_total, warning_share=warning_share, placement_category=category))
    lines.append(_excursion_boundary_clip_summary(
        n_clipped_all, n_total_all, warning_share=warning_share))
    return lines




def _srv_location_draw_summary_lines(subtype_stats: Dict[str, int]) -> List[str]:
    """One draw-rate line per SrV-covered purpose plus a pooled-total line.

    Pure (no I/O, no randomness) so the exact wording is testable. Each purpose's
    line reports how many bounded legs drew each location category AND that
    purpose's OWN marginal-fallback rate -- draws resolved from the purpose's
    marginal distribution because the pinned table has no ``(mode, distance
    band)`` cell for the leg (CLAUDE.md fallback transparency: the rate must
    always be observable, not only when it is non-zero). The
    ``SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE`` escalation is evaluated PER
    PURPOSE, because a pooled rate lets a badly covered purpose hide behind a
    well covered one (the purposes differ in leg volume by several times). The
    trailing pooled line is informational only and never warns.
    """
    lines = []
    n_all = 0
    n_marginal_all = 0
    for purpose, categories in (
        ("leisure", SRV_LEISURE_CATEGORIES), ("other", SRV_OTHER_CATEGORIES),
    ):
        counts = {
            name: subtype_stats[SRV_LOCATION_STAT_PREFIX + name] for name in categories
        }
        n_legs = sum(counts.values())
        n_all += n_legs
        shares = ", ".join(
            f"{name} {count:,} ({_rate_pct(count, n_legs):.1f}%)"
            for name, count in counts.items()
        )
        n_marginal = subtype_stats[srv_location_marginal_fallback_stat(purpose)]
        n_marginal_all += n_marginal
        share = (n_marginal / n_legs) if n_legs else 0.0
        prefix = "WARNING: " if share >= SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE else ""
        lines.append(
            f"[braunschweig.secondary_chainsolvers] {prefix}srv location draw ({purpose}): "
            f"{n_legs:,} bounded {purpose} legs -> {shares}; marginal fallback (no "
            f"(mode, band) cell in the pinned table) {n_marginal:,}/{n_legs:,} "
            f"({share * 100.0:.1f}%)"
        )
    lines.append(
        "[braunschweig.secondary_chainsolvers] srv location draw: marginal fallback "
        f"total {n_marginal_all:,}/{n_all:,} "
        f"({_rate_pct(n_marginal_all, n_all):.1f}%) -- see the per-purpose lines above "
        "for the rates the warning threshold is applied to"
    )
    return lines


# Pinned draw-vs-reference table produced by scripts/derive_srv_location_types.py
# (Task 1). Committed reference data -- regenerate there, never edit by hand.
# Also carries purpose="shop" rows (a validation-only contribution for a
# different feature, issue #242); srv_location_draw_summary EXCLUDES them --
# shop location choice is not decided by this decider.
DEFAULT_SRV_LOCATION_TYPE_SHARES_PATH = (
    "eqasim-data/data/braunschweig/srv/srv2023_secondary_type_shares.csv"
)

# HEURISTIC escalation threshold (percentage points, NOT a scientifically
# derived bound): the maximum tolerated |drawn_share - reference_share| for a
# category before the per-run draw-summary writer emits a WARN. Configurable
# via ``srv_location_share_warn_pp`` (declared in ``configure``).
DEFAULT_SRV_LOCATION_SHARE_WARN_PP = 5.0

# Column order of the srv_location_draw_summary.csv artifact and the
# DataFrame returned by srv_location_draw_summary(); kept as a module
# constant so the writer and the tests agree on the schema.
SRV_LOCATION_DRAW_SUMMARY_COLUMNS = (
    "purpose", "category", "drawn_share", "reference_share",
    "drawn_median_desired_km", "reference_median_euclid_km", "n_drawn",
)

# Honesty note (CLAUDE.md "No invented reference values" + issue #262 plan):
# this summary is a DRAW-COHERENCE check, not a validation of realised model
# output against SrV. Reused verbatim as the CSV header comment and quoted in
# the function docstring below so both surfaces state the same caveat.
SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE = (
    "This table compares DRAWN desired-distance medians (the leg's sampled "
    "target distance, before candidate search) against the SrV "
    "euclidean-equivalent medians of srv2023_secondary_type_shares.csv. It is "
    "a draw-coherence check on the category<->distance decider, NOT a "
    "validation of REALISED (placed) distances: carla's candidate search can "
    "still deviate from the desired distance (top_n selection inertness, "
    "backlog Tier-0 item (a)), which is assessed separately in the A/B "
    "validation run. Never read this file as \"validated against SrV\"."
)


def srv_location_draw_summary(subtype_stats: Dict[str, int],
                               desired_by_category: Dict[str, List[float]],
                               shares_df: pd.DataFrame) -> pd.DataFrame:
    """Per-category drawn-vs-reference coherence table (issue #262, Task 9).

    IMPORTANT -- read before using this table: it compares DRAWN
    desired-distance medians against the SrV euclidean-equivalent medians. It
    is a draw-coherence check on the ``srv_location_decider``, NOT a
    validation of REALISED (placed) distances -- carla's candidate search can
    still deviate from the desired distance (top_n selection inertness,
    backlog Tier-0 item (a)); that is assessed in the A/B validation run, not
    here. See :data:`SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE`.

    Parameters
    ----------
    subtype_stats:
        The ``subtype_stats`` dict returned by ``_build_plans_df`` (namespaced
        ``SRV_LOCATION_STAT_PREFIX + category`` draw counters). Missing keys
        are treated as zero draws (a category the decider never drew in this
        run, e.g. under a small/synthetic input).
    desired_by_category:
        The ``desired_by_category`` dict returned by ``_build_plans_df``:
        ``{category: [desired_km, ...]}``, keyed by the BARE category name.
        A category absent from this dict gets a NaN
        ``drawn_median_desired_km`` (no legs to take a median over).
    shares_df:
        The pinned ``srv2023_secondary_type_shares.csv`` frame (columns
        ``purpose``, ``category``, ``weight_share``, ``weighted_median_euclid_km``,
        among others -- see :func:`load_srv_location_type_shares`). Its
        ``purpose="shop"`` rows are VALIDATION-ONLY rows for a different
        feature (issue #242) and are excluded here: this decider only draws
        for ``SRV_LOCATION_PURPOSES`` (leisure, other).

    Returns
    -------
    pandas.DataFrame
        One row per ``(purpose, category)`` for every category in
        ``SRV_LEISURE_CATEGORIES`` / ``SRV_OTHER_CATEGORIES`` (the full pinned
        vocabulary for the two SrV-covered purposes), columns
        :data:`SRV_LOCATION_DRAW_SUMMARY_COLUMNS`. ``drawn_share`` is
        ``n_drawn`` over that PURPOSE's total drawn legs (sums to 1.0 per
        purpose when at least one leg was drawn); a category the decider
        never drew still gets its own row with ``n_drawn=0`` and
        ``drawn_share=0.0`` (never silently omitted). ``reference_share`` /
        ``reference_median_euclid_km`` are looked up from ``shares_df``; a
        category absent from the pinned reference (should not occur for the
        fixed vocabulary above, but not assumed) gets NaN there instead of a
        fabricated value.
    """
    shares_lookup = shares_df[shares_df["purpose"].isin(SRV_LOCATION_PURPOSES)].set_index(
        ["purpose", "category"]
    )

    rows = []
    for purpose, categories in (
        ("leisure", SRV_LEISURE_CATEGORIES), ("other", SRV_OTHER_CATEGORIES),
    ):
        counts = {
            category: int(subtype_stats.get(SRV_LOCATION_STAT_PREFIX + category, 0))
            for category in categories
        }
        n_legs = sum(counts.values())
        for category in categories:
            n_drawn = counts[category]
            drawn_share = (n_drawn / n_legs) if n_legs else float("nan")
            desired_km = desired_by_category.get(category, [])
            drawn_median_desired_km = float(np.median(desired_km)) if desired_km else float("nan")
            if (purpose, category) in shares_lookup.index:
                reference_row = shares_lookup.loc[(purpose, category)]
                reference_share = float(reference_row["weight_share"])
                reference_median_euclid_km = float(reference_row["weighted_median_euclid_km"])
            else:
                reference_share = float("nan")
                reference_median_euclid_km = float("nan")
            rows.append({
                "purpose": purpose,
                "category": category,
                "drawn_share": drawn_share,
                "reference_share": reference_share,
                "drawn_median_desired_km": drawn_median_desired_km,
                "reference_median_euclid_km": reference_median_euclid_km,
                "n_drawn": n_drawn,
            })
    return pd.DataFrame(rows, columns=list(SRV_LOCATION_DRAW_SUMMARY_COLUMNS))


def load_srv_location_type_shares(path: str) -> pd.DataFrame:
    """Load the pinned ``srv2023_secondary_type_shares.csv`` reference table.

    Thin wrapper around ``pd.read_csv(path, comment="#")`` (the file's header
    is a block of ``#``-prefixed provenance comments, see the file itself);
    kept as a named function so the load convention is documented once and
    both the stage writer and the tests share it.
    """
    return pd.read_csv(path, comment="#")





def _rate_pct(count, total) -> float:
    """Percentage of ``count`` over ``total``, or 0.0 when ``total`` is falsy
    (guards the ZeroDivisionError on an empty leg group, e.g. no bounded
    escort legs at all). Shared by every fallback-rate / per-group-share
    percentage the execute() summary print block below reports, so the
    guarded formula is defined once instead of being repeated inline at
    each call site."""
    return 100.0 * count / total if total else 0.0


# Filename of the per-run draw-summary artifact written by
# _write_srv_location_draw_summary (issue #262, Task 9). Kept as a module
# constant so the writer and any downstream reader agree on the name.
SRV_LOCATION_DRAW_SUMMARY_FILENAME = "srv_location_draw_summary.csv"


def _write_srv_location_draw_summary(context, subtype_stats: Dict[str, int],
                                      desired_by_category: Dict[str, List[float]]) -> None:
    """Write the per-run draw-summary artifact and WARN on large deviations
    (issue #262, Task 9).

    Loads the pinned ``srv_location_type_shares_path`` reference table, builds
    :func:`srv_location_draw_summary`, and writes it as
    ``SRV_LOCATION_DRAW_SUMMARY_FILENAME`` under the stage's synpp output
    directory (``context.path()``), prefixed with
    :data:`SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE` as a ``#``-commented CSV
    header (mirrors the pinned-CSV convention used by
    ``scripts/derive_srv_location_types.py``: read back with
    ``pd.read_csv(path, comment="#")``). Emits one WARN line per category
    whose ``|drawn_share - reference_share|`` exceeds
    ``srv_location_share_warn_pp`` percentage points (a category with no
    pinned reference, i.e. NaN ``reference_share``, is skipped -- there is
    nothing to compare against, and that is not itself a draw failure).

    This is a stage-side effect (file I/O + logging), never called on the OFF
    path -- the caller in ``execute()`` gates it on
    ``srv_location_decider is not None``.
    """
    shares_path = context.config("srv_location_type_shares_path")
    shares_df = load_srv_location_type_shares(shares_path)
    summary_df = srv_location_draw_summary(subtype_stats, desired_by_category, shares_df)

    # Zero-leg purpose (review finding, Minor): a purpose with reference rows
    # but NO drawn legs at all is near-100% non-coverage and must be loud, not
    # silent -- the per-category loop below would otherwise say nothing about
    # it (drawn_share is 0.0 for every one of its categories, which reads like
    # "no deviation" unless the total is checked separately).
    for purpose in SRV_LOCATION_PURPOSES:
        n_purpose_drawn = int(summary_df.loc[summary_df["purpose"] == purpose, "n_drawn"].sum())
        if n_purpose_drawn == 0:
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: 0 drawn legs for purpose {purpose!r} while "
                "secondary_srv_location_types is ON -- verify this run actually "
                f"produced bounded {purpose!r} legs (an entirely unbounded/"
                "fallback-only run would explain this, but a bounded run with "
                "zero draws for a whole purpose is a wiring bug, not noise)."
            )

    warn_pp = float(context.config("srv_location_share_warn_pp"))
    for row in summary_df.itertuples(index=False):
        if pd.isna(row.reference_share):
            # Fallback-transparency rule (CLAUDE.md): a category from the fixed
            # code vocabulary (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES)
            # with NO row in the pinned reference is a vocabulary-drift signal
            # -- e.g. the CSV was regenerated with a renamed or dropped
            # category -- and must be surfaced loudly, never silently skipped.
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: {row.purpose}/{row.category} has NO matching row in "
                f"the pinned reference ({shares_path}) -- possible drift between "
                "the code vocabulary (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES) "
                "and the pinned CSV; reference_share/reference_median_euclid_km "
                "are NaN and this category cannot be compared."
            )
            continue
        deviation_pp = abs(row.drawn_share - row.reference_share) * 100.0
        if deviation_pp > warn_pp:
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: {row.purpose}/{row.category} drawn_share "
                f"{row.drawn_share * 100.0:.1f}% deviates from the pinned reference "
                f"share {row.reference_share * 100.0:.1f}% by {deviation_pp:.1f} "
                f"percentage points (> srv_location_share_warn_pp={warn_pp:.1f})."
            )

    output_path = "%s/%s" % (context.path(), SRV_LOCATION_DRAW_SUMMARY_FILENAME)
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(
            "# " + SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE.replace("\n", "\n# ") + "\n"
        )
        handle.write(f"# Reference: {shares_path}\n")
        handle.write(f"# srv_location_share_warn_pp={warn_pp}\n")
        summary_df.to_csv(handle, index=False)
    print(
        "[braunschweig.secondary_chainsolvers] wrote srv location draw summary "
        f"({len(summary_df)} category rows) to {output_path}"
    )
