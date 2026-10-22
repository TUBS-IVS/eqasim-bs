"""Write the throwaway overlays of the #468 Task 18 server scenarios (recorded in the run manifest).

All start from configs/overlays/test_100pct.yml of the checked-out commit, so every scientific key and
every stage hash equals the production 100 % configuration; only the run targets, the working directory of
the prime scenario and the launcher-only cache_share / gate keys differ (no stage reads them).
- i468p4_runtime.yml: run targets matsim.runtime.eqasim + braunschweig.freight.extraction_tool (the cheap
  Java runtime subgraph: java, git, maven, the eqasim jar and the freight tool jar), cache_share on with
  an empty scratch store (for provenance tracking only, see below).
- i468p4_runtime_gate.yml: the same plus synpp_expected_stale_stages [matsim.runtime.java] (launcher gate).
- i468p4_fresh.yml: the same run targets in a FRESH working directory eqasim-data/cache_i468p4_fresh,
  primed from the scratch store eqasim-data/cache_i468p4_store with exactly the two jar stages; export off.
Usage: python3 make_runtime_overlays_i468p4.py <eqasim-bs worktree>
"""
import copy
import sys

import yaml

worktree = sys.argv[1]
with open(f"{worktree}/configs/overlays/test_100pct.yml", encoding="utf-8") as handle:
    base = yaml.safe_load(handle)
targets = ["matsim.runtime.eqasim", "braunschweig.freight.extraction_tool"]


def write(name, doc):
    path = f"{worktree}/eqasim-data/{name}"
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(doc, handle, sort_keys=False)
    print(f"--- {path}\n" + open(path, encoding="utf-8").read())


runtime = copy.deepcopy(base)
runtime["run"] = list(targets)
# cache_share stays enabled so the launcher's track_run writes provenance envelopes for executed entries
# (scripts/run_synpp.py track_cache_provenance_from_config); the store is a scratch directory that is
# empty until the explicit export of the prime scenario, and the post-run export is off, so the
# production store eqasim-data/cache_shared is never read or written.
runtime["config"]["cache_share_enabled"] = True
runtime["config"]["cache_share_export"] = False
runtime["config"]["cache_share_store"] = "eqasim-data/cache_i468p4_store"
runtime["config"]["cache_share_stages"] = list(targets)
runtime["config"]["cache_share_recompute"] = []
write("i468p4_runtime.yml", runtime)

gate = copy.deepcopy(runtime)
gate["config"]["synpp_expected_stale_stages"] = ["matsim.runtime.java"]
write("i468p4_runtime_gate.yml", gate)

fresh = copy.deepcopy(runtime)
fresh["working_directory"] = "eqasim-data/cache_i468p4_fresh"
write("i468p4_fresh.yml", fresh)
