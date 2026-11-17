"""Cross-checks for the ladder manifest: env independence of the 0a diagnostic, P vs 0a identity."""
import hashlib
import sys
from pathlib import Path

import pandas as pd

home = Path.home()
lad = home / "i138_ladder"
for name in ("control_fit.csv", "misfit_split.csv", "rounding_shift.csv", "solver_paths.csv", "reuse.csv"):
    a = pd.read_csv(lad / "diag_arm0a" / name)
    b = pd.read_csv(lad / "ladder_arm0a" / name)
    num = a.select_dtypes("number").columns
    same_shape = a.shape == b.shape
    diff = (a[num] - b[num]).abs().max().max() if same_shape else float("nan")
    print(f"{name}: shapes {a.shape} {b.shape}, max abs numeric difference eqasim-env vs master-env {diff}")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


for batch in ("batch_000", "batch_032"):
    paths = {
        "P": home / "prod100_20261031/popsim_work" / batch / "output/final_expanded_household_ids.csv",
        "0a": lad / "arm0a" / batch / "output/final_expanded_household_ids.csv",
        "0b": lad / "arm0b" / batch / "output/final_expanded_household_ids.csv",
    }
    print(batch, {k: (sha(p), p.stat().st_mtime) for k, p in paths.items()})
    frames = {k: pd.read_csv(p) for k, p in paths.items()}
    print(batch, "P==0a rows identical:", frames["P"].equals(frames["0a"]),
          "| 0a==0b:", frames["0a"].equals(frames["0b"]))
