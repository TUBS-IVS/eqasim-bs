import os.path

import matsim.runtime.pt2matsim as pt2matsim

def configure(context):
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.pt2matsim")

    context.stage("matsim.scenario.supply.osm")
    context.stage("matsim.scenario.supply.gtfs")

    context.config("data_path")
    # Execution detail, not scientific config: changing it must not devalidate cached stages (upstream eqasim-france #438)
    context.config("processes", volatile = True)
    # MATSim CPU thread budget for the pt2matsim PublicTransitMapper. The PT
    # mapping is CPU-bound, so it should use the (potentially higher) MATSim
    # thread budget rather than the memory-bound synthesis worker count. Defaults
    # to None -> fall back to "processes", so existing configs are unchanged.
    # Declared here because the two-arg default form is only allowed in
    # configure(); execute() reads it with the one-arg form. pt2matsim mapping is
    # deterministic w.r.t. thread count, so the output network/schedule is
    # unchanged regardless of the value.
    context.config("matsim_threads", None)

def execute(context):
    # Prepare input paths
    network_path = "%s/%s" % (
        context.path("matsim.scenario.supply.osm"),
        context.stage("matsim.scenario.supply.osm")
    )

    schedule_path = "%s/%s" % (
        context.path("matsim.scenario.supply.gtfs"),
        context.stage("matsim.scenario.supply.gtfs")["schedule_path"]
    )

    # Create and modify config file
    pt2matsim.run(context, "org.matsim.pt2matsim.run.CreateDefaultPTMapperConfig", [
        "config_template.xml"
    ])

    with open("%s/config_template.xml" % context.path()) as f_read:
        content = f_read.read()

        content = content.replace(
            '<param name="inputNetworkFile" value="" />',
            '<param name="inputNetworkFile" value="%s" />' % network_path
        )
        content = content.replace(
            '<param name="inputScheduleFile" value="" />',
            '<param name="inputScheduleFile" value="%s" />' % schedule_path
        )
        content = content.replace(
            '<param name="numOfThreads" value="2" />',
            '<param name="numOfThreads" value="%d" />'
            % (context.config("matsim_threads") or context.config("processes"))
        )
        content = content.replace(
            '<param name="outputNetworkFile" value="" />',
            '<param name="outputNetworkFile" value="network.xml.gz" />'
        )
        content = content.replace(
            '<param name="outputScheduleFile" value="" />',
            '<param name="outputScheduleFile" value="schedule.xml.gz" />'
        )
        content = content.replace(
            '<param name="outputStreetNetworkFile" value="" />',
            '<param name="outputStreetNetworkFile" value="road_network.xml.gz" />'
        )
        content = content.replace(
            '<param name="modesToKeepOnCleanUp" value="car" />',
            '<param name="modesToKeepOnCleanUp" value="car,car_passenger,truck" />'
        )

        with open("%s/config.xml" % context.path(), "w+") as f_write:
            f_write.write(content)

    # Run mapping process
    pt2matsim.run(context, "org.matsim.pt2matsim.run.PublicTransitMapper", [
        "config.xml"
    ])

    assert(os.path.exists("%s/network.xml.gz" % context.path()))
    assert(os.path.exists("%s/schedule.xml.gz" % context.path()))

    return dict(
        network_path = "network.xml.gz",
        schedule_path = "schedule.xml.gz",
        #plausibility_path = "allPlausibilityWarnings.xml.gz"
    )
