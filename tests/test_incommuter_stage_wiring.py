"""Stage-wiring checks for the per-Bundesland in-commuter mode reference (#129)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.mikrozensus.reference import source_bundeslaender  # noqa: E402


def test_source_bundeslaender_from_pendler_flows():
    # execute() derives the set of source Bundeslaender from the pendler flows'
    # orig_ars column; only Niedersachsen (03) and Sachsen-Anhalt (15) appear here.
    flows = pd.DataFrame({
        "orig_ars": ["03241", "03101", "15001", "03151"],
        "dest_ars": ["03101", "03101", "03101", "03101"],
        "flow": [10, 20, 5, 7],
    })
    assert source_bundeslaender(flows["orig_ars"]) == ["Niedersachsen", "Sachsen-Anhalt"]


def test_configure_declares_default_on_flag():
    # configure() must declare the new flag with default True when the cordon is enabled.
    import braunschweig.synthesis.incommuters as inc

    seen = {}

    class _Ctx:
        def config(self, key, default=None):
            seen[key] = default
            # cordon must be enabled so configure() proceeds past the early return.
            if key == "cordon_enabled":
                return True
            # keep the real_origin branch (extra stages/configs) out of this unit test.
            if key == "cordon_incommuter_real_origin":
                return False
            return default

        def stage(self, *a, **k):
            pass

    inc.configure(_Ctx())
    assert "cordon_incommuter_mode_reference_by_bundesland" in seen
    assert seen["cordon_incommuter_mode_reference_by_bundesland"] is True
