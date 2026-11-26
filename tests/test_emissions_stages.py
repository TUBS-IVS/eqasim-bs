"""The post-run emissions stage chain (#688) on a synthetic archive; the Java call is faked.

Defects these tests catch: a fleet answered silently by the average table, a run that starts the
(hours-long) Java tool although a lookup must fail, totals that lose the driving after 24:00 or
are scaled twice, a stale result after the archive changed, and a method choice taken by default.
All numbers are synthetic; the expected values are computed by hand in the comments.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from braunschweig.data import hbefa as hbefa_stage
from braunschweig.emissions import events as events_stage
from braunschweig.emissions import inputs as inputs_stage
from braunschweig.emissions import summary as summary_stage
from matsim.runtime import eqasim
from tests.emissions_archive import write_archive
from tests.emissions_tables import write_tables

STAGE_NAMES = {
    hbefa_stage: "braunschweig.data.hbefa",
    inputs_stage: "braunschweig.emissions.inputs",
    events_stage: "braunschweig.emissions.events",
    summary_stage: "braunschweig.emissions.summary",
}
SAMPLE_SIZE = 0.1


class _Recording:
    """Configure-time context: records every declared key with its default (``()`` = none)."""

    def __init__(self):
        self.declared, self.stages = {}, []

    def config(self, key, *default, **kwargs):
        self.declared.setdefault(key, default)
        return default[0] if default else None

    def stage(self, name, *args, **kwargs):
        self.stages.append(name)


class _Pipeline:
    """Runs the stages in order with their declared defaults; ``config(key)`` takes the key alone."""

    def __init__(self, tmp_path, *, with_diesel=False, archive_sample_size=SAMPLE_SIZE, **overrides):
        self.root = tmp_path / "cache"
        self.output_path = tmp_path / "output"
        self.archive = write_archive(self.output_path, with_diesel=with_diesel, sample_size=archive_sample_size)
        tables = write_tables(tmp_path / "tables", hgv_cold=True)
        self.values = {}
        for stage in STAGE_NAMES:
            recording = _Recording()
            stage.configure(recording)
            self.values.update({key: default[0] for key, default in recording.declared.items() if default})
        self.values.update({hbefa_stage.KEY_TABLE_PATHS[kind]: str(path) for kind, path in tables.items()})
        self.values.update({
            "output_path": str(self.output_path),
            "sampling_rate": SAMPLE_SIZE,
            inputs_stage.KEY_COMPUTATION_METHOD: "StopAndGoFraction",
        })
        self.values.update(overrides)
        self.results = {}
        self.current = None

    def config(self, key):
        return self.values[key]

    def stage(self, name):
        return self.results[name]

    def path(self, name=None):
        folder = self.root / (name or self.current)
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def run(self, stage):
        self.current = STAGE_NAMES[stage]
        self.results[self.current] = stage.execute(self)
        return self.results[self.current]

    def report(self, stage, file_name):
        return json.loads((self.root / STAGE_NAMES[stage] / file_name).read_text(encoding="utf-8"))


@pytest.fixture
def fake_tool(monkeypatch):
    """Fake the Java tool: it writes the two files the summary reads, with hand-made grams."""
    seen = {"calls": [], "write": True}

    def fake_run(context, command, arguments):
        seen["calls"].append((command, list(arguments)))
        if not seen["write"]:
            return
        out = Path(context.path())
        (out / "emissions_per_vehicle_type.csv").write_text(
            "vehicleType,pollutant,value\ncar_petrol,CO,3000.0\ndefault_car,CO,1000.0\nheavy_truck,CO,4000.0\n",
            encoding="utf-8")
        (out / "emissions_per_network_mode.csv").write_text(
            "vehicleType,pollutant,value\ncar,CO,4000.0\ntruck,CO,4000.0\n", encoding="utf-8")
        (out / "emissions_total.csv").write_text("Pollutant,kg\nCO,6.0\n", encoding="utf-8")

    monkeypatch.setattr(eqasim, "run", fake_run)
    return seen


def test_the_chain_builds_the_view_runs_the_tool_and_summarises(tmp_path, fake_tool):
    pipeline = _Pipeline(tmp_path)
    pipeline.run(hbefa_stage)

    inputs = pipeline.run(inputs_stage)
    view = pipeline.root / STAGE_NAMES[inputs_stage] / inputs["view_path"]
    with gzip.open(view / "output_network.xml.gz", "rt", encoding="utf-8") as handle:
        network = handle.read()
    assert network.count('name="hbefa_road_type"') == 3 and "URB/Access/30" in network
    assert 'name="emissionsComputationMethod" value="StopAndGoFraction"' in (view / "output_config.xml").read_text()
    report = pipeline.report(inputs_stage, inputs["report_path"])
    # Primary paths: both drivable links have an HBEFA road class, the fleet type hits the detailed table.
    assert report["road_types"]["drivable_links"] == 2 and report["road_types"]["fallback_links"] == 0
    assert report["road_types"]["length_meters_by_road_type"] == {"URB/Access/30": 500.0, "URB/Local/50": 1000.0}
    assert report["lookups"]["primary_types"] == 1 and report["lookups"]["fallback_types"] == []
    assert report["view"]["excluded_types"] == ["Bus", "default_car_passenger"]
    assert report["tables"]["warm_average"]["sha256"] == pipeline.results[STAGE_NAMES[hbefa_stage]]["tables"][
        "warm_average"]["sha256"]

    events = pipeline.run(events_stage)
    (command, arguments), = fake_tool["calls"]
    assert command == "org.matsim.application.analysis.emissions.AirPollutionAnalysis"
    assert arguments == ["--input", str(view), "--config", str(view / "output_config.xml"),
                         "--sample-size", "0.1", "--grid-size", "1000.0", "--output", "emissions_total.csv"]
    assert events["sample_size"] == SAMPLE_SIZE

    summary = pipeline.run(summary_stage)["summary"]
    carbon_monoxide = summary["pollutants"]["CO"]
    # 3000 + 1000 + 4000 g = 8 kg over the whole time; the tool's total says 6 kg before 24:00.
    assert carbon_monoxide["total_kg"] == pytest.approx(8.0)
    assert carbon_monoxide["after_24h_share"] == pytest.approx(0.25)
    # Emitting sample vehicle-km 2.5 + 1.0 + 1.0 = 4.5; scaled once by 1 / 0.1 = 45 km; 8000 g / 45 km.
    assert summary["emitting_vehicle_km_scaled"] == pytest.approx(45.0)
    assert carbon_monoxide["gram_per_vehicle_km"] == pytest.approx(8000.0 / 45.0)
    assert summary["groups"]["primary"]["kilograms"] == {"CO": pytest.approx(3.0)}
    assert summary["groups"]["average_table_by_choice"]["kilograms"] == {"CO": pytest.approx(5.0)}
    assert summary["average_table_by_choice_vehicle_km_share"] == pytest.approx(2.0 / 4.5)
    assert summary["fallback_vehicle_km_share"] == 0.0
    assert summary["vehicle_km_after_24h_share"] == pytest.approx(1.0 / 4.5)
    assert summary["types_driving_without_emissions"] == [] and summary["unknown_vehicle_link_entries"] == 0
    final = pipeline.report(summary_stage, "emissions_summary_report.json")
    assert final["hbefa_tables"].keys() == report["tables"].keys() and final["archive"] == str(pipeline.archive)
    by_pollutant = (pipeline.root / STAGE_NAMES[summary_stage] / "emissions_by_pollutant.csv").read_text()
    assert by_pollutant.splitlines()[0] == "pollutant,total_kg,before_24h_kg,after_24h_share,gram_per_vehicle_km"


def test_a_fleet_type_below_the_detailed_table_stops_the_chain_before_the_tool(tmp_path, fake_tool):
    pipeline = _Pipeline(tmp_path, with_diesel=True)
    pipeline.run(hbefa_stage)

    with pytest.raises(ValueError, match=r"car_diesel.*do not match the table's keys"):
        pipeline.run(inputs_stage)

    # The refused run leaves its counts on disk: 1 of 2 fleet types on the fallback.
    lookups = pipeline.report(inputs_stage, inputs_stage.REPORT_FILE_NAME)["lookups"]
    assert lookups["fallback_types"] == ["car_diesel"] and lookups["fallback_type_share"] == 0.5
    assert fake_tool["calls"] == []
    # Accepted on purpose, the chain runs and the summary carries the fallback share by vehicle-km:
    # the diesel car does not drive, so 0 of 2.5 fleet vehicle-km.
    accepted = _Pipeline(tmp_path / "accepted", with_diesel=True,
                         **{inputs_stage.KEY_MAX_TYPE_FALLBACK: 0.5})
    for stage in STAGE_NAMES:
        result = accepted.run(stage)
    assert result["summary"]["fallback_vehicle_km_share"] == 0.0
    assert result["summary"]["fleet_vehicle_km_sample"] == pytest.approx(2.5)


def test_the_inputs_stage_refuses_a_missing_archive_and_an_unnamed_method(tmp_path):
    recording = _Recording()
    inputs_stage.configure(recording)
    # No default while the method choice is open: a run has to name the method it used.
    assert recording.declared[inputs_stage.KEY_COMPUTATION_METHOD] == ()
    assert recording.declared["output_path"] == () and "braunschweig.data.hbefa" in recording.stages

    pipeline = _Pipeline(tmp_path, **{inputs_stage.KEY_COMPUTATION_METHOD: None})
    pipeline.run(hbefa_stage)
    with pytest.raises(ValueError, match="emissions_computation_method=None"):
        pipeline.run(inputs_stage)

    pipeline.values[inputs_stage.KEY_COMPUTATION_METHOD] = "AverageSpeed"
    pipeline.values["output_path"] = str(tmp_path / "nowhere")
    with pytest.raises(RuntimeError, match="no MATSim output archive"):
        pipeline.run(inputs_stage)


def test_the_inputs_token_follows_the_archive_and_the_committed_tables(tmp_path):
    pipeline = _Pipeline(tmp_path)

    token = inputs_stage.validate(pipeline)

    assert token == inputs_stage.validate(pipeline)
    assert set(token["files"]) == {
        "vehicle_type_rules.csv", "technology_names.csv", "osm_hbefa_road_classes.csv"}
    assert token["archive"]["events_size_bytes"] == (pipeline.archive / "output_events.xml.gz").stat().st_size
    # A new simulation under the same output path: the archive info changes, so does the token.
    (pipeline.archive / "ARCHIVE_INFO.json").write_text('{"created": "later"}', encoding="utf-8")
    assert inputs_stage.validate(pipeline)["archive"] != token["archive"]
    pipeline.values["output_path"] = str(tmp_path / "nowhere")
    assert inputs_stage.validate(pipeline)["archive"] is None


def test_the_events_stage_refuses_a_silent_tool_and_a_wrong_sample_size(tmp_path, fake_tool):
    pipeline = _Pipeline(tmp_path)
    pipeline.run(hbefa_stage)
    pipeline.run(inputs_stage)

    fake_tool["write"] = False
    with pytest.raises(RuntimeError, match="without writing emissions_total.csv"):
        pipeline.run(events_stage)
    pipeline.values["sampling_rate"] = 10
    with pytest.raises(ValueError, match="sampling_rate=10"):
        pipeline.run(events_stage)
    # The archive was simulated at 0.1 (its eqasim module; the simwrapper module says 1.0): a config
    # that names another rate would scale every output by the wrong factor.
    assert len(fake_tool["calls"]) == 1
    pipeline.values["sampling_rate"] = 0.25
    with pytest.raises(ValueError, match=r"sampling_rate=0\.25.*simulated with 0\.1"):
        pipeline.run(events_stage)
    assert len(fake_tool["calls"]) == 1


def test_an_archive_that_does_not_name_its_sample_size_stops_before_the_tool(tmp_path, fake_tool):
    pipeline = _Pipeline(tmp_path, archive_sample_size=None)
    pipeline.run(hbefa_stage)
    assert pipeline.run(inputs_stage)["archive_sample_size"] is None

    with pytest.raises(ValueError, match="does not name the sample size"):
        pipeline.run(events_stage)
    assert fake_tool["calls"] == []


def test_the_tool_gets_absolute_paths_for_a_relative_working_directory(tmp_path, fake_tool, monkeypatch):
    # The Java call runs with the stage directory as working directory: a relative view path
    # would be looked up below it.
    monkeypatch.chdir(tmp_path)
    pipeline = _Pipeline(tmp_path)
    pipeline.root = Path("cache")
    pipeline.run(hbefa_stage)
    pipeline.run(inputs_stage)

    pipeline.run(events_stage)

    (_, arguments), = fake_tool["calls"]
    view = tmp_path / "cache" / STAGE_NAMES[inputs_stage] / "view"
    assert arguments[:4] == ["--input", str(view), "--config", str(view / "output_config.xml")]


@pytest.mark.parametrize("key, value", [
    (inputs_stage.KEY_HANDLE_HIGH_SPEEDS, "false"),
    (inputs_stage.KEY_COORDINATE_SYSTEM, ""),
    (inputs_stage.KEY_FALLBACK_ROAD_TYPE, None),
])
def test_a_setting_of_the_wrong_type_is_refused_by_its_key(tmp_path, key, value):
    pipeline = _Pipeline(tmp_path, **{key: value})

    with pytest.raises(ValueError, match=key):
        inputs_stage.read_settings(pipeline)
