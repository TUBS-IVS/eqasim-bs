"""Which KREIS control columns differ between a base batch folder and a prepared arm folder."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

base, *arms = (Path(p) for p in sys.argv[1:])
b = pd.read_csv(base / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str}).set_index("KREIS")
for arm in arms:
    a = pd.read_csv(arm / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str}).set_index("KREIS")
    assert list(a.columns) == list(b.columns) and list(a.index) == list(b.index)
    changed = [c for c in b.columns if not np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), rtol=0, atol=1e-9)]
    same = [c for c in b.columns if c not in changed]
    print(arm, "changed", len(changed), "unchanged", len(same))
    print("  unchanged:", same)
