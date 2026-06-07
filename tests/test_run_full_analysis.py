"""Tests for the default analysis-suite CLI (``run_full_analysis``).

These cover the population-validation default-on contract: the
PopulationSim-style control validation runs as part of the standard
analysis output unless explicitly disabled.
"""
from braunschweig.analysis import run_full_analysis as RFA


def test_population_validation_on_by_default():
    ns = RFA._parse_args(["--output-dir", "o", "--sim-cache", "c"])
    assert ns.population_validation is True


def test_population_validation_can_be_disabled():
    ns = RFA._parse_args(["--output-dir", "o", "--sim-cache", "c",
                          "--no-population-validation"])
    assert ns.population_validation is False
