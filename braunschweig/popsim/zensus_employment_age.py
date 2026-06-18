"""Zensus 2000S-2001 employment-by-age shares: the SHARE of total Erwerbstätige in each
of 3 coarse age groups (young 16-29 / prime 30-59 / old 60+), per Kreis. Exact for the
kreisfreie Städte (03101/02/03); the Landkreise fall back to the national (DE large-Gemeinden)
share. Used to distribute the cleancensus Kreis×sex Erwerbstätige level across age groups."""
from __future__ import annotations
import pandas as pd

AGE_GROUPS = (("young", 16, 29), ("prime", 30, 59), ("old", 60, 200))
# 2000S-2001 10-year bands -> our 3 groups (band lower-edge based; <16 excluded upstream).
_BAND_TO_GROUP = {
    "10-19": "young", "20-29": "young",
    "30-39": "prime", "40-49": "prime", "50-59": "prime",
    "60-69": "old", "70-79": "old", "80+": "old",
}


def load_age_shares(ref_path: str, kreis: str) -> dict[str, float]:
    df = pd.read_csv(ref_path, dtype={"region": str})
    region = kreis if (df["region"] == kreis).any() else "DE_large_gemeinden"
    sub = df[df["region"] == region].copy()
    sub["group"] = sub["age_band"].map(_BAND_TO_GROUP)
    emp = sub.dropna(subset=["group"]).groupby("group")["erwerbstaetige"].sum()
    total = emp.sum()
    return {g: (float(emp.get(g, 0.0)) / total if total > 0 else 0.0)
            for g, _, _ in AGE_GROUPS}
