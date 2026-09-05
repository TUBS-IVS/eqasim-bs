"""Deterministic stage hashing for synpp 1.6.2 (monkeypatch of ``synpp.pipeline.process_stages``).

Why this module exists
----------------------
synpp names every cache entry ``<stage>__<hash>`` where the hash covers the stage's own config
values PLUS the config values of all its upstream stages ("implicit config parameters"), so that
changing an upstream option devalidates the downstream cache. synpp 1.6.2 computes that implicit
set by walking the stage graph from ``list(set_of_source_hashes)`` and following only
``stage["downstream"][0]``. Both choices make the result depend on the iteration order of a set
of hash strings, i.e. on Python's per-process string-hash randomisation (``PYTHONHASHSEED``), and
on which of several downstream paths happens to be walked. The same code with the same config
therefore yields different stage hashes in different processes (measured 2026-09-05 on
``configs/base_bs.yml`` + ``configs/overlays/test_25pct.yml``: five stages changed hash between
``PYTHONHASHSEED`` 1, 2 and 3; ``braunschweig.locations.synthesis.replacement_education_gravity``
carried 119 propagated keys in one process and 70 in another). Every such change is a spurious
cache miss: the shared 100 % cache accumulated nine hash variants of ``braunschweig.popsim.stage``
and five of ``replacement_education_gravity`` with byte-identical payloads.

What the patch does
-------------------
:func:`process_stages` is a verbatim copy of synpp 1.6.2's function except for the block marked
``# --- deterministic propagation ---``, which is replaced by :func:`propagate_implicit_config`:
upstream stages are processed before downstream stages (Kahn's topological order with sorted
tie-breaks), every downstream edge is followed, and conflicting propagated values raise instead
of being asserted away. The propagated set is thus the complete, order-independent closure that
synpp's algorithm approximates. Stage semantics (identification hashes, cycle detection, ephemeral
handling, ``hash_name``) are unchanged.

Consequence: hashes computed with the patch can differ from the ones an unpatched run produced,
so the FIRST run after installing the patch may recompute stages whose hash changes (a one-time
cost, after which hashes are stable across processes). Use ``scripts/report_stage_hash_impact.py``
to list the affected stages for a given cache directory before running.

How to use
----------
Call :func:`install` BEFORE ``synpp.run``/``synpp.run_from_yaml`` (done by ``scripts/run_synpp.py``
and ``braunschweig.documentation.dag``). Installing from a stage module is too late: synpp is
already inside ``process_stages`` when it imports stage modules. The patch refuses to install on a
synpp version other than the pinned one, because it copies internal code.
"""
from __future__ import annotations

import copy
import importlib.metadata
import logging
from collections import deque

logger = logging.getLogger(__name__)

SUPPORTED_SYNPP_VERSION = "1.6.2"
_LOG_TAG = "[synpp deterministic]"
_original_process_stages = None


class UnsupportedSynppVersion(RuntimeError):
    """The installed synpp is not the version this patch was copied from."""


class ImplicitConfigConflict(RuntimeError):
    """Two upstream stages propagate different values for the same config key."""


class ImplicitConfigCycle(RuntimeError):
    """The dependency graph has a cycle, so no topological order exists."""


def propagate_implicit_config(hashed_stages: dict) -> None:
    """Propagate upstream config keys into downstream stages in topological order (in place).

    ``hashed_stages`` maps identification hash -> stage dict with ``config`` (dict),
    ``volatile_config`` (set), optional ``dependencies`` (list of upstream identification hashes)
    and optional ``downstream`` (list of dicts with ``hash`` and ``passed-parameters``), exactly
    as synpp's ``process_stages`` builds it before the propagation step.

    Rules (identical to synpp 1.6.2's intent): every non-volatile key of an upstream stage's config
    reaches the downstream stage, except keys the downstream callers pass explicitly via
    ``context.stage(descriptor, config={...})`` (they are already part of the callee's identity);
    a key already present downstream must carry the same value. Because upstream stages are
    complete before any downstream stage is processed, one pass yields the full closure.
    """
    dependencies = {key: list(stage.get("dependencies", []) or []) for key, stage in hashed_stages.items()}
    dependents: dict = {key: [] for key in hashed_stages}
    indegree = {}
    for key, upstream_keys in dependencies.items():
        indegree[key] = len(upstream_keys)
        for upstream_key in upstream_keys:
            dependents[upstream_key].append(key)

    ready = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    order = []
    while ready:
        key = ready.popleft()
        order.append(key)
        newly_ready = []
        for dependent in dependents[key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                newly_ready.append(dependent)
        for dependent in sorted(newly_ready):
            ready.append(dependent)
    if len(order) != len(hashed_stages):
        stuck = sorted(key for key, degree in indegree.items() if degree > 0)
        raise ImplicitConfigCycle(
            f"{_LOG_TAG} dependency graph has a cycle; stages never became ready: {stuck[:10]}")

    for key in order:
        stage = hashed_stages[key]
        if not dependencies[key]:
            continue
        passed_config_options: dict = {}
        for upstream_key in dependencies[key]:
            upstream = hashed_stages[upstream_key]
            explicit_config_keys: set = set()
            for info in upstream.get("downstream", []) or []:
                explicit_config_keys |= set(info["passed-parameters"])
            for option in sorted(upstream["config"].keys() - explicit_config_keys):
                if option in upstream["volatile_config"]:
                    continue
                value = upstream["config"][option]
                if option in passed_config_options and passed_config_options[option] != value:
                    raise ImplicitConfigConflict(
                        f"{_LOG_TAG} stage {stage.get('name') or key} receives conflicting values for "
                        f"config option '{option}' from its upstream stages: "
                        f"{passed_config_options[option]!r} vs {value!r}")
                passed_config_options[option] = value
        for option, value in passed_config_options.items():
            if option in stage["config"]:
                if stage["config"][option] != value:
                    raise ImplicitConfigConflict(
                        f"{_LOG_TAG} stage {stage.get('name') or key} requests config option '{option}' = "
                        f"{stage['config'][option]!r} but its upstream stages carry {value!r}")
            else:
                stage["config"][option] = value


def process_stages(definitions, global_config, externals={}, aliases={}):  # noqa: B006 - synpp signature
    """synpp 1.6.2 ``process_stages`` with deterministic implicit-config propagation.

    Verbatim copy of the pinned upstream function except for the marked block; see the module
    docstring. Helpers are imported from ``synpp.pipeline`` so their behaviour stays upstream's.
    """
    from synpp.pipeline import (ConfigurationContext, PipelineError, calculate_identification_hash,
                                flatten, hash_name, resolve_stage)

    pending = copy.copy(definitions)
    hashed_stages = {}

    for index, stage in enumerate(pending):
        stage["required-index"] = index

    global_config = flatten(global_config)

    while len(pending) > 0:
        definition = pending.pop(0)

        # Resolve the underlying code of the stage
        wrapper = resolve_stage(definition["descriptor"], externals, aliases)
        if wrapper is None:
            raise PipelineError(f"{definition['descriptor']} is not a supported object for pipeline stage definition!")

        # A stage that has the same descriptor and is called with the same config
        # must produce the same logic in the configure method and in execution. We
        # hence do not examine the same case twice ...
        identification_hash = calculate_identification_hash(wrapper, definition)

        if identification_hash in hashed_stages:
            # ... but we need to take care of reconstructing the dependency structure
            stage = hashed_stages[identification_hash]
            stage["downstream"] += definition["downstream"]
            continue

        # Call the configure method of the stage and obtain parameters
        config = copy.copy(global_config)

        if "config" in definition:
            config.update(definition["config"])

        # Obtain configuration information through configuration context
        context = ConfigurationContext(config, [d['descriptor'] for d in definitions], externals)
        wrapper.configure(context)
        required_config = flatten(context.required_config)
        definition = copy.copy(definition)
        definition.update({
            "wrapper": wrapper,
            "name": wrapper.name,
            "config": copy.copy(required_config),
            "required_config": copy.copy(required_config),
            "volatile_config": context.volatile_config,
            "required_stages": context.required_stages,
            "aliases": context.aliases
        })

        # Check for cycles
        cycle_hash = hash_name(definition["wrapper"].name, definition["config"], definition["volatile_config"])

        if "cycle_hashes" in definition and cycle_hash in definition["cycle_hashes"]:
            raise PipelineError("Found cycle in dependencies: %s" % definition["wrapper"].name)

        # Everything fine, add
        hashed_stages[identification_hash] = definition
        definition["identification-hash"] = identification_hash

        # Process dependencies
        for position, upstream in enumerate(context.required_stages):
            passed_parameters = set(flatten(upstream["config"]).keys())

            upstream_config = copy.copy(config)
            upstream_config.update(upstream["config"])

            cycle_hashes = copy.copy(definition["cycle_hashes"]) if "cycle_hashes" in definition else []
            cycle_hashes.append(cycle_hash)

            upstream = copy.copy(upstream)
            upstream.update({
                "config": upstream_config,
                "downstream": [
                    {
                        "hash": identification_hash, "position": position,
                        "length": len(context.required_stages), "passed-parameters": passed_parameters
                    }
                ],
                "cycle_hashes": cycle_hashes,
                "ephemeral": context.ephemeral_mask[position] or ("ephemeral" in definition and definition["ephemeral"])
            })
            pending.append(upstream)

    # Now go backwards in the tree to find intermediate config requirements and set up dependencies
    downstream_hashes = set()

    for stage in hashed_stages.values():
        if "downstream" in stage:
            for info in stage["downstream"]:
                downstream_hashes.add(info["hash"])

    source_hashes = sorted(set(hashed_stages.keys()) - downstream_hashes)

    # Connect downstream stages with upstream stages via dependency field
    pending = list(source_hashes)

    while len(pending) > 0:
        stage_hash = pending.pop(0)
        stage = hashed_stages[stage_hash]

        if "downstream" in stage:
            for downstream_info in stage["downstream"]:
                downstream = hashed_stages[downstream_info["hash"]]

                # Connect this stage with the downstream stage
                if not "dependencies" in downstream:
                    downstream["dependencies"] = [None] * downstream_info["length"]

                downstream["dependencies"][downstream_info["position"]] = stage_hash

                pending.append(downstream_info["hash"])

    # --- deterministic propagation (replaces synpp's set-ordered, first-downstream-only walk) ---
    propagate_implicit_config(hashed_stages)
    # --- end of the replaced block ---

    # Hash all stages
    required_hashes = {}

    stages = hashed_stages.values()
    for stage in stages:
        stage["hash"] = hash_name(stage["wrapper"].name, stage["config"], stage["volatile_config"])

        if "required-index" in stage:
            index = stage["required-index"]

            if stage["hash"] in required_hashes:
                assert required_hashes[stage["hash"]] == index
            else:
                required_hashes[stage["hash"]] = index

    # Reset ephemeral stages
    ephemeral_hashes = set([stage["hash"] for stage in stages]) - set([stage["hash"] for stage in stages if not "ephemeral" in stage or not stage["ephemeral"]])
    for stage in stages: stage["ephemeral"] = stage["hash"] in ephemeral_hashes

    # Collapse stages again by hash
    registry = {}

    for stage in stages:
        registry[stage["hash"]] = stage

        stage["dependencies"] = [
            hashed_stages[identification]["hash"] for identification in stage["dependencies"]
        ] if "dependencies" in stage else []

    for hash in required_hashes:
        registry[hash]["required-index"] = required_hashes[hash]

    return registry


def installed_synpp_version() -> str:
    return importlib.metadata.version("synpp")


def original_process_stages():
    """Return synpp's own ``process_stages`` (captured on first import of synpp.pipeline)."""
    global _original_process_stages
    import synpp.pipeline as pipeline
    if _original_process_stages is None:
        _original_process_stages = pipeline.process_stages if pipeline.process_stages is not process_stages else None
    return _original_process_stages


def install(*, force_version_check: bool = True) -> None:
    """Replace ``synpp.pipeline.process_stages`` with the deterministic copy (idempotent).

    Raises :class:`UnsupportedSynppVersion` when the installed synpp is not
    :data:`SUPPORTED_SYNPP_VERSION`: the copy tracks that version's internals, so running an
    unverified pairing silently would defeat the purpose of the patch.
    """
    import synpp.pipeline as pipeline

    version = installed_synpp_version()
    if force_version_check and version != SUPPORTED_SYNPP_VERSION:
        raise UnsupportedSynppVersion(
            f"{_LOG_TAG} installed synpp {version} != supported {SUPPORTED_SYNPP_VERSION}; "
            "re-verify braunschweig/synpp_deterministic.py against the new upstream process_stages "
            "before bumping SUPPORTED_SYNPP_VERSION")
    original_process_stages()  # capture upstream's function before replacing it
    if pipeline.process_stages is process_stages:
        return
    pipeline.process_stages = process_stages
    logger.info("%s patched synpp %s process_stages: implicit config propagation now topological and "
                "order-independent (stage hashes stable across processes)", _LOG_TAG, version)
