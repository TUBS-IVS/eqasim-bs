"""Selecting the same-facility leisure and stroll trip constraints in the prepared MATSim config (eqasim-bs#679, ADR-0207)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from braunschweig.matsim import config_modules
from braunschweig.matsim.simulation import loop_mode_rule as rule

#: The DiscreteModeChoice module exactly as the prepared config of the 1 % evidence arm D of issue #679 carries it
#: (run manifest docs/runs/i679-stroll-loops-ab-1pct-2026-11-25.yml; tabs, comments, flat params followed by parameter
#: sets, tripConstraints value "OutsideConstraint, TransitWalk"); a reader that supported only flat modules failed on it.
REAL_MODULE = (Path(__file__).parent / "fixtures" / "dmc_module_real.xml").read_bytes().decode("utf-8")
REAL_VALUE = "OutsideConstraint, TransitWalk"
CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
          '<config>\n'
          '\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n'
          '\t\t<param name="tripConstraints" value="untouched" />\n\t</module>\n'
          + REAL_MODULE + '</config>\n')


def _config_text(value=REAL_VALUE, newline="\n"):
    assert 'name="tripConstraints" value="%s"' % REAL_VALUE in CONFIG
    return CONFIG.replace('name="tripConstraints" value="%s"' % REAL_VALUE,
                          'name="tripConstraints" value="%s"' % value).replace("\n", newline)


def _write(tmp_path, value=REAL_VALUE, newline="\n"):
    path = tmp_path / "config.xml"
    path.write_bytes(_config_text(value, newline).encode("utf-8"))
    return path


@pytest.mark.parametrize("old, expected", [
    (REAL_VALUE, "OutsideConstraint, TransitWalk, SameFacilityLeisureNonMotorised, StrollNonMotorised"),
    ("VehicleContinuity,SubtourMode", "VehicleContinuity,SubtourMode,SameFacilityLeisureNonMotorised,StrollNonMotorised"),
    ("VehicleContinuity,StrollNonMotorised", "VehicleContinuity,StrollNonMotorised,SameFacilityLeisureNonMotorised"),
    ("", "SameFacilityLeisureNonMotorised,StrollNonMotorised"),
])
def test_apply_adds_the_constraint_once_and_touches_nothing_else(tmp_path, old, expected):
    """On the real module (parameter sets included): only the one value changes, every parameter set keeps its bytes."""
    path = _write(tmp_path, old)
    before = path.read_bytes()
    assert rule.apply(path) == (old, expected)
    after = path.read_bytes()
    assert after == _config_text(expected).encode("utf-8")
    assert config_modules.read_param(path, "DiscreteModeChoice", "tripConstraints") == expected
    assert config_modules.read_param(path, "global", "tripConstraints") == "untouched"
    assert after.count(b"<parameterset") == before.count(b"<parameterset") == REAL_MODULE.count("<parameterset")
    assert REAL_MODULE.count("<parameterset") > 0  # the fixture must keep what the flat reader could not read
    # A second call is a no-op: no change, no return value.
    assert rule.apply(path) is None
    assert path.read_bytes() == after


def test_apply_keeps_crlf_line_endings(tmp_path):
    path = _write(tmp_path, newline="\r\n")
    assert rule.apply(path) is not None
    assert path.read_bytes().count(b"\r\n") == path.read_bytes().count(b"\n")


def test_apply_refuses_a_config_without_the_module_or_the_param(tmp_path):
    no_module = tmp_path / "no_module.xml"
    no_module.write_text('<config>\n\t<module name="global" />\n</config>\n', encoding="utf-8")
    with pytest.raises(ValueError, match="DiscreteModeChoice"):
        rule.apply(no_module)
    no_param = tmp_path / "no_param.xml"
    no_param.write_text('<config>\n\t<module name="DiscreteModeChoice">\n'
                        '\t\t<param name="tripFilters" value="OutsideFilter" />\n\t</module>\n</config>\n',
                        encoding="utf-8")
    before = no_param.read_bytes()
    with pytest.raises(ValueError, match="tripConstraints"):
        rule.apply(no_param)
    assert no_param.read_bytes() == before


def _jar(tmp_path, entries):
    path = tmp_path / "eqasim.jar"
    with zipfile.ZipFile(path, "w") as archive:
        for name in entries:
            archive.writestr(name, b"\xca\xfe\xba\xbe")
    return path


def test_the_jar_check_passes_with_both_classes_and_names_each_missing_class_and_the_jar(tmp_path):
    rule.require_constraint_in_jar(_jar(tmp_path, ["a/B.class", *rule.CONSTRAINT_CLASSES]))
    for index, present in enumerate(rule.CONSTRAINT_CLASSES):
        folder = tmp_path / str(index)
        folder.mkdir()
        jar = _jar(folder, ["a/B.class", present])
        with pytest.raises(RuntimeError) as error:
            rule.require_constraint_in_jar(jar)
        message = str(error.value)
        missing = rule.CONSTRAINT_CLASSES[1 - index]
        assert missing in message and present not in message and str(jar) in message
        assert "StrollTripConstraint" in message and rule.KEY_ENABLED in message  # names the fix and the way out
    empty = tmp_path / "none"
    empty.mkdir()
    with pytest.raises(RuntimeError) as error:
        rule.require_constraint_in_jar(_jar(empty, ["a/B.class"]))
    assert all(name in str(error.value) for name in rule.CONSTRAINT_CLASSES)


def test_the_jar_check_refuses_a_missing_file_and_a_non_zip(tmp_path):
    with pytest.raises(FileNotFoundError):
        rule.require_constraint_in_jar(tmp_path / "missing.jar")
    garbage = tmp_path / "garbage.jar"
    garbage.write_bytes(b"not a zip")
    with pytest.raises(RuntimeError, match="not a zip"):
        rule.require_constraint_in_jar(garbage)


@pytest.mark.parametrize("configured, expected", [(None, False), (True, True)])
def test_configure_declares_the_switch_off_by_default_and_enabled_reads_it(configured, expected):
    declared = {}

    class _Context:
        def config(self, key, default=None):
            declared.setdefault(key, default)
            return default if configured is None else configured

    rule.configure(_Context())
    assert declared == {rule.KEY_ENABLED: False}
    assert rule.enabled(_Context()) is expected
