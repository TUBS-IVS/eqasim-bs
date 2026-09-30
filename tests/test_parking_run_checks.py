"""Run checks of the zone-based parking costs (issue #436): the MATSim run must actually use the parking module.

MATSim reads a config module it has no registered group for as an untyped group without error, and the pipeline
builds the jar from whatever eqasim-java-bs checkout ``eqasim_source_path`` points to. A prepared config that enables
``braunschweigParking``, run with a jar that lacks ``org.eqasim.braunschweig.parking``, would therefore price no
parking at all and still look healthy. ``matsim.simulation.run`` checks the jar before the run and the outcome report
of the last iteration after it (``braunschweig.parking.runtime_checks``); with the module absent or disabled it checks
nothing and calls MATSim exactly as before.

Synthetic throughout: prepared configs written by the production module writer, fake jars (zip files with empty
class entries) and fake output trees. MATSim never runs: ``matsim.runtime.java.run`` is replaced by a recorder, so
the real ``matsim.runtime.eqasim.run`` resolves the jar it would execute, and the tests compare that jar with the one
the check opened.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from braunschweig.matsim import config_modules
from braunschweig.matsim.simulation import prepare
from braunschweig.parking import runtime_checks
from matsim.simulation import run as run_stage

PREFIX = "bs_"
CONFIG_NAME = f"{PREFIX}config.xml"
#: The relative jar path matsim.runtime.eqasim returns for a source build of the braunschweig module.
JAR_RELATIVE_PATH = "eqasim-java/braunschweig/target/braunschweig-2.3.1.jar"
#: Written out here independently of the implementation: the class the Java ParkingConfigGroup compiles to.
PARKING_CLASS_ENTRY = "org/eqasim/braunschweig/parking/ParkingConfigGroup.class"
#: Another class of the braunschweig module, present in every jar the pipeline builds.
OTHER_CLASS_ENTRY = "org/eqasim/braunschweig/RunSimulation.class"
RUN_SIMULATION = "org.eqasim.braunschweig.RunSimulation"
LOGGER = "braunschweig.parking.runtime_checks"

#: A prepared MATSim config as the Java preparation writes it: XML declaration, DOCTYPE and flat modules.
PREPARED_CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
                   '<config>\n'
                   '\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n\t</module>\n'
                   '</config>\n')
#: The module braunschweig.matsim.simulation.prepare._write_parking_inputs writes with the zones on.
ENABLED_MODULE = {"enabled": "true", "tariffsPath": f"{PREFIX}parking_tariffs_2026-09-29.json",
                  "terminalStayRule": "until_fee_end"}
DISABLED_MODULE = {**ENABLED_MODULE, "enabled": "false"}


def _prepared_config(directory: Path, parking_module=None) -> Path:
    """A prepared config in ``directory``; with ``parking_module`` the production writer adds braunschweigParking."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_NAME
    path.write_bytes(PREPARED_CONFIG.encode("utf-8"))
    if parking_module is not None:
        config_modules.write_module(path, prepare.PARKING_MODULE, parking_module)
    return path


def _jar(path: Path, *entries: str) -> Path:
    """A zip file with one (empty) class entry per name, standing for a built jar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as jar:
        for entry in entries:
            jar.writestr(entry, b"")
    return path


def _output_tree(simulation_output: Path, iterations, *, with_report: bool = True) -> Path:
    """MATSim's simulation_output with the final events and ``ITERS/it.N``; the outcome report where asked."""
    simulation_output.mkdir(parents=True, exist_ok=True)
    (simulation_output / "output_events.xml.gz").write_bytes(b"")
    for iteration in iterations:
        directory = simulation_output / "ITERS" / f"it.{iteration}"
        directory.mkdir(parents=True)
        if with_report:
            (directory / f"{iteration}.parking_outcomes.csv").write_text("outcome,count,share\n", encoding="utf-8")
    return simulation_output


def _check_messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


def _must_not_be_called(*arguments, **options):
    raise AssertionError(f"a parking run check was called on the OFF path with {arguments} {options}")


# --------------------------------------------------------------------------- the trigger


def test_the_run_checks_read_the_module_the_preparation_writes():
    assert runtime_checks.PARKING_MODULE == prepare.PARKING_MODULE == "braunschweigParking"


def test_a_prepared_config_without_the_parking_module_triggers_no_check(tmp_path):
    assert runtime_checks.parking_module_enabled(_prepared_config(tmp_path)) is False


@pytest.mark.parametrize("module, expected", [
    (ENABLED_MODULE, True),
    (DISABLED_MODULE, False),
    # Without an enabled parameter the Java ParkingConfigGroup keeps its default false.
    ({"tariffsPath": ENABLED_MODULE["tariffsPath"]}, False),
], ids=["enabled", "disabled", "no-enabled-parameter"])
def test_only_an_enabled_parking_module_triggers_the_checks(tmp_path, module, expected):
    assert runtime_checks.parking_module_enabled(_prepared_config(tmp_path, module)) is expected


@pytest.mark.parametrize("value", ["True", "yes", "1", ""])
def test_an_enabled_value_the_java_group_refuses_raises_naming_it(tmp_path, value):
    # ParkingConfigGroup accepts only the literal texts true and false; a jar without the package would ignore any
    # value silently, so the run stage refuses what the typed group would refuse.
    config = _prepared_config(tmp_path, {**ENABLED_MODULE, "enabled": value})
    with pytest.raises(ValueError, match=f"braunschweigParking.enabled.*{value!r}"):
        runtime_checks.parking_module_enabled(config)


# --------------------------------------------------------------------------- the jar, before the run


def test_a_jar_with_the_parking_config_group_passes(tmp_path):
    runtime_checks.require_parking_package(_jar(tmp_path / "branch.jar", OTHER_CLASS_ENTRY, PARKING_CLASS_ENTRY))


def test_a_jar_without_the_parking_package_raises_naming_the_jar_the_class_the_module_and_the_fix(tmp_path):
    jar = _jar(tmp_path / "main.jar", OTHER_CLASS_ENTRY)
    with pytest.raises(RuntimeError) as error:
        runtime_checks.require_parking_package(jar)
    message = str(error.value)
    for part in (str(jar), PARKING_CLASS_ENTRY, "braunschweigParking", "eqasim-java-bs", "eqasim_source_path"):
        assert part in message, (part, message)


def test_a_missing_jar_raises_naming_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing.jar"):
        runtime_checks.require_parking_package(tmp_path / "missing.jar")


def test_a_jar_that_is_not_a_zip_file_raises_naming_the_path(tmp_path):
    broken = tmp_path / "broken.jar"
    broken.write_bytes(b"not a zip archive")
    with pytest.raises(RuntimeError, match="broken.jar"):
        runtime_checks.require_parking_package(broken)


# --------------------------------------------------------------------------- the outcome report, after the run


def test_the_outcome_report_of_the_last_iteration_passes_and_logs_one_line(tmp_path, caplog):
    # it.10 sorts before it.9 as text: the last iteration is the highest NUMBER.
    output = _output_tree(tmp_path / "simulation_output", range(11))
    caplog.set_level(logging.INFO, logger=LOGGER)
    assert runtime_checks.require_parking_outcomes(output) == 10
    assert _check_messages(caplog) == [
        "[parking] run check: jar contains the parking package; outcomes written for iteration 10"]


def test_entries_of_iters_that_are_no_iteration_directory_are_ignored(tmp_path):
    output = _output_tree(tmp_path / "simulation_output", range(3))
    # A FILE named like an iteration directory, and a directory whose suffix is no iteration number.
    (output / "ITERS" / "it.99").write_text("not an iteration directory", encoding="utf-8")
    (output / "ITERS" / "it.x").mkdir()
    assert runtime_checks.require_parking_outcomes(output) == 2


def test_a_run_without_the_outcome_report_raises_naming_the_path_and_the_reason(tmp_path):
    output = _output_tree(tmp_path / "simulation_output", range(3), with_report=False)
    with pytest.raises(RuntimeError) as error:
        runtime_checks.require_parking_outcomes(output)
    message = str(error.value)
    assert str(output / "ITERS" / "it.2" / "2.parking_outcomes.csv") in message
    assert "ParkingOutcomeReportListener" in message and "braunschweigParking" in message


def test_only_an_earlier_iteration_with_a_report_does_not_pass(tmp_path):
    output = _output_tree(tmp_path / "simulation_output", range(2))
    (output / "ITERS" / "it.2").mkdir()
    with pytest.raises(RuntimeError, match="2.parking_outcomes.csv"):
        runtime_checks.require_parking_outcomes(output)


@pytest.mark.parametrize("make_iters", [False, True], ids=["no-ITERS", "empty-ITERS"])
def test_an_output_without_an_iteration_directory_raises(tmp_path, make_iters):
    output = tmp_path / "simulation_output"
    (output / "ITERS" if make_iters else output).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="no iteration directory"):
        runtime_checks.require_parking_outcomes(output)


# --------------------------------------------------------------------------- matsim.simulation.run


class _RunContext:
    """synpp context double of matsim.simulation.run: records what configure() declares, then serves only that.

    Before ``executing`` is set it behaves like synpp's ConfigurationContext (an option resolves to the given value,
    else to its declared default); afterwards like its ExecuteContext: ``config`` and ``stage`` serve only declared
    names, so an execute-time read that configure() did not declare fails here instead of in a real run.
    """

    def __init__(self, root: Path, values: dict):
        self.values = dict(values)
        self.declared_config = {}
        self.declared_stages = set()
        self.executing = False
        self.paths = {"matsim.simulation.prepare": root / "prepare", "matsim.runtime.eqasim": root / "eqasim"}
        self.stage_results = {"matsim.simulation.prepare": CONFIG_NAME, "matsim.runtime.eqasim": JAR_RELATIVE_PATH}
        self._path = root / "run"
        self._path.mkdir(parents=True)

    def config(self, key, *default, **options):  # configure-time config also takes volatile=...
        if self.executing:
            if key not in self.declared_config:
                raise KeyError(f"Config option {key} is not requested")
            return self.declared_config[key]
        self.declared_config[key] = self.values.get(key, default[0] if default else None)
        return self.declared_config[key]

    def stage(self, name, **options):
        if not self.executing:
            self.declared_stages.add(name)
            return None
        if name not in self.declared_stages:
            raise KeyError(f"Stage {name} is not requested")
        return self.stage_results.get(name)

    def path(self, name=None):
        if name is None:
            return str(self._path)
        if name not in self.declared_stages:
            raise KeyError(f"Stage {name} is not requested")
        return str(self.paths[name])


def _run_context(tmp_path, parking_module=None) -> _RunContext:
    """A configured run-stage context over a prepared config, configured for 10 iterations on 4 threads."""
    context = _RunContext(tmp_path, {"matsim_last_iteration": 10, "processes": 4})
    run_stage.configure(context)
    context.executing = True
    _prepared_config(context.paths["matsim.simulation.prepare"], parking_module)
    return context


def _jar_path(context: _RunContext) -> str:
    """The jar as matsim.runtime.eqasim.run resolves it: the eqasim stage path joined with its result."""
    return "%s/%s" % (context.paths["matsim.runtime.eqasim"], JAR_RELATIVE_PATH)


def _expected_arguments(context: _RunContext) -> list[str]:
    """The RunSimulation arguments of the stage before the run checks existed, for these config values."""
    return ["--config-path", "%s/%s" % (context.paths["matsim.simulation.prepare"], CONFIG_NAME),
            "--config:controler.lastIteration", "10", "--config:controler.writeEventsInterval", "10",
            "--config:controler.writePlansInterval", "10", "--config:global.numberOfThreads", "4",
            "--config:qsim.numberOfThreads", "4", "--config:controler.compressionType", "gzip",
            "--simwrapper", "true"]


def _fake_java(monkeypatch, iterations, *, with_report: bool):
    """Replace matsim.runtime.java.run by a recorder that writes MATSim's output tree; returns the call list."""
    calls = []

    def fake_run(context, entry_point, arguments=(), class_path=None, *more, **options):
        calls.append({"entry_point": entry_point, "arguments": list(arguments), "class_path": class_path})
        _output_tree(Path(context.path()) / "simulation_output", iterations, with_report=with_report)

    monkeypatch.setattr(run_stage.java, "run", fake_run)
    return calls


def _record_jar_checks(monkeypatch) -> list[str]:
    """Wrap require_parking_package so a test can compare the jar it opened with the jar the stage runs."""
    checked = []
    real_check = runtime_checks.require_parking_package

    def recording_check(jar_path):
        checked.append(str(jar_path))
        return real_check(jar_path)

    monkeypatch.setattr(runtime_checks, "require_parking_package", recording_check)
    return checked


@pytest.mark.parametrize("parking_module", [None, DISABLED_MODULE], ids=["module-absent", "module-disabled"])
def test_off_calls_no_parking_check_and_runs_matsim_exactly_as_before(tmp_path, monkeypatch, caplog, parking_module):
    context = _run_context(tmp_path, parking_module)
    monkeypatch.setattr(runtime_checks, "require_parking_package", _must_not_be_called)
    monkeypatch.setattr(runtime_checks, "require_parking_outcomes", _must_not_be_called)
    # The OFF run writes no outcome report, and no jar exists: neither is looked at.
    calls = _fake_java(monkeypatch, range(11), with_report=False)
    caplog.set_level(logging.INFO, logger=LOGGER)
    run_stage.execute(context)
    assert calls == [{"entry_point": RUN_SIMULATION, "arguments": _expected_arguments(context),
                      "class_path": _jar_path(context)}]
    assert _check_messages(caplog) == []


def test_on_checks_the_jar_the_stage_runs_and_the_last_iteration_that_ran(tmp_path, monkeypatch, caplog):
    context = _run_context(tmp_path, ENABLED_MODULE)
    _jar(Path(_jar_path(context)), OTHER_CLASS_ENTRY, PARKING_CLASS_ENTRY)
    checked = _record_jar_checks(monkeypatch)
    # Configured for 10 iterations; the eqasim mode-share termination criterion stopped the run after iteration 3.
    calls = _fake_java(monkeypatch, range(4), with_report=True)
    caplog.set_level(logging.INFO, logger=LOGGER)
    run_stage.execute(context)
    assert checked == [calls[0]["class_path"]] == [_jar_path(context)]
    # The checks change nothing about the MATSim call.
    assert calls == [{"entry_point": RUN_SIMULATION, "arguments": _expected_arguments(context),
                      "class_path": _jar_path(context)}]
    assert _check_messages(caplog) == [
        "[parking] run check: jar contains the parking package; outcomes written for iteration 3"]


def test_on_with_a_jar_without_the_parking_package_fails_before_matsim_starts(tmp_path, monkeypatch):
    context = _run_context(tmp_path, ENABLED_MODULE)
    _jar(Path(_jar_path(context)), OTHER_CLASS_ENTRY)
    calls = _fake_java(monkeypatch, range(11), with_report=True)
    with pytest.raises(RuntimeError, match="ParkingConfigGroup.class"):
        run_stage.execute(context)
    assert calls == []


def test_on_without_an_outcome_report_fails_after_the_run(tmp_path, monkeypatch):
    context = _run_context(tmp_path, ENABLED_MODULE)
    _jar(Path(_jar_path(context)), OTHER_CLASS_ENTRY, PARKING_CLASS_ENTRY)
    calls = _fake_java(monkeypatch, range(11), with_report=False)
    with pytest.raises(RuntimeError, match="10.parking_outcomes.csv"):
        run_stage.execute(context)
    assert len(calls) == 1
