"""
Bundesagentur für Arbeit "Pendler nach Wirtschaftsabschnitten" loader
(TASK-015).

The BA publishes a Kreis-level commuting matrix split by 21 NACE-Rev.2
sections (A..U). The XLSX/CSV layout is the same for the
``Pendlerstatistik nach dem Arbeitsort`` and ``... nach dem Wohnort``
products: a wide block of (origin, destination) cells per economic
section.

This stage parses the long-form CSV produced by the official "Tabellen"
download (``Pendler nach Wirtschaftsabschnitten``, monthly snapshot) and
returns a tidy DataFrame:

    home_kreis (str, 5-digit Kennziffer)
    work_kreis (str, 5-digit Kennziffer)
    sector     (str, NACE-section A..U)
    flow       (int, sozialversicherungspflichtig Beschäftigte)

Configuration
-------------
``braunschweig.ba_pendler_detailed_path`` (str)
    Path to the CSV (relative to ``data_path``). When unset or the file
    does not exist, the stage returns an empty frame and prints a
    notice — keeps the pipeline runnable while the pinned download is
    pending.

``braunschweig.ba_pendler_detailed_separator`` (str, default ";")

The companion downloader ``scripts/download_ba_pendler_detailed.py``
fetches the file from the BA "Statistik-Service" portal with a
SHA-256 pin (analogous to ``scripts/download_regiostar.py``).
"""

from __future__ import annotations

import os

import pandas as pd


def configure(context):
    context.config("data_path")
    context.config("braunschweig.ba_pendler_detailed_path", None)
    context.config("braunschweig.ba_pendler_detailed_separator", ";")
    context.config("braunschweig.political_prefix", None)


def _resolve(context) -> str | None:
    rel = context.config("braunschweig.ba_pendler_detailed_path")
    if not rel:
        return None
    return os.path.join(context.config("data_path"), rel)


def execute(context) -> pd.DataFrame:
    path = _resolve(context)
    if path is None or not os.path.exists(path):
        print(
            "[braunschweig.data.ba.pendler_detailed] No file configured "
            "(braunschweig.ba_pendler_detailed_path). Returning empty frame."
        )
        return pd.DataFrame(
            columns=["home_kreis", "work_kreis", "sector", "flow"]
        )

    sep = context.config("braunschweig.ba_pendler_detailed_separator")
    df = pd.read_csv(
        path,
        sep=sep,
        dtype={
            "home_kreis": str,
            "work_kreis": str,
            "sector": str,
        },
        na_values=["", "x", "*", "."],
    )

    required = {"home_kreis", "work_kreis", "sector", "flow"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(
            f"[braunschweig.data.ba.pendler_detailed] {path} missing "
            f"columns: {sorted(missing)}. Expected long-form CSV with "
            f"home_kreis;work_kreis;sector;flow."
        )

    df["home_kreis"] = df["home_kreis"].astype(str).str.zfill(5)
    df["work_kreis"] = df["work_kreis"].astype(str).str.zfill(5)
    df["sector"] = df["sector"].astype(str).str.strip().str.upper()
    flow_numeric = pd.to_numeric(df["flow"], errors="coerce")
    # Fallback transparency: BA privacy-masked cells ("x"/"*"/".") coerce to
    # NaN and are treated as flow 0 -- systematic in sector-fine matrices, so
    # the rate must be visible rather than silently zeroed.
    n_masked = int(flow_numeric.isna().sum())
    if n_masked:
        print(
            f"[braunschweig.data.ba.pendler_detailed] {n_masked}/{len(df)} "
            f"({100.0 * n_masked / len(df):.1f}%) flow cells are privacy-masked "
            f"or non-numeric -> treated as 0."
        )
    df["flow"] = flow_numeric.fillna(0).astype(int)

    scope = context.config("braunschweig.political_prefix")
    if scope:
        scope = {str(p) for p in scope}
        df = df[
            df["home_kreis"].isin(scope) | df["work_kreis"].isin(scope)
        ].copy()

    print(
        "[braunschweig.data.ba.pendler_detailed] {:,} OD×sector cells "
        "loaded from {}".format(len(df), path)
    )
    return df.reset_index(drop=True)


def validate(context):
    path = _resolve(context)
    if path is None:
        return 0
    if not os.path.exists(path):
        raise RuntimeError(
            f"[braunschweig.data.ba.pendler_detailed] Missing file: {path}"
        )
    return os.path.getsize(path)
