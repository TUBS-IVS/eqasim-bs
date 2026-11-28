"""Decision aid for the commuter weights of the student balance (issue #140, Phase 2). Not a validation.

The calibration of ``scripts/build_student_balance.py calibrate`` cannot decide the weights: every
parameter set lies within the bootstrap noise floor of the SrV destination reference (see
``../student_commuter_weight_calibration.csv``). The owner therefore sets the values. This script
puts a list of candidate sets next to the few POOLED SrV statistics that are robust enough to be
yardsticks, so the choice rests on committed numbers:

* destination shares of all region students (``home_group == region`` of the SrV destination table):
  share studying outside the region, share going to Region Hannover, share going to other outside
  places; and of the Braunschweig residents: share studying in Braunschweig, share studying in
  Landkreis Wolfenbuettel, share studying outside the region;
* the routed distance of the education leg (SrV band table, row 03ZGB): share 25 to 50 km and
  share of 50 km or more;
* the students living per Kreis (the gate columns of ``student_level_table.gate_table``).

Model side of the distance bands: every commuter cell T(home Kreis, site) is spread over the
communes of the home Kreis in proportion to population times the weight curve (the rule that built
the cell), the straight-line distance commune centroid to site is multiplied by the detour factor
``braunschweig.calibration.circuity.LEGACY_DETOUR_FACTOR`` (SrV distances are routed) and binned with
the SrV band edges. Local cells (students living in the Kreis of their site) carry no distance and
count as below 25 km. Kreise are pooled with the SrV student weights of the Kreis (the mix the
calibration uses, assumption A9).

ASSUMPTIONS of this aid: the standard errors of the pooled shares are
``sqrt(p (1 - p) / n_effective)`` with the Kish ``n_effective`` of the SrV table; for the distance
bands (111 of the 156 persons have a routed distance) ``n_effective`` is scaled by 111 / 156. They
are approximate and only rank the candidates; ``z_*`` = (model - SrV) / that standard error.

Usage (local-only inputs as arguments, as for the CLI):
    python analyse_weight_candidates.py --sites <lsn csv> --vg250 <zip> --zensus-students <csv>
        --zensus-employed <csv> --source-commit <sha> --out <csv>
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[5]
sys.path.append(str(REPO))

from braunschweig.calibration.circuity import LEGACY_DETOUR_FACTOR  # noqa: E402

ALL_THREE = ("03102", "03103", "03158")
WOLFENBUETTEL = ("03158",)
#: (short, middle, long, mover share, mover site Kreise, label)
CANDIDATES = [
    (1.0, 1.0, 0.95, 0.0, ALL_THREE, "option_a_stage_weights_optimum"),
    (1.0, 1.0, 0.55, 0.15, ALL_THREE, "option_b_stage_weights_and_movers_optimum"),
    (1.0, 1.0, 0.3, 0.4, WOLFENBUETTEL, "option_c_lead_recommendation"),
    # one parameter of option C changed at a time
    (1.0, 1.0, 0.0, 0.4, WOLFENBUETTEL, "c_long_0.0"),
    (1.0, 1.0, 0.1, 0.4, WOLFENBUETTEL, "c_long_0.1"),
    (1.0, 1.0, 0.2, 0.4, WOLFENBUETTEL, "c_long_0.2"),
    (1.0, 1.0, 0.4, 0.4, WOLFENBUETTEL, "c_long_0.4"),
    (1.0, 1.0, 0.55, 0.4, WOLFENBUETTEL, "c_long_0.55"),
    (1.0, 1.0, 0.95, 0.4, WOLFENBUETTEL, "c_long_0.95"),
    (1.0, 1.0, 0.3, 0.0, WOLFENBUETTEL, "c_without_movers"),
    (1.0, 1.0, 0.3, 0.2, WOLFENBUETTEL, "c_movers_0.2"),
    (1.0, 1.0, 0.3, 0.3, WOLFENBUETTEL, "c_movers_0.3"),
    (1.0, 1.0, 0.3, 0.5, WOLFENBUETTEL, "c_movers_0.5"),
    (1.0, 1.0, 0.3, 0.6, WOLFENBUETTEL, "c_movers_0.6"),
    (1.0, 1.0, 0.3, 0.15, ALL_THREE, "c_movers_0.15_at_all_three_sites"),
    (1.0, 1.0, 0.3, 0.3, ALL_THREE, "c_movers_0.3_at_all_three_sites"),
    (1.0, 0.8, 0.3, 0.4, WOLFENBUETTEL, "c_middle_0.8"),
    (1.0, 0.6, 0.3, 0.4, WOLFENBUETTEL, "c_middle_0.6"),
    (0.9, 0.9, 0.3, 0.4, WOLFENBUETTEL, "c_short_0.9_middle_0.9"),
    (0.8, 0.8, 0.3, 0.4, WOLFENBUETTEL, "c_short_0.8_middle_0.8"),
    # lower curves, for contrast
    (0.8, 0.6, 0.2, 0.4, WOLFENBUETTEL, "low_short_0.8_middle_0.6_long_0.2"),
    (0.6, 0.3, 0.1, 0.4, WOLFENBUETTEL, "low_short_0.6_middle_0.3_long_0.1"),
]
OUTSIDE_GROUPS = ["region_hannover", "landkreis_hildesheim", "other_outside"]
SRV_BAND_ROW = "03ZGB"
SRV_PERSONS_WITH_DISTANCE = 111
SRV_PERSONS_REFERENCE = 156
BAND_EDGES_KM = [5.0, 10.0, 25.0, 50.0, 100.0]
BAND_LABELS = ["0_5", "5_10", "10_25", "25_50", "50_100", "100_plus"]
DETOUR_SENSITIVITY = (1.2, 1.4)


def _load_cli():
    spec = importlib.util.spec_from_file_location("build_student_balance", REPO / "scripts" / "build_student_balance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _standard_error(share: float, n_effective: float) -> float:
    return float(np.sqrt(share * (1.0 - share) / n_effective))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("--sites", "--vg250", "--zensus-students", "--zensus-employed", "--source-commit", "--out"):
        parser.add_argument(name, required=True)
    own = parser.parse_args(argv)
    cli = _load_cli()
    T, W, LV, E, G, SR = cli.T, cli.W, cli.LV, cli.E, cli.G, cli.SR
    args = cli.build_parser().parse_args([
        "balance", "--sites", own.sites, "--vg250", own.vg250, "--source-commit", own.source_commit,
        "--weights", "1,1,1", "--mover-share", "0", "--weights-source", "owner",
        "--zensus-students", own.zensus_students, "--zensus-employed", own.zensus_employed,
        "--artifact-dir", str(Path(own.out).parent), "--label", "unused"])
    logging.basicConfig(level=logging.WARNING)

    parameters = cli._read_csv(args.srv_parameters, cli.PARAMETER_COLUMNS, "SrV student parameters")
    srv_share = cli._read_csv(args.srv_share, cli.SRV_SHARE_COLUMNS, "SrV student share", text_columns=["code"])
    mid_share = cli._read_csv(args.mid_share, cli.MID_SHARE_COLUMNS, "MiD student share", text_columns=["region"])
    model_level = cli._read_csv(args.model_level, cli.MODEL_LEVEL_COLUMNS, "model level table", text_columns=["code"])
    zensus_students = cli._read_csv(args.zensus_students, E.CENSUS_COLUMNS, "Zensus students", text_columns=["region"])
    reference = cli._read_csv(args.destination, [*cli.DESTINATION_COLUMNS, "se_share"], "SrV destination reference",
                              text_columns=["home_group", "destination_group"])
    srv_distance = cli._read_csv(args.srv_distance, cli.SRV_DISTANCE_COLUMNS, "SrV education distance",
                                 text_columns=["code"])
    age_shares = cli._parameter_shares(parameters, "who", "age_group", "students_present")
    away_share = float(cli._parameter_shares(parameters, "day", "away_all_day", "students_all")["yes"])
    day_factor = float(cli.read_header_value(args.srv_parameters, cli.EDUCATION_LEG_KEY)) * (1.0 - away_share)
    model_today = cli._model_today(model_level)
    kreis_mix = srv_share[srv_share["level"] == "kreis"].set_index("code")["students_weighted_persons"].astype(float)

    prepared = cli.prepare(args)
    region = prepared["region_kreise"]
    site_kreise = frozenset(prepared["site_kreise"])
    braunschweig = SR.BRAUNSCHWEIG_KREIS
    surveyed = [code for code in region if code not in W.UNSURVEYED_KREISE]
    mix = kreis_mix.reindex(surveyed)
    communes = prepared["gemeinden"].assign(ars5=lambda f: f["ags8"].str[:5])
    communes = communes[communes["ewz"] > 0]
    points = prepared["points"].set_index("destination_id")
    max_km = float(args.max_commute_km)

    pooled = reference[reference["home_group"] == SR.HOME_GROUP_ALL].set_index("destination_group")
    bs = reference[reference["home_group"] == SR.HOME_GROUP_BRAUNSCHWEIG].set_index("destination_group")
    n_pooled, n_bs = float(pooled["n_effective"].iloc[0]), float(bs["n_effective"].iloc[0])
    n_distance = n_pooled * SRV_PERSONS_WITH_DISTANCE / SRV_PERSONS_REFERENCE
    band_row = srv_distance[srv_distance["code"] == SRV_BAND_ROW].iloc[0]
    if int(band_row["n_persons"]) != SRV_PERSONS_WITH_DISTANCE:
        raise ValueError("SrV distance row %s has n_persons %s, expected %d" % (SRV_BAND_ROW, band_row["n_persons"],
                                                                                SRV_PERSONS_WITH_DISTANCE))
    srv = {
        "leave_region": float(pooled.loc[OUTSIDE_GROUPS, "share"].sum()),
        "to_region_hannover": float(pooled.loc["region_hannover", "share"]),
        "to_other_outside": float(pooled.loc["other_outside", "share"]),
        "bs_study_in_bs": float(bs.loc[SR.DEST_OWN, "share"]),
        "bs_study_in_wolfenbuettel": float(bs.loc[SR.DEST_SITE_PREFIX + "03158", "share"]),
        "bs_leave_region": float(bs.loc[OUTSIDE_GROUPS, "share"].sum()),
        "distance_25_50_km": float(band_row["share_25_50"]),
        "distance_50_km_or_more": float(band_row["share_50_100"] + band_row["share_100_plus"]),
    }
    srv_se = {
        "leave_region": _standard_error(srv["leave_region"], n_pooled),
        "to_region_hannover": float(pooled.loc["region_hannover", "se_share"]),
        "to_other_outside": float(pooled.loc["other_outside", "se_share"]),
        "bs_study_in_bs": float(bs.loc[SR.DEST_OWN, "se_share"]),
        "bs_study_in_wolfenbuettel": float(bs.loc[SR.DEST_SITE_PREFIX + "03158", "se_share"]),
        "bs_leave_region": _standard_error(srv["bs_leave_region"], n_bs),
        "distance_25_50_km": _standard_error(srv["distance_25_50_km"], n_distance),
        "distance_50_km_or_more": _standard_error(srv["distance_50_km_or_more"], n_distance),
    }

    commune_cache: dict = {}

    def commune_distances(kreis: str, destination: str):
        key = (kreis, destination)
        if key not in commune_cache:
            part = communes[communes["ars5"] == kreis]
            point = points.loc[destination]
            d = np.hypot(part["x"].to_numpy() - point["x"], part["y"].to_numpy() - point["y"]) / 1000.0
            commune_cache[key] = (d, part["ewz"].to_numpy(dtype=float), G.curve_basis(d, max_km))
        return commune_cache[key]

    def band_shares(od: pd.DataFrame, weights, detour: float) -> pd.Series:
        """Pooled share of the surveyed Kreise's students per routed-distance band (local cells: 'local')."""
        rows = od[od["home_in_region"] & od["home_ars5"].isin(surveyed) & (od["students_persons"] > 0)]
        counts = {code: dict.fromkeys(["local", *BAND_LABELS], 0.0) for code in surveyed}
        for row in rows.itertuples(index=False):
            if row.cell_kind == T.KIND_LOCAL:
                counts[row.home_ars5]["local"] += row.students_persons
                continue
            d, ewz, basis = commune_distances(row.home_ars5, row.destination_id)
            commune_weight = ewz if row.cell_kind == T.KIND_MOVER else ewz * (basis @ np.asarray(weights, dtype=float))
            if commune_weight.sum() <= 0:
                commune_weight = ewz
            share = commune_weight / commune_weight.sum()
            band = np.digitize(d * detour, BAND_EDGES_KM)
            for index, label in enumerate(BAND_LABELS):
                counts[row.home_ars5][label] += row.students_persons * share[band == index].sum()
        frame = pd.DataFrame(counts).T
        per_kreis = frame.div(frame.sum(axis=1), axis=0)
        return per_kreis.mul(mix, axis=0).sum() / mix.sum()

    records = []
    for short, middle, long, mover, mover_kreise, label in CANDIDATES:
        weights = (short, middle, long)
        od, _ = prepared["build"](weights)
        od = T.apply_movers(od, mover, mover_kreise, braunschweig)
        tables = T.balance_tables(od, region)
        living = tables["by_home_kreis"].set_index("home_ars5")["students_living_persons"].astype(float)
        fit = W.fit_distance(W.implied_shares(od, site_kreise, kreis_mix), reference[cli.DESTINATION_COLUMNS])
        gate = LV.gate_table(
            E.build_level_gate(srv_share, zensus_students, mid_share, prepared["lsn_sites"][E.SITE_COLUMNS],
                               residents_implied=living.to_dict()),
            srv_share, away_share, age_shares, model_today).set_index("code")

        rows = od[od["home_in_region"] & od["home_ars5"].isin(surveyed)].copy()
        rows["group"] = [W.destination_group(h, s, c) for h, s, c in
                         zip(rows["home_ars5"], rows["site_kreis_ars5"], rows["scope"])]
        table = rows.pivot_table(index="home_ars5", columns="group", values="students_persons", aggfunc="sum",
                                 fill_value=0.0)
        shares = table.div(table.sum(axis=1), axis=0)
        pooled_model = shares.mul(mix, axis=0).sum() / mix.sum()
        bands = band_shares(od, weights, LEGACY_DETOUR_FACTOR)
        model = {
            "leave_region": float(pooled_model.reindex(OUTSIDE_GROUPS).fillna(0.0).sum()),
            "to_region_hannover": float(pooled_model.get("region_hannover", 0.0)),
            "to_other_outside": float(pooled_model.get("other_outside", 0.0)),
            "bs_study_in_bs": float(shares.loc[braunschweig].get(SR.DEST_OWN, 0.0)),
            "bs_study_in_wolfenbuettel": float(shares.loc[braunschweig].get(SR.DEST_SITE_PREFIX + "03158", 0.0)),
            "bs_leave_region": float(shares.loc[braunschweig].reindex(OUTSIDE_GROUPS).fillna(0.0).sum()),
            "distance_25_50_km": float(bands["25_50"]),
            "distance_50_km_or_more": float(bands["50_100"] + bands["100_plus"]),
        }
        by_site = tables["by_site"]
        record = {"label": label, "short": short, "middle": middle, "long": long, "mover_share": mover,
                  "mover_site_kreise": "+".join(mover_kreise), "objective_tvd": fit.objective,
                  "noise_floor_tvd_95": fit.noise_floor}
        for key, value in model.items():
            record["model_" + key] = value
            record["z_" + key] = (value - srv[key]) / srv_se[key]
        record["sum_squared_z_yardsticks"] = float(sum(record["z_" + key] ** 2 for key in model))
        for detour in DETOUR_SENSITIVITY:
            other = band_shares(od, weights, detour)
            record["model_distance_50_km_or_more_detour_%s" % detour] = float(other["50_100"] + other["100_plus"])
        for code in [*surveyed, cli.E.REGION_CODE]:
            record["level_difference_in_srv_se_" + code] = float(gate.loc[code, "difference_in_srv_se"])
        record["students_living_braunschweig_persons"] = float(living[braunschweig])
        record["students_living_surveyed_kreise_persons"] = float(gate.loc[cli.E.REGION_CODE, "balance_students_persons"])
        record["gate_triggered"] = bool(LV.gate_triggered(gate.reset_index()))
        record["in_commuters_all_local_sites_persons"] = float(by_site["in_commuters_persons"].sum())
        record["in_commuters_all_local_sites_persons_per_day"] = record["in_commuters_all_local_sites_persons"] * day_factor
        record["out_commuters_persons"] = float(tables["out_commuters"]["out_commuters_persons"].sum())
        records.append(record)

    header = [
        "# Table: weight_candidates.csv (issue #140, Phase 2). A decision aid on the inputs below, not a validation.",
        "# Generated by docs/runs/artifacts/student-balance-2026-11-26/weights_decision/analyse_weight_candidates.py; "
        "code state eqasim-bs %s." % own.source_commit,
        "# Inputs:", *cli._input_lines([
            *prepared["inputs"], ("srv_destination_reference", Path(args.destination), False),
            ("srv_student_share", Path(args.srv_share), False), ("srv_education_distance", Path(args.srv_distance), False),
            ("srv_student_parameters", Path(args.srv_parameters), False), ("mid_student_share", Path(args.mid_share), False),
            ("zensus_students", Path(args.zensus_students), True), ("model_level", Path(args.model_level), False)]),
        "# Weight rule: weight_rule=%s, max_commute_km=%s; detour factor %s (circuity.LEGACY_DETOUR_FACTOR), "
        "sensitivity columns for %s." % (args.weight_rule, args.max_commute_km, LEGACY_DETOUR_FACTOR,
                                         list(DETOUR_SENSITIVITY)),
        "# SrV yardsticks (pooled shares): " + ", ".join("%s=%.10g" % item for item in srv.items()),
        "# Approximate SrV standard errors (ASSUMPTION, see the script docstring): "
        + ", ".join("%s=%.10g" % item for item in srv_se.items()),
        "# z_<yardstick> = (model - SrV) / standard error; sum_squared_z_yardsticks adds the eight squares (the yardsticks "
        "share respondents, so this ranks candidates and is no test statistic).",
        "# level_difference_in_srv_se_<code>: students living per Kreis, balance minus SrV (students away all day added), "
        "in SrV standard errors; %s = the seven surveyed Kreise." % cli.E.REGION_CODE,
        "# Persons per day = head count times the day factor %.10g (education leg share times one minus the away share)."
        % day_factor,
    ]
    cli._write(pd.DataFrame(records), Path(own.out), header)
    print("wrote %s (%d candidates)" % (Path(own.out).name, len(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
