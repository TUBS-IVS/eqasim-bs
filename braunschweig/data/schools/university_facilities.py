"""Build university destination points for the education gravity model.

Two paths, chosen by ``braunschweig.students.flows_enabled`` (default ON, issue #140 Phase 4):

* OFF (``build_university_facilities``, unchanged): inside the ZGB region the per-Kreis enrollment
  (LSN, institution-level ``nds_hochschulen.csv``) is distributed across the Kreis's OSM university
  buildings by the chosen weight column (default: footprint area; with
  education_building_distribution=True: building potential from the building-potentials stage).
  Ostfalia is one row over three Kreise. Each surrounding institution becomes a single curated
  campus point (that branch is not affected by the weight_column parameter). Output:
  GeoDataFrame[location_id, capacity, geometry] in EPSG:25832.
* ON (``build_site_facilities``): one facility set per DESTINATION of the student
  origin-destination table T. The destinations, their enrollment and their points come from the
  committed tables of Phase 2 and Task 1 (``student_balance_by_site.csv``,
  ``student_balance_out_commuters.csv``, ``student_destination_points.csv``), never from
  ``nds_hochschulen.csv``. A local site distributes its enrollment over the OSM university
  buildings of its COMMUNE (8-digit AGS, ``site_ags8``) by the same weights as the OFF path
  (same-unit rule of ``pick_unit_weights``); a building gets its commune from its ``commune_id``
  (the 12-digit ARS of the municipality polygon it lies in, ``data.spatial.municipalities``)
  through ``ars_to_ags8``. A surrounding institution is one facility at its point with the
  out-commuters of the region as capacity (heads: the students of the region who study there).
  Output: GeoDataFrame[location_id, capacity, geometry, destination_id, scope, site_ags8] in
  EPSG:25832; ``location_id`` is ``uni_loc_<slug>_<n>`` (building n of a local site) or
  ``uni_sur_<slug>`` (``slug``: ASCII, lower case, runs of other characters as one ``_``).

ASSUMPTION: two sites in one commune (TU Braunschweig and the art academy, 03101000) both get ALL
university buildings of the commune, each with capacities by its own enrollment, because the
building data do not say which building belongs to whom. Their facility rows share geometries and
differ in ``destination_id`` and ``location_id``.

University buildings in a commune without a site, and buildings of weight 0, get no capacity: they
are left out, counted and logged as a rate (and written to ``university_facilities_report.json``).
A surrounding institution without an out-commuter of the region has no share in T (design B5): it
gets no facility, is counted and named, and ``check_destinations_covered`` accepts exactly those
destinations as missing. A destination of T without a facility otherwise, a local site whose
commune holds no university building and a local site without enrollment raise.
"""
from __future__ import annotations

import json
import math
import os
import re

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig import synpp_tokens
from braunschweig.data import building_potential_attach as _building_potential_attach
from braunschweig.data.bbsr import regiostar as _regiostar
from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.data.building_potential_attach import (
    log_unit_basis, pick_unit_weights,
)
from braunschweig.data.education import student_flows_inputs as _student_flows_inputs
from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136). ``execute`` imports
# ``attach_potential`` from the same module inside the function.
_HELPER_MODULES = (
    _building_potential_attach,
    _regiostar,
    _student_flows_inputs,
)
# The helpers only the ON path runs: validate() leaves them (and the deferred names below) out of the
# OFF token, so the OFF token stays the one of the code before the switch existed.
_FLOWS_ONLY_HELPER_MODULES = (
    _regiostar,
    _student_flows_inputs,
)
# The import closure of ``student_flows_inputs`` (the code the ON path runs through its loaders):
# the module is a leaf and imports only ``braunschweig.data.csv_header``
# (scripts/audit_synpp_helper_hash.py, ADR-0136). One name per line.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.csv_header",
)

CRS_METRIC = "EPSG:25832"

# Share of skipped units above which the rate line is prefixed WARNING.
FALLBACK_WARN_FRACTION = 0.05

# Ostfalia is one enrollment row but spans three ZGB campus communes; its students
# are distributed across these communes' OSM university buildings by area.
OSTFALIA_COMMUNES = ["03158", "03102", "03103"]

# ON path: the four committed tables, relative to ``data_path`` (keys of the stage; the defaults
# are the committed files of Phase 2 and Task 1).
KEY_OD_TABLE_PATH = "braunschweig.students.od_table_path"
KEY_SITE_BALANCE_PATH = "braunschweig.students.site_balance_path"
KEY_OUT_COMMUTER_BALANCE_PATH = "braunschweig.students.out_commuter_balance_path"
KEY_DESTINATION_POINTS_PATH = "braunschweig.students.destination_points_path"
DEFAULT_OD_TABLE_PATH = "braunschweig/schools/student_od_table.csv"
DEFAULT_SITE_BALANCE_PATH = "braunschweig/schools/student_balance_by_site.csv"
DEFAULT_OUT_COMMUTER_BALANCE_PATH = "braunschweig/schools/student_balance_out_commuters.csv"
DEFAULT_DESTINATION_POINTS_PATH = "braunschweig/schools/student_destination_points.csv"
_FLOWS_PATH_DEFAULTS = {
    KEY_OD_TABLE_PATH: DEFAULT_OD_TABLE_PATH,
    KEY_SITE_BALANCE_PATH: DEFAULT_SITE_BALANCE_PATH,
    KEY_OUT_COMMUTER_BALANCE_PATH: DEFAULT_OUT_COMMUTER_BALANCE_PATH,
    KEY_DESTINATION_POINTS_PATH: DEFAULT_DESTINATION_POINTS_PATH,
}
# ASSUMPTION: share of the OSM university buildings left out because their commune holds no site,
# above which the rate line is followed by a WARNING (no committed table fixes this value).
KEY_LEFT_OUT_WARN_SHARE = "braunschweig.students.university_left_out_warn_share"
DEFAULT_LEFT_OUT_WARN_SHARE = FALLBACK_WARN_FRACTION

REPORT_FILE = "university_facilities_report.json"
# Tolerance of the check that the placed capacities add up to the table sums (floating point only).
_SUM_REL_TOL = 1e-9
_SUM_ABS_TOL = 1e-6
_LOG_TAG = "[university_facilities]"
_AGS8_PATTERN = re.compile(r"\d{8}")
_SLUG_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def build_university_facilities(df_hochschulen, df_osm_uni, weight_column="weight",
                                area_weight_column=None):
    """Distribute local enrollment across OSM university buildings; add surrounding points.

    Parameters
    ----------
    df_hochschulen:
        Institution table with columns institution, scope, ars5, enrollment, lon, lat.
    df_osm_uni:
        OSM university buildings GeoDataFrame; must contain commune_id, geometry,
        and the column named by weight_column (for local-scope institutions only).
    weight_column:
        Column in df_osm_uni to use as the distribution weight for LOCAL institutions.
        Default "weight" (footprint area) reproduces the legacy behaviour
        byte-identically. Pass "potential" to distribute by building potential.
        The surrounding-institution branch (curated single points) is not affected.
    area_weight_column:
        Only with a potential ``weight_column`` (NaN where a building matched no
        footprint): same-unit rule, see ``pick_unit_weights``. A facility unit
        (the commune, or the Ostfalia commune group) uses the potentials only if
        every building has one > 0, else the area x floors weights of ALL its
        buildings. The unit counts are logged. None (default): ``weight_column``
        is used for every unit as is.
    """
    rows_id, rows_cap, rows_geom = [], [], []

    # --- surrounding: one point per institution (UNCHANGED) -------------------
    sur = df_hochschulen[df_hochschulen["scope"] == "surrounding"]
    if len(sur):
        pts = gpd.GeoSeries(
            [Point(lon, lat) for lon, lat in zip(sur["lon"], sur["lat"])],
            crs="EPSG:4326").to_crs(CRS_METRIC)
        for inst, cap, geom in zip(sur["institution"], sur["enrollment"], pts):
            rows_id.append("uni_sur_" + str(inst).replace(" ", "_"))
            rows_cap.append(float(cap))
            rows_geom.append(geom)

    # --- local: pool enrollment per commune, split across OSM buildings -------
    local = df_hochschulen[df_hochschulen["scope"] == "local"].copy()
    osm = df_osm_uni.to_crs(CRS_METRIC).copy()
    osm["ars5"] = osm["commune_id"].astype(str).str[:5]

    unit_basis = []  # one bool per distributed unit: True = on potentials
    unit_counts = [0, 0]  # [units with buildings, zero-weight units skipped]

    def _distribute(enrollment, communes):
        sub = osm[osm["ars5"].isin(communes)]
        if sub.empty:
            # No building in the communes: the enrollment is not placed (counted).
            unit_counts[0] += 1
            unit_counts[1] += 1
            return
        if area_weight_column is None:
            weights = sub[weight_column].to_numpy(float)
        else:
            weights, used_potential = pick_unit_weights(
                sub, weight_column, area_weight_column)
        total_area = weights.sum()
        unit_counts[0] += 1
        if total_area <= 0:
            # Zero-weight unit: its enrollment is not placed (counted, see print below).
            unit_counts[1] += 1
            return
        if area_weight_column is not None:
            unit_basis.append(used_potential)
        for w, geom in zip(weights, sub["geometry"]):
            rows_id.append("uni_loc_%d" % len(rows_id))
            rows_cap.append(float(enrollment) * float(w) / total_area)
            rows_geom.append(geom)

    pooled = {}
    for _, r in local.iterrows():
        if str(r["institution"]).startswith("Ostfalia"):
            continue
        pooled[r["ars5"]] = pooled.get(r["ars5"], 0.0) + float(r["enrollment"])
    for ars5, enr in pooled.items():
        _distribute(enr, [ars5])

    ost = local[local["institution"].astype(str).str.startswith("Ostfalia")]
    if len(ost):
        _distribute(float(ost["enrollment"].sum()), OSTFALIA_COMMUNES)

    if area_weight_column is not None:
        log_unit_basis("university", sum(unit_basis), len(unit_basis) - sum(unit_basis))
    skipped_fraction = unit_counts[1] / unit_counts[0] if unit_counts[0] else 0.0
    print("[university_facilities] %sunits with OSM buildings: placed %d/%d (%.1f%%), "
          "zero-weight or building-less units skipped (enrollment not placed) %d (%.1f%%)" % (
              "WARNING " if skipped_fraction > FALLBACK_WARN_FRACTION else "",
              unit_counts[0] - unit_counts[1], unit_counts[0],
              100.0 * (1.0 - skipped_fraction) if unit_counts[0] else 0.0,
              unit_counts[1], 100.0 * skipped_fraction))

    gdf = gpd.GeoDataFrame(
        {"location_id": rows_id, "capacity": rows_cap},
        geometry=rows_geom, crs=CRS_METRIC)
    return gdf[["location_id", "capacity", "geometry"]]


# --------------------------------------------------------------------------- ON path: per destination
def slug(destination_id) -> str:
    """ASCII facility-id part of a destination id: umlauts transliterated (ae, oe, ue, ss), lower
    case, every run of characters outside ``[a-z0-9]`` one ``_``, stripped of ``_``."""
    text = str(destination_id).lower().translate(_SLUG_TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _require_columns(frame, columns, what):
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError("%s %s: missing column(s) %s; found %s"
                         % (_LOG_TAG, what, missing, list(frame.columns)))


def _require_unique(frame, column, what):
    duplicated = sorted(set(frame.loc[frame[column].duplicated(), column].astype(str)))
    if duplicated:
        raise ValueError("%s %s: %s is not unique: %s" % (_LOG_TAG, what, column, duplicated))


def _require_non_negative(values, label):
    values = np.asarray(values, float)
    if not (np.isfinite(values) & (values >= 0)).all():
        raise ValueError("%s %s holds a non-finite or negative value" % (_LOG_TAG, label))


def build_site_facilities(sites, surrounding, points, buildings, communes,
                          weight_column="weight", area_weight_column=None,
                          warn_share=DEFAULT_LEFT_OUT_WARN_SHARE):
    """One facility set per destination of T (module docstring: ON path).

    Parameters
    ----------
    sites:
        ``student_balance_by_site`` rows (local sites): destination_id, enrollment_persons.
    surrounding:
        ``student_balance_out_commuters`` rows (surrounding institutions): destination_id,
        out_commuters_persons (heads).
    points:
        ``student_destination_points`` rows: destination_id, scope (``local`` / ``surrounding``),
        site_ags8, x, y (EPSG:25832). Exactly the destinations of ``sites`` and ``surrounding``.
    buildings:
        OSM university buildings, a GeoDataFrame with ``geometry`` and the columns named by
        ``weight_column`` / ``area_weight_column`` (same-unit rule, ``pick_unit_weights``).
    communes:
        Series aligned with ``buildings`` (same index): the 8-digit AGS of each building's commune;
        a missing value or a commune without a site leaves the building out.
    warn_share:
        Share of left-out buildings above which a WARNING follows the rate line.

    Returns ``GeoDataFrame[location_id, capacity, geometry, destination_id, scope, site_ags8]``
    with ``attrs["report"]`` holding the counts (also written by the stage).
    """
    _require_columns(sites, ["destination_id", "enrollment_persons"], "site balance")
    _require_columns(surrounding, ["destination_id", "out_commuters_persons"],
                     "out-commuter balance")
    _require_columns(points, ["destination_id", "scope", "site_ags8", "x", "y"],
                     "destination points")
    for frame, what in ((sites, "site balance"), (surrounding, "out-commuter balance"),
                        (points, "destination points")):
        _require_unique(frame, "destination_id", what)
    _require_non_negative(sites["enrollment_persons"], "site balance enrollment_persons")
    no_enrollment = sorted(sites.loc[sites["enrollment_persons"] <= 0, "destination_id"])
    if no_enrollment:
        raise ValueError("%s local site(s) without enrollment (a local site of T always has "
                         "students): %s" % (_LOG_TAG, no_enrollment))
    _require_non_negative(surrounding["out_commuters_persons"],
                          "out-commuter balance out_commuters_persons")
    if not communes.index.equals(buildings.index):
        raise ValueError("%s communes must be indexed like the buildings" % _LOG_TAG)

    # The destinations of the three tables are one set, and a slug names exactly one of them.
    scope_of = points.set_index("destination_id")["scope"]
    local_ids, surrounding_ids = list(sites["destination_id"]), list(surrounding["destination_id"])
    problems = []
    for ids, scope in ((local_ids, "local"), (surrounding_ids, "surrounding")):
        for destination in ids:
            if destination not in scope_of.index:
                problems.append("%s has no point" % destination)
            elif scope_of[destination] != scope:
                problems.append("%s is %s in the points table, expected %s"
                                % (destination, scope_of[destination], scope))
    for destination in scope_of.index:
        if destination not in set(local_ids) | set(surrounding_ids):
            problems.append("%s has a point but no enrollment / out-commuter row" % destination)
    if problems:
        raise ValueError("%s destination tables disagree: %s" % (_LOG_TAG, "; ".join(problems)))
    slugs = pd.Series({destination: slug(destination) for destination in scope_of.index})
    colliding = slugs[slugs.duplicated(keep=False)]
    if len(colliding):
        raise ValueError("%s two destinations share one slug (facility id part): %s"
                         % (_LOG_TAG, sorted(colliding.index)))

    point_rows = points.set_index("destination_id")
    buildings = buildings.to_crs(CRS_METRIC)
    positions_of = {}      # commune -> (row positions of its buildings, weights, on potentials)
    units_on_potentials = units_on_area = 0

    def _unit(commune, destination):
        nonlocal units_on_potentials, units_on_area
        if commune not in positions_of:
            positions = np.flatnonzero((communes == commune).to_numpy())
            if not len(positions):
                raise ValueError(
                    "%s local site %s: its commune %s holds no OSM university building "
                    "(no fallback to another commune)" % (_LOG_TAG, destination, commune))
            sub = buildings.iloc[positions]
            if area_weight_column is None:
                weights, on_potential = sub[weight_column].to_numpy(float), None
            else:
                weights, on_potential = pick_unit_weights(sub, weight_column, area_weight_column)
            if not weights.sum() > 0:
                raise ValueError("%s local site %s: the university buildings of commune %s have "
                                 "zero total weight" % (_LOG_TAG, destination, commune))
            positions_of[commune] = (positions, weights, on_potential)
            if on_potential is True:
                units_on_potentials += 1
            elif on_potential is False:
                units_on_area += 1
        return positions_of[commune]

    rows = []
    zero_weight_buildings = 0
    for destination, enrollment in zip(sites["destination_id"], sites["enrollment_persons"]):
        commune = str(point_rows.loc[destination, "site_ags8"])
        if not _AGS8_PATTERN.fullmatch(commune):
            raise ValueError("%s local site %s: site_ags8 %r is not an 8-digit AGS"
                             % (_LOG_TAG, destination, commune))
        positions, weights, _ = _unit(commune, destination)
        total = float(weights.sum())
        for n, (position, weight) in enumerate(zip(positions, weights)):
            if not weight > 0:
                # A building of weight 0 gets no capacity (counted; the site keeps its enrollment).
                zero_weight_buildings += 1
                continue
            rows.append(("uni_loc_%s_%d" % (slugs[destination], n),
                         float(enrollment) * float(weight) / total,
                         buildings.geometry.iloc[position], destination, "local", commune))
    zero_share = []
    for destination, out_commuters in zip(surrounding["destination_id"],
                                          surrounding["out_commuters_persons"]):
        if not out_commuters > 0:
            # No student of the region attends it: no share in T, so no facility (design B5).
            zero_share.append(destination)
            continue
        row = point_rows.loc[destination]
        site_ags8 = row["site_ags8"]
        rows.append(("uni_sur_%s" % slugs[destination], float(out_commuters),
                     Point(float(row["x"]), float(row["y"])), destination, "surrounding",
                     "" if pd.isna(site_ags8) else str(site_ags8)))

    columns = ["location_id", "capacity", "geometry", "destination_id", "scope", "site_ags8"]
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows, columns=columns), geometry="geometry",
                           crs=CRS_METRIC)[columns]
    if gdf["location_id"].duplicated().any():
        raise ValueError("%s location_id is not unique: %s"
                         % (_LOG_TAG, sorted(set(gdf.loc[gdf["location_id"].duplicated(),
                                                         "location_id"]))))

    # Buildings in a commune without a site get no capacity: counted, logged, reported.
    site_communes = set(point_rows.loc[local_ids, "site_ags8"].astype(str))
    in_site_commune = communes.isin(site_communes)
    total_buildings = len(buildings)
    left_out = int((~in_site_commune).sum())
    left_out_share = left_out / total_buildings if total_buildings else 0.0
    report = {
        "buildings_total": total_buildings,
        "buildings_in_site_communes": total_buildings - left_out,
        "buildings_left_out": left_out,
        "left_out_communes": sorted(set(communes[~in_site_commune].dropna().astype(str))),
        "local_sites": len(local_ids),
        "local_sites_placed": len(local_ids),
        "buildings_zero_weight_left_out": zero_weight_buildings,
        "surrounding_institutions": len(surrounding_ids),
        "surrounding_facilities": len(surrounding_ids) - len(zero_share),
        "surrounding_left_out_zero_share": len(zero_share),
        "surrounding_zero_share_destinations": sorted(zero_share),
        "units_on_potentials": units_on_potentials,
        "units_on_area_weights": units_on_area,
        "left_out_warn_share": float(warn_share),
    }
    if area_weight_column is not None:
        log_unit_basis("university", units_on_potentials, units_on_area)
    print("%s university buildings in a site commune %d/%d (%.1f%%), left out (no capacity) %d "
          "(%.1f%%); local sites placed %d/%d, surrounding facilities %d"
          % (_LOG_TAG, total_buildings - left_out, total_buildings,
             100.0 * (1.0 - left_out_share) if total_buildings else 0.0, left_out,
             100.0 * left_out_share, len(local_ids), len(local_ids),
             report["surrounding_facilities"]))
    n_surrounding = len(surrounding_ids)
    print("%s surrounding institutions with a share: placed %d/%d (%.1f%%), left out (no student "
          "of the region, no share in T) %d (%.1f%%): %s; buildings of weight 0 left out %d"
          % (_LOG_TAG, n_surrounding - len(zero_share), n_surrounding,
             100.0 * (1.0 - len(zero_share) / n_surrounding) if n_surrounding else 0.0,
             len(zero_share), 100.0 * len(zero_share) / n_surrounding if n_surrounding else 0.0,
             report["surrounding_zero_share_destinations"], zero_weight_buildings))
    if n_surrounding and len(zero_share) == n_surrounding:
        print("%s WARNING: every surrounding institution is left out (no out-commuter of the "
              "region): the out-commuter table is probably empty or mismatched" % _LOG_TAG)
    if left_out_share > warn_share:
        print("%s WARNING: %.1f%% of the OSM university buildings lie in a commune without a "
              "site (> %.1f%%): communes %s"
              % (_LOG_TAG, 100.0 * left_out_share, 100.0 * warn_share,
                 report["left_out_communes"]))
    gdf.attrs["report"] = report
    return gdf


def check_destinations_covered(facilities, od, zero_share_destinations=()):
    """Raise unless every destination of the table T has a facility row.

    Exactly the counted zero-share destinations (``zero_share_destinations``: surrounding
    institutions without an out-commuter of the region, left out by ``build_site_facilities``) may
    lack one; any other missing destination raises.
    """
    missing = sorted(set(od["destination_id"]) - set(facilities["destination_id"])
                     - set(zero_share_destinations))
    if missing:
        raise ValueError("%s destination(s) of the table T without a facility: %s"
                         % (_LOG_TAG, missing))


def _read_table(path, what, **dtype):
    """A committed table with ``#`` header lines, utf-8; a missing file raises."""
    if not os.path.isfile(path):
        raise RuntimeError("%s %s not found: %s" % (_LOG_TAG, what, path))
    return pd.read_csv(path, comment="#", encoding="utf-8", dtype=dtype or None)


def _flows_paths(context):
    data_path = context.config("data_path")
    return {key: os.path.join(data_path, context.config(key)) for key in _FLOWS_PATH_DEFAULTS}


def _university_buildings(context):
    """The OSM university buildings with the weight columns of the chosen distribution."""
    df_osm = context.stage("eqasim_common.locations.education")
    df_osm = df_osm[df_osm["education_type"] == "university"].copy()

    enabled = context.config("education_building_distribution")
    weight_column = "weight"
    area_weight_column = None
    if enabled:
        from braunschweig.data.building_potential_attach import attach_potential
        df_b = context.stage("braunschweig.data.building_potentials")
        # Unmatched buildings stay NaN (not the m2 x floors weight): the builder
        # applies the same-unit rule so a unit never mixes places and m2 x floors.
        vals, _p, _f = attach_potential(
            df_osm, df_b, "potential_university",
            fallback=np.full(len(df_osm), np.nan), label="university")
        df_osm = df_osm.copy()
        df_osm["potential"] = vals
        weight_column = "potential"
        area_weight_column = "weight"
    return df_osm, weight_column, area_weight_column


def _building_communes(df_osm, municipalities):
    """8-digit AGS of each building: ``commune_id`` is the municipality it lies in (12-digit ARS of
    ``data.spatial.municipalities``), converted by ``ars_to_ags8``. A commune that is not a
    municipality of the declared stage, or a code that is no 8-digit AGS afterwards, raises."""
    known = set(municipalities["commune_id"].astype(str))
    ars = df_osm["commune_id"].astype(str)
    unknown = sorted(set(ars) - known)
    if unknown:
        raise ValueError("%s building commune_id(s) not in data.spatial.municipalities: %s"
                         % (_LOG_TAG, unknown[:10]))
    communes = ars.map(ars_to_ags8)
    bad = sorted(set(communes[~communes.str.fullmatch(_AGS8_PATTERN.pattern)]))
    if bad:
        raise ValueError("%s building commune(s) that are no 8-digit AGS after ars_to_ags8: %s"
                         % (_LOG_TAG, bad[:10]))
    return communes


def _execute_flows(context):
    paths = _flows_paths(context)
    od = _student_flows_inputs.load_od_table(paths[KEY_OD_TABLE_PATH])
    sites = _read_table(paths[KEY_SITE_BALANCE_PATH], "site balance", site_kreis_ars5=str)
    surrounding = _read_table(paths[KEY_OUT_COMMUTER_BALANCE_PATH], "out-commuter balance",
                              site_kreis_ars5=str)
    points = _read_table(paths[KEY_DESTINATION_POINTS_PATH], "destination points",
                         site_ags8=str)
    df_osm, weight_column, area_weight_column = _university_buildings(context)
    communes = _building_communes(df_osm, context.stage("data.spatial.municipalities"))

    gdf = build_site_facilities(
        sites, surrounding, points, df_osm, communes, weight_column=weight_column,
        area_weight_column=area_weight_column,
        warn_share=float(context.config(KEY_LEFT_OUT_WARN_SHARE)))
    report = dict(gdf.attrs["report"])
    check_destinations_covered(gdf, od, report["surrounding_zero_share_destinations"])
    report["destinations_of_t"] = int(od["destination_id"].nunique())
    report["capacity_sum_local"] = float(gdf.loc[gdf["scope"] == "local", "capacity"].sum())
    report["enrollment_sum_local"] = float(sites["enrollment_persons"].sum())
    report["out_commuter_sum_surrounding"] = float(surrounding["out_commuters_persons"].sum())
    report["capacity_sum_surrounding"] = float(
        gdf.loc[gdf["scope"] == "surrounding", "capacity"].sum())
    for placed, expected, what in (
            (report["capacity_sum_local"], report["enrollment_sum_local"], "local capacity"),
            (report["capacity_sum_surrounding"], report["out_commuter_sum_surrounding"],
             "surrounding capacity")):
        if not math.isclose(placed, expected, rel_tol=_SUM_REL_TOL, abs_tol=_SUM_ABS_TOL):
            raise ValueError("%s %s sum %.6f differs from the table sum %.6f (tolerance rel %g, "
                             "abs %g): students would be lost or created"
                             % (_LOG_TAG, what, placed, expected, _SUM_REL_TOL, _SUM_ABS_TOL))
    with open(os.path.join(context.path(), REPORT_FILE), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print("%s %d facilities for %d destinations of T; capacity sum local %.0f of enrollment %.0f"
          % (_LOG_TAG, len(gdf), report["destinations_of_t"], report["capacity_sum_local"],
             report["enrollment_sum_local"]))
    return gdf


def configure(context):
    context.config("data_path")
    context.config("nds_hochschulen_path", "braunschweig/schools/nds_hochschulen.csv")
    context.stage("eqasim_common.locations.education")
    enabled = context.config("education_building_distribution", True)
    if enabled:
        context.stage("braunschweig.data.building_potentials")
    if context.config(KEY_FLOWS_ENABLED, True):
        # ON path only: the committed tables (hashed in validate()) and the municipalities its
        # building communes are checked against; OFF keeps today's inputs.
        for key, default in _FLOWS_PATH_DEFAULTS.items():
            context.config(key, default)
        context.config(KEY_LEFT_OUT_WARN_SHARE, DEFAULT_LEFT_OUT_WARN_SHARE)
        context.stage("data.spatial.municipalities")


def execute(context):
    if context.config(KEY_FLOWS_ENABLED):
        return _execute_flows(context)
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    df_h = pd.read_csv(path, dtype={"ars5": str})
    df_osm, weight_column, area_weight_column = _university_buildings(context)

    gdf = build_university_facilities(df_h, df_osm, weight_column=weight_column,
                                      area_weight_column=area_weight_column)
    # Guard against silent enrollment loss: a local institution whose commune has
    # no OSM university building is dropped by the area-distribution (its students
    # would be redistributed to the nearest surviving campus). Name any such
    # institution explicitly so the calibration is not trusted on incomplete data.
    osm_ars5 = set(df_osm["commune_id"].astype(str).str[:5])
    dropped = []
    for _, r in df_h[df_h["scope"] == "local"].iterrows():
        communes = (OSTFALIA_COMMUNES
                    if str(r["institution"]).startswith("Ostfalia")
                    else [str(r["ars5"])])
        if not (set(communes) & osm_ars5):
            dropped.append("%s (commune(s) %s)" % (r["institution"], communes))
    if dropped:
        print("[braunschweig.data.schools.university_facilities] WARNING: no OSM "
              "university buildings for local institution(s): %s -- their students "
              "are redistributed to the nearest campus." % "; ".join(dropped))
    placed = float(gdf["capacity"].sum())
    expected = float(df_h["enrollment"].sum())
    print("[braunschweig.data.schools.university_facilities] %d university points; "
          "capacity sum %.0f of %.0f" % (len(gdf), placed, expected))
    return gdf


def validate(context):
    flows_enabled = bool(context.config(KEY_FLOWS_ENABLED))
    # OFF: the helpers the OFF path runs and nothing else, the token of the code before the switch.
    helper_modules = _HELPER_MODULES if flows_enabled else tuple(
        module for module in _HELPER_MODULES if module not in _FLOWS_ONLY_HELPER_MODULES)
    deferred_names = _DEFERRED_HELPER_MODULE_NAMES if flows_enabled else ()
    helpers = synpp_tokens.source_token(
        helper_modules, deferred_names, stage_label=__name__ + ".validate()")
    if flows_enabled:
        # ON path: the four committed tables it reads (CRLF-neutral digests, as git stores them),
        # not nds_hochschulen.csv; a missing table stops the run at DAG time (ADR-0194).
        paths = _flows_paths(context)
        files = {}
        for key, path in paths.items():
            if not os.path.isfile(path):
                raise RuntimeError("%s %s not found: %s" % (_LOG_TAG, key, path))
            files[key] = synpp_tokens.text_file_sha256(path)
        return {"flows_files": files, "helpers": helpers}
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    if not os.path.exists(path):
        raise RuntimeError("nds_hochschulen.csv missing: run scripts/seed_nds_hochschulen.py")
    return {
        "nds_hochschulen_csv_size": os.path.getsize(path),
        "helpers": helpers,
    }
