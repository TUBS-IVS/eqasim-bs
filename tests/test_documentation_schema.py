"""Tests for the model-registry schema layer (braunschweig.documentation.schema).

Two layers, mirroring the readiness-register test design it generalizes:

1. Unit tests over synthetic records pin the parser's strictness -- an unknown key,
   a missing note on an assumption, or an unproven byte-identity claim must raise
   rather than pass silently (CLAUDE.md "Error handling": fail early).
2. ``tests/test_documentation_registry.py`` runs the same parsers over the REAL
   registries so drift breaks the suite.
"""
from __future__ import annotations

import pytest

from braunschweig.documentation import schema


# --------------------------------------------------------------------------- #
# synthetic minimal records
# --------------------------------------------------------------------------- #

def minimal_feature(**overrides):
    doc = {
        "feature": "demo_feature",
        "title": "Demo feature",
        "area": "attributes",
        "description": "A synthetic feature used by the schema tests.",
        "stages": ["braunschweig.demo.stage"],
        "pipelines": {"popsim_mid": "active", "popsim_open": "inactive",
                      "simple_ipf_open": "inactive"},
        "lifecycle": "active",
        "production": {"enabled": True, "flags": ["demo_flag"]},
        "introduced": {"issue": None, "pr": None, "adr": None},
        "detail_doc": None,
        "code_paths": ["braunschweig/documentation/schema.py"],
        "evidence": {
            "tests": ["tests/test_documentation_schema.py"],
            "off_path_byte_identical": {"claimed": "not_applicable"},
            "fallback_rate": {"instrumented": False},
            "reference": {"kind": "none", "note": "synthetic test record"},
        },
        "validation": {"state": "unvalidated", "runs": [], "metric": None},
        "assessment": {"status": "pending", "by": None, "date": None,
                       "pending_reason": "synthetic", "source": None},
    }
    doc.update(overrides)
    return doc


def minimal_stage(**overrides):
    doc = {
        "stage": "braunschweig.demo.stage",
        "title": "Demo stage",
        "layer": "population",
        "description": "A synthetic stage used by the schema tests.",
        "lineage": {"type": "braunschweig_new", "upstream": None, "notes": None},
        "code": ["braunschweig/documentation/schema.py"],
        "pipelines": {"popsim_mid": "active", "popsim_open": "active",
                      "simple_ipf_open": "not_used"},
        "production": True,
        "inputs": [],
        "features": [],
        "decisions": [],
    }
    doc.update(overrides)
    return doc


def minimal_dataset(**overrides):
    doc = {
        "dataset": "demo_dataset",
        "title": "Demo dataset",
        "inventory_id": "Z1",
        "provider": "Synthetic provider",
        "vintage": "2026",
        "geography": "test",
        "crs": None,
        "roles": ["control"],
        "used_by": ["braunschweig.demo.stage"],
        "acquisition": {"method": "manual_download",
                        "source": "https://example.org", "script": None},
        "storage": {"expected_path": "eqasim-data/data/demo.csv",
                    "committed": False, "local_only": True},
        "verification": {"verifier_entry": None},
        "requirements": {"synthesis": "required", "matsim": "not_needed",
                         "production": "required"},
        "licensing": {"license": "dl-de/by-2-0", "restricted": False,
                      "redistribution": "allowed"},
        "limitations": None,
        "notes": None,
    }
    doc.update(overrides)
    return doc


def minimal_manifest(**overrides):
    doc = {
        "id": "demo-run-2026-01-01",
        "date": "2026-01-01",
        "repository": {"commit": "unknown", "branch": "main"},
        "configuration": {"base": "configs/base_bs.yml",
                          "overlays": ["configs/overlays/test.yml"], "notes": None},
        "sampling": {"rate": "1%", "scope": "ZGB-8"},
        "pipeline": {"targets": ["synthesis.output"]},
        "classification": ["smoke"],
        "status": {"execution": "completed"},
        "environment": "local",
        "validation": [],
        "features": {"note": None},
        "artifacts": [],
        "issues": [],
        "decisions": [],
        "source": "synthetic",
    }
    doc.update(overrides)
    return doc


# --------------------------------------------------------------------------- #
# feature records
# --------------------------------------------------------------------------- #

def test_feature_minimal_round_trips():
    record = schema.parse_feature(minimal_feature(), "docs/registry/features/demo_feature.yml")
    assert record["feature"] == "demo_feature"
    assert record["production"]["enabled"] is True


def test_feature_unknown_top_level_key_is_rejected():
    with pytest.raises(schema.SchemaError, match="unknown key"):
        schema.parse_feature(minimal_feature(typo_key=1),
                             "docs/registry/features/demo_feature.yml")


def test_feature_file_name_must_match_id():
    with pytest.raises(schema.SchemaError, match="must be named"):
        schema.parse_feature(minimal_feature(), "docs/registry/features/other.yml")


def test_feature_invalid_lifecycle_is_rejected():
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_feature(minimal_feature(lifecycle="probably_fine"),
                             "docs/registry/features/demo_feature.yml")


def test_feature_pipelines_must_name_all_three_workflows():
    doc = minimal_feature(pipelines={"popsim_mid": "active"})
    with pytest.raises(schema.SchemaError, match="missing required key"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_byte_identity_claim_without_test_is_rejected():
    doc = minimal_feature()
    doc["evidence"]["off_path_byte_identical"] = {"claimed": "true"}
    with pytest.raises(schema.SchemaError, match="no 'test' is named"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_assumption_reference_requires_note():
    doc = minimal_feature()
    doc["evidence"]["reference"] = {"kind": "assumption"}
    with pytest.raises(schema.SchemaError, match="requires a 'note'"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_committed_reference_requires_path():
    doc = minimal_feature()
    doc["evidence"]["reference"] = {"kind": "committed"}
    with pytest.raises(schema.SchemaError, match="no 'path'"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_validation_claim_requires_runs():
    doc = minimal_feature(validation={"state": "measured_vs_reference",
                                      "runs": [], "metric": "demo"})
    with pytest.raises(schema.SchemaError, match="requires at least one run"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_instrumented_fallback_requires_log_marker():
    doc = minimal_feature()
    doc["evidence"]["fallback_rate"] = {"instrumented": True}
    with pytest.raises(schema.SchemaError, match="log_marker"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_pending_assessment_requires_reason():
    doc = minimal_feature(assessment={"status": "pending", "by": None, "date": None,
                                      "source": None})
    with pytest.raises(schema.SchemaError, match="pending_reason"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_invalid_assessment_status_is_rejected():
    doc = minimal_feature(assessment={"status": "not_a_real_status", "by": None,
                                      "date": None, "pending_reason": None, "source": None})
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")


def test_feature_assessed_status_round_trips():
    # Task 14 (#358): "assessed" is a valid assessment.status value, added alongside
    # "pending" and "reviewed" (the two already in use across the registry).
    doc = minimal_feature(assessment={"status": "assessed", "by": "Test", "date": "2026-09-04",
                                      "source": "ADR-0103"})
    record = schema.parse_feature(doc, "docs/registry/features/demo_feature.yml")
    assert record["assessment"]["status"] == "assessed"


# --------------------------------------------------------------------------- #
# stage records
# --------------------------------------------------------------------------- #

def test_stage_minimal_round_trips():
    record = schema.parse_stage(minimal_stage(),
                                "docs/registry/stages/braunschweig.demo.stage.yml")
    assert record["stage"] == "braunschweig.demo.stage"
    assert record["lineage"]["type"] == "braunschweig_new"


def test_stage_overridden_lineage_requires_upstream():
    doc = minimal_stage(lineage={"type": "overridden", "upstream": None, "notes": None})
    with pytest.raises(schema.SchemaError, match="upstream"):
        schema.parse_stage(doc, "docs/registry/stages/braunschweig.demo.stage.yml")


def test_stage_invalid_lineage_type_is_rejected():
    doc = minimal_stage(lineage={"type": "copied", "upstream": None, "notes": None})
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_stage(doc, "docs/registry/stages/braunschweig.demo.stage.yml")


def test_stage_invalid_layer_is_rejected():
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_stage(minimal_stage(layer="misc"),
                           "docs/registry/stages/braunschweig.demo.stage.yml")


# --------------------------------------------------------------------------- #
# data records
# --------------------------------------------------------------------------- #

def test_dataset_minimal_round_trips():
    record = schema.parse_dataset(minimal_dataset(), "docs/registry/data/demo_dataset.yml")
    assert record["dataset"] == "demo_dataset"
    assert record["roles"] == ["control"]


def test_dataset_invalid_acquisition_method_is_rejected():
    doc = minimal_dataset()
    doc["acquisition"] = {"method": "magic", "source": "x", "script": None}
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_dataset(doc, "docs/registry/data/demo_dataset.yml")


def test_dataset_auto_script_requires_script():
    doc = minimal_dataset()
    doc["acquisition"] = {"method": "auto_script", "source": "x", "script": None}
    with pytest.raises(schema.SchemaError, match="requires a 'script'"):
        schema.parse_dataset(doc, "docs/registry/data/demo_dataset.yml")


def test_dataset_restricted_licence_cannot_allow_redistribution():
    doc = minimal_dataset()
    doc["licensing"] = {"license": "proprietary", "restricted": True,
                        "redistribution": "allowed"}
    with pytest.raises(schema.SchemaError, match="restricted"):
        schema.parse_dataset(doc, "docs/registry/data/demo_dataset.yml")


def test_dataset_unknown_role_is_rejected():
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_dataset(minimal_dataset(roles=["fancy"]),
                             "docs/registry/data/demo_dataset.yml")


def test_dataset_empty_roles_is_rejected():
    with pytest.raises(schema.SchemaError, match="non-empty"):
        schema.parse_dataset(minimal_dataset(roles=[]),
                             "docs/registry/data/demo_dataset.yml")


# --------------------------------------------------------------------------- #
# run manifests
# --------------------------------------------------------------------------- #

def test_manifest_minimal_round_trips():
    record = schema.parse_manifest(minimal_manifest(), "docs/runs/demo-run-2026-01-01.yml")
    assert record["id"] == "demo-run-2026-01-01"
    assert record["classification"] == ["smoke"]


def test_manifest_invalid_classification_is_rejected():
    with pytest.raises(schema.SchemaError, match="not one of"):
        schema.parse_manifest(minimal_manifest(classification=["heroic"]),
                              "docs/runs/demo-run-2026-01-01.yml")


def test_manifest_unknown_key_is_rejected():
    with pytest.raises(schema.SchemaError, match="unknown key"):
        schema.parse_manifest(minimal_manifest(surprise=1),
                              "docs/runs/demo-run-2026-01-01.yml")


def test_manifest_file_name_must_match_id():
    with pytest.raises(schema.SchemaError, match="must be named"):
        schema.parse_manifest(minimal_manifest(), "docs/runs/wrong.yml")


# --------------------------------------------------------------------------- #
# directory loaders
# --------------------------------------------------------------------------- #

def test_loader_rejects_duplicate_ids(tmp_path):
    import yaml
    from braunschweig.documentation import registries
    directory = tmp_path / "features"
    directory.mkdir()
    doc_a = minimal_feature()
    doc_b = minimal_feature()  # same feature id under a second file name
    (directory / "demo_feature.yml").write_text(yaml.safe_dump(doc_a), encoding="utf-8")
    (directory / "demo_feature2.yml").write_text(yaml.safe_dump(doc_b), encoding="utf-8")
    with pytest.raises(schema.SchemaError):
        registries.load_features(str(tmp_path), directory="features")


def test_loader_reads_valid_directory(tmp_path):
    import yaml
    from braunschweig.documentation import registries
    directory = tmp_path / "features"
    directory.mkdir()
    (directory / "demo_feature.yml").write_text(
        yaml.safe_dump(minimal_feature()), encoding="utf-8")
    records = registries.load_features(str(tmp_path), directory="features")
    assert len(records) == 1
    assert records[0]["feature"] == "demo_feature"
