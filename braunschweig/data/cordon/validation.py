"""Cross-cordon commuter validation aggregations (pure).

Compare the synthesized in/out-commuters against real-world targets and aggregate
boundary flows per gate. Pure pandas; the synpp stage / writer feed these and emit
CSV + GPKG so every run shows "how well did we hit reality" and "where does the
boundary traffic enter".

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import pandas as pd


def counts_by_kreis_direction_mode(agents: pd.DataFrame) -> pd.DataFrame:
    """Synthesized agent counts per (external Kreis, direction, mode)."""
    return (
        agents.groupby(["ars5", "direction", "mode"], sort=True)
        .size()
        .reset_index(name="n")
    )


def deviation_vs_target(counts: pd.DataFrame, od_target: pd.DataFrame,
                        sampling_rate: float = 1.0) -> pd.DataFrame:
    """Scale sampled counts to 100 % and compare against the BA Pendler OD target.

    Args:
        counts: output of :func:`counts_by_kreis_direction_mode` (column ``n``).
        od_target: target counts (columns ars5, direction, mode, ``n_target``),
            at full-population scale.
        sampling_rate: the run's sampling rate; counts are divided by it to scale
            up to 100 % before the comparison.

    Returns:
        Outer-merged frame with ``n``, ``n_scaled``, ``n_target``, ``abs_dev``
        (scaled - target), ``pct_dev`` (100 * abs_dev / target).
    """
    if sampling_rate <= 0:
        raise ValueError("deviation_vs_target: sampling_rate must be > 0")
    merged = counts.merge(od_target, on=["ars5", "direction", "mode"], how="outer")
    merged["n"] = merged["n"].fillna(0)
    merged["n_target"] = merged["n_target"].fillna(0)
    merged["n_scaled"] = (merged["n"] / sampling_rate).round().astype(int)
    merged["abs_dev"] = merged["n_scaled"] - merged["n_target"]
    merged["pct_dev"] = merged.apply(
        lambda r: (100.0 * r["abs_dev"] / r["n_target"]) if r["n_target"] else float("nan"),
        axis=1,
    )
    return merged.sort_values(["ars5", "direction", "mode"]).reset_index(drop=True)


def modal_split_deviation(counts: pd.DataFrame, mode_target: pd.DataFrame) -> pd.DataFrame:
    """Per-direction synthesized modal split vs target share (percentage points).

    Args:
        counts: output of :func:`counts_by_kreis_direction_mode`.
        mode_target: target shares (columns direction, mode, ``share_pct_target``).

    Returns:
        Frame [direction, mode, share_pct, share_pct_target, pp_dev].
    """
    by_dir = counts.groupby(["direction", "mode"], sort=True)["n"].sum().reset_index()
    totals = by_dir.groupby("direction")["n"].transform("sum")
    by_dir["share_pct"] = 100.0 * by_dir["n"] / totals
    out = by_dir.merge(mode_target, on=["direction", "mode"], how="outer")
    out["share_pct"] = out["share_pct"].fillna(0.0)
    out["share_pct_target"] = out["share_pct_target"].fillna(0.0)
    out["pp_dev"] = out["share_pct"] - out["share_pct_target"]
    return out.sort_values(["direction", "mode"]).reset_index(drop=True)


def gate_flows(agents: pd.DataFrame) -> pd.DataFrame:
    """Agent counts per (gate, direction, mode), keeping gate coordinates.

    The per-gate validation output: how many in/out-commuters of each mode use
    each cordon gate (mappable). ``gate_x``/``gate_y`` are carried through.
    """
    counts = (
        agents.groupby(["gate_id", "direction", "mode"], sort=True)
        .size()
        .reset_index(name="n")
    )
    coords = agents.groupby("gate_id")[["gate_x", "gate_y"]].first().reset_index()
    return counts.merge(coords, on="gate_id", how="left")
