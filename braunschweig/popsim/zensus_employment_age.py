"""Zensus 2000S-2001 employment-by-age shares: the SHARE of total Erwerbstätige in each
of 5 age groups (16_29 / 30_39 / 40_49 / 50_59 / 60plus), per Kreis. Exact for the
kreisfreie Städte (03101/02/03); the Landkreise fall back to the national (DE large-Gemeinden)
share. Used to distribute the cleancensus Kreis×sex Erwerbstätige level across age groups."""
from __future__ import annotations
import pandas as pd

AGE_GROUPS = (
    ("16_29", 16, 29),
    ("30_39", 30, 39),
    ("40_49", 40, 49),
    ("50_59", 50, 59),
    ("60plus", 60, 200),
)
# 2000S-2001 10-year bands -> our 5 groups (band lower-edge based; <16 excluded upstream).
_BAND_TO_GROUP = {
    "10-19": "16_29", "20-29": "16_29",
    "30-39": "30_39",
    "40-49": "40_49",
    "50-59": "50_59",
    "60-69": "60plus", "70-79": "60plus", "80+": "60plus",
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
