"""Unit tests for the pure gate logic in scripts/gate_kreis_income_control.py.

Coverage:
  - decide_gate: KEEP on improvement + correct between-Kreis order
  - decide_gate: FLIP when ON realism worsens beyond tolerance
  - decide_gate: FLIP when between-Kreis order is violated on ON run
  - decide_gate: fail-open on absent/None diagnostics (never force a flip)
  - summarize: KS / coherence / kreis_mean computed correctly on tiny synthetic frame
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

# Make the repo root importable so the scripts/ module can be found.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import via importlib.util (mirrors test_gate_income_tilt.py pattern).
import importlib.util as _ilu
_gate_spec = _ilu.spec_from_file_location(
    "gate_kreis_income_control",
    str(_REPO_ROOT / "scripts" / "gate_kreis_income_control.py"),
)
_gate_mod = _ilu.module_from_spec(_gate_spec)  # type: ignore[arg-type]
_gate_spec.loader.exec_module(_gate_mod)  # type: ignore[union-attr]

decide_gate = _gate_mod.decide_gate
summarize = _gate_mod.summarize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summary(ks, incoherent, sz_mean, wob_mean):
    return {"ks_to_mid": ks, "incoherent_fraction": incoherent,
            "kreis_mean": {"03102": sz_mean, "03103": wob_mean}}


# ---------------------------------------------------------------------------
# decide_gate tests
# ---------------------------------------------------------------------------

def test_keep_when_realism_and_coherence_improve_and_order_correct():
    off = _summary(0.30, 0.25, 2400, 2400)   # off: spikes, mislabeled, no spread
    on = _summary(0.08, 0.00, 2100, 2600)    # on: realistic, coherent, SZ<WOB
    rec, code = decide_gate(off, on)
    assert rec == "KEEP_DEFAULT_ON"
    assert code == 0


def test_flip_when_on_worse_realism():
    off = _summary(0.10, 0.00, 2100, 2600)
    on = _summary(0.30, 0.00, 2100, 2600)
    rec, code = decide_gate(off, on)
    assert rec == "FLIP_DEFAULT_OFF"
    assert code == 1


def test_flip_when_order_violated_on():
    off = _summary(0.30, 0.25, 2400, 2400)
    on = _summary(0.08, 0.00, 2600, 2100)    # SZ>WOB: relativity wrong
    rec, code = decide_gate(off, on)
    assert rec == "FLIP_DEFAULT_OFF"


def test_missing_kpi_fails_open_keep():
    off = _summary(0.30, 0.25, 2400, 2400)
    on = {"ks_to_mid": None, "incoherent_fraction": None, "kreis_mean": {}}
    rec, code = decide_gate(off, on)
    assert rec == "KEEP_DEFAULT_ON"  # absent diagnostics must not force a flip


# ---------------------------------------------------------------------------
# summarize tests
# ---------------------------------------------------------------------------

def test_summarize_computes_kpis_on_tiny_frame():
    # two households, one per Kreis; household_income label matches EUR for hh1, not hh2
    persons = pd.DataFrame({
        "household_id": [1, 1, 2],
        "departement_id": ["03102", "03102", "03103"],
        "household_income_eur": [1200.0, 1200.0, 8000.0],
        "household_income": ["900_1500", "900_1500", "under_500"],  # hh2 mislabeled
    })
    ref = np.array([1000.0, 1500.0, 3000.0, 8000.0])
    s = summarize(persons, ref)
    assert set(s["kreis_mean"]) == {"03102", "03103"}
    assert s["kreis_mean"]["03102"] == 1200.0
    assert 0.0 <= s["ks_to_mid"] <= 1.0
    # exactly one of two households is incoherent (hh2's label band != 8000)
    assert s["incoherent_fraction"] == pytest.approx(0.5)
