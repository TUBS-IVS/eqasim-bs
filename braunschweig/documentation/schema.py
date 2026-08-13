"""Strict schemas for the model-registry records (features, stages, data, runs).

One YAML file per record under ``docs/registry/{features,stages,data}/`` and
``docs/runs/``. Loading is strict on purpose (generalized from the
feature-readiness register, branch ``feature/readiness-register``): an unknown
key, a missing required key, or an invalid enum value raises immediately
(CLAUDE.md "Error handling": fail early on invalid input), because a typo in a
declaration key would otherwise silently disable exactly the check it names.

Structural validation only -- whether declared POINTERS resolve (code paths,
tests, flags, ADRs, run ids, DAG reachability) is the job of
``braunschweig.documentation.checks``.

This module must stay import-light (stdlib + PyYAML callers only): the
documentation checker runs in CI without the scientific stack.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Controlled vocabularies (docs/DOCUMENTATION_GOVERNANCE.md documents them)
# --------------------------------------------------------------------------- #

#: Model areas: stage ``layer`` / feature ``area`` / status-view grouping.
MODEL_AREAS = (
    "population", "attributes", "behavior", "fleet", "home", "work", "education",
    "secondary", "cordon", "freight", "matsim", "analysis", "validation",
    "infrastructure", "spatial",
)

#: Stage lineage relative to the eqasim-bavaria fork point (ADR-0000).
LINEAGE_TYPES = (
    "inherited", "configured", "extended", "overridden", "braunschweig_new",
    "upstream_port", "retired",
)

#: Implementation lifecycle of a feature (NOT the same as production state).
LIFECYCLES = ("active", "supported", "experimental", "parked", "retired")

#: Per-pipeline applicability. ``active`` = executed when that pipeline runs with
#: the canonical config; ``supported`` = wired and usable but not the configured
#: default; ``inactive`` = wired into that pipeline but disabled there;
#: ``not_used`` = the pipeline never reaches this feature/stage at all.
PIPELINE_APPLICABILITY = ("active", "supported", "inactive", "not_used")

#: The three population workflows (``braunschweig.population.method``); every
#: feature/stage record states its applicability for each one explicitly.
PIPELINES = ("popsim_mid", "popsim_open", "simple_ipf_open")

#: Provenance class of a validation reference (readiness-register semantics).
REFERENCE_KINDS = ("committed", "assumption", "none")

VALID_BYTE_IDENTITY = ("true", "false", "not_applicable")

#: Honest validation states. ``behaviourally_validated`` requires an observed
#: behavioural reference AND a recorded run; with mode choice OFF in every
#: committed config nothing currently qualifies (convergence is not validation).
VALIDATION_STATES = (
    "unvalidated", "measured_vs_reference", "behaviourally_validated",
    "not_applicable",
)

#: Dataset roles in the model (a dataset may hold several).
DATA_ROLES = (
    "donor", "control", "calibration_target", "validation_reference", "network",
    "spatial_input", "supply_input", "assumption_basis", "derived_input",
    "reference_table",
)

#: How a dataset is obtained.
ACQUISITION_METHODS = (
    "auto_script", "manual_download", "scrape", "restricted_delivery", "derived",
    "committed",
)

REQUIREMENT_LEVELS = ("required", "optional", "not_needed")

REDISTRIBUTION = ("allowed", "restricted", "forbidden")

#: Run-manifest classifications (a run may carry several).
RUN_CLASSIFICATIONS = (
    "smoke", "wiring_proof", "ab_test", "calibration", "validation",
    "production_candidate", "production",
)

RUN_EXECUTION_STATES = ("completed", "partial", "killed", "running", "unknown")


class SchemaError(ValueError):
    """A record is structurally invalid (missing/unknown key, bad enum value)."""


# --------------------------------------------------------------------------- #
# Small validation helpers
# --------------------------------------------------------------------------- #

def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _check_keys(doc: Mapping[str, Any], required: Sequence[str],
                optional: Sequence[str], where: str) -> None:
    unknown = set(doc) - set(required) - set(optional)
    if unknown:
        raise SchemaError(
            f"{where}: unknown key(s) {sorted(unknown)}; allowed: "
            f"{sorted(set(required) | set(optional))}")
    missing = [key for key in required if key not in doc]
    if missing:
        raise SchemaError(f"{where}: missing required key(s) {missing}")


def _check_enum(value: Any, allowed: Sequence[str], where: str) -> str:
    value = str(value)
    if value not in allowed:
        raise SchemaError(f"{where}: '{value}' is not one of {list(allowed)}")
    return value


def _check_str_list(value: Any, where: str, allow_empty: bool = True) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SchemaError(f"{where}: must be a list of strings")
    if not allow_empty and not value:
        raise SchemaError(f"{where}: must be a non-empty list")


def _check_filename(record_id: str, source_file: str, id_key: str) -> None:
    expected = f"{record_id}.yml"
    if os.path.basename(source_file) != expected:
        raise SchemaError(
            f"{source_file}: '{id_key}' is '{record_id}', so the file must be named "
            f"'{expected}' (keeps ids and files in one-to-one correspondence)")


def _check_pipelines(value: Any, where: str) -> None:
    block = _require_mapping(value, where)
    _check_keys(block, required=list(PIPELINES), optional=(), where=where)
    for pipeline in PIPELINES:
        _check_enum(block[pipeline], PIPELINE_APPLICABILITY, f"{where}.{pipeline}")


# --------------------------------------------------------------------------- #
# Feature records
# --------------------------------------------------------------------------- #

_FEATURE_REQUIRED = ("feature", "title", "area", "description", "stages",
                     "pipelines", "lifecycle", "production", "code_paths",
                     "evidence", "validation")
_FEATURE_OPTIONAL = ("introduced", "detail_doc", "assessment", "notes")
_EVIDENCE_REQUIRED = ("tests", "off_path_byte_identical", "fallback_rate", "reference")


def parse_feature(doc: Any, source_file: str) -> dict:
    """Validate one feature declaration; raises ``SchemaError`` naming file and key."""
    doc = _require_mapping(doc, source_file)
    _check_keys(doc, _FEATURE_REQUIRED, _FEATURE_OPTIONAL, source_file)

    feature = str(doc["feature"])
    _check_filename(feature, source_file, "feature")
    _check_enum(doc["area"], MODEL_AREAS, f"{source_file}: area")
    _check_enum(doc["lifecycle"], LIFECYCLES, f"{source_file}: lifecycle")
    _check_str_list(doc["stages"], f"{source_file}: stages")
    _check_pipelines(doc["pipelines"], f"{source_file}: pipelines")
    _check_str_list(doc["code_paths"], f"{source_file}: code_paths", allow_empty=False)

    production = _require_mapping(doc["production"], f"{source_file}: production")
    _check_keys(production, ("enabled",), ("flags",), f"{source_file}: production")
    if not isinstance(production["enabled"], bool):
        raise SchemaError(f"{source_file}: production.enabled must be a boolean")
    if "flags" in production:
        _check_str_list(production["flags"], f"{source_file}: production.flags")

    if "introduced" in doc and doc["introduced"] is not None:
        introduced = _require_mapping(doc["introduced"], f"{source_file}: introduced")
        _check_keys(introduced, (), ("issue", "pr", "adr"), f"{source_file}: introduced")

    evidence = _require_mapping(doc["evidence"], f"{source_file}: evidence")
    missing = [key for key in _EVIDENCE_REQUIRED if key not in evidence]
    if missing:
        raise SchemaError(f"{source_file}: evidence is missing {missing}")
    unknown = set(evidence) - set(_EVIDENCE_REQUIRED)
    if unknown:
        raise SchemaError(f"{source_file}: evidence has unknown key(s) {sorted(unknown)}")
    _check_str_list(evidence["tests"], f"{source_file}: evidence.tests")

    byte_identity = _require_mapping(
        evidence["off_path_byte_identical"], f"{source_file}: evidence.off_path_byte_identical")
    claimed = str(byte_identity.get("claimed")).strip().lower()
    if claimed not in VALID_BYTE_IDENTITY:
        raise SchemaError(
            f"{source_file}: evidence.off_path_byte_identical.claimed must be one of "
            f"{list(VALID_BYTE_IDENTITY)}, got '{byte_identity.get('claimed')}'")
    if claimed == "true" and not byte_identity.get("test"):
        raise SchemaError(
            f"{source_file}: off_path_byte_identical.claimed is true but no 'test' is named "
            "-- an unproven byte-identity claim is exactly what this registry exists to prevent")

    fallback = _require_mapping(evidence["fallback_rate"],
                                f"{source_file}: evidence.fallback_rate")
    if bool(fallback.get("instrumented")) and not str(fallback.get("log_marker") or "").strip():
        raise SchemaError(
            f"{source_file}: fallback_rate.instrumented is true but no 'log_marker' is "
            "declared -- the marker is what makes the rate observable (CLAUDE.md rule)")

    reference = _require_mapping(evidence["reference"], f"{source_file}: evidence.reference")
    kind = _check_enum(reference.get("kind"), REFERENCE_KINDS,
                       f"{source_file}: evidence.reference.kind")
    if kind == "committed" and not reference.get("path"):
        raise SchemaError(f"{source_file}: reference.kind is 'committed' but no 'path' is given")
    if kind in ("assumption", "none") and not reference.get("note"):
        raise SchemaError(
            f"{source_file}: evidence.reference.kind is '{kind}' and therefore requires a "
            "'note' stating the reasoning (CLAUDE.md: an unsourced number must be labelled "
            "an ASSUMPTION)")

    validation = _require_mapping(doc["validation"], f"{source_file}: validation")
    _check_keys(validation, ("state", "runs"), ("metric", "note"),
                f"{source_file}: validation")
    state = _check_enum(validation["state"], VALIDATION_STATES,
                        f"{source_file}: validation.state")
    _check_str_list(validation["runs"], f"{source_file}: validation.runs")
    if state in ("measured_vs_reference", "behaviourally_validated") and not validation["runs"]:
        raise SchemaError(
            f"{source_file}: validation.state '{state}' requires at least one run manifest "
            "id in validation.runs (no validation claim without a recorded run)")

    if "assessment" in doc and doc["assessment"] is not None:
        assessment = _require_mapping(doc["assessment"], f"{source_file}: assessment")
        if str(assessment.get("status", "")).strip() == "pending" and not assessment.get("pending_reason"):
            raise SchemaError(
                f"{source_file}: assessment.status is 'pending' and therefore requires a "
                "'pending_reason'")

    record = dict(doc)
    record["_source"] = source_file
    return record


# --------------------------------------------------------------------------- #
# Stage records
# --------------------------------------------------------------------------- #

_STAGE_REQUIRED = ("stage", "title", "layer", "description", "lineage", "code",
                   "pipelines", "production")
_STAGE_OPTIONAL = ("inputs", "features", "decisions", "notes", "resolves_to")


def parse_stage(doc: Any, source_file: str) -> dict:
    """Validate one stage record; raises ``SchemaError`` naming file and key."""
    doc = _require_mapping(doc, source_file)
    _check_keys(doc, _STAGE_REQUIRED, _STAGE_OPTIONAL, source_file)

    stage = str(doc["stage"])
    _check_filename(stage, source_file, "stage")
    _check_enum(doc["layer"], MODEL_AREAS, f"{source_file}: layer")
    _check_str_list(doc["code"], f"{source_file}: code")
    _check_pipelines(doc["pipelines"], f"{source_file}: pipelines")
    if not isinstance(doc["production"], bool):
        raise SchemaError(f"{source_file}: production must be a boolean")

    lineage = _require_mapping(doc["lineage"], f"{source_file}: lineage")
    _check_keys(lineage, ("type",), ("upstream", "notes"), f"{source_file}: lineage")
    lineage_type = _check_enum(lineage["type"], LINEAGE_TYPES,
                               f"{source_file}: lineage.type")
    if lineage_type == "overridden" and not lineage.get("upstream"):
        raise SchemaError(
            f"{source_file}: lineage.type 'overridden' requires 'upstream' naming the "
            "upstream stage this one replaces (usually via the config alias table)")
    if lineage_type == "upstream_port" and not lineage.get("notes"):
        raise SchemaError(
            f"{source_file}: lineage.type 'upstream_port' requires 'notes' naming the "
            "source project/commit the mechanism was ported from")

    for key in ("inputs", "features", "decisions"):
        if key in doc and doc[key] is not None:
            _check_str_list(doc[key], f"{source_file}: {key}")

    record = dict(doc)
    record["_source"] = source_file
    return record


# --------------------------------------------------------------------------- #
# Data records
# --------------------------------------------------------------------------- #

_DATASET_REQUIRED = ("dataset", "title", "provider", "roles", "acquisition",
                     "storage", "licensing", "requirements")
_DATASET_OPTIONAL = ("inventory_id", "vintage", "geography", "crs", "used_by",
                     "verification", "limitations", "notes")


def parse_dataset(doc: Any, source_file: str) -> dict:
    """Validate one dataset record; raises ``SchemaError`` naming file and key."""
    doc = _require_mapping(doc, source_file)
    _check_keys(doc, _DATASET_REQUIRED, _DATASET_OPTIONAL, source_file)

    dataset = str(doc["dataset"])
    _check_filename(dataset, source_file, "dataset")

    roles = doc["roles"]
    _check_str_list(roles, f"{source_file}: roles", allow_empty=False)
    for role in roles:
        _check_enum(role, DATA_ROLES, f"{source_file}: roles")

    if "used_by" in doc and doc["used_by"] is not None:
        _check_str_list(doc["used_by"], f"{source_file}: used_by")

    acquisition = _require_mapping(doc["acquisition"], f"{source_file}: acquisition")
    _check_keys(acquisition, ("method",), ("source", "script", "notes"),
                f"{source_file}: acquisition")
    method = _check_enum(acquisition["method"], ACQUISITION_METHODS,
                         f"{source_file}: acquisition.method")
    if method == "auto_script" and not acquisition.get("script"):
        raise SchemaError(
            f"{source_file}: acquisition.method 'auto_script' requires a 'script' path")

    storage = _require_mapping(doc["storage"], f"{source_file}: storage")
    _check_keys(storage, ("expected_path",), ("committed", "local_only", "notes"),
                f"{source_file}: storage")
    if not str(storage.get("expected_path") or "").strip():
        raise SchemaError(f"{source_file}: storage.expected_path must be a non-empty path")

    licensing = _require_mapping(doc["licensing"], f"{source_file}: licensing")
    _check_keys(licensing, ("license", "restricted", "redistribution"), ("notes",),
                f"{source_file}: licensing")
    if not isinstance(licensing["restricted"], bool):
        raise SchemaError(f"{source_file}: licensing.restricted must be a boolean")
    redistribution = _check_enum(licensing["redistribution"], REDISTRIBUTION,
                                 f"{source_file}: licensing.redistribution")
    if licensing["restricted"] and redistribution == "allowed":
        raise SchemaError(
            f"{source_file}: licensing.restricted is true, so redistribution cannot be "
            "'allowed' (a restricted dataset must not be redistributable)")

    requirements = _require_mapping(doc["requirements"], f"{source_file}: requirements")
    _check_keys(requirements, ("synthesis", "matsim", "production"), (),
                f"{source_file}: requirements")
    for scope in ("synthesis", "matsim", "production"):
        _check_enum(requirements[scope], REQUIREMENT_LEVELS,
                    f"{source_file}: requirements.{scope}")

    if "verification" in doc and doc["verification"] is not None:
        verification = _require_mapping(doc["verification"], f"{source_file}: verification")
        _check_keys(verification, (), ("verifier_entry", "script", "notes"),
                    f"{source_file}: verification")

    record = dict(doc)
    record["_source"] = source_file
    return record


# --------------------------------------------------------------------------- #
# Run manifests
# --------------------------------------------------------------------------- #

_MANIFEST_REQUIRED = ("id", "date", "repository", "configuration", "sampling",
                      "pipeline", "classification", "status", "source")
_MANIFEST_OPTIONAL = ("environment", "validation", "features", "artifacts",
                      "issues", "decisions", "notes")


def parse_manifest(doc: Any, source_file: str) -> dict:
    """Validate one run manifest; raises ``SchemaError`` naming file and key.

    Values that are not recoverable from a committed source are the literal string
    ``unknown`` (RUNS.md rule carried over: no invented values).
    """
    doc = _require_mapping(doc, source_file)
    _check_keys(doc, _MANIFEST_REQUIRED, _MANIFEST_OPTIONAL, source_file)

    run_id = str(doc["id"])
    _check_filename(run_id, source_file, "id")

    repository = _require_mapping(doc["repository"], f"{source_file}: repository")
    _check_keys(repository, (), ("commit", "branch", "notes"), f"{source_file}: repository")

    configuration = _require_mapping(doc["configuration"], f"{source_file}: configuration")
    _check_keys(configuration, (), ("base", "overlays", "config", "notes"),
                f"{source_file}: configuration")

    sampling = _require_mapping(doc["sampling"], f"{source_file}: sampling")
    _check_keys(sampling, (), ("rate", "scope", "notes"), f"{source_file}: sampling")

    pipeline = _require_mapping(doc["pipeline"], f"{source_file}: pipeline")
    _check_keys(pipeline, (), ("targets", "notes"), f"{source_file}: pipeline")
    if "targets" in pipeline and pipeline["targets"] is not None:
        _check_str_list(pipeline["targets"], f"{source_file}: pipeline.targets")

    classification = doc["classification"]
    _check_str_list(classification, f"{source_file}: classification", allow_empty=False)
    for entry in classification:
        _check_enum(entry, RUN_CLASSIFICATIONS, f"{source_file}: classification")

    status = _require_mapping(doc["status"], f"{source_file}: status")
    _check_keys(status, ("execution",), ("notes",), f"{source_file}: status")
    _check_enum(status["execution"], RUN_EXECUTION_STATES,
                f"{source_file}: status.execution")

    if "validation" in doc and doc["validation"] is not None:
        if not isinstance(doc["validation"], list):
            raise SchemaError(f"{source_file}: validation must be a list")
        for index, entry in enumerate(doc["validation"]):
            entry = _require_mapping(entry, f"{source_file}: validation[{index}]")
            _check_keys(entry, (), ("metric", "reference", "result", "artifact", "notes"),
                        f"{source_file}: validation[{index}]")

    for key in ("artifacts", "issues", "decisions"):
        if key in doc and doc[key] is not None:
            _check_str_list(doc[key], f"{source_file}: {key}")

    record = dict(doc)
    record["_source"] = source_file
    return record
