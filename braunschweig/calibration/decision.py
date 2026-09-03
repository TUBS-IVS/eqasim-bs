"""Pre-registered decision rule for the SrV distance-distribution calibration (spec 5.4).

Fixed BEFORE the baseline measurement so the build/no-build decision cannot be tuned
post hoc. A cell is a GAP when its model-vs-reference EMD exceeds both the project
threshold and the reference's own bootstrap noise floor. A layer is BUILT when at least
one home Kreis with >= ``min_persons`` reference persons is a gap, or the aggregate is.

EMD values are NORMALISED band EMDs in [0, 1] (same unit as
`braunschweig.calibration.metrics.emd_on_bands`); the threshold 0.08 and the noise
floors are on that scale.
"""
from __future__ import annotations

import math

import pandas as pd

DEFAULT_EMD_THRESHOLD = 0.08   # project convention (docs/features/calibration-corner.md)
DEFAULT_MIN_PERSONS = 200


def classify_cell(emd, noise_floor, n_reference_persons, emd_threshold=DEFAULT_EMD_THRESHOLD) -> str:
    if n_reference_persons is None or n_reference_persons <= 0 or emd is None or (
            isinstance(emd, float) and math.isnan(emd)):
        return "no_reference"
    if emd <= emd_threshold:
        return "ok"
    if emd <= (noise_floor or 0.0):
        return "within_noise"
    return "gap"


def decide_layer(cells: pd.DataFrame, emd_threshold=DEFAULT_EMD_THRESHOLD,
                 min_persons=DEFAULT_MIN_PERSONS) -> dict:
    classification = {}
    gap_codes = []
    for row in cells.itertuples(index=False):
        label = classify_cell(row.emd, row.noise_floor, row.n_reference_persons, emd_threshold)
        classification[str(row.code)] = label
        decisive = bool(row.is_aggregate) or row.n_reference_persons >= min_persons
        if label == "gap" and decisive:
            gap_codes.append(str(row.code))
    if gap_codes:
        reason = (f"build: gap (EMD > {emd_threshold} and > noise floor) in decisive cell(s) "
                  f"{gap_codes} (Kreis with >= {min_persons} reference persons, or the aggregate)")
    else:
        reason = (f"do not build: no gap in any Kreis with >= {min_persons} reference persons "
                  f"nor in the aggregate (EMD threshold {emd_threshold}, noise floor respected)")
    return {"build": bool(gap_codes), "reason": reason, "gap_codes": gap_codes,
            "classification": classification}
