"""SrV location-category candidate columns and ATKIS landuse escapes.

Issue #262: per-category building offer/potential columns
(``append_location_category_columns``), ATKIS landuse grid-point candidates
(``append_landuse_candidates``), external Gemeinde-centroid category escapes
(``append_external_category_escapes``) and the fail-fast supply checks. All
functions operate on the assembled candidate GeoDataFrame consumed by BOTH
this stage and the ``secondary_candidates`` stage.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Dict

import geopandas as gpd
import numpy as np
import pandas as pd

from .candidate_columns import VISIT_CANDIDATE_WARN_FACTOR, VISIT_POTENTIAL_COLUMN
from .srv_location_types import (
    EXTERNAL_CATEGORY_ESCAPE_CATEGORIES,
    SRV_LEISURE_CATEGORIES,
)


# ---------------------------------------------------------------------------
# SrV-grounded location-category candidates (issue #262): per-category
# building offer/potential columns + ATKIS landuse grid-point candidates.
# ---------------------------------------------------------------------------

# Offer/potential columns for the SrV location categories (issue #262).
#
# PLAN AMENDMENT (issue #262, post-Task-4 review): the leisure_* categories
# genuinely MASK the pot_leisure aggregate a sec_b_* row already carries (see
# ``build_secondary_candidates`` for how pot_leisure is derived) -- that part
# is unchanged. The errand_* categories do NOT mask pot_other: every sec_b_*
# row carries pot_other=0.0 by construction (build_secondary_candidates keeps
# only buildings with retail>0 | leisure>0), and errand-class buildings
# (hospitals, authorities, service businesses, ...) are therefore excluded
# from the candidate set entirely. Masking pot_other would be a structural
# zero-supply bug, not a thin-data limitation. ``append_location_category_columns``
# now derives the two errand categories' potential directly from
# ``df_potentials`` (the ``derive_other_potential`` cap-and-floor formula,
# applied per category -- see that function for the shared numerics) and
# appends a NEW ``sec_b_<building_id>`` candidate row for every errand-class
# building missing from ``candidates``. The dict below is kept as the
# leisure/errand grouping key other callers (e.g.
# ``secondary_candidates.execute``) use to select the leisure subset; for the
# errand entries it no longer means "mask this column literally".
SRV_BUILDING_CATEGORY_BASE_POTENTIAL = {
    "leisure_culture": "pot_leisure",
    "leisure_gastronomy": "pot_leisure",
    "leisure_sports": "pot_leisure",
    "errand_authority_medical": "pot_other",
    "errand_service": "pot_other",
}


def append_location_category_columns(candidates: gpd.GeoDataFrame,
                                      df_potentials: gpd.GeoDataFrame,
                                      mapping: pd.DataFrame,
                                      *, min_volume_m3: float = 50.0,
                                      cap_percentile: float = 0.99) -> gpd.GeoDataFrame:
    """Add per-category offer/potential columns to the candidates frame (issue #262).

    For each of the five ``SRV_BUILDING_CATEGORY_BASE_POTENTIAL`` categories,
    adds ``offers_<category>`` (bool) and ``pot_<category>`` (float) to
    EVERY row of ``candidates``. The three ``leisure_*`` categories MASK the
    existing ``pot_leisure`` aggregate on ``sec_b_<building_id>`` rows already
    present in ``candidates``: for a row whose Bosserhof class maps to
    ``<category>`` in ``mapping``, ``pot_<category> = pot_leisure`` (a mask,
    not a new formula) and ``offers_<category> = pot_<category> > 0``.

    The two ``errand_*`` categories (``errand_authority_medical``,
    ``errand_service``) are DIFFERENT: masking ``pot_other`` would be
    structurally zero everywhere, because ``build_secondary_candidates`` sets
    ``pot_other=0.0`` on every ``sec_b_*`` row and excludes errand-class
    buildings (hospitals, authorities, services, ...) from the candidate set
    entirely (its keep-filter is ``retail > 0 | leisure > 0``). Their
    potential is instead computed directly from ``df_potentials`` with the
    same cap-and-floor formula as ``secondary_other_potential.derive_other_potential``,
    applied per category:

        cap_<category>  = nanquantile(potential_generic over buildings whose
                           class maps to <category>, cap_percentile)
        pot_<category>  = min(potential_generic, cap_<category>) where the
                           building's class maps to <category>, else 0.0
        pot_<category>  = 0.0 where volume_m3 < min_volume_m3

    A building with a positive computed potential is guaranteed a
    ``sec_b_<building_id>`` candidate row: if one already exists (e.g. the
    building also has retail/leisure potential) its errand columns are
    updated in place; otherwise a NEW row is appended, carrying ONLY that
    errand category's offer/potential (every other offer/potential column --
    ``offers_shop``, ``offers_leisure``, ``offers_other``, ``offers_escort``,
    the other four SrV categories, etc. -- is ``False`` / ``0.0``). A
    class-member building that is NOT already a candidate and whose computed
    potential is zero (``volume_m3 < min_volume_m3``, or a class with no
    members forcing the all-building quantile cap onto a zero row) gets NO
    new row at all -- appending an inert all-False/0.0 row would only pollute
    the candidate set (and, downstream, ``facilities.xml``) without ever being
    selectable.

    Every row that is neither a matching leisure building nor a matching
    errand building -- non-building candidates (external centroids,
    ``sec_res_*``, ``sec_edu_*``, legacy ``sec_*`` catalog rows) and building
    rows whose class is unmapped in ``mapping`` -- gets ``False`` / ``0.0``
    for all five columns. An unmapped class is a VALID outcome (not every
    Bosserhof class maps to one of the five categories), not an error.

    The external Gemeinde centroids are the one family that must not STAY at
    ``False`` / ``0.0``: they are category-agnostic long-distance escapes, and
    :func:`append_external_category_escapes` re-opens every category on them
    once all category columns exist (i.e. after the landuse append). That step
    is deliberately NOT done here -- adding ``pot_leisure_outdoor`` at this point
    would both create a column this function has no building source for and let
    ewz population counts contaminate the landuse mixed-pool scale factor in
    :func:`append_landuse_candidates`.

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame; must already carry
        ``pot_leisure`` (added by ``build_secondary_candidates``).
    df_potentials:
        ``braunschweig.data.building_potentials`` frame: ``building_id``,
        ``bosserhof_class_clean``, ``potential_generic``, ``volume_m3``,
        ``commune_id``, ``geometry`` (footprint polygon or point).
    mapping:
        ``braunschweig.data.bosserhof_location_category`` frame:
        ``bosserhof_class``, ``location_category`` (one of
        ``bosserhof_location_category.BUILDING_CATEGORIES``).
    min_volume_m3:
        Errand potential is zeroed for buildings with ``volume_m3`` below
        this threshold (mirrors ``secondary_other_min_volume_m3``; the
        ``secondary_candidates`` stage passes the configured value).
    cap_percentile:
        Quantile of ``potential_generic`` (over each errand category's own
        class-member buildings) used as that category's potential cap
        (mirrors ``secondary_other_cap_percentile``).

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` with the ten new columns (five offers_/pot_ pairs),
        plus any newly appended errand-only ``sec_b_<building_id>`` rows.

    Raises
    ------
    ValueError
        If ``candidates`` is missing ``pot_leisure``, or
        ``df_potentials``/``mapping`` is missing a required column
        (fail-fast; no silent fallback to an all-zero category set).
    """
    if "pot_leisure" not in candidates.columns:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "requires candidates to already carry column 'pot_leisure' (produced by "
            "build_secondary_candidates); available: %s." % list(candidates.columns)
        )
    missing_potentials = [c for c in ["building_id", "bosserhof_class_clean", "potential_generic",
                                      "volume_m3", "commune_id", "geometry"]
                          if c not in df_potentials.columns]
    if missing_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "building_potentials source is missing column(s) %s; available: %s."
            % (missing_potentials, list(df_potentials.columns))
        )
    missing_mapping = [c for c in ["bosserhof_class", "location_category"]
                       if c not in mapping.columns]
    if missing_mapping:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "category mapping is missing column(s) %s; available: %s."
            % (missing_mapping, list(mapping.columns))
        )

    out = candidates.copy()
    categories = list(SRV_BUILDING_CATEGORY_BASE_POTENTIAL)
    for category in categories:
        out["offers_" + category] = False
        out["pot_" + category] = 0.0

    category_by_class = dict(zip(
        mapping["bosserhof_class"].astype(str), mapping["location_category"].astype(str),
    ))
    leisure_categories = [c for c, base in SRV_BUILDING_CATEGORY_BASE_POTENTIAL.items()
                          if base == "pot_leisure"]
    errand_categories = [c for c, base in SRV_BUILDING_CATEGORY_BASE_POTENTIAL.items()
                         if base == "pot_other"]

    # --- leisure categories: UNCHANGED -- mask the existing pot_leisure
    # aggregate on sec_b_* rows already present in candidates. ---
    building_mask = out["location_id"].astype(str).str.startswith("sec_b_")
    n_building_rows = int(building_mask.sum())

    class_by_building = dict(zip(
        df_potentials["building_id"].astype(str),
        df_potentials["bosserhof_class_clean"].astype(str),
    ))
    building_ids = out.loc[building_mask, "location_id"].astype(str).str.slice(len("sec_b_"))
    classes = building_ids.map(class_by_building)
    row_categories = classes.map(category_by_class)

    n_class_matched = int(classes.notna().sum())
    n_category_mapped = int(row_categories.notna().sum())
    per_category_counts = {
        category: int((row_categories == category).sum()) for category in categories
    }

    for category in leisure_categories:
        matched_index = row_categories.index[row_categories == category]
        if len(matched_index):
            out.loc[matched_index, "pot_" + category] = out.loc[matched_index, "pot_leisure"].astype(float)
            out.loc[matched_index, "offers_" + category] = out.loc[matched_index, "pot_" + category] > 0.0

    print(
        "[braunschweig.secondary_chainsolvers] leisure location category columns: %d "
        "sec_b_* building candidates; class matched %d/%d (%.1f%%), mapped to a "
        "leisure_* category %d/%d (%.1f%%); per-category counts: %s"
        % (n_building_rows, n_class_matched, n_building_rows,
           100.0 * n_class_matched / n_building_rows if n_building_rows else 0.0,
           n_category_mapped, n_class_matched,
           100.0 * n_category_mapped / n_class_matched if n_class_matched else 0.0,
           per_category_counts)
    )
    n_unmatched_building = n_building_rows - n_class_matched
    if n_unmatched_building:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] %d/%d sec_b_* candidates "
            "have no matching building_id in the building_potentials source "
            "(df_potentials); they carry False/0.0 for the leisure_* SrV location "
            "categories -- verify braunschweig.data.building_potentials and the "
            "building candidate set share the same building_id space."
            % (n_unmatched_building, n_building_rows)
        )

    # --- errand categories: derived independently from df_potentials, using
    # the derive_other_potential cap-and-floor formula per category (plan
    # amendment, issue #262). ---
    building_out_index = dict(zip(building_ids.values, building_ids.index))

    generic = pd.to_numeric(df_potentials["potential_generic"], errors="coerce").astype(float).to_numpy()
    volume = pd.to_numeric(df_potentials["volume_m3"], errors="coerce").astype(float).to_numpy()
    potential_building_ids = df_potentials["building_id"].astype(str).to_numpy()
    potential_classes = df_potentials["bosserhof_class_clean"].astype(str).to_numpy()
    potential_commune = df_potentials["commune_id"].astype(str).to_numpy()
    potential_geometry = df_potentials.geometry
    is_point_geometry = (potential_geometry.geom_type == "Point").to_numpy()
    potential_points = np.where(
        is_point_geometry, potential_geometry.values, potential_geometry.centroid.values)

    category_of_building = pd.Series(potential_classes).map(category_by_class)
    present_out_index = pd.Series(potential_building_ids).map(building_out_index)
    present_mask = present_out_index.notna().to_numpy()

    all_offer_columns = [c for c in out.columns if c.startswith("offers_")]
    all_potential_columns = [c for c in out.columns if c.startswith("pot_")]

    append_frames = []
    n_appended_total = 0
    per_category_supply = {}

    for category in errand_categories:
        member_mask = (category_of_building == category).to_numpy()
        n_members = int(member_mask.sum())
        if n_members:
            cap = float(np.nanquantile(generic[member_mask], cap_percentile))
        else:
            cap = float(np.nanquantile(generic, cap_percentile))
            print(
                "WARNING: [braunschweig.secondary_chainsolvers] no buildings map to "
                "location category '%s' in the Bosserhof mapping; potential cap "
                "derived from the all-building potential_generic quantile instead."
                % category
            )
        pot = np.where(member_mask, np.minimum(generic, cap), 0.0)
        pot = np.where(volume < float(min_volume_m3), 0.0, pot)
        offers = pot > 0.0
        per_category_supply[category] = int(offers.sum())

        update_mask = member_mask & present_mask
        if update_mask.any():
            target_index = present_out_index[update_mask].to_numpy()
            out.loc[target_index, "pot_" + category] = pot[update_mask]
            out.loc[target_index, "offers_" + category] = offers[update_mask]

        # Only append a NEW row for a building with a genuinely positive
        # computed potential (per the docstring's "positive computed
        # potential is guaranteed a row" contract). Without the `& offers`
        # condition, every class-member building below min_volume_m3 (or with
        # a NaN potential_generic) would still gain an inert all-False/0.0
        # sec_b_* row -- dead weight in facilities.xml that never offers
        # anything and contradicts that contract.
        append_mask = member_mask & ~present_mask & offers
        n_appended = int(append_mask.sum())
        n_appended_total += n_appended
        if n_appended:
            data = {
                "location_id": ["sec_b_" + b for b in potential_building_ids[append_mask]],
                "commune_id": potential_commune[append_mask],
                "iris_id": potential_commune[append_mask],
                "geometry": potential_points[append_mask],
            }
            for column in all_offer_columns:
                data[column] = np.zeros(n_appended, dtype=bool)
            for column in all_potential_columns:
                data[column] = np.zeros(n_appended, dtype=float)
            data["offers_" + category] = offers[append_mask]
            data["pot_" + category] = pot[append_mask]
            append_frames.append(gpd.GeoDataFrame(data, crs=out.crs))

    if append_frames:
        out = gpd.GeoDataFrame(
            pd.concat([out] + append_frames, ignore_index=True), crs=out.crs)

    print(
        "[braunschweig.secondary_chainsolvers] errand location category columns: "
        "%d new sec_b_* candidates appended for errand-class buildings absent from "
        "the candidate set; positive-potential rows per category: %s (min_volume_m3=%s, "
        "cap_percentile=%s)"
        % (n_appended_total, per_category_supply, min_volume_m3, cap_percentile)
    )
    return out


def check_category_supply(candidates: gpd.GeoDataFrame, categories) -> None:
    """Raise if any category in ``categories`` has zero positive-potential rows.

    A region-wide zero supply for a location category means the candidate
    universe carries no ``pot_<category> > 0`` row anywhere, so the carla
    solver could never select that category regardless of demand -- this is
    a wiring failure (a broken mapping, a grid-seeding gap, a potential-join
    miss), not merely thin data, and must be surfaced loudly (CLAUDE.md
    "Fallback transparency").

    MUST be called on the ESCAPE-FREE frame, i.e. BEFORE
    :func:`append_external_category_escapes`: those escapes give every external
    Gemeinde centroid a positive potential in every category, which would make
    this guard unfalsifiable whenever ``secondary_external_candidates`` is ON
    (its default). The check exists to prove genuine IN-AREA supply.

    Parameters
    ----------
    candidates:
        The assembled candidate GeoDataFrame; expected to carry
        ``pot_<category>`` for every entry in ``categories``.
    categories:
        Iterable of category names to check.

    Raises
    ------
    RuntimeError
        Naming every category with zero positive-potential rows (a missing
        ``pot_<category>`` column counts as zero supply).
    """
    empty = []
    for category in categories:
        column = "pot_" + category
        if column not in candidates.columns or not (candidates[column].astype(float) > 0.0).any():
            empty.append(category)
    if empty:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] zero candidate supply for location "
            "categor%s %s -- every pot_<category> column has no positive-potential "
            "rows; this indicates broken wiring (mapping / grid seeding / potential "
            "join), not thin data."
            % ("y" if len(empty) == 1 else "ies", empty)
        )


def check_visit_pool_supply(candidates: gpd.GeoDataFrame) -> None:
    """Raise if the residential visit pool has zero positive-potential rows.

    Sibling of :func:`check_category_supply` for the ONE SrV location category
    whose candidate pool does not follow the ``pot_<category>`` naming scheme:
    ``leisure_visit`` is placed on the residential visit candidates
    (``VISIT_OFFER_COLUMN`` / ``VISIT_POTENTIAL_COLUMN``, appended by
    :func:`append_residential_visit_candidates`), so a ``categories`` entry
    cannot cover it. Kept as a dedicated function rather than widening
    ``check_category_supply``'s ``"pot_" + category`` semantics, so the message
    can name the actual producer of that pool.

    ``leisure_visit_building_potential`` is a hard prerequisite of
    ``secondary_srv_location_types`` (see
    :func:`_validate_srv_location_type_prerequisites`), so with the flag ON a
    zero-supply visit pool always means broken wiring -- the residential append
    never ran, ran on an empty/zero-weight building frame, or lost its column --
    never merely thin data. Without this guard the omission would only surface
    much later, from inside the measure-only excursion boundary-clip diagnostic
    (``_srv_excursion_boundary_clip_lines``), which is the wrong place to
    discover a broken candidate set.

    MUST be called on the escape-free frame, next to
    :func:`check_category_supply`: ``leisure_visit`` is deliberately excluded
    from :func:`append_external_category_escapes`, so its supply can only ever
    come from in-area residential rows.

    Raises
    ------
    RuntimeError
        If ``VISIT_POTENTIAL_COLUMN`` is missing or has no positive row.
    """
    column = VISIT_POTENTIAL_COLUMN
    if column not in candidates.columns or not (candidates[column].astype(float) > 0.0).any():
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] zero candidate supply for location "
            "category 'leisure_visit' -- the residential visit pool ('%s') has no "
            "positive-potential rows; this indicates broken wiring "
            "(append_residential_visit_candidates did not run, or ran on an empty / "
            "zero-weight braunschweig.data.buildings frame), not thin data. Note that "
            "'leisure_visit' is excluded from the external Gemeinde-centroid escapes, "
            "so this pool is its only source of candidates." % column
        )


def append_landuse_candidates(candidates: gpd.GeoDataFrame,
                              df_landuse_points: gpd.GeoDataFrame,
                              layer_to_category: Dict[str, str],
                              df_municipalities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append one landuse grid-point candidate row per seeded point (issue #262).

    ``df_landuse_points`` is the output of
    ``braunschweig.synthesis.locations.landuse_candidates.grid_seed_polygons``
    (columns ``layer``, ``represented_area_m2``, ``geometry`` (Point)): each
    point becomes a ``sec_lu_<n>`` candidate row (``n`` = its positional
    index in ``df_landuse_points``, stable because grid seeding is
    deterministic) carrying ``offers_<category>=True`` /
    ``pot_<category>=represented_area_m2`` for its layer's category
    (``layer_to_category[layer]``) and ``False`` / ``0.0`` for every other
    offer/potential column already on ``candidates``, mirroring the
    column-fill pattern of :func:`append_residential_visit_candidates`. Any
    category column named by ``layer_to_category`` that does not yet exist
    on ``candidates`` (e.g. ``leisure_outdoor``, which has no building
    counterpart) is added here, defaulting to ``False`` / ``0.0`` on the
    pre-existing rows.

    ``commune_id`` / ``iris_id`` are attached by a point-in-polygon spatial
    join against ``df_municipalities`` (predicate ``"within"``). Points that
    fall outside every municipality polygon are outside the study area and
    are DROPPED (counted and logged -- no silent fallback to an unset zone
    id). ``iris_id`` is set equal to ``commune_id`` because
    ``data.spatial.municipalities`` does not carry a separate IRIS code
    (mirroring the ``iris_col`` fallback already used by
    ``append_residential_visit_candidates`` / ``append_escort_candidates``
    when the finer-grained id is unavailable).

    Scale coherence in MIXED pools (plan amendment, issue #262): a category
    such as ``leisure_culture`` or ``leisure_sports`` can carry candidates
    from TWO incompatible-unit sources -- buildings (``pot_<category>``
    already on ``candidates``, a disaggregated zonal person-mass potential,
    see :func:`append_location_category_columns`) and landuse grid points
    (``represented_area_m2``, a constant per grid cell). The combined carla
    scorer's default ``attr_transform="linear"`` feeds these raw magnitudes
    directly into the score, so whichever source happens to carry the larger
    numbers would dominate the within-category ranking regardless of actual
    relative attractiveness. ASSUMPTION: an AVERAGE landuse point should rank
    like an AVERAGE building of the same category. To realise that, every
    mixed category's landuse potentials are rescaled by a single factor
    (``mean(positive building pot_<category>) / mean(raw landuse pot_<category>)``)
    so the two source means coincide, while the relative AREA RATIOS among a
    category's own landuse points are preserved exactly (a pure linear
    rescale, not a reshaping). A category with NO building counterpart at
    all (``leisure_outdoor``: ``append_location_category_columns`` never
    creates a ``pot_leisure_outdoor`` column, because there is no building
    source for it) is a PURE landuse pool -- every candidate in it carries
    the same kind of potential, so the constant scale cancels out in a
    same-scale ranking and is intentionally left unnormalised. A category
    whose ``pot_<category>`` column exists (mixed by design) but has zero
    positive building rows in the current region keeps its raw areas (there
    is nothing to normalise against) and logs a ``WARNING`` rather than
    silently normalising by an undefined factor; :func:`check_category_supply`
    still governs whether that is a hard failure.

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame. Should already carry
        the five SrV building-category columns (i.e. called AFTER
        :func:`append_location_category_columns`) so those columns are
        correctly zero-filled for the new landuse rows rather than added
        fresh here.
    df_landuse_points:
        ``grid_seed_polygons`` output: ``layer``, ``represented_area_m2``,
        ``geometry`` (Point).
    layer_to_category:
        ATKIS layer name -> SrV location category, e.g.
        ``landuse_candidates.LANDUSE_LAYER_TO_CATEGORY``.
    df_municipalities:
        ``data.spatial.municipalities`` frame: ``commune_id``, ``geometry``
        (polygon), same CRS as ``candidates`` or reprojectable to it.

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` concatenated with one landuse-candidate row per point
        that falls inside a municipality.

    Raises
    ------
    ValueError
        If ``df_landuse_points`` is missing a required column, if
        ``df_municipalities`` is missing ``commune_id``, or if
        ``df_landuse_points`` carries a ``layer`` value with no entry in
        ``layer_to_category`` (fail-fast; no silent drop of an unrecognised
        layer).
    """
    required_points = ["layer", "represented_area_m2", "geometry"]
    missing_points = [c for c in required_points if c not in df_landuse_points.columns]
    if missing_points:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates landuse "
            "point source is missing column(s) %s; available: %s."
            % (missing_points, list(df_landuse_points.columns))
        )
    if "commune_id" not in df_municipalities.columns:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates "
            "municipalities source is missing the 'commune_id' column; available: %s."
            % list(df_municipalities.columns)
        )
    unknown_layers = sorted(set(df_landuse_points["layer"].astype(str)) - set(layer_to_category))
    if unknown_layers:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates: "
            "df_landuse_points has layer(s) %s with no entry in layer_to_category "
            "(known: %s)." % (unknown_layers, sorted(layer_to_category))
        )

    n_before = len(candidates)
    base = candidates.copy()

    categories = sorted(set(layer_to_category.values()))
    for category in categories:
        if ("offers_" + category) not in base.columns:
            base["offers_" + category] = False
        if ("pot_" + category) not in base.columns:
            base["pot_" + category] = 0.0

    pts = df_landuse_points.copy()
    if pts.crs is not None and candidates.crs is not None and pts.crs != candidates.crs:
        pts = pts.to_crs(candidates.crs)
    municipalities = df_municipalities
    if (municipalities.crs is not None and candidates.crs is not None
            and municipalities.crs != candidates.crs):
        municipalities = municipalities.to_crs(candidates.crs)

    n_total = len(pts)
    pts_indexed = gpd.GeoDataFrame(
        {"_row": np.arange(n_total)}, geometry=pts.geometry.values, crs=candidates.crs)
    joined = gpd.sjoin(
        pts_indexed, municipalities[["commune_id", "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"])
    # A point exactly on a shared municipality border can match more than one
    # polygon; keep the first match (deterministic row order) so every input
    # point contributes at most one output row.
    joined = joined.drop_duplicates(subset="_row", keep="first").set_index("_row")
    commune_by_row = joined["commune_id"].reindex(range(n_total))

    kept_mask = commune_by_row.notna().to_numpy()
    n_kept = int(kept_mask.sum())
    n_dropped = n_total - n_kept

    kept_n = np.arange(n_total)[kept_mask]
    layer_kept = df_landuse_points["layer"].to_numpy()[kept_mask]
    area_kept = df_landuse_points["represented_area_m2"].astype(float).to_numpy()[kept_mask]
    geom_kept = pts.geometry.to_numpy()[kept_mask]
    commune_kept = commune_by_row.to_numpy()[kept_mask].astype(str)
    category_kept = np.array([layer_to_category[layer] for layer in layer_kept])

    offer_columns_all = [c for c in base.columns if c.startswith("offers_")]
    potential_columns_all = [c for c in base.columns if c.startswith("pot_")]

    data = {
        "location_id": ["sec_lu_%d" % n for n in kept_n],
        "commune_id": commune_kept,
        "iris_id": commune_kept,
        "geometry": geom_kept,
    }
    for column in offer_columns_all:
        data[column] = np.zeros(n_kept, dtype=bool)
    for column in potential_columns_all:
        data[column] = np.zeros(n_kept, dtype=float)
    for category in categories:
        mask = category_kept == category
        data["offers_" + category][mask] = True
        data["pot_" + category][mask] = area_kept[mask]

    # Scale-normalize mixed categories (plan amendment, issue #262): see the
    # docstring section "Scale coherence in MIXED pools" above for the
    # rationale. Checked against the INCOMING `candidates` frame (i.e.
    # building supply only, before this function's own default-column fill
    # above), because that is the sole source of the "does this category
    # already carry buildings" signal.
    for category in categories:
        column = "pot_" + category
        category_mask = category_kept == category
        n_points = int(category_mask.sum())
        if n_points == 0:
            continue
        if column not in candidates.columns:
            # Pure landuse pool (e.g. leisure_outdoor): no building
            # counterpart exists, so there is nothing to normalize against
            # and no normalization is needed -- every candidate in this pool
            # is on the same (area) scale already.
            continue
        building_values = pd.to_numeric(candidates[column], errors="coerce").astype(float)
        positive_building = building_values[building_values > 0.0]
        raw_values = data[column][category_mask]
        if len(positive_building) == 0:
            print(
                "WARNING: [braunschweig.secondary_chainsolvers] landuse category "
                "'%s' shares column '%s' with building candidates but has zero "
                "positive-potential building rows in this region -- keeping raw "
                "represented_area_m2 landuse potentials (no scale factor applied); "
                "check_category_supply still governs hard failure if the category's "
                "total supply is zero." % (category, column)
            )
            continue
        building_mean = float(positive_building.mean())
        landuse_raw_mean = float(np.mean(raw_values))
        if landuse_raw_mean == 0.0:
            print(
                "WARNING: [braunschweig.secondary_chainsolvers] landuse category "
                "'%s' has zero raw represented_area_m2 potential across its %d "
                "point(s); cannot mean-normalize against the building scale -- "
                "keeping raw (zero) landuse potentials." % (category, n_points)
            )
            continue
        factor = building_mean / landuse_raw_mean
        data[column][category_mask] = raw_values * factor
        print(
            "[braunschweig.secondary_chainsolvers] landuse potential scale-"
            "normalization: category=%s factor=%.4f building_mean=%.3f "
            "landuse_raw_mean=%.3f n_points=%d"
            % (category, factor, building_mean, landuse_raw_mean, n_points)
        )

    landuse_rows = gpd.GeoDataFrame(data, crs=candidates.crs)
    out = gpd.GeoDataFrame(
        pd.concat([base, landuse_rows], ignore_index=True), crs=candidates.crs)

    n_after = len(out)
    growth_factor = (n_after / n_before) if n_before else float("inf")
    print(
        "[braunschweig.secondary_chainsolvers] landuse candidates: %d/%d grid points "
        "inside a municipality kept, %d dropped (outside the study area boundary); "
        "locations frame %d -> %d rows after appending %d landuse candidates "
        "(growth x%.2f)"
        % (n_kept, n_total, n_dropped, n_before, n_after, n_kept, growth_factor)
    )
    if growth_factor > VISIT_CANDIDATE_WARN_FACTOR:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] landuse candidate growth "
            "factor x%.2f exceeds VISIT_CANDIDATE_WARN_FACTOR=%.1f -- this materially "
            "increases the carla candidate universe and solve cost; verify "
            "secondary_landuse_grid_spacing_meters is not set too fine for the "
            "region's landuse extent."
            % (growth_factor, VISIT_CANDIDATE_WARN_FACTOR)
        )
    return out


def external_centroid_mask(candidates: gpd.GeoDataFrame) -> pd.Series:
    """Boolean mask selecting the external Gemeinde-centroid candidate rows.

    External centroids are the long-distance escape hatch appended by
    :func:`build_secondary_candidates` when ``secondary_external_candidates`` is
    ON: they let carla match a desired distance that reaches beyond the study
    area instead of truncating it to the area edge. They are identified exactly
    as that function constructs them -- ``location_id`` IS the bare
    ``commune_id`` (every in-area family is prefixed: ``sec_b_``, ``sec_lu_``,
    ``sec_res_``, ``sec_edu_``, legacy ``sec_``) AND all three base purposes are
    offered (which no other family does: legacy rows are ``other``-only,
    building rows never offer ``other``, visit/education rows offer neither).
    Both conditions are required so a hypothetical unprefixed in-area id cannot
    be mistaken for an external centroid.
    """
    return (
        (candidates["location_id"].astype(str) == candidates["commune_id"].astype(str))
        & candidates["offers_shop"].astype(bool)
        & candidates["offers_leisure"].astype(bool)
        & candidates["offers_other"].astype(bool)
    )


def append_external_category_escapes(candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Make external Gemeinde centroids candidates for EVERY SrV location
    category (issue #262, post-Task-8 review finding).

    External centroids are category-AGNOSTIC distance escapes: before this
    feature they offered all three base purposes with the same population (ewz)
    potential precisely so a long desired distance could be realised outside the
    study area. :func:`append_location_category_columns` (buildings) and
    :func:`append_landuse_candidates` (ATKIS grid points) both leave them at
    ``False`` / ``0.0`` for every category, so under
    ``secondary_srv_location_types`` a ``leisure_culture`` / ``leisure_gastronomy``
    / ``leisure_sports`` / ``leisure_outdoor`` / ``errand_*`` leg would have NO
    external candidate at all and its desired distance would clip to the region
    edge -- a reach REGRESSION versus the OFF path, where the same leg was a
    plain ``leisure`` / ``other`` leg with external candidates available. This
    function restores that role: for every external-centroid row (see
    :func:`external_centroid_mask`) each category gets ``offers_<category> =
    True`` and ``pot_<category>`` = the row's existing ``pot_leisure`` (leisure
    categories) or ``pot_other`` (errand categories), i.e. the same ewz value the
    aggregate offers already carry.

    ``leisure_visit`` is deliberately NOT touched: its candidate pool is the
    residential building stock (``offers_visit`` / ``pot_visit``, Task 5, issue
    #127) and external centroids never offered ``offers_visit`` on the OFF path
    either -- extending them here would be a behaviour CHANGE, not a regression
    fix.

    Call order -- this is the LAST step of the candidate assembly:

    * AFTER both :func:`append_location_category_columns` and
      :func:`append_landuse_candidates`, so every ``pot_<category>`` column
      exists (``leisure_outdoor`` is created only by the landuse append) and so
      the landuse mixed-pool mean-normalisation still compares landuse points
      against BUILDING potentials only -- ewz population counts must never enter
      that scale factor. Missing category columns therefore raise (fail-fast on a
      wrong call order rather than silently skipping a category).
    * AFTER :func:`check_category_supply`, which must measure genuine IN-AREA
      supply: these escapes give every external centroid a positive potential in
      every category, so running them first would make that guard unfalsifiable
      whenever ``secondary_external_candidates`` is ON (its default) and a broken
      building mapping or landuse seeding would pass on external supply alone.

    Parameters
    ----------
    candidates:
        Assembled candidate GeoDataFrame carrying ``location_id``,
        ``commune_id``, the three base offers, ``pot_leisure`` / ``pot_other``
        and every ``offers_``/``pot_`` pair of
        ``EXTERNAL_CATEGORY_ESCAPE_CATEGORIES``.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy of ``candidates`` with the external rows' category columns set.
        Row count and row order are unchanged (no rows are added or dropped).

    Raises
    ------
    ValueError
        If a required base or category column is missing.
    """
    required_base = ["location_id", "commune_id", "offers_shop", "offers_leisure",
                     "offers_other", "pot_leisure", "pot_other"]
    missing_base = [column for column in required_base if column not in candidates.columns]
    if missing_base:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_external_category_escapes "
            "requires column(s) %s on the candidate frame; available: %s."
            % (missing_base, list(candidates.columns))
        )
    missing_category = [
        column
        for category in EXTERNAL_CATEGORY_ESCAPE_CATEGORIES
        for column in ("offers_" + category, "pot_" + category)
        if column not in candidates.columns
    ]
    if missing_category:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_external_category_escapes is "
            "missing category column(s) %s -- call it AFTER "
            "append_location_category_columns AND append_landuse_candidates (which "
            "create them), never before." % missing_category
        )

    out = candidates.copy()
    mask = external_centroid_mask(out)
    external_index = out.index[mask]
    if len(external_index) == 0:
        print(
            "[braunschweig.secondary_chainsolvers] external category escapes: no external "
            "Gemeinde-centroid rows in the candidate set (secondary_external_candidates "
            "OFF) -- no category escape rows added; long leisure/other desired distances "
            "can only be realised inside the study area."
        )
        return out

    for category in EXTERNAL_CATEGORY_ESCAPE_CATEGORIES:
        base_column = "pot_leisure" if category in SRV_LEISURE_CATEGORIES else "pot_other"
        out.loc[external_index, "pot_" + category] = \
            out.loc[external_index, base_column].astype(float)
        out.loc[external_index, "offers_" + category] = True
    print(
        "[braunschweig.secondary_chainsolvers] external category escapes: %d external "
        "Gemeinde centroids now offer all %d SrV location categories at their aggregate "
        "(ewz) potential -- category-agnostic long-distance escapes, mirroring their "
        "pre-flag any-purpose role ('leisure_visit' stays residential-only)."
        % (len(external_index), len(EXTERNAL_CATEGORY_ESCAPE_CATEGORIES))
    )
    return out
