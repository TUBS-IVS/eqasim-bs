"""Config keys of the portal-trip layer (eqasim-bs#442): one home for key names and defaults,
and the range checks that turn a bad YAML into a configure-time failure."""
import pytest

from braunschweig.synthesis.portal_trips import config_keys as keys


def test_keys_share_the_portal_prefix_and_the_declared_defaults():
    assert keys.KEY_ENABLED == "braunschweig.portal.enabled"
    assert keys.DEFAULT_ENABLED is True
    assert keys.KEY_MAX_ROUTABLE_DISTANCE_M == "braunschweig.portal.max_routable_distance_m"
    assert keys.DEFAULT_MAX_ROUTABLE_DISTANCE_M == 45000.0
    assert keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE == "braunschweig.portal.external_point_distance_tolerance"
    assert keys.DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE == 0.2
    assert keys.KEY_MODE_SUBSTITUTION_WARN_SHARE == "braunschweig.portal.mode_substitution_warn_share"
    assert keys.DEFAULT_MODE_SUBSTITUTION_WARN_SHARE == 0.1
    assert keys.KEY_FALLBACK_WARN_SHARE == "braunschweig.portal.fallback_warn_share"
    assert keys.DEFAULT_FALLBACK_WARN_SHARE == 0.1
    assert keys.OUTSIDE_PURPOSE == "outside"
    # Disjoint from the in-commuter (100000) and student in-commuter (200000) seed offsets.
    assert keys.RNG_OFFSET == 300000


def test_validate_settings_accepts_the_defaults():
    keys.validate_settings(keys.DEFAULT_MAX_ROUTABLE_DISTANCE_M,
                           keys.DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE,
                           keys.DEFAULT_MODE_SUBSTITUTION_WARN_SHARE,
                           keys.DEFAULT_FALLBACK_WARN_SHARE)


@pytest.mark.parametrize("threshold, tolerance, warn_share, fallback_share, key", [
    (0.0, 0.2, 0.1, 0.1, keys.KEY_MAX_ROUTABLE_DISTANCE_M),
    (-1.0, 0.2, 0.1, 0.1, keys.KEY_MAX_ROUTABLE_DISTANCE_M),
    (45000.0, 0.0, 0.1, 0.1, keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE),
    (45000.0, 1.0, 0.1, 0.1, keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE),
    (45000.0, 0.2, 0.0, 0.1, keys.KEY_MODE_SUBSTITUTION_WARN_SHARE),
    (45000.0, 0.2, 1.5, 0.1, keys.KEY_MODE_SUBSTITUTION_WARN_SHARE),
    (45000.0, 0.2, 0.1, 0.0, keys.KEY_FALLBACK_WARN_SHARE),
    (45000.0, 0.2, 0.1, 1.5, keys.KEY_FALLBACK_WARN_SHARE),
])
def test_validate_settings_names_the_offending_key(threshold, tolerance, warn_share, fallback_share, key):
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        keys.validate_settings(threshold, tolerance, warn_share, fallback_share)


def test_validate_settings_accepts_a_fallback_share_of_exactly_one():
    keys.validate_settings(45000.0, 0.2, 0.1, 1.0)


def test_timetable_gate_keys_are_declared_with_their_defaults():
    assert keys.KEY_PT_GATE_MODES == "braunschweig.portal.pt_gate_modes"
    assert keys.DEFAULT_PT_GATE_MODES == "all"
    assert keys.KEY_GATE_SERVICE_WINDOW_S == "braunschweig.portal.gate_service_window_s"
    assert keys.DEFAULT_GATE_SERVICE_WINDOW_S == 3600.0
    assert keys.KEY_GATE_CANDIDATES == "braunschweig.portal.gate_candidates"
    assert keys.DEFAULT_GATE_CANDIDATES == 5
    assert keys.KEY_SHIFT_WINDOW_S == "braunschweig.portal.shift_window_s"
    assert keys.DEFAULT_SHIFT_WINDOW_S == 7200.0
    assert keys.KEY_SHIFT_STEP_S == "braunschweig.portal.shift_step_s"
    assert keys.DEFAULT_SHIFT_STEP_S == 1800.0
    assert keys.KEY_MIN_STAY_S == "braunschweig.portal.min_stay_s"
    assert keys.DEFAULT_MIN_STAY_S == 1800.0
    assert keys.KEY_NO_CONNECTION_WARN_SHARE == "braunschweig.portal.no_connection_warn_share"
    assert keys.DEFAULT_NO_CONNECTION_WARN_SHARE == 0.05


@pytest.mark.parametrize("override, key", [
    ({"gate_candidates": 0}, keys.KEY_GATE_CANDIDATES),
    ({"gate_service_window_s": -1.0}, keys.KEY_GATE_SERVICE_WINDOW_S),
    ({"shift_window_s": -1.0}, keys.KEY_SHIFT_WINDOW_S),
    ({"shift_step_s": 0.0}, keys.KEY_SHIFT_STEP_S),
    ({"min_stay_s": -1.0}, keys.KEY_MIN_STAY_S),
    ({"no_connection_warn_share": 0.0}, keys.KEY_NO_CONNECTION_WARN_SHARE),
    ({"no_connection_warn_share": 1.5}, keys.KEY_NO_CONNECTION_WARN_SHARE),
])
def test_validate_settings_rejects_invalid_timetable_gate_settings(override, key):
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        keys.validate_settings(45000.0, 0.2, 0.1, 0.1, **override)


def test_validate_settings_accepts_a_no_connection_share_of_exactly_one_and_zero_windows():
    keys.validate_settings(45000.0, 0.2, 0.1, 0.1, no_connection_warn_share=1.0, shift_window_s=0.0,
                           gate_service_window_s=0.0, min_stay_s=0.0)
