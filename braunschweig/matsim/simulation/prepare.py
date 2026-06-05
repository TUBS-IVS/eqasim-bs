"""MATSim simulation preparation stage.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/matsim/simulation/prepare.py``.
Moved to ``braunschweig.matsim.simulation.prepare`` in Phase 2.12 of the
eqasim-bs refactor.

The transit-zone source has been switched from MVG (Munich) to VRB
(Verkehrsverbund Region Braunschweig). ``braunschweig.data.vrb.zones``
mirrors the MVG stage's output schema (``GeoDataFrame[zone, geometry]``
in EPSG:25832, 400 m buffered MultiPoint per zone) so the downstream
Java consumer sees an identical input.

The ``org.eqasim.bavaria.scenario.AddTransitZoneInformation`` Java class
reference is intentionally retained per Decision D-1c: renaming the Java
package to ``org.eqasim.braunschweig.*`` is out of scope for the Python
refactor. The cached eqasim-java checkout still publishes the bavaria
namespace; the class itself is region-neutral (point-in-polygon zone
attribution against the supplied shapefile).
"""

import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.simulation.prepare as delegate

def configure(context):
    delegate.configure(context)
    context.stage("braunschweig.data.vrb.zones")

    # Cross-cordon feature (flag-gated, default off). When enabled, the prepared
    # full scenario is cut to the cordon (dissolved municipalities + a fractional
    # buffer) with eqasim's native RunScenarioCutter, turning every boundary-
    # crossing trip into an "outside" activity (DMC then keeps it fixed) and
    # cutting the (enlarged) network at the cordon so motorways become real
    # boundary links/gates. Because both matsim.simulation.run and matsim.output
    # consume this stage, the cut output flows downstream transparently -- no
    # changes to run.py / output.py are needed.
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.config("cordon_network_buffer_fraction", 0.10)
        context.stage("data.spatial.municipalities")

def execute(context):
    result = delegate.execute(context)

    df_zones = context.stage("braunschweig.data.vrb.zones")
    df_zones.to_file("{}/transit_zones.shp".format(context.path()))

    eqasim.run(context, "org.eqasim.braunschweig.scenario.AddTransitZoneInformation", [
        "--input-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
        "--output-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
        "--zones-path", "transit_zones.shp"
    ])

    # Cut the full scenario to the cordon (after the transit zones are attached, so
    # the cut transit schedule keeps them). Must be the last step: it rewrites the
    # prefixed scenario files in place with their cut versions.
    if context.config("cordon_enabled"):
        result = _cut_to_cordon(context)

    return result


def _cut_to_cordon(context):
    """Cut the prepared scenario to the cordon extent with RunScenarioCutter.

    Builds the cordon polygon (dissolved in-scope municipalities + a fractional
    buffer) as a single-polygon GeoPackage, then runs eqasim's native cutter on the
    prepared config. The cutter writes the cut ``<prefix>*`` scenario files into the
    stage directory (overwriting the uncut inputs -- safe, as the cutter loads the
    whole scenario before writing) and we return the cut config filename so all
    downstream consumers see the cut scenario.
    """
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction
    from braunschweig.data.cordon.extent import write_cordon_extent

    prefix = context.config("output_prefix")
    threads = context.config("matsim_threads") or context.config("processes")

    df_muni = context.stage("data.spatial.municipalities")
    fraction = float(context.config("cordon_network_buffer_fraction"))
    buffer_m = buffer_m_from_fraction(df_muni, fraction)
    cordon = build_cordon_polygon(df_muni, buffer_m)
    write_cordon_extent("%s/cordon_extent.gpkg" % context.path(), cordon, crs=str(df_muni.crs))

    # The cutter loads the scenario relative to the config file's directory (the
    # stage path, the java cwd), so config-path / extent-path are given relative.
    # No --eqasim-configurator is passed: like every other prepare/run java call,
    # the cutter falls back to the bavaria jar's default configurator (the cutter's
    # option is "eqasim-configurator", not "...-class", and is unnecessary here).
    eqasim.run(context, "org.eqasim.core.scenario.cutter.RunScenarioCutter", [
        "--config-path", "%sconfig.xml" % prefix,
        "--output-path", context.path(),
        "--extent-path", "cordon_extent.gpkg",
        "--threads", threads,
        "--prefix", prefix,
    ])

    cut_config = "%sconfig.xml" % prefix
    assert os.path.exists("%s/%s" % (context.path(), cut_config)), "cutter did not write the cut config"
    return cut_config
