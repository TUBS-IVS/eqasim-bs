"""Write the throwaway overlay of the #468 freight tool jar comparison (recorded in the run manifest).

It is configs/overlays/test_1pct.yml of the checked-out commit with ONE run target,
braunschweig.freight.extraction, and the shared-cache store switched off (cache_share_enabled and
cache_share_export false: no stage reads these keys, they only steer the launcher's prime/export, so
the run neither reads from nor writes into the shared store). Every scientific key keeps its value.
Usage: python3 make_overlay_i468.py <eqasim-bs worktree> <output yml>
"""
import sys

import yaml

worktree, out = sys.argv[1:3]
with open(f"{worktree}/configs/overlays/test_1pct.yml", encoding="utf-8") as handle:
    doc = yaml.safe_load(handle)
doc["run"] = ["braunschweig.freight.extraction"]
doc["config"]["cache_share_enabled"] = False
doc["config"]["cache_share_export"] = False
with open(out, "w", encoding="utf-8") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
print(open(out, encoding="utf-8").read())
