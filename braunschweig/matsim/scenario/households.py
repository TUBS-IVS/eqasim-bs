"""MATSim households writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.households: appends the injected in-commuter persons (each a
single-person household) to the resident persons before the SAME write. OFF ->
in-commuter frame empty -> byte-identical.
"""
from __future__ import annotations

import matsim.scenario.households as base
from braunschweig.synthesis.incommuter_merge._base import concat_frame


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")


def execute(context):
    output_path = "%s/households.xml.gz" % context.path()
    df_persons = context.stage("synthesis.population.enriched")
    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")["persons"]
        df_persons = concat_frame(df_persons, inc, "person_id")
    return base.write_households(output_path, df_persons, context)
