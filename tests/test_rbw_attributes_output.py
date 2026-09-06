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

Missing values are a DIFFERENT case from a reported zero: a person without a MiD
plan source (e.g. an in-commuter injected by the cordon merge) has no rbW
information, so the MATSim writer omits both attributes for that person rather than
writing 0, and reports the omission rate once per population.
"""
from __future__ import annotations

import collections
import contextlib
import gzip
import sys
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import shapely.geometry as geo

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


def test_writer_omits_rbw_attributes_for_person_without_plan_source():
    """Cordon-injected in-commuter rows are reindexed onto the resident column set,
    so a resident-only column becomes NaN for them. Such a person has NO rbW
    information -- which is not the same as "zero rbW legs" -- so BOTH attributes are
    omitted for it (never written as 0, which would mask the gap) and the omission is
    counted for the population-level summary."""
    from matsim.scenario import population as pop

    fields = pop.PERSON_FIELDS + ["rbw_legs_count", "rbw_distance_km"]
    counter = collections.Counter()
    writer = _StubWriter()
    pop.add_person(writer,
                   _person_row(fields, rbw_legs_count=float("nan"),
                               rbw_distance_km=float("nan")),
                   [_home_activity(pop)], [], [], person_fields=fields,
                   rbw_omission_counter=counter)
    assert "rbwLegsCount" not in writer.attributes
    assert "rbwDistanceKm" not in writer.attributes
    assert counter["persons_without_rbw_facts"] == 1


# ---------------------------------------------------------------------------
# MATSim writer, end to end: population.xml.gz + the omission summary log
# ---------------------------------------------------------------------------

class _StubProgress:
    def update(self, *args, **kwargs): pass


class _StubContext:
    """Minimal synpp context for write_population (config lookup + progress bar)."""

    def __init__(self, config=None):
        self._config = {"remode_carless_car_legs": False}
        if config is not None:
            self._config.update(config)

    def config(self, key):
        return self._config[key]

    @contextlib.contextmanager
    def progress(self, total=None, label=None):
        yield _StubProgress()


def _write_population_frames(rbw_legs_count, rbw_distance_km):
    """Prepared writer frames for len(rbw_legs_count) persons, one home activity
    each and no trips (write_population requires len(trips) == len(activities) - 1)."""
    from matsim.scenario import population as pop

    n = len(rbw_legs_count)
    person_ids = list(range(1, n + 1))
    persons = pd.DataFrame({field: [0] * n for field in pop.PERSON_FIELDS})
    persons["person_id"] = person_ids
    persons["household_id"] = person_ids
    persons["household_income"] = "2600-3000"
    persons["sex"] = "female"
    persons["employed"] = "yes"
    persons["high_income"] = False
    persons["is_urban_resident"] = False
    persons["has_pt_subscription"] = False
    persons["has_license"] = True
    persons["pt_subscription_type"] = "never_pt"
    persons["household_income_eur"] = 3000.0
    persons["rbw_legs_count"] = rbw_legs_count
    persons["rbw_distance_km"] = rbw_distance_km
    persons = persons[pop.effective_person_fields(persons)]

    activities = pd.DataFrame({
        "person_id": person_ids,
        "start_time": [float("nan")] * n,
        "end_time": [float("nan")] * n,
        "purpose": ["home"] * n,
        "geometry": [geo.Point(0.0, 0.0) for _ in person_ids],
        "location_id": [-1] * n,
    })
    trips = pd.DataFrame({field: [] for field in pop.TRIP_FIELDS})
    vehicles = pd.DataFrame({field: [] for field in pop.VEHICLE_FIELDS})
    return persons, activities, trips, vehicles


def _person_attributes(path):
    """{person_id: {attribute name: text}} parsed from a written population.xml.gz."""
    with gzip.open(path, "rb") as handle:
        root = ElementTree.parse(handle).getroot()
    return {
        person.get("id"): {
            attribute.get("name"): attribute.text
            for attribute in person.findall("./attributes/attribute")
        }
        for person in root.findall("person")
    }


def test_write_population_omits_rbw_attributes_and_logs_the_rate(tmp_path, caplog):
    from matsim.scenario import population as pop

    persons, activities, trips, vehicles = _write_population_frames(
        rbw_legs_count=[2, 0, float("nan")],
        rbw_distance_km=[25.5, 0.0, float("nan")])
    output_path = str(tmp_path / "population.xml.gz")

    with caplog.at_level("INFO", logger=pop.__name__):
        pop.write_population(output_path, persons, activities, trips, vehicles,
                             enable_urban_parking=False, context=_StubContext())

    attributes = _person_attributes(output_path)
    # Persons 1 and 2 have a MiD plan source: both attributes written, typed values.
    assert attributes["1"]["rbwLegsCount"] == "2"
    assert attributes["1"]["rbwDistanceKm"] == "25.5"
    # A donor who reported no rbW leg is a REAL zero and stays written.
    assert attributes["2"]["rbwLegsCount"] == "0"
    assert attributes["2"]["rbwDistanceKm"] == "0.0"
    # Person 3 has no plan source: neither attribute is written (no 0 masking the gap),
    # while the mandatory attributes are unaffected.
    assert "rbwLegsCount" not in attributes["3"]
    assert "rbwDistanceKm" not in attributes["3"]
    assert attributes["3"]["age"] == "0"

    messages = [record.getMessage() for record in caplog.records]
    assert any("rbw attributes omitted for 1/3 persons" in message for message in messages), messages


def test_write_population_without_rbw_columns_logs_nothing(tmp_path, caplog):
    """No rbW columns -> no attributes and no omission summary (the feature is off,
    and a "0/N omitted" line would suggest it ran)."""
    from matsim.scenario import population as pop

    persons, activities, trips, vehicles = _write_population_frames(
        rbw_legs_count=[2], rbw_distance_km=[25.5])
    persons = persons.drop(columns=["rbw_legs_count", "rbw_distance_km"])
    output_path = str(tmp_path / "population.xml.gz")

    with caplog.at_level("INFO", logger=pop.__name__):
        pop.write_population(output_path, persons, activities, trips, vehicles,
                             enable_urban_parking=False, context=_StubContext())

    attributes = _person_attributes(output_path)
    assert "rbwLegsCount" not in attributes["1"]
    assert not any("rbw attributes omitted" in record.getMessage() for record in caplog.records)
