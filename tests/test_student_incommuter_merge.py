"""Test that the student in-commuter stage is merged into the final population
alongside residents and SvB in-commuters (#140 Task 5).

Mirrors the shape of ``tests/test_incommuter_merge.py`` (pure ``_base.py`` unit
tests, no stage stub needed for the generic helper) and adds a context-stub
integration test that exercises the ACTUAL wiring in
``braunschweig.matsim.scenario.households.execute`` -- the simplest of the
three writer overrides (population/households/vehicles) that concatenate
in-commuter frames, since it only depends on one resident stage plus the two
in-commuter stages (no geometry/locations plumbing to stub).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.incommuter_merge._base import (  # noqa: E402
    assert_unique_ids, concat_frame)


# ---------------------------------------------------------------------------
# Unit tests for the new id-collision safety net (mirrors
# tests/test_incommuter_merge.py's direct-call style).
# ---------------------------------------------------------------------------

def test_assert_unique_ids_passes_when_no_collision():
    resident = pd.DataFrame({"person_id": [0, 1]})
    svb = pd.DataFrame({"person_id": [2, 3]})
    student = pd.DataFrame({"person_id": [10_000_002, 10_000_003]})
    assert_unique_ids([resident, svb, student], "person_id", "test")  # no raise


def test_assert_unique_ids_raises_on_collision():
    svb = pd.DataFrame({"household_id": [100, 101]})
    student = pd.DataFrame({"household_id": [101, 102]})  # 101 collides with SvB
    with pytest.raises(ValueError, match="duplicate household_id"):
        assert_unique_ids([svb, student], "household_id", "test")


def test_assert_unique_ids_ignores_empty_and_columnless_frames():
    # A skipped in-commuter stage (e.g. student_incommuters with its parent
    # feature off) returns a totally columnless empty frame -- must not crash.
    resident = pd.DataFrame({"person_id": [0, 1]})
    skipped = pd.DataFrame()
    assert_unique_ids([resident, skipped], "person_id", "test")  # no raise


# ---------------------------------------------------------------------------
# Integration test: the real households.py wiring merges SvB AND student
# in-commuter persons, keeps person_id unique, and would catch a household_id
# collision between the two in-commuter sources.
# ---------------------------------------------------------------------------

class FullCtx:
    """Minimal synpp-style context stub: config dict + fixed stage map +
    output path, mirroring tests/test_student_incommuters_stage.py's FullCtx."""

    def __init__(self, cfg, stages, path="."):
        self._cfg = cfg
        self._stages = stages
        self._path = path

    def config(self, key, default=None):
        return self._cfg.get(key, default)

    def stage(self, name):
        return self._stages[name]

    def path(self):
        return self._path


def _resident_persons():
    return pd.DataFrame({
        "person_id": [0, 1],
        "household_id": [0, 1],
        "census_household_id": [0, 1],
        "household_income": ["1000", "2000"],
        "high_income": [False, False],
        "car_availability": ["all", "none"],
        "bicycle_availability": ["all", "all"],
    })


def _svb_persons():
    return pd.DataFrame({
        "person_id": [100, 101],
        "household_id": [100, 101],
        "census_household_id": [100, 101],
        "household_income": ["3000", "3000"],
        "high_income": [False, False],
        "car_availability": ["all", "none"],
        "bicycle_availability": ["all", "all"],
        "subpopulation": ["svb_incommuter", "svb_incommuter"],
    })


def _student_persons():
    return pd.DataFrame({
        "person_id": [10_000_100],
        "household_id": [10_000_100],
        "census_household_id": [10_000_100],
        "household_income": ["1500"],
        "high_income": [False],
        "car_availability": ["none"],
        "bicycle_availability": ["all"],
        "subpopulation": ["student_incommuter"],
    })


def test_households_execute_merges_svb_and_student_with_unique_ids(monkeypatch):
    import braunschweig.matsim.scenario.households as hh

    captured = {}

    def fake_write_households(output_path, df_persons, context):
        captured["df_persons"] = df_persons
        return "households.xml.gz"

    monkeypatch.setattr(hh.base, "write_households", fake_write_households)

    ctx = FullCtx(
        cfg={"cordon_enabled": True},
        stages={
            "synthesis.population.enriched": _resident_persons(),
            "braunschweig.synthesis.incommuters": {"persons": _svb_persons()},
            "braunschweig.synthesis.student_incommuters": {"persons": _student_persons()},
        },
    )

    hh.execute(ctx)

    merged = captured["df_persons"]
    # "subpopulation" is an in-commuter-only column; concat_frame reindexes the
    # in-commuter frame onto the RESIDENT frame's columns (extra columns
    # dropped), so it does not survive into df_persons -- check the student
    # row via its person_id/household_income instead (matches the household
    # writer's actual output columns, FIELDS in matsim.scenario.households).
    assert set(merged["person_id"]) == {0, 1, 100, 101, 10_000_100}
    assert merged["person_id"].is_unique
    student_row = merged[merged["person_id"] == 10_000_100]
    assert len(student_row) == 1
    assert student_row["household_income"].iloc[0] == "1500"


def test_households_execute_raises_on_household_id_collision(monkeypatch):
    import braunschweig.matsim.scenario.households as hh

    monkeypatch.setattr(hh.base, "write_households",
                        lambda output_path, df_persons, context: "households.xml.gz")

    colliding_student = _student_persons().copy()
    colliding_student["household_id"] = [101]  # collides with the SvB row above

    ctx = FullCtx(
        cfg={"cordon_enabled": True},
        stages={
            "synthesis.population.enriched": _resident_persons(),
            "braunschweig.synthesis.incommuters": {"persons": _svb_persons()},
            "braunschweig.synthesis.student_incommuters": {"persons": colliding_student},
        },
    )

    with pytest.raises(ValueError, match="duplicate household_id"):
        hh.execute(ctx)


def test_households_execute_off_path_unchanged(monkeypatch):
    import braunschweig.matsim.scenario.households as hh

    resident = _resident_persons()
    ctx = FullCtx(
        cfg={"cordon_enabled": False},
        stages={"synthesis.population.enriched": resident},
    )

    written = {}

    def fake_write_households(output_path, df_persons_arg, context):
        written["df_persons"] = df_persons_arg
        return "households.xml.gz"

    monkeypatch.setattr(hh.base, "write_households", fake_write_households)
    # OFF path: neither in-commuter stage is even looked up (would KeyError if
    # they were, since the stub map does not contain them).
    hh.execute(ctx)
    pd.testing.assert_frame_equal(written["df_persons"], resident)
