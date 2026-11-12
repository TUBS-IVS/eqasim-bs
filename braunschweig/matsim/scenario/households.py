"""MATSim households writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.households: appends the injected in-commuter persons (each a
single-person household) to the resident persons before the SAME write -- both the SvB
cross-cordon commuters (braunschweig.synthesis.incommuters) and the student in-commuters
(braunschweig.synthesis.student_incommuters, #140 Task 5). OFF -> both in-commuter frames
empty -> byte-identical.
"""
from __future__ import annotations

import matsim.scenario.households as base
from braunschweig import synpp_tokens
from braunschweig.synthesis.incommuter_merge import _base as _merge_base
from braunschweig.synthesis.incommuter_merge._base import (assert_unique_ids,
                                                            concat_frame)

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136). ``base`` is the upstream stage this
# one extends; ``matsim.writers`` is reached through ``base``, so it is hashed by name.
_HELPER_MODULES = (base, _merge_base)
_DEFERRED_HELPER_MODULE_NAMES = ("matsim.writers",)


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.student_incommuters")


def validate(context):
    """Include the helper modules' source in synpp's stage cache identity (ADR-0136)."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def execute(context):
    output_path = "%s/households.xml.gz" % context.path()
    df_persons = context.stage("synthesis.population.enriched")
    if context.config("cordon_enabled"):
        svb_persons = context.stage("braunschweig.synthesis.incommuters")["persons"]
        student_persons = context.stage(
            "braunschweig.synthesis.student_incommuters")["persons"]
        # Loud safety net (CLAUDE.md no-silent-corruption): the student stage's
        # household_id block is offset above the resident+SvB range by a fixed
        # assumption (_ID_OFFSET_ABOVE_RESIDENTS), not a hard dependency on the
        # SvB stage's actual count -- verify the two in-commuter blocks never
        # actually overlap before writing them into the same households file.
        assert_unique_ids([svb_persons, student_persons], "household_id",
                         "braunschweig.matsim.scenario.households (SvB vs student "
                         "in-commuter households)")
        df_persons = concat_frame(df_persons, svb_persons, "person_id")
        df_persons = concat_frame(df_persons, student_persons, "person_id")
    return base.write_households(output_path, df_persons, context)
