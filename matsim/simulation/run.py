import shutil
import os.path

import matsim.runtime.eqasim as eqasim

def configure(context):
    context.stage("matsim.simulation.prepare")

    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")

    context.config("matsim_last_iteration", 1)
    context.config("matsim_write_events_interval", 0)
    context.config("matsim_write_plans_interval", 0)

def execute(context):
    config_path = "%s/%s" % (
        context.path("matsim.simulation.prepare"),
        context.stage("matsim.simulation.prepare")
    )

    last_iteration = int(context.config("matsim_last_iteration"))
    write_events_interval = int(context.config("matsim_write_events_interval"))
    write_plans_interval = int(context.config("matsim_write_plans_interval"))

    # Always write final iteration outputs even if interval is 0
    if write_events_interval == 0:
        write_events_interval = max(last_iteration, 1)
    if write_plans_interval == 0:
        write_plans_interval = max(last_iteration, 1)

    # Run routing
    eqasim.run(context, "org.eqasim.bavaria.RunSimulation", [
        "--config-path", config_path,
        "--config:controler.lastIteration", str(last_iteration),
        "--config:controler.writeEventsInterval", str(write_events_interval),
        "--config:controler.writePlansInterval", str(write_plans_interval),
    ])
    assert os.path.exists("%s/simulation_output/output_events.xml.gz" % context.path())
