"""Editing the mode choice reach settings (tour finder, vehicle constraint, tour length) in a MATSim config (#678)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from braunschweig.matsim.simulation import mode_choice_reach as R
from tests.test_config_modules import DMC_CONFIG, _config

TODAY = R.ReachSettings(False, 6)


def test_apply_with_todays_values_leaves_the_file_byte_identical(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    assert R.apply(path, TODAY) == {}
    assert path.read_bytes() == DMC_CONFIG.encode("utf-8")


def test_apply_sets_only_the_length_limit(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    changes = R.apply(path, R.ReachSettings(False, 11))
    assert changes == {"maximumLength": ("6", "11")}
    new, old = path.read_bytes().decode("utf-8").split("\n"), DMC_CONFIG.split("\n")
    assert sum(a != b for a, b in zip(old, new)) == 1 and len(old) == len(new)


def test_apply_selects_the_finer_rule(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    R.apply(path, R.ReachSettings(True, 13))
    text = path.read_bytes().decode("utf-8")
    assert 'name="tourFinder" value="IsolatedOutsideTrips"' in text
    assert 'name="tourConstraints" value="FromTripBased, BorderTourVehicleContinuity"' in text
    assert '<param name="maximumLength" value="13" />' in text
    assert '<param name="maximumLength" value="99" />' in text  # the other set is untouched


def test_apply_can_be_repeated_and_undone(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    R.apply(path, R.ReachSettings(True, 13))
    assert R.apply(path, R.ReachSettings(True, 13)) == {}
    R.apply(path, TODAY)
    assert path.read_bytes() == DMC_CONFIG.encode("utf-8")


def test_apply_refuses_the_finer_rule_without_a_vehicle_constraint_token(tmp_path):
    path = _config(tmp_path, DMC_CONFIG.replace("VehicleContinuity", "SomethingElse"))
    with pytest.raises(ValueError, match="tourConstraints"):
        R.apply(path, R.ReachSettings(True, 6))


def test_write_initial_config_writes_a_second_file(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    second = R.write_initial_config(path, R.ReachSettings(True, 13))
    assert second == tmp_path / "bs_config_initial_mode_choice.xml"
    assert path.read_bytes() == DMC_CONFIG.encode("utf-8")
    assert 'value="IsolatedOutsideTrips"' in second.read_text(encoding="utf-8")
    assert 'name="maximumLength" value="13"' in second.read_text(encoding="utf-8")


def test_write_initial_config_returns_the_config_when_nothing_differs(tmp_path):
    path = _config(tmp_path, DMC_CONFIG)
    assert R.write_initial_config(path, TODAY) == path
    assert not (tmp_path / "bs_config_initial_mode_choice.xml").exists()


def test_require_border_vehicle_constraint_names_the_missing_class(tmp_path):
    jar = tmp_path / "eqasim.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("org/eqasim/Other.class", b"x")
    with pytest.raises(RuntimeError, match="BorderTourVehicleConstraint"):
        R.require_border_vehicle_constraint(jar)
    with zipfile.ZipFile(jar, "a") as z:
        z.writestr(R.BORDER_VEHICLE_CONSTRAINT_CLASS, b"x")
    R.require_border_vehicle_constraint(jar)


def test_configure_declares_the_four_keys_with_their_defaults():
    calls = {}

    class Ctx:
        def config(self, key, default=None, **kwargs):
            calls.setdefault(key, default)
            return default

    R.configure(Ctx())
    assert calls == {R.KEY_LOOP_ISOLATE: False, R.KEY_LOOP_MAX_TOUR_LENGTH: 11,
                     R.KEY_INITIAL_ISOLATE: True, R.KEY_INITIAL_MAX_TOUR_LENGTH: 13}


REAL_MODULE = (Path(__file__).parent / "fixtures" / "dmc_module_prod100.xml").read_text(encoding="utf-8")
REAL_CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
               '<config>\n' + REAL_MODULE + '</config>\n')


def _params(path):
    """The three reach values of the config, read with an XML parser (independent of the text edit)."""
    root = ET.parse(str(path)).getroot()
    module = next(m for m in root.findall("module") if m.get("name") == "DiscreteModeChoice")
    params = {p.get("name"): p.get("value") for p in module.findall("param")}
    length = next(s for s in module.findall("parameterset") if s.get("type") == "tourFilter:TourLength")
    return params["tourFinder"], params["tourConstraints"], next(
        p.get("value") for p in length.findall("param") if p.get("name") == "maximumLength")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_apply_on_an_eqasim_generated_module_selects_undoes_and_keeps_the_bytes(tmp_path, newline):
    """The module block is copied from the config of the 100 % production run (comments, trailing-space tags)."""
    original = REAL_CONFIG.replace("\n", newline).encode("utf-8")
    path = tmp_path / "bs_config.xml"
    path.write_bytes(original)
    assert R.apply(path, TODAY) == {} and path.read_bytes() == original
    R.apply(path, R.ReachSettings(True, 13))
    assert _params(path) == ("IsolatedOutsideTrips", "FromTripBased, BorderTourVehicleContinuity", "13")
    assert path.read_bytes().count(newline.encode()) == original.count(newline.encode())
    R.apply(path, TODAY)
    assert path.read_bytes() == original


def test_apply_swaps_the_border_constraint_back_when_the_finer_rule_is_off(tmp_path):
    border = DMC_CONFIG.replace("FromTripBased, VehicleContinuity", "FromTripBased,BorderTourVehicleContinuity")
    path = _config(tmp_path, border)
    R.apply(path, TODAY)
    assert path.read_bytes() == DMC_CONFIG.replace("FromTripBased, VehicleContinuity",
                                                   "FromTripBased,VehicleContinuity").encode("utf-8")


@pytest.mark.parametrize("isolate", [True, False])
def test_apply_refuses_a_constraint_list_with_both_vehicle_tokens(tmp_path, isolate):
    both = DMC_CONFIG.replace("FromTripBased, VehicleContinuity",
                              "FromTripBased, VehicleContinuity, BorderTourVehicleContinuity")
    path = _config(tmp_path, both)
    with pytest.raises(ValueError, match="tourConstraints"):
        R.apply(path, R.ReachSettings(isolate, 11))
    assert path.read_bytes() == both.encode("utf-8")


def test_apply_does_not_rewrite_a_file_that_already_has_the_tour_finder(tmp_path):
    path = _config(tmp_path, DMC_CONFIG.replace("ActivityBased", "IsolatedOutsideTrips")
                   .replace("FromTripBased, VehicleContinuity", "FromTripBased, BorderTourVehicleContinuity")
                   .replace('value="6"', 'value="13"'))
    before = path.stat().st_mtime_ns
    assert R.apply(path, R.ReachSettings(True, 13)) == {}
    assert path.stat().st_mtime_ns == before


class _Ctx:
    def __init__(self, values):
        self._values = values

    def config(self, key):
        return self._values[key]


@pytest.mark.parametrize("raw, expected", [("false", False), ("False", False), ("TRUE", True), (True, True),
                                           (False, False)])
def test_settings_parse_the_switch_strictly(raw, expected):
    values = {R.KEY_LOOP_ISOLATE: raw, R.KEY_LOOP_MAX_TOUR_LENGTH: "11"}
    assert R.loop_settings(_Ctx(values)) == R.ReachSettings(expected, 11)
    values = {R.KEY_INITIAL_ISOLATE: raw, R.KEY_INITIAL_MAX_TOUR_LENGTH: 13}
    assert R.initial_settings(_Ctx(values)) == R.ReachSettings(expected, 13)


@pytest.mark.parametrize("isolate, length", [("yes", 11), (1, 11), (None, 11), (True, 0), (True, "x"), (True, 2.5),
                                             (True, True), (True, -3)])
def test_settings_reject_a_bad_value_naming_the_key(isolate, length):
    with pytest.raises(ValueError, match="braunschweig.mode_choice"):
        R.loop_settings(_Ctx({R.KEY_LOOP_ISOLATE: isolate, R.KEY_LOOP_MAX_TOUR_LENGTH: length}))
