"""Secondary-activity location candidates (leisure / shop / other).

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/locations/secondary.py``.
Moved to ``braunschweig.locations`` in Phase 2.7 of the eqasim-bs refactor;
inherited unchanged.
"""

# --- Inherited from eqasim-bavaria -----------------------------------------

import numpy as np
import pandas as pd

def configure(context):
    context.stage("braunschweig.data.locations")

def execute(context):
    # Load data
    df = context.stage("braunschweig.data.locations")

    # Activity types    
    df["offers_leisure"] = df["location_type"] == "leisure"
    df["offers_shop"] = df["location_type"] == "shop"
    df["offers_other"] = True

    # Filter
    df = df[
        df["offers_leisure"] | df["offers_shop"] | df["offers_other"]
    ].copy()

    # Identifiers
    df["location_id"] = np.arange(len(df))
    df["location_id"] = "sec_" + df["location_id"].astype(str)

    return df[[
        "location_id", "commune_id", "iris_id", "geometry",
        "offers_leisure", "offers_shop", "offers_other"
    ]]
