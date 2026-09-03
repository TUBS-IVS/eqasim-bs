"""synpp stage: realised home->work / home->education distances per home Kreis vs SrV 2023.

Mirrors eqasim's ``analysis/synthesis/commute_distance`` (realised euclidean distance
per person, quantile curve) and adds what this project needs for the pre-registered
calibration decision (spec 2026-09-03, Section 5): per-Kreis band shares against the
committed SrV targets, EMD, the SrV bootstrap noise floor, an intra/inter-Gemeinde
split for work (attributes a gap to the OD layer or the per-person-target layer), and
education by the model's age levels. Compares in ROUTED km: euclidean * detour factor.

Outputs go under ``<output_path>/analysis/srv_distance_validation/``. This stage reads
cached synthesis stages only (no MATSim), so it runs in minutes on a cached 100% run.
"""
from __future__ import annotations

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
KEY_SUBDIR = "srv_distance_output_subdir"
DEFAULT_SUBDIR = os.path.join("analysis", "srv_distance_validation")
EQASIM_CDF_PROBABILITIES = np.linspace(0.0, 1.0, 20)
ZGB_CODES = tuple(T.ZGB_KREISE)


def configure(context):
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("synthesis.population.enriched")
    context.stage("braunschweig.analysis.reference.srv.commute_distance")
    context.config("output_path")
    context.config(KEY_DETOUR, T.DEFAULT_DETOUR_FACTOR)
    context.config(KEY_EMD_THRESHOLD, D.DEFAULT_EMD_THRESHOLD)
    context.config(KEY_MIN_PERSONS, D.DEFAULT_MIN_PERSONS)
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)


# --------------------------------------------------------------------------- pure helpers

def _home_per_person(df_home_geo, df_persons):
    """person_id -> home geometry, ars5, home commune_id (one row per person)."""
    homes = df_home_geo[["household_id", "geometry", "ars5", "commune_id"]].rename(
        columns={"geometry": "home_geometry", "commune_id": "home_commune_id"})
    return df_persons[["person_id", "household_id", "age"]].merge(homes, on="household_id", how="left")


def realised_work_frame(df_home_geo, df_work, df_persons, gemeinden):
    """Per worker: home Kreis, home and destination Gemeinde, euclidean km, intra flag.

    ``gemeinden`` (GeoDataFrame commune_id, geometry) resolves the DESTINATION Gemeinde
    by point-in-polygon, so home and destination share one key universe; destinations
    outside every polygon (outside ZGB) count as inter-Gemeinde and are logged.

    Per CLAUDE.md "Fallback transparency": persons without a home geometry are dropped
    and counted; persons whose resulting euclidean distance is NaN (e.g. a missing
    destination geometry) are also dropped and counted here -- BEFORE any band-share
    computation -- because ``srv_distance_targets.weighted_band_shares`` raises on a
    NaN distance rather than silently absorbing it into a band.
    """
    per_person = _home_per_person(df_home_geo, df_persons)
    work = df_work[["person_id", "geometry"]].rename(columns={"geometry": "dest_geometry"})
    frame = work.merge(per_person, on="person_id", how="left")
    n_no_home = int(frame["home_geometry"].isna().sum())
    frame = frame[frame["home_geometry"].notna()]

    dest = gpd.GeoDataFrame(frame[["person_id"]], geometry=frame["dest_geometry"].values,
                            crs=gemeinden.crs)
    joined = gpd.sjoin(dest, gemeinden[["commune_id", "geometry"]], how="left", predicate="within")
    joined = joined.drop_duplicates("person_id")
    frame = frame.merge(joined[["person_id", "commune_id"]].rename(columns={"commune_id": "dest_commune_id"}),
                        on="person_id", how="left")
    n_dest_outside = int(frame["dest_commune_id"].isna().sum())

    home = gpd.GeoSeries(frame["home_geometry"].values, crs=gemeinden.crs)
    dest_geo = gpd.GeoSeries(frame["dest_geometry"].values, crs=gemeinden.crs)
    frame["distance_km_euclid"] = home.distance(dest_geo).values / 1000.0
    n_nan_distance = int(frame["distance_km_euclid"].isna().sum())
    frame = frame[frame["distance_km_euclid"].notna()]
    frame["intra_gemeinde"] = (frame["dest_commune_id"].notna()
                               & (frame["dest_commune_id"] == frame["home_commune_id"]))
    LOGGER.info("[srv_distance] work: %d persons; %d without home geometry dropped; %d destinations "
                "outside every Gemeinde polygon (counted inter-Gemeinde); %d with a NaN distance dropped",
                len(frame), n_no_home, n_dest_outside, n_nan_distance)
    if n_nan_distance:
        LOGGER.warning("[srv_distance] work: %d/%d persons (%.1f%%) had a NaN euclidean distance and "
                       "were dropped before banding -- check for missing home/destination geometries",
                       n_nan_distance, n_no_home + n_nan_distance + len(frame),
                       100.0 * n_nan_distance / max(n_no_home + n_nan_distance + len(frame), 1))
    return frame[["person_id", "ars5", "home_commune_id", "dest_commune_id",
                  "distance_km_euclid", "intra_gemeinde"]].reset_index(drop=True)


def realised_education_frame(df_home_geo, df_education, df_persons):
    """Per pupil/student: home Kreis, model age level, euclidean km.

    Per CLAUDE.md "Fallback transparency": persons without a home geometry, persons
    whose age maps to no model education level, and persons whose resulting euclidean
    distance is NaN are each dropped and counted -- the NaN-distance drop happens
    BEFORE banding, for the same reason as in :func:`realised_work_frame`.
    """
    per_person = _home_per_person(df_home_geo, df_persons)
    edu = df_education[["person_id", "geometry"]].rename(columns={"geometry": "dest_geometry"})
    frame = edu.merge(per_person, on="person_id", how="left")
    n_no_home = int(frame["home_geometry"].isna().sum())
    frame = frame[frame["home_geometry"].notna()]
    frame["level"] = frame["age"].map(T.model_education_level)
    n_unmapped = int(frame["level"].isna().sum())
    home = gpd.GeoSeries(frame["home_geometry"].values, crs=df_home_geo.crs)
    dest = gpd.GeoSeries(frame["dest_geometry"].values, crs=df_home_geo.crs)
    frame["distance_km_euclid"] = home.distance(dest).values / 1000.0
    n_nan_distance = int(frame["distance_km_euclid"].isna().sum())
    frame = frame[frame["distance_km_euclid"].notna()]
    LOGGER.info("[srv_distance] education: %d persons; %d without home geometry dropped; %d with an age "
                "outside every level (%.1f%%) excluded; %d with a NaN distance dropped",
                len(frame), n_no_home, n_unmapped,
                100.0 * n_unmapped / max(len(frame) + n_unmapped, 1), n_nan_distance)
    if n_nan_distance:
        LOGGER.warning("[srv_distance] education: %d persons had a NaN euclidean distance and were "
                       "dropped before banding -- check for missing home/destination geometries",
                       n_nan_distance)
    frame = frame[frame["level"].notna()]
    return frame[["person_id", "ars5", "level", "distance_km_euclid"]].reset_index(drop=True)


def _target_row(targets, code):
    sel = targets[targets["code"] == code]
    return None if sel.empty else sel.iloc[0]


def _cell(code, scope, model_km_routed, target_row, share_prefix, shrunk_prefix, noise_col,
          edges, labels, emd_threshold, is_aggregate):
    """One comparison cell (code x scope/level): model band shares vs the SrV target.

    ``target_row is None`` or ``n_persons == 0`` produces a cell with ``n_reference_persons
    = 0`` and ``emd = NaN``; a target with n_persons > 0 but zero MODEL persons in the cell
    also yields ``emd = NaN`` (the EMD is only computed when ``model_km_routed`` is
    non-empty). Both cases classify as "no_reference" via
    ``braunschweig.calibration.decision.classify_cell`` -- a gap can never be declared
    without an actual model distribution to compare, so "no_reference" is the correct,
    conservative label rather than treating an empty model cell as a perfect (or a
    worst-case) match.
    """
    model_shares = T.weighted_band_shares(model_km_routed, np.ones(len(model_km_routed)), edges)
    row = {"code": code, "scope": scope, "n_model": int(len(model_km_routed)), "is_aggregate": is_aggregate}
    row.update({f"model_share_{lbl}": float(s) for lbl, s in zip(labels, model_shares)})
    if target_row is None or int(target_row["n_persons"]) == 0:
        row.update(n_reference_persons=0, emd=float("nan"), noise_floor=float("nan"), source="none")
        row.update({f"target_share_{lbl}": float("nan") for lbl in labels})
    else:
        target = np.array([float(target_row[f"{shrunk_prefix}_{lbl}"]) for lbl in labels])
        row.update(n_reference_persons=int(target_row["n_persons"]), source=str(target_row["source"]),
                   emd=T.emd_on_shares(model_shares, target) if len(model_km_routed) else float("nan"),
                   noise_floor=float(target_row[noise_col]))
        row.update({f"target_share_{lbl}": float(t) for lbl, t in zip(labels, target)})
    row["classification"] = D.classify_cell(row["emd"], row["noise_floor"], row["n_reference_persons"], emd_threshold)
    return row


def compare_work(realised, targets, detour_factor, emd_threshold, min_persons):
    """One row per (code, scope); decisions per scope via the pre-registered rule."""
    routed = realised["distance_km_euclid"].values * float(detour_factor)
    intra = realised["intra_gemeinde"].astype(bool).values
    scopes = {"all": np.ones(len(realised), dtype=bool), "inter": ~intra, "intra": intra}
    rows = []
    for scope, mask in scopes.items():
        for code in list(ZGB_CODES) + ["zgb"]:
            sel = mask if code == "zgb" else (mask & (realised["ars5"].values == code))
            rows.append(_cell(code, scope, routed[sel], _target_row(targets, code),
                              f"share_{scope}", f"share_{scope}_shrunk", f"emd_noise_95_{scope}",
                              T.WORK_BAND_EDGES_KM, T.WORK_BAND_LABELS, emd_threshold, code == "zgb"))
    cells = pd.DataFrame(rows)
    decisions = {}
    for scope in scopes:
        sub = cells[cells["scope"] == scope][["code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"]]
        decisions[scope] = D.decide_layer(sub, emd_threshold, min_persons)
    return cells, decisions


def compare_education(realised, targets, detour_factor, emd_threshold, min_persons):
    """One row per (code, education level); decisions per level via the pre-registered rule."""
    routed = realised["distance_km_euclid"].values * float(detour_factor)
    comparable = targets[targets["comparable"].astype(bool)]
    rows = []
    decisions = {}
    for level in T.COMPARABLE_LEVELS:
        lvl_targets = comparable[comparable["education_level"] == level]
        lvl_mask = realised["level"].values == level
        for code in list(ZGB_CODES) + ["zgb"]:
            sel = lvl_mask if code == "zgb" else (lvl_mask & (realised["ars5"].values == code))
            row = _cell(code, level, routed[sel], _target_row(lvl_targets, code), "share", "share_shrunk",
                        "emd_noise_95", T.EDUCATION_BAND_EDGES_KM, T.EDUCATION_BAND_LABELS,
                        emd_threshold, code == "zgb")
            row["education_level"] = level
            rows.append(row)
        cells_level = pd.DataFrame([r for r in rows if r["education_level"] == level])
        decisions[level] = D.decide_layer(
            cells_level[["code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"]],
            emd_threshold, min_persons)
    return pd.DataFrame(rows), decisions


def model_quantiles(realised_work, probabilities=EQASIM_CDF_PROBABILITIES):
    """eqasim-style 20-point quantile curve of the realised euclidean distance per Kreis + ZGB."""
    rows = []
    groups = [(code, realised_work[realised_work["ars5"] == code]) for code in ZGB_CODES] + [("zgb", realised_work)]
    for code, grp in groups:
        if grp.empty:
            continue
        q = np.quantile(grp["distance_km_euclid"].values, probabilities)
        rows.extend({"code": code, "cdf": float(p), "distance_km_euclid": float(v)} for p, v in zip(probabilities, q))
    return pd.DataFrame(rows)


def _summary_markdown(cells_work, dec_work, cells_edu, dec_edu):
    lines = ["# SrV primary-distance baseline", "",
             "Model = realised euclidean home->activity distance x detour factor; reference = SrV 2023",
             "(GIS routed, person-level, GEWICHT_W_ZENSUS, shrunk shares). Classification per the",
             "pre-registered rule (braunschweig.calibration.decision).", "", "## Work (per scope)"]
    for scope, d in dec_work.items():
        lines.append(f"- **{scope}**: build = {d['build']} -- {d['reason']}")
    lines += ["", "| scope | code | n_model | n_ref | EMD | noise floor | class |", "|---|---|---|---|---|---|---|"]
    for r in cells_work.itertuples(index=False):
        lines.append(f"| {r.scope} | {r.code} | {r.n_model} | {r.n_reference_persons} | {r.emd:.3f} | {r.noise_floor:.3f} | {r.classification} |")
    lines += ["", "## Education (per level)"]
    for level, d in dec_edu.items():
        lines.append(f"- **{level}**: build = {d['build']} -- {d['reason']}")
    lines += ["", "| level | code | n_model | n_ref | EMD | noise floor | class |", "|---|---|---|---|---|---|---|"]
    for r in cells_edu.itertuples(index=False):
        lines.append(f"| {r.education_level} | {r.code} | {r.n_model} | {r.n_reference_persons} | {r.emd:.3f} | {r.noise_floor:.3f} | {r.classification} |")
    return "\n".join(lines) + "\n"


def write_outputs(directory, cells_work, dec_work, cells_edu, dec_edu, quantiles):
    os.makedirs(directory, exist_ok=True)
    cells_work.to_csv(os.path.join(directory, "commute_by_kreis.csv"), index=False)
    cells_edu.to_csv(os.path.join(directory, "education_by_kreis_level.csv"), index=False)
    quantiles.to_csv(os.path.join(directory, "commute_quantiles_model.csv"), index=False)
    with open(os.path.join(directory, "decisions.json"), "w", encoding="utf-8") as fh:
        json.dump({"work": dec_work, "education": dec_edu}, fh, indent=2)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(_summary_markdown(cells_work, dec_work, cells_edu, dec_edu))


# --------------------------------------------------------------------------- stage

def execute(context):
    from braunschweig.analysis import spatial  # VG250 access only at run time

    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, df_education = context.stage("synthesis.population.spatial.primary.locations")
    df_persons = context.stage("synthesis.population.enriched")[["person_id", "household_id", "age"]]
    reference = context.stage("braunschweig.analysis.reference.srv.commute_distance")
    detour = float(context.config(KEY_DETOUR))
    emd_threshold = float(context.config(KEY_EMD_THRESHOLD))
    min_persons = int(context.config(KEY_MIN_PERSONS))
    out_dir = os.path.join(context.config("output_path"), context.config(KEY_SUBDIR))

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    gemeinden = spatial.load_gemeinden(df_home.crs)
    realised_work = realised_work_frame(homes, df_work, df_persons, gemeinden)
    realised_edu = realised_education_frame(homes, df_education, df_persons)

    cells_work, dec_work = compare_work(realised_work, reference["commute"], detour, emd_threshold, min_persons)
    cells_edu, dec_edu = compare_education(realised_edu, reference["education"], detour, emd_threshold, min_persons)
    quantiles = model_quantiles(realised_work)
    write_outputs(out_dir, cells_work, dec_work, cells_edu, dec_edu, quantiles)
    for scope, d in dec_work.items():
        LOGGER.info("[srv_distance] work/%s: %s", scope, d["reason"])
    for level, d in dec_edu.items():
        LOGGER.info("[srv_distance] education/%s: %s", level, d["reason"])
    return dict(commute=cells_work, education=cells_edu, quantiles=quantiles,
                decisions={"work": dec_work, "education": dec_edu})
