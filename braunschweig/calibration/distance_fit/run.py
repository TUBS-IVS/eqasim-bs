"""Orchestrates the distance-fit diagnostic for one or more activities.

Consumes already-loaded cache stages, computes realised distances from the
actual assigned locations, fits them to the committed MiD references per
spatial stratum, runs the work between/within decomposition + consistency
check, and writes committed reports. Callable from both the CLI and the synpp
stage. End-to-end exercised on the server (needs a cache).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.calibration.distance_fit import distances as dist_mod
from braunschweig.calibration.distance_fit import fit_metrics as fm
from braunschweig.calibration.distance_fit import references as refs
from braunschweig.calibration.distance_fit import work_decomposition as wd
from braunschweig.calibration.distance_fit import report as report_mod

logger = logging.getLogger(__name__)

# Age -> T43 age-band column (school age groups). Ages outside 0..17 are not
# school pupils in the T43 sense and are excluded (logged).
_T43_AGE_BANDS = [(0, 6, "km_0_6"), (7, 10, "km_7_10"), (11, 13, "km_11_13"), (14, 17, "km_14_17")]


def _build_rs7_lookup(df_regiostar, communes):
    ags8_to_rs7 = df_regiostar.set_index("commune_id")["regiostar7"].to_dict()
    out = {}
    for c in communes:
        v = ags8_to_rs7.get(ars_to_ags8(c))
        out[c] = int(v) if v is not None and not pd.isna(v) else -1
    return out


def _age_band(age):
    for lo, hi, col in _T43_AGE_BANDS:
        if lo <= age <= hi:
            return col
    return None


def _aggregate_emd(df_dist, target_zgb, band_edges):
    if target_zgb is None:
        return None
    tmp = df_dist.copy()
    tmp["__all"] = "03ZGB"
    fit = fm.band_share_fit(tmp, "__all", {"03ZGB": target_zgb}, band_edges,
                            reference_tag="out_of_sample")
    return float(fit["emd"].iloc[0]) if not fit.empty else None


def _sum_by_commune(df, value_col=None):
    keyed = df.assign(_c=df["commune_id"].astype(str))
    if value_col is None:
        return keyed.groupby("_c").size().astype(float).to_dict()
    return keyed.groupby("_c")[value_col].sum().to_dict()


def run_distance_fit(stages, mid_dir, *, activities, detour_factor, output_dir, provenance):
    df_act = stages["activities"]
    df_loc = stages["locations"]
    df_regiostar = stages["regiostar"]

    communes = sorted(str(c) for c in df_loc["commune_id"].dropna().unique())
    rs7_lookup = _build_rs7_lookup(df_regiostar, communes)

    summaries = {}

    if "work" in activities:
        dfw = dist_mod.realised_distances(df_act, df_loc, activity="work",
                                          detour_factor=detour_factor, rs7_lookup=rs7_lookup)
        dfw["ars5"] = dfw["home_commune_id"].astype(str).str[:5]
        p13, edges13, tag13 = refs.work_p13(mid_dir)
        p13_rs7, _, _ = refs.work_p13_rs7(mid_dir)
        fit_kreis = fm.band_share_fit(dfw, "ars5", p13, edges13, reference_tag=tag13)
        fit_rs7 = fm.band_share_fit(dfw, "home_rs7", p13_rs7, edges13, reference_tag="out_of_sample")
        report_mod.write_fit_csv(pd.concat([fit_kreis, fit_rs7], ignore_index=True),
                                 output_dir, "work_distance_fit_by_key.csv")
        s = fm.honesty_summary(fit_kreis, metric="emd")
        s["aggregate"] = _aggregate_emd(dfw, p13.get("03ZGB"), edges13)
        summaries["work"] = s
        # NOTE: P38_2 cross-check deferred: its keys are region NAMES, not ARS5,
        # so joining needs a name->ARS map that does not yet exist (logged).
        logger.info("[distance-fit] work: P38_2 cross-check skipped (region-name vs ARS5 key mismatch).")

        # between/within decomposition
        work_loc = (df_act[df_act.purpose == "work"][["person_id", "activity_index"]]
                    .merge(df_loc[["person_id", "activity_index", "commune_id"]],
                           on=["person_id", "activity_index"], how="left"))
        work_communes = (work_loc.dropna(subset=["commune_id"]).drop_duplicates("person_id")
                         .rename(columns={"commune_id": "work_commune_id"})
                         [["person_id", "work_commune_id"]])
        dec = dfw[["person_id", "home_commune_id", "distance_km"]].merge(
            work_communes, on="person_id", how="left")
        muni = stages["municipalities"]
        centroids = {str(r["commune_id"]): (r["geometry"].centroid.x, r["geometry"].centroid.y)
                     for _, r in muni.iterrows()}
        between = wd.between_gemeinde_distances(dec, centroids, detour_factor=detour_factor)
        dec = dec.merge(between, on="person_id", how="left")
        dec["within_km"] = dec["distance_km"] - dec["between_km"]
        report_mod.write_fit_csv(dec, output_dir, "work_distance_fit_decomposition.csv")

        # jobs / attraction / potential consistency
        assigned_jobs = _sum_by_commune(work_loc.dropna(subset=["commune_id"]))
        emp = _sum_by_commune(stages["employees"], "weight")
        wl = stages["work_locations"]
        pot_col = "potential_work" if "potential_work" in wl.columns else (
            "employees" if "employees" in wl.columns else None)
        pot = _sum_by_commune(wl, pot_col) if pot_col else {}
        report_mod.write_fit_csv(
            wd.jobs_attraction_consistency(assigned_jobs, emp, pot),
            output_dir, "work_jobs_attraction_consistency.csv")

    if "secondary" in activities:
        dfs = dist_mod.realised_distances(df_act, df_loc, activity="secondary",
                                          detour_factor=detour_factor, rs7_lookup=rs7_lookup)
        w12, edges12, tag12 = refs.secondary_w12(mid_dir)
        fit_sec = fm.band_share_fit(dfs, "purpose", w12, edges12, reference_tag=tag12)
        report_mod.write_fit_csv(fit_sec, output_dir, "secondary_distance_fit_by_key.csv")
        summaries["secondary"] = fm.honesty_summary(fit_sec, metric="emd")

    if "education" in activities:
        dfe = dist_mod.realised_distances(df_act, df_loc, activity="education",
                                          detour_factor=detour_factor, rs7_lookup=rs7_lookup)
        enriched = stages.get("enriched")
        if enriched is not None and "age" in getattr(enriched, "columns", []):
            age_map = enriched.drop_duplicates("person_id").set_index("person_id")["age"].to_dict()
            dfe["age"] = dfe["person_id"].map(age_map)
            dfe = dfe[dfe["age"].notna()].copy()
            dfe["ageband"] = dfe["age"].astype(int).map(_age_band)
            n_drop = int(dfe["ageband"].isna().sum())
            if n_drop:
                logger.warning("[distance-fit] education: %d pupils outside T43 age bands 0-17; dropped.", n_drop)
            dfe = dfe[dfe["ageband"].notna()].copy()
            dfe["t43_key"] = dfe["home_rs7"].astype(str) + "|" + dfe["ageband"].astype(str)
            t43, _, tag43 = refs.education_t43(mid_dir)
            fit_edu = fm.mean_distance_fit(dfe, ["t43_key"], t43, reference_tag=tag43)
            report_mod.write_fit_csv(fit_edu, output_dir, "education_distance_fit_by_key.csv")
            summaries["education"] = fm.honesty_summary(fit_edu, metric="abs_err_km")
        else:
            logger.warning("[distance-fit] education: no enriched 'age' available; skipping education fit.")

    provenance = dict(provenance)
    provenance["detour_factor"] = detour_factor
    provenance.setdefault("git_hash", report_mod.git_hash())
    report_mod.write_summary(summaries, provenance, output_dir)
    return summaries
