"""
INKAR full-panel stage (TASK-014).

Reads multiple BBSR INKAR ``E_*.xls`` exports (each providing one Kreis-level
indicator over a multi-year panel) and joins them into a single wide
DataFrame keyed on the 5-digit Kennziffer (``ars5``). Defaults are off
(empty mapping) so existing pipelines stay bit-identical until the user
opts into TASK-014 by configuring ``braunschweig.inkar_panel``.

Configuration
-------------
``braunschweig.inkar_panel`` (dict[str, str], optional)
    Map from indicator-name (the column key in the output) to a path
    (relative to ``data_path``) of the corresponding INKAR ``E_*.xls``.
    Supported indicators include — but are not limited to — those
    listed in ``plan/feature-bs-model-improvements-1.md`` for TASK-014:

        - ``population_density``   (E_Bevoelkerungsdichte.xls)
        - ``unemployment_rate``    (E_Arbeitslosenquote.xls)
        - ``education_attainment`` (E_HochschulabsolventenQuote.xls)
        - ``healthcare_access``    (E_AerzteJeEinwohner.xls)

``braunschweig.inkar_panel_year`` (str, default "latest")
    Which annual column to extract. Same semantics as
    ``braunschweig.data.inkar.household_income``.

Schema out (wide)
-----------------
    ars5 (str, 5-digit Kennziffer)
    raumeinheit (str, Kreis name)
    <indicator_1> (float)
    <indicator_2> (float)
    ...

When the config is empty/missing the stage returns the bare ``ars5``
listing for the political scope and prints a notice.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import pandas as pd


def configure(context):
    context.config("data_path")
    context.config("braunschweig.inkar_panel", {})
    context.config("braunschweig.inkar_panel_year", "latest")
    context.config("braunschweig.political_prefix", None)


def _resolve(context, rel_path: str) -> str:
    return os.path.join(context.config("data_path"), rel_path)


def _read_indicator(path: str, year_cfg) -> pd.DataFrame:
    """Read a single INKAR ``E_*.xls`` and return ``ars5, raumeinheit, value``."""
    df = pd.read_excel(path, sheet_name="Daten", header=[0, 1])
    lvl0 = df.columns.get_level_values(0)
    lvl1 = df.columns.get_level_values(1)

    years = sorted(
        str(y) for l0, y in zip(lvl0, lvl1)
        if isinstance(l0, str) and pd.notna(y)
        and str(y).strip().isdigit()
    )
    if not years:
        raise RuntimeError(f"No annual data columns found in {path}")

    target_year = years[-1] if year_cfg in (None, "latest") else str(year_cfg)
    if target_year not in years:
        raise RuntimeError(
            f"INKAR year '{target_year}' not found in {path}; have {years}"
        )

    df.columns = [
        str(l0).strip() if pd.isna(l1) or str(l1).startswith("Unnamed")
        else str(l1)
        for l0, l1 in zip(lvl0, lvl1)
    ]

    keep = ["Kennziffer", "Raumeinheit", "Aggregat", target_year]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise RuntimeError(f"{path}: missing expected columns {missing}")

    df = df[keep].copy()
    df.columns = ["kennziffer", "raumeinheit", "aggregat", "value"]
    df = df[df["aggregat"] == "Kreise"].copy()
    df["ars5"] = df["kennziffer"].astype(str).str.zfill(5)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df[["ars5", "raumeinheit", "value"]].dropna(subset=["value"])


def execute(context) -> pd.DataFrame:
    panel_cfg: Optional[Dict[str, str]] = context.config(
        "braunschweig.inkar_panel"
    ) or {}
    year_cfg = context.config("braunschweig.inkar_panel_year")
    scope = context.config("braunschweig.political_prefix")

    if not panel_cfg:
        print(
            "[braunschweig.data.inkar.full_panel] No indicators configured "
            "(braunschweig.inkar_panel is empty). Returning empty panel."
        )
        return pd.DataFrame(columns=["ars5", "raumeinheit"])

    merged: Optional[pd.DataFrame] = None
    for indicator_name, rel_path in panel_cfg.items():
        full_path = _resolve(context, rel_path)
        if not os.path.exists(full_path):
            raise RuntimeError(
                f"[braunschweig.data.inkar.full_panel] Missing INKAR file "
                f"for indicator '{indicator_name}': {full_path}"
            )
        df = _read_indicator(full_path, year_cfg).rename(
            columns={"value": indicator_name}
        )
        if merged is None:
            merged = df
        else:
            merged = merged.merge(
                df.drop(columns=["raumeinheit"]),
                on="ars5",
                how="outer",
            )

    assert merged is not None
    if scope:
        scope = [str(p) for p in scope]
        merged = merged[merged["ars5"].isin(scope)].copy()

    print(
        "[braunschweig.data.inkar.full_panel] {} indicators × {} Kreise".format(
            len(panel_cfg), len(merged)
        )
    )
    return merged.reset_index(drop=True)


def validate(context):
    panel_cfg = context.config("braunschweig.inkar_panel") or {}
    sizes = {}
    for name, rel_path in panel_cfg.items():
        full_path = _resolve(context, rel_path)
        if not os.path.exists(full_path):
            raise RuntimeError(
                f"[braunschweig.data.inkar.full_panel] Missing INKAR file "
                f"for '{name}': {full_path}"
            )
        sizes[name] = os.path.getsize(full_path)
    return sizes
