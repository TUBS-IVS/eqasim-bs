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
    """Scale sampled counts to 100 % and compare against a mode-resolved OD target.

    General mode-resolved building block: it merges on ``[ars5, direction, mode]`` and
    therefore requires a target that carries a ``mode`` dimension. The production cordon
    validation does NOT use this function, because the only OD source (BA Pendler,
    ``braunschweig.data.census.pendler``) has no mode dimension; the production path uses
    :func:`od_deviation_vs_target` (mode-agnostic) instead. This function is retained and
    tested so a future mode-resolved OD reference (were one to exist) can be wired without
    re-deriving the logic.

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


def od_deviation_vs_target(counts: pd.DataFrame, od_target: pd.DataFrame,
                           sampling_rate: float = 1.0) -> pd.DataFrame:
    """Per-(Kreis, direction) OD deviation, aggregating realized counts over mode.

    The BA Pendler OD reference (``braunschweig.data.census.pendler``, SvB commuter
    counts) has NO mode dimension, so the realized agent counts are summed over mode
    before the comparison. This is the production cordon OD check.

    Interpretation (kept honest): in-commuter agents are expanded directly from the same
    BA inbound flow (``expand_to_agents``), so this is a CONSISTENCY / coverage check --
    it verifies no agent mass was lost between demand expansion and assembly, and it
    surfaces small-Kreis coverage loss at low sampling rates (flows whose scaled count
    rounds to zero are dropped). It is NOT an independent validation against a source the
    synthesis did not already draw from; the independent reality check is the modal-split
    deviation vs the Mikrozensus reference (:func:`modal_split_deviation`).

    Args:
        counts: output of :func:`counts_by_kreis_direction_mode` (columns ars5,
            direction, mode, ``n``).
        od_target: target counts at full-population scale, columns
            ``[ars5, direction, n_target]`` (NO mode column).
        sampling_rate: the run's sampling rate; counts are divided by it to scale up
            to 100 % before the comparison.

    Returns:
        Outer-merged frame ``[ars5, direction, n, n_scaled, n_target, abs_dev, pct_dev]``
        (``abs_dev`` = n_scaled - n_target; ``pct_dev`` = 100 * abs_dev / target, NaN
        when the target is zero).
    """
    if sampling_rate <= 0:
        raise ValueError("od_deviation_vs_target: sampling_rate must be > 0")
    by_od = counts.groupby(["ars5", "direction"], as_index=False)["n"].sum()
    merged = by_od.merge(od_target, on=["ars5", "direction"], how="outer")
    merged["n"] = merged["n"].fillna(0)
    merged["n_target"] = merged["n_target"].fillna(0)
    merged["n_scaled"] = (merged["n"] / sampling_rate).round().astype(int)
    merged["abs_dev"] = merged["n_scaled"] - merged["n_target"]
    merged["pct_dev"] = merged.apply(
        lambda r: (100.0 * r["abs_dev"] / r["n_target"]) if r["n_target"] else float("nan"),
        axis=1,
    )
    return merged.sort_values(["ars5", "direction"]).reset_index(drop=True)


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
    """Agent counts per ACTUAL entry point (entry_x, entry_y, entry_kind, direction, mode).

    Groups boundary flows by the real boarding coordinate:

    - PT in-commuters are placed at their rail station (``entry_kind="rail_station"``,
      ``entry_x/entry_y`` = station coords) so the output map shows them at the
      Bahnhof, not on the motorway.
    - Car agents appear at their road gate (``entry_kind="road_gate"``).

    ``gate_id`` is carried through for traceability (the road gate the agent is
    associated with for network entry / PT fallback), but it is NOT the grouping key.

    The input frame is expected to have the B5 schema with columns
    ``[entry_x, entry_y, entry_kind, direction, mode, gate_id]``.
    Output columns: ``[entry_x, entry_y, entry_kind, direction, mode, gate_id, n]``,
    ordered descending by ``n``.
    """
    group_keys = ["entry_x", "entry_y", "entry_kind", "direction", "mode"]
    counts = (
        agents.groupby(group_keys, sort=True)
        .size()
        .reset_index(name="n")
    )
    # Carry gate_id for traceability (first occurrence per entry point + kind).
    if "gate_id" in agents.columns:
        gate_lookup = agents.groupby(group_keys)["gate_id"].first().reset_index()
        counts = counts.merge(gate_lookup, on=group_keys, how="left")
    return counts.sort_values("n", ascending=False).reset_index(drop=True)
