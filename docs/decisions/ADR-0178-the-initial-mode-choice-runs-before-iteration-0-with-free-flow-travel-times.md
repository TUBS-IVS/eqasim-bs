# ADR-0178 · 2026-11-04 · The initial mode choice runs before iteration 0 with free-flow travel times

- **Status:** accepted (implemented on branch `feat/i542-initial-mode-choice-before-iteration-0`; unmeasured: no run
  has used it yet, see the status line at the end)
- **Numbering:** 0178 checked free across all local and remote branches on 2026-11-04 (0177 belongs to the sibling
  branch `fix/analysis-after-matsim-output`).
- **Issue:** #542. **Supersedes:** decisions 1-3 of ADR-0155 (#465, mechanism 4); its diagnosis and its rejected
  options stand.
- **Owner decisions (2026-11-04):** the step sits at the end of `matsim.simulation.prepare`, not in an own synpp
  stage; it uses free-flow travel times, "the way eqasim intends it"; eqasim's own mechanism is reused as far as
  possible.

## Context

- ADR-0155 made every agent choose its modes once at the first replanning: a second `DiscreteModeChoice` strategy
  with weight 1e6 and `disableAfterIteration` 1 in the final config, so that in MATSim iteration 1 practically all
  ~1.15 M agents of the 100 % scenario replan inside the loop, with the travel times of iteration 0.
- In the 100 % production run `prod100_20261031` on felix, iteration 1 reportedly took 8 h 23 min, 7 h 37 min of
  it replanning (REPORTED, UNVERIFIED: read from the felix log by the sibling session that diagnosed it; no run
  manifest of that run exists, so the figure is context, not a measurement of this repository). The cause is
  a defect of the DMC contrib's `CachedTripEstimator` (constant `DiscreteModeChoiceTrip.hashCode`, so every cache
  lookup is a linear scan and the replanning time of a thread grows with the square of its persons); eqasim-java-bs
  fixes it on `fix/dmc-trip-cache-quadratic` (`PersonScopedCachedTripEstimator`), independently of this ADR.
- eqasim intends an upfront mode choice before the loop: eqasim-france (`france/main`, `matsim/simulation/prepare.py`
  @ 860772d0) runs `org.eqasim.core.standalone_mode_choice.RunStandaloneModeChoice` as the last step of its prepare
  stage when `mode_choice` is true ("upfront mode choice based on freeflow travel times", eqasim docs
  `simulation_execution.md`), on the unrouted population, replacing `RunPopulationRouting`. The vendored copy of
  that call exists in `matsim/simulation/prepare.py` (flag `mode_choice`, off); ADR-0155 rejected it because it
  replaces the routing (walk-fallback stamp #465/#487, routed day-end check ADR-0160) and runs before the cordon
  cut and the freight injection.
- Verified on eqasim-java-bs `origin/main` (decf09bda) for this decision: `RunStandaloneModeChoice` builds a full
  Controler injector (never run), so every module of `BraunschweigConfigurator` is installed, the parking and VRB
  fare cost models included; without `--travel-times-factors-path` / `--recorded-travel-times-path` the car travel
  time is the uncongested network (fork docs `docs/standalone_mode_choice.md`); the output plans are routed by the
  DMC itself (`EqasimUtilityEstimator` is an `AbstractTripRouterEstimator`, `DiscreteModeChoiceAlgorithm` applies
  `RoutedTripCandidate.getRoutedPlanElements()`, DMC jar 2026.0-2026w12); freight agents get only `truck`
  (`BraunschweigModeAvailability`); tours touching an `outside` activity are filtered by `OutsideFilter`
  (`GenerateConfig`) exactly as in the loop; one `DiscreteModeChoiceModel` with its own trip cache per chunk of
  1000 plans (`StandaloneModeChoicePerformer`, unscoped providers); with
  `standaloneModeChoice.removePersonsWithNoValidAlternatives` false a person without a feasible alternative keeps
  its plan silently, with true it is counted, logged and removed (DMC `fallbackBehaviour` is `EXCEPTION`, the
  default; `RunAdaptConfig` does not change it). The fork reads the option `--eqasim-configurator`; eqasim-france's
  `--eqasim-configurator-class` is accepted by `allowAnyOption` and never read (the jar's `eqasim.properties` then
  applies). The controller has no built-in iteration-0 mode choice.

## Decision

1. `braunschweig.matsim.initial_mode_choice.run` calls `RunStandaloneModeChoice` as the last step of
   `braunschweig.matsim.simulation.prepare.execute` before the plan check, i.e. after the routing, the cordon cut,
   the freight injection and the VRB fare and parking inputs, on the FINAL config and the population file that
   config names (`plans.inputPlansFile`, resolved as the plan check resolves it). Arguments, mirroring eqasim-france:
   `--config-path`, `--config:plans.inputPlansFile`, `--config:standaloneModeChoice.outputDirectory mode_choice`,
   `--config:standaloneModeChoice.removePersonsWithNoValidAlternatives true`, `--config:global.numberOfThreads`
   (`matsim_threads` or `processes`), `--write-input-csv-trips true`, `--write-output-csv-trips true`,
   `--compression gzip`, `--skip-scenario-check true`, `--eqasim-configurator
   org.eqasim.braunschweig.BraunschweigConfigurator`. No travel-time option: free flow, eqasim's documented default.
   The seed is the config's `global.randomSeed` (written by eqasim's `GenerateConfig` from `random_seed`).
2. `mode_choice/output_plans.xml.gz` is moved over the population file under its own name, so
   `matsim.simulation.run`, `matsim.output` and the plan check read the chosen plans without any change; the plan
   check therefore checks the chosen plans (it classifies pt-intended trips by the `walkFallbackFrom` stamp OR
   `routingMode == pt`; the DMC's routing sets `routingMode` but not the stamp).
3. Fail loudly instead of a silent fallback: the persons of the input and the output population are counted and
   the stage raises when they differ (eqasim removed persons with no feasible alternative; such a person would
   abort iteration 1 in the loop anyway, since the replanning rethrows the exception); it also raises when
   `output_plans.xml.gz` or the trips CSVs are missing. No tolerance threshold and no key for one.
4. Observability: from `input_trips.csv.gz` and `output_trips.csv.gz` (eqasim `TripWriter`, residents only: the
   analysis filter skips `pt_` and `freight_` ids) the stage logs under `[initial mode choice]` and writes to
   `initial_mode_choice.json` the persons before/after, the trips, the share of trips whose mode changed, the mode
   shares before and after, and the Java wall time. These are observations of the model for the run manifest,
   never reference values and never a validation.
5. The ADR-0155 mechanism is removed: no strategy is added to the config, `braunschweig.initial_mode_choice.weight`
   is retired (a config that still sets it is refused at configure time with a pointer to this ADR), the switch
   `braunschweig.initial_mode_choice.enabled` (default true, pinned in `configs/base_bs.yml`) now means this step.
   The loop runs stock eqasim (DiscreteModeChoice 0.05, KeepLastSelected 0.95). The vendored flag `mode_choice`
   stays off and untouched.

## Rejected options

- **Keep the 1e6 strategy and rely on the trip-cache fix alone:** the cache fix removes the quadratic cost, but the
  first choice would still happen inside the loop with iteration-0 travel times, against eqasim's intent of a
  free-flow upfront choice, and iteration 1 would still carry a full replanning of every agent.
- **The vendored `mode_choice` flag:** before the cut and the freight injection, and it replaces
  `RunPopulationRouting` (walk-fallback stamp, routed day-end shift); ADR-0155's reasons stand.
- **An own synpp stage after prepare:** would let the mode choice be re-run without the hours of prepare at 100 %
  (useful for the calibration loop of #217), but `matsim.simulation.run`, `matsim.output` and the plan check all
  read prepare's directory, so the stage would have to carry the whole scenario; the owner decided for the end of
  prepare now, to be revisited with #217.
- **Congested travel times** (`--travel-times-factors-path`, `--recorded-travel-times-path` of a previous run):
  there is no reference run to take them from, and free flow is what eqasim documents; a later option.
- **Tolerating removed persons with a threshold:** a tolerated removal silently changes the population; the loop
  would not tolerate it either.

## Consequences

- Output change by design: the loop starts from a population whose modes follow the production model under
  free-flow car travel times. The loop runs the same model; whether its stationary state differs from the one
  reached under ADR-0155 is unmeasured (with plan memory 1 and KeepLastSelected 0.95 it may depend on the start),
  and convergence is not validation either way.
- The DMC's routing drops the `walkFallbackFrom` stamp of re-routed trips; the plan check keeps its pt-as-walk
  figures through `routingMode`. The routed day-end shift (ADR-0160) has already happened in the routing the mode
  choice starts from; the choice itself does not repeat it, exactly as the loop's replanning does not.
- The runtime of the step at 100 % is unknown until measured; the per-chunk model bounds the trip-cache defect
  to the trips of 1000 plans per chunk (ESTIMATE: a few thousand trips, at 3-4 trips per person; not measured)
  even without the C1 fix, which applies here too once merged.
- **Status:** implemented and unit-tested (fakes for the Java step); the first 1 % and 100 % runs after the merge
  record the wall time of the step and of iteration 1 and the mode shares before/after in run manifests. A smoke
  is not a validation.
