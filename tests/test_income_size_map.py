"""Regression tests for household-size -> income-bin resolution.

The household-income reference (MiD H4 / INKAR by household size) uses an
open-ended top category: "6+" (six or more persons) for the Braunschweig 6-bin
scheme, "5+" for the Bavaria 5-bin scheme. The IPF household formation, however,
produces real households of any size (7, 8, ... up to ~11). Mapping those onto
the reference must collapse every size above the top category onto it; otherwise
``braunschweig.synthesis.population.enriched._execute_base`` aborts the whole
pipeline with "income_size_map produced bins not present in df_income"
(observed: unresolved sizes ['7','8','9','10','11'] against ['1'..'6+']).

These tests pin the resolution so the regression cannot reappear.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _build_income_size_map,
    _income_bin_for_size,
)

SIX_BIN = {"1", "2", "3", "4", "5", "6+"}
FIVE_BIN = {"1", "2", "3", "4", "5+"}


def test_build_map_recognises_six_bin_scheme():
    _mapping, scheme = _build_income_size_map(SIX_BIN)
    assert scheme == "6-bin"


def test_build_map_recognises_five_bin_scheme():
    _mapping, scheme = _build_income_size_map(FIVE_BIN)
    assert scheme == "5-bin"


def test_six_bin_large_households_collapse_onto_top_category():
    """Sizes 7..11 must resolve to '6+' (the open-ended top bin), not to
    themselves -- this is the exact regression that crashed the pipeline."""
    income_size_map, scheme = _build_income_size_map(SIX_BIN)
    for size in ["6", "7", "8", "9", "10", "11"]:
        assert _income_bin_for_size(size, income_size_map, scheme) == "6+"


def test_five_bin_large_households_collapse_onto_top_category():
    income_size_map, scheme = _build_income_size_map(FIVE_BIN)
    for size in ["5", "6", "7", "11"]:
        assert _income_bin_for_size(size, income_size_map, scheme) == "5+"


def test_small_households_map_to_their_own_bin():
    income_size_map, scheme = _build_income_size_map(SIX_BIN)
    for size in ["1", "2", "3", "4", "5"]:
        assert _income_bin_for_size(size, income_size_map, scheme) == size


def test_every_resolved_size_is_present_in_the_reference_bins():
    """The pipeline's hard invariant: no resolved bin may be absent from the
    reference table. Exercise the full plausible IPF size range for both schemes.
    """
    sizes = [str(n) for n in range(1, 12)] + ["6+", "5+"]
    for bins in (SIX_BIN, FIVE_BIN):
        income_size_map, scheme = _build_income_size_map(bins)
        resolved = {
            _income_bin_for_size(s, income_size_map, scheme)
            for s in sizes
            # "6+"/"5+" literals only belong to their own scheme
            if s.isdigit() or s in bins
        }
        assert resolved <= bins, (
            f"{scheme}: resolved bins {sorted(resolved - bins)} are not in the "
            f"reference {sorted(bins)}"
        )
