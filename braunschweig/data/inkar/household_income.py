"""
INKAR household-income stage.

Reads the BBSR INKAR export ``E_Haushaltseinkommen.xls`` ("Durchschnittliches
Haushaltseinkommen in € je Einwohner", Kreis-level, monthly) and returns a
per-Kreis multiplicative shift factor relative to the national mean.

The national mean is the mean of the Kreis values weighted with the DESTATIS
12411-0018 Kreis population (ADR-0191): the indicator is income per inhabitant,
so the weighted mean is the national value, which the INKAR export itself does
not carry (it has Kreis rows only). ASSUMPTION: the population is the 31.12.2024
DESTATIS stock while the income year is the INKAR year read here (its vintage
belongs to the data record); the difference in population year is not corrected.

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
    scale (float, einkommen_eur / national_mean)
"""

from __future__ import annotations

import os

import pandas as pd

from braunschweig import synpp_tokens
from braunschweig.data.census import population as _population

#: Module-level helper whose source changes this stage's result (the DESTATIS parser).
_HELPER_MODULES = (_population,)


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.inkar_household_income_path",
        "braunschweig/E_Haushaltseinkommen.xls",
    )
    context.config("braunschweig.inkar_year", "latest")
    # DESTATIS 12411-0018 Kreis population, the weights of the national mean (ADR-0191).
    context.config("braunschweig.destatis_population_path",
                   _population.DEFAULT_DESTATIS_POPULATION_PATH)
    # Optional scope (list of 5-digit Kreis prefixes). If absent, all Kreise
    # from the file are returned; downstream only reads the 8 ZGB Kreise.
    context.config("braunschweig.political_prefix", None)


def _path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.inkar_household_income_path"),
    )


def _population_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.destatis_population_path"),
    )


def kreis_income_scale(df: pd.DataFrame, population_by_kreis: pd.Series):
    """Per-Kreis ``scale`` relative to the population-weighted national mean.

    Args:
        df: INKAR Kreis rows with ``ars5`` and ``einkommen_eur`` (EUR/inhabitant/month).
        population_by_kreis: inhabitants per 5-digit Kreis key (index ``ars5``).

    Returns:
        ``(df with a 'scale' column, national_mean_eur)``.

    Raises:
        ValueError: if a Kreis has no positive population. The national reference
            would otherwise silently shift, so there is no fallback for it.
    """
    weights = df["ars5"].map(population_by_kreis)
    missing = df.loc[~(weights > 0), "ars5"].tolist()
    if missing:
        raise ValueError(
            f"[braunschweig.data.inkar.household_income] {len(missing)}/{len(df)} INKAR Kreise "
            f"have no DESTATIS population, so the national income mean cannot be weighted: "
            f"{missing[:10]}. Check that the DESTATIS 12411-0018 export and the INKAR export "
            f"use the same Kreis territory.")
    national_mean = float((df["einkommen_eur"] * weights).sum() / weights.sum())
    out = df.copy()
    out["scale"] = out["einkommen_eur"] / national_mean
    return out, national_mean


def _load(path: str, year_cfg, population_path: str):
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
    # silently empties the frame and the national mean becomes NaN, which propagates as
    # 100 % NaN income downstream. Fail loud instead.
    if len(df) == 0:
        raise RuntimeError(
            f"INKAR household-income table for year '{target_year}' has no "
            f"numeric values after dropna ({n_before} Kreis rows before, 0 after). "
            f"Check {path} - the source may contain only 'N.A.' placeholders."
        )

    population = _population.load_population_by_kreis(population_path, set(df["ars5"]))
    df, national_mean = kreis_income_scale(df, population)
    print(
        "[braunschweig.data.inkar.household_income] INKAR {}: national mean {:.1f} EUR "
        "(population-weighted, DESTATIS 12411-0018); unweighted Kreis mean {:.1f} EUR".format(
            target_year, national_mean, df["einkommen_eur"].mean()))

    return df[["ars5", "raumeinheit", "einkommen_eur", "scale"]], target_year, national_mean


def execute(context) -> pd.DataFrame:
    df, year, national_mean = _load(_path(context), context.config("braunschweig.inkar_year"),
                                    _population_path(context))

    scope = context.config("braunschweig.political_prefix")
    if scope:
        scope = [str(p) for p in scope]
        in_scope = df[df["ars5"].isin(scope)].copy()
        print(
            "[braunschweig.data.inkar.household_income] INKAR {}: national mean {:.0f} EUR; "
            "scope scale range {:.3f}..{:.3f}".format(
                year, national_mean,
                in_scope["scale"].min(), in_scope["scale"].max(),
            )
        )

    return df.reset_index(drop=True)


def validate(context):
    path = _path(context)
    if not os.path.exists(path):
        raise RuntimeError(f"Missing INKAR household-income file: {path}")
    population_path = _population_path(context)
    if not os.path.exists(population_path):
        raise RuntimeError(f"Missing DESTATIS 12411-0018 population file: {population_path}")
    return {
        "inkar_size": os.path.getsize(path),
        "population_size": os.path.getsize(population_path),
        "helpers": synpp_tokens.source_token(_HELPER_MODULES, (), stage_label=__name__ + ".validate()"),
    }
