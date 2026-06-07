"""Tests for the default analysis-suite CLI (``run_full_analysis``).

These cover the population-validation default-on contract (the
PopulationSim-style control validation runs as part of the standard
analysis output unless explicitly disabled) and the cross-argument
validation that --sim-cache is required when --skip-dashboard is not set.
"""
import pytest

from braunschweig.analysis import run_full_analysis as RFA


def test_population_validation_on_by_default():
    ns = RFA._parse_args(["--output-dir", "o", "--sim-cache", "c"])
    assert ns.population_validation is True


def test_population_validation_can_be_disabled():
    ns = RFA._parse_args(["--output-dir", "o", "--sim-cache", "c",
                          "--no-population-validation"])
    assert ns.population_validation is False


# ---------------------------------------------------------------------------
# Cross-argument validation: --sim-cache required unless --skip-dashboard
# ---------------------------------------------------------------------------

def test_parse_args_requires_sim_cache_without_skip_dashboard():
    """_parse_args must raise SystemExit(2) when --sim-cache is absent and
    --skip-dashboard is not set -- the validation now lives in _parse_args,
    not in main(), so NameError must not be raised."""
    with pytest.raises(SystemExit) as exc_info:
        RFA._parse_args(["--output-dir", "o"])
    assert exc_info.value.code == 2


def test_parse_args_allows_missing_sim_cache_with_skip_dashboard():
    """When --skip-dashboard is passed, --sim-cache is not required."""
    ns = RFA._parse_args(["--output-dir", "o", "--skip-dashboard"])
    assert ns.sim_cache is None
    assert ns.skip_dashboard is True


# ---------------------------------------------------------------------------
# main() smoke test: must not raise NameError; population validation is called
# ---------------------------------------------------------------------------

def test_main_runs_without_nameerror(tmp_path, monkeypatch):
    """main() must complete without raising NameError and must invoke the
    population validation when it is not disabled."""
    out = tmp_path / "out"
    out.mkdir()

    # Patch the heavy sub-steps so main() only exercises arg handling and
    # control flow -- no real I/O or imports of heavy optional dependencies.
    monkeypatch.setattr(RFA, "_dash", type("_Stub", (), {"main": staticmethod(lambda: None)})())
    monkeypatch.setattr(RFA, "_modestats_inside",
                        type("_Stub", (), {
                            "write_modestats_inside": staticmethod(lambda p: (p / "a.csv", p / "a.png"))
                        })())
    monkeypatch.setattr(RFA, "_mid",
                        type("_Stub", (), {"main": staticmethod(lambda argv: None)})())

    # Capture whether population validation was invoked.
    called = {}

    import braunschweig.analysis.population_validation.run_population_validation as _rpv
    monkeypatch.setattr(_rpv, "run", lambda ns: called.setdefault("pop", True))
    # _parse_args inside _pop_validation is also called; give it a minimal stub.
    monkeypatch.setattr(_rpv, "_parse_args", lambda argv: type("NS", (), {
        "run_output_dir": str(out), "label": None, "prefix": None,
    })())

    rc = RFA.main([
        "--output-dir", str(out),
        "--skip-dashboard",   # avoids the --sim-cache requirement
        "--skip-mid",         # avoids _mid.main doing real I/O
        "--label", "smoke",
    ])

    assert rc == 0
    assert "pop" in called, "population validation must be invoked by default"


def test_main_skips_population_validation_when_disabled(tmp_path, monkeypatch):
    """--no-population-validation must prevent the population validation from
    being called."""
    out = tmp_path / "out"
    out.mkdir()

    monkeypatch.setattr(RFA, "_dash", type("_Stub", (), {"main": staticmethod(lambda: None)})())
    monkeypatch.setattr(RFA, "_modestats_inside",
                        type("_Stub", (), {
                            "write_modestats_inside": staticmethod(lambda p: (p / "a.csv", p / "a.png"))
                        })())
    monkeypatch.setattr(RFA, "_mid",
                        type("_Stub", (), {"main": staticmethod(lambda argv: None)})())

    called = {}

    import braunschweig.analysis.population_validation.run_population_validation as _rpv
    monkeypatch.setattr(_rpv, "run", lambda ns: called.setdefault("pop", True))

    rc = RFA.main([
        "--output-dir", str(out),
        "--skip-dashboard",
        "--skip-mid",
        "--label", "smoke",
        "--no-population-validation",
    ])

    assert rc == 0
    assert "pop" not in called, "population validation must NOT be called when disabled"
