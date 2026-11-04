#!/usr/bin/env python
"""Key and coverage check of the committed FZ 27.17 table (issue C5b, 2026-11-04).

Answers, with the production code and not a re-implementation:

1. How many rows does ``kba_gemeinde_private_bev.csv`` have, and is any
   ``(kreis_ags5, gemeinde)`` key repeated, as a raw key and as the key the consumers use
   (``kreis_ags5`` plus ``normalize_gemeinde_name(gemeinde)``)?
2. Which cells are empty?
3. Which Gemeinden get a composition factor from ``_gemeinde_electric_composition``, and
   which Kreise does ``_single_gemeinde_kreise`` call sole-Gemeinde?

Usage (from the repository root, with the project's Python environment)::

    python docs/runs/artifacts/prod100-fleet-composition-accounting-2026-11-04/check_fz2717_table.py \\
        eqasim-data/data/braunschweig/kba/derived/kba_gemeinde_private_bev.csv
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(path: str) -> None:
    raw_bytes = Path(path).read_bytes().replace(b"\r\n", b"\n")
    print("file: %s" % path)
    print("sha256 of the table with LF line endings (the committed blob): %s"
          % hashlib.sha256(raw_bytes).hexdigest())

    df = pd.read_csv(path, dtype={"kreis_ags5": str})
    df["gemeinde_norm"] = df["gemeinde"].map(fs.normalize_gemeinde_name)
    print("data rows: %d" % len(df))
    print("distinct (kreis_ags5, gemeinde): %d"
          % df[["kreis_ags5", "gemeinde"]].drop_duplicates().shape[0])
    print("distinct (kreis_ags5, normalised gemeinde): %d"
          % df[["kreis_ags5", "gemeinde_norm"]].drop_duplicates().shape[0])
    print("fully identical rows: %d" % int(df.duplicated(keep=False).sum()))
    print("rows per Kreis: %s" % df.groupby("kreis_ags5").size().to_dict())
    print("rows with an empty normalised name: %d" % int((df["gemeinde_norm"] == "").sum()))

    print("")
    for column in ("private_total", "private_bev", "private_phev"):
        empty = df[df[column].isna()]
        print("empty %s: %d %s" % (column, len(empty),
                                   list(zip(empty["kreis_ags5"], empty["gemeinde"]))))

    print("")
    per_gemeinde, _per_kreis = fs._gemeinde_electric_composition(df)
    print("Gemeinden with a composition factor: %d of %d" % (len(per_gemeinde), len(df)))
    for kreis in sorted(fs._single_gemeinde_kreise(df)):
        entries = {key[1]: {pt: repr(value) for pt, value in factors.items()}
                   for key, factors in per_gemeinde.items() if key[0] == kreis}
        print("sole-Gemeinde Kreis %s: factor entry %s" % (kreis, entries or "none"))


if __name__ == "__main__":
    main(sys.argv[1])
