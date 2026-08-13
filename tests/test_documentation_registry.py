"""Guards over the REAL model registries (docs/registry/**, docs/runs/).

The synthetic strictness tests live in test_documentation_schema.py; these run
the same strict parsers over the committed registry content, so a renamed test,
a deleted code path or a stage id that no DAG knows breaks the suite instead of
silently degrading the registry (readiness-register design carried forward).

Deliberately data-independent: everything here resolves against committed files
and the committed DAG snapshots, never against the local eqasim-data tree.
"""
from __future__ import annotations

import os

from braunschweig.documentation import dag, registries

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dag_union_nodes():
    snapshots = dag.load_all_snapshots(REPO_ROOT)
    nodes = set()
    for snapshot in snapshots.values():
        nodes.update(snapshot["nodes"])
    return nodes


def test_feature_registry_parses_and_is_complete():
    records = registries.load_features(REPO_ROOT)
    assert len(records) >= 69, (
        "the feature registry lost records -- 67 migrated readiness declarations "
        "plus the escort and SrV-location-type features are the 2026-08 baseline")


def test_feature_stage_references_resolve_against_the_dag():
    nodes = _dag_union_nodes()
    for record in registries.load_features(REPO_ROOT):
        for stage in record["stages"]:
            assert stage in nodes, (
                f"{record['feature']}: stage '{stage}' is not a node of any "
                "committed DAG snapshot (docs/registry/dag/)")


def test_feature_active_pipelines_have_reachable_stages():
    snapshots = dag.load_all_snapshots(REPO_ROOT)
    pipeline_nodes = {
        "popsim_mid": set(snapshots["production"]["nodes"]),
        "popsim_open": set(snapshots["popsim_open"]["nodes"]),
        "simple_ipf_open": set(snapshots["simple_ipf_open"]["nodes"]),
    }
    for record in registries.load_features(REPO_ROOT):
        for pipeline, applicability in record["pipelines"].items():
            if applicability == "active" and record["stages"]:
                assert any(stage in pipeline_nodes[pipeline]
                           for stage in record["stages"]), (
                    f"{record['feature']}: declared active under {pipeline} but "
                    "none of its stages is reachable in that pipeline's DAG")


def test_feature_code_paths_and_tests_exist():
    for record in registries.load_features(REPO_ROOT):
        for path in record["code_paths"]:
            assert os.path.exists(os.path.join(REPO_ROOT, path)), (
                f"{record['feature']}: code path missing: {path}")
        for test in record["evidence"]["tests"]:
            test_path = str(test).partition("::")[0]
            assert os.path.exists(os.path.join(REPO_ROOT, test_path)), (
                f"{record['feature']}: declared test missing: {test}")
