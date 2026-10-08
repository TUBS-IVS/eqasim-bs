"""synpp stage: the gate anchors of the portal stays for the secondary chain solver (#442)."""
CORE_STAGE = "braunschweig.synthesis.portal_trips.stage"


def configure(context):
    context.stage(CORE_STAGE)


def execute(context):
    return context.stage(CORE_STAGE)["anchors"]
