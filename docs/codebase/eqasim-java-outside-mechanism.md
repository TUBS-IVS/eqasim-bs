# eqasim-java — cross-boundary "outside" mechanism (focused study)

> `/acquire-codebase-knowledge` focus study of `eqasim-org/eqasim-java` (shallow
> clone at `../eqasim-java-ref`) + the eqasim Python pipeline, scoped to how eqasim
> implements cross-boundary "outside" trips, so the eqasim-bs cross-cordon module
> is integrated the exact eqasim-idiomatic way. (The eqasim-bs codebase map lives
> in the other docs/codebase/*.md; this file is the external-dependency study.)

## TL;DR — eqasim already does the cordon, natively

eqasim has a built-in **scenario cutter** that cuts a full scenario to a sub-area
and turns every boundary-crossing trip into an `outside` activity + teleported
`outside` leg, which Discrete Mode Choice then holds **fixed**. This is exactly the
cordon concept. Drive our cordon through this machinery rather than hand-rolling a
network clip + a custom subpopulation mode-fix.

## Components (with evidence)

### 1. "outside" primitives
- `core/.../misc/Constants.java`: `OUTSIDE_MODE = "outside"`, `OUTSIDE_ACTIVITY_TYPE = "outside"`.
- `outside` is a **teleported** mode (instant) with a **ZERO utility estimator** -> DMC
  never *chooses* it, only keeps it where it is the initial mode.
  Evidence: `scenario/config/GenerateConfig.java:130` (teleport speed 1000), `:188`
  (`outside` -> ZERO_ESTIMATOR).

### 2. DMC keeps outside trips fixed
- `simulation/mode_choice/constraints/OutsideConstraint.java`: a trip with
  `getInitialMode()=="outside"` must stay `"outside"` (and only such trips may be).
  Registered as a DMC **trip constraint**.
- `simulation/mode_choice/filters/OutsideFilter.java`: a tour whose **start origin**
  or **end destination** activity `type=="outside"` is **excluded from DMC tour
  processing entirely**. Registered as a DMC **tour filter**.
- `GenerateConfig.java`: `dmcConfig.setPerformReroute(false)` (ReRoute is a separate
  replanning strategy, so an outside-fixed tour can still be rerouted); tourFinder
  activity types `["home","outside"]`.

**=> The eqasim way to fix a mode: give the boundary activity `type="outside"`
(OutsideFilter excludes its tour from mode choice) and/or the external leg
`mode="outside"` (OutsideConstraint locks it). No custom subpopulation strategy
needed.**

### 3. The scenario cutter (the cordon tool)
- Entry: `org.eqasim.core.scenario.cutter.RunScenarioCutter` (docs:
  `eqasim-java/docs/cutting.md`).
- CLI (REQUIRED_ARGS = config-path, output-path, extent-path):
  `--config-path <full scenario config> --output-path <out> --extent-path
  <single-polygon shp/GeoPackage, no holes, correct CRS> [--prefix --threads
  --config:plans.inputPlansFile <plans> --skip-routing ...]`.
- Extent: `extent/ShapeScenarioExtent` reads .shp **or GeoPackage**;
  `isInside = polygon.contains(point)`. `CircularScenarioExtent` = disc.
- Cuts network/facilities/population/transit; `outside/OutsideActivityAdapter`
  snaps each `type=="outside"` activity to the nearest cut-network link/facility and
  opens/closes the day at outside boundaries.
- `gis/` ships `paris_20km.shp`, `zurich_20km.shp` (region + 20 km buffer) ->
  confirms the "region + buffer" extent pattern == our RVB + 10 % buffer.

### 4. Python hook
- eqasim Python drives Java via `eqasim.run(context, "org.eqasim...Run*", [...])`
  (e.g. `matsim/simulation/prepare.py` runs RunGenerateConfig / RunPopulationRouting).
- eqasim-bs `matsim/` does NOT yet invoke the cutter (`grep cutter matsim/` empty) ->
  a `RunScenarioCutter` hook must be added.

## Revised integration recipe (eqasim-idiomatic)

1. **Cordon extent** = dissolved RVB + buffer (`braunschweig.data.spatial.cordon`)
   exported as a **single-polygon GeoPackage** (EPSG:25832) -> `--extent-path`.
2. **Auspendler**: place work at the **real external Kreis coords (outside the
   extent)**; the cutter converts those trips to `outside` natively. (Replaces the
   EXT-workplace-inside-network approach + the custom clip.)
3. **Einpendler** (no native inbound in eqasim — documented limitation): keep our
   **injection** (demand + plans + Mikrozensus mode + gate placement), tagging the
   in-commuter **home activity `type="outside"`** so OutsideFilter fixes the mode;
   the in-ZGB leg keeps a real (Mikrozensus) mode and loads the network.
4. **Mode-fix**: native via OutsideFilter/OutsideConstraint ->
   `braunschweig/matsim/simulation/cordon_subpopulation.py` becomes a *fallback*.
5. **Network clip**: the cutter's `NetworkCutter` (not a custom clip stage).
6. **Keep** our gates (placement + validation), demand/plans/mode reference, and the
   validation CSV/GPKG outputs — eqasim provides none of these.

## What this changes vs. our spec/plan (2026-06-05)
- Phase 3 "supply-ring clip" -> **use RunScenarioCutter**, not a hand-rolled clip.
- "auspendler -> outside" -> **native via the cutter** (place work outside the extent).
- "subpopulation mode-fix" -> **native via OutsideFilter** (tag home `outside`);
  keep `cordon_subpopulation.py` as fallback.
- Our pure modules (foundation, gate_entry, demand, plans, mode_reference, validation,
  outputs) are unaffected and still needed.

## Open decision
- `[ASK USER]` Adopt the build-then-cut flow (build a scenario with external
  endpoints, then `RunScenarioCutter` to the RVB+buffer extent) — the eqasim-blessed
  path, but it changes the build order — vs. direct-build + manual `outside` tagging?

## Evidence (paths under ../eqasim-java-ref)
- `core/.../misc/Constants.java`
- `core/.../simulation/mode_choice/constraints/OutsideConstraint.java`
- `core/.../simulation/mode_choice/filters/OutsideFilter.java`
- `core/.../scenario/config/GenerateConfig.java` (130,163,170,178,188)
- `core/.../scenario/cutter/RunScenarioCutter.java` (REQUIRED_ARGS:47)
- `core/.../scenario/cutter/extent/ShapeScenarioExtent.java`
- `core/.../scenario/cutter/outside/OutsideActivityAdapter.java`
- `docs/cutting.md`; `gis/*_20km.shp`
- eqasim-bs `matsim/simulation/prepare.py`; `grep -r cutter matsim/` (empty)
