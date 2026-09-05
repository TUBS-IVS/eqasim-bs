"""Pre-registered decision rule for the SrV distance-distribution calibration (spec 5.4).

Fixed BEFORE the baseline measurement so the build/no-build decision cannot be tuned
post hoc. A cell is a GAP when its model-vs-reference EMD exceeds both the project
threshold and the reference's own bootstrap noise floor. A layer is BUILT when at least
one home Kreis with >= ``min_persons`` reference persons is a gap, or the aggregate is.

EMD values are NORMALISED band EMDs in [0, 1] (same unit as
`braunschweig.calibration.metrics.emd_on_bands`); the threshold 0.08 and the noise
floors are on that scale.

AMENDMENT (2026-09-04, disclosed in ADR-0103, section "Amendment 2026-09-04"): the
2026-09-03 pre-registered rule above exempted the aggregate row from the
``min_persons`` floor UNCONDITIONALLY (``decisive = is_aggregate or n_reference_persons
>= min_persons``), so a thin aggregate reference (e.g. n=142 for the university level)
could single-handedly force ``build=True`` on a reference too small to be scientifically
decisive. This amendment, made AFTER the 2026-09-03 baseline measurement, sharpens the
rule via the new ``aggregate_requires_min_persons`` parameter of :func:`decide_layer`:
with it True (the new default) the aggregate is decisive under the SAME
``n_reference_persons >= min_persons`` floor as every other cell; passing False restores
the original 2026-09-03 behaviour (kept only for the exemption-pinning regression test).
More generally (fix round 1, whole-branch review of #358): whenever NO cell in the frame
is decisive at all -- not merely when the aggregate itself is a gap -- the rule has
nothing decisive to check and cannot certify "do not build" either; this is reported via
the ``"undecidable"`` result key rather than silently resolving to ``build=False``. A
"do not build" verdict is only asserted when at least one cell WAS actually decisive
(and none of the decisive cells was a gap); the reason string then names how many Kreise
were decisive and whether the aggregate itself was decisive, rather than blanket-
asserting "nor in the aggregate" when the aggregate was never actually checked.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_EMD_THRESHOLD = 0.08   # project convention (docs/features/calibration-corner.md)
DEFAULT_MIN_PERSONS = 200
#: AMENDMENT (2026-09-04, ADR-0103): see the module docstring. True sharpens the
#: pre-registered rule so the aggregate row needs the same min_persons floor as any
#: other cell; False restores the original 2026-09-03 unconditional exemption.
DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS = True


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
                 min_persons=DEFAULT_MIN_PERSONS,
                 aggregate_requires_min_persons=DEFAULT_AGGREGATE_REQUIRES_MIN_PERSONS) -> dict:
    """Decide whether to build a calibration layer based on gap detection in decisive cells.

    A cell is decisive iff ``n_reference_persons >= min_persons``, OR (``is_aggregate``
    and NOT ``aggregate_requires_min_persons``) -- see the AMENDMENT (2026-09-04,
    ADR-0103) in the module docstring for why the aggregate no longer gets an
    unconditional exemption by default.

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
        Minimum reference persons to make a cell decisive; default 200.
    aggregate_requires_min_persons : bool
        AMENDMENT (2026-09-04, ADR-0103): if True (default), the aggregate row is
        decisive only when it too satisfies ``n_reference_persons >= min_persons``,
        like any other cell. If False, the aggregate is unconditionally decisive, as
        in the original 2026-09-03 pre-registered rule (kept only so the exemption
        can still be pinned by a regression test).

    Returns
    -------
    dict
        Keys:
        - "build" (bool): True if any decisive cell is a gap.
        - "undecidable" (bool): True if the rule could not certify either verdict --
          NO cell in the frame is decisive at all (regardless of whether any
          non-decisive cell happens to be a gap), so there is nothing decisive to
          check and neither "build" nor "do not build" is defensible. Always False
          whenever at least one cell is decisive (including when
          ``aggregate_requires_min_persons`` is False, since the aggregate is then
          always decisive).
        - "reason" (str): Explanation of the decision and decision parameters.
        - "gap_codes" (list of str): Codes of cells classified as gap and decisive.
        - "classification" (dict): {code -> label} for all cells (labels unchanged by
          the amendment: "ok", "gap", "within_noise", "no_reference").

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
    n_decisive_kreis = 0        # decisive NON-aggregate cells (Kreise with >= min_persons)
    aggregate_n = None
    aggregate_decisive = False

    for row in cells.itertuples(index=False):
        label = classify_cell(row.emd, row.noise_floor, row.n_reference_persons, emd_threshold)
        classification[str(row.code)] = label
        label_counts[label] += 1
        if label == "no_reference":
            no_ref_codes.append(str(row.code))

        is_aggregate = bool(row.is_aggregate)
        decisive = (row.n_reference_persons >= min_persons) or (
            is_aggregate and not aggregate_requires_min_persons)
        if is_aggregate:
            aggregate_n = int(row.n_reference_persons)
            aggregate_decisive = decisive
        elif decisive:
            n_decisive_kreis += 1
        if label == "gap" and decisive:
            gap_codes.append(str(row.code))

    # Fix round 1 (whole-branch review of #358): the rule is undecidable whenever NO
    # cell in the frame is decisive at all, regardless of whether a non-decisive cell
    # happens to be a gap -- there being a non-decisive gap does not by itself make the
    # verdict undecidable if some OTHER cell was actually decisive and gap-free.
    any_decisive = aggregate_decisive or n_decisive_kreis > 0
    undecidable = (not gap_codes) and (not any_decisive)

    # Build reason string.
    if gap_codes:
        reason = (f"build: gap (EMD > {emd_threshold} and > noise floor) in decisive cell(s) "
                  f"{gap_codes} (Kreis with >= {min_persons} reference persons, or the aggregate)")
    elif undecidable:
        reason = (f"not decidable: no cell reaches the >= {min_persons}-person floor "
                  f"(aggregate n={aggregate_n}) and no decisive gap exists")
    else:
        aggregate_word = "decisive" if aggregate_decisive else "non-decisive"
        reason = (f"do not build: no gap in any decisive cell ({n_decisive_kreis} Kreise with "
                  f">= {min_persons} persons; aggregate {aggregate_word} with n={aggregate_n})")

    if no_ref_codes:
        reason += f"; {len(no_ref_codes)} cell(s) without a usable reference: {no_ref_codes}"

    # Log the outcome.
    logger.info(
        f"decide_layer: {len(cells)} cells, labels: ok={label_counts['ok']}, "
        f"gap={label_counts['gap']}, within_noise={label_counts['within_noise']}, "
        f"no_reference={label_counts['no_reference']}; build={bool(gap_codes)}, "
        f"undecidable={undecidable}"
    )

    return {"build": bool(gap_codes), "undecidable": undecidable, "reason": reason,
            "gap_codes": gap_codes, "classification": classification}
