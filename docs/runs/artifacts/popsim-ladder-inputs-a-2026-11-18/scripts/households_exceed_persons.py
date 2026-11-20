"""How often do the integer 100 m targets ask for more households than persons? (60 production batches)"""
import sys
from pathlib import Path

import pandas as pd

from braunschweig.popsim import input_consistency as ic

rows = []
for folder in sorted(Path(sys.argv[1]).glob("batch_*")):
    t = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv", dtype={"ZENSUS100m": str})
    cw = pd.read_csv(folder / "data" / "geo_cross_walk.csv", dtype=str).drop_duplicates("ZENSUS100m")
    t = t.merge(cw[["ZENSUS100m", "ZENSUS1km"]], on="ZENSUS100m", how="left")
    t["hh"] = t[ic.HH_TOTAL_COLUMN]
    t["persons"] = t[list(ic.ALL_BAND_COLUMNS)].sum(axis=1)
    t["batch"] = folder.name
    rows.append(t[["batch", "ZENSUS100m", "ZENSUS1km", "hh", "persons"]])
c = pd.concat(rows, ignore_index=True)
print("cells", len(c), "households", int(c.hh.sum()), "persons", int(c.persons.sum()))
for label, mask in (("hh > persons, persons > 0", (c.hh > c.persons) & (c.persons > 0)),
                    ("hh > 0, persons = 0", (c.hh > 0) & (c.persons == 0)),
                    ("hh > persons (all)", c.hh > c.persons)):
    d = c[mask]
    print(f"{label}: cells {len(d)}, excess households {int((d.hh - d.persons).sum())}, "
          f"batches {d.batch.nunique()}")
big = c[(c.hh - c.persons) >= 5].assign(excess=lambda d: d.hh - d.persons).sort_values("excess", ascending=False)
print("cells with excess >= 5:", len(big), "excess", int(big.excess.sum()))
print(big.head(12).to_string(index=False))
s = c.groupby(["batch", "ZENSUS1km"])[["hh", "persons"]].sum()
sq = s[s.hh > s.persons]
print(f"1 km squares with hh > persons: {len(sq)} of {len(s)}, excess {int((sq.hh - sq.persons).sum())}, "
      f"batches {sq.index.get_level_values(0).nunique()}")
print(sq.assign(excess=sq.hh - sq.persons).sort_values("excess", ascending=False).head(10).to_string())
