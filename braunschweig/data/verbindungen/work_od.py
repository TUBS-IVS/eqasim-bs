"""VerBindungen QZM Berufspendler OD, clipped to ZGB-internal cell pairs.

Source file ``QZM-Berufspendler-VerBindungen-Verkehrszellen.csv`` (StBA;
comma-separated, quoted; columns wo_zell_id, ao_zell_id, gesamtpendler).
Universe: ALL workers (SvB + aGeB + Beamte + Selbststaendige via the
Pendlerrechnung der Laender), reference date 31.12.2019, POTENTIAL commutes
(registered home -> employer location). Relations < 10 are removed upstream
(anonymisation), so the loaded values are censored at 10; the validation
metrics must account for that. Verified Germany-wide totals: 174,748 rows /
41,030,553 commuters (report figure after anonymisation).
"""
from __future__ import annotations

import os

import pandas as pd

QZM_NAME = "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv"


def read_qzm_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"wo_zell_id": str, "ao_zell_id": str})
    expected = {"wo_zell_id", "ao_zell_id", "gesamtpendler"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.work_od] columns missing from "
            f"{path}: {sorted(missing)}"
        )
    df["gesamtpendler"] = pd.to_numeric(df["gesamtpendler"], errors="raise").astype(int)
    if (df["gesamtpendler"] < 10).any():
        raise RuntimeError(
            "[braunschweig.data.verbindungen.work_od] values < 10 found; the "
            "upstream censoring contract (relations < 10 removed) is violated "
            "-- file identity/format changed?"
        )
    return df


def clip_qzm_to_cells(df_qzm: pd.DataFrame, cell_ids: set) -> tuple:
    """Keep relations with BOTH ends in *cell_ids*; report boundary mass."""
    wo_in = df_qzm["wo_zell_id"].isin(cell_ids)
    ao_in = df_qzm["ao_zell_id"].isin(cell_ids)
    internal = df_qzm[wo_in & ao_in].copy()
    if internal.empty:
        raise RuntimeError(
            "[braunschweig.data.verbindungen.work_od] zero ZGB-internal "
            "relations after clip -- empty join (cell id scheme mismatch?)"
        )
    stats = dict(
        internal_relations=int(len(internal)),
        internal_commuters=int(internal["gesamtpendler"].sum()),
        outbound_commuters=int(df_qzm.loc[wo_in & ~ao_in, "gesamtpendler"].sum()),
        inbound_commuters=int(df_qzm.loc[~wo_in & ao_in, "gesamtpendler"].sum()),
    )
    internal = internal.rename(columns={
        "wo_zell_id": "origin_cell_id",
        "ao_zell_id": "destination_cell_id",
        "gesamtpendler": "commuters",
    })[["origin_cell_id", "destination_cell_id", "commuters"]].reset_index(drop=True)
    return internal, stats


def configure(context):
    context.config("data_path")
    context.config("braunschweig.verbindungen_path", "verbindungen")
    context.stage("braunschweig.data.verbindungen.zones")


def execute(context):
    path = os.path.join(
        context.config("data_path"),
        context.config("braunschweig.verbindungen_path"),
        QZM_NAME,
    )
    if not os.path.exists(path):
        raise RuntimeError(
            f"[braunschweig.data.verbindungen.work_od] missing {path}; "
            "fetch it with: python scripts/download_verbindungen.py"
        )
    df_cells, _ = context.stage("braunschweig.data.verbindungen.zones")
    df, stats = clip_qzm_to_cells(read_qzm_csv(path), set(df_cells["cell_id"]))
    intra = df[df["origin_cell_id"] == df["destination_cell_id"]]["commuters"].sum()
    print(
        "[braunschweig.data.verbindungen.work_od] ZGB-internal: "
        f"{stats['internal_relations']} relations, "
        f"{stats['internal_commuters']:,} commuters "
        f"(intra-cell {100.0 * intra / stats['internal_commuters']:.1f}%); "
        f"boundary (not returned): outbound {stats['outbound_commuters']:,}, "
        f"inbound {stats['inbound_commuters']:,}"
    )
    return df
