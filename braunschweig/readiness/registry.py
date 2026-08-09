"""Load and structurally validate the feature-readiness declarations.

One YAML file per feature under ``docs/readiness/``. Loading is strict on purpose:
an unknown key or a missing required key raises immediately (CLAUDE.md "Error
handling": fail early on invalid input) rather than being silently ignored, because
a typo in an evidence key would otherwise disable exactly the check it names.

Structural validation only -- whether the declared pointers RESOLVE is the job of
``braunschweig.readiness.checks``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import yaml

logger = logging.getLogger("braunschweig")

#: Directory (repo-relative) holding one YAML declaration per feature.
REGISTRY_DIRECTORY = os.path.join("docs", "readiness")

#: Lifecycle state of a feature. ``on``/``off_by_default`` mirror the STATUS matrix
#: legend; ``parked`` is a measured-and-deliberately-disabled feature (e.g. ADR-0065);
#: ``assumption`` is a feature shipped without any committed reference (e.g. freight).
VALID_STATUS = ("on", "off_by_default", "parked", "assumption")

#: Provenance class of the value a feature is validated against. ``committed`` must
#: point at a git-tracked file; ``assumption`` must carry a note explaining the
#: reasoning; ``none`` means no reference exists yet and the feature is unvalidated.
VALID_REFERENCE_KIND = ("committed", "assumption", "none")

VALID_BYTE_IDENTITY = ("true", "false", "not_applicable")

_TOP_LEVEL_REQUIRED = ("feature", "title", "status", "code_paths", "evidence")
_TOP_LEVEL_OPTIONAL = (
    "flag", "adr", "detail_doc", "status_matrix_row", "issue", "assessment",
)
_EVIDENCE_REQUIRED = ("tests", "off_path_byte_identical", "fallback_rate", "reference", "kpi")


class RegistryError(ValueError):
    """A declaration is structurally invalid (missing/unknown key, bad enum value)."""


@dataclass(frozen=True)
class FeatureDeclaration:
    """One feature's declared evidence. Values are pointers, not measurements."""

    feature: str
    title: str
    status: str
    code_paths: Sequence[str]
    evidence: Mapping[str, Any]
    source_file: str
    flag: str | None = None
    adr: str | None = None
    detail_doc: str | None = None
    status_matrix_row: str | None = None
    issue: str | None = None
    assessment: Mapping[str, Any] | None = field(default=None)

    @property
    def tests(self) -> Sequence[str]:
        return tuple(self.evidence.get("tests") or ())

    @property
    def reference(self) -> Mapping[str, Any]:
        return self.evidence.get("reference") or {}

    @property
    def kpi(self) -> Mapping[str, Any]:
        return self.evidence.get("kpi") or {}

    @property
    def fallback_rate(self) -> Mapping[str, Any]:
        return self.evidence.get("fallback_rate") or {}

    @property
    def off_path_byte_identical(self) -> Mapping[str, Any]:
        return self.evidence.get("off_path_byte_identical") or {}

    @property
    def assessment_is_pending(self) -> bool:
        """True when an assessment block exists but the expert verdict is not written yet."""
        return bool(self.assessment) and str(self.assessment.get("status", "")).strip() == "pending"


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def parse_declaration(doc: Any, source_file: str) -> FeatureDeclaration:
    """Validate one loaded YAML document and return the declaration.

    Raises ``RegistryError`` naming the offending file and key. Every rejection is a
    hard error: a declaration that cannot be parsed must not be silently skipped, or
    the register would under-report exactly the features whose files are broken.
    """
    doc = _require_mapping(doc, source_file)

    unknown = set(doc) - set(_TOP_LEVEL_REQUIRED) - set(_TOP_LEVEL_OPTIONAL)
    if unknown:
        raise RegistryError(
            f"{source_file}: unknown top-level key(s) {sorted(unknown)}; allowed: "
            f"{sorted(set(_TOP_LEVEL_REQUIRED) | set(_TOP_LEVEL_OPTIONAL))}")
    missing = [k for k in _TOP_LEVEL_REQUIRED if k not in doc]
    if missing:
        raise RegistryError(f"{source_file}: missing required key(s) {missing}")

    status = str(doc["status"])
    if status not in VALID_STATUS:
        raise RegistryError(
            f"{source_file}: status '{status}' is not one of {list(VALID_STATUS)}")

    code_paths = doc["code_paths"]
    if not isinstance(code_paths, list) or not code_paths:
        raise RegistryError(f"{source_file}: 'code_paths' must be a non-empty list")

    evidence = _require_mapping(doc["evidence"], f"{source_file}: evidence")
    missing_evidence = [k for k in _EVIDENCE_REQUIRED if k not in evidence]
    if missing_evidence:
        raise RegistryError(f"{source_file}: evidence is missing {missing_evidence}")

    tests = evidence["tests"]
    if not isinstance(tests, list):
        raise RegistryError(f"{source_file}: evidence.tests must be a list (may be empty)")

    byte_identity = _require_mapping(
        evidence["off_path_byte_identical"], f"{source_file}: evidence.off_path_byte_identical")
    claimed = str(byte_identity.get("claimed")).strip().lower()
    if claimed not in VALID_BYTE_IDENTITY:
        raise RegistryError(
            f"{source_file}: evidence.off_path_byte_identical.claimed must be one of "
            f"{list(VALID_BYTE_IDENTITY)}, got '{byte_identity.get('claimed')}'")
    if claimed == "true" and not byte_identity.get("test"):
        raise RegistryError(
            f"{source_file}: off_path_byte_identical.claimed is true but no 'test' is named "
            "-- an unproven byte-identity claim is exactly what this register exists to prevent")

    reference = _require_mapping(evidence["reference"], f"{source_file}: evidence.reference")
    kind = str(reference.get("kind"))
    if kind not in VALID_REFERENCE_KIND:
        raise RegistryError(
            f"{source_file}: evidence.reference.kind must be one of {list(VALID_REFERENCE_KIND)}, "
            f"got '{kind}'")
    if kind == "committed" and not reference.get("path"):
        raise RegistryError(f"{source_file}: reference.kind is 'committed' but no 'path' is given")
    if kind in ("assumption", "none") and not reference.get("note"):
        raise RegistryError(
            f"{source_file}: reference.kind is '{kind}' and therefore requires a 'note' stating "
            "the reasoning (CLAUDE.md: an unsourced number must be labelled an ASSUMPTION)")

    _require_mapping(evidence["kpi"], f"{source_file}: evidence.kpi")
    _require_mapping(evidence["fallback_rate"], f"{source_file}: evidence.fallback_rate")

    assessment = doc.get("assessment")
    if assessment is not None:
        assessment = _require_mapping(assessment, f"{source_file}: assessment")
        if str(assessment.get("status", "")).strip() == "pending" and not assessment.get("pending_reason"):
            raise RegistryError(
                f"{source_file}: assessment.status is 'pending' and therefore requires a "
                "'pending_reason'")

    feature = str(doc["feature"])
    expected_name = f"{feature}.yml"
    if os.path.basename(source_file) != expected_name:
        raise RegistryError(
            f"{source_file}: 'feature' is '{feature}', so the file must be named "
            f"'{expected_name}' (keeps keys and files in one-to-one correspondence)")

    return FeatureDeclaration(
        feature=feature,
        title=str(doc["title"]),
        status=status,
        code_paths=tuple(str(p) for p in code_paths),
        evidence=evidence,
        source_file=source_file,
        flag=doc.get("flag"),
        adr=doc.get("adr"),
        detail_doc=doc.get("detail_doc"),
        status_matrix_row=doc.get("status_matrix_row"),
        issue=doc.get("issue"),
        assessment=assessment,
    )


def load_registry(repo_root: str, registry_directory: str = REGISTRY_DIRECTORY):
    """Load every ``*.yml`` declaration, sorted by feature key.

    Raises ``FileNotFoundError`` when the registry directory is absent and
    ``RegistryError`` on a duplicate feature key or any structural violation.
    """
    directory = os.path.join(repo_root, registry_directory)
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"readiness registry directory not found: {directory}")

    declarations = []
    seen = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".yml"):
            continue
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        declaration = parse_declaration(doc, os.path.join(registry_directory, name))
        if declaration.feature in seen:
            raise RegistryError(
                f"duplicate feature key '{declaration.feature}' in {path} and {seen[declaration.feature]}")
        seen[declaration.feature] = path
        declarations.append(declaration)

    logger.info("[readiness] loaded %d feature declaration(s) from %s",
                len(declarations), directory)
    return declarations
