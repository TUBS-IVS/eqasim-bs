"""Coverage logging for silent left-merge + fillna(0) joins (key-matching audit).

Three joins attach volumes/attributes via ``merge(how="left")`` followed by
``fillna(0)``. The keys are correct today, but if a key format ever drifts the
volumes silently become 0 (or rows are dropped on the other side) and the
pipeline keeps running -- the exact hidden-mismatch class the
"Fallback transparency" rule (CLAUDE.md) forbids. These tests pin that every
such join reports its match coverage and WARNs loudly on unmatched keys.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.data.cordon import gate_assignment as ga
from braunschweig.popsim import assembly


def _kreise(ars5_list):
    return gpd.GeoDataFrame(
        {"ars5": ars5_list},
        geometry=[Point(i * 1000.0, 0.0) for i in range(len(ars5_list))],
        crs="EPSG:25832",
    )


def _gates(n=1):
    return gpd.GeoDataFrame(
        {"gate_id": [f"g{i}" for i in range(n)],
         "capacity": [1000.0] * n},
        geometry=[Point(i * 500.0, 100.0) for i in range(n)],
        crs="EPSG:25832",
    )


# ---------------------------------------------------------------------------
# assign_kreise_to_gates_with_volume
# ---------------------------------------------------------------------------

def test_assign_kreise_warns_on_volume_without_kreis_geometry(capsys):
    # "99999" carries volume but has no Kreis geometry row -> that volume is
    # silently DROPPED by the left merge; must be surfaced as a WARNING.
    kreise = _kreise(["03241"])
    volume = pd.DataFrame({"ars5": ["03241", "99999"],
                           "inbound": [100, 55], "outbound": [10, 5]})
    out = ga.assign_kreise_to_gates_with_volume(kreise, _gates(), volume)
    captured = capsys.readouterr().out
    assert "WARNING" in captured
    assert "99999" in captured
    assert len(out) == 1  # behaviour unchanged: only the matched Kreis remains


def test_assign_kreise_logs_kreis_without_volume(capsys):
    # A Kreis geometry without any volume row is zero-filled -- fine, but the
    # count must be visible.
    kreise = _kreise(["03241", "03252"])
    volume = pd.DataFrame({"ars5": ["03241"], "inbound": [100], "outbound": [10]})
    out = ga.assign_kreise_to_gates_with_volume(kreise, _gates(), volume)
    captured = capsys.readouterr().out
    assert "03252" in captured
    assert out.loc[out["ars5"] == "03252", "inbound"].iloc[0] == 0


def test_assign_kreise_full_match_no_warning(capsys):
    kreise = _kreise(["03241"])
    volume = pd.DataFrame({"ars5": ["03241"], "inbound": [100], "outbound": [10]})
    ga.assign_kreise_to_gates_with_volume(kreise, _gates(), volume)
    assert "WARNING" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# population_gravity_gate_assignment
# ---------------------------------------------------------------------------

def _gemeinden(ars5_list):
    return gpd.GeoDataFrame(
        {"ars5": ars5_list, "ewz": [1000.0] * len(ars5_list)},
        geometry=[Point(i * 1000.0, 0.0) for i in range(len(ars5_list))],
        crs="EPSG:25832",
    )


def test_population_gravity_warns_on_dropped_kreis_volume(capsys):
    # kreis_volume row "99999" has no Gemeinde in the gemeinden frame -> its
    # whole commuter volume silently vanishes; must WARN.
    gem = _gemeinden(["03241"])
    vol = pd.DataFrame({"ars5": ["03241", "99999"],
                        "inbound": [100, 44], "outbound": [10, 4]})
    out = ga.population_gravity_gate_assignment(gem, _gates(2), vol)
    captured = capsys.readouterr().out
    assert "WARNING" in captured
    assert "99999" in captured
    assert set(out["ars5"]) == {"03241"}


def test_population_gravity_full_match_no_warning(capsys):
    gem = _gemeinden(["03241"])
    vol = pd.DataFrame({"ars5": ["03241"], "inbound": [100], "outbound": [10]})
    ga.population_gravity_gate_assignment(gem, _gates(2), vol)
    assert "WARNING" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# popsim.assembly donor-household attribute attach
# ---------------------------------------------------------------------------

def test_attach_donor_household_attrs_warns_on_unmatched_donor(capsys):
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "donor_hh_id": [10, 10, 99],  # 99 has no donor household row
    })
    donor_hh = pd.DataFrame({
        "donor_hh_id": [10],
        "number_of_cars": [2],
        "number_of_bicycles": [1],
        "has_ebike": [1],
    })
    out = assembly._attach_donor_household_attrs(
        persons, donor_hh, "donor_hh_id",
        ["number_of_cars", "number_of_bicycles", "has_ebike"],
    )
    captured = capsys.readouterr().out
    assert "WARNING" in captured
    assert "1/3" in captured or "1 " in captured
    # Behaviour unchanged: unmatched person zero-filled on the count attrs.
    assert out.loc[out["person_id"] == 3, "number_of_cars"].iloc[0] == 0
    assert out.loc[out["person_id"] == 1, "number_of_cars"].iloc[0] == 2


def test_attach_donor_household_attrs_full_match_no_warning(capsys):
    persons = pd.DataFrame({"person_id": [1], "donor_hh_id": [10]})
    donor_hh = pd.DataFrame({
        "donor_hh_id": [10],
        "number_of_cars": [1],
        "number_of_bicycles": [0],
        "has_ebike": [0],
    })
    out = assembly._attach_donor_household_attrs(
        persons, donor_hh, "donor_hh_id",
        ["number_of_cars", "number_of_bicycles", "has_ebike"],
    )
    assert "WARNING" not in capsys.readouterr().out
    assert out["number_of_cars"].iloc[0] == 1
