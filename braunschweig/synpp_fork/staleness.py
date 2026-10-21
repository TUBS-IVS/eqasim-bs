# braunschweig/synpp_fork/staleness.py
"""Why a synpp cache entry will or will not be reused (spec §4.4 and §4.6).

``assess`` replicates synpp 1.6.2's own-reason devalidation steps (required, not in meta,
module hash, cache files, parent set, validation token) and the parent-output rule: a child
is stale when a parent's recorded output digest differs from the parent's current one; when
either side lacks output hashes the upstream timestamp rule applies and is reported in
``legacy_rules``. Descendants of a stale stage are NOT marked stale here: the fork decides
them at run time from the re-executed parent's output (early cutoff); they are predicted in
``stale_if``. When a stale ancestor has no recorded output digest (not in ``pipeline.json``,
or its record lacks ``output_hash``, as every legacy entry does), there is nothing to compare
against and synpp (and the future fork) recompute the descendant for certain:
``certain_recompute`` is then True and the report says ``RECOMPUTE`` instead of ``STALE-IF``.

Not modelled: ephemeral stages. synpp neither caches nor validates them, so ``assess`` sees them
as uncached stages and over-reports them as stale (the safe direction: never a false HIT).
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
    unhashed_ancestors: list = field(default_factory=list)
    certain_recompute: bool = False
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
        if not isinstance(cm, dict) or not isinstance(sm, dict):
            return ["opaque"]
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


def has_output_hash(record) -> bool:
    """True when a ``pipeline.json`` record carries a digest of the stage's recorded output."""
    return bool(record) and bool((record.get("output_hash") or {}).get("digest"))


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
                        # A parent absent from meta is always stale for its own reasons, so
                        # every parent that is not skipped here has a meta record.
                        assessment.stale_if.append(name_of[dependency])
                        if not has_output_hash(meta.get(dependency)):
                            assessment.unhashed_ancestors.append(name_of[dependency])
                        continue
                    parent = meta[dependency]
                    recorded = entry.get("dependency_output_hashes", {}).get(dependency)
                    current = (parent.get("output_hash") or {}).get("digest")
                    if recorded is not None and current is not None:
                        if recorded != current:
                            assessment.reasons.append("parent_output_changed:" + name_of[dependency])
                    else:
                        assessment.legacy_rules.append(name_of[dependency])
                        parent_updated = parent.get("updated")
                        child_seen = entry.get("dependencies", {}).get(dependency)
                        if parent_updated is None:
                            assessment.reasons.append("meta_malformed:updated:" + name_of[dependency])
                        elif child_seen is None:
                            assessment.reasons.append("meta_malformed:dependency_timestamp:" + name_of[dependency])
                        elif parent_updated > child_seen:
                            assessment.reasons.append("parent_updated:" + name_of[dependency])
        else:
            assessment.config_diff = nearest_config_diff(name_of[stage_hash], stage["config"], meta)
        if not assessment.reasons:
            for dependency in stage.get("dependencies", []):
                for ancestor in out[dependency].stale_if:
                    if ancestor not in assessment.stale_if:
                        assessment.stale_if.append(ancestor)
                for ancestor in out[dependency].unhashed_ancestors:
                    if ancestor not in assessment.unhashed_ancestors:
                        assessment.unhashed_ancestors.append(ancestor)
            assessment.certain_recompute = bool(assessment.unhashed_ancestors)
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
        elif assessment.certain_recompute:
            names = ", ".join(assessment.unhashed_ancestors)
            verb = "is stale and has no" if len(assessment.unhashed_ancestors) == 1 else "are stale and have no"
            noun = "ancestor" if len(assessment.unhashed_ancestors) == 1 else "ancestors"
            lines.append(f"RECOMPUTE {assessment.stage}  [{noun} {names} {verb} recorded output hash]")
        elif assessment.stale_if:
            lines.append(f"STALE-IF {assessment.stage}  [if output of {', '.join(assessment.stale_if)} changes]")
        else:
            lines.append(f"HIT      {assessment.stage}")
        if assessment.legacy_rules:
            lines.append(f"         legacy timestamp rule for parents: {', '.join(assessment.legacy_rules)}")
    stale = [a for a in assessments.values() if a.stale]
    certain = [a for a in assessments.values() if not a.stale and a.certain_recompute]
    maybe = [a for a in assessments.values() if not a.stale and not a.certain_recompute and a.stale_if]
    lines.append(f"# {len(assessments)} stages: {len(stale)} stale for own reasons, "
                 f"{len(certain)} will recompute (stale ancestor without recorded output hash), "
                 f"{len(maybe)} may recompute (stale ancestor, early cutoff decides), "
                 f"{len(assessments) - len(stale) - len(certain) - len(maybe)} hits")
    return "\n".join(lines)
