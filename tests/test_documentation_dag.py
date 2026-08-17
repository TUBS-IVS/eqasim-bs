"""Tests for synpp DAG extraction (braunschweig.documentation.dag).

The synthetic test builds a two-stage pipeline in tmp_path, so it verifies the
extraction mechanics (dryrun resolution, deterministic snapshot shape) without
touching the real stage modules or any data. The committed-snapshot guards make
sure the tracked ``docs/registry/dag/*.json`` artifacts stay parseable and
non-empty; their FRESHNESS against the live configs is the checker's job (it
needs the full scientific environment, which this test must not assume).
"""
from __future__ import annotations

import json
import os
import textwrap

import pytest

pytest.importorskip("synpp", reason="DAG extraction requires synpp (eqasim env)")

from braunschweig.documentation import dag

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_demo_pipeline(tmp_path):
    package = tmp_path / "demo_dag_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "stage_a.py").write_text(textwrap.dedent(
        """
        def configure(context):
            pass

        def execute(context):
            return 1
        """), encoding="utf-8")
    (package / "stage_b.py").write_text(textwrap.dedent(
        """
        def configure(context):
            context.stage("demo_dag_pkg.stage_a")

        def execute(context):
            return 2
        """), encoding="utf-8")
    (tmp_path / "demo_config.yml").write_text(textwrap.dedent(
        """
        run:
          - demo_dag_pkg.stage_b
        config: {}
        """), encoding="utf-8")


def test_extract_resolves_dependencies_without_executing(tmp_path):
    _write_demo_pipeline(tmp_path)
    data = dag.extract(str(tmp_path), "demo_config.yml")
    assert data["nodes"] == ["demo_dag_pkg.stage_a", "demo_dag_pkg.stage_b"]
    assert data["edges"] == [["demo_dag_pkg.stage_a", "demo_dag_pkg.stage_b"]]
    assert data["targets"] == ["demo_dag_pkg.stage_b"]
    assert data["config"] == {"base": "demo_config.yml", "overlay": None}


def test_extract_rejects_config_without_run_list(tmp_path):
    (tmp_path / "empty.yml").write_text("config: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no non-empty 'run' list"):
        dag.extract(str(tmp_path), "empty.yml")


def test_committed_snapshots_parse_and_are_nonempty():
    snapshots = dag.load_all_snapshots(REPO_ROOT)
    assert set(snapshots) == set(dag.PIPELINE_CONFIGS), (
        "every tracked pipeline needs a committed snapshot under docs/registry/dag/ "
        "(regenerate with: python -m braunschweig.documentation dag)")
    for name, snapshot in snapshots.items():
        assert snapshot["nodes"], f"{name}: empty node list"
        assert snapshot["edges"], f"{name}: empty edge list"
        node_set = set(snapshot["nodes"])
        for source, target in snapshot["edges"]:
            assert source in node_set and target in node_set, (
                f"{name}: edge {source}->{target} references an unknown node")
        for target in snapshot["targets"]:
            assert target in node_set, f"{name}: run target {target} not in nodes"


def test_snapshots_are_deterministic_json():
    for name in dag.PIPELINE_CONFIGS:
        path = dag.snapshot_path(REPO_ROOT, name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert text == json.dumps(json.loads(text), indent=1, sort_keys=True) + "\n", (
            f"{name}.json is not in canonical (indent=1, sorted-keys) form")
