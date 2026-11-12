"""Flag-gated education-location replacement for synthesis primary locations.

OFF (default): identical to eqasim_common.locations.synthesis.replacement (work
from the base step, education from the OSM per-person sampler) -> byte-identical.
ON: education comes from braunschweig.synthesis.locations.education_gravity (real
NDS schools, capacity-constrained gravity); work is unchanged.
"""
import synthesis.population.spatial.primary.locations as base

from braunschweig import synpp_tokens

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136). ``base`` is the upstream stage
# module this one delegates its work assignment to.
_HELPER_MODULES = (base,)
_DEFERRED_HELPER_MODULE_NAMES = (
    # Declared stages this stage runs as libraries, and their imports (ADR-0194): the DAG edge
    # covers their output, not this code. ``base`` imports them; this stage runs ``base.execute``.
    "braunschweig.gravity.taz_margins",
    "synthesis.population.spatial.primary.candidates",
)


def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.spatial.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.locations.work")
    context.stage("synthesis.locations.education")

    # Declare the TAZ flag so that when this stage is used as an alias for
    # synthesis.population.spatial.primary.locations, base.execute() can read
    # it without raising PipelineError ("config option not requested").
    # The default is False (OFF), preserving byte-identical OFF behaviour.
    context.config("taz_work_location_choice", False)
    # ADR-0164: the work/education assignment keys that base.execute() reads.
    base.configure_assignment(context)

    if context.config("education_gravity_enabled", False):
        context.stage("braunschweig.synthesis.locations.education_gravity",
                      alias="education_source")
    else:
        context.stage("eqasim_common.locations.synthesis.education",
                      alias="education_source")


def validate(context):
    """Include the delegated base module's source in synpp's stage cache identity (ADR-0136)."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def execute(context):
    df_work, df_original = base.execute(context)
    df_replacement = context.stage("education_source")

    assert len(df_original) == len(df_replacement)
    assert set(df_original["person_id"]) == set(df_replacement["person_id"])
    return df_work, df_replacement
