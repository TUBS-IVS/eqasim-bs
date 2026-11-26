"""Selecting the same-facility leisure trip constraint in the prepared MATSim config (eqasim-bs#679, ADR-0207)."""
from __future__ import annotations

import zipfile

import pytest

from braunschweig.matsim import config_modules
from braunschweig.matsim.simulation import loop_mode_rule as rule

CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
          '<config>\n'
          '\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n'
          '\t\t<param name="tripConstraints" value="untouched" />\n\t</module>\n'
          '\t<module name="DiscreteModeChoice">\n'
          '\t\t<param name="tripFilters" value="OutsideFilter" />\n'
          '\t\t<param name="tripConstraints" value="%s" />\n'
          '\t</module>\n</config>\n')


def _write(tmp_path, value="VehicleContinuity,SubtourMode", newline="\n"):
    path = tmp_path / "config.xml"
    path.write_bytes((CONFIG % value).replace("\n", newline).encode("utf-8"))
    return path


@pytest.mark.parametrize("old, expected", [
    ("VehicleContinuity,SubtourMode", "VehicleContinuity,SubtourMode,SameFacilityLeisureNonMotorised"),
    ("VehicleContinuity, SubtourMode", "VehicleContinuity, SubtourMode, SameFacilityLeisureNonMotorised"),
    ("VehicleContinuity", "VehicleContinuity,SameFacilityLeisureNonMotorised"),
    ("", "SameFacilityLeisureNonMotorised"),
    ("PassengerConstraint, OutsideConstraint, TransitWalk",
     "PassengerConstraint, OutsideConstraint, TransitWalk, SameFacilityLeisureNonMotorised"),
])
def test_apply_adds_the_constraint_once_and_touches_nothing_else(tmp_path, old, expected):
    path = _write(tmp_path, old)
    before = path.read_bytes()
    assert rule.apply(path) == (old, expected)
    after = path.read_bytes()
    # Only the one value changed: the same bytes with the old value replaced by the new one.
    assert after == before.replace(b'name="tripConstraints" value="%s" />\n\t</module>\n</config>' % old.encode(),
                                   b'name="tripConstraints" value="%s" />\n\t</module>\n</config>' % expected.encode())
    assert config_modules.read_module(path, "DiscreteModeChoice")["tripConstraints"] == expected
    assert config_modules.read_module(path, "global")["tripConstraints"] == "untouched"
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


def test_the_jar_check_passes_with_the_class_and_names_class_and_jar_without_it(tmp_path):
    rule.require_constraint_in_jar(_jar(tmp_path, ["a/B.class", rule.CONSTRAINT_CLASS]))
    other = tmp_path / "other"
    other.mkdir()
    jar = _jar(other, ["a/B.class"])
    with pytest.raises(RuntimeError) as error:
        rule.require_constraint_in_jar(jar)
    message = str(error.value)
    assert "SameFacilityLeisureTripConstraint" in message and str(jar) in message
    assert rule.KEY_ENABLED in message  # names the way out: false = today's behaviour


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
