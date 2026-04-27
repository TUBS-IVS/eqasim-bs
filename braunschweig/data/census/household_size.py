"""
Household-size distribution for the Braunschweig region from Zensus 2022
table 5000H-2001 (Haushalte × Gemeinde × Haushaltsgröße × Haushaltstyp).

This replaces the earlier MiD-based stub and the Bavaria-specific GENESIS
file 12211-105.xlsx. Zensus 2022 is a full-count (registergestützt) source
with two orders of magnitude more data points than the MiD regional sample.

Schema out (matches bavaria/data/census/household_size.py):
    lower_age, upper_age, sex, household_size, weight

The downstream consumer (bavaria.synthesis.population.enriched) samples
household_size per (lower_age, upper_age, sex) group. Zensus 2022 does
not cross-tabulate HH-size with sex or age, so we emit the same size
distribution for every (sex, age) stratum. The `<16 years → no 1P-HH`
override in enriched.py relies on a (0, minimum_age) stratum being
present, which we provide.
"""

from __future__ import annotations

import os

import pandas as pd


# Bavaria's 5-bin scheme would merge 5P + 6+P. R-C uses Zensus 2022's native
# 6 bins (1, 2, 3, 4, 5, 6+) so the IPF target preserves the long tail of
# the household-size distribution. Third tuple element = representative
# persons-per-HH (for person weighting).
SIZE_BINS = [
    ("1",  ["1 Person"],                          1.0),
    ("2",  ["2 Personen"],                        2.0),
    ("3",  ["3 Personen"],                        3.0),
    ("4",  ["4 Personen"],                        4.0),
    ("5",  ["5 Personen"],                        5.0),
    ("6+", ["6 und mehr Personen"],               6.5),
]

# Age strata — boundary at minimum_age.one_person_household (default 16).
AGE_STRATA = [(0, 16), (16, 150)]


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.zensus_households_path",
        "braunschweig/5000H-2001_de_flat.csv",
    )
    context.config("bavaria.political_prefix")
    context.config("bavaria.minimum_age.one_person_household", 16)


def _path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.zensus_households_path"),
    )


def _load_region_distribution(path: str, scope_prefixes):
    """Return dict[size_bin] -> household_count summed over the scope."""
    usecols = [
        "1_variable_attribute_code",     # 12-digit ARS (Gemeinde)
        "2_variable_attribute_code",     # HSHGR2 code (PERSON01..06, NaN=Insgesamt)
        "2_variable_attribute_label",    # "1 Person", ...
        "3_variable_attribute_code",     # HSHTP1 code (NaN=Insgesamt over types)
        "value",
    ]
    df = pd.read_csv(path, sep=";", dtype=str, usecols=usecols)
    df = df.rename(columns={
        "1_variable_attribute_code":  "ars12",
        "2_variable_attribute_code":  "size_code",
        "2_variable_attribute_label": "size_label",
        "3_variable_attribute_code":  "type_code",
    })

    df["ars5"] = df["ars12"].str[:5]
    df = df[df["ars5"].isin(scope_prefixes)]

    # Keep only rows where type is aggregated (Insgesamt) and size is a
    # concrete class (not Insgesamt). Gives one row per Gemeinde × size.
    df = df[
        df["type_code"].isna()
        & df["size_code"].notna()
        & df["size_label"].ne("Insgesamt")
    ]

    # Zensus value_q='e' marks suppressed cells; value becomes '-' → NaN → 0.
    df["count"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)

    counts = {}
    for bin_name, labels, _ in SIZE_BINS:
        counts[bin_name] = float(df.loc[df["size_label"].isin(labels), "count"].sum())

    if sum(counts.values()) <= 0:
        raise RuntimeError(
            f"No Zensus households found for scope {scope_prefixes} in {path}"
        )
    return counts


def execute(context) -> pd.DataFrame:
    scope = [str(p) for p in context.config("bavaria.political_prefix")]
    hh_counts = _load_region_distribution(_path(context), scope)

    # Person-weighted share of each size bin.
    person_weights = {
        bin_name: hh_counts[bin_name] * mid
        for bin_name, _, mid in SIZE_BINS
    }
    total_persons = sum(person_weights.values())
    total_hh = sum(hh_counts.values())
    mean_size = total_persons / total_hh

    print(
        "[braunschweig.data.census.household_size] Zensus 2022: "
        "{:,.0f} HHs in scope, mean size {:.2f}, shares ".format(total_hh, mean_size)
        + ", ".join(f"{k}={hh_counts[k]/total_hh:.1%}" for k, _, _ in SIZE_BINS)
    )

    rows = []
    for sex in ("male", "female"):
        for lower_age, upper_age in AGE_STRATA:
            for bin_name, _, _ in SIZE_BINS:
                rows.append({
                    "lower_age": lower_age,
                    "upper_age": upper_age,
                    "sex": sex,
                    "household_size": bin_name,
                    "weight": person_weights[bin_name] / total_persons,
                })

    df = pd.DataFrame(rows)
    df["household_size"] = df["household_size"].astype("category")
    df["sex"] = df["sex"].astype("category")
    return df[["lower_age", "upper_age", "sex", "household_size", "weight"]]


def validate(context):
    path = _path(context)
    if not os.path.exists(path):
        raise RuntimeError(f"Missing Zensus 2022 households file: {path}")
    return os.path.getsize(path)
