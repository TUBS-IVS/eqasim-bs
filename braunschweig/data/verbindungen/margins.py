"""VerBindungen BA cell margins: workers at home / at workplace per cell.

Source files (BA, semicolon-separated, leading index column, values rounded to
multiples of 10, Dominanz suppression as ``*``):
``SvBaGeB_Statisch_WO_Verkehrszellen.csv`` (workers at HOME per cell) and
``SvBaGeB_Statisch_AO_Verkehrszellen.csv`` (workers at WORKPLACE per cell).
Universe: SvB + aGeB only (NO Beamte/Selbststaendige), 31.12.2019. Only the
``SvB_aGeB`` total is loaded; breakdown columns are ignored for now.

``*`` is the ONLY documented suppression marker: it maps to NA and is counted
and logged as Dominanz suppression. Any OTHER unparseable ``SvB_aGeB`` token
(garbage text, empty field) raises, so a data-quality regression can never
hide inside the legitimate suppression bucket.
"""
from __future__ import annotations

import os

import pandas as pd

WO_NAME = "SvBaGeB_Statisch_WO_Verkehrszellen.csv"
AO_NAME = "SvBaGeB_Statisch_AO_Verkehrszellen.csv"


def read_statisch_csv(path: str, value_name: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", dtype=str)
    id_cols = [c for c in df.columns if str(c).endswith("_verb_zell_id")]
    if not id_cols or "SvB_aGeB" not in df.columns:
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.margins] unexpected header in "
            f"{path}: need one *_verb_zell_id column and SvB_aGeB, got "
            f"{list(df.columns)}"
        )
    values_raw = df["SvB_aGeB"]
    is_star = values_raw == "*"
    n_star = int(is_star.sum())
    parsed = pd.to_numeric(values_raw.mask(is_star), errors="coerce")
    # NA that did NOT come from the documented '*' marker is unparseable
    # garbage (bad token, empty/whitespace field) -> fail loudly, never fold
    # it into the Dominanz suppression bucket.
    bad_mask = parsed.isna() & ~is_star
    if bad_mask.any():
        examples = [repr(v) for v in values_raw[bad_mask].head(5).tolist()]
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.margins] {int(bad_mask.sum())} "
            f"non-Dominanz unparseable SvB_aGeB value(s) in {path} "
            f"(e.g. {examples}); only '*' is the documented suppression "
            "marker -- upstream format changed?"
        )
    out = pd.DataFrame({
        "cell_id": df[id_cols[0]].astype(str),
        value_name: parsed.astype("Int64"),
    })
    print(
        f"[braunschweig.data.verbindungen.margins] {os.path.basename(path)}: "
        f"{len(out)} rows, {n_star} suppressed ('*' Dominanz) "
        f"({100.0 * n_star / len(out) if len(out) else 0.0:.1f}%)"
    )
    return out


def build_margins_frame(df_wo: pd.DataFrame, df_ao: pd.DataFrame,
                        cell_ids: list) -> pd.DataFrame:
    """One row per ZGB cell; cells absent from a file carry NA (logged)."""
    base = pd.DataFrame({"cell_id": [str(c) for c in cell_ids]})
    out = base.merge(df_wo, on="cell_id", how="left")
    out = out.merge(df_ao, on="cell_id", how="left")
    for col in ("workers_at_home", "workers_at_workplace"):
        n_missing = int(out[col].isna().sum())
        if n_missing:
            print(
                f"[braunschweig.data.verbindungen.margins] {n_missing}/{len(out)} "
                f"cells without {col} (suppressed or absent from source)"
            )
    return out


def configure(context):
    context.config("data_path")
    context.config("braunschweig.verbindungen_path", "verbindungen")
    context.stage("braunschweig.data.verbindungen.zones")


def execute(context):
    base = os.path.join(
        context.config("data_path"),
        context.config("braunschweig.verbindungen_path"),
    )
    for name in (WO_NAME, AO_NAME):
        if not os.path.exists(os.path.join(base, name)):
            raise RuntimeError(
                f"[braunschweig.data.verbindungen.margins] missing "
                f"{os.path.join(base, name)}; fetch it with: "
                "python scripts/download_verbindungen.py"
            )
    df_cells, _ = context.stage("braunschweig.data.verbindungen.zones")
    return build_margins_frame(
        read_statisch_csv(os.path.join(base, WO_NAME), "workers_at_home"),
        read_statisch_csv(os.path.join(base, AO_NAME), "workers_at_workplace"),
        cell_ids=list(df_cells["cell_id"]),
    )
