# ADR-0152 · 2026-10-22 · Portal pt gates are chosen against the timetable, and a pt stay without a connection is resolved by a counted chain

- **Status:** accepted (implemented; measured once at 1 %, run manifest `docs/runs/i465-wpe-smoke-1pct-2026-10-23.yml`,
  a smoke, not a validation; see the status line at the end)
- **Design:** `docs/superpowers/specs/2026-10-22-outside-cell-realism-wave-design.md` (work package C),
  plan `docs/superpowers/plans/2026-10-22-outside-cell-wave-wp-c-timetable-aware-gates.md`.
- **Numbering:** ADR-0152 because 0150 is taken by `chore/ci-is-the-test-gate` (PR #477) and 0151 by the
  sibling work-package-A branch; re-checked across local and remote branches on 2026-10-22.
- **Issue:** #465. **Supersedes:** the rail-exit-station part of ADR-0141 (the gate of a pt stay).
  The rest of ADR-0141 stands. **Relates to:** ADR-0145 (outside-tour modes), ADR-0146.

## Context

- Before this ADR a pt portal stay took the rail exit station with the smallest detour origin -> station ->
  external point (`gates.rail_exit_stations`, ADR-0141). Nothing checked the timetable: neither whether the
  station is served at the stay's times nor whether a connection from the in-region end exists.
- When the router then finds only walk legs, eqasim `PlanRouter.fixOnlyWalk` silently re-routes the trip as
  `walk`; the cordon cutter keeps it as a teleported walk and `OutsideFilter` freezes the tour, so the walk is
  never corrected (issue #465, section "Mechanism").
- Measured, run `i453-i443-ab-1pct-2026-10-16` (1 % A/B, arm 2, final iteration 84), as reported in the
  body of issue #465 (https://github.com/TUBS-IVS/eqasim-bs/issues/465): 58 of the 104 stuck residents were
  walking (issue "Why it matters": stuck residents are those that stopped moving before the end of the
  simulated day). The issue's "Measured" list gives a different count over a different set: 58 of the 66
  residents frozen at the end of the simulation were stuck walking, and 57 of those 58 were planned as pt.
  Of 246 planned-pt portal trips, 90 were walk after prepare, and 33 of 33 departing at 23:00 or later. The run manifest of that A/B is on branch `runs/i453-i443-ab-1pct` (not in `docs/runs/` on
  this branch); the numbers are cited from the issue, which names the run and its outputs on felix.
  Amended 2026-10-22: that manifest is now on main as `docs/runs/i453-i443-ab-1pct-2026-10-16.yml`.
- Gate table and probe, issue comment https://github.com/TUBS-IVS/eqasim-bs/issues/465#issuecomment-6334330264
  (router probe `RunBatchPublicTransportRouter` on the pre-cut schedule, 53 planned-pt portal trips routed as
  walk over 600 m in arm 2, against the 19 gate stations used in arm 2): Sophienhof has 4 departures, all
  10:58 to 13:55, and all 4 of its trips became walks; of 20 trips before 23:00, 13 have pt at another gate and
  7 at no gate (rural home ends whose last bus has left); of 33 trips at 23:00 or later, 7 have pt at another
  gate and 26 at none. A timetable check in the gate choice therefore helps for roughly 8 to 13 of the 20
  daytime cases and not at night, so a counted fallback is needed as well.

## Decision

1. **Exit stops replace rail exit stations.** `gates.exit_stops(schedule, cordon_polygon, modes)` lists, per
   (inside stop, route), the stops of ALL transit modes (config `pt_gate_modes`, default `"all"`) from which a
   route continues to a stop outside the cut extent; the next outside stop fixes the direction. The schedule
   comes from `braunschweig/data/transit/schedule.py` (`read_schedule`, with departures and per-stop offsets).
   The pt gate `kind` constant is renamed from `KIND_RAIL = "rail"` to `KIND_PT = "pt"` (it was read only in
   `gates.py`; the road/pt split is unchanged).
2. **`choose_pt_gates` filters in this order.** Candidates are the exit stops with a route in the stay's
   direction (the served check is directional: a route that leaves toward the stay's point, not "any route
   stops here"). Keep the `gate_candidates` (default 5) with the smallest detour. The stop must be served
   within `[t, t + gate_service_window_s]` (default 3600 s) at the planned gate arrival (outbound) and, with a
   return leg, at the planned re-entry. The router must connect the in-region end to the gate at the planned
   departure and the gate back at the re-entry. The admissible candidate with the smallest detour wins. The
   router runs once per direction as a batch, the return batch only for candidates already connected outbound.
   The return-leg service check reads OUTWARD departures at the gate near the re-entry time as a proxy; the
   inbound service of the return is checked by the router, not by the timetable scan.
3. **Connection rule** (`braunschweig/data/transit/connections.py`): `is_only_walk == 0`, every pt leg's
   access and egress stop inside the cut extent, access, egress and transfer walking each at most
   `plan_check.DEFAULT_WALK_MAX_DISTANCE_M / 1000` km (the constant is read, not copied); or an only-walk result
   with `total_walk_distance_km <= 0.6` km (a walk of that length is plausible, so no connection is needed).
   The Java tool writes no trips row for a task without a route; that is the router outcome "no route", not a
   broken run: the task counts as not connected, under its own rejection reason `no_route` in the log, and the
   run raises only when every task lacks a trips row, or when the output has unexpected or duplicate
   identifiers, or lacks the leg stop-id columns (an eqasim jar without the fork change).
4. **Router.** The eqasim production batch tool `RunBatchPublicTransportRouter`, run through the Java stages
   `matsim.runtime.eqasim` and `matsim.runtime.java` on the pre-cut network and schedule of
   `matsim.scenario.supply.processed` and the vehicles of `matsim.scenario.supply.gtfs`, with a minimal config
   that keeps the MATSim-default `transitRouter` / SwissRailRaptor settings, the same as the simulation (not
   the production prepare config with its own search radius); threads from `matsim_threads` or `processes`
   (volatile).
5. **No-connection chain** (`portal_trips/no_connection.py`), in this order, each counted as
   `no_connection_action` and logged as a rate over the pt stays: `gate_found`; `tour_to_car` (every leg of the
   stay's home-based tour becomes car, needs `can_car`); `tour_to_car_passenger` (needs `can_car_passenger`);
   `departure_shifted` (first grid time in `[t, t + shift_window_s]` with step `shift_step_s` at which a gate is
   found; variants per step: outbound only, return only, then both together. The "both" variant is the
   realistic "whole visit later" case with unchanged durations. An outbound shift keeps `min_stay_s` before the
   return, a return shift keeps the 30 h plan bound and the next trip's departure); `kept_no_connection`. A
   tour switch also switches the other stays of the same tour and counts them under the switch label, so
   `gate_found` counts only the stays whose tour was not switched. A switched stay takes the substitution
   columns of `modes.py` with the new reason `no_pt_connection`. A whole-tour switch counts every changed stay
   in `n_mode_substituted`, including stays of that tour that were not pt.
6. **Kept stays still get a gate.** The plan is kept (design decision 4), and the stay takes the
   nearest-by-detour gate among its directional exit stops, else among all exit stops. The eqasim `fixOnlyWalk`
   path stays the observable last resort; the kept share is counted and warned above
   `no_connection_warn_share` (default 0.05).
7. **Config keys** (prefix `braunschweig.portal.`, in `portal_trips/config_keys.py`): `pt_gate_modes`,
   `gate_service_window_s` 3600, `gate_candidates` 5, `shift_window_s` 7200, `shift_step_s` 1800, `min_stay_s`
   1800, `no_connection_warn_share` 0.05.

## Rejected options

- **A Python RAPTOR re-implementation:** a second truth that can drift from the simulation's router; the
  production Java router is reused instead.
- **Rail-only gates:** buses cross the cordon too, and the probe found daytime connections via other gates.
- **A silent car switch:** every switch is counted and logged, with `can_car` / `can_car_passenger` checked.
- **Relaxing the simulation** (letting pt trips walk, or dropping the freeze): the owner's principle is to fix
  plan generation, not to make the simulation more tolerant.
- **Adding next-morning departures (+24 h) to the schedule** (an option in issue #465): independent of this
  change and does not help rural lines that end in the afternoon; not part of this ADR.

## Consequences

- A synthesis stage now depends on the eqasim jar and the Java runtime (`matsim.runtime.eqasim`; the DAG
  changed), and its `validate()` token hashes the new helper modules. The jar must come from the fork change
  that writes access and egress stop ids per leg (eqasim-java-bs branch `feat/batch-router-leg-stops`).
- The stage also depends on `matsim.scenario.supply.gtfs` (the transit vehicles file the router config names)
  besides `matsim.scenario.supply.processed`.
- Runtime of the batch router at 100 %: ASSUMPTION, under 15 min at 25,000 stays x 5 candidates x 2 directions;
  not measured at 100 %; estimated in WP-E at about 9 min (see the status line below).
- The result changes by design: pt stays without a connection become car, car passenger, shifted or counted
  kept stays, so the mode shares of portal stays move. No committed observed reference exists for that share.
- **Status:** implemented and measured once, 1 % smoke `docs/runs/i465-wpe-smoke-1pct-2026-10-23.yml` against arm 2
  of the #453/#443 A/B (same seed and persons, one draw each, no observed reference). The first real router call
  failed until the router config got the qsim values the eqasim injector needs (fixed with that run). Of 123 pt
  portal stays, 39 kept a timetable-aware gate, 68 were switched to car or car passenger, 2 shifted and 14 kept
  without a connection (warned: 68 % without a gate). Frozen residents stuck walking at the end of the day fell from
  58 to 20 (bounded, not gone; 10 of the 20 are incommuters, whom this ADR does not touch). Router runtime: 10 calls
  of about 30 s at 1 %; 18,300 tasks (the task count of a 100 % sample) took 55 s by hand, so the 100 % assumption
  above holds as an estimate (about 9 min), not as a 100 % measurement. Convergence of the mode shares is not
  validation.
