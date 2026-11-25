"""Editing the mode choice reach settings (tour finder, vehicle constraint, tour length) in a MATSim config (#678)."""
from __future__ import annotations

import zipfile

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
