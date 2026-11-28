#!/usr/bin/env python3
"""Pick the smoke batch of the student synthesis bundle (design A3, last bullet; ADR-0211).

Read-only over the batch folders the PopulationSim stage wrote in smoke mode. For every batch folder:
the persons aged 18 to 29 (M/F_AGE_18_19 + M/F_AGE_20_29 of control_totals_ZENSUS100m.csv) over the
persons aged 18 or older (the same 18_19 ... 80_plus bands) in the 100 m cells whose KREIS in
geo_cross_walk.csv is the Kreis asked for (default 03101, Braunschweig). Writes one CSV row per batch
(also batches without a part in that Kreis, with NaN) and prints the batch with the highest share.

    python pick_batch.py <popsim work dir> <out.csv> [--kreis 03101]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

ADULT_BANDS = ("18_19", "20_29", "30_39", "40_49", "50_59", "60_69", "70_79", "80_plus")
YOUNG_BANDS = ("18_19", "20_29")


def band_columns(columns, bands):
    wanted = []
    for sex in ("M", "F"):
        for band in bands:
            name = "%s_AGE_%s_agg_ZENSUS100m" % (sex, band)
            if name not in columns:
                raise SystemExit("missing column %s" % name)
            wanted.append(name)
    return wanted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("work_dir")
    parser.add_argument("out_csv")
    parser.add_argument("--kreis", default="03101")
    args = parser.parse_args(argv)

    rows = []
    batches = sorted(p for p in Path(args.work_dir).iterdir() if re.fullmatch(r"batch_\d+", p.name))
    for batch in batches:
        data = batch / "data"
        cells = pd.read_csv(data / "control_totals_ZENSUS100m.csv")
        walk = pd.read_csv(data / "geo_cross_walk.csv", dtype={"KREIS": str}, usecols=["ZENSUS100m", "KREIS"])
        merged = cells.merge(walk, on="ZENSUS100m", how="left", validate="1:1")
        if merged["KREIS"].isna().any():
            raise SystemExit("%s: %d cell(s) without a Kreis in the crosswalk" % (batch.name, merged["KREIS"].isna().sum()))
        part = merged[merged["KREIS"] == args.kreis]
        adults = float(part[band_columns(cells.columns, ADULT_BANDS)].to_numpy().sum())
        young = float(part[band_columns(cells.columns, YOUNG_BANDS)].to_numpy().sum())
        rows.append({"batch": batch.name, "kreis": args.kreis, "n_cells_in_kreis": int(len(part)),
                     "persons_18plus": adults, "persons_18_29": young,
                     "share_18_29_among_18plus": young / adults if adults > 0 else float("nan"),
                     "kreise_in_batch": " ".join(sorted(set(merged["KREIS"])))})
    table = pd.DataFrame(rows)
    table.to_csv(args.out_csv, index=False, lineterminator="\n", float_format="%.10g")
    best = table.loc[table["share_18_29_among_18plus"].idxmax()]
    print("batches: %d, with a %s part: %d; highest share: %s (%.6f, %d persons 18+)"
          % (len(table), args.kreis, int((table["n_cells_in_kreis"] > 0).sum()), best["batch"],
             best["share_18_29_among_18plus"], best["persons_18plus"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
