"""placement_income (L2 of #108): donor-coherent income via signature-preserving reallocation.

Each synthetic household keeps its OWN MiD income (a seeded draw within its own
hheink_gr1 codebook bracket); the per-Kreis INKAR income relativity is approached by
permuting WHICH real donors sit in which Kreis, strictly inside exact control-signature
groups so every PopulationSim control aggregate (cell and Kreis) and every donor's
clone count are preserved. Pure module: no file I/O; the stage passes frames in.

Spec: docs/superpowers/specs/2026-07-17-placement-income-l2-design.md.
"""
from __future__ import annotations

import logging
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
# Deliberate same-package reuse of the redraw's bracket machinery (DRY): the own-income
# draw must use the SAME floor / open-top treatment so OFF vs ON differ only by design.
from braunschweig.popsim.income_kreis_control import (
    INCOME_MIN_EUR,
    INCOME_OPEN_TOP_MAX_EUR,
    INCOME_OPEN_TOP_PARETO_ALPHA,
    _draw_truncated_pareto,
    _truncated_pareto_mean,
)

logger = logging.getLogger(__name__)

# Dedicated RNG offset for the own-income within-bracket draw (disjoint from the
# build_persons +74511, kreis-seed +24680, tenure +83947 and redraw +91237 streams).
PLACEMENT_INCOME_RNG_OFFSET = 31771

# MiD hheink_gr1 codebook EUR ranges (monthly net household income). These are the
# bracket BOUNDS of the same codebook whose midpoints are committed in
# attributes.INCOME_GROUP_MIDPOINT_EUR; a unit test ties the two representations
# together so they cannot drift apart. The top bracket is open-ended.
INCOME_LABEL_BOUNDS_EUR: dict[str, tuple[float, float | None]] = {
    "under_500": (0.0, 500.0),
    "500_900": (500.0, 900.0),
    "900_1500": (900.0, 1500.0),
    "1500_2000": (1500.0, 2000.0),
    "2000_2600": (2000.0, 2600.0),
    "2600_3000": (2600.0, 3000.0),
    "3000_3600": (3000.0, 3600.0),
    "3600_4000": (3600.0, 4000.0),
    "4000_4600": (4000.0, 4600.0),
    "4600_5000": (4600.0, 5000.0),
    "5000_5600": (5000.0, 5600.0),
    "5600_6000": (5600.0, 6000.0),
    "6000_6600": (6000.0, 6600.0),
    "6600_7000": (6600.0, 7000.0),
    "over_7000": (7000.0, None),
}


def label_expected_eur(
    *,
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> dict[str, float]:
    """Expected EUR per income label, matching what draw_own_income_eur realises.

    Closed brackets: uniform on [max(low, INCOME_MIN_EUR), high) -> mean of the two.
    Open top: truncated-Pareto mean on [low, INCOME_OPEN_TOP_MAX_EUR] (default), else
    the exponential-tail mean used by the redraw path. Used for reallocation targeting
    so target and draw agree in expectation.
    """
    out: dict[str, float] = {}
    for label, (low, high) in INCOME_LABEL_BOUNDS_EUR.items():
        if high is None:
            if open_top_pareto:
                out[label] = _truncated_pareto_mean(low, INCOME_OPEN_TOP_MAX_EUR, pareto_alpha)
            else:
                out[label] = low * 1.4
        else:
            out[label] = (max(low, INCOME_MIN_EUR) + high) / 2.0
    return out


def draw_own_income_eur(
    labels: pd.Series,
    rng,
    *,
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> np.ndarray:
    """Seeded continuous EUR within each household's OWN income label bracket.

    NaN / unknown labels stay NaN (the caller keeps today's NaN shielding). Closed
    brackets draw uniform on [max(low, INCOME_MIN_EUR), high); the open top draws a
    truncated Pareto on [7000, INCOME_OPEN_TOP_MAX_EUR] — identical tail treatment to
    the income_kreis_control redraw, so distributions stay comparable OFF vs ON.
    Returns values rounded to whole EUR.
    """
    values = labels.astype("object").to_numpy()
    n = len(values)
    eur = np.full(n, np.nan, dtype=float)
    known = np.array([v in INCOME_LABEL_BOUNDS_EUR for v in values], dtype=bool)
    n_unknown_nonnull = int(sum(1 for v in values[~known] if isinstance(v, str)))
    if n_unknown_nonnull:
        logger.warning(
            "[placement_income] %d/%d labels are non-null but outside the codebook "
            "vocabulary; their income stays NaN (investigate upstream mapping).",
            n_unknown_nonnull, n,
        )
    # Draw label by label (15 labels max) so each subgroup consumes its own RNG block
    # deterministically in label order.
    for label in INCOME_LABEL_BOUNDS_EUR:
        mask = known & (values == label)
        m = int(mask.sum())
        if m == 0:
            continue
        low, high = INCOME_LABEL_BOUNDS_EUR[label]
        if high is None:
            if open_top_pareto:
                eur[mask] = _draw_truncated_pareto(m, low, INCOME_OPEN_TOP_MAX_EUR, pareto_alpha, rng)
            else:
                eur[mask] = np.minimum(low * (1.0 + rng.exponential(scale=0.4, size=m)),
                                       INCOME_OPEN_TOP_MAX_EUR)
        else:
            lo = max(low, INCOME_MIN_EUR)
            eur[mask] = lo + rng.random_sample(m) * (high - lo)
    return np.round(eur, 0)


def donor_expected_income_eur(
    donor_households: pd.DataFrame,
    *,
    group_col: str = "hheink_gr1",
    donor_col: str = "H_ID",
    open_top_pareto: bool = True,
    pareto_alpha: float = INCOME_OPEN_TOP_PARETO_ALPHA,
) -> pd.Series:
    """Per-donor expected own income (EUR) from the RAW hheink_gr1 code.

    Codes outside 1..15 (missing / refused) -> NaN: such donors get a NEUTRAL weight in
    the reallocation and are excluded from means; their share is logged by the caller.
    Uses the raw code (no imputation) so targeting never invents an income.
    """
    if group_col not in donor_households.columns or donor_col not in donor_households.columns:
        raise ValueError(
            f"[placement_income] donor_expected_income_eur needs columns "
            f"{[donor_col, group_col]}; got {sorted(donor_households.columns)[:20]}."
        )
    exp = label_expected_eur(open_top_pareto=open_top_pareto, pareto_alpha=pareto_alpha)
    codes = pd.to_numeric(donor_households[group_col], errors="coerce")
    labels = codes.map(INCOME_CLASS_BY_GROUP)
    values = labels.map(exp)
    out = pd.Series(values.to_numpy(dtype=float), index=donor_households[donor_col].to_numpy())
    out.index.name = donor_col
    return out
