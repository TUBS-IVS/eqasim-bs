"""MATSim simulation preparation stage.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/matsim/simulation/prepare.py``.
Moved to ``braunschweig.matsim.simulation.prepare`` in Phase 2.12 of the
eqasim-bs refactor; inherited unchanged.

The ``org.eqasim.bavaria.scenario.AddTransitZoneInformation`` Java class
reference is intentionally retained per Decision D-1c: renaming the Java
package to ``org.eqasim.braunschweig.*`` is out of scope for the Python
refactor. The cached eqasim-java checkout still publishes the bavaria
namespace.
"""

import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.simulation.prepare as delegate

def configure(context):
    delegate.configure(context)
    context.stage("bavaria.data.mvg.zones")

def execute(context):
    result = delegate.execute(context)

    df_zones = context.stage("bavaria.data.mvg.zones")
    df_zones.to_file("{}/transit_zones.shp".format(context.path()))

    eqasim.run(context, "org.eqasim.bavaria.scenario.AddTransitZoneInformation", [
        "--input-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
        "--output-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
        "--zones-path", "transit_zones.shp"
    ])

    return result
