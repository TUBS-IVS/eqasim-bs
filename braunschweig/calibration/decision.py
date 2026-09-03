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

import logging

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_EMD_THRESHOLD = 0.08   # project convention (docs/features/calibration-corner.md)
DEFAULT_MIN_PERSONS = 200


def classify_cell(emd, noise_floor, n_reference_persons, emd_threshold=DEFAULT_EMD_THRESHOLD) -> str:
    """Classify a cell as gap, ok, within_noise, or no_reference.

    Parameters
    ----------
    emd : float or None or NaN
        Earth Mover Distance between model and reference, normalised to [0, 1].
        If None or NaN, returns "no_reference".
    noise_floor : float or None or NaN
        Bootstrap noise floor (uncertainty range) at the reference, in [0, 1].
        If None or NaN, the cell has no usable reference and returns "no_reference".
    n_reference_persons : int or None or NaN
        Number of reference persons in this cell.
        If None, NaN, or <= 0, returns "no_reference".
    emd_threshold : float
        Project threshold; default 0.08 (pre-registered).

    Returns
    -------
    str
        One of:
        - "no_reference": any of emd, noise_floor, n_reference_persons is None/NaN,
                         or n_reference_persons <= 0. A gap cannot be declared without a
                         known reference and noise floor.
        - "ok": emd <= emd_threshold (model fits reference within project tolerance).
        - "within_noise": emd > emd_threshold but emd <= noise_floor
                         (model differs from reference but within its own uncertainty).
        - "gap": emd > emd_threshold and emd > noise_floor (deficiency warranting calibration).
    """
    if pd.isna(emd) or pd.isna(noise_floor) or pd.isna(n_reference_persons) or n_reference_persons <= 0:
        return "no_reference"
    if emd <= emd_threshold:
        return "ok"
    if emd <= noise_floor:
        return "within_noise"
    return "gap"


def decide_layer(cells: pd.DataFrame, emd_threshold=DEFAULT_EMD_THRESHOLD,
                 min_persons=DEFAULT_MIN_PERSONS) -> dict:
    """Decide whether to build a calibration layer based on gap detection in decisive cells.

    Parameters
    ----------
    cells : pd.DataFrame
        Input frame with required columns: code (str), n_reference_persons (int),
        emd (float), noise_floor (float), is_aggregate (bool).
        EXACTLY ONE row must have is_aggregate == True.
        All codes must be unique.
    emd_threshold : float
        Threshold for classifying a cell as gap; default 0.08 (pre-registered).
    min_persons : int
        Minimum reference persons to make a non-aggregate cell decisive; default 200.

    Returns
    -------
    dict
        Keys:
        - "build" (bool): True if any decisive cell is a gap.
        - "reason" (str): Explanation of the decision and decision parameters.
        - "gap_codes" (list of str): Codes of cells classified as gap and decisive.
        - "classification" (dict): {code -> label} for all cells.

    Raises
    ------
    ValueError
        If required columns are missing, frame is empty, codes are not unique,
        or aggregate count != 1.
    """
    # Validate input.
    required = {"code", "n_reference_persons", "emd", "noise_floor", "is_aggregate"}
    missing = required - set(cells.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if cells.empty:
        raise ValueError("no cells to decide")

    duplicates = cells[cells["code"].duplicated()]["code"].unique().tolist()
    if duplicates:
        raise ValueError(f"Duplicate codes: {duplicates}")

    aggregate_count = (cells["is_aggregate"] == True).sum()
    if aggregate_count != 1:
        raise ValueError(f"Expected exactly 1 aggregate row, found {aggregate_count}")

    # Classify and collect gaps.
    classification = {}
    gap_codes = []
    label_counts = {"ok": 0, "gap": 0, "within_noise": 0, "no_reference": 0}
    no_ref_codes = []

    for row in cells.itertuples(index=False):
        label = classify_cell(row.emd, row.noise_floor, row.n_reference_persons, emd_threshold)
        classification[str(row.code)] = label
        label_counts[label] += 1
        if label == "no_reference":
            no_ref_codes.append(str(row.code))

        decisive = bool(row.is_aggregate) or row.n_reference_persons >= min_persons
        if label == "gap" and decisive:
            gap_codes.append(str(row.code))

    # Build reason string.
    if gap_codes:
        reason = (f"build: gap (EMD > {emd_threshold} and > noise floor) in decisive cell(s) "
                  f"{gap_codes} (Kreis with >= {min_persons} reference persons, or the aggregate)")
    else:
        reason = (f"do not build: no gap in any Kreis with >= {min_persons} reference persons "
                  f"nor in the aggregate (EMD threshold {emd_threshold}, noise floor respected)")

    if no_ref_codes:
        reason += f"; {len(no_ref_codes)} cell(s) without a usable reference: {no_ref_codes}"

    # Log the outcome.
    logger.info(
        f"decide_layer: {len(cells)} cells, labels: ok={label_counts['ok']}, "
        f"gap={label_counts['gap']}, within_noise={label_counts['within_noise']}, "
        f"no_reference={label_counts['no_reference']}; build={bool(gap_codes)}"
    )

    return {"build": bool(gap_codes), "reason": reason, "gap_codes": gap_codes,
            "classification": classification}
