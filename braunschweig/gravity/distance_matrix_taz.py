"""synpp stage: pairwise TAZ-centroid distance matrix.

REUSE/mirror of ``eqasim_common/gravity/distance_matrix.py`` (centroid distances
in km), built from ``braunschweig.data.spatial.taz`` and keyed on ``taz_id``.
The existing Gemeinde stage is untouched. Stage: ``braunschweig.gravity.distance_matrix_taz``.
"""
from __future__ import annotations

import numpy as np
import numpy.linalg as la
import pandas as pd


def taz_distance_matrix(df_taz):
    zones = df_taz["taz_id"].astype(str).values
    locations = np.array([df_taz["geometry"].centroid.x, df_taz["geometry"].centroid.y]).T
    distance_matrix = np.ones((len(zones), len(zones)))
    for k in range(len(locations)):
        distance_matrix[k, :] = la.norm(locations[k] - locations, axis=1)
    distance_matrix *= 1e-3
    return pd.DataFrame(
        {"distance_km": distance_matrix.reshape(-1)},
        index=pd.MultiIndex.from_product([zones, zones], names=["origin_id", "destination_id"]),
    ).reset_index()


def configure(context):
    context.stage("braunschweig.data.spatial.taz")


def execute(context):
    return taz_distance_matrix(context.stage("braunschweig.data.spatial.taz"))
