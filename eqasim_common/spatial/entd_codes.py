"""ENTD `departement_id` whitelist used to short-circuit chain filtering.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/entd_codes.py``.
Moved to ``eqasim_common.spatial`` in Phase 2.3 of the eqasim-bs refactor;
inherited unchanged.
"""

import numpy as np
import pandas as pd
import multiprocessing as mp
from tqdm import tqdm


def configure(context):
    context.stage("data.hts.entd.cleaned")

def execute(context):
    df_households, df_persons, df_trips = context.stage("data.hts.entd.cleaned")

    values = set(df_persons["departement_id"])
    values |= set(df_trips["origin_departement_id"])
    values |= set(df_trips["destination_departement_id"])
    
    return pd.DataFrame({ "departement_id": sorted(values) })
