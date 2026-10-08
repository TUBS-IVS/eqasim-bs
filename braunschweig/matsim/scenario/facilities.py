"""MATSim facilities writer: assembled secondary candidates + in-commuters.

Overrides matsim.scenario.facilities twice over the base behaviour:

1. **Assembled secondary candidates.** When ``secondary_building_potentials``
   is ON, the secondary chainsolvers place activities on the ASSEMBLED
   candidate set (gpkg ``sec_b_*`` buildings, legacy ``sec_*`` 'other' rows,
   external Gemeinde-centroid ids, residential ``sec_res_*`` visit/escort
   rows, education ``sec_edu_*`` escort rows) -- not on the legacy
   ``synthesis.locations.secondary`` frame the base writer uses. Facilities
   therefore consume the SAME
   ``braunschweig.synthesis.locations.secondary_candidates`` stage, so every
   location id the population can reference exists as a facility (2026-07-11
   kreis5 fix: realised ``sec_b_*`` ids were missing from facilities.xml and
   crashed RunPreparation's LinkAssignment). ``secondary_facility_frame``'s
   ``leisure_visit_enabled`` keyword (issue #201) gates the ``offers_visit``
   -> ``offers_leisure`` fold on the leisure feature actually being active,
   so an escort-only residential row (``leisure_visit_building_potential``
   OFF) advertises "escort" alone, not "leisure"; ``offers_escort`` itself
   always passes through unchanged (the base writer -- Task 8 -- decides
   whether to actually emit the "escort" activity option, gated on
   ``escort_purpose``).

2. **In-commuter facilities (terminal).** Registers a home facility
   (``home_<household_id>``) and a work facility (``ic_work_<person_id>``)
   for each injected SvB in-commuter, and -- mirroring the same pattern --
   a home facility (``home_<household_id>``) and an education facility
   (``ic_edu_<person_id>``) for each injected student in-commuter
   (``braunschweig.synthesis.student_incommuters``, #140 Task 5 review fix:
   these were previously never registered, which crashed MATSim
   RunPreparation with a dangling-facility IllegalStateException as soon as
   the student in-commuter feature was enabled). OFF -> no in-commuter
   frames -> byte-identical.

3. **Portal gate facilities (eqasim-bs#442, ADR-0141).** The locations of a portal stay carry
   ``portal_<gate_id>`` (``braunschweig.synthesis.locations.secondary_chainsolvers.portal_anchors``); one
   facility offering the activity type ``outside`` is registered per used gate at the gate coordinate, read
   from the realised secondary locations themselves (the same rows the population writer consumes). Eqasim
   core's Java ``LinkAssignment`` throws ``Facility ... does not exist`` for any activity without a facility.
   No portal rows (feature off, or no stay) -> nothing is registered and the file is byte-identical.

Additionally a flag-independent coverage validation compares the REALISED
secondary location ids against the written secondary facility ids and raises
before writing if any id would be dangling (fail-early instead of a Java
IllegalStateException 30+ minutes later).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

import matsim.scenario.facilities as base
from braunschweig.synthesis.portal_trips.config_keys import PORTAL_LOCATION_ID_PREFIX

logger = logging.getLogger(__name__)


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.student_incommuters")

    # Default mirrors secondary_chainsolvers.configure (True there).
    if context.config("secondary_building_potentials", True):
        context.stage("braunschweig.synthesis.locations.secondary_candidates")

    # Read-only mirrors (issue #201): this stage does not OWN either flag --
    # braunschweig.synthesis.locations.secondary_candidates / _chainsolvers do
    # -- but a synpp stage must declare every config key its own execute()
    # reads, so leisure_visit_building_potential (read below, to decide the
    # offers_visit -> offers_leisure fold) is mirrored here too. escort_purpose
    # is kept alongside it for the same reason.
    context.config("leisure_visit_building_potential", False)
    context.config("escort_purpose", False)
    # SrV-grounded location-category offers fold (issue #262): declared here for
    # the same reason as escort_purpose -- this stage's secondary_facility_frame()
    # reads it to decide the category-column fold into leisure/other.
    context.config("secondary_srv_location_types", False)
    # Only consumed to widen the coverage check with the education facility ids
    # that household-linked escort anchors reference (#201 Phase 2).
    context.config("escort_household_link", False)

    # Realised secondary locations, for the dangling-id validation below.
    context.stage("synthesis.population.spatial.secondary.locations")


def secondary_facility_frame(df_candidates, *, leisure_visit_enabled=True,
                             secondary_srv_location_types=False):
    """Map the assembled candidate frame onto the facilities SECONDARY_FIELDS.

    ``offers_visit`` rows (residential leisure_visit candidates) are folded
    into ``offers_leisure`` only when ``leisure_visit_enabled`` is True: the
    population writes the BASE purpose ("leisure") for subtype legs, so the
    facility must offer "leisure" for a visit location to be consistent --
    but only while the leisure feature actually places legs on these rows.
    Escort-only residential rows (``escort_purpose`` ON,
    ``leisure_visit_building_potential`` OFF) must advertise "escort" alone,
    not "leisure", so the fold is gated on the flag rather than unconditional.

    ``offers_escort`` (issue #201) needs no special handling here: it is
    already True/False on every candidate row (set by
    ``braunschweig.synthesis.locations.secondary_candidates``) and simply
    passes through the ``SECONDARY_FIELDS`` selection below, in both cases.

    SrV-grounded location-category columns (issue #262) fold into leisure/other
    only when ``secondary_srv_location_types`` is ON: ``offers_leisure`` gets
    all four category columns (culture, gastronomy, sports, outdoor), and
    ``offers_other`` gets the two errand categories (authority_medical, service).
    Each category column is guard-checked for presence when the flag is ON,
    raising a hard ValueError if missing -- no silent fold-skip.

    Parameters
    ----------
    df_candidates:
        The assembled secondary candidate GeoDataFrame (the return value of
        ``braunschweig.synthesis.locations.secondary_candidates``).
    leisure_visit_enabled:
        Whether ``leisure_visit_building_potential`` is ON. Keyword-only so
        callers cannot pass it positionally by mistake.
    secondary_srv_location_types:
        Whether ``secondary_srv_location_types`` is ON. Keyword-only. When ON,
        category-column offers fold into leisure/other. OFF (default): no fold,
        byte-identical treatment.

    Raises
    ------
    ValueError
        If ``secondary_srv_location_types`` is ON but a required category column
        is missing, or if ``df_candidates`` is missing a required
        ``SECONDARY_FIELDS`` column.
    """
    df = df_candidates.copy()
    if "offers_visit" in df.columns and leisure_visit_enabled:
        # The population writes the BASE purpose ("leisure") for subtype legs,
        # so a visit location must offer "leisure" -- but only when the leisure
        # feature actually places on these rows; escort-only residential rows
        # (escort_purpose ON, leisure_visit OFF) advertise just "escort".
        df["offers_leisure"] = df["offers_leisure"] | df["offers_visit"]

    if secondary_srv_location_types:
        # Fold SrV location-category columns into the base eqasim leisure/other
        # offers: every sec_lu_* candidate must advertise the base purpose its
        # internal category maps onto (e.g. "leisure" for culture/sports/etc,
        # "other" for errand authority/service).
        leisure_category_columns = [
            "offers_leisure_culture",
            "offers_leisure_gastronomy",
            "offers_leisure_sports",
            "offers_leisure_outdoor",
        ]
        errand_category_columns = [
            "offers_errand_authority_medical",
            "offers_errand_service",
        ]

        # Hard guard: when the flag is ON, every category column MUST exist.
        # No silent skip -- this catches stage-interface divergence immediately.
        missing_leisure = [c for c in leisure_category_columns if c not in df.columns]
        if missing_leisure:
            raise ValueError(
                "[braunschweig.facilities] secondary_srv_location_types is ON but "
                "required leisure category column(s) %s are missing; available: %s"
                % (missing_leisure, list(df.columns))
            )
        missing_errand = [c for c in errand_category_columns if c not in df.columns]
        if missing_errand:
            raise ValueError(
                "[braunschweig.facilities] secondary_srv_location_types is ON but "
                "required errand category column(s) %s are missing; available: %s"
                % (missing_errand, list(df.columns))
            )

        # Fold all leisure-category columns into offers_leisure
        for col in leisure_category_columns:
            df["offers_leisure"] = df["offers_leisure"] | df[col]

        # Fold all errand-category columns into offers_other
        for col in errand_category_columns:
            df["offers_other"] = df["offers_other"] | df[col]

    missing = [c for c in base.SECONDARY_FIELDS if c not in df.columns]
    if missing:
        raise ValueError(
            "[braunschweig.facilities] assembled secondary candidate frame is "
            "missing required column(s) %s; available: %s" % (missing, list(df.columns))
        )
    return df[base.SECONDARY_FIELDS]


def portal_facility_frame(df_realised):
    """One facility row per distinct portal gate used by the realised secondary locations.

    A row is a portal row when its ``location_id`` starts with ``PORTAL_LOCATION_ID_PREFIX``
    (``portal_<gate_id>``); the facility sits at the gate coordinate of that row. Returns a GeoDataFrame
    with ``base.PORTAL_FIELDS`` (empty when there is no portal row, i.e. feature off or no stay).

    Raises
    ------
    RuntimeError
        If one gate id appears at two different coordinates: a facility has exactly one coordinate, so the
        gate set of the portal stage and the realised locations have diverged.
    """
    location_ids = df_realised["location_id"].astype(str)
    is_portal = df_realised["location_id"].notna() & location_ids.str.startswith(PORTAL_LOCATION_ID_PREFIX)
    if not bool(is_portal.any()):
        return gpd.GeoDataFrame({"location_id": pd.Series([], dtype=object)}, geometry=gpd.GeoSeries([], crs=None),
                                crs=None)[base.PORTAL_FIELDS]
    portal_rows = df_realised.loc[is_portal, ["location_id", "geometry"]].copy()
    portal_rows["location_id"] = portal_rows["location_id"].astype(str)
    distinct = portal_rows.drop_duplicates(subset="location_id", keep="first")
    if len(distinct) < len(portal_rows.drop_duplicates(subset=["location_id", "geometry"])):
        coordinate_counts = (portal_rows.drop_duplicates(subset=["location_id", "geometry"])
                             .groupby("location_id").size())
        offending = sorted(coordinate_counts[coordinate_counts > 1].index)[:5]
        raise RuntimeError(
            "[braunschweig.facilities] portal gate id(s) %s occur at more than one coordinate in the realised "
            "secondary locations; a facility has one coordinate. The portal stage's gate set and the locations "
            "output have diverged." % offending)
    return gpd.GeoDataFrame(distinct[base.PORTAL_FIELDS].reset_index(drop=True), geometry="geometry",
                            crs=getattr(df_realised, "crs", None))


def validate_secondary_coverage(df_realised, df_secondary, extra_valid_ids=None, portal_facility_ids=None):
    """Fail fast if a realised secondary location id has no facility row.

    Every secondary activity's ``location_id`` must exist in the written
    secondary facilities, otherwise MATSim's RunPreparation crashes much later
    with an opaque ``IllegalStateException`` (the 2026-07-11 kreis5 failure
    mode). Raises RuntimeError naming the miss count and a sample.

    extra_valid_ids: facility ids written OUTSIDE the secondary frame that
    realised secondary rows may legitimately reference -- the household-linked
    escort anchors reference PRIMARY education facilities (#201 Phase 2).

    portal_facility_ids: ids of the portal gate facilities registered separately
    (eqasim-bs#442); a realised ``portal_*`` id that is not among them is dangling.
    """
    realised_ids = set(df_realised["location_id"].dropna().astype(str))
    written_ids = set(df_secondary["location_id"].astype(str))
    if extra_valid_ids:
        written_ids = written_ids | {str(i) for i in extra_valid_ids}
    n_portal_facilities = 0
    if portal_facility_ids:
        portal_ids = {str(i) for i in portal_facility_ids}
        n_portal_facilities = len(portal_ids)
        written_ids = written_ids | portal_ids
    missing = realised_ids - written_ids
    if missing:
        sample = sorted(missing)[:5]
        raise RuntimeError(
            "[braunschweig.facilities] %d realised secondary location id(s) have "
            "no facility row (sample: %s). The candidate set used by the "
            "chainsolvers and the facilities writer have diverged -- both must "
            "consume braunschweig.synthesis.locations.secondary_candidates."
            % (len(missing), sample)
        )
    logger.info(
        "[braunschweig.facilities] secondary coverage OK: %d realised ids, "
        "%d facility rows, 0 dangling; %d portal gate facilities registered.",
        len(realised_ids), len(written_ids), n_portal_facilities,
    )


def execute(context):
    output_path = "%s/facilities.xml.gz" % context.path()
    df_homes, df_primary, df_secondary = base.load_facility_frames(context)

    if context.config("secondary_building_potentials"):
        df_secondary = secondary_facility_frame(
            context.stage("braunschweig.synthesis.locations.secondary_candidates"),
            leisure_visit_enabled=bool(context.config("leisure_visit_building_potential")),
            secondary_srv_location_types=bool(context.config("secondary_srv_location_types")),
        )

    # Fail-early check: all realised secondary ids must be writable facilities.
    # Household-linked escort anchors (#201 Phase 2) reference PRIMARY EDUCATION
    # facility ids, which live in df_primary rather than df_secondary. Widen the
    # accepted set with those ids ONLY when the household link is ON, and only
    # with education ids -- keeping the guard as tight as possible otherwise.
    df_realised = context.stage("synthesis.population.spatial.secondary.locations")[0]
    extra_valid_ids = None
    if context.config("escort_household_link"):
        education_ids = df_primary.loc[~df_primary["is_work"].astype(bool), "location_id"]
        extra_valid_ids = set(education_ids.astype(str))
    # Portal gates (eqasim-bs#442): one facility per used gate; empty (a no-op) when the portal layer is off.
    df_portal = portal_facility_frame(df_realised)
    validate_secondary_coverage(df_realised, df_secondary, extra_valid_ids=extra_valid_ids,
                                portal_facility_ids=set(df_portal["location_id"]))

    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")
        loc = inc["locations"]
        # Guard on non-empty: the SvB stage returns a columns-less empty locations
        # frame (incommuters._empty_frames) when no in-commuter is injected (e.g. a
        # tiny region with zero inbound flow) even while cordon_enabled is True.
        # Without this guard, indexing "activity_index" would KeyError. Mirrors the
        # student block below (#140 review fix).
        if len(loc) > 0:
            persons = inc["persons"][["person_id", "household_id"]]

            # Home facilities: home_<household_id> at the home (activity 0) coordinate.
            home_rows = loc[loc["activity_index"] == 0].merge(persons, on="person_id")
            inc_homes = gpd.GeoDataFrame(home_rows[["household_id", "geometry"]],
                                         geometry="geometry", crs=df_homes.crs)
            df_homes = pd.concat([df_homes, inc_homes[base.HOME_FIELDS]], ignore_index=True)

            # Work facilities: the unique ic_work_<person_id> at the work (activity 1) coord.
            work_rows = loc[loc["activity_index"] == 1][["location_id", "geometry"]].copy()
            work_rows["is_work"] = True
            inc_work = gpd.GeoDataFrame(work_rows, geometry="geometry", crs=df_primary.crs)
            df_primary = pd.concat([df_primary, inc_work[base.PRIMARY_FIELDS]], ignore_index=True)

        # Student in-commuters (#140 Task 5 review fix): the same home + middle-
        # activity facility registration as the SvB block above, but the middle
        # activity is "education" (is_work=False) instead of "work". The OFF/skip
        # path returns a columns-less empty locations frame
        # (student_incommuters._empty_frames), so guard on non-empty before
        # indexing "activity_index" -- this keeps the OFF path a true no-op.
        student_inc = context.stage("braunschweig.synthesis.student_incommuters")
        student_loc = student_inc["locations"]
        if len(student_loc) > 0:
            student_persons = student_inc["persons"][["person_id", "household_id"]]

            # Home facilities: home_<household_id> at the home (activity 0) coordinate.
            student_home_rows = (student_loc[student_loc["activity_index"] == 0]
                                 .merge(student_persons, on="person_id"))
            student_homes = gpd.GeoDataFrame(
                student_home_rows[["household_id", "geometry"]],
                geometry="geometry", crs=df_homes.crs)
            df_homes = pd.concat([df_homes, student_homes[base.HOME_FIELDS]],
                                 ignore_index=True)

            # Education facilities: the unique ic_edu_<person_id> at the education
            # (activity 1) coordinate.
            student_edu_rows = (student_loc[student_loc["activity_index"] == 1]
                                [["location_id", "geometry"]].copy())
            student_edu_rows["is_work"] = False
            student_edu = gpd.GeoDataFrame(student_edu_rows, geometry="geometry",
                                           crs=df_primary.crs)
            df_primary = pd.concat([df_primary, student_edu[base.PRIMARY_FIELDS]],
                                   ignore_index=True)

    return base.write_facilities(output_path, df_homes, df_primary, df_secondary, context,
                                 df_portal=df_portal)
