"""Export of the rbW (regelmaessiger beruflicher Weg) plan-source facts.

The completed-donor persons frame carries the per-person diary facts
``src_n_rbw_legs`` / ``src_rbw_distance_km`` (attached by
``braunschweig.popsim.diary_facts.attach_plan_source_facts``). Those facts explain
WHY a synthetic person's plan looks the way it does (a donor whose reported day
consists only of rbW legs produces a short or empty activity chain), so they must
reach the analysis outputs:

  * ``braunschweig.popsim.enriched_adapter.run`` renames them to the public
    output names ``rbw_legs_count`` / ``rbw_distance_km``;
  * ``synthesis.output.select_person_output_columns`` appends them to persons.csv;
  * ``matsim.scenario.population.add_person`` emits them as the MATSim person
    attributes ``rbwLegsCount`` (java.lang.Integer) / ``rbwDistanceKm``
    (java.lang.Double).

All three steps are ADDITIVE: when the source facts are absent (non-MiD producer,
or a popsim run without the diary-facts attachment) the columns and the MATSim
attributes must not appear at all, so the output stays byte-identical to the
legacy output.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.popsim import enriched_adapter  # noqa: E402
from synthesis.output import select_person_output_columns  # noqa: E402


# ---------------------------------------------------------------------------
# enriched_adapter: src_* facts -> public output columns
# ---------------------------------------------------------------------------

def test_enriched_adapter_maps_rbw_facts_when_present():
    persons = pd.DataFrame({
        "person_id": [0, 1], "household_id": [0, 0],
        "source_person_id": [5, 6], "source_household_id": [3, 3],
        "src_n_rbw_legs": [2, 0], "src_rbw_distance_km": [25.5, 0.0],
    })
    out = enriched_adapter.run(persons)
    assert out["rbw_legs_count"].tolist() == [2, 0]
    assert out["rbw_distance_km"].tolist() == [25.5, 0.0]
    # Dtypes are the ones the MATSim writer promises to Java (Integer / Double).
    assert pd.api.types.is_integer_dtype(out["rbw_legs_count"])
    assert pd.api.types.is_float_dtype(out["rbw_distance_km"])


def test_enriched_adapter_without_facts_has_no_rbw_columns():
    persons = pd.DataFrame({
        "person_id": [0], "household_id": [0],
        "source_person_id": [5], "source_household_id": [3],
    })
    out = enriched_adapter.run(persons)
    assert "rbw_legs_count" not in out.columns
    assert "rbw_distance_km" not in out.columns


def test_enriched_adapter_fills_missing_rbw_facts_with_zero(caplog):
    """Rows without a source fact (e.g. agents injected onto the resident column
    set by the cordon merge) are NaN in the src_* columns. They are filled with
    the structural default 0 / 0.0 -- no rbW leg reported -- and the fill rate is
    logged so the fill can never fire silently (CLAUDE.md fallback transparency)."""
    persons = pd.DataFrame({
        "person_id": [0, 1], "household_id": [0, 0],
        "source_person_id": [5, 6], "source_household_id": [3, 3],
        "src_n_rbw_legs": [2.0, float("nan")],
        "src_rbw_distance_km": [25.5, float("nan")],
    })
    with caplog.at_level("INFO", logger=enriched_adapter.__name__):
        out = enriched_adapter.run(persons)
    assert out["rbw_legs_count"].tolist() == [2, 0]
    assert out["rbw_distance_km"].tolist() == [25.5, 0.0]
    # The fill rate is reported explicitly (1 of 2 persons filled).
    assert any("rbw" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# persons.csv column selection
# ---------------------------------------------------------------------------

def test_output_columns_append_rbw_attributes_last():
    cols = select_person_output_columns(
        ["person_id", "rbw_legs_count", "rbw_distance_km", "employment_status"],
        "is_urban_resident")
    assert cols[-3:] == ["employment_status", "rbw_legs_count", "rbw_distance_km"]


def test_output_columns_without_rbw_attributes_unchanged():
    cols = select_person_output_columns(["person_id"], "is_urban_resident")
    assert "rbw_legs_count" not in cols
    assert "rbw_distance_km" not in cols


# ---------------------------------------------------------------------------
# MATSim writer: additive rbwLegsCount / rbwDistanceKm attributes
# ---------------------------------------------------------------------------

class _StubWriter:
    """Minimal PopulationWriter stand-in capturing the emitted attributes.

    Mirrors the stub used by tests/test_housing_tenure.py for the equivalent
    additive-attribute test."""

    def __init__(self):
        self.attributes = {}
        self.types = {}

    def start_person(self, *args, **kwargs): pass

    def start_attributes(self): pass

    def end_attributes(self): pass

    def end_person(self, *args, **kwargs): pass

    def start_plan(self, *args, **kwargs): pass

    def end_plan(self, *args, **kwargs): pass

    def add_attribute(self, key, java_type, value):
        self.attributes[key] = value
        self.types[key] = java_type

    def yes_no(self, value):
        return "yes" if value else "no"

    def location(self, *args, **kwargs):
        return None

    def add_activity(self, *args, **kwargs): pass

    def add_leg(self, *args, **kwargs): pass


class _Geometry:
    x = 0.0
    y = 0.0


def _home_activity(population_module):
    activity = {field: 0 for field in population_module.ACTIVITY_FIELDS}
    activity["person_id"] = 1
    activity["purpose"] = "home"
    activity["start_time"] = float("nan")
    activity["end_time"] = float("nan")
    activity["location_id"] = -1
    activity["geometry"] = _Geometry()
    return tuple(activity[field] for field in population_module.ACTIVITY_FIELDS)


def _person_row(fields, **overrides):
    row = {field: 0 for field in fields}
    row["person_id"] = 1
    row["household_id"] = 1
    row["household_income"] = "2600-3000"
    row["sex"] = "female"
    row["employed"] = "yes"
    row["high_income"] = False
    row["is_urban_resident"] = False
    row["has_pt_subscription"] = False
    row["has_license"] = True
    row["pt_subscription_type"] = "never_pt"
    row["household_income_eur"] = 3000.0
    row.update(overrides)
    return tuple(row[field] for field in fields)


def test_writer_emits_rbw_attributes_only_when_present():
    from matsim.scenario import population as pop

    df_off = pd.DataFrame({field: [0] for field in pop.PERSON_FIELDS})
    assert pop.effective_person_fields(df_off) == pop.PERSON_FIELDS

    df_on = df_off.copy()
    df_on["rbw_legs_count"] = 2
    df_on["rbw_distance_km"] = 25.5
    fields_on = pop.effective_person_fields(df_on)
    assert fields_on == pop.PERSON_FIELDS + ["rbw_legs_count", "rbw_distance_km"]

    activity = _home_activity(pop)

    writer_on = _StubWriter()
    pop.add_person(writer_on,
                   _person_row(fields_on, rbw_legs_count=2, rbw_distance_km=25.5),
                   [activity], [], [], person_fields=fields_on)
    assert writer_on.attributes["rbwLegsCount"] == 2
    assert writer_on.types["rbwLegsCount"] == "java.lang.Integer"
    assert writer_on.attributes["rbwDistanceKm"] == 25.5
    assert writer_on.types["rbwDistanceKm"] == "java.lang.Double"

    writer_off = _StubWriter()
    pop.add_person(writer_off, _person_row(pop.PERSON_FIELDS), [activity], [], [],
                   person_fields=pop.PERSON_FIELDS)
    assert "rbwLegsCount" not in writer_off.attributes
    assert "rbwDistanceKm" not in writer_off.attributes


def test_writer_rbw_attributes_tolerate_missing_values():
    """Cordon-injected in-commuter rows are reindexed onto the resident column set,
    so a resident-only column becomes NaN for them. The writer must emit the
    structural default (no rbW leg reported) instead of stringifying a float NaN
    into a java.lang.Integer attribute."""
    from matsim.scenario import population as pop

    fields = pop.PERSON_FIELDS + ["rbw_legs_count", "rbw_distance_km"]
    writer = _StubWriter()
    pop.add_person(writer,
                   _person_row(fields, rbw_legs_count=float("nan"),
                               rbw_distance_km=float("nan")),
                   [_home_activity(pop)], [], [], person_fields=fields)
    assert writer.attributes["rbwLegsCount"] == 0
    assert writer.attributes["rbwDistanceKm"] == 0.0
