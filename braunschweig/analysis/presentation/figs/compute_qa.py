# Control-fit audit for the PopulationSim 100% run (popsim_work_allfeat_opt).
# Per batch: evaluate each control expression on the synthetic side
# (synthetic_households joined to seed tables via H_ID; persons reconstructed
# through synthetic_households x seed_persons because synthetic persons carry
# no attributes) and compare per-cell counts against the control targets at
# ZENSUS100m (native), ZENSUS1km (native totals file) and KREIS (native for
# KREIS controls; crosswalk-aggregated 100m targets for grid controls).
#
# Output: one aggregate CSV row per (batch, control_base, level) with
# count sums so that a target-count-weighted mean absolute percentage
# deviation can be formed exactly across batches:
#   wmape = sum(absdev_pos_sum) / sum(target_sum)   (cells with target > 0)
import os
import sys
import glob
import numpy as np
import pandas as pd

WORK = "/home/felix/eqasim-bs/eqasim-data/popsim_work_allfeat_opt"
OUT = "/home/felix/tmp_qa100m/qa100m_aggregate.csv"

GEO_SUFFIXES = ("_ZENSUS100m", "_ZENSUS1km", "_KREIS")


def base_name(field):
    for s in GEO_SUFFIXES:
        if field.endswith(s):
            return field[: -len(s)]
    return field


def family_of(base):
    if base.startswith("Insgesamt_Haushalte"):
        return "hh_total"
    if base.startswith(("M_AGE", "F_AGE")):
        return "age_sex"
    if "Groesse_des_privaten_Haushalts" in base:
        return "hh_size"
    if "Typ_priv_HH_Familie" in base:
        return "hh_type"
    if "Tenure" in base:
        return "tenure"
    if base.startswith("building_type"):
        return "building_type"
    if base.startswith(("EMPLOYED_", "employed")):
        return "employment"
    if base.startswith(("schulabschluss", "beruflabschluss")):
        return "education"
    return "other"


def norm_kreis(series):
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)


def cell_stats(syn_counts, targets):
    # union of cells; targets define the weighting universe
    df = pd.concat([targets.rename("t"), syn_counts.rename("s")], axis=1).fillna(0.0)
    pos = df[df["t"] > 0]
    absdev_pos = (pos["s"] - pos["t"]).abs()
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = absdev_pos / pos["t"]
    return {
        "n_cells_union": len(df),
        "n_cells_pos": len(pos),
        "target_sum": float(df["t"].sum()),
        "syn_sum": float(df["s"].sum()),
        "absdev_pos_sum": float(absdev_pos.sum()),
        "syn_in_zero_target_cells": float(df.loc[df["t"] <= 0, "s"].sum()),
        "max_pct_dev": float(pct.max()) if len(pos) else np.nan,
        "n_cells_exact": int((absdev_pos < 1e-9).sum()),
    }


def main():
    rows = []
    batches = sorted(glob.glob(os.path.join(WORK, "batch_*")))
    print("batches found: %d" % len(batches), flush=True)
    for bdir in batches:
        bname = os.path.basename(bdir)
        try:
            controls = pd.read_csv(os.path.join(bdir, "configs", "controls.csv"))
            t100 = pd.read_csv(os.path.join(bdir, "data", "control_totals_ZENSUS100m.csv")).set_index("ZENSUS100m")
            t1k = pd.read_csv(os.path.join(bdir, "data", "control_totals_ZENSUS1km.csv")).set_index("ZENSUS1km")
            tkr = pd.read_csv(os.path.join(bdir, "data", "control_totals_KREIS.csv"), dtype={"KREIS": str})
            tkr["KREIS"] = norm_kreis(tkr["KREIS"])
            tkr = tkr.set_index("KREIS")
            xwalk = pd.read_csv(os.path.join(bdir, "data", "geo_cross_walk.csv"), dtype=str)
            xwalk["KREIS"] = norm_kreis(xwalk["KREIS"])
            cell2kreis = xwalk.set_index("ZENSUS100m")["KREIS"]

            seed_hh = pd.read_csv(os.path.join(bdir, "data", "seed_households.csv"))
            seed_hh["H_ID"] = pd.to_numeric(seed_hh["H_ID"]).round().astype("int64")
            seed_p = pd.read_csv(os.path.join(bdir, "data", "seed_persons.csv"))
            seed_p["H_ID"] = pd.to_numeric(seed_p["H_ID"]).round().astype("int64")

            syn_hh = pd.read_csv(os.path.join(bdir, "output", "synthetic_households.csv"))
            syn_hh["H_ID"] = pd.to_numeric(syn_hh["H_ID"]).round().astype("int64")
            syn_hh["KREIS"] = norm_kreis(syn_hh["KREIS"])

            hh = syn_hh.merge(seed_hh, on="H_ID", how="left", suffixes=("", "_seed"))
            n_missing = hh["H_GR"].isna().sum()
            if n_missing:
                print("WARN %s: %d synthetic households without seed match" % (bname, n_missing), flush=True)

            geo_cols = ["ZENSUS100m", "ZENSUS1km", "KREIS"]
            persons = syn_hh[["household_id", "H_ID"] + geo_cols].merge(seed_p, on="H_ID", how="left")

            # cross-check person expansion against the official synthetic_persons row count
            n_syn_p = pd.read_csv(os.path.join(bdir, "output", "synthetic_persons.csv"), usecols=["household_id"]).shape[0]
            if n_syn_p != len(persons):
                print("WARN %s: reconstructed persons %d != synthetic_persons rows %d" % (bname, len(persons), n_syn_p), flush=True)

            env_tables = {"households": hh, "persons": persons}
            seen = set()
            for _, c in controls.iterrows():
                geo = c["geography"]
                field = c["control_field"]
                base = base_name(field)
                expr = c["expression"]
                table = env_tables[c["seed_table"]]
                mask = eval(expr, {"np": np}, {"households": hh, "persons": persons})
                vals = mask.astype(float)

                jobs = []  # (level, target_series, geo_col_series, native)
                if geo == "ZENSUS100m":
                    jobs.append(("ZENSUS100m", t100[field], table["ZENSUS100m"], True))
                    f1k = base + "_ZENSUS1km"
                    if f1k in t1k.columns:
                        jobs.append(("ZENSUS1km", t1k[f1k], table["ZENSUS1km"], False))
                    # KREIS level: aggregate the 100m targets through the crosswalk
                    tk = t100[field].groupby(cell2kreis.reindex(t100.index)).sum()
                    jobs.append(("KREIS", tk, table["KREIS"], False))
                elif geo == "ZENSUS1km":
                    jobs.append(("ZENSUS1km", t1k[field], table["ZENSUS1km"], True))
                    tk = t1k[field].groupby(
                        xwalk.drop_duplicates("ZENSUS1km").set_index("ZENSUS1km")["KREIS"].reindex(t1k.index)
                    ).sum()
                    jobs.append(("KREIS", tk, table["KREIS"], False))
                elif geo == "KREIS":
                    jobs.append(("KREIS", tkr[field], table["KREIS"], True))
                else:
                    continue

                for level, tgt, geo_series, native in jobs:
                    key = (base, level)
                    if key in seen and not native:
                        continue
                    if key in seen and native:
                        # native wins: drop the derived row added earlier
                        rows[:] = [r for r in rows if not (r["batch"] == bname and r["control_base"] == base and r["level"] == level)]
                    seen.add(key)
                    syn_counts = vals.groupby(geo_series).sum()
                    st = cell_stats(syn_counts, tgt)
                    st.update({
                        "batch": bname,
                        "control_base": base,
                        "family": family_of(base),
                        "level": level,
                        "native": native,
                        "importance": c["importance"],
                    })
                    rows.append(st)
            print("done %s (%d hh, %d persons)" % (bname, len(hh), len(persons)), flush=True)
        except Exception as e:
            print("ERROR %s: %r" % (bname, e), flush=True)

    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_csv(OUT, index=False)
    print("wrote %s (%d rows)" % (OUT, len(out)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
