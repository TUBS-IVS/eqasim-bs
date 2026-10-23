"""Compare the validation tokens a config would compute with those stored in a working directory. Read-only
unless --patch is given.

Usage (from the code worktree whose tokens should be computed):
    python token_diff.py <base.yml> <overlay.yml> <working_directory> [--patch <stage name> ...]

Prints, for every stage whose config hash is present in pipeline.json, whether the stored validation token equals
the one this code computes. With --patch, the stored token of the NAMED stages is replaced by the computed one
(a throwaway A/B cache only; the reason must be recorded in the run manifest).
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))

from report_stage_hash_impact import build_registry  # noqa: E402
from synpp.pipeline import ValidateContext  # noqa: E402

base, overlay, working_directory = sys.argv[1:4]
patch = sys.argv[sys.argv.index("--patch") + 1:] if "--patch" in sys.argv else []

registry = build_registry(base, overlay, True)
meta_path = os.path.join(working_directory, "pipeline.json")
meta = json.load(open(meta_path))
changed = []
for stage_hash, stage in sorted(registry.items()):
    if stage_hash not in meta:
        continue
    cache_path = os.path.join(working_directory, stage_hash + ".cache")
    token = stage["wrapper"].validate(ValidateContext(stage["config"], cache_path))
    stored = meta[stage_hash].get("validation_token")
    name = stage_hash.split("__")[0]
    same = token == stored
    module_same = meta[stage_hash].get("module_hash") == stage["wrapper"].module_hash
    if not same or not module_same:
        changed.append(name)
        print(f"DIFF  {name:70s} token_same={same} module_hash_same={module_same}")
    if name in patch and not same:
        meta[stage_hash]["validation_token"] = token
        print(f"PATCHED {name}")
print(f"{len(changed)} stages differ")
if patch:
    with open(meta_path + ".new", "w") as handle:
        json.dump(meta, handle)
    os.replace(meta_path + ".new", meta_path)
    print("pipeline.json written")
