"""Pairwise municipality-centroid distance matrix.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/gravity/distance_matrix.py``.
Moved to ``eqasim_common`` in Phase 2.2 of the eqasim-bs refactor;
inherited unchanged.

The stage produces an N×N matrix of straight-line distances (metres)
between municipality centroids, indexed in the same order as the
``data.spatial.iris`` (or its alias) DataFrame.  It is consumed by the
gravity model.
"""

from tqdm import tqdm
import pandas as pd
import numpy as np
import numpy.linalg as la


def configure(context):
    context.stage("eqasim_common.data.spatial.iris")

def execute(context):
    # One municipality per "IRIS"
    df_municipalities = context.stage("eqasim_common.data.spatial.iris")
    municipalities = df_municipalities["commune_id"].values
        
    # Initialize matrix to zero
    distance_matrix = np.ones((len(municipalities), len(municipalities)))
    
    # Convert locations to (N,2)-array
    locations = np.array([
        df_municipalities["geometry"].centroid.x,
        df_municipalities["geometry"].centroid.y
    ]).T
    
    # Calculate Euclidean distances per row
    for k in range(len(locations)):
        distance_matrix[k,:] = la.norm(locations[k] - locations, axis = 1)
    
    # Convert to km
    distance_matrix *= 1e-3
    
    # Formatting into a data frame
    df_distances = pd.DataFrame({ "distance_km": distance_matrix.reshape(-1) }, index = pd.MultiIndex.from_product([
    municipalities, municipalities
    ], names = ["origin_id", "destination_id"])).reset_index()
   
    return df_distances
