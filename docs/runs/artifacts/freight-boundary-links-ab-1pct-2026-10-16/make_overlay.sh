#!/usr/bin/env bash
# Write the throwaway 1 % overlay of one #444 smoke arm (not committed; recorded in the run manifest).
# It is configs/overlays/test_1pct.yml with an own working directory, output directory and prefix,
# run target matsim.output only. Every scientific key (sampling_rate, random_seed, matsim_last_iteration,
# freight, eqasim_source_path, ...) keeps the base / test_1pct value, so the two arms differ only in code.
# Usage: bash make_overlay.sh <arm>
set -euo pipefail
ARM=$1
WT=$HOME/i444-smoke/$ARM/eqasim-bs
python3 - "$WT" "$ARM" <<'PY'
import sys, yaml
wt, arm = sys.argv[1:3]
with open(f"{wt}/configs/overlays/test_1pct.yml", encoding="utf-8") as fh:
    doc = yaml.safe_load(fh)
doc["working_directory"] = f"eqasim-data/cache_i444_1pct_{arm}"
doc["run"] = ["matsim.output"]
doc["config"].update({
    "output_path": f"eqasim-data/output_i444_1pct_{arm}",
    "analysis_working_directory": f"eqasim-data/cache_i444_1pct_{arm}",
    "output_prefix": f"braunschweig_1pct_i444_{arm}_",
})
with open(f"{wt}/eqasim-data/i444_1pct_{arm}.yml", "w", encoding="utf-8") as fh:
    yaml.safe_dump(doc, fh, sort_keys=False)
print(open(f"{wt}/eqasim-data/i444_1pct_{arm}.yml", encoding="utf-8").read())
PY
