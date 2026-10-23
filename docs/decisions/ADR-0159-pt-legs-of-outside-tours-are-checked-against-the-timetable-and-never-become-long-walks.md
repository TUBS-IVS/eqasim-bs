# ADR-0159 · 2026-10-23 · The pt legs of outside tours are checked against the timetable and never become long walks

- **Status:** accepted (implemented; to be measured in the next 1 % rerun of the #465 fixes)
- **Numbering:** 0159 checked free across all local and remote branches and the local worktrees on 2026-10-23
  (0153-0157 are committed on the #465 fix branches; 0158 is reserved by a sibling session of #465).
- **Issue:** #465 (one of the three remaining causes of the residents stuck at the end of the simulated day; no
  new issue, owner decision). **Relates to:** ADR-0145 (the outside-tour mode layer this extends), ADR-0153 (the same
  check for the in-commuters, whose order it follows), ADR-0152 and ADR-0156 (the portal pt stays, which keep their
  own check), ADR-0154 (day-end margin), ADR-0155 (first-replanning mode choice, which never re-chooses an outside tour).
- **Owner decisions (2026-10-23):** no long walks at all ("die Fußwege sind ja absolut unrealistisch -> fixen"); the
  departure shift first (ADR-0153), a mode switch next, a lift as the last resort (ADR-0156).

## Context

- An **outside tour** is a home-based tour with a leg whose realised destination lies outside the cut extent, or a
  leg at an `outside` activity (`outside_tour_modes.tours`). The eqasim `OutsideFilter` keeps its modes for the whole
  run, so whatever mode the synthesis writes is the mode the agent travels with (ADR-0145, ADR-0155).
- Its destination outside the extent comes from the secondary location choice (external Gemeinde centroids,
  ADR-0027) or from the work/education location. Its mode is the survey donor's, or a substitute that
  `outside_tour_modes` draws from the SrV table by distance (pt is always available and always plausible there).
  **Nothing checks a pt leg of such a tour against the timetable.** The portal pt stays have that check
  (ADR-0152/0156), the in-commuters too (ADR-0153); these tours have none.
- `matsim.simulation.prepare` routes the uncut population (eqasim `RunPopulationRouting`, pre-cut network and
  schedule). A pt trip for which the router finds no connection becomes one direct walk (`PlanRouter.fixOnlyWalk`,
  stamped `walkFallbackFrom=pt`). The cutter then cuts that walk at the extent; the boundary times of the cut
  `outside` activities are taken along the walk, which also moves late returns past midnight.
- Evidence, 1 % rerun of the #465 fixes (felix `~/wt-i487-rerun`, prepared population
  `braunschweig_1pct_i465_rerun_population.xml.gz`, read-only probe `~/i465-outside-walks-probe/probe_walks.py`,
  2026-10-23). Residents with an `outside` activity, walk legs over 5 km:
  - 15 legs (14 persons) stamped `walkFallbackFrom=pt` at an `outside` activity that is NOT a portal gate: these
    tours. Departures 09-26 h, mostly evening.
  - 1 tour (person 343377) is a portal stay with the donor mode walk (82.5 km each way) that `outside_tour_modes`
    left unchanged: the person has no car, no lift and no bicycle availability, and a road-gate tour may not take pt
    (`no_candidate_in_gate_class`, logged as a WARNING, 2 such tours in the run).
  - Two of the seven residents stuck at the end of that run are the first kind (125291: a pt leisure tour to an
    external destination at 18:47, both legs without a connection, 29.6 km walks; 905013: the return of a pt
    leisure tour at 22:22 without a connection, 24.7 km walk from 26:48 h), the third (343377) is the second kind.
  - Not this decision: 3 stamped legs at portal gates (6.8-9.1 km; the portal stage, ADR-0152/0156) and walks in
    tours without an outside leg (re-chosen by the mode choice at the first replanning, ADR-0155).

## Decision

1. **Check.** After the SrV re-moding of ADR-0145 (so a drawn pt substitute is checked too), every pt leg of an outside
   tour WITHOUT a portal leg is probed from its realised origin to its realised destination at its departure time,
   with the batch pt router of `braunschweig.data.transit.connections` (production `RunBatchPublicTransportRouter`,
   the pre-cut network and schedule that the prepare routing uses) and its connection rule over ALL stops of the
   schedule (the destination lies outside the cut extent, as for ADR-0153), walk limit
   `plan_check.DEFAULT_WALK_MAX_DISTANCE_M`. Portal tours keep the check of ADR-0152/0156.
2. **Chain** (`braunschweig.synthesis.outside_tour_modes.pt_connections.resolve_pt_tours`), counted per tour, in this
   order (owner order of ADR-0153 and ADR-0156):
   - `connection_found`: every pt leg of the tour is a connection; untouched.
   - `tour_shifted`: the WHOLE tour (every leg, so every activity inside it) moves by the first offset of -step,
     +step, -2 step, +2 step, ... up to the window (`incommuter_pt_connections.shift_offsets_s`, earlier first) at
     which every pt leg is a connection. The tour's first departure stays strictly after the arrival of the
     person's previous trip (at or after 0 s for the first tour) and its last arrival strictly before the departure
     of the person's next trip (so no zero-duration activity arises) and at or before the bound (end of the
     simulated day minus the day-end margin, ADR-0154). The probe runs once per offset for all unresolved tours.
   - `tour_to_car`, then `tour_to_car_passenger`: every leg of the tour switches, when the person has the car
     (respectively lift) availability of `portal_trips.modes.mode_availability`.
   - `tour_to_car_passenger_lift` (last resort `car_passenger`, the default): the tour becomes `car_passenger`
     WITHOUT the passenger availability. ASSUMPTION (as ADR-0156): an out-of-region trip that no timetable serves is
     made as a lift or a taxi, not as a walk of tens of kilometres.
   - `kept_no_connection` (last resort `keep`): kept and warned about; the router will realise it as a walk.
3. **Portal tours without an admissible mode** (`no_candidate_in_gate_class`, a road-gate tour of a person who may
   use neither car, lift nor bicycle): with last resort `car_passenger` they become a lift (`car_passenger`, a road
   mode, so the gate class holds) instead of keeping a walk or bicycle leg of tens of kilometres; counted as
   `n_portal_tours_lifted`. A rail-gate tour always has pt (always available), a mixed-class tour stays unchanged
   (counted, as before).
4. **Times reach the plan.** The population writer and the synthesis output build the activities from the same trips
   they write: a new stage `braunschweig.synthesis.outside_tour_modes.activities_stage` (alias
   `synthesis.population.activities.simulated`) runs the vendored eqasim trips->activities transform on
   `synthesis.population.trips.simulated`. Without a shifted tour it equals `activities.final` (modes do not enter the
   activities). The chain solver, the portal stage and the analyses keep reading `trips.final` / `activities.final`.
5. **Config** (pinned in `configs/base_bs.yml`): `braunschweig.outside_tour_modes.pt_connection_check.enabled`
   (true), `pt_shift_window_s` 7200 and `pt_shift_step_s` 1800 (ASSUMPTION: the values of ADR-0152/0153, not
   calibrated), `pt_no_connection_warn_share` 0.05 (WARNING above that share of the checked tours switched, lifted
   or kept), `last_resort` `car_passenger` | `keep`. The two switches are independent: `last_resort` also governs
   decision 3 when the check is off, so the former modes need BOTH `pt_connection_check.enabled: false` and
   `last_resort: keep`.
6. **Report.** `outside_tour_modes_report.json` carries `pt_connection` (tours checked, actions, median shift, legs
   probed), `n_portal_tours_lifted` and `last_resort`; one log line with the rates. `n_outside_legs_implausible_after`
   is judged after the pt check. A `connection_found` share near 0 % in a real run means a broken probe (the
   unserved share then exceeds the warn share and warns).

## Rejected options

- **Let the router walk** (status quo): the walks of 25-83 km are the defect.
- **Make the simulation tolerate it** (teleport the fallback walk at a higher speed, or route `outside` legs
  differently): changes the simulation, not the plan; the owner principle is to fix plan generation.
- **A per-leg shift** (only the failing leg moves): changes the activity duration of the donor; the tour moves as a
  whole, as the in-commuter's day (ADR-0153).
- **Car first, as for the portal pt stays (ADR-0152):** the owner chose the departure first (ADR-0153).
- **Re-run the secondary location choice with a reachability constraint:** the chain solver has no timetable; a much
  larger change, not needed when the chain above resolves every tour.
- **Check inside the portal stage:** the realised secondary locations do not exist yet there (the chain solver runs
  after it).

## Consequences

- The outside-tour stage now depends on the eqasim jar, the Java runtime and the supply stages (DAG change, stage
  registry), and the population writer and the synthesis output read a new activities stage.
- Output change by design: pt outside tours without a connection move by up to 2 h, drive, or ride as a lift; the
  pt share of the outside tours falls by the switched count (logged; no observed reference of that share exists).
- **Status:** implemented; a rerun should show 0 walk legs over 5 km stamped `walkFallbackFrom=pt` at a non-portal
  `outside` activity, 0 portal tours left unchanged by `no_candidate_in_gate_class`, and none of 125291, 343377,
  905013 stuck. A smoke, not a validation.
