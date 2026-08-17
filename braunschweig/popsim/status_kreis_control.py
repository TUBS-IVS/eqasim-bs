"""Backward-compat shim. The economic_status x Kreis control is now one entry in the generic
kreis_attribute_control REGISTRY (S1a). This module re-exports the economic_status pieces so
existing imports/tests keep working; new code should use kreis_attribute_control directly.
"""
from __future__ import annotations

import pandas as pd

from braunschweig.popsim.kreis_attribute_control import (
    REGISTRY,
    control_columns,
    attribute_kreis_count_table,
    _shrunk_shares,
)

_ECON = next(c for c in REGISTRY if c.name == "economic_status")

# L1-compatible name: the five economic_status_{class} control columns.
STATUS_CONTROL_COLUMNS = control_columns(_ECON)


def shrunk_status_shares(h4, *, prior_n: float = 0.0) -> pd.DataFrame:
    """L1-compatible wrapper: per-Kreis economic_status shares (ars5 + very_low..very_high),
    Dirichlet-shrunk toward the ZGB aggregate, via the generic derivation."""
    return _shrunk_shares(_ECON, h4, prior_n)


def status_kreis_count_table(h4, hh_total_by_ars5, *, prior_n: float = 0.0) -> pd.DataFrame:
    """L1-compatible wrapper: the economic_status per-Kreis count table via the generic derivation.

    ``h4`` is the committed H4 frame (ars5 + very_low..very_high); its columns equal the
    economic_status registry entry's target_columns, so it is consumed directly.
    """
    return attribute_kreis_count_table(_ECON, h4, hh_total_by_ars5, prior_n=prior_n)
