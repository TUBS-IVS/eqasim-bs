"""Parking inputs of the prepared scenario (issue #249; design spec sections 3.6, 5.1, 5.4 and 5.5).

With ``parking_zones_enabled`` the preparation stage exports the tariff table of
``braunschweig.parking.zones_stage`` as the tariff model JSON next to ``<prefix>config.xml``, writes the
``braunschweigParking`` module that names this JSON by its bare file name, and lists both files in the parking
inputs report; ``matsim.output`` copies the listed files with the scenario. OFF declares no parking key but the
flag itself and no parking stage, leaves the prepared config byte-identical and writes no parking file.

The zones stage is faked: its tariff table is ``tests/fixtures/parking/parking_tariffs_fixture.csv`` read by the
production loader ``braunschweig.parking.zones.load_tariffs`` (the frame the real stage returns), which pins
arithmetic, not truth. The context double follows ``tests/test_vrb_zone_fares_prepare_wiring.py`` but is strict
in the execute phase, like synpp's ExecuteContext, so an execute-time read that configure() did not declare fails
here instead of in a real run (``tests/test_execute_context_config_contract.py``).
"""
from __future__ import annotations

import datetime
import hashlib
import inspect
import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from braunschweig.matsim import config_modules
from braunschweig.matsim.simulation import prepare
from braunschweig.parking import zones as parking_zones
from matsim import output

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = REPO / "tests" / "fixtures" / "parking"
#: The fixture tariff table (spec 5.3 columns), the input of the faked zones stage.
FIXTURE_TARIFFS_PATH = FIXTURE_DIRECTORY / "parking_tariffs_fixture.csv"
#: The spec 5.4 model of the same table, built by the production export (scripts/export_parking_golden_cases.py).
FIXTURE_MODEL_PATH = FIXTURE_DIRECTORY / "parking_tariffs_fixture.json"

PARKING_STAGE = "braunschweig.parking.zones_stage"
PREFIX = "bs_"
CONFIG_NAME = f"{PREFIX}config.xml"
TARIFFS_NAME = f"{PREFIX}parking_tariffs_2026-09-29.json"
REPORT_NAME = f"{PREFIX}parking_inputs_report.json"
#: The scenario files other than the config that matsim.output copies from the prepared stage.
SCENARIO_FILES = tuple(f"{PREFIX}{name}" for name in (
    "households.xml.gz", "population.xml.gz", "vehicles.xml.gz", "facilities.xml.gz", "network.xml.gz",
    "transit_schedule.xml.gz", "transit_vehicles.xml.gz"))

#: A prepared MATSim config as the Java preparation writes it: XML declaration, DOCTYPE and flat modules.
PREPARED_CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
                   '<config>\n'
                   '\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n\t</module>\n'
                   '\t<module name="plans">\n\t\t<param name="inputPlansFile" value="bs_population.xml.gz" />\n'
                   '\t</module>\n'
                   '</config>\n')


class _Context:
    """synpp context double: records what configure() declares, then serves only that to execute().

    Before ``executing`` is set it behaves like synpp's ConfigurationContext (an option resolves to the given
    value, else to its declared default); afterwards like its ExecuteContext: ``config`` takes the option alone
    and only for a declared option, and ``stage`` serves only declared stages.
    """

    def __init__(self, stage_path, values, paths=None):
        self.values = dict(values)
        self.declared_config = {}
        self.declared_stages = []
        self.stages = {}
        self.paths = dict(paths or {})
        self.executing = False
        self._path = Path(stage_path)
        self._path.mkdir(parents=True, exist_ok=True)

    def config(self, key, *default, **options):  # synpp's configure-time config also takes volatile=...
        if self.executing:
            if default or options:
                raise TypeError(f"ExecuteContext.config() takes the option alone; got more for {key!r}")
            if key not in self.declared_config:
                raise KeyError(f"Config option {key} is not requested")
            return self.declared_config[key]
        if key in self.values:
            self.declared_config[key] = self.values[key]
        elif default:
            self.declared_config[key] = default[0]
        else:
            self.declared_config.setdefault(key, None)
        return self.declared_config[key]

    def stage(self, name, **options):
        if not self.executing:
            self.declared_stages.append(name)
            return None
        if name not in self.declared_stages:
            raise KeyError(f"Stage {name} is not requested")
        return self.stages.get(name)

    def path(self, name=None):
        return str(self._path if name is None else self.paths[name])


def _fixture_tariffs() -> pd.DataFrame:
    """The fixture tariff table as the zones stage returns it: read by the production loader (typed, the
    identifier columns as text, e.g. 03102), so the export sees exactly the frame of a real release."""
    return parking_zones.load_tariffs(FIXTURE_TARIFFS_PATH)


def _zones_stage_result() -> dict:
    """The shape braunschweig.parking.zones_stage returns; the preparation reads only tariffs and sources."""
    tariffs = _fixture_tariffs()
    zones = gpd.GeoDataFrame({"zone_id": tariffs["zone_id"],
                              "geometry": [box(index, 0, index + 1, 1) for index in range(len(tariffs))]},
                             crs="EPSG:25832")
    sources = [{"source_id": "parking_tariffs_2026", "path": "braunschweig/parking/parking_tariffs_2026.csv",
                "sha256": hashlib.sha256(b"fixture tariff table").hexdigest()},
               {"source_id": "parking_zones_2026", "path": "braunschweig/parking/parking_zones_2026.geojson",
                "sha256": hashlib.sha256(b"fixture zone polygons").hexdigest()}]
    return {"zones": zones, "tariffs": tariffs, "workplace_shares": pd.DataFrame(),
            "coverage_register": pd.DataFrame(), "sources": sources}


def _prepare_context(tmp_path, monkeypatch, **values):
    """A configured preparation context over a prepared config; the Java calls and the delegate are stubbed."""
    context = _Context(tmp_path / "prepare", {"output_prefix": PREFIX, "vrb_zone_fares_enabled": False,
                                              "cordon_enabled": False, "freight_enabled": False, **values})
    prepare.configure(context)
    context.executing = True
    context.stages["braunschweig.data.vrb.zones"] = gpd.GeoDataFrame(
        {"zone": ["1"], "geometry": [box(0, 0, 1, 1)]}, crs="EPSG:25832")
    context.stages[PARKING_STAGE] = _zones_stage_result()
    config = Path(context.path()) / CONFIG_NAME
    config.write_bytes(PREPARED_CONFIG.encode("utf-8"))
    monkeypatch.setattr(prepare.eqasim, "run", lambda ctx, command, arguments: None)
    monkeypatch.setattr(prepare.delegate, "execute", lambda ctx: CONFIG_NAME)
    return context, config


@pytest.mark.parametrize("values", [{}, {"parking_zones_enabled": False}], ids=["absent", "false"])
def test_off_declares_only_the_flag_and_leaves_the_prepared_config_byte_identical(
        tmp_path, monkeypatch, values):
    context, config = _prepare_context(tmp_path, monkeypatch, **values)
    assert context.declared_config[prepare.PARKING_KEY] is False
    # The flag is the ONLY parking key OFF declares -- not merely none of PARKING_DEFAULTS -- and no parking
    # stage is declared, so a key added later cannot leak into OFF configurations unnoticed.
    assert [key for key in context.declared_config if "parking" in key] == [prepare.PARKING_KEY]
    assert not [stage for stage in context.declared_stages if "parking" in stage]
    before = config.read_bytes()
    assert prepare.execute(context) == CONFIG_NAME
    assert config.read_bytes() == before
    assert not [path.name for path in config.parent.iterdir() if "parking" in path.name]


def test_on_declares_the_zones_stage_and_the_parking_parameters(tmp_path, monkeypatch):
    context, _ = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    assert PARKING_STAGE in context.declared_stages
    assert {key: context.declared_config[key] for key in prepare.PARKING_DEFAULTS} == {
        "parking_tariff_snapshot_date": "2026-09-29", "parking_terminal_stay_rule": "until_fee_end",
        "parking_minimum_stay_min": 15}


@pytest.mark.parametrize("values, message", [
    ({"parking_terminal_stay_rule": "flat_8h"}, "parking_terminal_stay_rule"),
    # An unquoted YAML date arrives as datetime.date, not as the text the file name and the model carry.
    ({"parking_tariff_snapshot_date": datetime.date(2026, 9, 28)}, "parking_tariff_snapshot_date"),
    # The Java ParkingConfigGroup reads minimumStayMinutes as plain digits whose seconds fit a Java int; YAML `true`
    # arrives as a bool, which is an int in Python, and must not become a minimum of 1 min.
    ({"parking_minimum_stay_min": -1}, "parking_minimum_stay_min"),
    ({"parking_minimum_stay_min": 7.5}, "parking_minimum_stay_min"),
    ({"parking_minimum_stay_min": "15"}, "parking_minimum_stay_min"),
    ({"parking_minimum_stay_min": True}, "parking_minimum_stay_min"),
    ({"parking_minimum_stay_min": 35_791_395}, "parking_minimum_stay_min"),
], ids=["unsupported_terminal_stay_rule", "unquoted_yaml_date", "negative_minimum_stay", "fractional_minimum_stay",
        "text_minimum_stay", "boolean_minimum_stay", "minimum_stay_beyond_the_java_int_seconds"])
def test_on_configure_rejects_a_parking_parameter_the_export_cannot_use(tmp_path, values, message):
    # Checked at configure time: the export itself runs only at the end of the preparation, hours later.
    context = _Context(tmp_path / "prepare", {"output_prefix": PREFIX, "cordon_enabled": False,
                                              "freight_enabled": False, "parking_zones_enabled": True, **values})
    with pytest.raises(ValueError, match=message):
        prepare.configure(context)


@pytest.mark.parametrize("snapshot_date, fix, wrong_fix", [
    (datetime.date(2026, 9, 28), "quote", None),
    ("2026-9-28", "YYYY-MM-DD", "quote"),
    ("28.09.2026", "YYYY-MM-DD", "quote"),
], ids=["unquoted_yaml_date", "quoted_but_unpadded", "quoted_but_german"])
def test_on_configure_names_the_fix_that_matches_the_bad_snapshot_date(tmp_path, snapshot_date, fix, wrong_fix):
    # An unquoted YAML date needs quotes; a quoted text in the wrong format already is quoted and needs the ISO
    # format instead, so telling its author to quote it would send them the wrong way.
    context = _Context(tmp_path / "prepare", {"output_prefix": PREFIX, "cordon_enabled": False,
                                              "freight_enabled": False, "parking_zones_enabled": True,
                                              "parking_tariff_snapshot_date": snapshot_date})
    with pytest.raises(ValueError, match="parking_tariff_snapshot_date") as error:
        prepare.configure(context)
    assert fix in str(error.value) and "'2026-09-29'" in str(error.value)
    if wrong_fix is not None:
        assert wrong_fix not in str(error.value)


def test_the_configure_check_uses_the_public_snapshot_date_validator(tmp_path, monkeypatch):
    # One validator for the configure-time check and the export: tariff_export.check_snapshot_date.
    calls = []
    real_check = prepare.tariff_export.check_snapshot_date
    monkeypatch.setattr(prepare.tariff_export, "check_snapshot_date",
                        lambda value: calls.append(value) or real_check(value))
    _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    assert calls == ["2026-09-29"]


def test_on_execute_exports_the_fixture_zones_as_the_tariff_model(tmp_path, monkeypatch):
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(context)
    model = json.loads((config.parent / TARIFFS_NAME).read_text(encoding="utf-8"))
    assert sorted(model["zones"]) == ["fx_bs_ia", "fx_bs_ia_v2", "fx_bs_ib", "fx_bs_ib_v2", "fx_campus",
                                      "fx_campus_tie_v2", "fx_campus_v2", "fx_capped_street_v2", "fx_frac",
                                      "fx_garage_window_v2", "fx_pe", "fx_res_a", "fx_res_garage_v2", "fx_sz", "fx_wob",
                                      "fx_wob_v2"]
    # Same fixture table, same production export: the cents and seconds of the committed fixture model.
    assert model["zones"] == json.loads(FIXTURE_MODEL_PATH.read_text(encoding="utf-8"))["zones"]
    assert model["sources"] == context.stages[PARKING_STAGE]["sources"]
    assert (model["schema_version"], model["tariff_snapshot_date"], model["terminal_stay_rule"]) == (
        2, "2026-09-29", "until_fee_end")


def test_on_execute_writes_the_parking_module_and_keeps_every_other_byte_of_the_config(tmp_path, monkeypatch):
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(context)
    assert config_modules.read_module(config, prepare.PARKING_MODULE) == {
        "enabled": "true", "tariffsPath": TARIFFS_NAME, "terminalStayRule": "until_fee_end",
        "minimumStayMinutes": "15"}
    text = config.read_bytes().decode("utf-8")
    assert text.startswith(PREPARED_CONFIG[:PREPARED_CONFIG.rindex("</config>")])
    assert text.endswith("\t</module>\n</config>\n")


@pytest.mark.parametrize("minimum_stay_min, text", [(0, "0"), (30, "30"), (35_791_394, "35791394")],
                         ids=["rule_off", "sensitivity_arm_30_min", "largest_value_java_accepts"])
def test_on_execute_writes_the_configured_minimum_stay_as_plain_digits(tmp_path, monkeypatch, minimum_stay_min, text):
    # 0 and 30 min are the sensitivity arms of rule L1 (ADR-0139); 35,791,394 min is the largest value whose seconds
    # fit a Java int. The Java side reads plain digits only, so the value travels without a sign or decimals.
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True,
                                       parking_minimum_stay_min=minimum_stay_min)
    prepare.execute(context)
    assert config_modules.read_module(config, prepare.PARKING_MODULE)["minimumStayMinutes"] == text
    report = json.loads((config.parent / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["minimum_stay_min"] == minimum_stay_min


def test_module_paths_are_relative_and_listed_in_report(tmp_path, monkeypatch):
    # Review focus 5 of the plan: a relocated scenario resolves tariffsPath next to its config, so the module
    # must name the file without a directory, and the report that matsim.output copies from must list it.
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(context)
    tariffs_path = config_modules.read_module(config, prepare.PARKING_MODULE)["tariffsPath"]
    assert tariffs_path == TARIFFS_NAME == Path(tariffs_path).name
    assert "/" not in tariffs_path and "\\" not in tariffs_path
    assert (config.parent / tariffs_path).is_file()
    report = json.loads((config.parent / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["parking_input_files"] == [tariffs_path, REPORT_NAME]
    assert report == {"parking_input_files": [TARIFFS_NAME, REPORT_NAME], "zones": 16,
                      "zone_types": {"campus": 3, "resident_zone": 2, "street_paid": 11},
                      "terminal_stay_rule": "until_fee_end", "minimum_stay_min": 15,
                      "sources": context.stages[PARKING_STAGE]["sources"]}


def test_on_execute_logs_the_zone_count_and_the_file_names_once(tmp_path, monkeypatch, capsys):
    context, _ = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(context)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[parking]")]
    assert len(lines) == 1, lines
    assert "16 zones" in lines[0] and TARIFFS_NAME in lines[0] and REPORT_NAME in lines[0], lines[0]


def test_a_second_execute_on_the_same_config_is_idempotent(tmp_path, monkeypatch):
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(context)
    written = {name: (config.parent / name).read_bytes() for name in (CONFIG_NAME, TARIFFS_NAME, REPORT_NAME)}
    prepare.execute(context)
    assert {name: (config.parent / name).read_bytes() for name in written} == written
    assert written[CONFIG_NAME].decode("utf-8").count(f'<module name="{prepare.PARKING_MODULE}">') == 1


def test_a_conflicting_parking_module_is_refused_and_no_report_is_written(tmp_path, monkeypatch):
    context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    config_modules.write_module(config, prepare.PARKING_MODULE, {
        "enabled": "true", "tariffsPath": "bs_parking_tariffs_2025-01-01.json", "terminalStayRule": "until_fee_end"})
    before = config.read_bytes()
    with pytest.raises(ValueError, match="conflicting braunschweigParking configuration"):
        prepare.execute(context)
    assert config.read_bytes() == before
    assert not (config.parent / REPORT_NAME).exists()


def test_parking_inputs_are_written_last_into_the_final_config(tmp_path, monkeypatch):
    # After the cordon cut, the freight injection and the VRB fare inputs: the module belongs in the config the
    # run reads, which the cut replaces and the freight injection rewrites.
    context = _Context(tmp_path / "prepare", {"output_prefix": PREFIX, "vrb_zone_fares_enabled": True,
                                              "cordon_enabled": True, "freight_enabled": True,
                                              "parking_zones_enabled": True})
    prepare.configure(context)
    context.executing = True
    context.stages[PARKING_STAGE] = _zones_stage_result()
    cut_config = f"{PREFIX}cut_config.xml"
    events = []

    def cut_to_cordon(ctx):
        events.append("cordon_cut")
        (Path(ctx.path()) / cut_config).write_bytes(PREPARED_CONFIG.encode("utf-8"))
        return cut_config

    write_parking_inputs = prepare._write_parking_inputs
    monkeypatch.setattr(prepare.delegate, "execute", lambda ctx: events.append("delegate") or CONFIG_NAME)
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda ctx: events.append("vrb_zones"))
    monkeypatch.setattr(prepare, "_cut_to_cordon", cut_to_cordon)
    monkeypatch.setattr(prepare, "_inject_freight", lambda ctx, name: events.append(("freight", name)))
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda ctx, name: events.append(("vrb_inputs", name)))
    monkeypatch.setattr(prepare, "_write_parking_inputs",
                        lambda ctx, name: (events.append(("parking", name)), write_parking_inputs(ctx, name)))
    assert prepare.execute(context) == cut_config
    assert events == ["delegate", "vrb_zones", "cordon_cut", ("freight", cut_config), ("vrb_inputs", cut_config),
                      ("parking", cut_config)]
    module = config_modules.read_module(Path(context.path()) / cut_config, prepare.PARKING_MODULE)
    assert module["tariffsPath"] == TARIFFS_NAME


@pytest.mark.parametrize("module_name", ["braunschweig.parking.tariff_export", "braunschweig.matsim.config_modules",
                                         "braunschweig.parking.cost"])
def test_prepare_token_changes_when_a_module_that_shapes_the_parking_inputs_changes(monkeypatch, module_name):
    # tariff_export builds and writes the model, config_modules writes the module, and cost.ZoneTariff decides
    # every zone entry of the model: an edit to any of them must invalidate a cached prepared scenario.
    before = prepare.validate(None)
    getsource = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda obj: getsource(obj) + (
        "\n# edited" if getattr(obj, "__name__", None) == module_name else ""))
    assert prepare.validate(None) != before


def _output_context(tmp_path, prepare_dir, **values):
    """A configured matsim.output context over ``prepare_dir``; the run, the jar and the archive are off."""
    out = tmp_path / "out"
    out.mkdir()
    for name in SCENARIO_FILES:
        (prepare_dir / name).write_bytes(b"scenario file stand-in")
    context = _Context(tmp_path / "output_stage", {"run_matsim": False, "write_jar": False, "output_path": str(out),
                                                   "output_prefix": PREFIX, "vrb_zone_fares_enabled": False,
                                                   **values},
                       paths={"matsim.simulation.prepare": prepare_dir})
    output.configure(context)
    context.executing = True
    context.stages["matsim.simulation.prepare"] = CONFIG_NAME
    return context, out


def test_output_copies_the_listed_parking_inputs_next_to_the_exported_config(tmp_path, monkeypatch):
    prepare_context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(prepare_context)
    context, out = _output_context(tmp_path, config.parent, parking_zones_enabled=True)
    output.execute(context)
    # The scenario files, the config and the two listed parking files; working files such as the transit zone
    # shapefile stay behind.
    assert sorted(path.name for path in out.iterdir()) == sorted(
        (*SCENARIO_FILES, CONFIG_NAME, TARIFFS_NAME, REPORT_NAME))
    for name in (TARIFFS_NAME, REPORT_NAME):
        assert (out / name).read_bytes() == (config.parent / name).read_bytes()
    # The exported scenario resolves its tariff model relative to its own config.
    tariffs_path = config_modules.read_module(out / CONFIG_NAME, prepare.PARKING_MODULE)["tariffsPath"]
    assert (out / tariffs_path).is_file()


@pytest.mark.parametrize("values", [{}, {"parking_zones_enabled": False}], ids=["absent", "false"])
def test_output_copies_no_parking_input_when_off(tmp_path, values):
    prepare_dir = tmp_path / "prepare"
    prepare_dir.mkdir()
    (prepare_dir / CONFIG_NAME).write_bytes(PREPARED_CONFIG.encode("utf-8"))
    context, out = _output_context(tmp_path, prepare_dir, **values)
    assert context.declared_config["parking_zones_enabled"] is False
    # The prepared stage holds no parking report: OFF must not look for one.
    output.execute(context)
    assert sorted(path.name for path in out.iterdir()) == sorted((*SCENARIO_FILES, CONFIG_NAME))


def test_copy_parking_inputs_fails_naming_a_missing_listed_file(tmp_path, monkeypatch):
    prepare_context, config = _prepare_context(tmp_path, monkeypatch, parking_zones_enabled=True)
    prepare.execute(prepare_context)
    (config.parent / TARIFFS_NAME).unlink()
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileNotFoundError, match=re.escape(TARIFFS_NAME)):
        output.copy_parking_inputs(config.parent, out, PREFIX)
    assert not list(out.iterdir())
