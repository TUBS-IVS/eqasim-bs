"""
Unified MiD 2023 reference loader for the Braunschweig region.

Reads the four CSVs produced by scripts/extract_mid_tables.py into a clean
dict of DataFrames keyed by table code. All tables carry one row per Kreis
(``ars5`` ∈ {03101, 03102, 03103, 03151, 03153, 03154, 03157, 03158}) plus
one ``Gesamt`` row for the ZGB aggregate.

Returned structure::

    {
        "P9":    DataFrame[kreis, ars5, n_weighted, n_unweighted,
                           vollzeit, teilzeit, geringfuegig, sonstiges,
                           erwerbstaetig_unspec, in_ausbildung,
                           nicht_erwerbstaetig, keine_angabe],
        "P12_1": DataFrame[kreis, ars5, n_*, zu_fuss, fahrrad, oeffentlich,
                           auto, anderes, ganz_unterschiedlich,
                           keine_feste_arbeit, keine_angabe],
        "P13":   DataFrame[kreis, ars5, n_*, d_0, d_0_5, d_5_10, d_10_20,
                           d_20_30, d_30_50, d_50_100, d_100p,
                           keine_feste_arbeit, keine_angabe, mittel],
        "P17_1": DataFrame[kreis, ars5, n_*, ja, nein, keine_angabe],
    }

Downstream stages can consume this for both calibration (e.g. resampling
commute-distance CDFs from P13) and validation (comparing synthetic
modal split / Führerschein-Quote / Wegelängen against P12_1 / P17_1 / P13).
"""

from __future__ import annotations

import os
from typing import Dict

import numpy as np
import pandas as pd

# Distance band centres (km) for MiD 2023 P13 bands.  ``d_100p`` is open-
# ended — we use 150 km as a reasonable centre.  ``d_0`` is <0.5 km and
# gets centre 0.25 km.  These are used to derive a numeric CDF per Kreis.
P13_BANDS = [
    ("d_0",      0.25),
    ("d_0_5",    2.5),
    ("d_5_10",   7.5),
    ("d_10_20", 15.0),
    ("d_20_30", 25.0),
    ("d_30_50", 40.0),
    ("d_50_100", 75.0),
    ("d_100p",  150.0),
]

TABLES = ("P9", "P12_1", "P13", "P17_1")


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.mid_csv_dir",
        "braunschweig/mid",
    )


def _csv_path(context, table: str) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.mid_csv_dir"),
        "mid2023_{}.csv".format(table),
    )


def _load_table(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ars5"] = df["ars5"].astype(str)
    df["kreis"] = df["kreis"].astype(str)
    return df


def build_p13_cdfs(df_p13: pd.DataFrame) -> Dict[str, np.ndarray]:
    """
    Convert MiD P13 Kreis rows into per-Kreis cumulative distribution
    functions over the ``P13_BANDS`` midpoints.  Returns a dict
    ``{ars5: np.ndarray}`` where each array has shape (len(P13_BANDS),)
    and represents a CDF.  Rows with all-NaN bands are skipped.

    The Gesamt row (``ars5 == '03ZGB'``) is included so callers can use
    it as a fallback for Kreise outside the eight ZGB districts.
    """
    out: Dict[str, np.ndarray] = {}
    band_cols = [c for c, _ in P13_BANDS]
    for _, row in df_p13.iterrows():
        values = np.array([row[c] if pd.notna(row[c]) else 0.0
                           for c in band_cols], dtype=float)
        total = values.sum()
        if total <= 0:
            continue
        cdf = np.cumsum(values) / total
        out[str(row["ars5"])] = cdf
    return out


def build_p17_license_rates(df_p17: pd.DataFrame) -> Dict[str, float]:
    """
    MiD P17.1 reports Führerschein-Quote per Kreis as a percentage
    (``ja`` column).  Returns ``{ars5: rate in [0, 1]}``.
    """
    out: Dict[str, float] = {}
    for _, row in df_p17.iterrows():
        if pd.isna(row.get("ja")):
            continue
        out[str(row["ars5"])] = float(row["ja"]) / 100.0
    return out


def execute(context) -> dict:
    tables: Dict[str, pd.DataFrame] = {}
    for name in TABLES:
        path = _csv_path(context, name)
        tables[name] = _load_table(path)

    # Pre-compute CDFs and license rates as derived fields — downstream
    # callers can either use the raw tables or these convenience forms.
    p13_cdfs = build_p13_cdfs(tables["P13"])
    license_rates = build_p17_license_rates(tables["P17_1"])

    print(
        "[braunschweig.data.mid.references] loaded {}  P13 CDFs={}  "
        "P17 licenses Kreise={}".format(
            {k: len(v) for k, v in tables.items()},
            len(p13_cdfs), len(license_rates),
        )
    )

    return {
        "tables": tables,
        "p13_distance_cdfs": p13_cdfs,
        "p13_band_midpoints_km": np.array([m for _, m in P13_BANDS]),
        "p17_license_rates": license_rates,
    }


def validate(context):
    total = 0
    for name in TABLES:
        path = _csv_path(context, name)
        if not os.path.exists(path):
            raise RuntimeError(
                "MiD 2023 {} CSV missing at {}.  Re-run "
                "scripts/extract_mid_tables.py".format(name, path)
            )
        total += os.path.getsize(path)
    return total
