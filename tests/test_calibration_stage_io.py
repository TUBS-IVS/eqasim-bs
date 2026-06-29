import pathlib
import pytest
from braunschweig.calibration import stage_io


def test_resolve_stage_returns_configured_producer():
    aliases = {"data.census.filtered": "braunschweig.popsim.stage"}
    assert stage_io.resolve_stage(aliases, "data.census.filtered", "x") == "braunschweig.popsim.stage"


def test_resolve_stage_falls_back_when_absent():
    assert stage_io.resolve_stage({}, "k", "fallback") == "fallback"
    assert stage_io.resolve_stage(None, "k", "fallback") == "fallback"


def test_load_aliases_reads_popsim_mid_config():
    pytest.importorskip("yaml")
    repo = pathlib.Path(__file__).resolve().parents[1]
    aliases = stage_io.load_aliases(str(repo / "config_popsim_mid_braunschweig.yml"))
    assert aliases["synthesis.population.enriched"] == "braunschweig.popsim.enriched_adapter"
    assert aliases["synthesis.population.spatial.home.locations"] == "braunschweig.synthesis.locations.home_cell"
