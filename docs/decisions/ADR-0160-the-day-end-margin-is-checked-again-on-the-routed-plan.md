# ADR-0160 · 2026-10-23 · The day-end margin is checked again on the routed plan

- **Status:** accepted (implemented; not yet measured in a run, see the status line at the end)
- **Numbering:** 0153-0158 were taken by the sibling branches of #465 and 0159 by `fix/i465-outside-pt-walks`;
  0160-0162 checked free across all local and remote branches and worktrees on 2026-10-23 and claimed for branch
  `fix/i465-last-three-stuck` in the shared session notes (first claimed as 0159-0161, renumbered on the collision).
- **Issue:** #465 (one of the last three residents stuck at the end of the simulated day in the 1 % rerun of the #465
  fixes, felix `~/wt-i487-rerun`). **Amends:** ADR-0154 (the rule and its margin are kept; this record adds a second
  place where they are applied).

## Context

- ADR-0154 moves a day earlier as a whole when its last planned arrival is later than the end of the simulated day
  (30 h, `braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`) minus a margin (ASSUMPTION, 3600 s). The
  population writer measures the arrival with the SURVEY travel times, before MATSim routes the trips.
- Person 1125404 (resident, frozen tour with `outside` activities, not re-chosen by the mode choice): its day ends
  with a car trip home from an outside location. By the survey times the day ends exactly at 29:00 (the writer's
  target); the routed trip in the prepared population takes 1:20:37 (4,837 s, 112.9 km), so the routed last arrival
  is 29.62 h. In the final iteration of the rerun the car departs at 28.26 h on a 132.5 km route with a routed travel
  time of 1.35 h and is still on the road at 30 h (stuckAndAbort). No single link is congested (the longest link
  time on the trip is 42 s); the simulated trip is longer than the routed one because the queue simulation spends at
  least one time step per link on about 600 links. Evidence: felix `~/wt-i487-rerun/analysis/stuck_trace_rerun.csv`,
  the events of `output_rerun/matsim_output` and the prepared population of the rerun
  (`braunschweig_1pct_i465_rerun_population.xml.gz`).
- Measured on that prepared (routed) population with `scripts/measure_routed_last_arrival.py` (replays every
  selected plan as the QSim plans it; run manifest `docs/runs/routed-last-arrival-i465-rerun-2026-10-23.yml`): routed last arrival after 29 h for 19 of 1,468 residents with a frozen tour
  and 35 of 10,573 in-region residents (0.45 % of the residents), after 30 h for 4 and 16; 0 of 521 freight agents.
  The writer had already shifted the days by their survey times. The extreme values (46-52 h) are pt trips the
  router replaced by long walks, the defect class of the walk-fallback stamp (#487), not of the margin.

## Decision

1. **Place: inside the population routing.** eqasim's `RunPopulationRouting` takes `--latest-last-arrival <s>`.
   After a plan is routed, `org.eqasim.core.scenario.routing.DayEndShift` replays it as the QSim plans it
   (`tryEndTimeThenDuration`: an activity with an end time ends at the later of its end time and the arrival, one
   with only a duration that long after the arrival, a leg takes its routed travel time). A plan whose last arrival
   is later moves earlier by the excess, rounded up to a whole second: every activity start and end time by the same
   amount (durations kept, as in ADR-0154), limited by the end time of the first activity (no time before 0 s). The
   plan is then routed again, because pt routes depend on the departure time.
2. **Same rule, same margin.** The prepare stage passes `MAX_PLAN_TIME_SECONDS - margin`
   (`braunschweig.matsim.scenario.day_end_margin.latest_last_arrival_s`, 104,400 s at the default) through
   `matsim.simulation.prepare.population_routing_arguments`. The margin keeps its ADR-0154 meaning (ASSUMPTION,
   3600 s); measured against the routed time it now has to cover only congestion and the per-link time steps.
3. **Switch.** `braunschweig.day_end_margin.routed_check` (default true, pinned in `configs/base_bs.yml`); it acts
   only with `braunschweig.day_end_margin.enabled`. Off: the router is called without the option, the routed
   population is unchanged.
4. **Observability.** The router logs one line: plans with a routed last arrival after the latest arrival, plans
   moved and re-routed, mean and maximum shift, and unresolved plans (no room before 0 s, or still late after the
   re-routing, for example a later pt connection) at WARN when there are any.
5. **Kept:** the writer step of ADR-0154. It still moves the days by their survey times, so most days never reach
   the routed check, and it covers the stages that check their own days at the written times (ADR-0153).

## Rejected options

- **A margin relative to an estimated travel time in the writer** (e.g. crow-fly distance over an assumed speed):
  a second speed assumption per mode, still blind to the timetable waits of pt; the routed time is known one stage
  later.
- **A Python pass over the routed population with a re-routing of the affected persons:** a second read and write
  of the population file plus a merge; the routing step already holds every routed plan in memory and can route it
  again in the same thread.
- **A larger margin for everybody:** moves every late day further only to cover the few long trips, and still
  misses a trip longer than the margin.
- **Run the simulation longer than 30 h:** rejected in ADR-0154 (one constant for the simulated day).

## Consequences

- Output change by design: about 0.45 % of the residents of the 1 % rerun start their day earlier, by at most the
  excess of their routed last arrival (in-region and frozen tours alike; the mode choice later keeps the times).
- The shift is sized by the FIRST-pass routes. Before the second routing, a trip the first pass replaced by a walk
  gets its requested mode back (only when the routing routes that mode), so a late pt trip that walked may be routed
  by pt after the shift; such a plan may then have moved more than its new routes need. The router logs how many
  walk-fallback trips of shifted plans were restored and how many were routed by their mode afterwards (eqasim-java-bs
  `DayEndShift`, review of TUBS-IVS/eqasim-java-bs#45).
- The scenario cutter routes cut trips again without the option; a cut plan keeps the times the routing gave it.
- The prepare stage needs an eqasim-java-bs jar with `DayEndShift` (branch `fix/i465-routed-day-end`); an older jar
  rejects the unknown option and the stage fails.
- **Status:** implemented and tested (eqasim-java-bs `DayEndShiftTest`, eqasim-bs `tests/test_day_end_margin.py`);
  not yet measured in a simulation run. A smoke would not be a validation.
