"""Tests for braunschweig.synpp_deterministic: order-independent implicit-config propagation.

synpp 1.6.2 propagates the config keys of upstream stages into downstream stages ("implicit
config parameters") by walking the stage graph from an unordered ``set`` of source hashes and
following only the first downstream edge of every stage. The propagated key set, and hence the
stage hash that names the cache entry, therefore depends on Python's per-process string-hash
randomisation. These tests pin the replacement: a topological, all-edges propagation whose
result does not depend on insertion or iteration order.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from braunschweig import synpp_deterministic as D

REPO_ROOT = Path(__file__).resolve().parents[1]


def _stage(name, config, dependencies=None, downstream=None, volatile=()):
    stage = {
        "wrapper": None,
        "name": name,
        "config": dict(config),
        "volatile_config": set(volatile),
    }
    if dependencies is not None:
        stage["dependencies"] = list(dependencies)
    if downstream is not None:
        stage["downstream"] = list(downstream)
    return stage


def _diamond(order):
    """A -> B -> D and A -> C -> D; B is called by D with an explicitly passed parameter.

    ``order`` permutes the dict insertion order so the test can prove order independence.
    """
    stages = {
        "A": _stage("A", {"a": 1, "vol": "x"}, volatile=("vol",),
                    downstream=[{"hash": "B", "position": 0, "length": 1, "passed-parameters": set()},
                                {"hash": "C", "position": 0, "length": 1, "passed-parameters": set()}]),
        "B": _stage("B", {"b": 2, "passed": 9}, dependencies=["A"],
                    downstream=[{"hash": "D", "position": 0, "length": 2, "passed-parameters": {"passed"}}]),
        "C": _stage("C", {"c": 3}, dependencies=["A"],
                    downstream=[{"hash": "D", "position": 1, "length": 2, "passed-parameters": set()}]),
        "D": _stage("D", {"d": 4}, dependencies=["B", "C"]),
    }
    return {key: stages[key] for key in order}


@pytest.mark.parametrize("order", [("A", "B", "C", "D"), ("D", "C", "B", "A"), ("C", "D", "A", "B")])
def test_propagation_reaches_every_downstream_through_every_path(order):
    stages = _diamond(order)
    D.propagate_implicit_config(stages)
    assert stages["B"]["config"] == {"b": 2, "passed": 9, "a": 1}
    assert stages["C"]["config"] == {"c": 3, "a": 1}
    # D receives A's key through both paths, B's own key, C's key, but NOT the explicitly passed
    # parameter of B (the caller controls it) and NOT the volatile key of A.
    assert stages["D"]["config"] == {"d": 4, "a": 1, "b": 2, "c": 3}


def test_propagation_is_independent_of_insertion_order():
    results = []
    for order in (("A", "B", "C", "D"), ("D", "C", "B", "A"), ("B", "A", "D", "C")):
        stages = _diamond(order)
        D.propagate_implicit_config(stages)
        results.append({key: dict(sorted(stage["config"].items())) for key, stage in sorted(stages.items())})
    assert results[0] == results[1] == results[2]


def test_propagation_rejects_conflicting_values():
    stages = _diamond(("A", "B", "C", "D"))
    stages["C"]["config"]["b"] = 99  # conflicts with B's b == 2 when both reach D
    with pytest.raises(D.ImplicitConfigConflict):
        D.propagate_implicit_config(stages)


def test_propagation_rejects_cycles():
    stages = _diamond(("A", "B", "C", "D"))
    stages["A"]["dependencies"] = ["D"]
    with pytest.raises(D.ImplicitConfigCycle):
        D.propagate_implicit_config(stages)


def test_install_is_idempotent_and_patches_synpp():
    import synpp.pipeline as pipeline

    original = D.original_process_stages()
    D.install()
    assert pipeline.process_stages is D.process_stages
    D.install()
    assert pipeline.process_stages is D.process_stages
    assert original is not D.process_stages


def test_install_refuses_an_unsupported_synpp_version(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
    with pytest.raises(D.UnsupportedSynppVersion):
        D.install(force_version_check=True)


REPRO = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
from braunschweig import synpp_deterministic as D
from braunschweig import config_compose
D.install()
from synpp.pipeline import process_stages
settings = config_compose.compose(sys.argv[1], sys.argv[2])
definitions = []
for item in settings["run"]:
    params = {}
    if isinstance(item, dict):
        key = list(item.keys())[0]; params = item[key]; item = key
    definitions.append({"descriptor": item, "config": params})
registry = process_stages(definitions, settings.get("config", {}), settings.get("externals", {}), settings.get("aliases", {}))
print(json.dumps(sorted(registry.keys())))
"""


def test_real_pipeline_hashes_are_identical_across_hash_seeds(tmp_path):
    """The production graph (base + 25 % overlay) must hash identically under two PYTHONHASHSEEDs.

    Without the patch synpp 1.6.2 produced different hashes for several stages between seeds
    (measured 2026-09-05: e.g. replacement_education_gravity with 119 vs 70 propagated keys).
    """
    script = tmp_path / "repro.py"
    script.write_text(REPRO, encoding="utf-8")
    outputs = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, str(script), "configs/base_bs.yml", "configs/overlays/test_25pct.yml"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=600,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        outputs.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert len(outputs[0]) > 50
    assert outputs[0] == outputs[1]
