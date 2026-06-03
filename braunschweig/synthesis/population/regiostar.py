"""
Persist the BBSR/BMV RegioStaR-7 type per synthetic person (TASK-004).

This stage joins the home commune_id onto persons via the household
home location and looks up ``regiostar7`` from the
``braunschweig.data.bbsr.regiostar`` reference. Downstream calibration
(TASK-008 mode-choice MNL) can stratify utility coefficients by
urban/suburban/rural without pulling the Excel file again.

Output
------
``DataFrame`` with columns:

    - ``person_id``    integer person identifier (matches enriched).
    - ``regiostar7``   nullable Int64; NaN where the home commune is
                       outside the reference (external pendler residents).

The stage is intentionally narrow — it does not mutate the enriched
persons frame to avoid a cyclic dependency with
``synthesis.population.spatial.home.locations`` (which itself reads
enriched persons).
"""

from __future__ import annotations

import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("braunschweig.data.bbsr.regiostar")


def execute(context) -> pd.DataFrame:
    df_persons = context.stage("synthesis.population.enriched")[[
        "person_id", "household_id",
    ]]
    df_home = context.stage("synthesis.population.spatial.home.locations")[[
        "household_id", "commune_id",
    ]].drop_duplicates()
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")[[
        "commune_id", "regiostar7",
    ]]

    df = df_persons.merge(df_home, on="household_id", how="left")
    # ``home.locations`` carries the 12-digit ARS while the RegioStaR-7 table
    # keys on the 8-digit AGS; convert before merging or every person would
    # silently fall back to NaN (the same ARS12->AGS8 step the education
    # gravity stage applies).
    df["commune_id"] = df["commune_id"].map(ars_to_ags8)
    df = df.merge(df_regiostar, on="commune_id", how="left")

    n_total = len(df)
    n_unmatched = int(df["regiostar7"].isna().sum())
    print(
        "[braunschweig.synthesis.population.regiostar] "
        f"{n_total - n_unmatched}/{n_total} persons matched "
        f"({n_unmatched} external/unknown commune_id)"
    )

    return df[["person_id", "regiostar7"]]
