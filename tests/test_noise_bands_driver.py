"""Issue #126: driver config derivation."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_noise_bands", REPO / "scripts" / "run_noise_bands.py")
rnb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rnb)

from braunschweig.analysis import noise_bands as nb  # noqa: E402


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


def test_cleanup_draw_workdir_refuses_when_workdir_equals_root(tmp_path, caplog):
    # Final-review finding: the old guard was `workdir != root AND root not in
    # workdir.parents` -- when workdir == root, the first clause is False, so
    # the whole "and" short-circuits to False and the refusal branch is
    # skipped, falling through to `shutil.rmtree(root)`. Calling this with the
    # SAME path for both arguments (as could happen from a caller bug) must
    # never delete that shared root.
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    with caplog.at_level("WARNING", logger="braunschweig.noise_bands"):
        rnb._cleanup_draw_workdir(tmp_path, tmp_path)

    assert sentinel.is_file(), "root directory must survive when workdir == workdir_root"
    assert any("refusing to delete" in record.message for record in caplog.records)


def test_cleanup_draw_workdir_deletes_strict_descendant(tmp_path):
    draw_dir = tmp_path / "seed_1"
    draw_dir.mkdir()
    (draw_dir / "output.txt").write_text("draw output", encoding="utf-8")

    rnb._cleanup_draw_workdir(draw_dir, tmp_path)

    assert not draw_dir.exists()
    assert tmp_path.is_dir()


def test_metric_keyset_extracts_metric_group_pairs():
    frame = pd.DataFrame({
        "draw_seed": [1, 1], "metric_id": ["commute_mean_km_delta_km", "license_pct_delta_pp"],
        "group": ["03101", "03101"], "value": [0.1, -0.2],
    })
    assert nb.metric_keyset(frame) == frozenset({
        ("commute_mean_km_delta_km", "03101"), ("license_pct_delta_pp", "03101"),
    })


def test_metric_keyset_detects_mismatch_between_draw_frames():
    first = pd.DataFrame({
        "draw_seed": [1, 1], "metric_id": ["commute_mean_km_delta_km", "license_pct_delta_pp"],
        "group": ["03101", "03101"], "value": [0.1, -0.2],
    })
    # Second draw is missing the "license_pct_delta_pp" metric entirely -- the
    # kind of upstream stage change (e.g. an empty MiD join for that Kreis)
    # this per-draw check must catch immediately, before the workdir cleanup
    # for either draw runs and before N-1 other draws would otherwise be
    # needed for aggregate_draw_metrics to notice the same inconsistency.
    second = pd.DataFrame({
        "draw_seed": [2], "metric_id": ["commute_mean_km_delta_km"],
        "group": ["03101"], "value": [0.15],
    })
    first_keyset = nb.metric_keyset(first)
    second_keyset = nb.metric_keyset(second)
    assert first_keyset != second_keyset
    assert first_keyset ^ second_keyset == frozenset({("license_pct_delta_pp", "03101")})
