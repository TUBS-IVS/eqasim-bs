"""Reverse distance-decay origin model for student in-commuters (#140).

ASSUMPTION (CLAUDE.md no-invented-reference): the residences of non-resident
students follow the SAME calibrated distance-decay as the resident university
choice, population-weighted by each external Kreis's 18-29 population. No
committed current-residence student OD exists to validate this; it is a
documented assumption, not a reference. The 18-29 band is the student-age proxy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.data.census.population import load_age_sex_by_kreis
from braunschweig.synthesis.locations.education_gravity_model import assign_by_decay

# DESTATIS 12411-0018 lower bounds that fall in [18, 29]: {18, 20, 25}.
def _student_age_pop(df_age_sex, age_lower, age_upper):
    """Sum DESTATIS (sex, age_class) weights whose class lower bound is in
    [age_lower, age_upper] to a per-Kreis student-age population Series."""
    sel = df_age_sex[(df_age_sex["age_class"] >= age_lower)
                     & (df_age_sex["age_class"] <= age_upper)]
    return sel.groupby("kreis")["weight"].sum()


def student_age_pop_by_kreis(data_path, kreise, age_lower=18, age_upper=29):
    """18-29 population per Kreis from DESTATIS 12411-0018 for the given ars5 set."""
    df = load_age_sex_by_kreis(data_path, kreise)
    return _student_age_pop(df, age_lower, age_upper)


def draw_origin_kreise(dest_xy_by_commune, counts, kreis_xy, kreis_pop,
                       slope, max_radius_km, rng):
    """Draw an origin Kreis per student in-commuter via reverse decay.

    Args:
        dest_xy_by_commune: dict ``commune_ars5 -> (x, y)`` destination point
            (EPSG:25832), e.g. the commune's university centroid.
        counts: DataFrame ``[commune_ars5, in_commuters]`` (int counts per commune).
        kreis_xy: DataFrame ``[ars5, x, y]`` candidate ORIGIN Kreis points
            (EPSG:25832), already restricted to Kreise OUTSIDE ZGB.
        kreis_pop: Series ars5 -> 18-29 population (the decay weight).
        slope, max_radius_km: the resident ``education_university_*`` parameters.
        rng: seeded numpy Generator.

    Returns DataFrame ``[orig_ars5, dest_commune]`` with one row per in-commuter.
    Uses ``assign_by_decay`` (nearest-Kreis fallback beyond the radius, logged)."""
    ars5 = kreis_xy["ars5"].to_numpy()
    weight = np.array([float(kreis_pop.get(a, 0.0)) for a in ars5], dtype=float)
    school_xy = kreis_xy[["x", "y"]].to_numpy(dtype=float)

    parts = []
    for comm, n in zip(counts["commune_ars5"], counts["in_commuters"]):
        n = int(n)
        if n <= 0:
            continue
        dx, dy = dest_xy_by_commune[comm]
        pupil_xy = np.tile([dx, dy], (n, 1)).astype(float)
        # Reverse decay: destination is fixed, candidates are the origin Kreise.
        choice = assign_by_decay(
            pupil_xy, school_xy, weight, slope=slope,
            max_radius_km=max_radius_km, rng=rng,
            label=f"student_incommuter_origin:{comm}")
        parts.append(pd.DataFrame({
            "orig_ars5": ars5[np.asarray(choice, dtype=int)],
            "dest_commune": comm}))
    if not parts:
        return pd.DataFrame({"orig_ars5": [], "dest_commune": []})
    return pd.concat(parts, ignore_index=True)
