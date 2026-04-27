"""Verify MiD 2023 CSV loaders: columns, ZGB Kreis coverage, totals."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MID_DIR = ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"
ZGB = {"03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"}


def check(name: str, required_cols: list[str]):
    path = MID_DIR / f"mid2023_{name}.csv"
    print(f"\n=== {name}  ({path.name}) ===")
    df = pd.read_csv(path)
    df["ars5"] = df["ars5"].astype(str)
    missing = [c for c in required_cols if c not in df.columns]
    print(f"    cols={list(df.columns)}")
    if missing:
        print(f"    MISSING columns: {missing}")
    kreis = set(df["ars5"].unique()) - {"03ZGB", "Gesamt", "nan"}
    print(f"    ars5 rows: kreis={len(kreis)}  missing_ZGB={ZGB - kreis}  extra={kreis - ZGB}")
    print(f"    has_Gesamt='03ZGB': {'03ZGB' in df['ars5'].unique()}")
    nums = df.select_dtypes(include=[np.number])
    print(f"    numeric-col sums per Kreis (first row):\n{df.head(1).T}")


# Column expectations per docstring in references.py
check("P9",    ["ars5", "kreis", "n_weighted", "vollzeit", "nicht_erwerbstaetig"])
check("P12_1", ["ars5", "kreis", "zu_fuss", "fahrrad", "oeffentlich", "auto"])
check("P13",   ["ars5", "kreis", "d_0", "d_0_5", "d_5_10", "d_10_20", "d_20_30",
                "d_30_50", "d_50_100", "d_100p", "mittel"])
check("P17_1", ["ars5", "kreis", "ja", "nein", "keine_angabe"])
