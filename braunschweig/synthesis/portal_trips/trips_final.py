"""synpp stage: the reporting-day trips with portal stays, aliased as synthesis.population.trips.final (#442)."""
CORE_STAGE = "braunschweig.synthesis.portal_trips.stage"


def configure(context):
    context.stage(CORE_STAGE)


def execute(context):
    return context.stage(CORE_STAGE)["trips"]
