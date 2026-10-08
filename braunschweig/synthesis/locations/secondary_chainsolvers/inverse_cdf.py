"""The fixed-order inverse-CDF draw shared by the subtype deciders and the SrV location types.

A leaf module with no first-party imports, moved verbatim out of ``deciders``: the SrV
location-type draw needs only this function, and importing it from ``deciders`` put the whole
decider machinery (MiD loading, subtype estimation) into the token of every stage that reaches
``srv_location_types``, the candidates stage among them (ADR-0136).
"""
from __future__ import annotations

from typing import Dict

import numpy as np


def _inverse_cdf_choice(probs: Dict[str, float], group_names, draw: float) -> str:
    """Return the name in ``group_names`` whose cumulative probability first
    exceeds ``draw`` (standard inverse-CDF sampling): walk ``group_names`` in
    the given fixed order while accumulating a running sum, and pick the first
    entry whose cumulative probability exceeds ``draw``.

    This is exactly the per-leg selection rule
    ``braunschweig.popsim.purpose_subtype.impute_groups`` applies internally
    (see that function's determinism note) -- reused here as a plain one-leg
    helper INSTEAD OF calling ``impute_groups`` once per leg, because
    ``impute_groups`` is designed for a single BATCHED call over many legs and
    logs an aggregate marginal-fallback-rate message on every invocation.
    Calling it with a length-1 batch (as the per-leg decider architecture
    requires, mirroring ``_build_shop_subtype_decider``) would therefore emit
    one log line per fallback leg -- log spam at population scale (millions of
    legs). This helper performs the identical maths (one draw, a fixed-order
    cumulative sum, first-exceeding-index selection) without that per-call
    logging; the MODEL-level fallback rate (how many (mode, tt_band) cells got
    their own estimate vs. the marginal) is already logged once, at
    decider-build time, by ``estimate_group_probabilities`` itself -- so no
    fallback-rate signal is lost, only the per-leg spam.
    """
    cumulative = np.cumsum([probs.get(name, 0.0) for name in group_names])
    choice = int(np.clip(np.searchsorted(cumulative, draw, side="right"), 0, len(group_names) - 1))
    return group_names[choice]
