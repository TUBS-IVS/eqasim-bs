"""Vendored copy of synpp 1.6.2 ``run()`` (``synpp/pipeline.py``) with the project's patches.

Each patch is marked ``# PATCH <name>`` and listed in ``braunschweig.synpp_fork.PATCHES``.
Re-base this copy when synpp is upgraded (the SHA guard in ``braunschweig.synpp_fork`` fails
first). Everything the body references that is not patched is imported unchanged from
``synpp.pipeline``, including its own ``rmtree`` wrapper (it also removes write-protected
files on Windows, unlike ``shutil.rmtree``).

Content-addressed validity (spec 2026-10-16, section 4.4), in short: the stages that are stale for
their own reasons come from ``staleness.assess`` (the explainer's function); each executed stage
records the digest of its output (``output_hash``) and of the parents' outputs it was computed from
(``dependency_output_hashes``); when a parent has been re-executed, a child that is not stale on its
own stays cached ONLY if the child's own record holds the parent's new output digest (early cutoff),
otherwise it is devalidated with the reason ``parent_output_changed:<parent>`` (a different digest) or
``parent_output_unrecorded:<parent>`` (no digest recorded, e.g. a legacy record: upstream's behaviour,
logged). A registry with an uncached ephemeral stage runs upstream's eager rule for the whole run.
"""
from __future__ import annotations

import datetime
import inspect
import json
import logging
import os
import pickle

import networkx as nx
from networkx.readwrite.json_graph import node_link_data
from synpp.pipeline import ExecuteContext, PipelineError, ValidateContext, rmtree, update_json

from braunschweig import synpp_tokens
from braunschweig.synpp_deterministic import process_stages  # PATCH deterministic-propagation
from braunschweig.synpp_fork import output_hash as _output_hash  # PATCH content-addressed-validity
from braunschweig.synpp_fork import staleness as _staleness  # PATCH content-addressed-validity


def get_stage_hash(descriptor):
    # PATCH semantic-module-hash: AST digest instead of md5 of the raw text (spec 4.2).
    return synpp_tokens.semantic_module_digest(inspect.getsource(descriptor))


# PATCH content-addressed-validity: the two helpers below belong to the run() patch further down.
def _output_exclusions(stage):
    """The stage's ``_OUTPUT_HASH_EXCLUDE`` (default empty), validated before the stage executes.

    ``hash_stage_output`` rejects a malformed value too, but only after the stage ran; a stage
    that is hours long must fail at execution start instead (spec 4.9).
    """
    exclude = getattr(stage["wrapper"].instance, "_OUTPUT_HASH_EXCLUDE", ())
    if not isinstance(exclude, tuple) or not all(isinstance(pattern, str) for pattern in exclude):
        raise TypeError("%s: _OUTPUT_HASH_EXCLUDE must be a tuple of glob strings, got %r" % (
            stage["wrapper"].name, exclude))
    return exclude


def _describe_output(previous, output):
    """One phrase for the log: how a stage's new output relates to the output it replaced."""
    if previous is None or previous.get("digest") is None:
        return "no previous output hash recorded"
    if previous["digest"] == output["digest"]:
        return "identical to the previous output"
    parts = [label for label in ("pickle", "files") if previous.get(label) != output[label]]
    return "changed (%s differ)" % " and ".join(parts) if parts else "changed"


def run(definitions, config = {}, working_directory = None, flowchart_path = None, dryrun = False, verbose = False,
        logger = logging.getLogger("synpp"), rerun_required=True, ensure_working_directory=False,
        externals = {}, aliases = {}):
    # 0) Construct pipeline config
    pipeline_config = {}
    if "processes" in config: pipeline_config["processes"] = config["processes"]
    if "progress_interval" in config: pipeline_config["progress_interval"] = config["progress_interval"]

    if ensure_working_directory and working_directory is None:
        working_directory = '.synpp_cache'
    if working_directory is not None:
        if not os.path.isdir(working_directory) and ensure_working_directory:
            logger.warning("Working directory does not exist, it will be created: %s" % working_directory)
            os.mkdir(working_directory)

        working_directory = os.path.realpath(working_directory)

    # 1) Construct stage registry
    registry = process_stages(definitions, config, externals, aliases)

    required_hashes = [None] * len(definitions)
    for stage in registry.values():
        if "required-index" in stage:
            required_hashes[stage["required-index"]] = stage["hash"]

    logger.info("Found %d stages" % len(registry))

    # 2) Order stages
    graph = nx.DiGraph()
    flowchart = nx.MultiDiGraph() # graph to later plot

    for hash in registry.keys():
        graph.add_node(hash)

    for stage in registry.values():
        stage_name = stage['descriptor']

        if not flowchart.has_node(stage_name):
            flowchart.add_node(stage_name)

        for hash in stage["dependencies"]:
            graph.add_edge(hash, stage["hash"])

            dependency_name = registry.get(hash)['descriptor']
            if not flowchart.has_edge(dependency_name, stage_name):
                flowchart.add_edge(dependency_name, stage_name)

    # Write out flowchart
    if not flowchart_path is None:
        flowchart_directory = os.path.dirname(os.path.abspath(flowchart_path))
        if not os.path.isdir(flowchart_directory):
            raise PipelineError("Flowchart directory does not exist: %s" % flowchart_directory)

        logger.info("Writing pipeline flowchart to : {}".format(flowchart_path))
        with open(flowchart_path, 'w') as outfile:
            json.dump(node_link_data(flowchart, edges = "edges"), outfile)

    if dryrun:
        return node_link_data(flowchart, edges = "edges")

    for cycle in nx.cycles.simple_cycles(graph):
        cycle = [registry[hash]["hash"] for hash in cycle] # TODO: Make more verbose
        raise PipelineError("Found cycle: %s" % " -> ".join(cycle))

    sorted_hashes = list(nx.topological_sort(graph))

    # Check where cache is available
    cache_available = set()

    if not working_directory is None:
        for hash in sorted_hashes:
            directory_path = "%s/%s.cache" % (working_directory, hash)
            file_path = "%s/%s.p" % (working_directory, hash)

            if os.path.exists(directory_path) and os.path.exists(file_path):
                cache_available.add(hash)
                registry[hash]["ephemeral"] = False

    # Set up ephemeral stage counts
    ephemeral_counts = {}

    for stage in registry.values():
        for hash in stage["dependencies"]:
            dependency = registry[hash]

            if dependency["ephemeral"] and not hash in cache_available:
                if not hash in ephemeral_counts:
                    ephemeral_counts[hash] = 0

                ephemeral_counts[hash] += 1

    # 3) Load information about stages
    meta = {}

    if not working_directory is None:
        try:
            with open("%s/pipeline.json" % working_directory) as f:
                meta = json.load(f)
                logger.info("Found pipeline metadata in %s/pipeline.json" % working_directory)
        except FileNotFoundError:
            logger.info("Did not find pipeline metadata in %s/pipeline.json" % working_directory)

    # PATCH content-addressed-validity (spec 4.4, 4.9, 4.10)
    # 4) Devalidate stages for their own reasons. The same function drives
    # scripts/explain_stale_stages.py (braunschweig.synpp_fork.staleness.assess), so the pre-run
    # report and this run cannot disagree. It replaces upstream steps 4.1-4.8. Upstream step 4.9
    # (devalidate every descendant of a stale stage before anything executes) is replaced by the
    # per-child decision of step 6; step 4.10 (ephemeral parents) is kept.
    ephemeral_present = any(stage.get("ephemeral") for stage in registry.values())
    if ephemeral_present:
        logger.warning("Ephemeral stages present: early cutoff is disabled, upstream's eager "
                       "devalidation of every descendant applies to this run")

    stale_hashes = set()
    reasons = {}

    if working_directory is None:
        # Upstream: without a working directory nothing is cached and every stage executes.
        for hash in sorted_hashes:
            is_required = rerun_required and hash in required_hashes
            if hash not in ephemeral_counts or is_required:
                stale_hashes.add(hash)
                reasons[hash] = (["required"] if is_required else []) + ["no_working_directory"]
    else:
        # assess() skips an uncached ephemeral stage exactly like upstream's sorted_cached_hashes
        # does (it gives such a stage no own reason), so no ephemeral_counts guard is needed here.
        assessments = _staleness.assess(registry, working_directory, meta, rerun_required=rerun_required)

        for hash in sorted_hashes:
            assessment = assessments[hash]

            if assessment.stale:
                stale_hashes.add(hash)
                reasons[hash] = list(assessment.reasons)

            for parent_name in assessment.legacy_rules:
                logger.info("legacy timestamp rule for %s against parent %s (the child's or the "
                            "parent's record carries no output hash)" % (registry[hash]["wrapper"].name, parent_name))

    if ephemeral_present:
        # Upstream's eager rule (4.9), applied to the whole run: early cutoff is not reasoned
        # through for registries with ephemeral stages, behaviour there equals upstream exactly.
        for hash in set(stale_hashes):
            for descendant_hash in nx.descendants(graph, hash):
                if not descendant_hash in stale_hashes:
                    stale_hashes.add(descendant_hash)
                    reasons[descendant_hash] = ["ancestor_stale (eager rule)"]

    # 4.10) Devalidate ephemeral stages if necessary (upstream, plus the recorded reason)
    pending = set(stale_hashes)

    while len(pending) > 0:
        for dependency_hash in registry[pending.pop()]["dependencies"]:
            if registry[dependency_hash]["ephemeral"]:
                if not dependency_hash in stale_hashes:
                    reasons[dependency_hash] = ["ephemeral_parent_of_stale_stage"]
                    pending.add(dependency_hash)

                stale_hashes.add(dependency_hash)

    # A malformed output-hash exclusion is a bug in the stage: fail before anything executes and
    # before any cache is touched, not hours later inside the hashing of an already executed stage.
    output_exclusions = {}
    for hash in sorted_hashes:
        if hash in stale_hashes:
            output_exclusions[hash] = _output_exclusions(registry[hash])

    # The reasons of these stages are final; any later devalidation is decided per child in step 6.
    own_stale = set(stale_hashes)

    logger.info("Stale before execution: %d stages, decided for their own reasons" % len(stale_hashes))
    for hash in sorted(stale_hashes):
        logger.info("- %s [%s]" % (hash, ", ".join(reasons.get(hash, []))))

    # 5) Reset meta information. What the stale stages recorded is kept in memory first: the digest
    # of their previous output (diagnostics) and the parent digests a child recorded, which are the
    # exact claim step 6 compares a re-executed parent's new output against.
    previous_output = {hash: meta[hash].get("output_hash") for hash in meta}
    recorded_inputs = {hash: dict(meta[hash].get("dependency_output_hashes") or {}) for hash in meta}

    for hash in stale_hashes:
        if hash in meta:
            del meta[hash]

    if not working_directory is None:
        update_json(meta, working_directory)

    logger.info("Successfully reset meta data")

    # 6) Execute stages. A child of a re-executed parent is devalidated only when the parent's new
    # output digest differs from the digest the CHILD recorded for that parent (early cutoff).
    results = [None] * len(definitions)
    cache = {}

    progress = 0
    executed = []

    def _mark_stale(child_hash, reason):
        stale_hashes.add(child_hash)
        child_reasons = reasons.setdefault(child_hash, [])
        if not reason in child_reasons:
            child_reasons.append(reason)
        meta.pop(child_hash, None)

    for hash in sorted_hashes:
        if hash in stale_hashes:
            logger.info("Executing stage %s ..." % hash)
            logger.info("Reasons for %s: %s" % (hash, ", ".join(reasons.get(hash, [])) or "none recorded"))
            stage = registry[hash]

            if not hash in output_exclusions:
                # devalidated during this run, so it was not covered by the check before step 5
                output_exclusions[hash] = _output_exclusions(stage)

            # Load the dependencies, either from cache or from file
            #stage_dependencies = []
            #stage_dependency_info = {}

            #if name in dependencies:
            #    stage_dependencies = dependencies[name]
            #
            #    for parent in stage_dependencies:
            #        stage_dependency_info[parent] = meta[parent]["info"]
            #stage_dependencies =

            stage_dependency_info = {}
            for dependency_hash in stage["dependencies"]:
                stage_dependency_info[dependency_hash] = meta[dependency_hash]["info"]

            # Prepare cache path
            cache_path = "%s/%s.cache" % (working_directory, hash)

            if not working_directory is None:
                if os.path.exists(cache_path):
                    rmtree(cache_path)
                os.mkdir(cache_path)

            context = ExecuteContext(stage["config"], stage["required_stages"], stage["aliases"], working_directory, stage["dependencies"], cache_path, pipeline_config, logger, cache, stage_dependency_info)
            result = stage["wrapper"].execute(context)
            validation_token = stage["wrapper"].validate(ValidateContext(stage["config"], cache_path))

            if hash in required_hashes:
                results[required_hashes.index(hash)] = result

            if working_directory is None:
                cache[hash] = result
                output = None
            else:
                with open("%s/%s.p" % (working_directory, hash), "wb+") as f:
                    logger.info("Writing cache for %s" % hash)
                    pickle.dump(result, f, protocol=4)

                started = datetime.datetime.now()
                output = _output_hash.hash_stage_output(working_directory, hash, output_exclusions[hash])
                logger.info("Output hash for %s: %d files, %d bytes, %.1f s; %s" % (
                    hash, output["n_files"], output["bytes"], (datetime.datetime.now() - started).total_seconds(),
                    _describe_output(previous_output.get(hash), output)
                ))

            # The digests of the parents this stage was computed from (a parent without a recorded
            # digest leaves no entry: the child then falls back to the legacy timestamp rule).
            dependency_output_hashes = {}
            for dependency_hash in stage["dependencies"]:
                dependency_digest = (meta[dependency_hash].get("output_hash") or {}).get("digest")
                if not dependency_digest is None:
                    dependency_output_hashes[dependency_hash] = dependency_digest

            # Update meta information
            meta[hash] = {
                "config": stage["config"],
                "updated": datetime.datetime.now(datetime.timezone.utc).timestamp(),
                "dependencies": {
                    dependency_hash: meta[dependency_hash]["updated"] for dependency_hash in stage["dependencies"]
                },
                "dependency_output_hashes": dependency_output_hashes,
                "info": context.stage_info,
                "validation_token": validation_token,
                "module_hash": stage["wrapper"].module_hash,
                "output_hash": output,
                "stale_reasons": list(reasons.get(hash, [])),
                "fork": {"format": 1}
            }

            # Early cutoff: decide every child that is not already stale from the digest it recorded.
            if not ephemeral_present:  # otherwise the eager rule already marked every descendant
                for child_hash in graph.successors(hash):
                    if child_hash in own_stale:
                        continue

                    recorded = recorded_inputs.get(child_hash, {}).get(hash)

                    if not recorded is None and not output is None and recorded == output["digest"]:
                        if not child_hash in stale_hashes:
                            # not (yet) devalidated by another parent: keep it and make its record
                            # coherent with the parent that was just rebuilt, also for the timestamp rule
                            meta[child_hash]["dependencies"][hash] = meta[hash]["updated"]
                            logger.info("Output of %s unchanged: %s not devalidated by it" % (hash, child_hash))
                    else:
                        reason = "%s:%s" % (
                            "parent_output_unrecorded" if recorded is None else "parent_output_changed",
                            stage["wrapper"].name
                        )
                        if not child_hash in stale_hashes:
                            logger.info("Devalidating %s (%s)" % (child_hash, reason))
                        _mark_stale(child_hash, reason)

            if not working_directory is None:
                update_json(meta, working_directory)

            # Clear cache for ephemeral stages if they are no longer needed
            if not working_directory is None:
                for dependency_hash in stage["dependencies"]:
                    if dependency_hash in ephemeral_counts:
                        ephemeral_counts[dependency_hash] -= 1

                        if ephemeral_counts[dependency_hash] == 0:
                            cache_directory_path = "%s/%s.cache" % (working_directory, dependency_hash)
                            cache_file_path = "%s/%s.p" % (working_directory, dependency_hash)

                            rmtree(cache_directory_path)
                            os.remove(cache_file_path)

                            logger.info("Removed ephemeral %s." % dependency_hash)
                            del ephemeral_counts[dependency_hash]

            logger.info("Finished running %s." % hash)

            progress += 1
            executed.append(hash)
            logger.info("Pipeline progress: %d/%d (%.2f%%)" % (
                progress, len(stale_hashes), 100 * progress / len(stale_hashes)
            ))

    # End of run summary: what executed and why, and what early cutoff kept cached. Every descendant
    # of a stage that was stale before the run would have been re-executed by upstream; those that
    # did not execute were kept.
    spared = set()
    for hash in own_stale:
        spared.update(nx.descendants(graph, hash))
    spared -= stale_hashes

    logger.info("Executed %d stages; early cutoff kept %d stages cached" % (len(executed), len(spared)))
    for hash in executed:
        logger.info("- executed %s [%s]" % (hash, ", ".join(reasons.get(hash, []))))
    for hash in sorted(spared):
        logger.info("- kept cached %s" % hash)
    # END PATCH content-addressed-validity

    if not rerun_required:
        # Load remaining previously cached results
        for hash in required_hashes:
            if results[required_hashes.index(hash)] is None:
                with open("%s/%s.p" % (working_directory, hash), "rb") as f:
                    logger.info("Loading cache for %s ..." % hash)
                    results[required_hashes.index(hash)] = pickle.load(f)

    if verbose:
        info = {}

        for hash in sorted(meta.keys()):
            info.update(meta[hash]["info"])

        return {
            "results": results,
            "stale": stale_hashes,
            "info": info,
            "flowchart": node_link_data(flowchart, edges = "edges")
        }
    else:
        return results
