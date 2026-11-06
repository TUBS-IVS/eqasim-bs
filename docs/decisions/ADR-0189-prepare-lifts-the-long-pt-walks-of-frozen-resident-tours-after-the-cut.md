# ADR-0189 · 2026-11-06 · Prepare lifts the long pt walks of frozen resident tours after the cut

- **Status:** accepted (implemented; replay and MATSim start-up smoke on the prepared population of `prod100_20261031`
  in the run manifest `docs/runs/i575-frozen-pt-walk-lift-prod100-2026-11-06.yml`)
- **Numbering:** 0187 (#575, PR #587) and 0188 (#576, PR #586) are taken; no ref carried 0189 on 2026-11-06; claimed in
  the shared session notes of the #572-#576 sequence.
- **Issue:** #575 (group 3). **Builds on:** ADR-0188 (the plan-check definition of a long pt-as-walk trip, key
  `braunschweig.plan_check.pt_as_walk_bound_min_distance_m`), ADR-0146 (plan check, freeze rule). **Relates to:**
  ADR-0186 (cut), ADR-0156 and ADR-0187 (the lift as the last resort of the synthesis checks).
- **Owner decision (2026-11-06):** repair group 3 in prepare after the cut.

## Context

- A resident tour with every activity inside the extent is checked by none of the synthesis timetable checks.
- In prepare the cutter cuts a trip of such a tour whose ROUTED path leaves the extent, mostly a pt trip, and inserts
  `outside` activities. The whole tour is then frozen for mode choice.
- A pt trip of the same tour that the router could not serve stays a walk of many kilometres in every iteration. The
  replay manifest of #575 (`docs/runs/i575-outside-tour-replay-prod100-2026-11-06.yml`, group 3) measured this cause.
- Which trips get cut is only known after prepare's routing, so the synthesis cannot prevent it.

## Decision

1. **Where.** A step of `braunschweig.matsim.simulation.prepare`, module `frozen_pt_walk_lift`, after the cut, the
   freight injection and the initial mode choice, before the plan check, on the population file MATSim reads.
2. **What.** In the selected plan of every resident: a trip whose home-based tour is frozen (`plan_check.leg_tour_frozen`
   on the cut plan), that is pt-intended (the plan check's rule), whose legs are all walks and whose straight-line
   length is above the plan check's long-walk bound becomes one `car_passenger` leg without route, travel time or
   passenger availability (ASSUMPTION as ADR-0156/0187: a trip no timetable serves is made as a lift, not as a walk).
   The leg keeps its departure time and carries `liftedFrom=pt`; activities and their end times are unchanged. MATSim
   routes the leg at the start of the run (`car_passenger` is a routing network mode in the run's config).
3. **Not here.** In-commuters and freight (their own strategies and scope); non-frozen tours (mode choice repairs them);
   a time shift (after the cut the times are tied to the cut `outside` legs; a shift needs a re-routing in Java).
4. **Switch and observability.** `braunschweig.frozen_pt_walk_lift.enabled` (production on);
   `frozen_pt_walk_lift.json` and one log line give the frozen pt-intended trips, the all-walk ones, the lifted ones (by
   stamp or routing mode only, by distance band) and their rate, WARNING above
   `braunschweig.frozen_pt_walk_lift.warn_share` (ASSUMPTION).

## Rejected options

- **Check every resident tour in the synthesis:** would change many tours that mode choice repairs anyway, and still
  cannot know which tour the cut freezes.
- **Do not freeze tours whose `outside` activities come only from a pass-through cut:** a change of eqasim's freeze rule
  with effects on every cut trip; out of scope.
- **Re-route the lifted trip in Java inside prepare:** larger; MATSim's start-up routing does the same.

## Consequences

- The plan check counts fewer long pt-as-walk trips among the frozen residents; the lifted trips appear as
  `car_passenger`.
- The planned lateness of the plan check treats the missing travel time of a lifted leg as 0 s (a lower bound).
- MATSim starts on lifted plans and simulates the lifted legs (start-up smoke in the run manifest); the effect on the
  simulated day of the whole population is not measured.
- Not covered: walk trips that the synthesis itself made long (mode `walk` from the start, not pt); the run manifest
  records one such case as a side finding.
