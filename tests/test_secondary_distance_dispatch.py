"""Tests for per-leg circuity network dispatch in validate_secondary_distances.

Exercises the pure dispatch helper mode_to_network without touching the
cached synpp data (no I/O). Loaded via importlib so the script-level sys.path
insertion fires correctly.
"""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "validate_secondary_distances",
    pathlib.Path("scripts/validate_secondary_distances.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_mode_to_network_mapping():
    assert mod.mode_to_network("car") == "car"
    assert mod.mode_to_network("car_passenger") == "car"
    assert mod.mode_to_network("pt") == "pt"
    assert mod.mode_to_network("walk") == "walk"
    assert mod.mode_to_network("bike") == "walk"


def test_mode_to_network_unknown_defaults_to_car():
    """Unknown modes fall back to 'car' (the most common motorised network)."""
    assert mod.mode_to_network("unknown_mode") == "car"
    assert mod.mode_to_network("") == "car"
