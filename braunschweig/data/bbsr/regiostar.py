"""
Load the BMV/BBSR RegioStaR Gemeinde reference table (TASK-004).

Source data
-----------
``eqasim-data/data/regiostar/regiostar_referenzdatei.xlsx`` (sheet
``ReferenzGebietsstand2020``), fetched via
``scripts/download_regiostar.py``.

Output
------
``DataFrame`` with one row per Gemeinde:

    - ``commune_id``   8-digit AGS as zero-padded string (matches the
                       ``commune_id`` keys used downstream in
                       ``synthesis.locations`` etc.).
    - ``ars5``         5-digit Kreis prefix.
    - ``name``         Gemeindename.
    - ``regiostar7``   integer 71–77 RegioStaR-7 type.
    - ``regiostar17``  integer 3-digit RegioStaR-17 type (finer).
    - ``regiostar_gem7`` integer Gemeinde-typ (RegioStaRGem7).

The frame is filtered to the configured ``braunschweig.political_prefix``
(ZGB-8 by default) so downstream stages do not pay for full-Germany
overhead. A small ``RegioStaR-7`` legend is exposed as ``REGIOSTAR7_LABELS``
for reporting.

REGIOSTAR-7 codes (BMV 2020):
    71 — Metropole
    72 — Regiopole, Großstadt
    73 — Mittelstadt, städtischer Raum
    74 — Kleinstädtischer, dörflicher Raum
    75 — Zentrale Stadt
    76 — Mittelstadt, städtischer Raum (ländlich)
    77 — Kleinstädtischer, dörflicher Raum (ländlich)
"""

from __future__ import annotations

import os

import pandas as pd

REGIOSTAR7_LABELS = {
    71: "Metropole",
    72: "Regiopole, Großstadt",
    73: "Mittelstadt/städtischer Raum",
    74: "Kleinstädtischer, dörflicher Raum",
    75: "Zentrale Stadt (ländlich)",
    76: "Mittelstadt/städtischer Raum (ländlich)",
    77: "Kleinstädtischer, dörflicher Raum (ländlich)",
}

SHEET = "ReferenzGebietsstand2020"


def configure(context):
    context.config("data_path")
    context.config(
        "regiostar.path",
        "regiostar/regiostar_referenzdatei.xlsx",
    )
    context.config("braunschweig.political_prefix")


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("regiostar.path"),
    )


def execute(context) -> pd.DataFrame:
    path = _resolve_path(context)
    raw = pd.read_excel(path, sheet_name=SHEET, header=0)

    expected_cols = {
        "gem_20", "name_20", "RegioStaR7", "RegioStaR17", "RegioStaRGem7",
    }
    missing = expected_cols - set(raw.columns)
    if missing:
        raise RuntimeError(
            "[braunschweig.data.bbsr.regiostar] expected columns missing "
            f"from {path}::{SHEET}: {sorted(missing)}"
        )

    df = pd.DataFrame({
        "commune_id": raw["gem_20"].astype("Int64").astype(str).str.zfill(8),
        "name": raw["name_20"].astype(str),
        "regiostar7": pd.to_numeric(raw["RegioStaR7"], errors="coerce")
                       .astype("Int64"),
        "regiostar17": pd.to_numeric(raw["RegioStaR17"], errors="coerce")
                        .astype("Int64"),
        "regiostar_gem7": pd.to_numeric(raw["RegioStaRGem7"], errors="coerce")
                            .astype("Int64"),
    })
    df["ars5"] = df["commune_id"].str[:5]
    df = df.dropna(subset=["regiostar7"]).copy()

    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    df = df[df["ars5"].isin(scope)].reset_index(drop=True)

    if df.empty:
        raise RuntimeError(
            "[braunschweig.data.bbsr.regiostar] no Gemeinden matched scope "
            f"{scope}; check ``braunschweig.political_prefix`` and source file."
        )

    df = df[[
        "commune_id", "ars5", "name",
        "regiostar7", "regiostar17", "regiostar_gem7",
    ]]

    counts = df["regiostar7"].value_counts().sort_index()
    print(
        "[braunschweig.data.bbsr.regiostar] "
        f"{len(df)} Gemeinden across {df['ars5'].nunique()} Kreise; "
        f"RegioStaR7 distribution: {dict(counts)}"
    )
    return df


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"RegioStaR reference file not found: {path}\n"
            "Run scripts/download_regiostar.py to fetch it."
        )
    return os.path.getsize(path)
