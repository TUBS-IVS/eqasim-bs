"""Issue #126: driver config derivation."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_noise_bands", REPO / "scripts" / "run_noise_bands.py")
rnb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rnb)


def test_build_draw_config_overrides_seed_workdir_and_stages():
    doc = {"working_directory": "cache_base",
           "run": ["matsim.output"],
           "config": {"random_seed": 1234, "sampling_rate": 0.01}}
    out = rnb.build_draw_config(doc, seed=1240, workdir="cache_noise/seed_1240",
                                run_stages=["synthesis.output"])
    assert out["config"]["random_seed"] == 1240
    assert out["working_directory"] == "cache_noise/seed_1240"
    assert out["run"] == ["synthesis.output"]
    # The input doc is NOT mutated (each draw derives from the same base).
    assert doc["config"]["random_seed"] == 1234
    assert doc["run"] == ["matsim.output"]


def test_build_draw_config_overrides_output_path_under_workdir():
    # Every draw needs its OWN eqasim output directory (never the base config's
    # shared one), so concurrent/sequential draws cannot clobber each other's
    # persons/households/trips CSVs -- see docs/superpowers/sdd/task-3-brief.md
    # "IMPORTANT: output_path must also be per-draw".
    doc = {"working_directory": "cache_base",
           "run": ["matsim.output"],
           "config": {"random_seed": 1234, "sampling_rate": 0.01,
                      "output_path": "eqasim-data/output_bs_shared"}}
    out = rnb.build_draw_config(doc, seed=1240, workdir="cache_noise/seed_1240",
                                run_stages=["synthesis.output"])
    assert out["config"]["output_path"] == "cache_noise/seed_1240/output"
    # The base config's shared output_path is untouched.
    assert doc["config"]["output_path"] == "eqasim-data/output_bs_shared"


def test_build_draw_config_does_not_mutate_nested_config_dict():
    # A shallow copy of `doc["config"]` would still let draw mutations leak back
    # into the base dict shared across all draws -- this must be a deep copy.
    doc = {"working_directory": "cache_base", "run": [], "config": {"random_seed": 1}}
    rnb.build_draw_config(doc, seed=2, workdir="w", run_stages=["synthesis.output"])
    assert doc["config"] == {"random_seed": 1}
