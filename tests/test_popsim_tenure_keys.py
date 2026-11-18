"""Config keys and stage wiring of the owner-household limit (issue #657, Task T3).

The rule itself (``braunschweig.popsim.tenure_targets``) is tested in
``tests/test_popsim_tenure_targets.py``; these tests prove that the two config keys put it
behind a switch in the ``data.census.filtered`` control build, that the switch OFF leaves the
control tables of today untouched, and that the stage reports what the rule did. All expected
values are computed by hand from the synthetic cell frame of
``tests/popsim_control_build_fixture.py`` (households / owners / renters / rooms-table
dwellings per cell):

* cell 2: 30 / 30 / 0 / 2 -> 28 households outside any dwelling, owners 30 -> 2, renters 0 -> 28
* cell 3: 118 / 118 / 0 / 0 -> 118 households outside, owners 118 -> 0, renters 0 -> 118
* every other cell has fewer than 10 households outside its dwellings (cell 6: 3 / 3 / 0 / 0
  has only 3), so it stays as it is.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as fixture  # noqa: E402

from braunschweig.popsim import stage  # noqa: E402

OWNER_COLUMN = fixture.OWNER_COLUMN
RENTER_COLUMN = fixture.RENTER_COLUMN
TENURE_COLUMNS = {
    f"{base}_{geography}"
    for base in (OWNER_COLUMN, RENTER_COLUMN)
    for geography in ("ZENSUS100m", "ZENSUS1km")
}


def _tenure_warnings(caplog):
    """WARNING records or above that mention the tenure rule (logger name or message)."""
    return [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
        and ("tenure" in record.name or "tenure" in record.getMessage().lower())
    ]


def test_key_off_reproduces_the_control_tables(caplog):
    """The switch OFF: golden tables, no rule counters, no warning about the rule."""
    caplog.set_level(logging.DEBUG)

    built, report = fixture.build_control_tables_and_report(
        tenure_owner_limited_to_dwellings=False)

    for name in fixture.GOLDEN_NAMES:
        pd.testing.assert_frame_equal(
            built[name], fixture.read_golden(name), check_exact=True,
            obj=f"control_totals_{name}")
    assert report is None
    assert _tenure_warnings(caplog) == []
    assert not any("popsim.tenure_targets" in r.getMessage() for r in caplog.records)


def test_key_on_changes_only_the_tenure_columns():
    """The switch ON moves the owner and renter targets of the two over-count cells, nothing else.

    Hand computation (largest-remainder rounding within the 1 km parent, parents are cells
    0-2, 3-4 and 5-7): owner 100 m = 11.3, 12.1, 2, 0, 20.9, 8.4, 3, 9.8; renter 100 m =
    13.3, 5.2, 28, 118, 20.9, 25, 0, 2.8.
    """
    built, report = fixture.build_control_tables_and_report(
        tenure_owner_limited_to_dwellings=True, tenure_min_households_outside=10.0)
    assert report is not None

    # Owners: parent 0 sums to 25.4 -> 25 = floors (11, 12, 2); parent 1 sums to 20.9 -> 21,
    # the one leftover unit goes to cell 4 (remainder .9); parent 2 is unchanged (21).
    expected_owner = [11, 12, 2, 0, 21, 8, 3, 10]
    # Renters: parent 0 sums to 46.5 (floors 13, 5, 28 = 46; the .5 total rounds half to even
    # to 46); parent 1 sums to 138.9 -> 139, leftover unit to cell 4 (118, 21); parent 2 unchanged.
    expected_renter = [13, 5, 28, 118, 21, 25, 0, 3]

    for geography, key in (("ZENSUS100m", "ZENSUS100m"), ("ZENSUS1km", "ZENSUS1km")):
        golden = fixture.read_golden(geography)
        table = built[geography]
        assert list(table.columns) == list(golden.columns)
        changed = {c for c in table.columns if c != key and not table[c].equals(golden[c])}
        assert changed == {c for c in TENURE_COLUMNS if c.endswith(geography)}
        pd.testing.assert_frame_equal(
            table.drop(columns=list(changed)), golden.drop(columns=list(changed)),
            check_exact=True)

    table_100m = built["ZENSUS100m"]
    assert table_100m[f"{OWNER_COLUMN}_ZENSUS100m"].tolist() == expected_owner
    assert table_100m[f"{RENTER_COLUMN}_ZENSUS100m"].tolist() == expected_renter
    # The 1 km targets are the sums of their 100 m children (parents 0, 1, 2).
    table_1km = built["ZENSUS1km"]
    assert table_1km[f"{OWNER_COLUMN}_ZENSUS1km"].tolist() == [25, 21, 21]
    assert table_1km[f"{RENTER_COLUMN}_ZENSUS1km"].tolist() == [46, 139, 28]
    # STAAT and WELT tables carry no tenure column: unchanged (covered by the loop above for the
    # grid tables, here by equality).
    for name in ("STAAT", "WELT"):
        pd.testing.assert_frame_equal(
            built[name], fixture.read_golden(name), check_exact=True)


def test_report_lists_cells_and_households(tmp_path):
    """The stage report file holds the rule's counts (and the OFF path states 'off', no counts)."""
    cells = fixture.make_cells()
    _, report = stage._limit_owner_targets_to_dwellings(
        cells, fixture.CONTROL_SOURCE, fixture.SEED_NAME, fixture.CONTROL_TIERS,
        enabled=True, min_households_outside=10.0)

    # Cells 2 and 3 change: 30 - 2 = 28 and 118 - 0 = 118 owner households become renters.
    assert report["cells_seen"] == 8
    assert report["cells_changed"] == 2
    assert report["households_moved_owner_to_renter"] == pytest.approx(146.0)
    assert report["cells_with_nan_dwelling_count"] == 0
    assert report["cells_with_owner_above_household_total"] == 0
    assert report["min_households_outside"] == 10.0

    class _Context:
        def __init__(self):
            self.info = {}

        def path(self):
            return str(tmp_path)

        def set_info(self, key, value):
            self.info[key] = value

    context = _Context()
    stage._write_tenure_limit_report(
        context, enabled=True, min_households_outside=10.0, report=report)
    on = json.loads((tmp_path / stage.CENSUS_FILTERED_REPORT_FILE).read_text(encoding="utf-8"))
    assert on == {"tenure_owner_limited_to_dwellings": {
        "enabled": True, "min_households_outside": 10.0, "report": report}}
    assert context.info["popsim_tenure_owner_limit"] == on["tenure_owner_limited_to_dwellings"]

    stage._write_tenure_limit_report(
        context, enabled=False, min_households_outside=10.0, report=None)
    off = json.loads((tmp_path / stage.CENSUS_FILTERED_REPORT_FILE).read_text(encoding="utf-8"))
    assert off == {"tenure_owner_limited_to_dwellings": {
        "enabled": False, "min_households_outside": 10.0, "report": None}}


def test_key_on_without_a_tenure_control_changes_nothing(caplog):
    """On, but the tiers carry no tenure control: one INFO line, cells and report untouched."""
    cells = fixture.make_cells()
    caplog.set_level(logging.INFO)

    result, report = stage._limit_owner_targets_to_dwellings(
        cells, fixture.CONTROL_SOURCE, fixture.SEED_NAME, ("tier0", "tier1"),
        enabled=True, min_households_outside=10.0)

    pd.testing.assert_frame_equal(result, cells, check_exact=True)
    assert report is None
    assert len([r for r in caplog.records if "no tenure control" in r.getMessage()]) == 1
    assert _tenure_warnings(caplog) == []


def test_owner_quote_dwelling_column_is_loaded_only_when_the_rule_can_run():
    """The dwelling column joins the parquet load set only for key on and an active tenure control."""
    from braunschweig.popsim import control_spec

    class _Context:
        def config(self, key):
            return False

    def load_columns(tiers, enabled):
        return stage._resolve_cell_load_columns(
            _Context(), fixture.CONTROL_SOURCE, fixture.SEED_NAME, tiers, [],
            employment_grid_on=False, cells_path=None, active_entries=[],
            tenure_owner_limited_to_dwellings=enabled)

    column = control_spec.OWNER_QUOTE_DWELLING_COLUMN
    off = load_columns(fixture.CONTROL_TIERS, False)
    on = load_columns(fixture.CONTROL_TIERS, True)
    # The OFF path loads exactly the columns of today; ON adds the one dwelling column.
    assert column not in off
    assert set(on) - set(off) == {column} and len(on) == len(off) + 1
    assert set(off) <= set(on)
    assert load_columns(("tier0", "tier1"), True) == load_columns(("tier0", "tier1"), False)


def test_configure_declares_both_keys_and_rejects_a_non_positive_threshold():
    declared = {}

    class _Recorder:
        def __init__(self, threshold=None):
            self.threshold = threshold

        def config(self, key, default=None, **kwargs):
            declared.setdefault(key, default)
            if key == stage.KEY_TENURE_MIN_HOUSEHOLDS_OUTSIDE_DWELLINGS \
                    and self.threshold is not None:
                return self.threshold
            return default

        def stage(self, *args, **kwargs):
            return None

    stage.configure(_Recorder())
    assert declared[stage.KEY_TENURE_OWNER_LIMITED_TO_DWELLINGS] is False
    assert declared[stage.KEY_TENURE_MIN_HOUSEHOLDS_OUTSIDE_DWELLINGS] == 10.0
    for bad in (0, -1.0):
        with pytest.raises(ValueError, match="tenure_min_households_outside_dwellings"):
            stage.configure(_Recorder(bad))
