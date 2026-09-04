"""synpp stage: realised home->work / home->education distances per home Kreis vs SrV 2023.

Mirrors eqasim's ``analysis/synthesis/commute_distance`` (realised euclidean distance
per person, quantile curve) and adds what this project needs for the pre-registered
calibration decision (spec 2026-09-03, Section 5): per-Kreis band shares against the
committed SrV targets, EMD, the SrV bootstrap noise floor, an intra/inter-Gemeinde
split for work (attributes a gap to the OD layer or the per-person-target layer), and
education by the model's age levels. Compares in ROUTED km: euclidean * detour factor.

Outputs go under ``<output_path>/analysis/srv_distance_validation/``. This stage reads
cached synthesis stages only (no MATSim), so it runs in minutes on a cached 100% run.

KNOWN QUIRK (n_model off-by-one, disclosed rather than silently left to be rediscovered):
the ZGB aggregate row's ``n_model`` counts every scope-matching person in the realised
work/education frame, INCLUDING persons whose home Kreis (``ars5``) could not be resolved
(:func:`_check_home_match_rate` logs and bounds that rate, but does not drop the rows);
the 8 per-Kreis rows filter on ``ars5 == code`` and therefore can never include those
persons. On the committed 100% baseline run this affects exactly one worker, so
``sum(kreis n_model) == aggregate n_model - 1`` for the work "all" scope; this is expected
given the home-match-rate contract above, not a join bug, and is repeated as a footer
line in ``summary.md`` so a reader does not mistake the discrepancy for a defect.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.calibration import decision as D
from braunschweig.calibration import srv_distance_targets as T

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.commute_distance_by_kreis")

KEY_DETOUR = "srv_distance_detour_factor"
KEY_EMD_THRESHOLD = "srv_distance_emd_threshold"
KEY_MIN_PERSONS = "srv_distance_min_persons"
KEY_AGGREGATE_REQUIRES_MIN_PERSONS = "srv_distance_aggregate_requires_min_persons"
KEY_SUBDIR = "srv_distance_output_subdir"
KEY_MAX_UNMATCHED_HOME_SHARE = "srv_distance_max_unmatched_home_share"
KEY_WARN_UNMATCHED_DESTINATION_SHARE = "srv_distance_warn_unmatched_destination_share"
# Minor: a plain literal -- os.path.join(output_path, DEFAULT_SUBDIR) happens once, at use.
DEFAULT_SUBDIR = "analysis/srv_distance_validation"
EQASIM_CDF_PROBABILITIES = np.linspace(0.0, 1.0, 20)
ZGB_CODES = tuple(T.ZGB_KREISE)

# R17: a high unmatched-home-Kreis/Gemeinde rate almost always signals a VG250/RegioStaR
# join bug rather than genuinely home-less persons; above this share the intra/inter (work)
# or per-Kreis (education) comparison would be built on a meaningless home assignment for
# too large a share of the cohort, so the stage fails loudly instead of running on.
DEFAULT_MAX_UNMATCHED_HOME_SHARE = 0.05
# R17(c): destinations outside every Gemeinde polygon are counted as inter-Gemeinde (a
# conservative default, see realised_work_frame), but above this share it more likely means
# the Gemeinde coverage or destination CRS is wrong than that genuinely many workers commute
# outside the ZGB -- a warning, not a raise, because it is scientifically plausible in a
# cordon-adjacent model for a real (if unusual) share of destinations to fall outside ZGB.
DEFAULT_WARN_UNMATCHED_DESTINATION_SHARE = 0.30


def configure(context):
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("synthesis.population.enriched")
    context.stage("braunschweig.analysis.reference.srv.commute_distance")
    context.config("output_path")
    context.config("sampling_rate")
    context.config(KEY_DETOUR, T.DEFAULT_DETOUR_FACTOR)
    context.config(KEY_EMD_THRESHOLD, D.DEFAULT_EMD_THRESHOLD)
    context.config(KEY_MIN_PERSONS, D.DEFAULT_MIN_PERSONS)
    context.config(KEY_AGGREGATE_REQUIRES_MIN_PERSONS, D.DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS)
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_MAX_UNMATCHED_HOME_SHARE, DEFAULT_MAX_UNMATCHED_HOME_SHARE)
    context.config(KEY_WARN_UNMATCHED_DESTINATION_SHARE, DEFAULT_WARN_UNMATCHED_DESTINATION_SHARE)


# --------------------------------------------------------------------------- pure helpers

def _home_per_person(df_home_geo, df_persons):
    """person_id -> home geometry, ars5, home commune_id (one row per person).

    R17(a): deduplicates ``df_home_geo`` by ``household_id`` before the merge and logs how
    many duplicate rows were removed. ``assign_geographies`` does not dedupe its Kreis
    sjoin, so a home point that sits exactly on a Kreis/Gemeinde boundary can legitimately
    produce more than one row for the same ``household_id``; left unhandled, that would
    silently fan a person out into two rows downstream. The persons->homes merge uses
    ``validate="m:1"`` so a residual duplicate (or a duplicate household_id further
    upstream) fails loudly instead of silently duplicating persons.
    """
    homes = df_home_geo[["household_id", "geometry", "ars5", "commune_id"]].rename(
        columns={"geometry": "home_geometry", "commune_id": "home_commune_id"})
    n_before = len(homes)
    homes = homes.drop_duplicates("household_id", keep="first")
    n_duplicates = n_before - len(homes)
    if n_duplicates:
        LOGGER.warning(
            "[srv_distance] %d duplicate household_id row(s) in the home-geography frame "
            "removed (keeping the first); assign_geographies' Kreis sjoin can produce more "
            "than one row per household when a home sits on a Kreis/Gemeinde boundary",
            n_duplicates)
    return df_persons[["person_id", "household_id", "age"]].merge(
        homes, on="household_id", how="left", validate="m:1")


def _check_home_match_rate(frame, cohort_label, meaninglessness_clause, max_unmatched_home_share):
    """Raise if too many persons in ``frame`` have no home Kreis (``ars5``) or Gemeinde
    (``home_commune_id``) match; return the home-commune-specific missing count.

    R17(b): a high unmatched-home rate almost always signals a VG250/RegioStaR join bug
    (stale archive, CRS mismatch, ...) rather than genuinely home-less persons and would
    make the downstream comparison meaningless for a large share of the cohort -- per
    CLAUDE.md's fallback-transparency rule this must never pass silently.
    ``n_home_commune_missing`` is the (possibly zero) count of persons whose
    ``home_commune_id`` specifically is missing; those persons' ``intra_gemeinde`` is False
    by construction (a missing home commune can never equal a known destination commune),
    which this count makes explicit and auditable rather than an unremarked side effect.
    """
    n = len(frame)
    missing_ars5 = frame["ars5"].isna()
    missing_commune = frame["home_commune_id"].isna()
    n_unmatched = int((missing_ars5 | missing_commune).sum())
    n_home_commune_missing = int(missing_commune.sum())
    rate = n_unmatched / n if n else 0.0
    LOGGER.info(
        "[srv_distance] %s: %d/%d (%.2f%%) with no home Kreis/Gemeinde match (%d with a "
        "missing home Gemeinde specifically)",
        cohort_label, n_unmatched, n, 100.0 * rate, n_home_commune_missing)
    if rate > max_unmatched_home_share:
        raise ValueError(
            f"{n_unmatched}/{n} ({100.0 * rate:.1f}%) {cohort_label} have no home Kreis/Gemeinde "
            f"match; exceeds max_unmatched_home_share={max_unmatched_home_share} (config key "
            f"{KEY_MAX_UNMATCHED_HOME_SHARE}); {meaninglessness_clause} -- check VG250 / "
            f"RegioStaR inputs")
    return n_home_commune_missing


def _check_no_home_geometry_rate(n_no_home, n_before_filter, cohort_label, max_unmatched_home_share):
    """Raise if too many persons in ``cohort_label`` have no home geometry at all after the
    person->home merge (household without a resolved home point).

    Before this guard was added, the no-home-geometry drop in :func:`realised_work_frame` /
    :func:`realised_education_frame` was the only UNGUARDED drop path in this module: a
    household without a home geometry almost always signals a broken
    ``synthesis.population.spatial.home.locations`` / ``household_id`` join rather than a
    genuinely home-less household, and dropping it silently would make the downstream
    comparison meaningless for a large share of the cohort -- per CLAUDE.md's fallback-
    transparency rule this must never pass silently (mirrors :func:`_check_home_match_rate`,
    which guards the Kreis/Gemeinde match on the SURVIVING rows one step later).
    """
    rate = n_no_home / n_before_filter if n_before_filter else 0.0
    LOGGER.info(
        "[srv_distance] %s: %d/%d (%.2f%%) with no home geometry after the person->home merge",
        cohort_label, n_no_home, n_before_filter, 100.0 * rate)
    if rate > max_unmatched_home_share:
        raise ValueError(
            f"{n_no_home}/{n_before_filter} ({100.0 * rate:.1f}%) {cohort_label} have no home "
            f"geometry after the person->home merge; exceeds max_unmatched_home_share="
            f"{max_unmatched_home_share} (config key {KEY_MAX_UNMATCHED_HOME_SHARE}) -- check "
            f"the synthesis.population.spatial.home.locations / household_id join")


def realised_work_frame(df_home_geo, df_work, df_persons, gemeinden,
                        max_unmatched_home_share=DEFAULT_MAX_UNMATCHED_HOME_SHARE,
                        warn_unmatched_destination_share=DEFAULT_WARN_UNMATCHED_DESTINATION_SHARE,
                        stats=None):
    """Per worker: home Kreis, home and destination Gemeinde, euclidean km, intra flag.

    ``gemeinden`` (GeoDataFrame commune_id, geometry) resolves the DESTINATION Gemeinde
    by point-in-polygon, so home and destination share one key universe; destinations
    outside every polygon (outside ZGB) count as inter-Gemeinde and are logged (warned
    when their rate exceeds ``warn_unmatched_destination_share``).

    R18: ``df_home_geo``, ``df_work`` and ``gemeinden`` must carry the same, non-None CRS
    (checked here explicitly rather than silently relabelling geometries to
    ``gemeinden.crs``, which would keep running with a scientifically meaningless distance
    if the inputs actually differed).

    Per CLAUDE.md "Fallback transparency": persons without a home geometry are dropped and
    counted (``n_no_home``), and this is now a GUARDED drop -- above ``max_unmatched_home_share``
    it raises (see ``_check_no_home_geometry_rate``), because a high rate almost always signals
    a broken home-locations/household_id join rather than genuinely home-less households; R17(b)
    raises if too many of the SURVIVING persons have no home Kreis/Gemeinde match at all; persons
    whose resulting euclidean distance is NaN (e.g. a missing destination geometry) are dropped
    and counted (``n_nan_distance``) -- BEFORE any band-
    share computation -- because ``srv_distance_targets.weighted_band_shares`` raises on a
    NaN distance rather than silently absorbing it into a band. ``stats``, if given a dict,
    is filled with these diagnostic counts (unfiltered by the class boundary above) so
    ``execute`` can record them in ``provenance.json`` without re-deriving them from the
    (already-filtered) returned frame.
    """
    home_crs, dest_crs, gem_crs = df_home_geo.crs, df_work.crs, gemeinden.crs
    if home_crs is None or dest_crs is None or gem_crs is None or home_crs != gem_crs or dest_crs != gem_crs:
        raise ValueError(
            f"CRS mismatch building the work frame: home={home_crs}, work destinations={dest_crs}, "
            f"gemeinden={gem_crs} -- all three must match; reproject upstream")

    per_person = _home_per_person(df_home_geo, df_persons)
    work = df_work[["person_id", "geometry"]].rename(columns={"geometry": "dest_geometry"})
    frame = work.merge(per_person, on="person_id", how="left")
    n_before_home_filter = len(frame)
    n_no_home = int(frame["home_geometry"].isna().sum())
    _check_no_home_geometry_rate(n_no_home, n_before_home_filter, "workers", max_unmatched_home_share)
    frame = frame[frame["home_geometry"].notna()].copy()

    n_home_commune_missing = _check_home_match_rate(
        frame, "workers", "the intra/inter split would be meaningless", max_unmatched_home_share)
    # R3 (bug fix): n_input is captured ONCE, right after the home-geometry filter, and is
    # the single denominator for every rate logged below -- reusing a frame length that had
    # already been partially filtered by a LATER criterion (the original bug) silently mixes
    # numerator/denominator lineages and produces a wrong rate.
    n_input = len(frame)

    dest = gpd.GeoDataFrame(frame[["person_id"]], geometry=frame["dest_geometry"].values, crs=dest_crs)
    joined = gpd.sjoin(dest, gemeinden[["commune_id", "geometry"]], how="left", predicate="within")
    joined = joined.drop_duplicates("person_id")
    frame = frame.merge(joined[["person_id", "commune_id"]].rename(columns={"commune_id": "dest_commune_id"}),
                        on="person_id", how="left")
    n_dest_outside = int(frame["dest_commune_id"].isna().sum())
    dest_outside_rate = n_dest_outside / n_input if n_input else 0.0

    home = gpd.GeoSeries(frame["home_geometry"].values, crs=home_crs)
    dest_geo = gpd.GeoSeries(frame["dest_geometry"].values, crs=dest_crs)
    frame["distance_km_euclid"] = home.distance(dest_geo).values / 1000.0
    n_nan_distance = int(frame["distance_km_euclid"].isna().sum())
    nan_distance_rate = n_nan_distance / n_input if n_input else 0.0
    frame["intra_gemeinde"] = (frame["dest_commune_id"].notna()
                               & (frame["dest_commune_id"] == frame["home_commune_id"]))

    LOGGER.info(
        "[srv_distance] work: %d persons input; %d without home geometry dropped; %d "
        "destinations outside every Gemeinde polygon (%.1f%% of input, counted inter-"
        "Gemeinde); %d with a NaN distance (%.1f%% of input) dropped; %d with a missing "
        "home Gemeinde",
        n_input, n_no_home, n_dest_outside, 100.0 * dest_outside_rate, n_nan_distance,
        100.0 * nan_distance_rate, n_home_commune_missing)
    if dest_outside_rate > warn_unmatched_destination_share:
        LOGGER.warning(
            "[srv_distance] work: %.1f%% of destinations fall outside every Gemeinde polygon "
            "(threshold %.1f%%) -- check the VG250 Gemeinde coverage or the destination CRS "
            "before trusting the intra/inter split",
            100.0 * dest_outside_rate, 100.0 * warn_unmatched_destination_share)
    if n_nan_distance:
        LOGGER.warning(
            "[srv_distance] work: %d/%d persons (%.1f%%) had a NaN euclidean distance and "
            "were dropped before banding -- check for missing home/destination geometries",
            n_nan_distance, n_input, 100.0 * nan_distance_rate)

    frame = frame[frame["distance_km_euclid"].notna()]
    if stats is not None:
        stats.update(n_workers=len(frame), n_no_home=n_no_home,
                    n_home_commune_missing=n_home_commune_missing,
                    n_dest_outside=n_dest_outside, n_nan_distance=n_nan_distance)
    return frame[["person_id", "ars5", "home_commune_id", "dest_commune_id",
                  "distance_km_euclid", "intra_gemeinde"]].reset_index(drop=True)


def realised_education_frame(df_home_geo, df_education, df_persons,
                             max_unmatched_home_share=DEFAULT_MAX_UNMATCHED_HOME_SHARE,
                             stats=None):
    """Per pupil/student: home Kreis, model age level, euclidean km.

    R18: ``df_home_geo`` and ``df_education`` must carry the same, non-None CRS.

    Per CLAUDE.md "Fallback transparency": persons without a home geometry are dropped and
    counted, and this is a GUARDED drop -- above ``max_unmatched_home_share`` it raises (see
    ``_check_no_home_geometry_rate``); R17(b) raises if too many of the SURVIVING persons have
    no home Kreis/Gemeinde match; persons whose age maps to no model education level, and
    persons whose resulting euclidean distance is NaN, are each dropped and counted -- both
    rates share ONE denominator (``n_input``, captured once after the home-geometry filter) per
    the R3 fix described in :func:`realised_work_frame`. ``stats`` behaves as in
    :func:`realised_work_frame`.
    """
    home_crs, dest_crs = df_home_geo.crs, df_education.crs
    if home_crs is None or dest_crs is None or home_crs != dest_crs:
        raise ValueError(
            f"CRS mismatch building the education frame: home={home_crs}, education "
            f"destinations={dest_crs} -- both must match; reproject upstream")

    per_person = _home_per_person(df_home_geo, df_persons)
    edu = df_education[["person_id", "geometry"]].rename(columns={"geometry": "dest_geometry"})
    frame = edu.merge(per_person, on="person_id", how="left")
    n_before_home_filter = len(frame)
    n_no_home = int(frame["home_geometry"].isna().sum())
    _check_no_home_geometry_rate(n_no_home, n_before_home_filter, "pupils/students", max_unmatched_home_share)
    frame = frame[frame["home_geometry"].notna()].copy()

    n_home_commune_missing = _check_home_match_rate(
        frame, "pupils/students", "the per-Kreis comparison would be meaningless", max_unmatched_home_share)
    n_input = len(frame)

    frame["level"] = frame["age"].map(T.model_education_level)
    n_unmapped = int(frame["level"].isna().sum())
    unmapped_rate = n_unmapped / n_input if n_input else 0.0

    home = gpd.GeoSeries(frame["home_geometry"].values, crs=home_crs)
    dest = gpd.GeoSeries(frame["dest_geometry"].values, crs=dest_crs)
    frame["distance_km_euclid"] = home.distance(dest).values / 1000.0
    n_nan_distance = int(frame["distance_km_euclid"].isna().sum())
    nan_distance_rate = n_nan_distance / n_input if n_input else 0.0

    LOGGER.info(
        "[srv_distance] education: %d persons input; %d without home geometry dropped; %d "
        "with an age outside every level (%.1f%% of input) excluded; %d with a NaN distance "
        "(%.1f%% of input) dropped; %d with a missing home Gemeinde",
        n_input, n_no_home, n_unmapped, 100.0 * unmapped_rate, n_nan_distance,
        100.0 * nan_distance_rate, n_home_commune_missing)
    if n_nan_distance:
        LOGGER.warning(
            "[srv_distance] education: %d/%d persons (%.1f%%) had a NaN euclidean distance "
            "and were dropped before banding -- check for missing home/destination geometries",
            n_nan_distance, n_input, 100.0 * nan_distance_rate)

    frame = frame[frame["distance_km_euclid"].notna() & frame["level"].notna()]
    if stats is not None:
        stats.update(n_pupils=len(frame), n_no_home=n_no_home,
                    n_home_commune_missing=n_home_commune_missing,
                    n_unmapped=n_unmapped, n_nan_distance=n_nan_distance)
    return frame[["person_id", "ars5", "level", "distance_km_euclid"]].reset_index(drop=True)


def _target_row(targets, code):
    sel = targets[targets["code"] == code]
    return None if sel.empty else sel.iloc[0]


def _cell(code, scope, model_km_routed, target_row, shrunk_prefix, noise_col, n_reference_col,
          edges, labels, emd_threshold, is_aggregate):
    """One comparison cell (code x scope/level): model band shares vs the SrV target.

    ``n_reference_col`` names the target-table column holding the SCOPE-matching
    reference-person count (R16 assumption, see :func:`compare_work`): ``"n_persons"`` for
    the work "all" scope and for education (no inter/intra split there), ``"n_persons_inter"``
    / ``"n_persons_intra"`` for the work "inter" / "intra" scopes.

    ``target_row is None`` (no target row for this code at all) and a target row present
    but with ``target_row[n_reference_col] == 0`` both yield ``emd = NaN`` ->
    classification "no_reference" via
    :func:`braunschweig.calibration.decision.classify_cell` (accepted label: a gap can
    never be declared without a usable reference). They differ only in ``source``: a
    genuinely missing target row reports ``source = "none"``; a target row that exists but
    has zero reference persons IN THIS SCOPE keeps ITS OWN ``source`` (e.g. the Wolfsburg
    proxy source) so the report can still say which pool the cell would have compared
    against. A target present with model_km_routed empty (zero MODEL persons) also yields
    ``emd = NaN`` for the same "no gap without an actual comparison" reason, but its
    ``classification`` is OVERRIDDEN from ``classify_cell``'s "no_reference" to
    ``"no_model"`` (Task 14 minor): "no_reference" means the REFERENCE side has no usable
    target, which is misleading here since a real target with ``n_reference_persons > 0``
    exists -- the comparison is impossible because the MODEL side is empty instead. This
    override is presentation only: :func:`braunschweig.calibration.decision.decide_layer`
    still classifies the cell internally from the (unchanged) NaN ``emd``, so it already
    treats a "no_model" cell exactly like "no_reference" (never decisive, never a gap)
    without needing to know the new label.

    ``target_share_<label>`` is the SHRUNK target share used for the EMD/gap decision;
    ``target_share_raw_<label>`` is the matching un-shrunk (raw survey) share, read from the
    ``<raw_prefix>_<label>`` columns where ``raw_prefix`` is ``shrunk_prefix`` with its
    trailing ``"_shrunk"`` removed -- so the amount of shrinkage applied is auditable
    directly from the cell row without re-joining the target table.
    """
    raw_prefix = shrunk_prefix.rsplit("_shrunk", 1)[0]
    model_shares = T.weighted_band_shares(model_km_routed, np.ones(len(model_km_routed)), edges)
    row = {"code": code, "scope": scope, "n_model": int(len(model_km_routed)), "is_aggregate": is_aggregate}
    row.update({f"model_share_{lbl}": float(s) for lbl, s in zip(labels, model_shares)})
    if target_row is None:
        row.update(n_reference_persons=0, emd=float("nan"), noise_floor=float("nan"), source="none")
        row.update({f"target_share_{lbl}": float("nan") for lbl in labels})
        row.update({f"target_share_raw_{lbl}": float("nan") for lbl in labels})
    else:
        n_ref = int(target_row[n_reference_col])
        if n_ref == 0:
            row.update(n_reference_persons=0, emd=float("nan"), noise_floor=float("nan"),
                      source=str(target_row["source"]))
            row.update({f"target_share_{lbl}": float("nan") for lbl in labels})
            row.update({f"target_share_raw_{lbl}": float("nan") for lbl in labels})
        else:
            target = np.array([float(target_row[f"{shrunk_prefix}_{lbl}"]) for lbl in labels])
            target_raw = np.array([float(target_row[f"{raw_prefix}_{lbl}"]) for lbl in labels])
            row.update(n_reference_persons=n_ref, source=str(target_row["source"]),
                       emd=T.emd_on_shares(model_shares, target) if len(model_km_routed) else float("nan"),
                       noise_floor=float(target_row[noise_col]))
            row.update({f"target_share_{lbl}": float(t) for lbl, t in zip(labels, target)})
            row.update({f"target_share_raw_{lbl}": float(t) for lbl, t in zip(labels, target_raw)})
    row["classification"] = D.classify_cell(row["emd"], row["noise_floor"], row["n_reference_persons"], emd_threshold)
    if row["n_model"] == 0 and row["n_reference_persons"] > 0:
        row["classification"] = "no_model"
    return row


def compare_work(realised, targets, detour_factor, emd_threshold, min_persons,
                 aggregate_requires_min_persons=D.DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS):
    """One row per (code, scope); decisions per scope via the pre-registered rule.

    R16 ASSUMPTION: the reference-person count used both for classification context and
    for ``decide_layer``'s ``min_persons`` decisiveness gate is the SCOPE-MATCHING count
    from the target table (``n_persons`` for "all", ``n_persons_inter`` / ``n_persons_intra``
    for "inter" / "intra"), not the all-scope ``n_persons``. Using the all-scope count for
    an inter/intra cell would let a Kreis look "decisive" purely because its ALL-scope
    sample is large, even when the inter- or intra-specific reference sample backing that
    particular comparison is far too small to be scientifically meaningful.

    ``aggregate_requires_min_persons`` is passed through to
    :func:`braunschweig.calibration.decision.decide_layer` unchanged (see the AMENDMENT,
    2026-09-04, ADR-0103, in that module's docstring).
    """
    routed = realised["distance_km_euclid"].values * float(detour_factor)
    intra = realised["intra_gemeinde"].astype(bool).values
    scopes = {"all": np.ones(len(realised), dtype=bool), "inter": ~intra, "intra": intra}
    n_reference_col = {"all": "n_persons", "inter": "n_persons_inter", "intra": "n_persons_intra"}
    rows = []
    for scope, mask in scopes.items():
        for code in list(ZGB_CODES) + ["zgb"]:
            sel = mask if code == "zgb" else (mask & (realised["ars5"].values == code))
            target_row = _target_row(targets, code)
            cell = _cell(code, scope, routed[sel], target_row, f"share_{scope}_shrunk",
                        f"emd_noise_95_{scope}", n_reference_col[scope],
                        T.WORK_BAND_EDGES_KM, T.WORK_BAND_LABELS, emd_threshold, code == "zgb")
            if scope == "all":
                # Minor: report the realised intra share alongside the target's on the "all"
                # row, since "all" is the only scope where both directions are present.
                n_sel = int(sel.sum())
                cell["model_share_intra"] = float(intra[sel].mean()) if n_sel else float("nan")
                cell["target_share_intra"] = (float(target_row["share_intra"])
                                              if target_row is not None else float("nan"))
            rows.append(cell)
    cells = pd.DataFrame(rows)
    decisions = {}
    for scope in scopes:
        sub = cells[cells["scope"] == scope][["code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"]]
        decisions[scope] = D.decide_layer(sub, emd_threshold, min_persons, aggregate_requires_min_persons)
    return cells, decisions


def compare_education(realised, targets, detour_factor, emd_threshold, min_persons,
                      aggregate_requires_min_persons=D.DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS):
    """One row per (code, education level); decisions per level via the pre-registered rule.

    Cells carry ``scope = "education"`` uniformly (there is no inter/intra split for
    education) alongside ``education_level`` for the specific level being compared, so the
    column stays meaningful across both the commute and education output tables.

    ``aggregate_requires_min_persons`` is passed through to
    :func:`braunschweig.calibration.decision.decide_layer` unchanged (see
    :func:`compare_work`).
    """
    routed = realised["distance_km_euclid"].values * float(detour_factor)
    comparable = targets[targets["comparable"].astype(bool)]
    rows = []
    decisions = {}
    for level in T.COMPARABLE_LEVELS:
        lvl_targets = comparable[comparable["education_level"] == level]
        lvl_mask = realised["level"].values == level
        level_rows = []
        for code in list(ZGB_CODES) + ["zgb"]:
            sel = lvl_mask if code == "zgb" else (lvl_mask & (realised["ars5"].values == code))
            row = _cell(code, "education", routed[sel], _target_row(lvl_targets, code), "share_shrunk",
                        "emd_noise_95", "n_persons", T.EDUCATION_BAND_EDGES_KM, T.EDUCATION_BAND_LABELS,
                        emd_threshold, code == "zgb")
            row["education_level"] = level
            level_rows.append(row)
        rows.extend(level_rows)
        cells_level = pd.DataFrame(level_rows)
        decisions[level] = D.decide_layer(
            cells_level[["code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"]],
            emd_threshold, min_persons, aggregate_requires_min_persons)
    return pd.DataFrame(rows), decisions


def model_quantiles(realised_work, probabilities=EQASIM_CDF_PROBABILITIES):
    """eqasim-style 20-point quantile curve of the realised euclidean distance per Kreis + ZGB.

    Uses ``numpy.quantile`` (unweighted, linear interpolation) on the realised model
    distances; the SrV REFERENCE quantile table
    (``srv_distance_targets.weighted_quantiles``) instead uses the WEIGHTED Hazen
    midpoint-CDF convention, so the two curves are not numerically comparable point-by-
    point without accounting for that difference.
    """
    rows = []
    skipped = []
    groups = [(code, realised_work[realised_work["ars5"] == code]) for code in ZGB_CODES] + [("zgb", realised_work)]
    for code, grp in groups:
        if grp.empty:
            skipped.append(code)
            continue
        q = np.quantile(grp["distance_km_euclid"].values, probabilities)
        rows.extend({"code": code, "cdf": float(p), "distance_km_euclid": float(v)} for p, v in zip(probabilities, q))
    if skipped:
        LOGGER.info(
            "[srv_distance] model_quantiles: %d Kreis code(s) with no realised work persons, "
            "no quantile curve emitted: %s", len(skipped), skipped)
    return pd.DataFrame(rows)


def _fmt3(value):
    """Format a float to 3 decimals for the markdown report; NaN renders as the explicit
    "n/a" (never Python's bare "nan", which reads as a data error rather than the
    intentional "no_reference" cell it represents)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def _provenance_lines(provenance):
    """Leading parameter block shared by ``summary.md`` and ``provenance.json`` (IMPORTANT 7)."""
    params = (provenance or {}).get("parameters", {})
    if not params:
        return []
    lines = ["Parameters: " + ", ".join(f"{k}={v}" for k, v in params.items())]
    if provenance.get("generated_at"):
        lines.append(f"Generated at: {provenance['generated_at']}")
    lines.append("")
    return lines


def _build_label(decision):
    """Render one decision's "build" verdict for the summary: ``"UNDECIDABLE"`` when
    ``decision["undecidable"]`` is True (fix round 1, #358) -- printing the literal
    ``build = False`` there would misreport an undecidable verdict (no cell in the frame
    was decisive) as a certain "do not build", when the rule in fact could not check
    anything decisive at all."""
    if decision.get("undecidable"):
        return "UNDECIDABLE"
    return str(decision["build"])


def _summary_markdown(cells_work, dec_work, cells_edu, dec_edu, provenance=None):
    lines = ["# SrV primary-distance baseline", ""] + _provenance_lines(provenance) + [
             "Model = realised euclidean home->activity distance x detour factor; reference = SrV 2023",
             "(GIS routed, person-level, GEWICHT_W_ZENSUS, shrunk shares). Classification per the",
             "pre-registered rule (braunschweig.calibration.decision).", "", "## Work (per scope)"]
    for scope, d in dec_work.items():
        lines.append(f"- **{scope}**: build = {_build_label(d)} -- {d['reason']}")
    lines += ["", "| scope | code | n_model | n_ref | EMD | noise floor | class |", "|---|---|---|---|---|---|---|"]
    for r in cells_work.itertuples(index=False):
        lines.append(f"| {r.scope} | {r.code} | {r.n_model} | {r.n_reference_persons} | {_fmt3(r.emd)} | {_fmt3(r.noise_floor)} | {r.classification} |")
    lines += ["", "## Education (per level)"]
    for level, d in dec_edu.items():
        lines.append(f"- **{level}**: build = {_build_label(d)} -- {d['reason']}")
    lines += ["", "| level | code | n_model | n_ref | EMD | noise floor | class |", "|---|---|---|---|---|---|---|"]
    for r in cells_edu.itertuples(index=False):
        lines.append(f"| {r.education_level} | {r.code} | {r.n_model} | {r.n_reference_persons} | {_fmt3(r.emd)} | {_fmt3(r.noise_floor)} | {r.classification} |")
    lines += ["", "Known quirk: the ZGB aggregate row's n_model includes persons whose home Kreis "
                  "could not be resolved (see the module docstring); on the committed 100% baseline "
                  "this is exactly one worker, so sum(kreis n_model) == aggregate n_model - 1 for "
                  "the work 'all' scope -- expected, not a join defect."]
    return "\n".join(lines) + "\n"


def write_outputs(directory, cells_work, dec_work, cells_edu, dec_edu, quantiles, provenance=None):
    """Write the five report artifacts. ``provenance`` (IMPORTANT 7) is optional so the pure-
    helper tests can exercise this function without building a full stage-execute context;
    ``execute`` always passes the real parameter/reference/model-count block. The CSVs stay
    header-free (no provenance comment lines) because Task 9's plot reads them with plain
    ``pandas.read_csv``; the parameter block instead lives in ``provenance.json`` and at the
    top of ``summary.md``.
    """
    provenance = provenance or {}
    os.makedirs(directory, exist_ok=True)
    cells_work.to_csv(os.path.join(directory, "commute_by_kreis.csv"), index=False)
    cells_edu.to_csv(os.path.join(directory, "education_by_kreis_level.csv"), index=False)
    quantiles.to_csv(os.path.join(directory, "commute_quantiles_model.csv"), index=False)
    with open(os.path.join(directory, "decisions.json"), "w", encoding="utf-8") as fh:
        json.dump({"work": dec_work, "education": dec_edu}, fh, indent=2)
    with open(os.path.join(directory, "provenance.json"), "w", encoding="utf-8") as fh:
        json.dump(provenance, fh, indent=2)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(_summary_markdown(cells_work, dec_work, cells_edu, dec_edu, provenance))


# --------------------------------------------------------------------------- stage

def _validate_input_crs(df_home, df_work, df_education, gemeinden):
    """R18: fail fast on a CRS mismatch between the stage inputs, before any geometry work
    (sjoins, distance calculations) is attempted on them.

    Shapely computes a planar distance on raw coordinates regardless of the CRS label a
    GeoDataFrame carries, so a real mismatch would still "run" and silently produce a
    distance with no defensible unit -- unacceptable for research software (CLAUDE.md
    "Fallback transparency" / "Geospatial processing"). ``gemeinden`` is loaded via
    ``braunschweig.analysis.spatial.load_gemeinden(df_home.crs)``, which reprojects to
    ``df_home.crs`` by construction, so that leg of the check is a defence against a future
    change to that contract rather than one that can fail today; ``df_work`` and
    ``df_education`` are NOT reprojected anywhere upstream, so they are the legs that
    actually catch a real-world mismatch.
    """
    crs_by_name = {
        "synthesis.population.spatial.home.locations": df_home.crs,
        "synthesis.population.spatial.primary.locations (work)": df_work.crs,
        "synthesis.population.spatial.primary.locations (education)": df_education.crs,
        "gemeinden (VG250, via braunschweig.analysis.spatial.load_gemeinden)": gemeinden.crs,
    }
    missing = [name for name, crs in crs_by_name.items() if crs is None]
    if missing:
        raise ValueError(f"Missing CRS on: {missing}; every input geometry must carry an explicit CRS")
    reference_crs = df_home.crs
    mismatched = {name: crs for name, crs in crs_by_name.items() if crs != reference_crs}
    if mismatched:
        detail = ", ".join(f"{name}={crs}" for name, crs in crs_by_name.items())
        raise ValueError(
            f"CRS mismatch across stage inputs (expected {reference_crs} throughout, taken from "
            f"synthesis.population.spatial.home.locations): {detail}")


def execute(context):
    from braunschweig.analysis import spatial  # VG250 access only at run time

    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, df_education = context.stage("synthesis.population.spatial.primary.locations")
    df_persons = context.stage("synthesis.population.enriched")[["person_id", "household_id", "age"]]
    reference = context.stage("braunschweig.analysis.reference.srv.commute_distance")
    detour = float(context.config(KEY_DETOUR))
    emd_threshold = float(context.config(KEY_EMD_THRESHOLD))
    min_persons = int(context.config(KEY_MIN_PERSONS))
    aggregate_requires_min_persons = bool(context.config(KEY_AGGREGATE_REQUIRES_MIN_PERSONS))
    max_unmatched_home_share = float(context.config(KEY_MAX_UNMATCHED_HOME_SHARE))
    warn_unmatched_destination_share = float(context.config(KEY_WARN_UNMATCHED_DESTINATION_SHARE))
    sampling_rate = float(context.config("sampling_rate"))
    out_dir = os.path.join(context.config("output_path"), context.config(KEY_SUBDIR))

    gemeinden = spatial.load_gemeinden(df_home.crs)
    _validate_input_crs(df_home, df_work, df_education, gemeinden)

    LOGGER.info(
        "[srv_distance] parameters: detour_factor=%.3f, emd_threshold=%.3f, min_persons=%d, "
        "aggregate_requires_min_persons=%s, max_unmatched_home_share=%.3f, "
        "warn_unmatched_destination_share=%.3f, sampling_rate=%.4f; writing to %s",
        detour, emd_threshold, min_persons, aggregate_requires_min_persons,
        max_unmatched_home_share, warn_unmatched_destination_share, sampling_rate, out_dir)

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    work_stats, edu_stats = {}, {}
    realised_work = realised_work_frame(homes, df_work, df_persons, gemeinden,
                                        max_unmatched_home_share=max_unmatched_home_share,
                                        warn_unmatched_destination_share=warn_unmatched_destination_share,
                                        stats=work_stats)
    realised_edu = realised_education_frame(homes, df_education, df_persons,
                                            max_unmatched_home_share=max_unmatched_home_share,
                                            stats=edu_stats)

    cells_work, dec_work = compare_work(realised_work, reference["commute"], detour, emd_threshold, min_persons,
                                        aggregate_requires_min_persons)
    cells_edu, dec_edu = compare_education(realised_edu, reference["education"], detour, emd_threshold, min_persons,
                                           aggregate_requires_min_persons)
    quantiles = model_quantiles(realised_work)

    provenance = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "parameters": {
            "detour_factor": detour, "emd_threshold": emd_threshold, "min_persons": min_persons,
            "aggregate_requires_min_persons": aggregate_requires_min_persons,
            "max_unmatched_home_share": max_unmatched_home_share,
            "warn_unmatched_destination_share": warn_unmatched_destination_share,
            "sampling_rate": sampling_rate,
        },
        "reference": {
            "srv_dir": str(reference["srv_dir"]),
            "n_commute_rows": int(len(reference["commute"])),
            "n_education_rows": int(len(reference["education"])),
            "n_quantile_rows": int(len(reference["quantiles"])),
        },
        "model": {
            "n_workers": work_stats.get("n_workers"),
            "n_pupils": edu_stats.get("n_pupils"),
            "n_home_unmatched_workers": work_stats.get("n_no_home"),
            "n_home_unmatched_pupils": edu_stats.get("n_no_home"),
            "n_home_commune_missing_workers": work_stats.get("n_home_commune_missing"),
            "n_home_commune_missing_pupils": edu_stats.get("n_home_commune_missing"),
            "n_dest_outside": work_stats.get("n_dest_outside"),
            "n_nan_distance_workers": work_stats.get("n_nan_distance"),
            "n_nan_distance_pupils": edu_stats.get("n_nan_distance"),
            "n_ages_unmapped": edu_stats.get("n_unmapped"),
        },
    }
    write_outputs(out_dir, cells_work, dec_work, cells_edu, dec_edu, quantiles, provenance)
    for scope, d in dec_work.items():
        LOGGER.info("[srv_distance] work/%s: %s", scope, d["reason"])
    for level, d in dec_edu.items():
        LOGGER.info("[srv_distance] education/%s: %s", level, d["reason"])
    return dict(commute=cells_work, education=cells_edu, quantiles=quantiles,
                decisions={"work": dec_work, "education": dec_edu})
