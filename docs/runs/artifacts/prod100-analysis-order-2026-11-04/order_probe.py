"""Probe (C4, ADR-0177): execution order of the prod100_20261031 run list under the vendored synpp run().

Builds the production registry (base_bs.yml + overlays/test_100pct.yml, run list replaced by the
launch-1 run list of prod100_20261031) exactly like the launcher's pre-run assessment does, then
reproduces the ordering step synpp used before patch order-after (nx.topological_sort over
the dependency graph) and prints the position and Kahn generation of the relevant stages.
"""
import sys
import time

REPO = str(__import__("pathlib").Path(__file__).resolve().parents[4])
sys.path.insert(0, REPO)

import networkx as nx  # noqa: E402

from braunschweig.synpp_fork import registry as reg  # noqa: E402

RUN_LIST = [
    "synthesis.output",
    "matsim.output",
    "braunschweig.analysis.cordon_validation",
    "braunschweig.analysis.simwrapper_export",
    "braunschweig.analysis.analysis_suite",
    "braunschweig.analysis.synthesis.plan_structure_vs_srv",
    "braunschweig.analysis.synthesis.departure_time_vs_srv",
    "braunschweig.analysis.verbindungen_validation",
    "braunschweig.analysis.synthesis.commute_distance_by_kreis",
    "braunschweig.analysis.synthesis.work_participation_by_kreis",
]

started = time.time()
settings = reg.load_settings(REPO + "/configs/base_bs.yml", REPO + "/configs/overlays/test_100pct.yml")
settings["run"] = RUN_LIST
registry = reg.build_registry_from_settings(settings)
print("registry built: %d stages in %.1f s" % (len(registry), time.time() - started))

graph = nx.DiGraph()
for stage_hash in registry:
    graph.add_node(stage_hash)
for stage in registry.values():
    for dependency_hash in stage["dependencies"]:
        graph.add_edge(dependency_hash, stage["hash"])

order = list(nx.topological_sort(graph))
generation_of = {}
for index, generation in enumerate(nx.topological_generations(graph)):
    for stage_hash in generation:
        generation_of[stage_hash] = index

watched = ("synthesis.output", "braunschweig.analysis.simwrapper_export", "braunschweig.analysis.analysis_suite",
           "matsim.simulation.prepare", "matsim.simulation.run", "matsim.output")
for position, stage_hash in enumerate(order):
    name = registry[stage_hash]["wrapper"].name
    if name in watched or position >= len(order) - 12 or "output" in name or "prepare" in name:
        print("position %3d / %d  generation %2d  %s  (descriptor %r)" % (
            position, len(order), generation_of[stage_hash], name, registry[stage_hash]["descriptor"]))
print("descriptor example:", repr(registry[order[-1]]["descriptor"]), "| name:", registry[order[-1]]["wrapper"].name)
