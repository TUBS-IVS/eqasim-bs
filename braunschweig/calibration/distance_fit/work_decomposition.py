"""Work-specific two-stage decomposition + jobs/attraction/potential consistency.

The realised home->work-building distance conflates the gravity Gemeinde choice
and the within-Gemeinde building/home spread. between_gemeinde_distances isolates
the gravity component (home-Gemeinde centroid -> work-Gemeinde centroid);
within = realised - between is computed in the report. jobs_attraction_consistency
checks the building stage respected the gravity attraction (employees).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def between_gemeinde_distances(df, centroids_by_commune, *, detour_factor=1.3):
    rows = []
    for _, r in df.iterrows():
        h = centroids_by_commune.get(r["home_commune_id"])
        w = centroids_by_commune.get(r["work_commune_id"])
        if h is None or w is None:
            continue
        d_m = float(np.hypot(w[0] - h[0], w[1] - h[1]))
        rows.append({"person_id": r["person_id"], "between_km": d_m / 1000.0 * detour_factor})
    return pd.DataFrame(rows)


def jobs_attraction_consistency(assigned_jobs_by_commune, employees_by_commune,
                                potential_by_commune):
    communes = sorted(set(assigned_jobs_by_commune) | set(employees_by_commune)
                      | set(potential_by_commune))
    rows = []
    for c in communes:
        jobs = float(assigned_jobs_by_commune.get(c, 0.0))
        emp = float(employees_by_commune.get(c, 0.0))
        pot = float(potential_by_commune.get(c, 0.0))
        rows.append({
            "commune_id": c, "assigned_jobs": jobs, "employees": emp, "potential_work": pot,
            "assigned_vs_employees_ratio": (jobs / emp) if emp else float("nan"),
            "potential_vs_employees_ratio": (pot / emp) if emp else float("nan"),
        })
    return pd.DataFrame(rows)
