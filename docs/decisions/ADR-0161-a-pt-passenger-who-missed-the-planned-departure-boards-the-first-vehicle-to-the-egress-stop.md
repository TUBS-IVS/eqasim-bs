# ADR-0161 · 2026-10-23 · A pt passenger who missed the planned departure boards the first vehicle of any line to the egress stop

- **Status:** accepted (implemented in eqasim-java-bs; not yet measured in a run, see the status line at the end)
- **Numbering:** 0153-0158 were taken by the sibling branches of #465 and 0159 by `fix/i465-outside-pt-walks`;
  0160-0162 checked free across all local and remote branches and worktrees on 2026-10-23 and claimed for branch
  `fix/i465-last-three-stuck` in the shared session notes (first claimed as 0159-0161, renumbered on the collision).
- **Issue:** #465 (one of the last three residents stuck at the end of the simulated day in the 1 % rerun of the #465
  fixes). **Extends:** the boarding rules of eqasim-java-bs `TransitBoardingFinder` (zero-slack tolerance and the
  same-line fallback, eqasim-java-bs PR #40).

## Context

- The schedule-based transit engine (`org.eqasim.core.components.transit.EqasimTransitEngine`) boards a passenger
  on the planned route, searched from the arrival at the stop; when that route has no departure left it boards
  the earliest departure of another route of the SAME line. It never looks at other lines.
- Person 693334 (resident, in the region, mode choice active): the plan drives home from work at 15:38 with a routed
  car time of 19:42 min and continues by pt at 16:28 (tram line 6275, route 1346441, boarding 16:34). In the final
  iteration the car trip took 3 h 21 min: 2 h 35 min on one 28 m link (129867, Guntherstrasse, capacity 600 veh/h,
  6 veh/h at the 1 % flow capacity factor), where the departing cars of workplace `work_97721` (45 work activities in
  the 1 % sample) are inserted before the through traffic (MATSim default
  `insertingWaitingVehiclesBeforeDrivingVehicles` = true) and take every 600 s slot from 14:30 to 19:00. The same
  car leg took 0.3-3.3 h across the iterations with written plans (it. 0-45), and the agent was also stuck in
  iterations 35 and 45.
- The agent reached the stop at 19:05. Route 1346441 runs 07:04-18:19; the only other route of line 6275 serving the
  two stops next leaves at 06:17 of the next day (30.28 h, a next-day copy of ADR-0151), after the end of the
  simulated day, so the agent waited past 30 h. Line 15068, the evening tram on the same corridor, serves the same
  access and egress stop at 19:09, 19:24, 19:39, ... (felix, `output_transitSchedule.xml.gz` of the rerun).
- The planned connection itself was valid: the mode choice evaluated it with the plan's routed travel times; the
  agent reached the stop late because of the road delay, not because of the choice.

## Decision

1. **On time: unchanged.** A passenger whose planned route's next vehicle leaves no later than the planned boarding
   time boards it as planned, even when another line would arrive earlier, so the executed plan is the one the
   mode choice evaluated.
2. **Missed: the first vehicle to the egress stop.** When the planned route's next vehicle leaves after the planned
   boarding time (or the route has none left), every route of every line that serves the access stop and, later,
   the egress stop is a candidate, the planned route included; the earliest ARRIVAL at the egress stop wins. Ties go
   to the planned route, then the planned line, then line id and route id in lexical order. The trip keeps its
   planned access and egress stops and its planned distance; the transit event names the line and route boarded.
3. **Switch.** eqasim config `transitStopPairFallback` (default true). Off: the previous rules (planned route, then
   the same-line fallback `transitLineFallback`).
4. **Observability.** The engine's end-of-iteration line adds the share of pt departures boarded on another line
   serving the stop pair, next to the tolerated, later, other-route and missing boardings.

## Rejected options

- **Re-route the rest of the trip at the stop (within-day replanning):** finds connections over other stops too, but
  replaces the plan's legs inside the mobsim; the stop-pair rule covers the observed case with the stops the plan
  already has.
- **Keep the same-line fallback and abort the agent when its departure is after the end of the day:** reports the
  case earlier but leaves it stuck.
- **Change the QSim insertion order or the capacity of link 129867:** these are the cause of the delay, not of the
  stuck agent. Both change the whole road model and need their own decision: the insertion order is the MATSim
  default, and the access of a large workplace through one link is a property of the facility-to-link assignment.
  They are recorded here as observed, not changed.

## Consequences

- Output change by design for late passengers only: in the rerun 7 of 4,854 pt rides boarded later than planned.
  A late passenger may now arrive with another line; on-time passengers are unaffected.
- The finder builds an index of the routes per stop facility once per mobsim (all lines of the schedule).
- **Status:** implemented and tested (eqasim-java-bs `TestTransitBoardingFinder`, including the 693334 case) on
  branch `fix/i465-stop-pair-boarding`; not yet measured in a simulation run.
