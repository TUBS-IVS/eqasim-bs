# ADR-0187 · 2026-11-06 · The outside-tour timetable check also covers tours with a portal leg

- **Status:** accepted (implemented; replay on the cache of `prod100_20261031` in the run manifest
  `docs/runs/i575-outside-tour-replay-prod100-2026-11-06.yml`)
- **Numbering:** 0186 is the last on `origin/main`; no remote ref or open PR carried 0187 on 2026-11-06; claimed in the
  shared session notes of the #572-#576 sequence.
- **Issue:** #575. **Amends:** ADR-0159 (the outside-tour check skipped tours with a portal leg). **Relates to:**
  ADR-0152 and ADR-0156 (the portal stage's own pt gate check and chain), ADR-0185 (connection rule), ADR-0186 (cut
  timetable; it names #575 as the remaining time-feasibility question).
- **Owner decisions (2026-11-05/06):** check the other legs of portal tours like ADR-0159 (shift first, then car, a lift
  as the last resort); check the portal return to the real destination; for a tour at a rail gate whose legs stay
  unconnected after the shift, lift only those legs instead of choosing a new road gate (see Rejected options).

## Context

- Since ADR-0159 the stage behind `synthesis.population.trips.simulated` probes the pt legs of outside tours, but only
  of tours WITHOUT a portal leg, so that the portal stage stays the single owner of a portal tour.
- The portal stage only checks the stay's own two legs, and it runs before the secondary location choice: it routes the
  return from the gate to the stay's in-region origin, a proxy, while the person may go to a leisure or shop place first.
- So nothing checked the other pt legs of a portal tour, nor the portal return to the place the person really goes to.
  Such legs without a connection become long walks in plans the simulation never re-modes (findings B3 of
  `prod100_20261031`, measured in the issue; model output, not validation).

## Decision

1. **Candidates.** `pt_connections.resolve_pt_tours` checks every home-based tour with a pt leg that is an outside tour
   or contains a portal leg, and probes ALL its pt legs at their written times, the portal pt legs included. The stage
   runs after the secondary location choice, so every leg is probed between its realised endpoints (the portal return
   from the gate to the real next place).
2. **Chain by gate class.** A tour without a portal leg, or with portal legs at a road gate only, keeps the ADR-0159
   chain on the whole tour (connection, whole-tour shift, car, car passenger, lift, keep): a whole-tour switch to a road
   mode keeps a road gate valid.
3. **Rail gate.** A tour with a portal leg by pt sits at a rail exit station (ADR-0141). It gets the same whole-tour
   shift first; when no offset serves every pt leg, only the pt legs without a connection at their written times become
   `car_passenger` WITHOUT the passenger availability (`pt_legs_to_car_passenger_lift`; ASSUMPTION: arriving by train
   and being picked up, as the lift of ADR-0156). A lift is not a chain mode, so the vehicle continuity of the tour
   holds and the rail gate stays valid. With `last_resort` `keep` such a tour is kept and counted.
4. **Observability.** The report (`pt_connection` of the stage report) and the log line give tours checked, pt legs
   probed, legs lifted and every action per class (`none`, `road`, `rail`), with a WARNING per class above
   `braunschweig.outside_tour_modes.pt_no_connection_warn_share`.

## Rejected options

- **Whole tour to car with a new road gate** (the owner's first choice): the gate fixes the stay times and the secondary
  places chosen around it, and this stage cannot rewrite the locations of another stage. It amounts to rebuilding the
  portal stage order, which the owner's triage excluded; the owner agreed to the lift after weighing the cost.
- **Keep and count only:** leaves the walks the issue is about.
- **Move the portal check after the secondary location choice:** the same rebuild of the stage order.

## Consequences

- One tour now has two checks: the portal stage picks the gate (cut timetable, ADR-0186), this stage then checks the
  whole tour at its realised places (pre-cut timetable, as ADR-0159). The second check can only shift times or change
  modes, never the gate.
- Outputs change for every cordon run with portal tours that have pt legs; the replay manifest gives the rates.
- Open: the third group of #575 lies outside this stage's scope (the replay manifest shows that none of its legs is in
  a tour the stage checks). They freeze because the cutter cuts ANOTHER trip of the same inside-only tour whose
  routed path leaves the extent (measured in the replay manifest); this decision makes no change for them, because
  which trips the cutter cuts is only known after prepare's routing.
