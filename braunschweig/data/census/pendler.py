"""BA Pendleratlas (employment statistics): Kreis-to-Kreis OD matrix for ZGB-8.

Source
------
``statistik.arbeitsagentur.de`` — two CSV exports requested for the eight
ZGB Kreise:

    * ``statistik_pendler_*412.csv`` — *Einpendler* (workplace inside ZGB,
      residence anywhere).
    * ``statistik_pendler_*430.csv`` — *Auspendler* (residence inside ZGB,
      workplace anywhere).
    * Gebietsstand June 2025, Datenstand January 2026.
    * Unit: ``sozialversicherungspflichtig Beschäftigte`` (SvB), counted
      at the workplace.

Output schema
-------------
A long ``DataFrame`` with one row per ordered Kreis pair::

    orig_ars  str  5-digit ARS of the residence Kreis
    dest_ars  str  5-digit ARS of the workplace Kreis
    flow      int  SvB commuter count

The Kreis level is chosen because the BA does not publish a
Gemeinde-to-Gemeinde matrix (privacy / small-cell suppression).

Downstream consumers
--------------------
* Validation of the gravity-model output
  (``braunschweig.gravity.model``) after aggregation to the Kreis level.
* Row-sum calibration of the gravity model: scale Gemeinde-level flows
  so that their Kreis aggregates match the BA totals.
* Workplace-sampling weights for synthetic commuters crossing the ZGB
  boundary (e.g. external inbound to Hannover).

The two source files overlap on ZGB↔ZGB pairs — the same pair appears
once in each export. Deduplication keeps the larger of the two flows
(see ``execute``); self-loops are dropped.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd


# Neun Datenspalten nach den 4 ID-Spalten (Einheit: Personen).
NUMERIC_COLUMNS = ["total", "male", "female", "de", "foreign", "apprentice"]


def _read_one(path: str, orientation: str) -> pd.DataFrame:
    """Read one BA Pendler CSV.

    orientation == 'ein'  => destination side = ZGB, origin side = rest
    orientation == 'aus'  => origin side = ZGB, destination side = rest
    """
    raw = pd.read_csv(
        path,
        sep=";",
        skiprows=10,
        encoding="utf-8",
        dtype=str,
    )

    if len(raw.columns) != 10:
        raise RuntimeError(
            f"Unexpected column count in {path}: got {len(raw.columns)}"
        )

    if orientation == "ein":
        # File layout: Arbeitsort;RS;Wohnort;RS;data...
        raw.columns = [
            "dest_name", "dest_ars",
            "orig_name", "orig_ars",
        ] + NUMERIC_COLUMNS
    elif orientation == "aus":
        # File layout: Wohnort;RS;Arbeitsort;RS;data...
        raw.columns = [
            "orig_name", "orig_ars",
            "dest_name", "dest_ars",
        ] + NUMERIC_COLUMNS
    else:
        raise ValueError(orientation)

    # Drop aggregate rows. The BA export mixes real Kreis codes (5-digit
    # numeric ARS) with Bundesland/Regierungsbezirk aggregates that share
    # the same string length — e.g. "031xx", "030xx", "Übrige Kreise" — so
    # a plain length filter is not sufficient. We require all-digit ARS.
    kreis_mask = (
        raw["orig_ars"].str.fullmatch(r"\d{5}", na=False)
        & raw["dest_ars"].str.fullmatch(r"\d{5}", na=False)
    )
    df = raw[kreis_mask].copy()

    # Numeric conversion. German format: "68.380" means 68380 (thousand sep).
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(".", "", regex=False),
            errors="coerce",
        )

    df = df.dropna(subset=["total"])
    df["total"] = df["total"].astype(int)

    df["source"] = orientation
    return df[["orig_ars", "dest_ars", "total", "source"]].rename(
        columns={"total": "flow"}
    )


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.pendler_ein_path",
        "braunschweig/statistik_pendler_2026042493412.csv",
    )
    context.config(
        "braunschweig.pendler_aus_path",
        "braunschweig/statistik_pendler_2026042493430.csv",
    )
    context.config("braunschweig.political_prefix")


def _paths(context) -> tuple[str, str]:
    base = context.config("data_path")
    return (
        os.path.join(base, context.config("braunschweig.pendler_ein_path")),
        os.path.join(base, context.config("braunschweig.pendler_aus_path")),
    )


def execute(context) -> pd.DataFrame:
    path_ein, path_aus = _paths(context)

    df_ein = _read_one(path_ein, "ein")
    df_aus = _read_one(path_aus, "aus")

    # Concatenate. The two files overlap on ZGB↔ZGB pairs — the same pair
    # appears once in the *Einpendler* export (workplace-side view) and
    # once in the *Auspendler* export (residence-side view). The two
    # numbers are identical when both reports cover the full population,
    # so we deduplicate by keeping the maximum flow per ordered pair (a
    # safety against zero rows that the BA emits when the count falls
    # below the suppression threshold in only one of the two views).
    df = pd.concat([df_ein, df_aus], ignore_index=True)
    df = (
        df.groupby(["orig_ars", "dest_ars"], as_index=False)
          .agg(flow=("flow", "max"))
    )

    # Drop self-loops; they are always zero in the BA exports.
    df = df[df["orig_ars"] != df["dest_ars"]].copy()

    # Diagnostics: print inbound / outbound / intra-ZGB SvB totals against
    # the configured ZGB Kreis scope (``braunschweig.political_prefix``).
    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    total_in = df.loc[df["dest_ars"].isin(scope), "flow"].sum()
    total_out = df.loc[df["orig_ars"].isin(scope), "flow"].sum()
    total_internal = df.loc[
        df["dest_ars"].isin(scope) & df["orig_ars"].isin(scope), "flow"
    ].sum()

    print(
        "[braunschweig.data.census.pendler] "
        "{:,} Kreis→Kreis flows | inbound to ZGB: {:,} | outbound: {:,} | "
        "intra-ZGB: {:,}".format(
            len(df),
            int(total_in),
            int(total_out),
            int(total_internal),
        )
    )

    return df[["orig_ars", "dest_ars", "flow"]]


def validate(context):
    path_ein, path_aus = _paths(context)
    size = 0
    for p in (path_ein, path_aus):
        if not os.path.exists(p):
            raise RuntimeError(f"Missing Pendler file: {p}")
        size += os.path.getsize(p)
    return size
