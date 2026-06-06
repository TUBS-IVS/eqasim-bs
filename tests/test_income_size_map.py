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

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    INCOME_MIDPOINT_FALLBACK_EUR,
    _apply_inkar_income_scale,
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


# ---------------------------------------------------------------------------
# Fallback transparency: income class-midpoint PRIMARY vs FALLBACK
#
# ``_apply_inkar_income_scale`` maps each person's ``household_income`` class
# label onto a € midpoint. A label present in the midpoint table is a PRIMARY
# hit; a label absent from the table falls back to the hard-coded median
# ``INCOME_MIDPOINT_FALLBACK_EUR``. These tests pin that the primary mapping is
# fully taken when every class is known (fallback count == 0) and that an
# unknown class is counted as a fallback -- without changing any computed value.
# ---------------------------------------------------------------------------

# Minimal class-midpoint table mirroring mid2023_class_midpoint_eur.csv.
_MIDPOINT_EUR = {
    "0-500": 250.0,
    "1500-2000": 1750.0,
    "2600-3000": 2800.0,
    "3600-4500": 4050.0,
    "5000+": 6000.0,
}


def _persons(income_classes, kreise):
    return pd.DataFrame({
        "household_income": income_classes,
        "_kreis": kreise,
    })


def test_income_midpoint_primary_fully_taken_when_all_classes_known():
    """Every household_income class is in the midpoint table -> fallback count 0,
    primary count == number of persons, and the EUR values use the real
    per-class midpoints (no value replaced by the fallback)."""
    classes = ["0-500", "1500-2000", "2600-3000", "3600-4500", "5000+"]
    kreis = pd.Series(["03101"] * len(classes))
    df = _persons(classes, kreis.tolist())
    df_inkar = pd.DataFrame({"ars5": ["03101"], "scale": [1.0]})

    out = _apply_inkar_income_scale(df, df_inkar, _MIDPOINT_EUR, kreis=kreis)

    assert out.attrs["income_midpoint_fallback_count"] == 0
    assert out.attrs["income_midpoint_primary_count"] == len(classes)
    assert out.attrs["income_midpoint_fallback_rate"] == 0.0
    # Scale 1.0 -> EUR equals the primary midpoints exactly (no fallback applied).
    expected = [_MIDPOINT_EUR[c] for c in classes]
    assert out["household_income_eur"].tolist() == expected
    assert INCOME_MIDPOINT_FALLBACK_EUR not in (
        v for c, v in _MIDPOINT_EUR.items()
        if c not in ("2600-3000",)
    )


def test_income_midpoint_fallback_counted_for_unknown_class():
    """A person whose household_income class is absent from the midpoint table
    is counted as a fallback and receives INCOME_MIDPOINT_FALLBACK_EUR (2800),
    while the known-class persons stay on their primary midpoints."""
    classes = ["0-500", "9999-UNKNOWN", "5000+"]
    kreis = pd.Series(["03101"] * len(classes))
    df = _persons(classes, kreis.tolist())
    df_inkar = pd.DataFrame({"ars5": ["03101"], "scale": [1.0]})

    out = _apply_inkar_income_scale(df, df_inkar, _MIDPOINT_EUR, kreis=kreis)

    assert out.attrs["income_midpoint_fallback_count"] == 1
    assert out.attrs["income_midpoint_primary_count"] == 2
    assert out.attrs["income_midpoint_fallback_rate"] == 1 / 3
    # The unknown class got the fallback midpoint; the known classes did not.
    assert out["household_income_eur"].tolist() == [
        _MIDPOINT_EUR["0-500"],
        INCOME_MIDPOINT_FALLBACK_EUR,
        _MIDPOINT_EUR["5000+"],
    ]


# ---------------------------------------------------------------------------
# Fallback transparency: INCOME_CLASS_MAP -> H4 quintile-position lookup
#
# household_income.execute() reads each (income_class, idx) of INCOME_CLASS_MAP
# from the H4 share vector at position ``idx``. A position present in the loaded
# vector is a PRIMARY hit; a position beyond the vector length is a FALLBACK.
# These tests pin that a complete 5-quintile H4 table is fully PRIMARY (no
# fallback, weights unchanged) and that a truncated vector is detected as a
# fallback rather than silently defaulted.
# ---------------------------------------------------------------------------

class _StubContext:
    def __init__(self, config):
        self._config = config

    def config(self, key, default=...):
        if key in self._config:
            return self._config[key]
        if default is not ...:
            return default
        raise KeyError(key)


def test_income_class_map_primary_fully_taken(monkeypatch, capsys):
    """A complete 5-quintile H4 table makes every INCOME_CLASS_MAP cell a
    PRIMARY hit: no fallback, and the weights equal share * 1e6 unchanged."""
    from braunschweig.data.census import household_income as hh

    income_by_size = {
        "1": (0.2, 0.2, 0.2, 0.2, 0.2),
        "2": (0.1, 0.2, 0.3, 0.2, 0.2),
    }
    monkeypatch.setattr(hh, "load_income_by_size", lambda _p: income_by_size)

    out = hh.execute(_StubContext({"data_path": "ignored"}))

    # 2 sizes x 5 classes = 10 PRIMARY cells, none dropped.
    assert len(out) == len(income_by_size) * len(hh.INCOME_CLASS_MAP)
    # Spot-check a primary weight is the unmodified share * 1e6.
    row = out[(out["household_size"] == "1") & (out["income_class"] == "5000+")]
    assert float(row["weight"].iloc[0]) == 0.2 * 1e6
    log = capsys.readouterr().out
    assert "PRIMARY lookup hit all 10/10" in log
    assert "WARNING" not in log


def test_income_class_map_fallback_detected_for_truncated_vector(monkeypatch):
    """A truncated H4 vector (missing the top quintile) is detected as a
    FALLBACK and raised, not silently defaulted -- the count is observable."""
    from braunschweig.data.census import household_income as hh

    # Only 4 quintiles -> position idx==4 ("5000+") is missing -> fallback.
    income_by_size = {"1": (0.25, 0.25, 0.25, 0.25)}
    monkeypatch.setattr(hh, "load_income_by_size", lambda _p: income_by_size)

    with pytest.raises(ValueError, match="quintile positions absent"):
        hh.execute(_StubContext({"data_path": "ignored"}))
