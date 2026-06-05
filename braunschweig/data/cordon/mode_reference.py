"""Mode / distance reference helpers for cross-cordon commuters (pure).

Ported from the proven ``feature/cordon-incommuters`` branch
(``mode_reference_from_table``, ``rake_to_margins``, ``commute_distance_band``,
``route_distance_band``) and re-homed into the cordon module so the data loader
(``braunschweig.data.mikrozensus.reference``) and the demand synthesis can share
one region-neutral implementation. Pure numpy/pandas.

See ``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# MiD 2023 wegkm_imp distance-class upper bounds (km). Pinned so the distance
# banding matches the MiD/Mikrozensus reference tables.
MID_DISTANCE_EDGES = (0.5, 1, 2, 5, 10, 20, 50, 100)


def mode_reference_from_table(tidy: pd.DataFrame) -> dict:
    """Nested mode reference ``{distance_band: {mode: probability}}`` from a tidy
    long table with columns [distance_band, mode, probability].

    Probabilities are renormalised to sum to 1 within each band (source values may
    be percentages or independently rounded). Raises ValueError on missing
    columns, negative probabilities, or a band summing to zero.
    """
    required = {"distance_band", "mode", "probability"}
    if not required.issubset(tidy.columns):
        raise ValueError(
            f"tidy table must have columns {sorted(required)}, got {list(tidy.columns)}"
        )
    if (tidy["probability"] < 0).any():
        raise ValueError("mode reference contains a negative probability")
    reference = {}
    for band, group in tidy.groupby("distance_band"):
        total = group["probability"].sum()
        if total == 0:
            raise ValueError(f"distance band {band!r} has zero total probability")
        reference[band] = {
            row["mode"]: row["probability"] / total for _, row in group.iterrows()
        }
    return reference


def rake_to_margins(matrix, row_margin, col_margin, iterations: int = 200):
    """Iterative proportional fitting (raking) of a non-negative seed ``matrix``
    to the given row and column margins (1D arrays, each summing to the same
    total). Returns the fitted matrix. Used to anchor the national
    mode-by-distance joint to the Niedersachsen distance + mode margins.
    """
    X = np.array(matrix, dtype=float)
    row_margin = np.asarray(row_margin, dtype=float)
    col_margin = np.asarray(col_margin, dtype=float)
    for _ in range(iterations):
        rs = X.sum(axis=1)
        rs[rs == 0] = 1.0
        X *= (row_margin / rs)[:, None]
        cs = X.sum(axis=0)
        cs[cs == 0] = 1.0
        X *= (col_margin / cs)[None, :]
    return X


def restrict_to_modes(reference: dict, allowed=("car", "pt")) -> dict:
    """Restrict a nested mode reference ``{band: {mode: prob}}`` to ``allowed`` modes,
    renormalising each band to sum to 1.

    Cross-cordon commuters realistically use only car or PT -- walk/bike over a cordon
    are negligible (design assumption), and the raw Mikrozensus reference would
    otherwise assign walk to short gate->work distances. The dropped probability mass
    is redistributed proportionally over the kept modes; if a band has none of the
    allowed modes, it falls back to the first allowed mode with probability 1.
    """
    allowed = tuple(allowed)
    out = {}
    for band, dist in reference.items():
        kept = {m: float(p) for m, p in dist.items() if m in allowed}
        total = sum(kept.values())
        if total > 0:
            out[band] = {m: p / total for m, p in kept.items()}
        else:
            out[band] = {allowed[0]: 1.0}
    return out


def commute_distance_band(distance_km, edges=(5, 10, 25, 50)) -> str:
    """Classify a commute distance (km) into a band with an EXCLUSIVE upper bound.

    ``edges`` is an ascending tuple of upper bounds; a distance falls in the first
    band whose upper bound it is strictly below. Labels: "<e0", "e0-e1", ...,
    ">=e_last". A distance exactly equal to an edge falls in the NEXT band.
    """
    d = float(distance_km)
    for i, upper in enumerate(edges):
        if d < upper:
            if i == 0:
                return f"<{edges[0]}"
            return f"{edges[i - 1]}-{upper}"
    return f">={edges[-1]}"


def route_distance_band(straight_line_km, detour_factor: float = 1.3,
                        edges=MID_DISTANCE_EDGES) -> str:
    """Distance band from the home<->work STRAIGHT-LINE distance (km).

    The MiD bands use the realized ROUTED length, so the straight-line distance is
    scaled by ``detour_factor`` (default 1.3) before binning.
    """
    return commute_distance_band(float(straight_line_km) * float(detour_factor), edges=edges)
