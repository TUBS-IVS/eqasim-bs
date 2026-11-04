"""Ordering-only constraints in the fork's run() (patch ``order-after``, ADR-0177).

Bug C4 of the production run prod100_20261031: the analysis stages read the MATSim archive that
``matsim.output`` writes, but the synpp graph had no edge between them, so synpp's layer-by-layer
order executed the analysis BEFORE the simulation and its MATSim parts were skipped with a warning.
A stage that declares ``_ORDER_AFTER_STAGES`` must execute after the named stage whenever both are
in one invocation, and must not pull that stage in otherwise (issue #354).
"""
import pytest

from tests.test_synpp_fork_early_cutoff import Pipeline

# Shape of the production graph: the analysis hangs below the synthesis output only, the archive
# producer sits at the end of a deeper simulation chain.
GRAPH = {
    "c4_synth": (),
    "c4_prepare": ("c4_synth",),
    "c4_run": ("c4_prepare",),
    "c4_output": ("c4_run",),
    "c4_analysis": ("c4_synth",),
}

# The producer writes the archive sentinel the way matsim.output does.
OUTPUT = """
from pathlib import Path
def configure(context):
    context.config("trace")
    context.config("c4_output_path")
    context.stage("c4_run")
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("{name}\\n")
    archive = Path(context.config("c4_output_path")) / "matsim_output"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "output_events.xml.gz").write_bytes(b"events")
    return context.stage("c4_run")
"""

# The consumer resolves the archive with the production resolver and returns whether it found it.
ANALYSIS = """
from braunschweig.analysis.matsim_archive import resolve_matsim_archive
_ORDER_AFTER_STAGES = {declaration}
def configure(context):
    context.config("trace")
    context.config("c4_output_path")
    context.stage("c4_synth")
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("{name}\\n")
    return resolve_matsim_archive(context.config("c4_output_path")) is not None
"""


def _pipeline(tmp_path, monkeypatch, declaration='("c4_output",)', graph=GRAPH, extra_overrides=None):
    overrides = {"c4_output": OUTPUT,
                 "c4_analysis": ANALYSIS.replace("{declaration}", declaration)}
    overrides.update(extra_overrides or {})
    pipe = Pipeline(tmp_path, monkeypatch, graph, overrides=overrides)
    pipe.config["c4_output_path"] = str(tmp_path / "output")
    return pipe


def test_combined_invocation_runs_the_archive_reader_after_the_producer(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path, monkeypatch)
    results = pipe.run(targets=["c4_synth", "c4_output", "c4_analysis"], rerun_required=True)["results"]
    events = pipe.events()
    assert events.index("c4_analysis") > events.index("c4_output")
    assert results[2] is True          # the analysis found the archive written in this invocation


def test_analysis_only_invocation_does_not_pull_in_the_producer(tmp_path, monkeypatch, caplog):
    pipe = _pipeline(tmp_path, monkeypatch)
    with caplog.at_level("INFO", logger="synpp"):
        results = pipe.run(targets=["c4_analysis"], rerun_required=True)["results"]
    assert pipe.events() == ["c4_synth", "c4_analysis"]      # #354: no simulation chain
    assert results == [False]                                # no archive: the stage's own skip applies
    assert "c4_output" in caplog.text and "not part of this invocation" in caplog.text


def _archive_reading_stage_modules():
    """Dotted names of every first-party stage module whose static import closure touches the archive.

    A module touches the archive when it imports ``braunschweig.analysis.matsim_archive`` or holds the
    archive directory name as a string constant. The closure follows module- and function-level
    imports (the analysis stages import lazily), relative ones included, within the first-party roots
    of tests/test_synpp_helper_hash_invariant.py. The producer itself is not a reader.

    About 4.5 s: parsing the 694 first-party files (3 s) plus the closure walk; a scan of every stage
    module is the point (a reader outside the committed run lists must be caught too), so it cannot
    be narrowed to one registry.
    """
    import ast

    from braunschweig.analysis.matsim_archive import ARCHIVE_DIR_NAME, ARCHIVE_PRODUCER_STAGE
    from tests.test_synpp_helper_hash_invariant import (
        _dotted_name_for, _is_stage_tree, _iter_first_party_python_files)

    modules = {}
    for path in _iter_first_party_python_files():
        modules[_dotted_name_for(path)] = (ast.parse(path.read_text(encoding="utf-8")), path.name == "__init__.py")

    import functools

    @functools.lru_cache(maxsize=None)
    def imported(name):
        tree, is_package = modules[name]
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = name.split(".") if is_package else name.split(".")[:-1]
                    package = package[:len(package) - (node.level - 1)]
                    base = ".".join(package + ([node.module] if node.module else []))
                found.add(base)
                found.update("%s.%s" % (base, alias.name) for alias in node.names)
        return frozenset(candidate for candidate in found if candidate in modules)

    import re
    # The directory name as a whole path component of any string constant or f-string part:
    # "matsim_output", "%s/matsim_output" (the producer's idiom), f"{path}/matsim_output/x".
    component = re.compile(r"(^|[/\\])%s([/\\]|$)" % re.escape(ARCHIVE_DIR_NAME))

    @functools.lru_cache(maxsize=None)
    def touches(name):
        tree, _ = modules[name]
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docstrings.add(id(node.body[0].value))     # prose that mentions the path reads nothing
        return any(isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                   and component.search(node.value) for node in ast.walk(tree))

    archive_module = "braunschweig.analysis.matsim_archive"
    readers = set()
    for name, (tree, _) in modules.items():
        if not _is_stage_tree(tree) or name == ARCHIVE_PRODUCER_STAGE:
            continue
        seen, pending = set(), [name]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(imported(current) - seen)
        if archive_module in seen or any(touches(module) for module in seen):
            readers.add(name)
    return readers


def test_every_stage_that_touches_the_archive_declares_the_producer():
    """Every stage module of the repository (not only those of one run list) whose import closure reads
    the MATSim archive must carry the ordering constraint, or a combined invocation runs it before the
    archive is written (bug C4)."""
    import importlib

    from braunschweig.analysis.matsim_archive import ARCHIVE_PRODUCER_STAGE

    readers = _archive_reading_stage_modules()
    assert readers >= {"braunschweig.analysis.analysis_suite", "braunschweig.analysis.simwrapper_export"}
    missing = sorted(name for name in readers
                     if ARCHIVE_PRODUCER_STAGE not in getattr(importlib.import_module(name), "_ORDER_AFTER_STAGES", ()))
    assert missing == []


@pytest.fixture(scope="module")
def production_registry():
    # About 20 s: configure() of all 108 stages of the committed 100 % configuration. Only the real
    # registry shows the real layer depth of the stages and the production names (aliases) the
    # constraint has to match, which is where the bug lived; built once for this module.
    from braunschweig.synpp_fork import registry
    return registry.build_registry(str(registry.REPO / "configs" / "base_bs.yml"),
                                   str(registry.REPO / "configs" / "overlays" / "test_100pct.yml"))


def test_production_run_list_executes_every_archive_reader_after_the_archive_producer(production_registry):
    """The committed 100 % run list is the run list of prod100_20261031 launch 1 (synthesis, simulation
    and analysis in one invocation). On the real registry, with its production names and aliases, the
    fork must order every stage that declares the producer after it."""
    import networkx as nx

    from braunschweig.analysis.matsim_archive import ARCHIVE_PRODUCER_STAGE
    from braunschweig.synpp_fork.pipeline import execution_order

    readers = [h for h, stage in production_registry.items()
               if ARCHIVE_PRODUCER_STAGE in getattr(stage["wrapper"].instance, "_ORDER_AFTER_STAGES", ())]
    assert {production_registry[h]["wrapper"].name for h in readers} >= {
        "braunschweig.analysis.analysis_suite", "braunschweig.analysis.simwrapper_export"}

    graph = nx.DiGraph()
    graph.add_nodes_from(production_registry)
    for stage in production_registry.values():
        for dependency_hash in stage["dependencies"]:
            graph.add_edge(dependency_hash, stage["hash"])
    order = execution_order(production_registry, graph)
    position = {stage_hash: index for index, stage_hash in enumerate(order)}
    producer = next(h for h, stage in production_registry.items()
                    if stage["wrapper"].name == ARCHIVE_PRODUCER_STAGE)
    assert all(position[reader] > position[producer] for reader in readers)


CYCLE = dict(GRAPH, c4_late=("c4_analysis",))


@pytest.mark.parametrize("declaration, graph, targets, error", [
    ('"c4_output"', GRAPH, ["c4_output", "c4_analysis"], TypeError),          # a string, not a tuple
    ('("c4_late",)', CYCLE, ["c4_late"], "PipelineError"),                    # after its own descendant
])
def test_an_invalid_declaration_fails_before_any_stage_executes(tmp_path, monkeypatch, declaration, graph,
                                                                targets, error):
    from synpp.pipeline import PipelineError
    pipe = _pipeline(tmp_path, monkeypatch, declaration=declaration, graph=graph)
    with pytest.raises(PipelineError if error == "PipelineError" else error, match="_ORDER_AFTER_STAGES"):
        pipe.run(targets=targets, rerun_required=True)
    assert pipe.events() == []
