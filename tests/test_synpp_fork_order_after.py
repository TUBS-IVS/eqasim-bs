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
    and analysis in one invocation). Every stage there that resolves the archive must declare the
    producer, and the fork must order it after the producer."""
    import inspect

    import networkx as nx

    from braunschweig.analysis.matsim_archive import ARCHIVE_PRODUCER_STAGE
    from braunschweig.synpp_fork.pipeline import execution_order

    readers = [h for h, stage in production_registry.items()
               if "resolve_matsim_archive(" in inspect.getsource(stage["wrapper"].instance)]
    assert {production_registry[h]["wrapper"].name for h in readers} >= {
        "braunschweig.analysis.analysis_suite", "braunschweig.analysis.simwrapper_export"}
    for stage_hash in readers:
        assert ARCHIVE_PRODUCER_STAGE in getattr(production_registry[stage_hash]["wrapper"].instance,
                                                 "_ORDER_AFTER_STAGES", ()), stage_hash

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
