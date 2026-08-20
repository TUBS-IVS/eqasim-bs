import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java

def configure(context):
    context.stage("matsim.simulation.prepare")

    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")

    # synpp scopes config per stage (issue #229): execute() calls eqasim.run() ->
    # java.run(), which reads the java binary/memory options AND the hang-watchdog
    # options (#330) from THIS stage's context. Delegate to java's own configure()
    # so the declares cannot drift from what java.run actually reads. Required
    # rather than optional for the watchdog keys: they are volatile, and synpp does
    # not propagate volatile options to downstream stages.
    java.configure(context)

    context.config("matsim_last_iteration", 1)
    context.config("matsim_write_events_interval", 0)
    context.config("matsim_write_plans_interval", 0)
    # MATSim SimWrapper dashboards (network volumes, mode share, trips), written
    # by the Java SimWrapperModule into the simulation output directory. Default
    # ON per the project feature-flag policy (analysis-only module: simulation
    # results are unaffected, the run gains dashboard files); ADR-0074, pinned by
    # tests/test_simwrapper_dashboards_default.py. Set false for a byte-identical
    # output directory: the --simwrapper option is then omitted from the
    # org.eqasim.braunschweig.RunSimulation call (see execute below) and the Java
    # side applies its own false default.
    context.config("simwrapper_dashboards", True)
    context.config("processes")
    # MATSim thread counts, decoupled from the (memory-bound) synthesis worker
    # count. global.numberOfThreads (routing / replanning / scoring between
    # iterations) parallelises well across persons and gets the high value;
    # qsim.numberOfThreads (the mobsim) is a Java shared-memory parallel queue
    # that only scales to ~4-8 threads (memory-bandwidth bound; Dobler 2013,
    # Waraich 2009), so it gets a separate, low value to avoid wasted cores and
    # synchronisation overhead. Both default to None -> fall back
    # (matsim_threads -> processes; qsim -> min(global, 12)), so legacy configs
    # are unchanged.
    context.config("matsim_threads", None)
    context.config("matsim_qsim_threads", None)

def execute(context):
    config_path = "%s/%s" % (
        context.path("matsim.simulation.prepare"),
        context.stage("matsim.simulation.prepare")
    )

    last_iteration = int(context.config("matsim_last_iteration"))
    write_events_interval = int(context.config("matsim_write_events_interval"))
    write_plans_interval = int(context.config("matsim_write_plans_interval"))
    global_threads = int(context.config("matsim_threads") or context.config("processes"))
    # The parallel QSim plateaus at ~4-8 threads, so cap it well below the global
    # thread count; an explicit matsim_qsim_threads overrides the cap.
    qsim_threads = context.config("matsim_qsim_threads")
    qsim_threads = int(qsim_threads) if qsim_threads else min(global_threads, 12)

    # Always write final iteration outputs even if interval is 0
    if write_events_interval == 0:
        write_events_interval = max(last_iteration, 1)
    if write_plans_interval == 0:
        write_plans_interval = max(last_iteration, 1)

    # Run the simulation. global.numberOfThreads (routing/replanning/scoring) and
    # qsim.numberOfThreads (mobsim) are set explicitly here, not only baked into
    # the generated config, so the run reliably uses the intended counts. They
    # differ on purpose: the mobsim does not scale past ~4-8 threads.
    simwrapper = bool(context.config("simwrapper_dashboards"))
    run_args = [
        "--config-path", config_path,
        "--config:controler.lastIteration", str(last_iteration),
        "--config:controler.writeEventsInterval", str(write_events_interval),
        "--config:controler.writePlansInterval", str(write_plans_interval),
        "--config:global.numberOfThreads", str(global_threads),
        "--config:qsim.numberOfThreads", str(qsim_threads),
        # The eqasim-java fork / MATSim 2026 default controler.compressionType to "zst", so
        # the controler writes output_*.xml.zst. The pipeline's existence checks (the assert
        # below, matsim.output's archive assert) and the downstream analysis all consume the
        # historical ".gz" names, so pin gzip to keep output_*.xml.gz. (Standalone eqasim
        # tools that write to explicit .gz paths -- e.g. RunPopulationRouting -- are already
        # fine; only the controler's auto-named output followed the new default.)
        "--config:controler.compressionType", "gzip",
    ]
    if simwrapper:
        run_args += ["--simwrapper", "true"]
    eqasim.run(context, "org.eqasim.braunschweig.RunSimulation", run_args)
    assert os.path.exists("%s/simulation_output/output_events.xml.gz" % context.path())
