import os.path

import matsim.runtime.pt2matsim as pt2matsim

def configure(context):
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.pt2matsim")
    context.stage("data.osm.cleaned")
    context.stage("data.spatial.iris")

    context.config("export_detailed_network", False)

    # synpp scopes config per stage (issue #229): this stage's execute() calls
    # pt2matsim.run(), which reads pt2matsim_version and the java.run options from
    # THIS stage's context. Delegate to the helper's own configure() so the
    # declares cannot drift from what it actually reads -- without this the run
    # raises 'Config option pt2matsim_version is not requested' as soon as this
    # stage's cache is devalidated (2026-08-20 night run, eqasim-java 2.3.0 bump).
    pt2matsim.configure(context)

def execute(context):
    osm_path = "%s/output.osm.gz" % context.path("data.osm.cleaned")
    crs = context.stage("data.spatial.iris").crs

    pt2matsim.run(context, "org.matsim.pt2matsim.run.CreateDefaultOsmConfig", 
        arguments=["config_template.xml"]
    )

    with open("%s/config_template.xml" % context.path()) as f_read:
        content = f_read.read()

        content = content.replace(
            '<param name="osmFile" value="null" />',
            '<param name="osmFile" value="%s" />' % osm_path
        )

        content = content.replace(
            '<param name="outputCoordinateSystem" value="null" />',
            '<param name="outputCoordinateSystem" value="{}" />'.format(crs)
        )

        content = content.replace(
            '<param name="outputNetworkFile" value="null" />',
            '<param name="outputNetworkFile" value="network.xml.gz" />'
        )

        if context.config("export_detailed_network"):
            content = content.replace(
                '<param name="outputDetailedLinkGeometryFile" value="null" />',
                '<param name="outputDetailedLinkGeometryFile" value="detailed_network.csv" />',
            )

        content = content.replace(
            '</module>',
            """
                <parameterset type="routableSubnetwork">
                    <param name="allowedTransportModes" value="car" />
                    <param name="subnetworkMode" value="car_passenger" />
                </parameterset>
            </module>
            """
        )

        with open("%s/config.xml" % context.path(), "w+") as f_write:
            f_write.write(content)

    pt2matsim.run(context, "org.matsim.pt2matsim.run.Osm2MultimodalNetwork", 
        arguments=["config.xml"]
    )

    assert(os.path.exists("%s/network.xml.gz" % context.path()))
    return "network.xml.gz"
