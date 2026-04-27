"""Home-zone aggregation for the synthesis pipeline.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/homes.py``.
Moved to ``braunschweig.synthesis.spatial.home_zones`` in Phase 2.10 of
the eqasim-bs refactor; inherited unchanged.
"""

import numpy as np
import pandas as pd

def configure(context):
    context.stage("synthesis.population.sampled")

def execute(context):
    # Load data
    df = context.stage("synthesis.population.sampled")

    # Format data
    df = df.drop_duplicates("household_id")

    return df[["household_id", "departement_id", "commune_id", "iris_id"]]
