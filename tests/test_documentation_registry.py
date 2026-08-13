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


def test_stage_registry_covers_every_dag_node():
    """Every DAG node has a stage record; records outside every DAG must be the
    explicitly parked ones (production False, all pipelines not_used)."""
    nodes = _dag_union_nodes()
    records = {record["stage"]: record for record in registries.load_stages(REPO_ROOT)}
    missing = nodes - set(records)
    assert not missing, f"DAG stages without a registry record: {sorted(missing)}"
    for stage, record in records.items():
        if stage not in nodes:
            assert record["production"] is False and all(
                value == "not_used" for value in record["pipelines"].values()), (
                f"{stage}: not in any DAG snapshot but not declared as parked")


def test_stage_production_flag_matches_the_production_dag():
    production_nodes = set(dag.load_snapshot(REPO_ROOT, "production")["nodes"])
    for record in registries.load_stages(REPO_ROOT):
        assert record["production"] == (record["stage"] in production_nodes), (
            f"{record['stage']}: production flag contradicts the production DAG")


def test_stage_feature_references_resolve():
    feature_ids = {record["feature"] for record in registries.load_features(REPO_ROOT)}
    for record in registries.load_stages(REPO_ROOT):
        for feature in record.get("features") or []:
            assert feature in feature_ids, (
                f"{record['stage']}: unknown feature reference '{feature}'")


def test_stage_code_paths_exist():
    for record in registries.load_stages(REPO_ROOT):
        for path in record["code"]:
            assert os.path.exists(os.path.join(REPO_ROOT, path)), (
                f"{record['stage']}: code path missing: {path}")


def test_data_registry_parses_and_covers_every_stage_input():
    datasets = {record["dataset"] for record in registries.load_data(REPO_ROOT)}
    assert len(datasets) >= 52
    for record in registries.load_stages(REPO_ROOT):
        for dataset in record.get("inputs") or []:
            assert dataset in datasets, (
                f"{record['stage']}: unknown dataset reference '{dataset}'")


def test_data_registry_used_by_references_resolve():
    stage_ids = {record["stage"] for record in registries.load_stages(REPO_ROOT)}
    for record in registries.load_data(REPO_ROOT):
        for stage in record.get("used_by") or []:
            assert stage in stage_ids, (
                f"{record['dataset']}: unknown stage reference '{stage}'")


def test_data_registry_verifier_entries_resolve():
    """Every declared verifier_entry must match an Input name prefix of the
    canonical input verifier, so the registry and the preflight stay in sync."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_braunschweig_inputs",
        os.path.join(REPO_ROOT, "scripts", "verify_braunschweig_inputs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefixes = {entry.name.split()[0] for entry in module.INPUTS}
    for record in registries.load_data(REPO_ROOT):
        entry = (record.get("verification") or {}).get("verifier_entry")
        if entry:
            assert entry in prefixes, (
                f"{record['dataset']}: verifier_entry '{entry}' does not match any "
                "Input name prefix in scripts/verify_braunschweig_inputs.py")
