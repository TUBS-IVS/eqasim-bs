# Plan check (`braunschweig/matsim/simulation/plan_check.py`)

Maintenance rules for the check that runs on the written MATSim population: what it reads, how it classifies
plans and legs, which thresholds raise and how to read its output. Why the check exists, what was rejected and
the assumptions are in ADR-0146 (issue #443); the production state is the Feature Registry record
`plan_feasibility_check`; the stage semantics are in the Stage Registry record `matsim.simulation.prepare`.
Nothing here repeats those.

## What it reads

`plan_check.run(context, config_name)` is the last step of `braunschweig.matsim.simulation.prepare.execute`,
after the cut, the freight injection and the fare inputs, so the checked file is the one MATSim reads. In this
order it:

1. checks that the written config `<config_name>` exists in the stage directory (`FileNotFoundError` otherwise);
2. asserts that `qsim.endTime` of that config equals `braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`
   (`ValueError` on a difference);
3. resolves the population path with `resolve_population_path`: the value of `plans.inputPlansFile` of the
   written config, relative to the directory of that config. The file name is never assumed (eqasim's
   `GenerateConfig`, `RunPreparation`, the cutter and the freight injection own it); a config without the
   param, or a path that does not exist, raises;
4. streams the population once with `iter_person_plans` (`iterparse`; each `person` element is cleared AND the
   root is cleared, so finished persons do not stay attached to the root; the selected plan, else the first plan,
   of each person is read) and reduces it with `summarise`, which raises `ValueError` naming the person when a
   plan has more legs than activities - 1;
5. logs the rates under `[plan_check]`, writes `plan_check.json` (with the `threshold` block) and only then calls
   `check_thresholds`, so the report exists when the check raises.

## Frozen and in-region

The class comes from the home-based tours of the WRITTEN plan (`leg_tour_frozen`): a tour starts at every leg
that departs from a `home` activity, and a tour is frozen when any of its legs starts or ends at an `outside`
activity. These are the tours the production discrete mode choice never re-chooses (`OutsideFilter`, see
ADR-0145). The two levels differ on purpose:

- **Plan-level metrics** (`n_persons`, `n_plans_late`, `n_plans_non_monotonic` and their shares) classify the
  PERSON: `frozen` when at least one of their tours is frozen, else `in_region`.
- **Leg-level metrics** (`n_legs`, `n_walk_legs_over`, `n_bicycle_legs_over`, `n_legs_over_duration`)
  classify each LEG by its own tour.

A plan is LATE (`n_plans_late`) when an activity END time exceeds the simulated day; it is ARRIVAL-LATE
(`n_plans_arrival_late`, report only) when its last planned arrival, the end time of the origin activity of the
last leg plus that leg's `trav_time`, exceeds the simulated day (a last leg without either value is not
counted); it is non-monotonic when an activity end time is earlier than the previous one. Activities without an
end time (the last activity) take no part in the first and third test. Both lateness metrics use PLANNED times:
a plan can still be stuck at the end when the routed travel time is longer than the planned one (ADR-0146,
Limitations). Distances are straight-line metres between the coordinates of the adjacent activities.

## Thresholds and which ones raise

| Key | Default | Effect |
|---|---|---|
| `braunschweig.plan_check.enabled` | `true` | `false` skips the whole check |
| `braunschweig.plan_check.walk_max_distance_m` | 5000 | counts walk legs over it |
| `braunschweig.plan_check.bicycle_max_distance_m` | 30000 | counts bicycle legs over it |
| `braunschweig.plan_check.leg_max_duration_s` | 10800 | counts legs with a `trav_time` over it |
| `braunschweig.plan_check.max_frozen_defect_share` | 0.005 | ERROR log, or RAISES with the next key |
| `braunschweig.plan_check.max_frozen_pt_as_walk_share` | 0.02 (default declared in configure() and pinned in `configs/base_bs.yml`) | share of frozen-resident legs that are pt replaced by walk (`walkFallbackFrom`); ERROR log, or RAISES with the next key (ASSUMPTION) |
| `braunschweig.plan_check.fail_on_threshold` | `false` | `true` makes the thresholds above raise |

The pt-as-walk counter (`n_pt_as_walk`, `pt_as_walk_by_hour`, `share_pt_as_walk`) depends on the eqasim-java-bs `PlanRouter` change that stamps `walkFallbackFrom`; until that is merged and the jar rebuilt it stays 0.

Only the threshold acts, and only for the `residents` subpopulation in the `frozen` class: when the share of
late plans or of non-monotonic plans exceeds it, the check logs at ERROR and continues
(`fail_on_threshold` false, production) or raises (true). The shares are ASSUMPTIONS until the A/B of #443
calibrated them, which is why the production value does not abort a run. Every other figure, `in_region` residents, `incommuter`,
`student_incommuter` and `freight`, is reported and never fails the stage (the mode choice repairs the
in-region tours; issue #444 owns the freight defects). All defaults are ASSUMPTIONS (ADR-0146); the production
values live in `configs/base_bs.yml` only, and the defaults are declared once as constants in `plan_check.py`.

## Reading `plan_check.json`

It sits in the prepare stage's cache directory. Top level: `simulation_end_s` (the bound used),
`thresholds` (the three metric thresholds as run), `threshold` (`max_frozen_defect_share`,
`fail_on_threshold`, `exceeded`: the names of the failure-rule shares over the limit) and `subpopulations`.
Under each subpopulation the keys `frozen` and `in_region` each hold `n_persons`, `n_legs`, `n_plans_late`,
`n_plans_arrival_late`, `n_plans_non_monotonic`, `n_walk_legs_over`, `n_bicycle_legs_over`,
`n_legs_over_duration`, `share_plans_late`, `share_plans_arrival_late` and `share_plans_non_monotonic`. The shares are shares of `n_persons` of that block. A subpopulation key is
whatever the written file carries (persons without the attribute count as `residents`).

## The one-constant rule for the simulated day

The simulated day exists ONCE as code: `MAX_PLAN_TIME_SECONDS` in `braunschweig/popsim/plan_validation.py`. The
synthesis uses it as the plan-time bound; the plan check uses it as the end of the day and asserts that the
written `qsim.endTime` agrees. A change of the simulated day is therefore one edit of that constant plus the
change of what writes `qsim.endTime`, and the assertion fails the prepare stage when they disagree. Do not
introduce a second constant or a config key for the bound.

## Invariants a change must keep

- **The check reads the written population, not a pipeline stage's output.** The population file is the one
  MATSim reads, found through the written config.
- **`plan_check` stays in the prepare token.** `plan_check` is in `_HELPER_MODULES` of `prepare.py`, and the
  modules of its import closure that live in other packages (`braunschweig.popsim.plan_validation`,
  `braunschweig.popsim.closure_dwell`, `data.hts.hts`) are in `_DEFERRED_HELPER_MODULE_NAMES`; a new import in
  `plan_check` must be added there or the closure gate of ADR-0136 fails.
- **Only the frozen residents are judged.** Adding a threshold for another class, or turning `fail_on_threshold` on in production, is a decision and belongs in an ADR.
- **The stuck-agent KPI is not here.** It reads the events of the final iteration and is implemented in the
  stuck-agent analysis (issue #443, part 2); this module sees only the input of the simulation.
