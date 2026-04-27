"""Zero-income placeholder for the synthesis pipeline.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/income.py``.
Moved to ``braunschweig.synthesis.income`` in Phase 2.9 of the eqasim-bs
refactor.

This is a deliberate stand-in: every household is emitted with
``household_income = 0.0`` because the real BS continuous EUR income lives
in :mod:`braunschweig.data.census.household_income` (sampled into
``household_income_eur`` by :mod:`braunschweig.synthesis.population.enriched`)
and the upstream eqasim ``synthesis.population.income.selected`` slot still
expects the legacy categorical ``household_income`` column. Replacing this
placeholder with the MiD H4 / INKAR-derived value is tracked separately
(BUG-009 follow-up).
"""

import numpy as np
import pandas as pd
import multiprocessing as mp
from tqdm import tqdm

def configure(context):
    context.stage("synthesis.population.sampled")

def execute(context):
    # Load data
    df = context.stage("synthesis.population.sampled")[["household_id"]]
    
    # Format
    df = df.drop_duplicates("household_id")
    df["household_income"] = 0.0
    
    return df
