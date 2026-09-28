"""Zensus 2022 employment-by-age shares: the SHARE of a Kreis's Erwerbstätige in each of the
employment grid's 5 age groups (16_29 / 30_39 / 40_49 / 50_59 / 60plus). Used to distribute the
cleancensus Kreis×sex Erwerbstätige level across age groups (``employment_grid``).

Two references, selected by ``braunschweig.population.popsim.employment_grid_age_shape_source``:

* :func:`load_kreis_sex_age_shares` (default since ADR-0137) reads
  ``zensus2022_employed_by_age_kreis.csv``, written by
  ``scripts/extract_zensus2022_employed_by_age.py`` from the Zensus 2022 Regionaltabelle
  "Bildung und Erwerbstätigkeit" (sheet CSV-ET_Alter). It is exact for EVERY Kreis and gives a
  separate shape per sex; a Kreis absent from it takes the national (``00``) row, which the
  stage reports as a fallback.
* :func:`load_age_shares` (the previous source) reads ``zensus2022_employment_by_age_ref.csv``
  (Zensus 2000S-2001), exact for the kreisfreie Städte (03101/02/03) only; the Landkreise take the
  national (DE large-Gemeinden) share, the same for both sexes.
"""
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

#: File name of the per-Kreis, per-sex reference under ``<data_path>/braunschweig/popsim/``.
KREIS_EMPLOYED_BY_AGE_FILE = "zensus2022_employed_by_age_kreis.csv"

#: The Regionaltabelle's age classes (``ET_ALTER_ERW__1..7``), in source order.
KREIS_AGE_CLASSES = ("15-19", "20-29", "30-39", "40-49", "50-59", "60-67", "68+")

#: Regionaltabelle class -> employment-grid group. 15-19 joins 16_29 the same way the previous
#: reference's 10-19 band did (the grid's population denominator starts at age 16); 60-67 and 68+
#: form 60plus.
_KREIS_CLASS_TO_GROUP = {
    "15-19": "16_29", "20-29": "16_29",
    "30-39": "30_39",
    "40-49": "40_49",
    "50-59": "50_59",
    "60-67": "60plus", "68+": "60plus",
}

#: The Regionaltabelle's code for Germany, the shape a Kreis without its own rows takes.
NATIONAL_REGION = "00"

#: How a Kreis got its shape (reported by the stage).
SHAPE_EXACT = "exact"
SHAPE_NATIONAL = "national"


def load_age_shares(ref_path: str, kreis: str) -> dict[str, float]:
    df = pd.read_csv(ref_path, dtype={"region": str})
    region = kreis if (df["region"] == kreis).any() else "DE_large_gemeinden"
    sub = df[df["region"] == region].copy()
    sub["group"] = sub["age_band"].map(_BAND_TO_GROUP)
    emp = sub.dropna(subset=["group"]).groupby("group")["erwerbstaetige"].sum()
    total = emp.sum()
    return {g: (float(emp.get(g, 0.0)) / total if total > 0 else 0.0)
            for g, _, _ in AGE_GROUPS}


def read_kreis_employed_by_age(path: str) -> pd.DataFrame:
    """The per-Kreis, per-sex reference as written by the extraction script (``#`` header skipped).

    Raises ``ValueError`` for an unknown age class or sex, which would otherwise drop out of every
    share without a trace.
    """
    frame = pd.read_csv(path, comment="#", dtype={"region": str})
    unknown_classes = sorted(set(frame["age_class"]) - set(_KREIS_CLASS_TO_GROUP))
    unknown_sexes = sorted(set(frame["sex"]) - {"M", "F"})
    if unknown_classes or unknown_sexes:
        raise ValueError(f"{path}: unknown age classes {unknown_classes} / sexes {unknown_sexes}; "
                         f"expected {list(KREIS_AGE_CLASSES)} and M/F.")
    return frame


def load_kreis_sex_age_shares(path: str, kreis: str,
                              reference: pd.DataFrame | None = None) -> tuple[dict, str]:
    """``({"M": {group: share}, "F": {group: share}}, method)`` for one Kreis.

    ``method`` is :data:`SHAPE_EXACT` when the Kreis has its own rows and :data:`SHAPE_NATIONAL`
    when it takes Germany's (``00``). Shares sum to 1 per sex. Pass the already-read
    ``reference`` to avoid re-reading the file per Kreis. Raises ``ValueError`` when neither the
    Kreis nor Germany is in the reference, or a sex has no employed persons at all.
    """
    frame = read_kreis_employed_by_age(path) if reference is None else reference
    regions = set(frame["region"])
    if kreis in regions:
        region, method = kreis, SHAPE_EXACT
    elif NATIONAL_REGION in regions:
        region, method = NATIONAL_REGION, SHAPE_NATIONAL
    else:
        raise ValueError(f"{path}: neither Kreis {kreis} nor Germany ({NATIONAL_REGION}) is in the "
                         "employed-by-age reference; re-run scripts/extract_zensus2022_employed_by_age.py "
                         "with that Kreis.")
    rows = frame[frame["region"] == region].copy()
    rows["group"] = rows["age_class"].map(_KREIS_CLASS_TO_GROUP)
    shares = {}
    for sex in ("M", "F"):
        by_group = rows[rows["sex"] == sex].groupby("group")["employed_persons"].sum()
        total = float(by_group.sum())
        if total <= 0:
            raise ValueError(f"{path}: region {region} has no employed persons for sex {sex}.")
        shares[sex] = {group: float(by_group.get(group, 0.0)) / total for group, _, _ in AGE_GROUPS}
    return shares, method


def compare_age_shapes(previous_reference_path: str, kreis_reference_path: str,
                       kreise) -> pd.DataFrame:
    """Previous (sex-pooled, :func:`load_age_shares`) next to new (per sex,
    :func:`load_kreis_sex_age_shares`) age shares for ``kreise``, in percent.

    One row per Kreis, sex and group: ``kreis, sex, group, previous_share_percent,
    new_share_percent, change_percentage_points, new_method``. This is what switching the
    employment grid's age-shape source changes (ADR-0137).
    """
    reference = read_kreis_employed_by_age(kreis_reference_path)
    records = []
    for kreis in kreise:
        previous = load_age_shares(previous_reference_path, kreis)
        new, method = load_kreis_sex_age_shares(kreis_reference_path, kreis, reference=reference)
        for sex in ("M", "F"):
            for group, _lo, _hi in AGE_GROUPS:
                previous_percent = 100.0 * previous[group]
                new_percent = 100.0 * new[sex][group]
                records.append((kreis, sex, group, previous_percent, new_percent,
                                new_percent - previous_percent, method))
    return pd.DataFrame(records, columns=["kreis", "sex", "group", "previous_share_percent",
                                          "new_share_percent", "change_percentage_points",
                                          "new_method"])
