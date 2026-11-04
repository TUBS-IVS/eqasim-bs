# ADR-0177 · 2026-11-04 · Stages that read the MATSim output archive execute after `matsim.output` whenever both are in one synpp invocation

- **Status:** accepted (owner decision 2026-11-04, option A; implemented on branch
  `fix/analysis-after-matsim-output`; no production run has used it yet)
- **Numbering:** 0177 checked free across all local and remote branches, the sibling worktrees and the open PRs on
  2026-11-04 (highest existing: ADR-0176).
- **Finding:** bug C4 of the findings list of the 100 % production run `prod100_20261031` (felix, launch 1).
- **Amends:** ADR-0101. Its "Accepted trade-off: no ordering edge in a combined invocation" is withdrawn; the rest of
  ADR-0101 (the analysis stages read `<output_path>/matsim_output`, never a stage edge to the simulation; a missing
  archive is a named skip) stays in force.

## Context

- Since ADR-0101 (issue #354) `braunschweig.analysis.analysis_suite` and `braunschweig.analysis.simwrapper_export` read
  the MATSim outputs from the archive `<output_path>/matsim_output` that `matsim.output` writes
  (`braunschweig.analysis.matsim_archive.resolve_matsim_archive`). That is a data dependency on a file, and the synpp
  graph does not know it: both stages depend on `synthesis.output` only.
- synpp executes the stages in `networkx.topological_sort` order (`braunschweig/synpp_fork/pipeline.py`, `run`). In
  networkx 3.4 (the version locally and on felix) that function yields `topological_generations`: layer by layer, a
  stage as soon as its declared parents are done. In the production registry (`configs/base_bs.yml` +
  `configs/overlays/test_100pct.yml`, 108 stages) the two analysis stages sit in the layer after `synthesis.output`,
  before `matsim.simulation.run` and `matsim.output`. In a combined invocation they therefore ALWAYS execute before
  the simulation, by construction, not by chance. ADR-0101 assumed that "all committed run lists order
  `matsim.output` first"; the order of the `run:` list does not set the execution order.
- What happened (launch 1 of `prod100_20261031`, run list = the committed 100 % overlay: `synthesis.output`,
  `matsim.output` and eight analysis stages, ONE `scripts/run_synpp.py` invocation): `simwrapper_export` executed at
  02:16 and `analysis_suite` from 02:17 to 02:56, `matsim.simulation.run` started at 04:53. Both stages logged
  `no MATSim output archive at ../output/matsim_output ... -- MATSim tabs will skip` / `stuck_agents: SKIPPED`,
  `dashboard: SKIPPED` as WARNING lines, completed and were cached. `mid_validation` ran without its simulated
  trips (`--sim-cache` is only passed when the archive exists): it logged `Computing all-trip modal split
  (sim-cache=None)` at INFO and wrote no modal split, the comparison of the commute mode shares with MiD P12_1
  included, with no WARNING or SKIPPED line. The run continued; the outputs lacked their MATSim parts without any
  failure.
- The committed scale overlays `test_1pct.yml`, `test_25pct.yml` and `test_100pct.yml` all mix `matsim.output` with
  both stages in one run list, so every run launched from them has this order. The September production run avoided
  it by hand with a separate analysis phase.
- A second, latent variant: when `<output_path>/matsim_output` already holds the archive of an EARLIER simulation
  (a relaunch into the same output path after a configuration change), the analysis stages read that old archive
  (logged only at INFO with its provenance) before `matsim.output` overwrites it. No occurrence was found in the
  felix and laptop logs searched on 2026-11-04; it follows from the same order.
- The other six analysis stages of the run list read synthesis stages through synpp edges only; they are not
  affected.

## Decision

1. **An ordering-only constraint, declared by the consuming stage.** A stage module may declare
   `_ORDER_AFTER_STAGES`, a tuple of stage names. When a stage of that name is part of the same synpp invocation (in
   the registry), the declaring stage executes after it. The name is matched against both the requested name and the
   resolved module name of every registry entry, so a synpp alias does not break the match.
2. **The fork's `run()` honours it (new patch `order-after`).** The execution order becomes a topological order of
   the dependency graph plus one edge per applied constraint. Nothing else changes: no stage dependency, no stage hash,
   no config key, no result passed; staleness, early cutoff and the flowchart / DAG snapshot keep using the dependency
   graph. A malformed declaration raises `TypeError` and a constraint that makes the order cyclic raises
   `PipelineError`, both before any stage executes. Every applied constraint is logged; a declared stage that is not
   in the invocation is logged as well ("not part of this invocation: no ordering constraint"), so an analysis-only run
   states that it reads an archive left by an earlier run.
3. **The two archive readers declare `matsim.output`.** `analysis_suite` and `simwrapper_export` declare
   `_ORDER_AFTER_STAGES = ("matsim.output",)`, the producer name single-sourced in
   `braunschweig.analysis.matsim_archive`. A contract test requires the declaration in every stage module that resolves
   the archive.

## Rationale

- It fixes the cause (a file dependency missing from the execution order) where the order is decided, and only when
  it matters: in an analysis-only invocation `matsim.output` is not in the registry, so nothing is added and #354
  stays fixed. Combined run lists, including the three committed overlays, become correct as they are, with no
  operator discipline; the latent old-archive variant is closed for combined runs as well.
- Cost measured on 2026-11-04 with `scripts/audit_synpp_helper_hash.py`: `braunschweig/synpp_fork/pipeline.py` is
  hashed by no stage token, so the patch recomputes nothing. The two analysis modules change their own digest; both
  are run targets, which synpp re-executes on every invocation anyway.
- **Rejected: raise when `simwrapper_include_matsim` is true and the archive is missing.** Because of the layer order,
  every combined run list would then fail at the analysis stage, the three committed overlays included; every
  synthesis-only invocation of the production base (the flag is true in `configs/base_bs.yml`) would fail too. It
  turns the trap into a stop instead of removing it, and it does not see an old archive.
- **Rejected: `scripts/isolated_run.py` refuses or splits run lists that mix analysis targets with `matsim.output`.**
  It protects one launcher only (not `scripts/run_synpp.py` called directly); refusing hands the problem back to the
  operator (the September workaround); splitting runs two invocations and duplicates synpp's ordering knowledge in a
  launcher.
- **Rejected: a config key that switches a real `matsim.output` dependency on in combined runs.** The key would have to
  agree with the run list by hand; one mismatch recreates either #354 (an analysis-only run pulls in the simulation) or
  this bug.

## Consequences

- In a combined invocation the two analysis stages run after `matsim.output`, so they read the archive this run just
  wrote. Total wall-clock time is unchanged (synpp executes one stage at a time).
- If the simulation fails, the two stages do not run in that launch (before, their synthesis-only parts were written
  before the simulation started). A relaunch with the same run list writes them.
- A fourth patch in the vendored synpp `run()` (`braunschweig.synpp_fork.PATCHES`, `docs/codebase/notes/synpp-fork.md`),
  to be re-applied when synpp is upgraded.
- A future stage that reads the archive must declare `_ORDER_AFTER_STAGES`; the contract test enforces it.
- Not changed: an analysis-only invocation reads whatever archive the last `matsim.output` wrote, and skips its MATSim
  parts with the named WARNING when there is none (ADR-0101).
- Possible side effect, NOT measured: `analysis_suite` (driver RSS peak 65.19 GiB in launch 1, finding C2) no longer
  runs before the MATSim JVM starts.

## Evidence

- Launch-1 log `felix:~/prod100_20261031/logs/run_20261031T071313.log` (sha256
  `f76a91545c06a063a7febf967c880c18fccc584302deb625a1a70c66f4b89b0a`), lines 49092-49382 (the two analysis stages, their
  WARNING lines) and 51261 (`Executing stage matsim.simulation.run`). Run list: `run.yml.launch1_20261031T071313` in
  the run directory, identical to the `run:` list of `configs/overlays/test_100pct.yml`.
- The layer positions in the production registry (analysis stages in layer 18, `matsim.simulation.run` 19,
  `matsim.output` 20 of 108 stages) are measured by the order probe in the run manifest
  `docs/runs/prod100-analysis-order-2026-11-04.yml`.
- Verification: `tests/test_synpp_fork_order_after.py`. It failed before the fix (synthetic production-shaped graph:
  the analysis executed second, before the simulation chain) and passes after it; it also checks the analysis-only
  invocation (no producer pulled in), the fail-fast cases, and on the real 100 % registry that every stage resolving
  the archive declares the producer and executes after it.
