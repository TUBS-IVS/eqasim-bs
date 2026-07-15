"""Tests for the ARS-prefix -> Bundesland lookup used by the in-commuter mode reference."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.mikrozensus.reference import (  # noqa: E402
    BUNDESLAND_BY_ARS2,
    bundesland_of_ars,
    source_bundeslaender,
)

_CSV = os.path.join(
    str(REPO), "eqasim-data", "data", "braunschweig", "mikrozensus",
    "mid_mode_margin_by_bundesland.csv")


def test_bundesland_of_ars_known_prefixes():
    assert bundesland_of_ars("03159001") == "Niedersachsen"     # ZGB / NDS
    assert bundesland_of_ars("15001000") == "Sachsen-Anhalt"    # ST
    assert bundesland_of_ars("01001") == "Schleswig-Holstein"


def test_bundesland_of_ars_unknown_or_short_returns_none():
    assert bundesland_of_ars("99123") is None
    assert bundesland_of_ars("0") is None
    assert bundesland_of_ars("") is None


def test_mapping_names_match_committed_csv_byte_for_byte():
    # Guards the umlaut spelling: every mapping value must be a Bundesland that
    # actually appears in the committed margin CSV, else the reference lookup misses.
    df = pd.read_csv(_CSV, comment="#")
    csv_names = set(df["bundesland"])
    assert set(BUNDESLAND_BY_ARS2.values()) == csv_names, (
        "BUNDESLAND_BY_ARS2 values must equal the committed CSV Bundesland names "
        f"(only in mapping: {set(BUNDESLAND_BY_ARS2.values()) - csv_names}; "
        f"only in CSV: {csv_names - set(BUNDESLAND_BY_ARS2.values())})")


def test_source_bundeslaender_distinct_sorted_drops_unmapped():
    got = source_bundeslaender(["03241", "03101", "15001", "99999"])
    assert got == ["Niedersachsen", "Sachsen-Anhalt"]
