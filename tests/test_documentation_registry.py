"""Guards over the REAL model registries (docs/registry/**, docs/runs/).

The synthetic strictness tests live in test_documentation_schema.py; these run
the same strict parsers over the committed registry content, so a renamed test,
a deleted code path or a stage id that no DAG knows breaks the suite instead of
silently degrading the registry (readiness-register design carried forward).

Deliberately data-independent: everything here resolves against committed files
and the committed DAG snapshots, never against the local eqasim-data tree.
"""
from __future__ import annotations

import functools
import os

from braunschweig.documentation import dag, registries

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Each loader parses every committed record (one to two seconds apiece); the tests
# only read the records, so each registry is parsed once per module.
@functools.cache
def _features():
    return registries.load_features(REPO_ROOT)


@functools.cache
def _stages():
    return registries.load_stages(REPO_ROOT)


@functools.cache
def _data():
    return registries.load_data(REPO_ROOT)


@functools.cache
def _snapshots():
    return dag.load_all_snapshots(REPO_ROOT)


def _dag_union_nodes():
    snapshots = _snapshots()
    nodes = set()
    for snapshot in snapshots.values():
        nodes.update(snapshot["nodes"])
    return nodes


def test_feature_registry_parses_and_is_complete():
    records = _features()
    assert len(records) >= 69, (
        "the feature registry lost records -- 67 migrated readiness declarations "
        "plus the escort and SrV-location-type features are the 2026-08 baseline")


def test_feature_stage_references_resolve_against_the_dag():
    nodes = _dag_union_nodes()
    for record in _features():
        for stage in record["stages"]:
            assert stage in nodes, (
                f"{record['feature']}: stage '{stage}' is not a node of any "
                "committed DAG snapshot (docs/registry/dag/)")


def test_feature_active_pipelines_have_reachable_stages():
    snapshots = _snapshots()
    pipeline_nodes = {
        "popsim_mid": set(snapshots["production"]["nodes"]),
        "popsim_open": set(snapshots["popsim_open"]["nodes"]),
        "simple_ipf_open": set(snapshots["simple_ipf_open"]["nodes"]),
    }
    for record in _features():
        for pipeline, applicability in record["pipelines"].items():
            if applicability == "active" and record["stages"]:
                assert any(stage in pipeline_nodes[pipeline]
                           for stage in record["stages"]), (
                    f"{record['feature']}: declared active under {pipeline} but "
                    "none of its stages is reachable in that pipeline's DAG")


def test_feature_code_paths_and_tests_exist():
    for record in _features():
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
    records = {record["stage"]: record for record in _stages()}
    missing = nodes - set(records)
    assert not missing, f"DAG stages without a registry record: {sorted(missing)}"
    for stage, record in records.items():
        if stage not in nodes:
            assert record["production"] is False and all(
                value == "not_used" for value in record["pipelines"].values()), (
                f"{stage}: not in any DAG snapshot but not declared as parked")


def test_stage_production_flag_matches_the_production_dag():
    production_nodes = set(dag.load_snapshot(REPO_ROOT, "production")["nodes"])
    for record in _stages():
        assert record["production"] == (record["stage"] in production_nodes), (
            f"{record['stage']}: production flag contradicts the production DAG")


def test_stage_feature_references_resolve():
    feature_ids = {record["feature"] for record in _features()}
    for record in _stages():
        for feature in record.get("features") or []:
            assert feature in feature_ids, (
                f"{record['stage']}: unknown feature reference '{feature}'")


def test_stage_code_paths_exist():
    for record in _stages():
        for path in record["code"]:
            assert os.path.exists(os.path.join(REPO_ROOT, path)), (
                f"{record['stage']}: code path missing: {path}")


def test_data_registry_parses_and_covers_every_stage_input():
    datasets = {record["dataset"] for record in _data()}
    assert len(datasets) >= 52
    for record in _stages():
        for dataset in record.get("inputs") or []:
            assert dataset in datasets, (
                f"{record['stage']}: unknown dataset reference '{dataset}'")


def test_data_registry_used_by_references_resolve():
    stage_ids = {record["stage"] for record in _stages()}
    for record in _data():
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
    for record in _data():
        entry = (record.get("verification") or {}).get("verifier_entry")
        if entry:
            assert entry in prefixes, (
                f"{record['dataset']}: verifier_entry '{entry}' does not match any "
                "Input name prefix in scripts/verify_braunschweig_inputs.py")


# ------------------------------------------- the run-manifest template (issue #378, bullet 3)
# docs/runs/TEMPLATE.yml is a blank manifest for a human to copy. Every other *.yml in that
# directory is an executed run, and the loader turns each into a row of docs/generated/RUNS.md
# -- so the template MUST be skipped there ("never invent history": a template is not a run),
# while still being held to the very schema it is a template for, or it silently rots.


def test_the_run_manifest_template_exists_and_is_skipped_by_the_loader():
    template = os.path.join(REPO_ROOT, registries.RUNS_DIRECTORY,
                            registries.MANIFEST_TEMPLATE_FILENAME)
    assert os.path.isfile(template), (
        f"{registries.MANIFEST_TEMPLATE_FILENAME} is missing; the loader skips that name, so a "
        "run manifest must never be given it either")

    manifest_ids = {record["id"] for record in registries.load_manifests(REPO_ROOT)}

    assert "TEMPLATE" not in manifest_ids


def test_the_run_manifest_template_still_satisfies_the_manifest_schema():
    """Skipped by the loader, so nothing else would notice it drifting from the schema."""
    import yaml

    from braunschweig.documentation import schema

    path = os.path.join(REPO_ROOT, registries.RUNS_DIRECTORY,
                        registries.MANIFEST_TEMPLATE_FILENAME)
    with open(path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)

    record = schema.parse_manifest(document, "docs/runs/TEMPLATE.yml")

    assert record["id"] == "TEMPLATE"


def test_the_run_manifest_template_names_the_commute_day_check_1_artifact():
    """Issue #378: commute_day_state_shares.csv must be listed where a run records artifacts."""
    path = os.path.join(REPO_ROOT, registries.RUNS_DIRECTORY,
                        registries.MANIFEST_TEMPLATE_FILENAME)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    assert "commute_day_state_shares.csv" in text
    assert "state_diagnostics.json" in text
