# braunschweig/synpp_fork/staleness.py
"""Why a synpp cache entry will or will not be reused (spec §4.4 and §4.6).

``assess`` replicates synpp 1.6.2's own-reason devalidation steps (required, not in meta,
module hash, cache files, parent set, validation token) and the parent-output rule: a child
is stale when a parent's recorded output digest differs from the parent's current one; when
either side lacks output hashes the upstream timestamp rule applies and is reported in
``legacy_rules``. Descendants of a stale stage are NOT marked stale here: the fork decides
them at run time from the re-executed parent's output (early cutoff); they are predicted in
``stale_if``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx
from synpp.pipeline import ValidateContext


@dataclass
class StageAssessment:
    stage: str
    hash: str
    cache_present: bool
    in_meta: bool
    reasons: list = field(default_factory=list)
    legacy_rules: list = field(default_factory=list)
    stale_if: list = field(default_factory=list)
    config_diff: dict = field(default_factory=dict)

    @property
    def stale(self) -> bool:
        return bool(self.reasons)


def artifacts_present(working_directory: str, stage_hash: str) -> bool:
    return (os.path.exists(os.path.join(working_directory, stage_hash + ".p"))
            and os.path.isdir(os.path.join(working_directory, stage_hash + ".cache")))


def token_difference(current, stored) -> list:
    """Names of the token modules that differ; ``["format"]`` for a structural change;
    ``["opaque"]`` when either token is not a structured source token."""
    if isinstance(current, dict) and isinstance(stored, dict) and "modules" in current and "modules" in stored:
        cm, sm = current["modules"], stored["modules"]
        names = sorted(name for name in set(cm) | set(sm) if cm.get(name) != sm.get(name))
        return names or ["format"]
    return ["opaque"]


def nearest_config_diff(stage_name: str, config: dict, meta: dict) -> dict:
    """Key -> (cached, current) against the cached variant of this stage with the fewest
    differing keys; empty when no variant of the stage is cached."""
    best = {}
    for entry, record in meta.items():
        if entry.rsplit("__", 1)[0] != stage_name:
            continue
        cached = record.get("config", {})
        diff = {key: (cached.get(key), config.get(key)) for key in set(cached) | set(config)
                if cached.get(key) != config.get(key)}
        if not best or len(diff) < len(best):
            best = diff
    return best


def assess(registry: dict, working_directory: str, meta: dict, *, rerun_required: bool = True) -> dict:
    graph = nx.DiGraph()
    for stage_hash, stage in registry.items():
        graph.add_node(stage_hash)
        for dependency in stage.get("dependencies", []):
            graph.add_edge(dependency, stage_hash)
    name_of = {h: s["wrapper"].name for h, s in registry.items()}
    out = {}
    for stage_hash in nx.topological_sort(graph):
        stage = registry[stage_hash]
        assessment = StageAssessment(name_of[stage_hash], stage_hash,
                                     artifacts_present(working_directory, stage_hash), stage_hash in meta)
        if rerun_required and "required-index" in stage:
            assessment.reasons.append("required")
        if not assessment.in_meta:
            assessment.reasons.append("not_in_meta")
        if not assessment.cache_present:
            assessment.reasons.append("cache_missing")
        if assessment.in_meta:
            entry = meta[stage_hash]
            if entry.get("module_hash") != stage["wrapper"].module_hash:
                assessment.reasons.append("module_hash_changed")
            if set(entry.get("dependencies", {})) != set(stage.get("dependencies", [])):
                assessment.reasons.append("parents_changed")
            cache_path = os.path.join(working_directory, stage_hash + ".cache")
            token = stage["wrapper"].validate(ValidateContext(stage["config"], cache_path))
            stored = entry.get("validation_token")
            if token != stored:
                assessment.reasons.extend("token_changed:" + name for name in token_difference(token, stored))
            if not assessment.reasons:
                for dependency in stage.get("dependencies", []):
                    if out[dependency].stale:
                        assessment.stale_if.append(name_of[dependency])
                        continue
                    parent = meta.get(dependency)
                    if parent is None:
                        assessment.reasons.append("parent_missing:" + name_of[dependency])
                        continue
                    recorded = entry.get("dependency_output_hashes", {}).get(dependency)
                    current = (parent.get("output_hash") or {}).get("digest")
                    if recorded is not None and current is not None:
                        if recorded != current:
                            assessment.reasons.append("parent_output_changed:" + name_of[dependency])
                    else:
                        assessment.legacy_rules.append(name_of[dependency])
                        if parent.get("updated", 0.0) > entry.get("dependencies", {}).get(dependency, 0.0):
                            assessment.reasons.append("parent_updated:" + name_of[dependency])
        else:
            assessment.config_diff = nearest_config_diff(name_of[stage_hash], stage["config"], meta)
        if not assessment.reasons:
            for dependency in stage.get("dependencies", []):
                for ancestor in out[dependency].stale_if:
                    if ancestor not in assessment.stale_if:
                        assessment.stale_if.append(ancestor)
        out[stage_hash] = assessment
    return out


def unexpected(assessments: dict, expected_names: set) -> list:
    """Stages stale for own reasons other than ``required`` and not in ``expected_names``."""
    return sorted(a.stage for a in assessments.values()
                  if a.stale and a.reasons != ["required"] and a.stage not in expected_names)


def format_report(assessments: dict) -> str:
    lines = []
    for assessment in sorted(assessments.values(), key=lambda a: a.stage):
        if assessment.stale:
            detail = ", ".join(assessment.reasons)
            if assessment.config_diff:
                detail += "; config vs nearest variant: " + ", ".join(
                    f"{k}: {old!r} -> {new!r}" for k, (old, new) in sorted(assessment.config_diff.items()))
            lines.append(f"STALE    {assessment.stage}  [{detail}]")
        elif assessment.stale_if:
            lines.append(f"STALE-IF {assessment.stage}  [if output of {', '.join(assessment.stale_if)} changes]")
        else:
            lines.append(f"HIT      {assessment.stage}")
        if assessment.legacy_rules:
            lines.append(f"         legacy timestamp rule for parents: {', '.join(assessment.legacy_rules)}")
    stale = [a for a in assessments.values() if a.stale]
    predicted = [a for a in assessments.values() if not a.stale and a.stale_if]
    lines.append(f"# {len(assessments)} stages: {len(stale)} stale for own reasons, "
                 f"{len(predicted)} depend on a stale ancestor, {len(assessments) - len(stale) - len(predicted)} hits")
    return "\n".join(lines)
