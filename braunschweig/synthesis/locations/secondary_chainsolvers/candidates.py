"""Candidate-set assembly and the chainsolvers locations frame / scorer.

Builds the REPLACE candidate set the carla solver searches over
(``build_secondary_candidates``: gpkg building potentials + legacy other +
external centroids), appends the residential visit and escort candidate
rows, constructs the chainsolvers ``locations_df``
(``_build_locations_df``) and the combined potential/distance ``Scorer``
(``build_scorer``). The cordon warning guards the external-candidates
feature combination.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from .activity_types import (
    LEISURE_SUBTYPE_ACTIVITIES,
    OTHER_SUBTYPE_ACTIVITIES,
    SHOP_SUBTYPE_ACTIVITIES,
)
from .candidate_columns import (
    ESCORT_EDU_OFFER_BY_TYPE,
    ESCORT_EDU_POTENTIAL_COLUMN,
    ESCORT_RESIDENTIAL_OFFER_COLUMN,
    VISIT_CANDIDATE_WARN_FACTOR,
    VISIT_OFFER_COLUMN,
    VISIT_POTENTIAL_COLUMN,
    _ACTIVITY_POTENTIAL_COLUMN,
)
from .srv_location_types import (
    SRV_LEISURE_CATEGORIES,
    SRV_OTHER_CATEGORIES,
    SRV_PLACEMENT_CATEGORIES,
    srv_category_offer_column,
    srv_category_potential_column,
)


def external_candidates_cordon_warning(external_on, cordon_on):
    """Return a warning string when external secondary candidates are enabled but
    the cordon cutter is off (the resulting boundary-crossing trips would not be
    converted into 'outside' activities and would be unroutable in MATSim), else None."""
    if external_on and not cordon_on:
        return ("[braunschweig.secondary_chainsolvers] WARNING: "
                "secondary_external_candidates is ON but cordon_enabled is OFF -- "
                "long-distance secondary activities at external Gemeinde centroids "
                "will not be converted to 'outside' activities and may be unroutable "
                "in MATSim. Enable cordon_enabled or disable secondary_external_candidates.")
    return None


def append_residential_visit_candidates(candidates: gpd.GeoDataFrame,
                                        df_residential_buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append one residential-building candidate row per building for the
    "leisure_visit" placement (Task 5, issue #127).

    "leisure_visit" legs (MiD W_ZWD 701, "visiting someone") are destined for a
    private household, not a leisure-activity building, so their candidate set
    is the residential building stock reused verbatim from the home-assignment
    path -- ``braunschweig.data.buildings`` (the SAME ALKIS/GFK-filtered,
    area-weighted frame ``synthesis/locations/home_cell.py`` consumes for home
    placement; no new data source). Each residential building becomes one
    candidate row carrying ``offers_visit=True`` / ``pot_visit=weight`` (the
    footprint-area dwelling-capacity proxy already used by the legacy
    area-weighted home sampler) and ``False`` / ``0.0`` for every other
    purpose's offer/potential column, so it is NEVER a candidate for
    shop / leisure (non-visit) / other.

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame (the return value of
        :func:`build_secondary_candidates`), missing the ``offers_visit`` /
        ``pot_visit`` columns (added here, defaulting to ``False`` / ``0.0``
        on the pre-existing rows).
    df_residential_buildings:
        ``braunschweig.data.buildings`` output: ``building_id``, ``weight``,
        ``commune_id`` (and, if present, ``iris_id``), ``geometry`` (point,
        same CRS as ``candidates`` or reprojectable to it).

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` with ``offers_visit`` / ``pot_visit`` columns added,
        concatenated with one residential-candidate row per building.

    Raises
    ------
    ValueError
        If ``df_residential_buildings`` is missing a required column
        (fail-fast; no silent fallback to an empty/degenerate residential
        candidate set -- see the ``leisure_visit_building_potential`` caller
        in ``execute()``).
    """
    required = ["building_id", "weight", "commune_id", "geometry"]
    missing = [c for c in required if c not in df_residential_buildings.columns]
    if missing:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "residential candidate source (braunschweig.data.buildings) is missing "
            "column(s) %s; available: %s." % (missing, list(df_residential_buildings.columns))
        )

    n_before = len(candidates)

    base = candidates.copy()
    base[VISIT_OFFER_COLUMN] = False
    base[VISIT_POTENTIAL_COLUMN] = 0.0

    res = df_residential_buildings
    if res.crs is not None and candidates.crs is not None and res.crs != candidates.crs:
        res = res.to_crs(candidates.crs)
    iris_col = "iris_id" if "iris_id" in res.columns else "commune_id"
    residential_rows = gpd.GeoDataFrame({
        "location_id": ("sec_res_" + res["building_id"].astype(str)).values,
        "commune_id": res["commune_id"].astype(str).values,
        "iris_id": res[iris_col].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": False,
        VISIT_OFFER_COLUMN: True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": 0.0,
        VISIT_POTENTIAL_COLUMN: res["weight"].astype(float).values,
        "geometry": res.geometry.values,
    }, crs=candidates.crs)

    out = gpd.GeoDataFrame(
        pd.concat([base, residential_rows], ignore_index=True), crs=candidates.crs)
    n_after = len(out)
    n_residential = len(residential_rows)
    growth_factor = (n_after / n_before) if n_before else float("inf")
    print(
        "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential: "
        "locations frame %d -> %d rows after appending %d residential visit "
        "candidates (growth x%.2f)"
        % (n_before, n_after, n_residential, growth_factor)
    )
    if growth_factor > VISIT_CANDIDATE_WARN_FACTOR:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] residential visit-candidate "
            "growth factor x%.2f exceeds VISIT_CANDIDATE_WARN_FACTOR=%.1f -- this "
            "materially increases the carla candidate universe and solve cost; "
            "verify braunschweig.data.buildings is scoped to the expected region."
            % (growth_factor, VISIT_CANDIDATE_WARN_FACTOR)
        )
    return out


def append_escort_candidates(candidates: gpd.GeoDataFrame,
                             df_education: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append education escort candidates + escort offer columns (issue #201).

    Adds, on EVERY row: the three education offer columns
    (ESCORT_EDU_OFFER_BY_TYPE, default False), ESCORT_EDU_POTENTIAL_COLUMN
    (default 0.0) and ESCORT_RESIDENTIAL_OFFER_COLUMN (True where the row is a
    residential visit candidate, i.e. its VISIT_OFFER_COLUMN is True; False
    elsewhere / when the visit machinery is off). Then appends one
    ``sec_edu_<n>`` candidate row per NON-fake education facility from
    ``synthesis.locations.education`` (fake rows are municipality-centroid
    placeholders, not real facilities), carrying ONLY its per-type escort offer
    and ``pot_escort_edu = weight`` (the OSM area*floors capacity proxy the
    education gravity assignment uses -- ASSUMPTION documented in the spec).
    """
    required = ["fake", "education_type", "weight", "location_id", "commune_id", "geometry"]
    missing = [c for c in required if c not in df_education.columns]
    if missing:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort education candidate source "
            "(synthesis.locations.education) is missing column(s) %s; available: %s."
            % (missing, list(df_education.columns))
        )

    base = candidates.copy()
    for column in ESCORT_EDU_OFFER_BY_TYPE.values():
        base[column] = False
    base[ESCORT_EDU_POTENTIAL_COLUMN] = 0.0
    if VISIT_OFFER_COLUMN in base.columns:
        base[ESCORT_RESIDENTIAL_OFFER_COLUMN] = base[VISIT_OFFER_COLUMN].astype(bool)
    else:
        base[ESCORT_RESIDENTIAL_OFFER_COLUMN] = False

    edu = df_education[~df_education["fake"].astype(bool)].copy()
    n_excluded_fake = int(df_education["fake"].astype(bool).sum())
    n_unknown = int((edu["education_type"].astype(str) == "unknown").sum())
    edu = edu[edu["education_type"].astype(str).isin(ESCORT_EDU_OFFER_BY_TYPE)]
    if edu.crs is not None and candidates.crs is not None and edu.crs != candidates.crs:
        edu = edu.to_crs(candidates.crs)

    iris_col = "iris_id" if "iris_id" in edu.columns else "commune_id"
    data = {
        "location_id": ("sec_" + edu["location_id"].astype(str)).values,
        "commune_id": edu["commune_id"].astype(str).values,
        "iris_id": edu[iris_col].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": False,
        "offers_escort": True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": 0.0,
        ESCORT_EDU_POTENTIAL_COLUMN: edu["weight"].astype(float).values,
        ESCORT_RESIDENTIAL_OFFER_COLUMN: False,
        "geometry": edu.geometry.values,
    }
    # Visit columns exist on base whenever the residential machinery ran; keep
    # the frames column-aligned.
    if VISIT_OFFER_COLUMN in base.columns:
        data[VISIT_OFFER_COLUMN] = False
        data[VISIT_POTENTIAL_COLUMN] = 0.0
    education_types = edu["education_type"].astype(str).values
    for education_type, column in ESCORT_EDU_OFFER_BY_TYPE.items():
        data[column] = (education_types == education_type)

    edu_rows = gpd.GeoDataFrame(data, crs=candidates.crs)
    out = gpd.GeoDataFrame(
        pd.concat([base, edu_rows], ignore_index=True), crs=candidates.crs)
    print(
        "[braunschweig.secondary_chainsolvers] escort candidates: appended "
        f"{len(edu_rows)} education rows "
        f"(kindergarten={int((education_types == 'kindergarten').sum())}, "
        f"school={int((education_types == 'school').sum())}, "
        f"university={int((education_types == 'university').sum())}); "
        f"excluded {n_excluded_fake} fake centroid rows and {n_unknown} "
        "unknown-type rows"
    )
    return out



def build_scorer(enabled: bool, mode: str, pot_weight: float, dist_dev_weight: float,
                 attr_transform: str = "linear"):
    """Construct the chainsolvers combined Scorer, or None when disabled (the
    legacy distance-only path). Import-lazy so the module loads without the dep.
    Raises if enabled but the Scorer is unavailable (no silent fallback).

    ``attr_transform`` controls how building potentials are scaled before scoring:
    ``"linear"`` (default, byte-identical to before), ``"log1p"`` (log(1+P),
    the calibrated-MNL form), or ``"log"``. Forwarded directly to
    ``chainsolvers.Scorer(attr_transform=...)``.
    """
    if not enabled:
        return None
    try:
        import chainsolvers as cs
        Scorer = getattr(cs, "Scorer", None)
        if Scorer is None:
            from chainsolvers.scoring_selection import Scorer
        return Scorer(mode=mode, pot_weight=pot_weight, dist_dev_weight=dist_dev_weight,
                      attr_transform=attr_transform)
    except Exception as exc:
        raise RuntimeError(
            "secondary_building_potentials is ON but the chainsolvers combined "
            "Scorer is unavailable (%s); pin the git commit in environment.yml" % exc
        )


def build_secondary_candidates(df_secondary_legacy: gpd.GeoDataFrame,
                               df_buildings: gpd.GeoDataFrame,
                               df_external: gpd.GeoDataFrame = None,
                               *, mapping=None,
                               other_potential_params=None) -> gpd.GeoDataFrame:
    """REPLACE secondary candidates when building potentials are ON.

    shop/leisure candidates = gpkg activity buildings (native potentials, no
    fallback — the candidate set IS the buildings that carry a non-zero
    retail or leisure potential); 'other' candidates = the legacy broad catalog
    with the generic potential attached by footprint join (fallback 0.0, logged
    by attach_potential so the rate stays observable).

    Parameters
    ----------
    df_secondary_legacy:
        The legacy secondary candidate GeoDataFrame from
        ``synthesis.locations.secondary`` (columns: location_id, commune_id,
        iris_id, geometry(Point), offers_shop, offers_leisure, offers_other).
    df_buildings:
        Building-footprint GeoDataFrame from
        ``braunschweig.data.building_potentials`` (columns: building_id,
        potential_retail_daily, potential_retail_non_daily, potential_leisure,
        potential_generic, commune_id, geometry(POLYGON), EPSG:25832).
        When ``mapping`` is provided, the buildings frame must additionally
        carry ``volume_m3`` and the Bosserhof class column (default
        ``bosserhof_class_clean``).
    df_external:
        Optional GeoDataFrame of external Gemeinde centroids (long-distance
        secondary candidates).
    mapping:
        Optional DataFrame ``[bosserhof_class, eqasim_purpose, other_destination]``
        from ``braunschweig.data.bosserhof_purpose``.  When provided (ON path)
        the ``other`` potential is derived via
        ``derive_other_potential`` (capped, whitelist-boosted) and the median of
        the positive values is used as the spatial-join fallback (logged).
        When ``None`` (default / OFF path) the raw ``potential_generic`` is used
        with a zero fallback — byte-identical to the pre-feature behaviour.
    other_potential_params:
        Optional dict with keyword arguments forwarded to
        ``derive_other_potential`` (``broad_share``, ``errand_share``,
        ``min_volume_m3``, ``cap_percentile``). Ignored when ``mapping`` is None.

    Returns
    -------
    GeoDataFrame with columns:
        location_id, commune_id, iris_id, geometry(Point),
        offers_shop, offers_leisure, offers_other, offers_escort,
        pot_shop, pot_shop_daily, pot_shop_non_daily, pot_leisure, pot_other
    concat of gpkg shop/leisure rows and legacy other rows, reset index.

    ``pot_shop`` stays the SUM of the daily + non-daily retail potential (used
    on the OFF / non-split path, byte-identical to before); ``pot_shop_daily``
    and ``pot_shop_non_daily`` carry the two gpkg components separately so the
    Tier-2 daily/non-daily split (secondary_shop_daily_split) can route a leg's
    placement to the matching retail subtype. The legacy 'other' rows carry 0.0
    for all three shop potentials.

    ``offers_escort`` (issue #201): True on every candidate row, regardless of
    the ``escort_purpose`` flag -- it is cheap to mark every candidate eligible
    here; whether facilities WRITE the escort option is gated by the
    ``escort_purpose`` flag in the facilities writer (Task 8), not by this
    column.
    """
    from braunschweig.data.building_potential_attach import attach_potential

    # --- gpkg shop/leisure candidates ---
    # One row per building that carries a non-zero retail or leisure potential.
    # Potentials are native (read directly from the building table); no spatial
    # join needed, so there is no fallback path for this half of the candidates.
    b = df_buildings.copy()
    retail_daily = b["potential_retail_daily"].astype(float)
    retail_non_daily = b["potential_retail_non_daily"].astype(float)
    retail = retail_daily + retail_non_daily
    leisure = b["potential_leisure"].astype(float)
    keep = (retail > 0) | (leisure > 0)
    b = b[keep]
    retail = retail[keep]
    retail_daily = retail_daily[keep]
    retail_non_daily = retail_non_daily[keep]
    leisure = leisure[keep]
    gpkg = gpd.GeoDataFrame({
        "location_id": ("sec_b_" + b["building_id"].astype(str)).values,
        "commune_id": b["commune_id"].astype(str).values,
        "iris_id": b["commune_id"].astype(str).values,
        "offers_shop": (retail > 0).values,
        "offers_leisure": (leisure > 0).values,
        "offers_other": False,
        "offers_escort": True,
        "pot_shop": retail.values,
        "pot_shop_daily": retail_daily.values,
        "pot_shop_non_daily": retail_non_daily.values,
        "pot_leisure": leisure.values,
        "pot_other": 0.0,
        "geometry": b.geometry.centroid.values,
    }, crs=df_buildings.crs)

    # --- legacy 'other' candidates (broad catalog) ---
    # All legacy candidates become 'other'-only rows so the broad OSM/ALKIS/
    # landuse catalog is preserved for the 'other' purpose.
    legacy = df_secondary_legacy.copy()
    if mapping is not None:
        # ON path: derive a capped, whitelist-boosted potential_other via the
        # Bosserhof function-class mapping. The footprint-join fallback is the
        # median of the positive potential_other values (so candidates without a
        # containing building still receive a reasonable non-zero potential rather
        # than the 0.0 that the generic fallback would give). The rate is logged
        # by attach_potential (no silent fallback).
        from braunschweig.synthesis.locations.secondary_other_potential import (
            derive_other_potential,
        )
        params = other_potential_params or {}
        bld = df_buildings.copy()
        pot_series, st = derive_other_potential(bld, mapping, **params)
        bld["potential_other"] = pot_series.values
        positive = pot_series[pot_series > 0.0]
        median_prior = float(positive.median()) if len(positive) else 0.0
        print("[braunschweig.secondary_chainsolvers] smart other potential: "
              "cap=%.0f whitelist=%d non-whitelist=%d unknown_class=%d tiny=%d "
              "median_prior=%.1f" % (st["cap_value"], st["n_whitelist"],
              st["n_nonwhitelist"], st["n_unknown_class"], st["n_tiny"], median_prior))
        pot_other, _p, _f = attach_potential(
            legacy, bld, "potential_other",
            fallback=np.full(len(legacy), median_prior, dtype=float), label="sec_other")
    else:
        # OFF path: byte-identical to the pre-feature behaviour (raw
        # potential_generic, zero fallback). No new imports, no new logic.
        pot_other, _p, _f = attach_potential(
            legacy, df_buildings, "potential_generic",
            fallback=np.zeros(len(legacy), dtype=float), label="sec_other")
    legacy_other = gpd.GeoDataFrame({
        "location_id": legacy["location_id"].astype(str).values,
        "commune_id": legacy["commune_id"].astype(str).values,
        "iris_id": legacy["iris_id"].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": True,
        "offers_escort": True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": pot_other,
        "geometry": legacy.geometry.values,
    }, crs=legacy.crs)

    # External Gemeinde centroids (outside ZGB): long-distance secondary candidates
    # so carla can match desired distances beyond the study area instead of
    # truncating to the area edge. offers all three purposes; potential =
    # population (ewz) -- a population proxy; external selection is distance-driven
    # (carla snaps the relaxed point to the nearest external centroid), so the exact
    # potential only ranks among near-equal-distance centroids.
    frames = [gpkg, legacy_other]
    if df_external is not None and len(df_external) > 0:
        ext = (df_external.to_crs(df_buildings.crs)
               if df_external.crs != df_buildings.crs else df_external)
        ewz = ext["ewz"].astype(float).values
        cid = ext["commune_id"].astype(str).values
        ext_rows = gpd.GeoDataFrame({
            "location_id": cid,
            "commune_id": cid,
            "iris_id": cid,
            "offers_shop": True,
            "offers_leisure": True,
            "offers_other": True,
            "offers_escort": True,
            "pot_shop": ewz,
            "pot_shop_daily": ewz,
            "pot_shop_non_daily": ewz,
            "pot_leisure": ewz,
            "pot_other": ewz,
            "geometry": ext.geometry.values,
        }, crs=df_buildings.crs)
        frames.append(ext_rows)
        print("[braunschweig.secondary_chainsolvers] external candidates: "
              "%d Gemeinde centroids appended for long-distance secondary trips"
              % len(ext_rows))

    out = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), crs=df_buildings.crs)
    print("[braunschweig.secondary_chainsolvers] REPLACE candidates: "
          "%d gpkg shop/leisure buildings + %d legacy 'other' candidates"
          % (len(gpkg), len(legacy_other)))
    return out


def _build_locations_df(df_secondary, with_potentials: bool = False,
                        shop_daily_split: bool = False,
                        leisure_subtype_split: bool = False,
                        other_subtype_split: bool = False,
                        leisure_visit_building_potential: bool = False,
                        escort_purpose: bool = False,
                        srv_location_types: bool = False):
    """Convert eqasim secondary candidates -> chainsolvers ``locations_df``.

    When ``with_potentials`` is True a ``potentials`` column is added: a
    semicolon-joined string aligned 1:1 with ``activities`` (the chainsolvers df
    parser reads per-activity potentials parallel to the activities list).

    When ``shop_daily_split`` is True (Tier 2: secondary_shop_daily_split) a
    building that offers shopping is emitted under the two internal subtype
    activities ``shop_daily`` / ``shop_non_daily`` (each carrying its own retail
    potential, ``pot_shop_daily`` / ``pot_shop_non_daily``) instead of a single
    ``shop`` activity, so the carla solver can place a daily shop leg at a
    daily-retail building and a non-daily leg at a non-daily-retail building. A
    subtype is only offered when its potential column is strictly positive, so a
    daily-only building is not a candidate for a non-daily leg and vice versa.
    ``shop_daily_split`` requires ``with_potentials`` (the split is meaningless
    without the per-subtype potentials). OFF (default) is byte-identical to the
    pre-feature behaviour (a single ``shop`` activity at ``pot_shop``).

    When ``leisure_subtype_split`` is True (Task 4, issue #127) a building that
    offers leisure is emitted under the four internal subtype activities
    (``LEISURE_SUBTYPE_ACTIVITIES``: leisure_local/visit/activity/excursion)
    INSTEAD OF the aggregate ``leisure`` activity. Unless
    ``leisure_visit_building_potential`` is also ON, there is no per-subtype
    building potential -- all four share the SAME ``pot_leisure`` value, so no
    offer is ever dropped for a non-positive potential here (that zero-skip
    only applies to the genuinely distinct ``SHOP_SUBTYPE_ACTIVITIES``
    potentials). ``leisure_subtype_split`` requires ``with_potentials``. OFF
    (default) is byte-identical.

    When ``leisure_visit_building_potential`` is also True (Task 5, issue #127)
    the ``leisure_visit`` subtype is REROUTED onto the dedicated residential
    candidate set: its offer column becomes ``VISIT_OFFER_COLUMN``
    ("offers_visit") instead of "offers_leisure", and its potential column
    becomes ``VISIT_POTENTIAL_COLUMN`` ("pot_visit") instead of "pot_leisure",
    so it only targets residential buildings appended by
    ``append_residential_visit_candidates`` (a "leisure_visit" offer with a
    non-positive ``pot_visit`` is dropped, mirroring the shop-subtype
    zero-skip). The other three leisure groups are unaffected (still
    "offers_leisure" / "pot_leisure"). Requires ``leisure_subtype_split`` and
    ``with_potentials``; fails fast if ``pot_visit`` is absent from
    ``df_secondary`` (no silent fallback to ``pot_leisure``).

    When ``other_subtype_split`` is True (Task 4, issue #127) a building that
    offers "other" is emitted under the three internal errand/escort subtype
    activities (``OTHER_SUBTYPE_ACTIVITIES``: other_errand_short/long,
    other_escort) IN ADDITION TO the aggregate ``other`` activity -- kept so
    ``other_rest`` legs (which the decider deliberately never subtypes, see
    ``_build_other_subtype_decider``) still find a candidate. All three subtypes
    share the SAME ``pot_other`` value. ``other_subtype_split`` requires
    ``with_potentials``. OFF (default) is byte-identical.

    When ``escort_purpose`` is True (issue #201) the seven internal
    ``ESCORT_LOCATION_ACTIVITIES`` are emitted IN ADDITION TO the aggregate/
    subtype activities above, so the same building can be a candidate for both
    its normal purpose and the matching escort drop-off/pick-up. Three
    (``escort_edu_kindergarten/school/university``) target the dedicated
    education candidates from :func:`append_escort_candidates`
    (``pot_escort_edu``); ``escort_leisure`` / ``escort_other`` / ``escort_shop``
    reuse the plain aggregate offer/potential of their base purpose;
    ``escort_residential`` reuses the residential visit candidates
    (``ESCORT_RESIDENTIAL_OFFER_COLUMN`` / ``pot_visit``) and is dropped for a
    non-positive potential, mirroring the shop-subtype zero-skip.
    ``escort_purpose`` requires ``with_potentials`` (the escort placement needs
    the education/visit/aggregate potential columns).

    When ``srv_location_types`` is True (issue #262, Task 8) the leisure/other
    placement vocabulary becomes the SrV-2023 location CATEGORIES instead of the
    MiD distance subtypes, because with the SrV decider active the MiD subtype is
    only a distance label and never a placement activity (see
    ``_build_plans_df``). Concretely, and REGARDLESS of
    ``leisure_subtype_split`` / ``other_subtype_split`` (which then only drive
    the distance layers):

    * a leisure-offering row emits the aggregate ``"leisure"`` activity (the
      placement target of the ``leisure_misc`` category) PLUS one activity per
      ``SRV_LEISURE_CATEGORIES`` member it actually offers
      (``leisure_culture`` / ``leisure_gastronomy`` / ``leisure_sports`` at
      ``pot_<category>`` from the building categories; ``leisure_outdoor`` and
      the landuse share of culture/sports from the ``sec_lu_*`` grid points;
      ``leisure_visit`` at ``offers_visit`` / ``pot_visit`` on the residential
      candidates), each dropped for a non-positive potential exactly like the
      shop subtypes;
    * an ``other``-offering row emits the aggregate ``"other"`` activity (for
      ``other_misc``) plus ``errand_authority_medical`` / ``errand_service``
      where their potential is positive;
    * the four MiD leisure subtypes and the three MiD other subtypes are NOT
      emitted (``leisure_visit`` survives only because it is ALSO an SrV
      category);
    * shop and escort emission is unchanged.

    ``srv_location_types`` requires ``with_potentials`` and fails fast when any
    ``offers_``/``pot_`` column of a placement category is missing from
    ``df_secondary`` -- a missing column means the candidate set was not built by
    the ``secondary_candidates`` stage's SrV branch, and silently skipping that
    category's offers would leave carla with no candidates for it (no silent
    fallback). OFF (default) is byte-identical, even on a candidate frame that
    already carries the category columns.
    """
    if shop_daily_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] shop_daily_split requires "
            "with_potentials (the daily/non-daily split needs the per-subtype "
            "retail potential columns)."
        )
    if leisure_subtype_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_subtype_split requires "
            "with_potentials (the leisure subtype placement needs the pot_leisure "
            "potential column)."
        )
    if other_subtype_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] other_subtype_split requires "
            "with_potentials (the other subtype placement needs the pot_other "
            "potential column)."
        )
    if leisure_visit_building_potential and not leisure_subtype_split:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "requires leisure_subtype_split (there is no 'leisure_visit' activity "
            "without the leisure subtype split)."
        )
    if leisure_visit_building_potential and VISIT_POTENTIAL_COLUMN not in df_secondary.columns:
        # Fail-fast (CLAUDE.md "Fallback transparency"): the flag promises a
        # dedicated residential potential; silently falling back to pot_leisure
        # here would hide a broken wiring (e.g. append_residential_visit_candidates
        # not called upstream) behind an apparently-working run.
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential is ON "
            "but the locations frame has no '%s' column (residential visit candidates "
            "were not appended -- call append_residential_visit_candidates() on "
            "df_secondary before _build_locations_df, or disable "
            "leisure_visit_building_potential)." % VISIT_POTENTIAL_COLUMN
        )
    if escort_purpose and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_purpose requires "
            "with_potentials (the escort placement needs the education/visit/"
            "aggregate potential columns)."
        )
    if srv_location_types and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] srv_location_types requires "
            "with_potentials (the SrV location-category placement needs the "
            "per-category pot_<category> columns)."
        )
    if srv_location_types:
        # Fail-fast on an incomplete candidate frame (same rationale as the
        # leisure_visit_building_potential check above): a missing category
        # column would otherwise silently remove that category's candidates.
        srv_required_columns = [
            column
            for category in SRV_PLACEMENT_CATEGORIES
            for column in (srv_category_offer_column(category),
                           srv_category_potential_column(category))
        ]
        srv_missing_columns = [
            column for column in dict.fromkeys(srv_required_columns)
            if column not in df_secondary.columns
        ]
        if srv_missing_columns:
            raise ValueError(
                "[braunschweig.secondary_chainsolvers] srv_location_types is ON but the "
                "locations frame has no %s column(s) (the SrV location-category candidates "
                "were not assembled -- run the braunschweig.synthesis.locations."
                "secondary_candidates stage with secondary_srv_location_types ON, or "
                "disable srv_location_types)." % srv_missing_columns
            )
    activities = []
    potentials = []
    # Activity emission order. With a split ON, the aggregate offer is either
    # REPLACED (shop, leisure -- every leg of that purpose gets a subtype) or
    # EXTENDED (other -- other_rest legs still need the plain "other" offer);
    # a purpose whose split is OFF keeps its single aggregate offer.
    shop_offer_specs = (
        (("shop_daily", "offers_shop"), ("shop_non_daily", "offers_shop"))
        if shop_daily_split else (("shop", "offers_shop"),)
    )
    # Issue #262: with the SrV categories owning placement, the MiD subtypes are
    # no longer placement activities at all -- the leisure/other vocabulary is
    # the aggregate purpose (for the ``*_misc`` categories) plus one activity per
    # drawable category. Checked FIRST so it overrides the MiD-subtype branches
    # below, which the same run also has ON (they still drive the distances).
    srv_category_offer_specs = tuple(
        (name, srv_category_offer_column(name)) for name in SRV_PLACEMENT_CATEGORIES
    ) if srv_location_types else ()
    if srv_location_types:
        leisure_offer_specs = (("leisure", "offers_leisure"),) + tuple(
            spec for spec in srv_category_offer_specs if spec[0] in SRV_LEISURE_CATEGORIES
        )
    elif leisure_subtype_split:
        leisure_offer_specs = tuple(
            (name, VISIT_OFFER_COLUMN if (leisure_visit_building_potential and name == "leisure_visit")
             else "offers_leisure")
            for name in LEISURE_SUBTYPE_ACTIVITIES
        )
    else:
        leisure_offer_specs = (("leisure", "offers_leisure"),)
    if srv_location_types:
        other_offer_specs = (("other", "offers_other"),) + tuple(
            spec for spec in srv_category_offer_specs if spec[0] in SRV_OTHER_CATEGORIES
        )
    elif other_subtype_split:
        other_offer_specs = tuple(
            (name, "offers_other") for name in OTHER_SUBTYPE_ACTIVITIES
        ) + (("other", "offers_other"),)
    else:
        other_offer_specs = (("other", "offers_other"),)
    escort_offer_specs = (
        (
            ("escort_edu_kindergarten", "offers_escort_edu_kindergarten"),
            ("escort_edu_school", "offers_escort_edu_school"),
            ("escort_edu_university", "offers_escort_edu_university"),
            ("escort_leisure", "offers_leisure"),
            ("escort_other", "offers_other"),
            ("escort_residential", ESCORT_RESIDENTIAL_OFFER_COLUMN),
            ("escort_shop", "offers_shop"),
        )
        if escort_purpose else ()
    )
    offer_specs = shop_offer_specs + leisure_offer_specs + other_offer_specs + escort_offer_specs
    # Per-activity potential column, overriding "leisure_visit" -> pot_visit
    # ONLY when leisure_visit_building_potential is ON (the OFF-path/default
    # mapping in _ACTIVITY_POTENTIAL_COLUMN stays leisure_visit -> pot_leisure,
    # see test_activity_potential_column_covers_all_subtype_activities).
    potential_column_by_activity = dict(_ACTIVITY_POTENTIAL_COLUMN)
    if leisure_visit_building_potential:
        potential_column_by_activity["leisure_visit"] = VISIT_POTENTIAL_COLUMN
    # Issue #262: every drawable SrV category is placed on its OWN potential
    # (pot_<category>, or pot_visit for "leisure_visit"), never on the shared
    # aggregate the MiD subtypes use.
    if srv_location_types:
        potential_column_by_activity.update({
            category: srv_category_potential_column(category)
            for category in SRV_PLACEMENT_CATEGORIES
        })
    cols = ["offers_shop", "offers_leisure", "offers_other"]
    if escort_purpose:
        cols = cols + list(ESCORT_EDU_OFFER_BY_TYPE.values()) + [ESCORT_RESIDENTIAL_OFFER_COLUMN]
    if srv_location_types:
        cols = cols + [offer for _, offer in srv_category_offer_specs]
    if with_potentials:
        # Only require the potential columns actually consumed by the active
        # offer_specs, so a non-split path does not demand subtype potential
        # columns (byte-identical + no spurious KeyError on candidate frames
        # that carry only the aggregate potentials). Deduplicated (preserving
        # first-seen order) because the leisure/other subtypes intentionally
        # SHARE one potential column across several offer_specs entries --
        # selecting a duplicated column name from df_secondary would otherwise
        # yield a multi-column slice instead of a per-row scalar below.
        potential_cols = [potential_column_by_activity[act] for act, _ in offer_specs]
        # "escort_residential" always maps to pot_visit (VISIT_POTENTIAL_COLUMN,
        # see the fixed _ACTIVITY_POTENTIAL_COLUMN entry above), but pot_visit is
        # only appended once append_residential_visit_candidates has actually run
        # -- escort_purpose alone does not guarantee it (append_escort_candidates
        # sets ESCORT_RESIDENTIAL_OFFER_COLUMN False on every row when the visit
        # machinery is off, so "escort_residential" is never offered and pot_visit
        # is never read in that case). Filtered to columns that actually exist so
        # escort_purpose stays usable without the residential visit machinery; a
        # column genuinely missing while its offer is True would still raise
        # inside the per-row loop below (fail loud, not silently wrong).
        cols = cols + [c for c in dict.fromkeys(potential_cols) if c in df_secondary.columns]
    if leisure_visit_building_potential:
        cols = cols + [VISIT_OFFER_COLUMN]
    cols = list(dict.fromkeys(cols))
    for _, row in df_secondary[cols].iterrows():
        acts, pots = [], []
        for act, offer in offer_specs:
            if not bool(row[offer]):
                continue
            if with_potentials:
                pot = float(row[potential_column_by_activity[act]])
                # A shop subtype with a zero potential is not a candidate for
                # that subtype (the building has no daily / no non-daily retail
                # floor area); the same zero-skip applies to "leisure_visit"
                # once it is rerouted onto pot_visit (a row offering
                # "leisure_visit" with a non-positive residential potential is
                # not a real candidate). The aggregate shop/leisure/other
                # offers, and the remaining leisure/other subtypes (which
                # share one undifferentiated potential column), are kept
                # regardless of sign so the OFF path stays byte-identical.
                if shop_daily_split and act in SHOP_SUBTYPE_ACTIVITIES and pot <= 0.0:
                    continue
                if leisure_visit_building_potential and act == "leisure_visit" and pot <= 0.0:
                    continue
                if escort_purpose and act == "escort_residential" and pot <= 0.0:
                    continue
                # Issue #262: a row that advertises an SrV category without a
                # positive potential for it is not a candidate for that category
                # (e.g. a leisure building whose culture potential is zero).
                if srv_location_types and act in SRV_PLACEMENT_CATEGORIES and pot <= 0.0:
                    continue
                acts.append(act)
                pots.append(pot)
            else:
                acts.append(act)
        activities.append("; ".join(acts))
        if with_potentials:
            potentials.append("; ".join(str(p) for p in pots))

    # Vectorised coordinate access (GeoSeries.x/.y) instead of a per-geometry
    # Python lambda; produces the identical (n, 2) ordering as the candidate
    # set, so the resulting locations table is byte-identical.
    coords = np.column_stack((
        df_secondary.geometry.x.values,
        df_secondary.geometry.y.values,
    ))
    data = {
        "id": df_secondary["location_id"].astype(str).values,
        "x": coords[:, 0],
        "y": coords[:, 1],
        "activities": activities,
    }
    if with_potentials:
        data["potentials"] = potentials
    return pd.DataFrame(data)
