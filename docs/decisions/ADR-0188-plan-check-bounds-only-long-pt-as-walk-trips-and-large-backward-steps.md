# ADR-0188 · 2026-11-06 · The plan check bounds only long pt-as-walk trips and large backward steps; the rest is reported in bands

- **Status:** accepted (implemented on branch `fix/i576-plan-check-split`; the split on a production-size population is recorded in the run manifest named under Evidence; a recount of an existing population is not a validation)
- **Numbering:** 0188 checked free across all local and remote branches and the open PRs on 2026-11-06 (highest record anywhere: ADR-0187).
- **Issue:** #576 (finding B3 of the demand-side findings of `prod100_20261031`).
- **Amends:** ADR-0146, decision 2 (which plans and trips the failure rule of the plan check judges). ADR-0146's status line points here.
- **Relates to:** ADR-0146 (plan check, its thresholds and `walk_max_distance_m`), ADR-0167 (gate facilities just outside the extent, the source of most second-scale backward steps of portal stays), ADR-0178 (the walk-only pt trips of the mode choice's routing that the pt-as-walk count includes), #566 (in-commuters counted as residents), #487 (stamp missing on border-crossing trips: the count is too LOW there; this decision is about it being too BROAD).
- **Owner decision:** 2026-11-05 (report separately); thresholds 5 km and 15 min chosen by the owner on 2026-11-06 from the two proposals below.

## Context

- The failure rule of ADR-0146 compares two shares of the frozen residents with their limits: the share of plans with non-monotonic activity end times (`max_frozen_defect_share`) and the share of pt-intended trips realised as walks (`max_frozen_pt_as_walk_share`, #465).
- A read-only recount of the prepared population of `prod100_20261031` (issue #576, Details) found that both numerators mix harmless cases with real defects: about half of the pt-as-walk trips are walks under 2 km, and most non-monotonic plans step back by seconds to a few minutes (portal stays at a road gate whose facility lies just outside the extent, ADR-0167). The two ERROR messages could therefore not tell a broken plan from a harmless one.

## Decision

1. **Bands, report only.** Each block of `plan_check.json` carries `pt_as_walk_by_distance_band` (straight-line distance of the trip, origin of its first leg to destination of its last leg; bands `[0,1)`, `[1,2)`, `[2,5)`, `[5,10)`, `[10,inf)` km) and `non_monotonic_by_step_band` (the plan's largest backward step between consecutive activity end times; bands `[0,1)`, `[1,5)`, `[5,15)`, `[15,60)`, `[60,inf)` min). Every label is always present. The bands are module constants of `braunschweig.matsim.simulation.plan_check` (report layout, not a model parameter).
2. **The bounds judge only the long walks and the large steps.** New counters `n_pt_as_walk_long` (distance strictly above `braunschweig.plan_check.pt_as_walk_bound_min_distance_m`) and `n_plans_non_monotonic_large` (step strictly above `braunschweig.plan_check.non_monotonic_bound_min_step_s`) with their shares `share_pt_as_walk_long` (of `n_pt_trips`) and `share_plans_non_monotonic_large` (of `n_persons`). The failure rule compares `share_plans_late` and `share_plans_non_monotonic_large` with `max_frozen_defect_share`, and `share_pt_as_walk_long` with `max_frozen_pt_as_walk_share`; `threshold.exceeded` names these keys. The limits themselves and `fail_on_threshold` are unchanged.
3. **The totals stay.** `n_pt_as_walk`, `share_pt_as_walk`, `n_plans_non_monotonic` and `share_plans_non_monotonic` are still counted, logged and written; they are no longer compared with a limit.
4. **Nothing MATSim receives changes.** The check reads the written population; no plan, trip or config value is altered.

### Rejected alternatives

- **Keep one number and raise the limits.** A higher limit would hide the same number of real defects as harmless ones; the mix, not the level, made the messages useless.
- **Drop short walks and small steps from the report.** They are observations of the input, and their counts show where the cutter and the gates produce artefacts (ADR-0167); dropping them would make those artefacts invisible.
- **2 km distance threshold.** The committed SrV table has no band boundary at 2 km (its first band is 0-5 km), so 2 km would have no anchor in a committed source.
- **5 min step threshold.** Smaller than one travel-time bin of the router; it would still count steps that lie inside the router's own time resolution.

### Assumptions (stated, not measured)

- ASSUMPTION: `braunschweig.plan_check.pt_as_walk_bound_min_distance_m` 5,000 m. Same anchor as `walk_max_distance_m` (ADR-0146, Assumptions: the SrV table `srv2023_mode_by_distance_band.csv`, where walking is common up to 5 km straight-line and below 1 % in every group beyond, with the one exception named there). A pt trip realised as a walk below it is treated as a plausible walk, not as a defect. It is a threshold for counting trips, not a reference value.
- ASSUMPTION: `braunschweig.plan_check.non_monotonic_bound_min_step_s` 900 s, one MATSim travel-time bin (`travelTimeCalculator.travelTimeBinSize`, MATSim default 900 s). No file under `configs/` sets the key, and no Java source of the eqasim-java-bs core or braunschweig modules sets it (searched 2026-11-06; only the unused switzerland module reads the config group); the bin size of the measured run is recorded in its manifest. A backward step within one bin lies inside the time resolution the router's travel times have, so it is not treated as a defect. It is a threshold for counting plans, not a reference value.

### Limitations

- The bands and thresholds use the straight-line distance and the planned end times, like the rest of the plan check (ADR-0146, Limitations); a short straight line can still hide a long network walk.
- The pt-as-walk count itself stays too LOW on border-crossing trips until #487 is resolved; the split does not change that.
- The in-commuters are counted as residents in the measured run (#566); the split reports them in the resident blocks.

## Consequences

- **No model output changes.** Only `plan_check.json`, the `[plan_check]` log line and the ERROR decision change; no stage downstream reads the changed keys (searched `braunschweig/`, `scripts/`, `configs/`). The prepare stage's token changes (the module is a helper of prepare), so a prepare stage recomputes once.
- **New config keys** `braunschweig.plan_check.pt_as_walk_bound_min_distance_m` and `.non_monotonic_bound_min_step_s`, declared in `braunschweig.matsim.simulation.prepare.configure` and set in `configs/base_bs.yml`.
- **Documentation.** This ADR; ADR-0146 status line; Feature Registry record `plan_feasibility_check`; Stage Registry record `matsim.simulation.prepare`; contributor note `docs/codebase/notes/plan-check.md`.

## Evidence

- Tests: `tests/test_plan_check.py` (bands by distance and by step, the exact-threshold cases 5,000 m and 900 s not counted, trip distance from the first leg's origin to the last leg's destination, every band label present, the failure rule on short versus long walks and small versus large steps, `run()` writing the new keys and thresholds), `tests/test_prepare_plan_check_wiring.py` (both keys declared).
- Split on the prepared population of `prod100_20261031`: run manifest `docs/runs/i576-plan-check-split-prod100-2026-11-06.yml` (a recount with this code; model output, not a validation).
