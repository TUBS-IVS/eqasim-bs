# ADR-0101 · 2026-09-03 · Analysis stages read the MATSim output archive, never the run stage

- **Status:** active
- **Context:** `braunschweig.analysis.analysis_suite` and
  `braunschweig.analysis.simwrapper_export` declared
  `context.stage("matsim.simulation.run")` whenever
  `simwrapper_include_matsim` was true — but consumed the stage ONLY as
  `Path(context.path(...)).parent`, i.e. to learn a directory. Neither stage
  reads the run's return value. In the 2026-08-23/24 100% run this edge made a
  separately-invoked analysis phase recompute the entire simulation chain
  (GTFS/OSM cleaning, vehicles, chainsolvers, population cutting, ~2.5 h) and
  head for a full MATSim re-run before producing a single diagnostic, because
  a per-invocation synpp devalidation cascade had invalidated the cached
  upstream stages (issue #354). The constant's own comment already called the
  flag a *signal* ("signals the run has MATSim"); the wiring made it a
  *dependency*.
- **Decision:** Resolve the simulation outputs from configuration instead of
  from the stage graph. `matsim.output` already mirrors `simulation_output/`
  into the deterministic `<output_path>/matsim_output/`
  (`archive_matsim_output`, default ON; ADR-0064) precisely so the outputs
  survive a cache wipe and are findable without a stage hash. The new
  `braunschweig.analysis.matsim_archive.resolve_matsim_archive` returns that
  directory when the archive is complete (sentinel `output_events.xml.gz`,
  which `matsim.output` asserts) and logs the `ARCHIVE_INFO.json` provenance
  (source hash dir, creation time) so analysis artifacts stay traceable to the
  simulation that produced their inputs. Both stages drop the
  `matsim.simulation.run` edge; `simwrapper_include_matsim` stays a pure
  signal. `_find_sim_output` (dashboard/simwrapper) and `_find_sim_trips`
  (MiD validation) additionally accept a directory that itself IS a
  simulation output, alongside the historical synpp cache-root layout, so the
  standalone CLIs keep working with either input.
  - **Absence is a loud skip, not a failure.** When the flag is on but the
    archive is missing, the MATSim-consuming panels SKIP with a named reason
    recorded in `analysis_suite_summary.json` / a WARNING log — the existing
    readiness contract (`_run`), no silent fallback. A half-written archive
    (missing sentinel) is treated as absent, never consumed.
  - **Rejected: depend on `matsim.output` instead.** `matsim.output` itself
    declares `matsim.simulation.run` whenever `run_matsim` is true (the
    default), so the transitive edge would reproduce exactly the recompute
    this ADR removes.
  - **Rejected: glob the synpp working directory for
    `matsim.simulation.run__*.cache`.** That is the stale-pickle trap: several
    hash dirs can coexist and a glob cannot tell the current config's run from
    an outdated one. The archive is rewritten by every `matsim.output`
    execution and carries provenance.
  - **Accepted trade-off: no ordering edge in a combined invocation.** With
    the stage edge gone, nothing in the graph forces `analysis_suite` /
    `simwrapper_export` to run after `matsim.output` when one invocation runs
    everything. All committed run lists order `matsim.output` first, and a
    mis-ordering is visible, not silent: the panels would skip with the named
    reason. An analysis phase invoked after the run phase (the operating mode
    that motivated #354) always finds the archive on disk.
  - **Accepted trade-off: the archive may predate the current config.** An
    analysis-only invocation consumes whatever the last `matsim.output` wrote;
    that is the point of the change. The logged provenance and the run
    manifest tie every analysis to the producing simulation.
- **Consequences:**
  - An analysis-only invocation (`run:` list of analysis stages only) costs
    minutes: no synthesis/MATSim stage is pulled into its DAG. The production
    DAG loses the two `matsim.simulation.run → analysis` edges
    (`docs/registry/dag/production.json`).
  - The MATSim tabs/panels appear whenever the archive exists — with
    `archive_matsim_output: true` (the committed default) that is every real
    run.
  - Both stage modules changed, so their synpp cache entries devalidate once
    (both are cheap collectors). `braunschweig.analysis.matsim_archive` joins
    the known helper-hash debt of both stages
    (`tests/test_synpp_helper_hash_invariant.py` allow-list, issue #290).
  - Not decided here: why synpp devalidates 26–44 stages per invocation with
    a byte-identical config block (the second cause in #354) — that question
    stays open in the issue.
- **Verification:** `tests/test_matsim_archive_resolution.py` (archive
  resolution incl. half-written-archive refusal, dual-layout `_find_sim_output`
  / `_find_sim_trips`), `tests/test_analysis_suite.py` (no
  `matsim.simulation.run` in `configure`, archive passed as `--sim-cache`,
  loud named skip when absent), `tests/test_simwrapper_stage.py` (same for
  the simwrapper stage).
