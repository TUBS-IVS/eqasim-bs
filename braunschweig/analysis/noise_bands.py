"""Aggregation + report-merge helpers for Monte-Carlo sampling-noise bands (issue #126).

A band quantifies how much a validation metric moves under pure re-seeding at a
FIXED sampling rate. It is a triage signal ("this deviation is indistinguishable
from sampling noise"), never a significance test and never a calibration input.
"""
from __future__ import annotations

import pandas as pd

REQUIRED_DRAW_COLUMNS = ("draw_seed", "metric_id", "group", "value")


def aggregate_draw_metrics(frames: list) -> pd.DataFrame:
    if not frames:
        raise ValueError("[noise_bands] no draw frames to aggregate.")
    keysets = []
    for f in frames:
        missing = [c for c in REQUIRED_DRAW_COLUMNS if c not in f.columns]
        if missing:
            raise ValueError(f"[noise_bands] draw frame missing columns {missing}.")
        keysets.append(frozenset(zip(f["metric_id"], f["group"])))
    if len(set(keysets)) != 1:
        raise ValueError(
            "[noise_bands] draws carry inconsistent metric/group sets -- a "
            "missing metric means the validation module changed mid-sweep; "
            "re-run the whole sweep on one code state."
        )
    df = pd.concat(frames, ignore_index=True)
    out = (
        df.groupby(["metric_id", "group"])["value"]
        .agg(mean="mean", q05=lambda x: x.quantile(0.05),
             q95=lambda x: x.quantile(0.95), n_draws="count")
        .reset_index()
    )
    return out


def merge_bands_into_report(report: pd.DataFrame, bands: pd.DataFrame, *,
                            metric_col: str = "metric_id", group_col: str = "group",
                            value_col: str = "value") -> pd.DataFrame:
    b = bands.rename(columns={"q05": "band_q05", "q95": "band_q95"})
    out = report.merge(
        b[[metric_col, group_col, "band_q05", "band_q95"]],
        on=[metric_col, group_col], how="left",
    )
    inside = (out[value_col] >= out["band_q05"]) & (out[value_col] <= out["band_q95"])
    out["within_noise_band"] = inside.astype("boolean")
    out.loc[out["band_q05"].isna(), "within_noise_band"] = pd.NA
    return out
