"""Flag-gated education-location replacement for synthesis primary locations.

OFF (default): identical to eqasim_common.locations.synthesis.replacement (work
from the base step, education from the OSM per-person sampler) -> byte-identical.
ON: education comes from braunschweig.synthesis.locations.education_gravity (real
NDS schools, capacity-constrained gravity); work is unchanged.
"""
import synthesis.population.spatial.primary.locations as base


def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.spatial.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.locations.work")
    context.stage("synthesis.locations.education")

    if context.config("education_gravity_enabled", False):
        context.stage("braunschweig.synthesis.locations.education_gravity",
                      alias="education_source")
    else:
        context.stage("eqasim_common.locations.synthesis.education",
                      alias="education_source")


def execute(context):
    df_work, df_original = base.execute(context)
    df_replacement = context.stage("education_source")

    assert len(df_original) == len(df_replacement)
    assert set(df_original["person_id"]) == set(df_replacement["person_id"])
    return df_work, df_replacement
