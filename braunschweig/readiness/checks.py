"""Verify that the evidence a feature declares actually resolves.

Every check here is deterministic and needs no simulation run: it resolves a declared
pointer against the repository (config, test files, git index, source markers,
DECISIONS index, RUNS.md) and reports OK / WARN / FAIL / SKIP. Nothing in this module
measures model quality -- a KPI value can only come from a run, so the register
declares it and these checks verify only that it is attributed to a recorded run.

Severity contract:
- FAIL  the declaration asserts something the repository contradicts (dead flag,
        missing test, uncommitted "committed" reference, absent assessment).
- WARN  the declaration is unproven or stale, but nothing is contradicted.
- SKIP  the evidence lives in a file that is deliberately not committed (SESSION_LOG.md)
        and is absent on this machine -- never an error.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

from braunschweig import config_compose
from braunschweig.readiness.registry import FeatureDeclaration

logger = logging.getLogger("braunschweig")

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

#: Source trees scanned for fallback-rate log markers (C4).
SOURCE_TREES = ("braunschweig", "synthesis", "data", "matsim", "documentation")

#: Config pair composed to resolve declared flags (C1). The 25% overlay is used
#: because it is the scale the current feature set was validated at; any overlay
#: yields the same flag KEYS, only per-scale values differ.
BASE_CONFIG = os.path.join("configs", "base_bs.yml")
OVERLAY_CONFIG = os.path.join("configs", "overlays", "test_25pct.yml")

SESSION_LOG = "SESSION_LOG.md"
DECISIONS = os.path.join("docs", "DECISIONS.md")
RUNS = "RUNS.md"
STATUS = "PROJECT_STATUS.md"


@dataclass(frozen=True)
class Finding:
    """One check outcome for one feature."""

    feature: str
    check: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity:<4} {self.feature:<28} {self.check}  {self.message}"


class CheckContext:
    """Repository facts, read once and reused across all features.

    Reading the git index and the source trees is the expensive part; doing it per
    feature would make the pytest guard slow enough that someone would disable it.
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self._tracked_files = None
        self._config_keys = None
        self._source_text = None
        self._file_cache = {}

    # -- lazily built repository facts ------------------------------------------------

    @property
    def tracked_files(self):
        """Set of repo-relative paths in the git index (POSIX separators)."""
        if self._tracked_files is None:
            output = self._git("ls-files")
            if output is None:
                logger.warning("[readiness] git unavailable; reference-commitment checks "
                               "(C3) will be reported as SKIP, not silently passed")
                self._tracked_files = frozenset()
            else:
                self._tracked_files = frozenset(
                    line.strip() for line in output.splitlines() if line.strip())
        return self._tracked_files

    @property
    def config_keys(self):
        """Every key of the composed run config (dotted keys stay verbatim)."""
        if self._config_keys is None:
            base = os.path.join(self.repo_root, BASE_CONFIG)
            overlay = os.path.join(self.repo_root, OVERLAY_CONFIG)
            merged = config_compose.compose(base, overlay)
            self._config_keys = frozenset(_flatten_keys(merged))
        return self._config_keys

    @property
    def source_text(self) -> str:
        """Concatenated text of every ``.py`` file in the tracked source trees."""
        if self._source_text is None:
            chunks = []
            for tree in SOURCE_TREES:
                root = os.path.join(self.repo_root, tree)
                if not os.path.isdir(root):
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".ipynb_checkpoints")]
                    for filename in filenames:
                        if filename.endswith(".py"):
                            chunks.append(self.read_text(os.path.join(dirpath, filename)))
            self._source_text = "\n".join(chunks)
        return self._source_text

    def read_text(self, path: str) -> str:
        """Read a file once, tolerating undecodable bytes (checks only ever substring-match)."""
        if path not in self._file_cache:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self._file_cache[path] = f.read()
            except OSError:
                self._file_cache[path] = ""
        return self._file_cache[path]

    def read_repo_text(self, relative_path: str) -> str:
        return self.read_text(os.path.join(self.repo_root, relative_path))

    def exists(self, relative_path: str) -> bool:
        return os.path.exists(os.path.join(self.repo_root, relative_path))

    def last_commit_date(self, paths: Sequence[str]):
        """Author date (``date``) of the newest commit touching ``paths``; None if unknown."""
        output = self._git("log", "-1", "--format=%cs", "--", *paths)
        if not output:
            return None
        try:
            return datetime.date.fromisoformat(output.strip().splitlines()[0].strip())
        except (ValueError, IndexError):
            logger.warning("[readiness] could not parse commit date for %s: %r", paths, output)
            return None

    def _git(self, *args: str):
        try:
            completed = subprocess.run(
                ("git", *args), cwd=self.repo_root, capture_output=True, text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout


def _flatten_keys(node, prefix: str = "") -> Iterable[str]:
    """Yield every key path of a nested mapping, both dotted-nested and verbatim.

    The run configs mix styles: ``freight_enabled`` sits at the top level of the
    ``config`` block while ``braunschweig.population.popsim.placement_income`` is a
    single dotted key. Emitting both forms lets one check cover both conventions.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            key = str(key)
            yield key
            dotted = f"{prefix}.{key}" if prefix else key
            yield dotted
            yield from _flatten_keys(value, dotted)


# -- individual checks ----------------------------------------------------------------


def check_flag_resolves(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C1 -- the declared flag exists either in the composed config or as a code default.

    A flag may legitimately live only in code (``context.config(KEY, default)``); the
    STATUS matrix marks several of those "(code true)". Both are accepted, but which
    one applies is reported, because a flag that resolves NOWHERE is dead wiring.
    """
    if not declaration.flag:
        return Finding(declaration.feature, "C1-flag", SKIP, "no flag declared (always-on feature)")
    flag = declaration.flag
    if flag in context.config_keys:
        return Finding(declaration.feature, "C1-flag", OK, f"'{flag}' set in the composed config")
    if f'"{flag}"' in context.source_text or f"'{flag}'" in context.source_text:
        return Finding(declaration.feature, "C1-flag", OK,
                       f"'{flag}' resolves as a code-level default (not set in the config)")
    return Finding(declaration.feature, "C1-flag", FAIL,
                   f"'{flag}' appears neither in the composed config nor in the source -- dead flag")


def check_tests_exist(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C2 -- every declared test file exists and every named test function is defined.

    Static verification, not pytest collection: running a collection pass from inside
    the suite is fragile, and a renamed or deleted test -- the drift that actually
    happens -- is caught statically all the same.
    """
    if not declaration.tests:
        return Finding(declaration.feature, "C2-tests", WARN, "no tests declared")
    problems = []
    for node_id in declaration.tests:
        path, _, test_name = str(node_id).partition("::")
        if not context.exists(path):
            problems.append(f"{path} does not exist")
            continue
        if test_name:
            body = context.read_repo_text(path)
            if not re.search(rf"^\s*(async\s+)?def\s+{re.escape(test_name)}\s*\(", body, re.MULTILINE):
                problems.append(f"{path} does not define {test_name}")
    if problems:
        return Finding(declaration.feature, "C2-tests", FAIL, "; ".join(problems))
    return Finding(declaration.feature, "C2-tests", OK,
                   f"{len(declaration.tests)} declared test target(s) present")


def check_reference_committed(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C3 -- a 'committed' reference must be git-tracked; assumptions must say so.

    This is the mechanical form of the CLAUDE.md rule that a reference value is only
    real if it is traceable to a committed source.
    """
    reference = declaration.reference
    kind = str(reference.get("kind"))
    if kind == "assumption":
        return Finding(declaration.feature, "C3-reference", WARN,
                       f"ASSUMPTION, no committed reference: {reference.get('note')}")
    if kind == "none":
        return Finding(declaration.feature, "C3-reference", WARN,
                       f"no reference at all: {reference.get('note')}")
    path = str(reference.get("path"))
    if not context.tracked_files:
        return Finding(declaration.feature, "C3-reference", SKIP,
                       "git index unavailable; cannot verify that the reference is committed")
    if path.replace("\\", "/") in context.tracked_files:
        return Finding(declaration.feature, "C3-reference", OK, f"committed: {path}")
    return Finding(declaration.feature, "C3-reference", FAIL,
                   f"declared as committed but not in the git index: {path}")


def check_fallback_instrumented(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C4 -- a claimed fallback-rate log marker must actually occur in the source.

    CLAUDE.md makes rate logging mandatory for every code path with a fallback. An
    uninstrumented feature is not an error here, but it is reported, so the gap is
    visible rather than assumed away.
    """
    fallback = declaration.fallback_rate
    instrumented = bool(fallback.get("instrumented"))
    if not instrumented:
        return Finding(declaration.feature, "C4-fallback", WARN,
                       "fallback rate not instrumented -- primary-vs-fallback split is unobservable")
    marker = str(fallback.get("log_marker") or "").strip()
    if not marker:
        return Finding(declaration.feature, "C4-fallback", FAIL,
                       "instrumented is true but no log_marker is declared")
    if marker in context.source_text:
        return Finding(declaration.feature, "C4-fallback", OK, f"log marker {marker!r} found in source")
    return Finding(declaration.feature, "C4-fallback", FAIL,
                   f"declared log marker {marker!r} occurs nowhere in the source trees")


def check_docs_resolve(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C5 -- the declared ADR is in the DECISIONS index and the detail doc exists."""
    problems = []
    if declaration.adr:
        index = context.read_repo_text(DECISIONS)
        if f"| {declaration.adr} |" not in index:
            problems.append(f"{declaration.adr} is not in the {DECISIONS} index")
    if declaration.detail_doc and not context.exists(declaration.detail_doc):
        problems.append(f"detail doc missing: {declaration.detail_doc}")
    if problems:
        return Finding(declaration.feature, "C5-docs", WARN, "; ".join(problems))
    return Finding(declaration.feature, "C5-docs", OK, "ADR and detail doc resolve")


def check_kpi_run_recorded(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C6 -- a declared KPI value must name a run that RUNS.md records."""
    kpi = declaration.kpi
    value = kpi.get("last_value")
    measured_on = str(kpi.get("measured_on") or "").strip()
    if value is None:
        return Finding(declaration.feature, "C6-kpi", WARN,
                       "no KPI value declared -- the feature has no measured realised outcome")
    if not measured_on:
        return Finding(declaration.feature, "C6-kpi", FAIL,
                       f"KPI value {value!r} is declared without a 'measured_on' run")
    if measured_on in context.read_repo_text(RUNS):
        return Finding(declaration.feature, "C6-kpi", OK,
                       f"{kpi.get('name')}={value} measured on {measured_on} (recorded in {RUNS})")
    return Finding(declaration.feature, "C6-kpi", WARN,
                   f"run '{measured_on}' is not recorded in {RUNS}")


def check_status_matrix_row(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C7 -- the declared STATUS matrix row exists verbatim.

    Only the declaration -> matrix direction is enforced. The reverse (every active
    matrix row is declared) is reported as coverage by ``run_all_checks`` and becomes
    an error once the register covers the whole matrix.
    """
    if not declaration.status_matrix_row:
        return Finding(declaration.feature, "C7-matrix", WARN, "no status_matrix_row declared")
    if declaration.status_matrix_row in context.read_repo_text(STATUS):
        return Finding(declaration.feature, "C7-matrix", OK, "matrix row found")
    return Finding(declaration.feature, "C7-matrix", FAIL,
                   f"row {declaration.status_matrix_row!r} is not in {STATUS} -- "
                   "the matrix was edited without updating this declaration")


def check_assessment_present(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C8 -- an active feature carries a human assessment (possibly still pending).

    The checker cannot judge prose. It enforces that a judgement EXISTS, is attributed,
    and names a source; a block marked ``status: pending`` is a WARN, so an honest
    "not yet assessed" is visible without turning the suite red.
    """
    if declaration.status not in ("on", "assumption", "parked"):
        return Finding(declaration.feature, "C8-assessment", SKIP,
                       f"status '{declaration.status}' does not require an assessment")
    if not declaration.assessment:
        return Finding(declaration.feature, "C8-assessment", FAIL,
                       "no assessment block -- an active feature must carry an expert judgement")
    if declaration.assessment_is_pending:
        return Finding(declaration.feature, "C8-assessment", WARN,
                       f"assessment pending: {declaration.assessment.get('pending_reason')}")
    if not str(declaration.assessment.get("verdict") or "").strip():
        return Finding(declaration.feature, "C8-assessment", FAIL, "assessment has an empty verdict")
    if not str(declaration.assessment.get("by") or "").strip():
        return Finding(declaration.feature, "C8-assessment", FAIL, "assessment is not attributed ('by')")
    return Finding(declaration.feature, "C8-assessment", OK,
                   f"assessed by {declaration.assessment.get('by')} on {declaration.assessment.get('date')}")


def check_assessment_freshness(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """C9 -- the assessment is not older than the code it assesses.

    Surfaces the case the STATUS matrix cannot show: a green feature whose judgement
    predates later changes to its own implementation.
    """
    assessment = declaration.assessment or {}
    raw_date = assessment.get("date")
    if not raw_date:
        return Finding(declaration.feature, "C9-freshness", SKIP, "no assessment date to compare")
    try:
        assessed_on = (raw_date if isinstance(raw_date, datetime.date)
                       else datetime.date.fromisoformat(str(raw_date)))
    except ValueError:
        return Finding(declaration.feature, "C9-freshness", FAIL,
                       f"assessment.date {raw_date!r} is not an ISO date (YYYY-MM-DD)")
    code_changed_on = context.last_commit_date(declaration.code_paths)
    if code_changed_on is None:
        return Finding(declaration.feature, "C9-freshness", SKIP,
                       "git history unavailable for the declared code paths")
    if code_changed_on > assessed_on:
        return Finding(declaration.feature, "C9-freshness", WARN,
                       f"assessment is from {assessed_on}, but the code changed on {code_changed_on} "
                       "-- the judgement predates the implementation it covers")
    return Finding(declaration.feature, "C9-freshness", OK,
                   f"assessed {assessed_on}, code last changed {code_changed_on}")


def check_assessment_source(declaration: FeatureDeclaration, context: CheckContext) -> Finding:
    """Resolve ``assessment.source``: ADRs hard, SESSION_LOG.md softly.

    ``SESSION_LOG.md`` is gitignored and absent on some machines, so a pointer into it
    resolves when the file is present and reports SKIP when it is not -- never FAIL.
    """
    assessment = declaration.assessment or {}
    source = str(assessment.get("source") or "").strip()
    if not source:
        return Finding(declaration.feature, "C5-source", SKIP, "no assessment source declared")
    if source.startswith("ADR-"):
        if f"| {source} |" in context.read_repo_text(DECISIONS):
            return Finding(declaration.feature, "C5-source", OK, f"{source} resolves")
        return Finding(declaration.feature, "C5-source", FAIL,
                       f"{source} is not in the {DECISIONS} index")
    if SESSION_LOG in source:
        if not context.exists(SESSION_LOG):
            return Finding(declaration.feature, "C5-source", SKIP,
                           f"{SESSION_LOG} is not present on this machine (gitignored); "
                           "cannot resolve, not an error")
        return Finding(declaration.feature, "C5-source", OK, f"{SESSION_LOG} present")
    return Finding(declaration.feature, "C5-source", WARN,
                   f"unrecognised assessment source {source!r} (expected an ADR id or {SESSION_LOG})")


ALL_CHECKS = (
    check_flag_resolves,
    check_tests_exist,
    check_reference_committed,
    check_fallback_instrumented,
    check_docs_resolve,
    check_assessment_source,
    check_kpi_run_recorded,
    check_status_matrix_row,
    check_assessment_present,
    check_assessment_freshness,
)


def run_all_checks(declarations: Sequence[FeatureDeclaration], context: CheckContext):
    """Run every check over every declaration and return the findings in file order."""
    findings = []
    for declaration in declarations:
        for check in ALL_CHECKS:
            findings.append(check(declaration, context))
    counts = {level: sum(1 for f in findings if f.severity == level) for level in (OK, WARN, FAIL, SKIP)}
    logger.info("[readiness] %d feature(s), %d checks: %d OK, %d WARN, %d FAIL, %d SKIP",
                len(declarations), len(findings), counts[OK], counts[WARN], counts[FAIL], counts[SKIP])
    return findings


def matrix_coverage(declarations: Sequence[FeatureDeclaration], context: CheckContext):
    """Count active STATUS matrix rows and how many of them are declared.

    Returns ``(declared_rows, active_rows)``. An active row is a matrix line marked
    merged (✅) or flag-gated-ON (🟢). Advisory while the register is being filled.
    """
    status_text = context.read_repo_text(STATUS)
    active_rows = [line for line in status_text.splitlines()
                   if line.startswith("| **[") and ("✅" in line or "🟢" in line)]
    declared = {d.status_matrix_row for d in declarations if d.status_matrix_row}
    covered = sum(1 for line in active_rows
                  if any(row in line for row in declared))
    return covered, len(active_rows)
