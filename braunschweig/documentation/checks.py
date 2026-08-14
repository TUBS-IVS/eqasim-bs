"""Verify that everything the model registries declare actually resolves.

Generalized from the feature-readiness checker (branch feature/readiness-register):
every check is deterministic, needs no simulation run, and resolves declared
POINTERS against the repository -- the git index, the composed canonical
production configuration, the committed synpp DAG snapshots, the ADR records,
the run manifests, and the source trees. Nothing here measures model quality; a
KPI can only come from a run, so the registries declare it and the checks verify
that it is attributed to a recorded manifest.

Severity contract (unchanged from the readiness register):
- FAIL  a declaration asserts something the repository contradicts (dead flag,
        missing test, uncommitted "committed" reference, unreachable "active"
        stage, production state contradicting the resolved config).
- WARN  declared but unproven, stale, or incomplete -- honest gaps stay visible
        without turning the suite red.
- SKIP  the evidence cannot be resolved in THIS environment (no git index, no
        synpp, gitignored file absent) -- reported, never silently passed.
"""
from __future__ import annotations

import datetime
import fnmatch
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import yaml

from braunschweig.documentation import adr as adr_module
from braunschweig.documentation import dag as dag_module
from braunschweig.documentation import registries

logger = logging.getLogger("braunschweig")

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

#: Source trees scanned for flag reads and fallback-rate log markers.
SOURCE_TREES = ("braunschweig", "eqasim_common", "synthesis", "data", "matsim",
                "documentation", "scripts")

#: Canonical production configuration (ADR-0077): the fixed feature base composed
#: with the 100% scale overlay. Feature flags live only in the base, so their
#: values are scale-invariant across overlays (enforced by check K5).
BASE_CONFIG = os.path.join("configs", "base_bs.yml")
PRODUCTION_OVERLAY = os.path.join("configs", "overlays", "test_100pct.yml")

#: Flags whose CODE default is true, verified against the source (readiness C1
#: accepted "resolves as a code-level default"; here the resolved production
#: VALUE additionally needs the default). Each entry names its evidence.
CODE_DEFAULT_TRUE = {
    "status_from_hhtype": "braunschweig/synthesis/population/enriched/base.py",
    "synthesise_housing_tenure": "braunschweig/popsim/stage.py",
    "simwrapper_dashboards": "matsim/simulation/run.py (ADR-0074; issue #253)",
    "work_building_potentials": "braunschweig/locations/work.py",
    "freight_enabled": "braunschweig/matsim/simulation/prepare.py",
    "braunschweig.population.popsim.placement_income":
        "braunschweig/popsim/stage.py (ADR-0069, default ON)",
    "fleet_consistency_v2": "braunschweig/synthesis/vehicles/cars/household.py:299",
    "simwrapper_export_enabled": "braunschweig/analysis/simwrapper_export.py:26",
    "braunschweig.population.popsim.work_participation_kreis_control":
        "braunschweig/popsim/stage.py _KREIS_CONTROL_DEFAULT ('on', #224)",
    "cordon_student_incommuters_enabled":
        "braunschweig/synthesis/student_incommuters.py (tri-state default None = ON "
        "when education_gravity_enabled, which configs/base_bs.yml sets true)",
    "braunschweig.gravity.verbindungen_anchor_enabled":
        "braunschweig/gravity/model.py:307 (ADR-0068, default ON)",
}

#: Overlay keys that are legitimately per-scale (check K5): everything else in an
#: overlay's config block is feature drift between scales, which the composed-
#: config design exists to prevent (ADR-0070).
SCALE_ONLY_KEYS = {
    "sampling_rate", "output_path", "analysis_working_directory", "output_prefix",
    "matsim_last_iteration", "processes",
    "braunschweig.population.popsim.work_dir",
    "braunschweig.population.popsim.max_cells",
    "braunschweig.population.popsim.num_workers",
    "braunschweig.population.popsim.importance_profile",
    "cache_share_export", "cache_share_recompute",
}

SESSION_LOG = "SESSION_LOG.md"


@dataclass(frozen=True)
class Finding:
    """One check outcome for one record."""

    record: str
    check: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity:<4} {self.record:<38} {self.check:<14} {self.message}"


def _flatten_values(node, prefix: str = "") -> Iterable:
    """Yield ``(key, value)`` for every key path, verbatim AND dotted-joined.

    The run configs mix styles: ``freight_enabled`` is a plain top-level key while
    ``braunschweig.population.popsim.placement_income`` is a single literal dotted
    key. Emitting both forms lets one lookup cover both conventions (verbatim keys
    win over joined paths on collision, handled by insertion order in the caller).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            key = str(key)
            dotted = f"{prefix}.{key}" if prefix else key
            yield dotted, value
            if not isinstance(value, dict):
                yield key, value
            yield from _flatten_values(value, dotted)


class CheckContext:
    """Repository facts, read once and reused across all checks."""

    def __init__(self, repo_root: str, use_dag_extraction: bool = True):
        self.repo_root = repo_root
        self.use_dag_extraction = use_dag_extraction
        self._tracked_files = None
        self._config_values = None
        self._source_text = None
        self._file_cache: Dict[str, str] = {}
        self.features = registries.load_features(repo_root)
        self.stages = registries.load_stages(repo_root)
        self.datasets = registries.load_data(repo_root)
        self.manifests = registries.load_manifests(repo_root)
        self.adrs = adr_module.load_adrs(repo_root)
        self.snapshots = dag_module.load_all_snapshots(repo_root)
        self.manifest_ids = {record["id"] for record in self.manifests}
        self.adr_ids = {record.id for record in self.adrs}
        self.stage_ids = {record["stage"] for record in self.stages}
        self.dataset_ids = {record["dataset"] for record in self.datasets}
        self.pipeline_nodes = {
            "popsim_mid": set(self.snapshots.get("production", {}).get("nodes", [])),
            "popsim_open": set(self.snapshots.get("popsim_open", {}).get("nodes", [])),
            "simple_ipf_open": set(
                self.snapshots.get("simple_ipf_open", {}).get("nodes", [])),
        }
        self.dag_union = set().union(*self.pipeline_nodes.values())

    # -- lazily built repository facts --------------------------------------- #

    @property
    def tracked_files(self):
        if self._tracked_files is None:
            output = self._git("ls-files")
            if output is None:
                logger.warning("[documentation] git unavailable; committed-reference "
                               "checks will be reported as SKIP")
                self._tracked_files = None
            else:
                self._tracked_files = frozenset(
                    line.strip() for line in output.splitlines() if line.strip())
        return self._tracked_files

    @property
    def config_values(self) -> Optional[Dict[str, object]]:
        """Resolved canonical production config values (verbatim keys win)."""
        if self._config_values is None:
            try:
                from braunschweig import config_compose
                merged = config_compose.compose(
                    os.path.join(self.repo_root, BASE_CONFIG),
                    os.path.join(self.repo_root, PRODUCTION_OVERLAY))
            except Exception as error:  # config module or files unavailable
                logger.warning("[documentation] cannot compose the canonical "
                               "production config (%s); config checks SKIP", error)
                self._config_values = {}
                return self._config_values
            values: Dict[str, object] = {}
            for key, value in _flatten_values(merged.get("config", {})):
                values.setdefault(key, value)
            # verbatim top-level keys override joined paths
            for key, value in (merged.get("config", {}) or {}).items():
                values[str(key)] = value
            self._config_values = values
        return self._config_values

    @property
    def source_text(self) -> str:
        if self._source_text is None:
            chunks = []
            for tree in SOURCE_TREES:
                root = os.path.join(self.repo_root, tree)
                if not os.path.isdir(root):
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames
                                   if d not in ("__pycache__", ".ipynb_checkpoints")]
                    for filename in filenames:
                        if filename.endswith(".py"):
                            chunks.append(self.read_text(os.path.join(dirpath, filename)))
            self._source_text = "\n".join(chunks)
        return self._source_text

    def read_text(self, path: str) -> str:
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
        output = self._git("log", "-1", "--format=%cs", "--", *paths)
        if not output:
            return None
        try:
            return datetime.date.fromisoformat(output.strip().splitlines()[0].strip())
        except (ValueError, IndexError):
            return None

    def is_tracked(self, path: str) -> Optional[bool]:
        """True/False when the git index is available, None otherwise. Supports
        one-glob paths and brace lists ('a/{x,y}.csv') by any-match."""
        tracked = self.tracked_files
        if tracked is None:
            return None
        path = path.replace("\\", "/")
        if "{" in path:
            inner = re.search(r"\{([^}]*)\}", path)
            if inner:
                variants = [path[:inner.start()] + option + path[inner.end():]
                            for option in inner.group(1).split(",")]
                return any(self.is_tracked(v) for v in variants)
        if "*" in path:
            return any(fnmatch.fnmatch(candidate, path) for candidate in tracked)
        if path.endswith("/"):
            return any(candidate.startswith(path) for candidate in tracked)
        return path in tracked

    def flag_value(self, flag: str):
        """Resolved production value of a flag: composed config, else verified
        code default, else None (unresolvable)."""
        values = self.config_values or {}
        if flag in values:
            return values[flag]
        if flag in CODE_DEFAULT_TRUE:
            return True
        return None

    def flag_mentioned_in_source(self, flag: str) -> bool:
        return f'"{flag}"' in self.source_text or f"'{flag}'" in self.source_text

    def _git(self, *args: str):
        try:
            completed = subprocess.run(("git", *args), cwd=self.repo_root,
                                       capture_output=True, text=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout


# --------------------------------------------------------------------------- #
# R -- registry cross-references
# --------------------------------------------------------------------------- #

def check_feature_stage_refs(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        unknown = [s for s in feature["stages"] if s not in context.dag_union]
        if unknown:
            findings.append(Finding(feature["feature"], "R1-stages", FAIL,
                                    f"stage(s) not in any DAG snapshot: {unknown}"))
        else:
            findings.append(Finding(feature["feature"], "R1-stages", OK,
                                    f"{len(feature['stages'])} stage ref(s) resolve"))
    return findings


def check_stage_dataset_refs(context: CheckContext) -> List[Finding]:
    findings = []
    for stage in context.stages:
        inputs = stage.get("inputs") or []
        unknown = [d for d in inputs if d not in context.dataset_ids]
        if unknown:
            findings.append(Finding(stage["stage"], "R2-inputs", FAIL,
                                    f"unknown dataset id(s): {unknown}"))
    return findings


def check_adr_refs(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        adr_ref = (feature.get("introduced") or {}).get("adr")
        if adr_ref and adr_ref not in context.adr_ids:
            findings.append(Finding(feature["feature"], "R4-adr", FAIL,
                                    f"introduced.adr '{adr_ref}' has no record under "
                                    "docs/decisions/"))
    for stage in context.stages:
        for adr_ref in stage.get("decisions") or []:
            if adr_ref not in context.adr_ids:
                findings.append(Finding(stage["stage"], "R4-adr", FAIL,
                                        f"decision '{adr_ref}' has no ADR record"))
    for manifest in context.manifests:
        for adr_ref in manifest.get("decisions") or []:
            if adr_ref not in context.adr_ids:
                findings.append(Finding(manifest["id"], "R4-adr", FAIL,
                                        f"decision '{adr_ref}' has no ADR record"))
    if not findings:
        findings.append(Finding("(all records)", "R4-adr", OK,
                                "every ADR reference resolves to a record file"))
    return findings


def check_run_refs(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        for run in feature["validation"]["runs"]:
            if run not in context.manifest_ids:
                findings.append(Finding(feature["feature"], "R5-runs", FAIL,
                                        f"validation run '{run}' has no manifest "
                                        "under docs/runs/"))
    if not findings:
        findings.append(Finding("(all features)", "R5-runs", OK,
                                "every validation run resolves to a manifest"))
    return findings


def check_paths_and_tests(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        problems = []
        for path in feature["code_paths"]:
            if not context.exists(path):
                problems.append(f"code path missing: {path}")
        for node_id in feature["evidence"]["tests"]:
            path, _, test_name = str(node_id).partition("::")
            if not context.exists(path):
                problems.append(f"test file missing: {path}")
            elif test_name:
                body = context.read_repo_text(path)
                if not re.search(rf"^\s*(async\s+)?def\s+{re.escape(test_name)}\s*\(",
                                 body, re.MULTILINE):
                    problems.append(f"{path} does not define {test_name}")
        detail = feature.get("detail_doc")
        if detail and not context.exists(detail):
            problems.append(f"detail doc missing: {detail}")
        if problems:
            findings.append(Finding(feature["feature"], "R6-paths", FAIL,
                                    "; ".join(problems)))
    if not findings:
        findings.append(Finding("(all features)", "R6-paths", OK,
                                "all code paths, tests and detail docs exist"))
    return findings


def check_stage_coverage(context: CheckContext) -> List[Finding]:
    findings = []
    missing = context.dag_union - context.stage_ids
    if missing:
        findings.append(Finding("(stage registry)", "R8-coverage", FAIL,
                                f"DAG stages without a registry record: {sorted(missing)}"))
    for stage in context.stages:
        if stage["stage"] not in context.dag_union:
            parked = (not stage["production"]) and all(
                value == "not_used" for value in stage["pipelines"].values())
            if not parked:
                findings.append(Finding(stage["stage"], "R8-coverage", FAIL,
                                        "not in any DAG snapshot but not declared "
                                        "as parked (production false, all not_used)"))
    if not findings:
        findings.append(Finding("(stage registry)", "R8-coverage", OK,
                                f"{len(context.dag_union)} DAG stages covered; "
                                "parked records consistent"))
    return findings


# --------------------------------------------------------------------------- #
# K -- config / DAG state
# --------------------------------------------------------------------------- #

def check_flags_resolve(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        flags = feature["production"].get("flags") or []
        if not flags:
            findings.append(Finding(feature["feature"], "K1-flag", SKIP,
                                    "no flag declared (always-on / data-driven)"))
            continue
        for flag in flags:
            if not context.config_values:
                findings.append(Finding(feature["feature"], "K1-flag", SKIP,
                                        "canonical config not composable here"))
                continue
            if flag in context.config_values:
                findings.append(Finding(feature["feature"], "K1-flag", OK,
                                        f"'{flag}' set in the composed config"))
            elif flag in CODE_DEFAULT_TRUE:
                findings.append(Finding(feature["feature"], "K1-flag", OK,
                                        f"'{flag}' resolves as a verified code default"))
            elif context.flag_mentioned_in_source(flag):
                findings.append(Finding(feature["feature"], "K1-flag", OK,
                                        f"'{flag}' resolves in the source (code-level "
                                        "default, not set in the config)"))
            else:
                findings.append(Finding(feature["feature"], "K1-flag", FAIL,
                                        f"'{flag}' appears neither in the composed "
                                        "config nor in the source -- dead flag"))
    return findings


def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "none", "off")
    return bool(value)


def check_production_state(context: CheckContext) -> List[Finding]:
    """K2 -- production.enabled must equal what the resolved config + DAG say.

    The lesson of the readiness branch (issue #255): a flag STRING existing is
    not production-activity. Expected enabled = (popsim_mid applicability is
    'active') AND (every declared flag resolves truthy in the canonical config
    or as a verified code default). Features without flags reduce to the
    applicability term.
    """
    findings = []
    if not context.config_values:
        return [Finding("(all features)", "K2-production", SKIP,
                        "canonical config not composable in this environment")]
    for feature in context.features:
        flags = feature["production"].get("flags") or []
        flag_values = [context.flag_value(flag) for flag in flags]
        flags_on = all(_truthy_flag(v) for v in flag_values) if flags else True
        unresolved = [flag for flag, v in zip(flags, flag_values) if v is None]
        applicable = feature["pipelines"]["popsim_mid"] == "active"
        expected = applicable and flags_on and not unresolved
        declared = feature["production"]["enabled"]
        if declared != expected:
            detail = (f"declared enabled={declared} but resolved state says "
                      f"{expected} (popsim_mid={feature['pipelines']['popsim_mid']}, "
                      f"flags={dict(zip(flags, flag_values))})")
            findings.append(Finding(feature["feature"], "K2-production", FAIL, detail))
        else:
            findings.append(Finding(feature["feature"], "K2-production", OK,
                                    f"enabled={declared} matches the resolved config"))
    return findings


def check_pipeline_reachability(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        if not feature["stages"]:
            findings.append(Finding(feature["feature"], "K3-reachability", SKIP,
                                    "no synpp stages declared (offline tooling / "
                                    "launcher-level feature)"))
            continue
        for pipeline, applicability in feature["pipelines"].items():
            nodes = context.pipeline_nodes.get(pipeline) or set()
            if not nodes:
                findings.append(Finding(feature["feature"], "K3-reachability", SKIP,
                                        f"no DAG snapshot for {pipeline}"))
                continue
            reachable = [s for s in feature["stages"] if s in nodes]
            if applicability == "active" and not reachable:
                findings.append(Finding(feature["feature"], "K3-reachability", FAIL,
                                        f"declared active under {pipeline} but none "
                                        f"of {feature['stages']} is reachable there"))
    if not any(f.severity == FAIL for f in findings):
        findings.append(Finding("(all features)", "K3-reachability", OK,
                                "every 'active' claim has a reachable stage"))
    return findings


def check_dag_freshness(context: CheckContext) -> List[Finding]:
    """K4 -- committed DAG snapshots match a fresh dryrun extraction."""
    if not context.use_dag_extraction:
        return [Finding("(dag)", "K4-freshness", SKIP, "--no-dag requested")]
    try:
        import synpp  # noqa: F401
    except ImportError:
        return [Finding("(dag)", "K4-freshness", SKIP,
                        "synpp not importable; cannot re-extract (CI mode)")]
    findings = []
    for pipeline, spec in dag_module.PIPELINE_CONFIGS.items():
        committed = context.snapshots.get(pipeline)
        if committed is None:
            findings.append(Finding(pipeline, "K4-freshness", FAIL,
                                    "no committed snapshot; run "
                                    "'python -m braunschweig.documentation dag'"))
            continue
        try:
            fresh = dag_module.extract(context.repo_root, spec["base"], spec["overlay"])
        except Exception as error:
            findings.append(Finding(pipeline, "K4-freshness", WARN,
                                    f"extraction failed here: {error}"))
            continue
        if fresh["nodes"] != committed["nodes"] or fresh["edges"] != committed["edges"]:
            findings.append(Finding(pipeline, "K4-freshness", WARN,
                                    "committed snapshot differs from a fresh dryrun "
                                    "-- regenerate with 'python -m "
                                    "braunschweig.documentation dag'"))
        else:
            findings.append(Finding(pipeline, "K4-freshness", OK,
                                    f"snapshot matches ({len(fresh['nodes'])} stages)"))
    return findings


def check_overlays_scale_only(context: CheckContext) -> List[Finding]:
    """K5 -- test_* overlays may only set per-scale keys (ADR-0070 invariant)."""
    findings = []
    overlay_dir = os.path.join(context.repo_root, "configs", "overlays")
    if not os.path.isdir(overlay_dir):
        return [Finding("(overlays)", "K5-scale-only", SKIP, "no overlays directory")]
    for name in sorted(os.listdir(overlay_dir)):
        if not name.startswith("test") or not name.endswith(".yml"):
            continue
        with open(os.path.join(overlay_dir, name), encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        keys = set((doc.get("config") or {}).keys())
        drift = sorted(keys - SCALE_ONLY_KEYS)
        if drift:
            findings.append(Finding(f"configs/overlays/{name}", "K5-scale-only", WARN,
                                    f"non-scale config key(s) in the overlay: {drift} "
                                    "-- feature flags belong in configs/base_bs.yml"))
        else:
            findings.append(Finding(f"configs/overlays/{name}", "K5-scale-only", OK,
                                    "only per-scale keys"))
    return findings


# --------------------------------------------------------------------------- #
# E -- evidence
# --------------------------------------------------------------------------- #

def check_reference_committed(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        reference = feature["evidence"]["reference"]
        kind = str(reference.get("kind"))
        if kind == "assumption":
            findings.append(Finding(feature["feature"], "E1-reference", WARN,
                                    f"ASSUMPTION, no committed reference: "
                                    f"{str(reference.get('note'))[:120]}"))
            continue
        if kind == "none":
            findings.append(Finding(feature["feature"], "E1-reference", WARN,
                                    f"no reference: {str(reference.get('note'))[:120]}"))
            continue
        tracked = context.is_tracked(str(reference.get("path")))
        if tracked is None:
            findings.append(Finding(feature["feature"], "E1-reference", SKIP,
                                    "git index unavailable"))
        elif tracked:
            findings.append(Finding(feature["feature"], "E1-reference", OK,
                                    f"committed: {reference.get('path')}"))
        else:
            findings.append(Finding(feature["feature"], "E1-reference", FAIL,
                                    f"declared committed but not in the git index: "
                                    f"{reference.get('path')}"))
    return findings


def check_fallback_instrumented(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        fallback = feature["evidence"]["fallback_rate"]
        if not bool(fallback.get("instrumented")):
            findings.append(Finding(feature["feature"], "E2-fallback", WARN,
                                    "fallback rate not instrumented -- primary-vs-"
                                    "fallback split unobservable"))
            continue
        marker = str(fallback.get("log_marker") or "").strip()
        if marker in context.source_text:
            findings.append(Finding(feature["feature"], "E2-fallback", OK,
                                    f"log marker {marker!r} found in source"))
        else:
            findings.append(Finding(feature["feature"], "E2-fallback", FAIL,
                                    f"declared log marker {marker!r} occurs nowhere "
                                    "in the source trees"))
    return findings


def check_validation_claims(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        validation = feature["validation"]
        state = validation["state"]
        if state == "behaviourally_validated":
            if feature["evidence"]["reference"].get("kind") != "committed":
                findings.append(Finding(feature["feature"], "E3-validation", FAIL,
                                        "behaviourally_validated requires a committed "
                                        "observed reference"))
        if state == "unvalidated" and feature["production"]["enabled"]:
            findings.append(Finding(feature["feature"], "E3-validation", WARN,
                                    "enabled in production but unvalidated (no "
                                    "recorded run compared it to its reference)"))
    if not findings:
        findings.append(Finding("(all features)", "E3-validation", OK,
                                "validation states consistent with evidence"))
    return findings


def check_assessments(context: CheckContext) -> List[Finding]:
    findings = []
    for feature in context.features:
        if feature["lifecycle"] not in ("active", "parked", "experimental"):
            continue
        assessment = feature.get("assessment")
        if not assessment:
            findings.append(Finding(feature["feature"], "E4-assessment", FAIL,
                                    "active feature without an assessment block"))
            continue
        if str(assessment.get("status", "")).strip() == "pending":
            findings.append(Finding(feature["feature"], "E4-assessment", WARN,
                                    f"assessment pending: "
                                    f"{str(assessment.get('pending_reason'))[:120]}"))
            continue
        if not str(assessment.get("by") or "").strip():
            findings.append(Finding(feature["feature"], "E4-assessment", FAIL,
                                    "assessment is not attributed ('by')"))
            continue
        raw_date = assessment.get("date")
        code_changed = context.last_commit_date(feature["code_paths"])
        if raw_date and code_changed:
            try:
                assessed = (raw_date if isinstance(raw_date, datetime.date)
                            else datetime.date.fromisoformat(str(raw_date)))
                if code_changed > assessed:
                    findings.append(Finding(feature["feature"], "E4-assessment", WARN,
                                            f"assessment from {assessed} predates the "
                                            f"code change on {code_changed}"))
                    continue
            except ValueError:
                findings.append(Finding(feature["feature"], "E4-assessment", FAIL,
                                        f"assessment.date {raw_date!r} is not ISO"))
                continue
        findings.append(Finding(feature["feature"], "E4-assessment", OK,
                                f"assessed by {assessment.get('by')}"))
    return findings


# --------------------------------------------------------------------------- #
# A / M -- ADRs and manifests
# --------------------------------------------------------------------------- #

def check_adr_id_preservation(context: CheckContext) -> List[Finding]:
    """A2 -- the migrated 0000..0076 range stays intact (0051 reserved)."""
    expected = {f"ADR-{i:04d}" for i in range(0, 77)} - {"ADR-0051"}
    missing = expected - context.adr_ids
    if missing:
        return [Finding("(adrs)", "A2-ids", FAIL,
                        f"historical ADR record(s) missing: {sorted(missing)}")]
    if "ADR-0051" in context.adr_ids:
        return [Finding("(adrs)", "A2-ids", FAIL,
                        "ADR-0051 is reserved (unmerged fleet branch) and must not "
                        "have a record until that branch lands")]
    return [Finding("(adrs)", "A2-ids", OK,
                    f"{len(context.adr_ids)} records; 0000..0076 preserved, 0051 "
                    "reserved")]


def check_manifest_dates(context: CheckContext) -> List[Finding]:
    findings = []
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
    for manifest in context.manifests:
        date = str(manifest.get("date"))
        if date == "unknown" or pattern.match(date):
            continue
        findings.append(Finding(manifest["id"], "M2-date", WARN,
                                f"date '{date}' is neither ISO-prefixed nor 'unknown'"))
    if not findings:
        findings.append(Finding("(manifests)", "M2-date", OK,
                                f"{len(context.manifests)} manifests dated or "
                                "explicitly unknown"))
    return findings


# --------------------------------------------------------------------------- #
# D -- documentation invariants
# --------------------------------------------------------------------------- #

_README_SCRIPT = re.compile(r"\bscripts/[A-Za-z0-9_]+\.(?:py|sh|ps1)\b")
_README_CONFIG = re.compile(r"\bconfigs/[A-Za-z0-9_./-]+\.yml\b")


def check_readme_references(context: CheckContext) -> List[Finding]:
    findings = []
    readme = context.read_repo_text("README.md")
    for pattern, check_name in ((_README_SCRIPT, "D2-scripts"),
                                (_README_CONFIG, "D3-configs")):
        missing = sorted({match for match in pattern.findall(readme)
                          if not context.exists(match)})
        if missing:
            findings.append(Finding("README.md", check_name, FAIL,
                                    f"referenced but missing: {missing}"))
        else:
            findings.append(Finding("README.md", check_name, OK,
                                    "all referenced paths exist"))
    return findings


def check_readme_data_coverage(context: CheckContext) -> List[Finding]:
    """D4 -- every production-required dataset is documented in the README."""
    findings = []
    readme = context.read_repo_text("README.md")
    for dataset in context.datasets:
        if dataset["requirements"]["production"] != "required":
            continue
        path = dataset["storage"]["expected_path"]
        needle = path.replace("eqasim-data/data/", "").split(" ")[0]
        needle = needle.split("{")[0].rstrip("/*")
        title_hint = dataset["dataset"]
        if needle and needle in readme or title_hint in readme:
            continue
        findings.append(Finding(dataset["dataset"], "D4-readme-data", WARN,
                                f"production-required dataset not found in README "
                                f"(looked for '{needle}')"))
    if not findings:
        findings.append(Finding("(datasets)", "D4-readme-data", OK,
                                "every production-required dataset appears in the "
                                "README data setup"))
    return findings


def check_archive_banners(context: CheckContext) -> List[Finding]:
    findings = []
    archive = os.path.join(context.repo_root, "docs", "archive")
    if not os.path.isdir(archive):
        return [Finding("(archive)", "D5-banners", SKIP, "no docs/archive directory")]
    for name in sorted(os.listdir(archive)):
        if not name.endswith(".md"):
            continue
        text = context.read_repo_text(os.path.join("docs", "archive", name))
        if "HISTORICAL" not in text[:600].upper():
            findings.append(Finding(f"docs/archive/{name}", "D5-banners", WARN,
                                    "archived document lacks a HISTORICAL banner in "
                                    "its head"))
    if not findings:
        findings.append(Finding("(archive)", "D5-banners", OK,
                                "every archived document carries a banner"))
    return findings


#: Files that legitimately mention the retired documents (stubs, history,
#: records quoting them as evidence).
_D6_ALLOWED_PREFIXES = (
    "docs/archive/", "docs/decisions/", "docs/runs/", "docs/registry/",
    "docs/superpowers/", "docs/generated/", ".superpowers/", "plan/",
    "docs/DECISIONS.md",
)
_D6_TARGETS = ("PROJECT_STATUS.md", "PROJECT_BACKLOG.md")
_D6_SCOPES = ("CLAUDE.md", "README.md", "CONTRIBUTING.md", "AGENTS.md",
              os.path.join("docs", "ONBOARDING.md"))


def check_no_retired_dependencies(context: CheckContext) -> List[Finding]:
    """D6 -- no live guidance/code depends on the retired PM documents."""
    findings = []
    for tree in ("braunschweig", "scripts"):
        root = os.path.join(context.repo_root, tree)
        for dirpath, dirnames, filenames in os.walk(root):
            # this package names the retired documents in its own messages
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "documentation")]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                relative = os.path.relpath(os.path.join(dirpath, filename),
                                           context.repo_root).replace(os.sep, "/")
                text = context.read_text(os.path.join(dirpath, filename))
                for target in _D6_TARGETS:
                    if target in text:
                        findings.append(Finding(relative, "D6-retired", FAIL,
                                                f"code references the retired "
                                                f"{target}"))
    for scope in _D6_SCOPES:
        lines = context.read_repo_text(scope).splitlines()
        for target in _D6_TARGETS:
            for index, line in enumerate(lines):
                if target not in line:
                    continue
                # sentences wrap: judge the mention in a one-line-each-side window
                window = " ".join(lines[max(0, index - 1):index + 2]).lower()
                if not any(word in window for word in ("retired", "archive", "stub")):
                    findings.append(Finding(scope, "D6-retired", WARN,
                                            f"still points at {target} without "
                                            f"marking it retired: {line.strip()[:90]}"))
                    break
    if not findings:
        findings.append(Finding("(docs)", "D6-retired", OK,
                                "no live dependency on the retired PM documents"))
    return findings


def check_generated_freshness(context: CheckContext) -> List[Finding]:
    """D1 -- docs/generated/* match a fresh render of the current registries."""
    from braunschweig.documentation import render
    findings = []
    for name, text in render.render_all(context).items():
        path = os.path.join("docs", "generated", name)
        if not context.exists(path):
            findings.append(Finding(path, "D1-generated", FAIL,
                                    "missing -- run 'python -m "
                                    "braunschweig.documentation build'"))
            continue
        committed = context.read_repo_text(path)
        if committed.replace("\r\n", "\n") != text:
            findings.append(Finding(path, "D1-generated", FAIL,
                                    "stale -- run 'python -m "
                                    "braunschweig.documentation build'"))
    if not findings:
        findings.append(Finding("(generated)", "D1-generated", OK,
                                "all generated views are fresh"))
    return findings


ALL_CHECKS = (
    check_feature_stage_refs,
    check_stage_dataset_refs,
    check_adr_refs,
    check_run_refs,
    check_paths_and_tests,
    check_stage_coverage,
    check_flags_resolve,
    check_production_state,
    check_pipeline_reachability,
    check_dag_freshness,
    check_overlays_scale_only,
    check_reference_committed,
    check_fallback_instrumented,
    check_validation_claims,
    check_assessments,
    check_adr_id_preservation,
    check_manifest_dates,
    check_readme_references,
    check_readme_data_coverage,
    check_archive_banners,
    check_no_retired_dependencies,
    check_generated_freshness,
)


def run_all_checks(context: CheckContext) -> List[Finding]:
    findings: List[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(context))
    counts = {level: sum(1 for f in findings if f.severity == level)
              for level in (OK, WARN, FAIL, SKIP)}
    logger.info("[documentation] %d records, %d findings: %d OK, %d WARN, %d FAIL, "
                "%d SKIP", len(context.features) + len(context.stages) +
                len(context.datasets) + len(context.manifests), len(findings),
                counts[OK], counts[WARN], counts[FAIL], counts[SKIP])
    return findings
