"""
INKAR household-income stage.

Reads the BBSR INKAR export ``E_Haushaltseinkommen.xls`` ("Durchschnittliches
Haushaltseinkommen in € je Einwohner", Kreis-level, monthly) and returns a
per-Kreis multiplicative shift factor relative to the national mean.

The MiD 2023 H4 quintile distribution used in
``braunschweig.data.census.household_income`` is regionless — it mixes all
eight ZGB Kreise into a single 5×5 table. INKAR provides the missing Kreis
dimension as a continuous income level, which
``braunschweig.synthesis.population.enriched`` applies as a multiplicative
scale on the class midpoints so that downstream income-sensitive models
(MATSim mode choice) see the Wolfsburg/Goslar spread.

Schema out:
    ars5 (str, 5-digit Kennziffer)  — e.g. "03101"
    einkommen_eur (float, €/EW/month)
    scale (float, einkommen_eur / de_mean)
"""

from __future__ import annotations

import os

import pandas as pd


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.inkar_household_income_path",
        "braunschweig/E_Haushaltseinkommen.xls",
    )
    context.config("braunschweig.inkar_year", "latest")
    # Optional scope (list of 5-digit Kreis prefixes). If absent, all Kreise
    # from the file are returned; downstream only reads the 8 ZGB Kreise.
    context.config("braunschweig.political_prefix", None)


def _path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.inkar_household_income_path"),
    )


def _load(path: str, year_cfg):
    # Multi-index header: ('Haushaltseinkommen', '2000'), ..., ('Raumeinheit','').
    df = pd.read_excel(path, sheet_name="Daten", header=[0, 1])
    lvl0 = df.columns.get_level_values(0)
    lvl1 = df.columns.get_level_values(1)

    years = sorted(
        str(y) for l0, y in zip(lvl0, lvl1)
        if isinstance(l0, str) and "Haushaltseinkommen" in l0 and pd.notna(y)
    )
    if not years:
        raise RuntimeError(f"No Haushaltseinkommen columns found in {path}")

    target_year = years[-1] if year_cfg in (None, "latest") else str(year_cfg)
    if target_year not in years:
        raise RuntimeError(
            f"INKAR year '{target_year}' not found in {path}; have {years}"
        )

    # Flatten columns for easy access.
    df.columns = [
        str(l0).strip() if pd.isna(l1) or str(l1).startswith("Unnamed")
        else str(l1)
        for l0, l1 in zip(lvl0, lvl1)
    ]

    keep = ["Kennziffer", "Raumeinheit", "Aggregat", target_year]
    df = df[keep].copy()
    df.columns = ["kennziffer", "raumeinheit", "aggregat", "einkommen_eur"]

    df = df[df["aggregat"] == "Kreise"].copy()
    df["ars5"] = df["kennziffer"].astype(str).str.zfill(5)
    df["einkommen_eur"] = pd.to_numeric(df["einkommen_eur"], errors="coerce")
    n_before = len(df)
    df = df.dropna(subset=["einkommen_eur"])
    # BUG-007 fix: if the INKAR export ships only "N.A." placeholders for the
    # selected year (happens with old/preliminary releases), to_numeric+dropna
    # silently empties the frame and de_mean becomes NaN, which propagates as
    # 100 % NaN income downstream. Fail loud instead.
    if len(df) == 0:
        raise RuntimeError(
            f"INKAR household-income table for year '{target_year}' has no "
            f"numeric values after dropna ({n_before} Kreis rows before, 0 after). "
            f"Check {path} - the source may contain only 'N.A.' placeholders."
        )

    de_mean = df["einkommen_eur"].mean()
    df["scale"] = df["einkommen_eur"] / de_mean

    return df[["ars5", "raumeinheit", "einkommen_eur", "scale"]], target_year, de_mean


def execute(context) -> pd.DataFrame:
    df, year, de_mean = _load(_path(context), context.config("braunschweig.inkar_year"))

    scope = context.config("braunschweig.political_prefix")
    if scope:
        scope = [str(p) for p in scope]
        in_scope = df[df["ars5"].isin(scope)].copy()
        print(
            "[braunschweig.data.inkar.household_income] INKAR {} — DE-Mean {:.0f} €; "
            "scope scale range {:.3f}..{:.3f}".format(
                year, de_mean,
                in_scope["scale"].min(), in_scope["scale"].max(),
            )
        )

    return df.reset_index(drop=True)


def validate(context):
    path = _path(context)
    if not os.path.exists(path):
        raise RuntimeError(f"Missing INKAR household-income file: {path}")
    return os.path.getsize(path)
