"""Student in-commuter OD-flow + distance analysis (#140).

Mirrors braunschweig/analysis/simwrapper/commuters.py (which writes the SvB
commuter_top_relations.csv [from, to, value]). Pure aggregation of the injected
student in-commuter agents -- no model, no external data, fully reproducible.
Outputs are model results, not compared to a target (no committed reference).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd


def student_od(persons_with_origin):
    """Origin-Kreis -> destination-university-commune flow counts.

    ``persons_with_origin`` has columns ``orig_ars5`` and ``dest_commune``.
    Returns DataFrame ``[from_ars5, to_commune, value]``."""
    od = (persons_with_origin.groupby(["orig_ars5", "dest_commune"])
          .size().reset_index(name="value")
          .rename(columns={"orig_ars5": "from_ars5", "dest_commune": "to_commune"}))
    return od.sort_values("value", ascending=False).reset_index(drop=True)


def write_outputs(persons_with_origin, straight_line_km, output_dir):
    """Write student_commuter_od.csv, _top_relations.csv, student_commute_distance.csv.

    ``straight_line_km`` is a per-agent Series of origin->campus straight-line
    distance (km). Nothing is written when there are no student in-commuters."""
    if persons_with_origin.empty:
        return
    os.makedirs(output_dir, exist_ok=True)
    od = student_od(persons_with_origin)
    od.to_csv(os.path.join(output_dir, "student_commuter_od.csv"), index=False)
    od.rename(columns={"from_ars5": "from", "to_commune": "to"}).head(50).to_csv(
        os.path.join(output_dir, "student_commuter_top_relations.csv"), index=False)
    bands = pd.cut(straight_line_km, bins=[0, 5, 10, 20, 50, 100, np.inf])
    dist = (straight_line_km.groupby(bands).size().reset_index(name="count")
            .rename(columns={straight_line_km.name or 0: "band"}))
    dist["mean_km"] = float(straight_line_km.mean())
    dist.to_csv(os.path.join(output_dir, "student_commute_distance.csv"), index=False)
