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

Ephemeral stages (spec 4.4): synpp 1.6.2 never validates an UNCACHED ephemeral stage on its own
(it is outside ``sorted_cached_hashes``); it recomputes it only when a descendant is stale (step
4.10). ``assess`` therefore gives such a stage no reasons and sets ``ephemeral_uncached``; it
is not unexpected, and it passes the staleness of its own ancestors on to its children like any
other valid stage. ``format_report`` warns when the registry contains ephemeral stages, because
the fork disables early cutoff for such runs.

``EARLY_CUTOFF_ACTIVE`` is False while only synpp 1.6.2 executes the pipeline: then a ``stale_if``
stage recomputes like every other descendant of a stale stage, and the ``STALE-IF`` label says so.
The fork's own run() (Phase 4) decides children from parent output digests and flips it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx
from synpp.pipeline import ValidateContext

# False while synpp 1.6.2 executes the pipeline (it recomputes every descendant of a stale stage);
# Phase 4 sets it True when the fork's run() decides children from parent output hashes.
EARLY_CUTOFF_ACTIVE = False


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
    # Parallel to stale_if / unhashed_ancestors (same order): stage names are ambiguous for two
    # parameterised variants of one stage, the hashes are not.
    stale_if_hashes: list = field(default_factory=list)
    unhashed_ancestor_hashes: list = field(default_factory=list)
    certain_recompute: bool = False
    config_diff: dict = field(default_factory=dict)
    ephemeral: bool = False
    ephemeral_uncached: bool = False

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


def _add_ancestor(assessment, name: str, stage_hash: str, *, unhashed: bool) -> None:
    """Record a stale ancestor (and, when it has no recorded digest, as unhashed); names stay
    parallel to hashes and are not duplicated."""
    if stage_hash not in assessment.stale_if_hashes:
        assessment.stale_if.append(name)
        assessment.stale_if_hashes.append(stage_hash)
    if unhashed and stage_hash not in assessment.unhashed_ancestor_hashes:
        assessment.unhashed_ancestors.append(name)
        assessment.unhashed_ancestor_hashes.append(stage_hash)


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
        assessment.ephemeral = stage.get("ephemeral") is True
        # synpp 1.6.2 (run(), ephemeral_counts / sorted_cached_hashes) leaves an ephemeral stage
        # that has dependents and no cache artifacts out of every own-reason check; only a stale
        # descendant makes it recompute (step 4.10).
        assessment.ephemeral_uncached = (
            assessment.ephemeral and not assessment.cache_present and graph.out_degree(stage_hash) > 0
            and not (rerun_required and "required-index" in stage))
        if not assessment.ephemeral_uncached:
            _assess_own_reasons(assessment, stage, stage_hash, name_of, working_directory, meta, out,
                                rerun_required)
        if not assessment.reasons:
            for dependency in stage.get("dependencies", []):
                if out[dependency].stale:
                    # A stale dependency is itself the ancestor; what it inherited from further
                    # up is cut off by its own output digest. A stage that skipped its own checks
                    # (uncached ephemeral) has not recorded it yet.
                    if assessment.ephemeral_uncached:
                        _add_ancestor(assessment, name_of[dependency], dependency,
                                      unhashed=not has_output_hash(meta.get(dependency)))
                    continue
                parent = out[dependency]
                for name, ancestor in zip(parent.stale_if, parent.stale_if_hashes):
                    # An ancestor stays "unhashed" through a valid intermediate only when that
                    # intermediate has no recorded digest of its own to stop the propagation
                    # (spec 4.4 loop), or is an uncached ephemeral stage (no early cutoff there).
                    unhashed = ancestor in parent.unhashed_ancestor_hashes and (
                        parent.ephemeral_uncached or not has_output_hash(meta.get(dependency)))
                    _add_ancestor(assessment, name, ancestor, unhashed=unhashed)
            assessment.certain_recompute = bool(assessment.unhashed_ancestors)
        out[stage_hash] = assessment
    return out


def _assess_own_reasons(assessment, stage, stage_hash, name_of, working_directory, meta, out,
                        rerun_required) -> None:
    """Fill ``reasons`` / ``legacy_rules`` / direct ``stale_if`` / ``config_diff`` for a stage that
    synpp validates on its own (everything except an uncached ephemeral stage)."""
    if rerun_required and "required-index" in stage:
        assessment.reasons.append("required")
    if not assessment.in_meta:
        assessment.reasons.append("not_in_meta")
    if not assessment.cache_present:
        assessment.reasons.append("cache_missing")
    if not assessment.in_meta:
        assessment.config_diff = nearest_config_diff(name_of[stage_hash], stage["config"], meta)
        return
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
    if assessment.reasons:
        return
    for dependency in stage.get("dependencies", []):
        if out[dependency].stale:
            _add_ancestor(assessment, name_of[dependency], dependency,
                          unhashed=not has_output_hash(meta.get(dependency)))
            continue
        if dependency not in meta:
            # Only an uncached ephemeral parent can be valid yet absent from meta. synpp step 4.6
            # devalidates a child whose recorded parent has no meta record.
            assessment.reasons.append("meta_malformed:parent_missing:" + name_of[dependency])
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


def unexpected(assessments: dict, expected_names: set) -> list:
    """Stages stale for own reasons other than ``required`` and not in ``expected_names``.

    An uncached ephemeral stage has no own reasons (synpp never validates it) and never counts."""
    return sorted(a.stage for a in assessments.values()
                  if a.stale and a.reasons != ["required"] and a.stage not in expected_names)


def format_report(assessments: dict) -> str:
    lines = []
    for assessment in sorted(assessments.values(), key=lambda a: a.stage):
        if assessment.ephemeral_uncached:
            lines.append(f"EPHEMERAL {assessment.stage}  "
                         "[not cached; synpp recomputes it only if a descendant runs]")
        elif assessment.stale:
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
            names = ", ".join(assessment.stale_if)
            if EARLY_CUTOFF_ACTIVE:
                lines.append(f"STALE-IF {assessment.stage}  [if output of {names} changes]")
            else:
                lines.append(f"STALE-IF {assessment.stage}  [recomputes under synpp 1.6.2; "
                             f"the fork would reuse it if {names} output is unchanged]")
        else:
            lines.append(f"HIT      {assessment.stage}")
        if assessment.legacy_rules:
            lines.append(f"         legacy timestamp rule for parents: {', '.join(assessment.legacy_rules)}")
    stale = [a for a in assessments.values() if a.stale]
    uncached = [a for a in assessments.values() if a.ephemeral_uncached]
    live = [a for a in assessments.values() if not a.stale and not a.ephemeral_uncached]
    certain = [a for a in live if a.certain_recompute]
    maybe = [a for a in live if not a.certain_recompute and a.stale_if]
    ephemeral = sum(a.ephemeral for a in assessments.values())
    if ephemeral:
        lines.append(f"# WARNING: {ephemeral} ephemeral stage(s) in the registry; "
                     "the fork disables early cutoff for such runs (spec 4.4)")
    if EARLY_CUTOFF_ACTIVE:
        maybe_note = "stale ancestor, early cutoff decides"
    else:
        maybe_note = "stale ancestor; synpp 1.6.2 recomputes them, only the fork's early cutoff may spare them"
    summary = (f"# {len(assessments)} stages: {len(stale)} stale for own reasons, "
               f"{len(certain)} will recompute (stale ancestor without recorded output hash), "
               f"{len(maybe)} may recompute ({maybe_note}), "
               f"{len(live) - len(certain) - len(maybe)} hits")
    if uncached:
        summary += f", {len(uncached)} uncached ephemeral"
    lines.append(summary)
    return "\n".join(lines)
