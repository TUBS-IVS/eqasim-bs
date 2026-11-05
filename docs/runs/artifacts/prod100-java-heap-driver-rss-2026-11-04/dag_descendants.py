"""Which stages sit below a stage in the committed production DAG (read-only; docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml).

Usage (from the repository root, in the pipeline environment):
    python docs/runs/artifacts/prod100-java-heap-driver-rss-2026-11-04/dag_descendants.py <ancestor node> <module> [<module> ...]

Reads docs/registry/dag/production.json (the resolved configs/base_bs.yml + configs/overlays/test_100pct.yml) and
the ``aliases:`` block of configs/base_bs.yml, which maps a DAG node to the module that implements it. Prints the
number of descendants of the ancestor node and, for each given module, its DAG node and whether it is a descendant.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]


def main(ancestor, modules):
    dag = json.loads((REPO / "docs" / "registry" / "dag" / "production.json").read_text(encoding="utf-8"))
    aliases = yaml.safe_load((REPO / "configs" / "base_bs.yml").read_text(encoding="utf-8")).get("aliases") or {}
    node_of_module = {module: node for node, module in aliases.items()}
    children = defaultdict(set)
    for parent, child in dag["edges"]:
        children[parent].add(child)

    below, pending = set(), [ancestor]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in below:
                below.add(child)
                pending.append(child)

    print("%s: %d descendants in the production DAG (%d edges)" % (ancestor, len(below), len(dag["edges"])))
    for module in modules:
        node = node_of_module.get(module, module)
        print("below %s: %-48s DAG node %-40s %s" % (ancestor, module, node, node in below))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
