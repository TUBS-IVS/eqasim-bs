"""Tests for the feature-readiness register (braunschweig.readiness).

Two layers:

1. Unit tests over synthetic declarations (tmp_path) pin the parser's strictness --
   an unknown key, a missing note on an assumption, or an unproven byte-identity
   claim must raise rather than pass silently.
2. Repository guards run the deterministic checks over the REAL registry, so a
   renamed test, a deleted reference or an edited STATUS matrix row breaks the suite
   instead of silently degrading the register. Only FAIL-severity findings fail the
   suite; WARN is the honest "declared but unproven" state and must stay reportable.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from braunschweig.readiness import checks, registry

REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _minimal_doc(**overrides):
    doc = {
        "feature": "demo_feature",
        "title": "Demo feature",
        "status": "on",
        "code_paths": ["braunschweig/readiness/registry.py"],
        "evidence": {
            "tests": ["tests/test_readiness_registry.py"],
            "off_path_byte_identical": {"claimed": "not_applicable"},
            "fallback_rate": {"instrumented": False},
            "reference": {"kind": "committed", "path": "CLAUDE.md"},
            "kpi": {"name": "demo", "last_value": None, "measured_on": None},
        },
        "assessment": {"status": "pending", "pending_reason": "demo"},
    }
    doc.update(overrides)
    return doc


# -- parser strictness -----------------------------------------------------------------


def test_parse_minimal_declaration_round_trips():
    declaration = registry.parse_declaration(_minimal_doc(), "docs/readiness/demo_feature.yml")
    assert declaration.feature == "demo_feature"
    assert declaration.status == "on"
    assert declaration.assessment_is_pending is True


def test_unknown_top_level_key_is_rejected():
    with pytest.raises(registry.RegistryError, match="unknown top-level key"):
        registry.parse_declaration(_minimal_doc(typo_key=1), "docs/readiness/demo_feature.yml")


def test_file_name_must_match_feature_key():
    with pytest.raises(registry.RegistryError, match="must be named"):
        registry.parse_declaration(_minimal_doc(), "docs/readiness/other_name.yml")


def test_invalid_status_is_rejected():
    with pytest.raises(registry.RegistryError, match="is not one of"):
        registry.parse_declaration(_minimal_doc(status="probably_fine"),
                                   "docs/readiness/demo_feature.yml")


def test_byte_identity_claim_without_a_test_is_rejected():
    """An unproven byte-identity claim is the exact failure this register exists to prevent."""
    doc = _minimal_doc()
    doc["evidence"]["off_path_byte_identical"] = {"claimed": "true"}
    with pytest.raises(registry.RegistryError, match="no 'test' is named"):
        registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")


def test_assumption_reference_requires_a_note():
    """CLAUDE.md: an unsourced number must be labelled an ASSUMPTION, with reasoning."""
    doc = _minimal_doc()
    doc["evidence"]["reference"] = {"kind": "assumption"}
    with pytest.raises(registry.RegistryError, match="requires a 'note'"):
        registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")


def test_pending_assessment_requires_a_reason():
    doc = _minimal_doc(assessment={"status": "pending"})
    with pytest.raises(registry.RegistryError, match="pending_reason"):
        registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")


def test_load_registry_reads_every_declaration(tmp_path):
    """The one-file-per-feature naming rule is what makes duplicate keys impossible."""
    directory = tmp_path / "docs" / "readiness"
    directory.mkdir(parents=True)
    for key in ("demo_feature", "demo_feature_copy"):
        doc = _minimal_doc(feature=key)
        (directory / f"{key}.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert [d.feature for d in registry.load_registry(str(tmp_path))] == [
        "demo_feature", "demo_feature_copy"]

    # A file whose name no longer matches its key is rejected, so two files can never
    # claim the same feature key.
    (directory / "demo_feature_copy.yml").write_text(
        yaml.safe_dump(_minimal_doc()), encoding="utf-8")
    with pytest.raises(registry.RegistryError, match="must be named"):
        registry.load_registry(str(tmp_path))


# -- individual check behaviour --------------------------------------------------------


def test_dead_flag_is_a_failure(tmp_path):
    declaration = registry.parse_declaration(
        _minimal_doc(flag="this_flag_does_not_exist_anywhere_in_the_repo"),
        "docs/readiness/demo_feature.yml")
    finding = checks.check_flag_resolves(declaration, checks.CheckContext(REPO_ROOT))
    assert finding.severity == checks.FAIL


def test_missing_test_file_is_a_failure():
    doc = _minimal_doc()
    doc["evidence"]["tests"] = ["tests/test_this_file_does_not_exist.py"]
    declaration = registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")
    finding = checks.check_tests_exist(declaration, checks.CheckContext(REPO_ROOT))
    assert finding.severity == checks.FAIL


def test_uncommitted_reference_is_a_failure():
    doc = _minimal_doc()
    doc["evidence"]["reference"] = {"kind": "committed",
                                    "path": "eqasim-data/data/braunschweig/not_committed.csv"}
    declaration = registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")
    finding = checks.check_reference_committed(declaration, checks.CheckContext(REPO_ROOT))
    assert finding.severity == checks.FAIL


def test_session_log_source_resolves_softly_when_absent():
    """SESSION_LOG.md is gitignored; a pointer into it must SKIP, never FAIL."""
    doc = _minimal_doc(assessment={"status": "pending", "pending_reason": "x",
                                   "source": "SESSION_LOG.md#2026-07-19"})
    declaration = registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")
    finding = checks.check_assessment_source(declaration, checks.CheckContext(REPO_ROOT))
    expected = checks.OK if os.path.exists(os.path.join(REPO_ROOT, "SESSION_LOG.md")) else checks.SKIP
    assert finding.severity == expected


def test_active_feature_without_assessment_fails():
    doc = _minimal_doc()
    doc.pop("assessment")
    declaration = registry.parse_declaration(doc, "docs/readiness/demo_feature.yml")
    finding = checks.check_assessment_present(declaration, checks.CheckContext(REPO_ROOT))
    assert finding.severity == checks.FAIL


# -- repository guards -----------------------------------------------------------------


@pytest.fixture(scope="module")
def real_registry():
    declarations = registry.load_registry(REPO_ROOT)
    context = checks.CheckContext(REPO_ROOT)
    return declarations, context, checks.run_all_checks(declarations, context)


def test_registry_is_not_empty(real_registry):
    declarations, _, _ = real_registry
    assert declarations, "docs/readiness/ contains no declarations"


def test_no_declaration_has_a_failing_pointer(real_registry):
    """Every declared pointer must resolve. WARN (unproven) is allowed; FAIL is not."""
    _, _, findings = real_registry
    failures = [str(f) for f in findings if f.severity == checks.FAIL]
    assert not failures, "readiness declarations with unresolvable pointers:\n" + "\n".join(failures)


def test_declared_code_paths_exist(real_registry):
    declarations, context, _ = real_registry
    missing = [f"{d.feature}: {p}" for d in declarations for p in d.code_paths
               if not context.exists(p)]
    assert not missing, "declared code_paths that do not exist:\n" + "\n".join(missing)


def test_matrix_coverage_is_reported(real_registry):
    """Coverage is advisory during the pilot; this pins that it is measurable at all."""
    declarations, context, _ = real_registry
    covered, active = checks.matrix_coverage(declarations, context)
    assert active > 0, "no active rows found in the PROJECT_STATUS.md feature matrix"
    assert covered == len(declarations), (
        f"{len(declarations)} declarations but only {covered} matched a matrix row -- "
        "a status_matrix_row string is stale")
