"""Donor reuse per household-size class for the October run (old) and the full rerun (new) (issue #648).

Joins each run's per-donor copy counts (zone_views.py donor_usage.csv) with the seed households of the run's first
batch folder (all 60 batches share one seed, popsim_batch_diagnostics seed_groups) and the number of seed persons per
household. Size classes 1, 2, 3, 4, 5 and 6+ persons.

Usage (eqasim conda env):
    python reuse_by_household_size.py OLD_WORK_DIR OLD_DONOR_USAGE NEW_WORK_DIR NEW_DONOR_USAGE OUT_CSV

Columns: run, size_class, n_seed_households, n_used, share_used, copies, max_copies, kish_n_eff
(kish_n_eff = (sum of copies)^2 / sum of copies^2 over the used seed households of the class).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("reuse_by_household_size")
SIZE_CLASSES = ["1", "2", "3", "4", "5", "6+"]


def size_class(n_persons: pd.Series) -> pd.Series:
    return n_persons.clip(upper=6).astype(int).astype(str).replace({"6": "6+"})


def reuse_by_size(work_dir: Path, donor_usage: Path, run: str) -> pd.DataFrame:
    batch = sorted(p for p in work_dir.glob("batch_*") if p.is_dir())[0]
    settings = yaml.safe_load((batch / "configs" / "settings.yaml").read_text(encoding="utf-8"))
    id_col = settings.get("household_id_col", "hh_id")
    seed = pd.read_csv(batch / "data" / "seed_households.csv", usecols=[id_col]).drop_duplicates()
    persons = pd.read_csv(batch / "data" / "seed_persons.csv", usecols=[id_col])
    n_persons = persons.groupby(id_col).size()
    missing = ~seed[id_col].isin(n_persons.index)
    if missing.any():
        raise ValueError(f"{run}: {int(missing.sum())} seed households without seed persons")
    seed["size_class"] = size_class(seed[id_col].map(n_persons))
    usage = pd.read_csv(donor_usage).set_index("seed_household_id")["copies"]
    unknown = ~usage.index.isin(seed[id_col])
    if unknown.any():
        raise ValueError(f"{run}: {int(unknown.sum())} used donors are not seed households of {batch.name}")
    seed["copies"] = seed[id_col].map(usage).fillna(0).astype(np.int64)
    rows = []
    for cls in SIZE_CLASSES:
        part = seed[seed["size_class"] == cls]
        used = part.loc[part["copies"] > 0, "copies"].to_numpy(dtype=float)
        rows.append({"run": run, "size_class": cls, "n_seed_households": len(part), "n_used": len(used),
                     "share_used": len(used) / len(part) if len(part) else np.nan,
                     "copies": int(used.sum()), "max_copies": int(used.max()) if len(used) else 0,
                     "kish_n_eff": used.sum() ** 2 / (used ** 2).sum() if len(used) else np.nan})
    logger.info("%s: %d seed households, %d used", run, len(seed), int((seed["copies"] > 0).sum()))
    return pd.DataFrame(rows)


def main(old_work: Path, old_usage: Path, new_work: Path, new_usage: Path, out_csv: Path) -> None:
    table = pd.concat([reuse_by_size(old_work, old_usage, "old"), reuse_by_size(new_work, new_usage, "new")])
    table.to_csv(out_csv, index=False)
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*(Path(a) for a in sys.argv[1:]))
