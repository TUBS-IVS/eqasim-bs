"""Override-and-replace stage for synthesis.population.spatial.primary.locations.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/locations/synthesis/replacement.py``.
Moved to ``eqasim_common.locations.synthesis`` in Phase 2.4 of the eqasim-bs
refactor; inherited unchanged apart from the dependency name update for the
companion ``education`` stage (also moved in 2.4).

Applies the upstream primary-locations logic, then overrides the education
locations with values produced by
:mod:`eqasim_common.locations.synthesis.education`.
"""

import synthesis.population.spatial.primary.locations as base

def configure(context):
    # Copy & past from base
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.spatial.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.locations.work")
    context.stage("synthesis.locations.education")

    # Custom data
    context.stage("eqasim_common.locations.synthesis.education")

def execute(context):
    # We delegate the logic to the base step
    df_work, df_original = base.execute(context)

    # And we override the education decisions
    df_replacement = context.stage("eqasim_common.locations.synthesis.education")

    # Verification
    assert len(df_original) == len(df_replacement)
    assert set(df_original["person_id"]) == set(df_replacement["person_id"])

    return df_work, df_replacement
