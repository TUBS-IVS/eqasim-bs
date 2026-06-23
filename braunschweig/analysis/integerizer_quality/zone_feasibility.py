"""Classify each 100m PopulationSim sub-zone as OPTIMAL or smart-rounded from the
batch logs. A zone is smart_rounded iff its ZENSUS100m id appears in an
``Integerizer failed for ... status INFEASIBLE`` line; otherwise the zone (seen via
``sequential_integerizing zone_id <id>``) is optimal. This extends the counting in
``braunschweig.popsim.mid.summarize_integerizer_feasibility`` to a per-zone table.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Union

import pandas as pd

_ZONE_SEEN = re.compile(r"sequential_integerizing zone_id (CRS3035RES100m\S+)")
_ZONE_FAILED = re.compile(r"Integerizer failed for \S*?(CRS3035RES100m\S+?) status INFEASIBLE")
_CONVERGED_FALSE = re.compile(r"converged False iter")


def classify_zones(work_dir: Union[str, Path]) -> pd.DataFrame:
    work_dir = Path(work_dir)
    rows = []
    for batch_dir in sorted(work_dir.glob("batch_*")):
        log = batch_dir / "output" / "populationsim.log"
        if not log.is_file():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        seen = set(_ZONE_SEEN.findall(text))
        failed = set(_ZONE_FAILED.findall(text))
        seen |= failed  # a failed zone is also a real zone even if its "seen" line was truncated
        for zid in sorted(seen):
            rows.append({
                "zensus100m": zid,
                "status": "smart_rounded" if zid in failed else "optimal",
                "converged_false": bool(_CONVERGED_FALSE.search(text)),
                "batch": batch_dir.name,
            })
    return pd.DataFrame(rows, columns=["zensus100m", "status", "converged_false", "batch"])
