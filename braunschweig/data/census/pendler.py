"""
BA Pendleratlas (Beschäftigtenstatistik) — Kreis-zu-Kreis OD-Matrix für ZGB-8.

Quelle: statistik.arbeitsagentur.de — zwei CSV-Exports:
    * statistik_pendler_*412.csv  Einpendler (Arbeitsort ZGB)
    * statistik_pendler_*430.csv  Auspendler (Wohnort ZGB)
    * Gebietsstand Juni 2025, Datenstand Januar 2026
    * Einheit: sozialversicherungspflichtig Beschäftigte (SvB) am Arbeitsort

Diese Stage liefert eine bereinigte, lange DataFrame im Schema:

    origin_ars    str   5-stelliger ARS des Wohnorts (Kreis)
    destination_ars  str   5-stelliger ARS des Arbeitsorts (Kreis)
    flow          int   SvB-Pendlerstrom
    source        str   'ein' | 'aus'   (Herkunftsdatei, Diagnose)

Die Kreis-Ebene ist bewusst als Datenquelle gewählt, weil die BA keine
Gemeinde-zu-Gemeinde-Matrix veröffentlicht (Datenschutz / kleine Fallzahlen).

Nutzung downstream:
    - Validierung der Gravity-Model-Ausgabe (bavaria.gravity.model) auf
      Kreis-Ebene nach Aggregation.
    - Optional: Zeilensummen-Kalibrierung des Gravity-Modells
      (Gemeinde-Flows skalieren so dass Kreissummen stimmen).
    - Direkte Arbeitsort-Sampling-Gewichte für Pendler über die
      ZGB-Grenze (externes Einpendeln nach Hannover etc.).
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
    context.config("bavaria.political_prefix")


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

    # Concatenate. The two files overlap for ZGB↔ZGB pairs — same pair
    # appears once as 'ein' (from external Kreis view of ZGB dest) and once
    # as 'aus' (from ZGB origin view). We deduplicate by keeping the first
    # occurrence and emit both 'source' labels via a helper column only for
    # forensic use.
    df = pd.concat([df_ein, df_aus], ignore_index=True)
    df = (
        df.groupby(["orig_ars", "dest_ars"], as_index=False)
          .agg(flow=("flow", "max"))  # max keeps the non-zero value if any
    )

    # Drop self-loops (always zero anyway)
    df = df[df["orig_ars"] != df["dest_ars"]].copy()

    # Report against the configured ZGB scope
    scope = [str(p) for p in context.config("bavaria.political_prefix")]
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
