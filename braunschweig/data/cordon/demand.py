"""Cross-cordon in-commuter demand expansion.

Turns the BA Pendler Kreis-pair flows into one row per synthetic in-commuter
agent (scaled by the sampling rate), with collision-free ids. Ported from the
proven ``feature/cordon-incommuters`` branch (``select_inbound_flows``,
``expand_to_agents``, ``make_incommuter_ids``, ``empty_frame_like``), re-homed
into the cross-cordon module. The home-cell sampler is intentionally NOT ported:
the gate-based design anchors in-commuter homes at cordon gates instead.

See ``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def select_inbound_flows(flows: pd.DataFrame, zgb_kreise, in_ring_kreise) -> pd.DataFrame:
    """BA Einpendler flows that are in-commutes from an in-ring source Kreis.

    Keep rows whose destination Kreis is in ZGB-8, whose origin Kreis is in the
    cordon ring, and whose origin is NOT itself a ZGB Kreis (those are residents).
    ``flows`` has columns [orig_ars, dest_ars, flow].
    """
    zgb = set(zgb_kreise)
    ring = set(in_ring_kreise)
    mask = (flows["dest_ars"].isin(zgb)
            & flows["orig_ars"].isin(ring)
            & ~flows["orig_ars"].isin(zgb))
    return flows[mask].reset_index(drop=True)


def expand_to_agents(flows: pd.DataFrame, sampling_rate: float) -> pd.DataFrame:
    """One row per synthetic in-commuter agent (origin/dest Kreis).

    Scales each SvB flow by ``sampling_rate`` and rounds to the nearest integer
    count; flows whose scaled count rounds to zero are dropped. Returns a
    DataFrame with columns [orig_ars, dest_ars], one row per agent.
    """
    rows = []
    for _, r in flows.iterrows():
        n = int(round(float(r["flow"]) * sampling_rate))
        rows.extend([(r["orig_ars"], r["dest_ars"])] * n)
    return pd.DataFrame(rows, columns=["orig_ars", "dest_ars"])


def make_incommuter_ids(n: int, n_residents: int, n_resident_households: int) -> pd.DataFrame:
    """Collision-free person/household ids for ``n`` in-commuters.

    Resident ids occupy [0, n_residents) and [0, n_resident_households); the
    in-commuter ids therefore start at ``n_residents`` / ``n_resident_households``.
    Each in-commuter forms its own single-person household.
    """
    return pd.DataFrame({
        "person_id": np.arange(n_residents, n_residents + n, dtype=np.int64),
        "household_id": np.arange(n_resident_households,
                                  n_resident_households + n, dtype=np.int64),
    })


def empty_frame_like(frame: pd.DataFrame) -> pd.DataFrame:
    """Zero-row copy of ``frame`` preserving columns/dtypes.

    Used on the flag-OFF path so the downstream population merge is a perfect
    no-op and the baseline output stays byte-identical when the feature is off.
    """
    return frame.iloc[0:0].copy()
