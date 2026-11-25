# Mode choice reach (`plan_check.leg_reach`)

The one home of the definition of "reached by the mode choice": which trips of a resident plan the discrete mode
choice can re-choose and why the others cannot. Why it exists, the three approaches, the vehicle rule's limit, the
assumptions and the owner decisions are in ADR-0206 (issue #678). The switches are commented at their keys in
`configs/base_bs.yml`; the measured size is in the run manifest
`docs/runs/i678-mode-choice-reach-probe-prod100-2026-11-25.yml`. Nothing here repeats those.

## The rule

`braunschweig.matsim.simulation.plan_check.leg_reach(activities, legs, isolate_outside_trips=..., max_tour_length=...,
restricted_modes=...)` gives every leg the class of its trip (`split_trips`: legs between two main activities). It
mirrors the Java mode choice of the fork eqasim-java-bs:

1. **Base tours.** The trip sequence is cut after every trip whose destination is `home` or `outside` (tour finder
   `ActivityBased` on `[home, outside]`).
2. **Runs.** Without `isolate_outside_trips` a base tour is one run. With it, a base tour is cut into maximal runs of
   consecutive trips that touch an `outside` activity and runs that do not (`ActivityTourFinderWithExcludedActivities`,
   selected as tour finder `IsolatedOutsideTrips`).
3. **Class of a trip**, checked in this order:
   - `fixed_border`: its run starts or ends at `outside` (`OutsideFilter`). With isolation this is exactly the trips
     that start or end at `outside`.
   - `fixed_vehicle`: `isolate_outside_trips` is on, the trip's mode is in `restricted_modes` (`car`, `bicycle`) and
     its home-based tour (cut at `home` only) has a trip that touches `outside` (the border tour rule of
     `BorderTourVehicleConstraint`, bound as `BorderTourVehicleContinuity`; trips whose vehicle the person does not
     have are free legs there and counted, a case the classification does not see).
   - `fixed_length`: its run has more than `max_tour_length` trips (`TourLengthFilter`).
   - `reached`: everything else.

A trip's mode is the routing mode of its first leg, else that leg's mode. Scope: residents only
(`plan_check.RESIDENTS`); in-commuters and freight are not classified. Running without reach settings is a configuration choice, not a per-item fallback. Only the lift can do it (`braunschweig.frozen_pt_walk_lift.all_modes` false): it then uses the older rule `plan_check.leg_tour_frozen` (border only, no length filter) for every trip. The plan check stage always passes the loop settings. The settings are recorded in the lift log line (scope: reach settings of the loop, or frozen tours without reach settings) and in the `reach` key of the lift report and of `plan_check.json`. A plan whose leg count differs from its
activity count minus one raises.

## Which settings a caller passes

The classification of the plan check and of the repair step (`frozen_pt_walk_lift`) uses the settings of the LOOP
(`mode_choice_reach.loop_settings`): their subject is what the loop will not change. The initial mode choice runs with
its own settings (`mode_choice_reach.initial_settings`, a second config file via `write_initial_config`). The
loop settings are applied to the final config by `mode_choice_reach.apply`; the border-vehicle class is checked in the
jar by `require_border_vehicle_constraint`.

## Named cases

H = home, O = `outside`, other letters = inside activities, trips numbered in order. "Off" = coarse rule, limit 6;
"on" = finer rule, limit 8 (test parameters, not the defaults). The Java tests pin these expectations against the
shipped classes first; if a shipped class differs, the Java behaviour is the truth and the Python mirror follows.

| Case | Plan | Off | On |
|---|---|---|---|
| 1 | H-W-S-H | all reached | all reached |
| 2 portal stay in the middle | H-W-O-L-H, all walk | all four fixed at the border | 1 and 4 reached; 2 and 3 fixed at the border |
| 3 outside destination before home | H-W-O-H, all walk | all three fixed at the border | 1 reached; 2 and 3 fixed at the border |
| 4 inside-inside trip cut in two | H-A-O-O-B-H, trip 3 has mode `outside` | all five fixed at the border | 1 and 5 reached; 2, 3 and 4 fixed at the border |
| 5 plan not starting at home | L-W-O-S-H, all walk | all four fixed at the border | 1 and 4 reached; 2 and 3 fixed at the border |
| 6a seven trips, no `outside` | H-a-b-c-d-e-f-H | all fixed by length | all reached |
| 6b nine trips, no `outside` | H-a-...-h-H | all fixed by length | all fixed by length |
| 7 car to the border | H-A-O-H, all car | all fixed at the border | 1 fixed as vehicle leg; 2 and 3 fixed at the border |
| 8 car left behind | H-A-B-O-H: car, walk, pt, pt | all fixed at the border | 1 fixed as vehicle leg; 2 reached; 3 and 4 fixed at the border |

## What uses it

- `plan_check.summarise`: the counters per reason and `n_unreached_unfit`; the invariant is
  `plan_check.check_unreached_invariant` (see `plan-check.md`).
- `frozen_pt_walk_lift`: scope of the repair; the lift conditions and the report are in its module docstring and
  ADR-0206.
- A change of the rule is a change of both sides: the Java classes above and `leg_reach`, with the named cases
  updated in both test suites.
