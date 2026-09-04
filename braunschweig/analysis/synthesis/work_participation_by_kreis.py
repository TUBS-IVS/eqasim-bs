"""synpp stage: work participation and assigned commute-distance classes per home Kreis.

Phase A measurement for the commute-day-state model (spec 2026-09-04, issue #244). The stage
is PURELY DIAGNOSTIC -- it reads cached synthesis stages and writes a report; it changes no
model behaviour and no pipeline output.

It answers three questions on a finished synthetic population:

1. **Work participation.** Of the persons the model calls ``employed``, what share actually
   makes a home->work trip on the simulated day, per home Kreis and for the ZGB total? The
   committed SrV 2023 reference (``braunschweig.calibration.srv_work_participation``) gives
   the observed counterpart. NOTE the asymmetry, restated in ``summary.md``: SrV splits the
   non-work-trip remainder into "full home-office day" and "neither", while the model has no
   day-state at all, so ONLY ``share_work_trip`` is comparable -- the model's
   ``share_no_work_trip`` is the sum of the two SrV remainder states, not a home-office share.
2. **Assigned commute-distance classes.** How do the realised home->work distances (euclidean
   x detour factor) distribute over the commute-distance classes of
   ``braunschweig.calibration.commute_day_state_reference`` (``lt10`` ... ``gt200``), per Kreis,
   split by whether the destination is external (outside the ZGB) or internal?
3. **External-destination geometry.** External workplaces are synthetic points fabricated by
   ``braunschweig.data.external_workplaces`` (``commune_id = "EXT" + <8-digit AGS>``). For each
   (home Kreis, destination Kreis) pair the stage compares the realised model distance against
   the plain Kreis-centroid-to-Kreis-centroid distance (VG250 ``vg250_krs``), so a systematic
   distance bias introduced by the point fabrication becomes visible, alongside the BA
   Pendleratlas flow of that pair.

Outputs go under ``<output_path>/<cds_output_subdir>/`` (default
``analysis/commute_day_state_phase_a``); see :func:`write_outputs` for the file set and
:data:`PARTICIPATION_COLUMNS` / :data:`DISTANCE_CLASS_COLUMNS` / :data:`EXT_DISTANCE_COLUMNS` /
:data:`PER_PERSON_COLUMNS` for the schemas.

Per CLAUDE.md "Fallback transparency" every exclusion path is counted, logged with its rate
under the ``[commute_day_state]`` marker, and recorded in ``provenance.json``; the two home-match
paths RAISE above ``cds_max_unmatched_home_share`` rather than degrading silently, because a
high unmatched rate almost always signals a broken VG250/household join and would make the
per-Kreis comparison meaningless. No number in the outputs is invented: the only reference is
the committed SrV table, and a Kreis without a reference row keeps ``NaN`` rather than a
substituted value.

This stage is measurement only. It states a difference against a reference; it does NOT
validate the model against observed behaviour, and it decides nothing (no pre-registered rule
is attached in Phase A).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.calibration import commute_day_state_reference as R
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.calibration.srv_work_participation import load_srv_work_participation

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.work_participation_by_kreis")

_LOG_TAG = "[commute_day_state]"

# --------------------------------------------------------------------------- config keys

KEY_DETOUR = "cds_detour_factor"
KEY_SUBDIR = "cds_output_subdir"
KEY_MAX_UNMATCHED_HOME_SHARE = "cds_max_unmatched_home_share"
KEY_EDGE_TOLERANCE_KM = "cds_edge_tolerance_km"

#: Euclidean -> routed conversion, same convention (and default) as
#: ``braunschweig.analysis.synthesis.commute_distance_by_kreis``; the SrV/MiD distance classes
#: are routed distances, the model's are straight-line, so they are not comparable unscaled.
DEFAULT_DETOUR_FACTOR = 1.3
DEFAULT_SUBDIR = "analysis/commute_day_state_phase_a"
#: Above this share of persons without a home Kreis match the stage raises (see the module
#: docstring); 5% mirrors ``srv_distance_max_unmatched_home_share``.
DEFAULT_MAX_UNMATCHED_HOME_SHARE = 0.05
#: Half-width (km) of the band around a class edge inside which a worker counts as "near an
#: edge", i.e. a worker whose assigned class would flip under a small distance change.
DEFAULT_EDGE_TOLERANCE_KM = 5.0

#: eqasim trip purpose marking a home->work leg (``synthesis.population.trips``).
WORK_PURPOSE = "work"
#: Prefix that ``braunschweig.data.external_workplaces`` puts in front of the 8-digit AGS of a
#: fabricated out-of-region workplace, so ``commune_id[3:8]`` is the destination Kreis ARS5.
EXTERNAL_PREFIX = "EXT"

ZGB_ROW_CODE = "zgb"
#: Destination scopes of the distance-class table (see :func:`assigned_distance_classes`).
SCOPES = ("all", "external", "internal")

PARTICIPATION_COLUMNS = (
    "code", "n_employed", "n_with_work_trip", "share_work_trip", "share_no_work_trip",
    "srv_n_persons", "srv_share_work_trip", "srv_share_home_office_day", "srv_share_neither",
    "delta_work_trip_pp",
)
DISTANCE_CLASS_COLUMNS = ("code", "scope", "distance_class", "n_workers", "share")
EXT_DISTANCE_COLUMNS = (
    "home_ars5", "dest_ars5", "n_model", "model_km_median", "model_km_p10", "model_km_p90",
    "centroid_km", "class_model_median", "class_centroid", "same_class", "ba_flow",
)
PER_PERSON_COLUMNS = (
    "person_id", "assigned_distance_class", "distance_km", "destination_is_external",
    "home_ars5", "destination_ars5",
)

_MODEL_PARTICIPATION_COLUMNS = (
    "code", "n_employed", "n_with_work_trip", "share_work_trip", "share_no_work_trip",
)


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    # The workplace pool is declared under its CONCRETE name, not under the
    # ``synthesis.locations.work`` alias seam, exactly as braunschweig.synthesis.incommuters
    # does. Every committed config aliases the seam to this module (see the stage record
    # synthesis.locations.work: resolves_to is braunschweig.locations.work for all three
    # workflows), so the two names denote the same stage -- but adding a NEW declaration of the
    # seam name flips how synpp labels that single node in the production DAG snapshot
    # (braunschweig.locations.work -> synthesis.locations.work), which renames a node four other
    # stages already depend on and breaks the stage-registry coverage check. Declaring the
    # concrete name keeps this stage purely additive to the graph.
    context.stage("braunschweig.locations.work")
    context.stage("data.spatial.municipalities")
    context.stage("braunschweig.data.census.pendler")
    context.config("output_path")
    context.config("data_path")
    context.config("sampling_rate")
    context.config(KEY_DETOUR, DEFAULT_DETOUR_FACTOR)
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_MAX_UNMATCHED_HOME_SHARE, DEFAULT_MAX_UNMATCHED_HOME_SHARE)
    context.config(KEY_EDGE_TOLERANCE_KM, DEFAULT_EDGE_TOLERANCE_KM)


# --------------------------------------------------------------------------- small helpers

def _require_columns(frame, columns, what):
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{what} is missing the required column(s) {missing} (present: "
            f"{sorted(frame.columns)[:20]}); this stage reads a finished synthetic population "
            f"and cannot reconstruct them")


def _rate(numerator, denominator):
    return float(numerator) / float(denominator) if denominator else 0.0


def _guard_unmatched_home_share(n_unmatched, n_total, cohort_label, max_unmatched_home_share):
    """Log the unmatched-home rate for ``cohort_label`` and raise above the threshold.

    A high share of persons whose home point resolves to no Kreis almost always signals a
    broken VG250 / household join (stale archive, wrong CRS) rather than genuinely home-less
    persons; silently dropping them would make every per-Kreis number below meaningless for
    that share of the cohort (CLAUDE.md "Fallback transparency").
    """
    rate = _rate(n_unmatched, n_total)
    LOGGER.info("%s %s: %d/%d (%.2f%%) with no home Kreis (ars5) match",
                _LOG_TAG, cohort_label, n_unmatched, n_total, 100.0 * rate)
    if rate > max_unmatched_home_share:
        raise ValueError(
            f"{n_unmatched}/{n_total} ({100.0 * rate:.1f}%) {cohort_label} have no home Kreis "
            f"match; exceeds {KEY_MAX_UNMATCHED_HOME_SHARE}={max_unmatched_home_share} -- the "
            f"per-Kreis comparison would be meaningless for that share; check the VG250 archive "
            f"and the synthesis.population.spatial.home.locations / household_id join")


def _dedupe_homes(homes, columns):
    """One row per ``household_id``; logs how many duplicates were removed.

    ``braunschweig.analysis.spatial.assign_geographies`` does not deduplicate its Kreis sjoin,
    so a home point exactly on a Kreis boundary can produce two rows for one household; left
    unhandled that silently fans persons out into duplicate rows downstream.
    """
    frame = homes[list(columns)]
    n_before = len(frame)
    frame = frame.drop_duplicates("household_id", keep="first")
    n_duplicates = n_before - len(frame)
    if n_duplicates:
        LOGGER.warning(
            "%s %d duplicate household_id row(s) in the home-geography frame removed (keeping "
            "the first); a home point on a Kreis boundary can be matched twice by the sjoin",
            _LOG_TAG, n_duplicates)
    return frame


# --------------------------------------------------------------------------- participation

def work_participation_by_kreis(persons, trips, homes_with_ars5,
                                max_unmatched_home_share=DEFAULT_MAX_UNMATCHED_HOME_SHARE,
                                stats=None):
    """Share of employed persons who make a home->work trip, per home Kreis + the ZGB total.

    ``persons`` must carry ``person_id, household_id, employed`` (the ``employed`` flag of
    ``synthesis.population.enriched``; it is absent when ``reactivate_person_attributes`` is
    OFF, which this function reports as a hard error rather than assuming a value), ``trips``
    must carry ``person_id, following_purpose`` (eqasim schema), and ``homes_with_ars5`` must
    carry ``household_id, ars5`` as produced by
    ``braunschweig.analysis.spatial.assign_geographies``.

    A person counts as making a work trip when at least one of their trips has
    ``following_purpose == "work"``. Only ``employed`` persons enter the denominator, so the
    result is directly comparable to the SrV universe (persons who were asked the home-office
    question, i.e. the employed).

    Emits one row per code in ``ZGB_KREISE`` -- including Kreise with zero employed persons,
    whose shares are ``NaN`` rather than 0 (never a substituted value) -- plus one ``zgb`` row
    over exactly the union of those Kreise, so ``sum(kreis n_employed) == zgb n_employed``
    holds by construction (same convention as the SrV reference table). Persons whose home
    resolves to no Kreis, or to a Kreis outside the ZGB, are excluded from both and counted;
    the unmatched-home rate raises above ``max_unmatched_home_share``. ``stats``, if a dict, is
    filled with the diagnostic counts for ``provenance.json``.
    """
    _require_columns(persons, ("person_id", "household_id", "employed"),
                     "synthesis.population.enriched")
    _require_columns(trips, ("person_id", "following_purpose"), "synthesis.population.trips")
    _require_columns(homes_with_ars5, ("household_id", "ars5"), "the home-geography frame")

    frame = persons[["person_id", "household_id", "employed"]].merge(
        _dedupe_homes(homes_with_ars5, ("household_id", "ars5")),
        on="household_id", how="left", validate="m:1")

    n_employed_missing = int(frame["employed"].isna().sum())
    if n_employed_missing:
        LOGGER.warning(
            "%s %d/%d persons have a missing 'employed' flag; counted as NOT employed (they "
            "cannot enter a participation denominator without inventing their status)",
            _LOG_TAG, n_employed_missing, len(frame))
    employed = frame[frame["employed"].fillna(False).astype(bool)].copy()

    n_employed_total = len(employed)
    n_home_unmatched = int(employed["ars5"].isna().sum())
    _guard_unmatched_home_share(n_home_unmatched, n_employed_total, "employed persons",
                                max_unmatched_home_share)

    in_zgb = employed["ars5"].isin(ZGB_KREISE)
    n_outside_zgb = int((employed["ars5"].notna() & ~in_zgb).sum())
    if n_outside_zgb:
        LOGGER.warning(
            "%s %d/%d employed persons have a home Kreis outside the 8 ZGB Kreise; excluded "
            "from both the per-Kreis rows and the zgb row",
            _LOG_TAG, n_outside_zgb, n_employed_total)
    employed = employed[in_zgb].copy()

    work_trip_persons = trips.loc[trips["following_purpose"] == WORK_PURPOSE, "person_id"]
    employed["has_work_trip"] = employed["person_id"].isin(set(work_trip_persons))

    rows = [_participation_row(code, employed[employed["ars5"] == code]) for code in ZGB_KREISE]
    rows.append(_participation_row(ZGB_ROW_CODE, employed))
    table = pd.DataFrame(rows, columns=list(_MODEL_PARTICIPATION_COLUMNS))

    LOGGER.info(
        "%s work participation: %d employed persons in the ZGB, %d with a work trip (%.2f%%); "
        "%d excluded without a home Kreis, %d excluded outside the ZGB, %d with a missing "
        "employed flag",
        _LOG_TAG, len(employed), int(employed["has_work_trip"].sum()),
        100.0 * _rate(int(employed["has_work_trip"].sum()), len(employed)),
        n_home_unmatched, n_outside_zgb, n_employed_missing)

    if stats is not None:
        stats.update(n_persons_total=int(len(frame)), n_employed_total=n_employed_total,
                     n_employed_missing_flag=n_employed_missing,
                     n_home_unmatched=n_home_unmatched, n_outside_zgb=n_outside_zgb,
                     n_employed_in_zgb=int(len(employed)),
                     n_with_work_trip=int(employed["has_work_trip"].sum()))
    return table


def _participation_row(code, subset):
    n_employed = int(len(subset))
    n_with_work_trip = int(subset["has_work_trip"].sum()) if n_employed else 0
    if n_employed == 0:
        # No employed person: a share is undefined, not zero (CLAUDE.md: never substitute a
        # value for an absent measurement).
        return {"code": code, "n_employed": 0, "n_with_work_trip": 0,
                "share_work_trip": float("nan"), "share_no_work_trip": float("nan")}
    share = n_with_work_trip / n_employed
    return {"code": code, "n_employed": n_employed, "n_with_work_trip": n_with_work_trip,
            "share_work_trip": float(share), "share_no_work_trip": float(1.0 - share)}


def compare_participation(model, srv_table):
    """Join the committed SrV 2023 reference onto the model participation table.

    ``srv_table`` is ``braunschweig.calibration.srv_work_participation.load_srv_work_participation``'s
    frame (``level, code, n_persons, share_home_office_day, share_work_trip, share_neither``).
    ``delta_work_trip_pp`` is ``(model - reference) * 100`` in percentage points and stays
    ``NaN`` wherever the reference share is ``NaN`` -- notably Wolfsburg (03103), which SrV does
    not survey (``n_persons == 0``) -- so an absent reference can never be read as a zero gap.

    Only ``share_work_trip`` is comparable between the two sides: the model has no day-state, so
    its ``share_no_work_trip`` corresponds to the SUM of the SrV ``share_home_office_day`` and
    ``share_neither`` columns, which are carried through for context, not for a delta.
    """
    _require_columns(model, _MODEL_PARTICIPATION_COLUMNS, "the model participation table")
    _require_columns(srv_table, ("code", "n_persons", "share_work_trip",
                                 "share_home_office_day", "share_neither"),
                     "the committed SrV work-participation table")
    reference = srv_table[["code", "n_persons", "share_work_trip", "share_home_office_day",
                           "share_neither"]].rename(columns={
        "n_persons": "srv_n_persons",
        "share_work_trip": "srv_share_work_trip",
        "share_home_office_day": "srv_share_home_office_day",
        "share_neither": "srv_share_neither",
    })
    out = model.merge(reference, on="code", how="left", validate="1:1")

    missing = out.loc[out["srv_n_persons"].isna(), "code"].tolist()
    if missing:
        LOGGER.warning(
            "%s %d code(s) have no SrV reference row and keep NaN reference columns: %s",
            _LOG_TAG, len(missing), missing)
    empty_reference = out.loc[out["srv_n_persons"].fillna(-1) == 0, "code"].tolist()
    if empty_reference:
        LOGGER.info(
            "%s %d code(s) have a SrV reference row with n_persons == 0 (not surveyed) and "
            "therefore a NaN delta: %s", _LOG_TAG, len(empty_reference), empty_reference)

    out["delta_work_trip_pp"] = 100.0 * (out["share_work_trip"] - out["srv_share_work_trip"])
    return out[list(PARTICIPATION_COLUMNS)]


# --------------------------------------------------------------------------- realised work

def _validate_geometry_crs(homes, df_work):
    """Both geometry inputs must carry the same, non-None, PROJECTED CRS.

    Shapely computes a planar distance on raw coordinates whatever CRS label a frame carries,
    so a mismatch -- or a geographic (degree-based) CRS -- would still "run" and produce a
    number with no defensible unit. Failing here is the only scientifically defensible
    behaviour (CLAUDE.md "Geospatial processing").
    """
    home_crs, work_crs = homes.crs, df_work.crs
    if home_crs is None or work_crs is None:
        raise ValueError(
            f"Missing CRS: homes={home_crs}, work destinations={work_crs}; every input geometry "
            f"must carry an explicit CRS")
    if home_crs != work_crs:
        raise ValueError(
            f"CRS mismatch between the stage inputs: homes={home_crs}, work destinations="
            f"{work_crs} -- both must match; reproject upstream")
    if not home_crs.is_projected:
        raise ValueError(
            f"Home/work geometries use the geographic CRS {home_crs}; metric distances require "
            f"a projected CRS (the pipeline uses EPSG:25832)")


def _destination_ars5(commune_id, is_external):
    """Destination Kreis ARS5 of one workplace ``commune_id``.

    External workplaces carry ``"EXT" + <8-digit AGS>`` (see
    ``braunschweig.data.external_workplaces``), so their Kreis is ``commune_id[3:8]``; a real
    ZGB workplace carries the plain 8-digit AGS, whose Kreis is the first 5 digits. Anything
    else returns ``""`` -- the caller counts those rather than guessing a Kreis.
    """
    if commune_id is None or (isinstance(commune_id, float) and math.isnan(commune_id)):
        return ""
    text = str(commune_id)
    if is_external:
        candidate = text[3:8]
    else:
        candidate = text[:5]
    return candidate if len(candidate) == 5 and candidate.isdigit() else ""


def realised_work_frame(homes, df_work, work_locations, persons,
                        detour_factor=DEFAULT_DETOUR_FACTOR,
                        max_unmatched_home_share=DEFAULT_MAX_UNMATCHED_HOME_SHARE,
                        known_commune_ids=None, stats=None):
    """Per worker: home Kreis, destination commune/Kreis, routed distance and distance class.

    Inputs (all already produced by cached synthesis stages):

    * ``homes`` -- GeoDataFrame ``household_id, geometry, ars5`` from
      ``braunschweig.analysis.spatial.assign_geographies``.
    * ``df_work`` -- GeoDataFrame ``person_id, location_id, geometry``, the work half of
      ``synthesis.population.spatial.primary.locations``.
    * ``work_locations`` -- DataFrame ``location_id, commune_id`` from
      ``braunschweig.locations.work``. The workplace ``commune_id`` is taken from here rather than
      from ``df_work``, because ``df_work`` names its destination-zone column ``commune_id``
      only while ``taz_work_location_choice`` is OFF (it is ``work_taz_id`` when ON), whereas
      the ``location_id`` join is correct under both settings.
    * ``persons`` -- DataFrame ``person_id, household_id``.
    * ``known_commune_ids`` -- optional set of the ZGB Gemeinde ids (``data.spatial.municipalities``)
      used only to COUNT internal destinations whose commune is outside that universe; it never
      changes a value.

    ``distance_km`` is the euclidean home->workplace distance in kilometres multiplied by
    ``detour_factor``, matching the routed distances the reference classes are defined on;
    ``distance_class`` applies ``commute_day_state_reference.classify_commute_distance`` to it.

    Every drop is counted and logged with its rate: workers without a home geometry/Kreis
    (guarded -- raises above ``max_unmatched_home_share``), workers whose ``location_id`` has no
    workplace row, and workers with a NaN distance. ``stats``, if a dict, receives the counts.
    """
    _require_columns(homes, ("household_id", "geometry", "ars5"), "the home-geography frame")
    _require_columns(df_work, ("person_id", "location_id", "geometry"),
                     "synthesis.population.spatial.primary.locations (work)")
    _require_columns(work_locations, ("location_id", "commune_id"), "braunschweig.locations.work")
    _require_columns(persons, ("person_id", "household_id"), "synthesis.population.enriched")
    _validate_geometry_crs(homes, df_work)

    home_per_person = persons[["person_id", "household_id"]].merge(
        _dedupe_homes(homes, ("household_id", "geometry", "ars5")).rename(
            columns={"geometry": "home_geometry", "ars5": "home_ars5"}),
        on="household_id", how="left", validate="m:1")

    frame = df_work[["person_id", "location_id", "geometry"]].rename(
        columns={"geometry": "dest_geometry"}).merge(
        home_per_person[["person_id", "home_geometry", "home_ars5"]], on="person_id", how="left",
        validate="m:1")

    n_workers_input = len(frame)
    n_no_home_geometry = int(frame["home_geometry"].isna().sum())
    if n_no_home_geometry:
        LOGGER.warning(
            "%s %d/%d workers (%.2f%%) have no home geometry after the person->home merge and "
            "are dropped -- check the home-locations / household_id join",
            _LOG_TAG, n_no_home_geometry, n_workers_input,
            100.0 * _rate(n_no_home_geometry, n_workers_input))
    _guard_unmatched_home_share(n_no_home_geometry, n_workers_input,
                                "workers (no home geometry)", max_unmatched_home_share)
    frame = frame[frame["home_geometry"].notna()].copy()

    n_input = len(frame)
    n_home_kreis_unmatched = int(frame["home_ars5"].isna().sum())
    _guard_unmatched_home_share(n_home_kreis_unmatched, n_input, "workers",
                                max_unmatched_home_share)

    locations = work_locations[["location_id", "commune_id"]].drop_duplicates("location_id")
    frame = frame.merge(locations.rename(columns={"commune_id": "destination_commune_id"}),
                        on="location_id", how="left", validate="m:1")
    n_no_workplace_row = int(frame["destination_commune_id"].isna().sum())
    if n_no_workplace_row:
        LOGGER.warning(
            "%s %d/%d workers (%.2f%%) have a location_id with no row in "
            "braunschweig.locations.work; their destination Kreis stays empty",
            _LOG_TAG, n_no_workplace_row, n_input, 100.0 * _rate(n_no_workplace_row, n_input))

    commune = frame["destination_commune_id"].astype("string")
    frame["destination_is_external"] = commune.str.startswith(EXTERNAL_PREFIX).fillna(False)
    frame["destination_ars5"] = [
        _destination_ars5(value, bool(flag))
        for value, flag in zip(frame["destination_commune_id"], frame["destination_is_external"])
    ]

    home_points = gpd.GeoSeries(frame["home_geometry"].values, crs=homes.crs)
    dest_points = gpd.GeoSeries(frame["dest_geometry"].values, crs=df_work.crs)
    frame["distance_km"] = home_points.distance(dest_points).values / 1000.0 * float(detour_factor)

    n_nan_distance = int(frame["distance_km"].isna().sum())
    if n_nan_distance:
        LOGGER.warning(
            "%s %d/%d workers (%.2f%%) have a NaN euclidean distance and are dropped -- check "
            "for missing home/destination geometries",
            _LOG_TAG, n_nan_distance, n_input, 100.0 * _rate(n_nan_distance, n_input))
    frame = frame[frame["distance_km"].notna()].copy()
    frame["distance_class"] = [R.classify_commute_distance(km) for km in frame["distance_km"]]

    n_external = int(frame["destination_is_external"].sum())
    n_no_dest_kreis = int((frame["destination_ars5"] == "").sum())
    n_internal_commune_unknown = 0
    if known_commune_ids is not None:
        internal = frame.loc[~frame["destination_is_external"], "destination_commune_id"]
        n_internal_commune_unknown = int((~internal.astype("string").isin(
            {str(value) for value in known_commune_ids})).sum())
        if n_internal_commune_unknown:
            LOGGER.warning(
                "%s %d/%d internal work destinations have a commune_id outside the "
                "data.spatial.municipalities universe -- check the workplace pool",
                _LOG_TAG, n_internal_commune_unknown, int(len(internal)))

    LOGGER.info(
        "%s realised work: %d workers input, %d without home geometry dropped, %d without a "
        "home Kreis, %d without a workplace row, %d with a NaN distance dropped, %d kept; "
        "external destinations %d (%.2f%%), destinations without a resolvable Kreis %d (%.2f%%)",
        _LOG_TAG, n_workers_input, n_no_home_geometry, n_home_kreis_unmatched,
        n_no_workplace_row, n_nan_distance, len(frame), n_external,
        100.0 * _rate(n_external, len(frame)), n_no_dest_kreis,
        100.0 * _rate(n_no_dest_kreis, len(frame)))

    if stats is not None:
        stats.update(n_workers_input=n_workers_input, n_no_home_geometry=n_no_home_geometry,
                     n_home_kreis_unmatched=n_home_kreis_unmatched,
                     n_no_workplace_row=n_no_workplace_row, n_nan_distance=n_nan_distance,
                     n_workers=int(len(frame)), n_external=n_external,
                     n_no_destination_kreis=n_no_dest_kreis,
                     n_internal_commune_unknown=n_internal_commune_unknown)

    return frame[["person_id", "home_ars5", "destination_commune_id", "destination_is_external",
                  "destination_ars5", "distance_km", "distance_class"]].reset_index(drop=True)


# --------------------------------------------------------------------------- distance classes

def assigned_distance_classes(realised_work, stats=None):
    """Worker counts and shares per (home Kreis, destination scope, commute-distance class).

    ``realised_work`` must carry ``home_ars5, distance_class, destination_is_external``.
    Emits the full cross product of ``ZGB_KREISE + ["zgb"]``, :data:`SCOPES` (``all`` /
    ``external`` / ``internal``) and ``commute_day_state_reference.COMMUTE_CLASS_LABELS``, so a
    class that no worker reached is an explicit ``n_workers == 0`` row rather than an absent one
    a reader could mistake for missing data. ``share`` is within its (code, scope) cell and is
    ``NaN`` when that cell has no worker at all.

    Workers whose ``distance_class`` is missing (a NaN or non-positive distance) are excluded
    from every count and reported once as an explicit rate.
    """
    _require_columns(realised_work, ("home_ars5", "distance_class", "destination_is_external"),
                     "the realised work frame")
    n_input = len(realised_work)
    classified = realised_work[realised_work["distance_class"].notna()]
    n_unclassified = n_input - len(classified)
    if n_unclassified:
        LOGGER.warning(
            "%s %d/%d workers (%.2f%%) have no distance class (NaN or non-positive distance) "
            "and are excluded from the distance-class table",
            _LOG_TAG, n_unclassified, n_input, 100.0 * _rate(n_unclassified, n_input))
    if stats is not None:
        stats.update(n_classified=int(len(classified)), n_unclassified=int(n_unclassified))

    rows = []
    for code in list(ZGB_KREISE) + [ZGB_ROW_CODE]:
        in_code = classified if code == ZGB_ROW_CODE else classified[classified["home_ars5"] == code]
        for scope in SCOPES:
            if scope == "external":
                subset = in_code[in_code["destination_is_external"].astype(bool)]
            elif scope == "internal":
                subset = in_code[~in_code["destination_is_external"].astype(bool)]
            else:
                subset = in_code
            counts = subset["distance_class"].value_counts()
            total = int(counts.sum())
            for label in R.COMMUTE_CLASS_LABELS:
                n_workers = int(counts.get(label, 0))
                rows.append({"code": code, "scope": scope, "distance_class": label,
                             "n_workers": n_workers,
                             "share": (n_workers / total) if total else float("nan")})
    return pd.DataFrame(rows, columns=list(DISTANCE_CLASS_COLUMNS))


def near_class_edge_share(distances_km, edges=R.COMMUTE_CLASS_EDGES_KM,
                          tolerance_km=DEFAULT_EDGE_TOLERANCE_KM):
    """Share of workers whose distance lies within ``tolerance_km`` of a class edge.

    Only the FINITE interior edges count (``edges[1:-1]``: 10/25/50/100/200 km with the default
    class definition); 0 and infinity are the outer bounds of the scale, not boundaries a worker
    can sit near. The band is inclusive (``<= tolerance_km``). Missing and non-positive
    distances are excluded, exactly as ``classify_commute_distance`` excludes them; the result
    is ``NaN`` when no valid distance remains, never 0 (which would read as "no worker sits near
    an edge").

    This is a fragility diagnostic: a high share means the assigned-class distribution would
    move substantially under a small change of the distance model (e.g. the detour factor).
    """
    if tolerance_km is None or float(tolerance_km) < 0:
        raise ValueError(
            f"{KEY_EDGE_TOLERANCE_KM} must be a non-negative distance in kilometres, got "
            f"{tolerance_km}")
    interior = [float(edge) for edge in edges[1:-1] if np.isfinite(edge)]
    values = pd.to_numeric(pd.Series(list(distances_km), dtype="float64"), errors="coerce")
    valid = values[values.notna() & (values > 0)]
    if valid.empty or not interior:
        LOGGER.info("%s near-class-edge share: no valid distance (or no interior class edge); "
                    "reporting NaN", _LOG_TAG)
        return float("nan")
    distance_to_edge = np.min(
        np.abs(valid.to_numpy()[:, None] - np.array(interior)[None, :]), axis=1)
    share = float(np.mean(distance_to_edge <= float(tolerance_km)))
    LOGGER.info("%s near-class-edge share: %d/%d workers (%.2f%%) lie within %.1f km of a class "
                "edge %s", _LOG_TAG, int(np.sum(distance_to_edge <= float(tolerance_km))),
                len(valid), 100.0 * share, float(tolerance_km), interior)
    return share


# --------------------------------------------------------------------------- EXT destinations

def ext_destination_distances(realised_work_ext, kreis_centroids, ba_flows,
                              detour_factor=DEFAULT_DETOUR_FACTOR, stats=None):
    """Per (home Kreis, external destination Kreis): model distances vs the centroid distance.

    ``realised_work_ext`` must carry ``home_ars5, dest_ars5, distance_km`` and hold ONLY workers
    with an external destination. ``kreis_centroids`` must carry ``ars5, centroid_x, centroid_y``
    in the same projected CRS the model distances were computed in (metres); ``ba_flows`` is
    ``braunschweig.data.census.pendler``'s ``orig_ars, dest_ars, flow``.

    ``centroid_km`` is the straight-line Kreis-centroid-to-Kreis-centroid distance multiplied by
    the SAME ``detour_factor`` as the model distances, so ``class_centroid`` and
    ``class_model_median`` are computed on comparable quantities. A pair whose home or
    destination Kreis has no centroid keeps ``NaN``/``<NA>`` (counted, never approximated), and
    a pair with no BA flow keeps a ``NaN`` ``ba_flow`` -- the BA export suppresses small cells,
    so an absent flow is genuinely unknown, not zero.
    """
    _require_columns(realised_work_ext, ("home_ars5", "dest_ars5", "distance_km"),
                     "the external-destination worker frame")
    _require_columns(kreis_centroids, ("ars5", "centroid_x", "centroid_y"),
                     "the Kreis centroid frame")
    _require_columns(ba_flows, ("orig_ars", "dest_ars", "flow"),
                     "braunschweig.data.census.pendler")

    if len(realised_work_ext) == 0:
        LOGGER.warning("%s no worker has an external destination; the EXT distance table is "
                       "empty (expected only if external workplaces are disabled)", _LOG_TAG)
        if stats is not None:
            stats.update(n_ext_workers=0, n_pairs=0, n_pairs_without_centroid=0,
                         n_pairs_without_ba_flow=0)
        return pd.DataFrame(columns=list(EXT_DISTANCE_COLUMNS))

    grouped = realised_work_ext.groupby(["home_ars5", "dest_ars5"], as_index=False).agg(
        n_model=("distance_km", "size"),
        model_km_median=("distance_km", "median"),
        model_km_p10=("distance_km", lambda values: values.quantile(0.10)),
        model_km_p90=("distance_km", lambda values: values.quantile(0.90)),
    )

    centroids = kreis_centroids[["ars5", "centroid_x", "centroid_y"]].drop_duplicates("ars5")
    grouped = grouped.merge(centroids.rename(columns={
        "ars5": "home_ars5", "centroid_x": "home_x", "centroid_y": "home_y"}),
        on="home_ars5", how="left", validate="m:1")
    grouped = grouped.merge(centroids.rename(columns={
        "ars5": "dest_ars5", "centroid_x": "dest_x", "centroid_y": "dest_y"}),
        on="dest_ars5", how="left", validate="m:1")

    grouped["centroid_km"] = np.hypot(grouped["dest_x"] - grouped["home_x"],
                                      grouped["dest_y"] - grouped["home_y"]) / 1000.0 \
        * float(detour_factor)
    n_pairs_without_centroid = int(grouped["centroid_km"].isna().sum())
    if n_pairs_without_centroid:
        LOGGER.warning(
            "%s %d/%d (home, destination) Kreis pairs have no Kreis centroid on at least one "
            "side; their centroid distance and class stay NaN (never approximated) -- check the "
            "VG250 vg250_krs layer coverage",
            _LOG_TAG, n_pairs_without_centroid, len(grouped))

    grouped["class_model_median"] = [R.classify_commute_distance(km)
                                     for km in grouped["model_km_median"]]
    grouped["class_centroid"] = [R.classify_commute_distance(km) for km in grouped["centroid_km"]]
    grouped["same_class"] = [
        pd.NA if (model is None or centroid is None) else bool(model == centroid)
        for model, centroid in zip(grouped["class_model_median"], grouped["class_centroid"])
    ]

    flows = ba_flows[["orig_ars", "dest_ars", "flow"]].drop_duplicates(["orig_ars", "dest_ars"])
    grouped = grouped.merge(
        flows.rename(columns={"orig_ars": "home_ars5", "dest_ars": "dest_ars5",
                              "flow": "ba_flow"}),
        on=["home_ars5", "dest_ars5"], how="left", validate="m:1")
    grouped["ba_flow"] = pd.to_numeric(grouped["ba_flow"], errors="coerce")
    n_pairs_without_ba_flow = int(grouped["ba_flow"].isna().sum())
    LOGGER.info(
        "%s EXT destinations: %d workers over %d (home, destination) Kreis pairs; %d pair(s) "
        "have no BA Pendler flow (the BA export suppresses small cells, so the flow is unknown, "
        "not zero); %d pair(s) have no Kreis centroid",
        _LOG_TAG, int(len(realised_work_ext)), len(grouped), n_pairs_without_ba_flow,
        n_pairs_without_centroid)

    if stats is not None:
        stats.update(n_ext_workers=int(len(realised_work_ext)), n_pairs=int(len(grouped)),
                     n_pairs_without_centroid=n_pairs_without_centroid,
                     n_pairs_without_ba_flow=n_pairs_without_ba_flow)
    return grouped[list(EXT_DISTANCE_COLUMNS)]


def ext_class_agreement_share(ext_table):
    """Worker-weighted share of EXT workers whose model class equals the centroid class.

    Weighted by ``n_model`` (workers, not pairs) and computed over the pairs where BOTH classes
    exist; ``NaN`` when no such pair exists. Reported in ``summary.md`` as the headline of the
    external-destination check.
    """
    if len(ext_table) == 0:
        return float("nan")
    decidable = ext_table[ext_table["same_class"].notna()]
    total = float(decidable["n_model"].sum())
    if total <= 0:
        return float("nan")
    agreeing = float(decidable.loc[decidable["same_class"].astype(bool), "n_model"].sum())
    return agreeing / total


# --------------------------------------------------------------------------- outputs

def per_person_frame(realised_work):
    """Per-worker export (controller ruling R1) joined by Task 6 with the donor's MiD distance.

    Columns :data:`PER_PERSON_COLUMNS`; ``distance_class`` is renamed to
    ``assigned_distance_class`` so the file is self-describing next to a donor column.
    """
    _require_columns(realised_work,
                     ("person_id", "distance_class", "distance_km", "destination_is_external",
                      "home_ars5", "destination_ars5"), "the realised work frame")
    out = realised_work.rename(columns={"distance_class": "assigned_distance_class"})
    return out[list(PER_PERSON_COLUMNS)].reset_index(drop=True)


def _fmt(value, digits=3):
    """Format a number for the markdown report; missing renders as the explicit ``n/a``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _provenance_lines(provenance):
    parameters = (provenance or {}).get("parameters", {})
    if not parameters:
        return []
    lines = ["Parameters: " + ", ".join(f"{key}={value}" for key, value in parameters.items())]
    if (provenance or {}).get("generated_at"):
        lines.append(f"Generated at: {provenance['generated_at']}")
    lines.append("")
    return lines


def summary_markdown(participation, distance_classes, ext_table, near_edge_share,
                     provenance=None):
    """Headline numbers of the Phase A measurement (see the module docstring for the caveats)."""
    lines = ["# Commute day state -- Phase A measurement", ""] + _provenance_lines(provenance) + [
        "Measurement only: the model is compared to a committed reference, which is NOT a",
        "validation against observed behaviour and decides nothing.", "",
        "## Work participation of employed persons (model vs SrV 2023)", "",
        "Comparable quantity: share_work_trip. The model has no day state, so its",
        "share_no_work_trip corresponds to the SUM of the SrV home-office and neither shares.",
        "", "| code | n_employed | share_work_trip | SrV share_work_trip | delta (pp) | "
        "SrV share_home_office_day | SrV n |",
        "|---|---|---|---|---|---|---|"]
    for row in participation.itertuples(index=False):
        lines.append(
            f"| {row.code} | {int(row.n_employed)} | {_fmt(row.share_work_trip)} | "
            f"{_fmt(row.srv_share_work_trip)} | {_fmt(row.delta_work_trip_pp, 2)} | "
            f"{_fmt(row.srv_share_home_office_day)} | "
            f"{'n/a' if pd.isna(row.srv_n_persons) else int(row.srv_n_persons)} |")

    zgb_all = distance_classes[(distance_classes["code"] == ZGB_ROW_CODE)
                               & (distance_classes["scope"] == "all")]
    lines += ["", "## Assigned commute-distance classes (ZGB, all destinations)", "",
              "| distance_class | n_workers | share |", "|---|---|---|"]
    for row in zgb_all.itertuples(index=False):
        lines.append(f"| {row.distance_class} | {int(row.n_workers)} | {_fmt(row.share)} |")

    lines += ["", "## External destinations vs BA Kreis centroids", "",
              f"- Worker-weighted share of EXT workers whose model distance class equals the "
              f"Kreis-centroid distance class: {_fmt(ext_class_agreement_share(ext_table))}",
              f"- (home, destination) Kreis pairs measured: {len(ext_table)}",
              f"- EXT workers measured: "
              f"{int(ext_table['n_model'].sum()) if len(ext_table) else 0}",
              "", "## Class-edge fragility", "",
              f"- Share of workers whose distance lies within the configured tolerance of a "
              f"commute-distance class edge: {_fmt(near_edge_share)}"]
    return "\n".join(lines) + "\n"


def write_outputs(directory, participation, distance_classes, ext_table, per_person,
                  provenance=None, near_edge_share=float("nan")):
    """Write the Phase A report artifacts into ``directory``.

    Files: ``work_participation_by_kreis.csv`` (:data:`PARTICIPATION_COLUMNS`),
    ``assigned_distance_classes.csv`` (:data:`DISTANCE_CLASS_COLUMNS`; the destination split
    lives in the ``scope`` column, values ``all`` / ``external`` / ``internal``, NOT in extra
    columns, so the table stays long-format and directly groupable),
    ``ext_destination_distances.csv`` (:data:`EXT_DISTANCE_COLUMNS`),
    ``assigned_class_by_person.csv`` (:data:`PER_PERSON_COLUMNS`, controller ruling R1 -- Task 6
    joins it with the donor's MiD commute distance on the server), ``summary.md`` and
    ``provenance.json``. The CSVs carry no comment header so downstream code can read them with
    plain ``pandas.read_csv``; the parameter block lives in ``provenance.json`` and at the top
    of ``summary.md``.
    """
    provenance = provenance or {}
    os.makedirs(directory, exist_ok=True)
    participation.to_csv(os.path.join(directory, "work_participation_by_kreis.csv"), index=False)
    distance_classes.to_csv(os.path.join(directory, "assigned_distance_classes.csv"), index=False)
    ext_table.to_csv(os.path.join(directory, "ext_destination_distances.csv"), index=False)
    per_person.to_csv(os.path.join(directory, "assigned_class_by_person.csv"), index=False)
    with open(os.path.join(directory, "provenance.json"), "w", encoding="utf-8") as handle:
        json.dump(provenance, handle, indent=2, default=str)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(summary_markdown(participation, distance_classes, ext_table,
                                      near_edge_share, provenance))
    LOGGER.info("%s wrote 6 report files to %s", _LOG_TAG, directory)


# --------------------------------------------------------------------------- stage

def kreis_centroids_from_vg250(spatial, target_crs):
    """Centroids of ALL German Kreise (VG250 ``vg250_krs``), keyed by 5-digit ARS.

    The external destinations reach far beyond the ZGB, so this uses the FULL Kreis layer, not
    ``braunschweig.analysis.spatial.load_kreise`` (which is deliberately restricted to the eight
    ZGB Kreise). ``ars5`` is derived exactly as ``load_kreise`` does it -- the layer's ARS as a
    string, its first five digits -- with a ``zfill(5)`` first so a Kreis code that lost its
    leading zero on an integer round trip ("3101") still resolves to "03101". The geometries are
    dissolved by ``ars5`` (a no-op on a well-formed layer, a safety net on a multi-part one) and
    reprojected to ``target_crs`` BEFORE the centroid is taken, so the centroid is metric.
    """
    layer = spatial.load_vg250_layer("vg250_krs", strict=True)
    column = next((name for name in ("ARS", "AGS") if name in layer.columns), None)
    if column is None:
        raise ValueError(
            "The VG250 vg250_krs layer carries neither an 'ARS' nor an 'AGS' column (found: "
            f"{sorted(layer.columns)}); the Kreis key cannot be derived without guessing")
    layer = layer.copy()
    layer["ars5"] = layer[column].astype(str).str.strip().str.zfill(5).str[:5]
    invalid = ~layer["ars5"].str.fullmatch(r"\d{5}")
    if bool(invalid.any()):
        raise ValueError(
            f"{int(invalid.sum())}/{len(layer)} rows of the VG250 vg250_krs layer yield a "
            f"non-numeric 5-digit Kreis key from column '{column}' (e.g. "
            f"{layer.loc[invalid, 'ars5'].head(3).tolist()}); refusing to guess the key")
    dissolved = layer[["ars5", "geometry"]].dissolve(by="ars5", as_index=False).to_crs(target_crs)
    centroids = dissolved.geometry.centroid
    LOGGER.info("%s Kreis centroids: %d Kreise from VG250 vg250_krs (column '%s'), reprojected "
                "to %s", _LOG_TAG, len(dissolved), column, target_crs)
    return pd.DataFrame({"ars5": dissolved["ars5"].values,
                         "centroid_x": centroids.x.values,
                         "centroid_y": centroids.y.values})


def execute(context):
    from braunschweig.analysis import spatial  # VG250 access only at run time
    from braunschweig import provenance as provenance_module

    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, _df_education = context.stage("synthesis.population.spatial.primary.locations")
    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    df_work_locations = context.stage("braunschweig.locations.work")
    df_municipalities = context.stage("data.spatial.municipalities")
    df_ba_flows = context.stage("braunschweig.data.census.pendler")

    detour_factor = float(context.config(KEY_DETOUR))
    max_unmatched_home_share = float(context.config(KEY_MAX_UNMATCHED_HOME_SHARE))
    edge_tolerance_km = float(context.config(KEY_EDGE_TOLERANCE_KM))
    sampling_rate = float(context.config("sampling_rate"))
    data_path = context.config("data_path")
    out_dir = os.path.join(context.config("output_path"), context.config(KEY_SUBDIR))

    LOGGER.info(
        "%s parameters: detour_factor=%.3f, max_unmatched_home_share=%.3f, "
        "edge_tolerance_km=%.2f, sampling_rate=%.4f; writing to %s",
        _LOG_TAG, detour_factor, max_unmatched_home_share, edge_tolerance_km, sampling_rate,
        out_dir)

    srv_dir = os.path.join(data_path, "braunschweig", "srv")
    srv_table = load_srv_work_participation(srv_dir)

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    kreis_centroids = kreis_centroids_from_vg250(spatial, homes.crs)
    known_commune_ids = set(df_municipalities["commune_id"].astype(str)) \
        if "commune_id" in df_municipalities.columns else None

    participation_stats, work_stats, class_stats, ext_stats = {}, {}, {}, {}
    model_participation = work_participation_by_kreis(
        df_persons, df_trips, homes, max_unmatched_home_share=max_unmatched_home_share,
        stats=participation_stats)
    participation = compare_participation(model_participation, srv_table)

    realised = realised_work_frame(
        homes, df_work, df_work_locations, df_persons, detour_factor=detour_factor,
        max_unmatched_home_share=max_unmatched_home_share,
        known_commune_ids=known_commune_ids, stats=work_stats)

    distance_classes = assigned_distance_classes(realised, stats=class_stats)
    ext_workers = realised.loc[realised["destination_is_external"],
                               ["person_id", "home_ars5", "destination_ars5", "distance_km"]] \
        .rename(columns={"destination_ars5": "dest_ars5"})
    ext_table = ext_destination_distances(ext_workers, kreis_centroids, df_ba_flows,
                                          detour_factor=detour_factor, stats=ext_stats)
    near_edge_share = near_class_edge_share(realised["distance_km"], R.COMMUTE_CLASS_EDGES_KM,
                                            edge_tolerance_km)
    per_person = per_person_frame(realised)

    provenance = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": provenance_module.git_commit(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
        "parameters": {
            "detour_factor": detour_factor,
            "max_unmatched_home_share": max_unmatched_home_share,
            "edge_tolerance_km": edge_tolerance_km,
            "sampling_rate": sampling_rate,
            "output_subdir": context.config(KEY_SUBDIR),
        },
        "inputs": {
            "srv_work_participation": os.path.join(
                srv_dir, "srv2023_work_participation_by_kreis.csv"),
            "n_srv_rows": int(len(srv_table)),
            "vg250_layer": "vg250_krs (braunschweig.analysis.spatial.load_vg250_layer)",
            "n_kreis_centroids": int(len(kreis_centroids)),
            "n_ba_flow_pairs": int(len(df_ba_flows)),
            "stages": [
                "synthesis.population.enriched", "synthesis.population.trips",
                "synthesis.population.spatial.home.locations",
                "synthesis.population.spatial.primary.locations",
                "braunschweig.locations.work", "data.spatial.municipalities",
                "braunschweig.data.census.pendler",
            ],
        },
        "counts": {
            "participation": participation_stats,
            "work": work_stats,
            "distance_classes": class_stats,
            "external_destinations": ext_stats,
        },
        "results": {
            "near_class_edge_share": near_edge_share,
            "ext_class_agreement_share": ext_class_agreement_share(ext_table),
        },
    }
    write_outputs(out_dir, participation, distance_classes, ext_table, per_person, provenance,
                  near_edge_share)

    zgb_row = participation[participation["code"] == ZGB_ROW_CODE].iloc[0]
    LOGGER.info(
        "%s ZGB: %d employed persons, share_work_trip %s vs SrV %s (delta %s pp); "
        "near-class-edge share %s; EXT class agreement %s",
        _LOG_TAG, int(zgb_row["n_employed"]), _fmt(zgb_row["share_work_trip"]),
        _fmt(zgb_row["srv_share_work_trip"]), _fmt(zgb_row["delta_work_trip_pp"], 2),
        _fmt(near_edge_share), _fmt(ext_class_agreement_share(ext_table)))

    # The per-person frame is deliberately NOT returned: it is one row per worker (~300k on a
    # 100% run) and is already written to disk for Task 6; caching it in the synpp stage output
    # would bloat the cache for no consumer.
    return dict(participation=participation, distance_classes=distance_classes,
                ext_destinations=ext_table, near_class_edge_share=near_edge_share,
                counts=provenance["counts"])
