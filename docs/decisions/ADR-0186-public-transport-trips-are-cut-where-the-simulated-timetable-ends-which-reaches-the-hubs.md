# ADR-0186 · 2026-11-06 · Public-transport trips are cut where the simulated timetable ends, which reaches the configured hubs

- **Status:** accepted (implemented; the portal check is replayed on the cache of `prod100_20261031` against main,
  run manifest `docs/runs/i574-portal-replay-prod100-2026-11-06.yml`; the population cut is measured by the next
  production run)
- **Numbering:** ADR-0186 because 0183-0185 are taken on main and the sibling branches (checked across all local
  and remote refs and the shared session notes on 2026-11-06; claim in the notes of the #572-#576 sequence).
- **Issue:** #574 (closes the "excursion" family behind B2 cause 2 and B3 mechanism A of the findings on
  `prod100_20261031`). **Amends:** ADR-0152 and ADR-0184 (the pt gate candidates and the connection check),
  ADR-0167 (a pt gate facility may lie outside the cut extent), the fork's transit cutter rules
  (`docs/codebase/notes/eqasim-transit-schedule-cutter.md`). **Relates to:** ADR-0141 (portal trips),
  ADR-0153 and ADR-0159 (the in-commuter and outside-tour checks keep routing on the pre-cut timetable),
  ADR-0183 and ADR-0185 (the other portal rules of the sequence), #575 (time feasibility of unchecked legs,
  the follow-up on the same mechanism), #487 (the walk-fallback marker).
- **Owner decisions (2026-11-05/06):** check the portal connection on the timetable MATSim runs; keep the
  timetable to the Oberzentren reachable by rail so that long trips leave the model where the traveller changes
  trains; the hub list below; no new issues for the parts this record covers.
- **Code:** eqasim-java-bs core `scenario.cutter.transit.TransitScheduleCutter` (anchor rule, `keptStopRanges`),
  `TransitHubStops`, `RunTransitScheduleCutter` (CLI), `scenario.cutter.population.trips.crossing.transit.KeptStopsTransitTripCrossingPointFinder`,
  `TransitTripProcessor` (collapse, marker), `PlanCutter` and `AttributeKeepingMergeOutsideActivities`
  (attributes), `TransitCutSummary`, `RunScenarioCutter` (`--transit-cut-mode`, `--transit-hub-stop-names-path`),
  `scenario.routing.PlanRouter` (marker survives routing); eqasim-bs `braunschweig/data/transit/cut_schedule.py`
  (stage), `braunschweig/synthesis/portal_trips/gates.py` and `stage.py`, `braunschweig/matsim/simulation/prepare.py`
  (`_cut_to_cordon`, the guard, the summary reader), `configs/base_bs.yml` (`braunschweig.cutter.*`).

## Context

- The synthesis decided whether a pt trip is feasible on the 45 km timetable (`matsim.scenario.supply.processed`),
  MATSim ran on the timetable cut to the extent, and eqasim's cutter split every pt trip at every crossing of the
  extent polygon and re-routed the inside pieces on the cut timetable. Every disagreement between the three became
  a walk in a frozen tour, which no iteration repairs. #465, #487, #488, #528, #573, #574 and #575 each patched one
  trip class on the Python side; the cutter itself was never changed.
- Two mechanisms, both visible in the findings on `prod100_20261031` (model output, the numbers are in the run
  manifests of this record and of ADR-0185): the fastest route transfers at a station just outside the extent
  (Hildesheim Hbf, Lehrte, Hannover Hbf and their local networks), so the portal check rejected it ("outside stop")
  and the cutter split it; and a pt trip whose two ends lie inside the extent was split when its route left the
  extent, its inside piece re-routed at the original time and realised as a walk.
- The cut extent is the ZGB municipalities buffered by `cordon_network_buffer_fraction`; the timetable and the
  roads are read for the ZGB plus `cordon_network_source_buffer_m`, so the stations of the surrounding
  Oberzentren are in the pre-cut timetable but not in the cut one.

## Decision

1. **Hubs in the timetable (anchor rule).** `braunschweig.cutter.transit_hub_stop_names` lists exact stop names.
   A transit route is kept iff at least one of its stops lies inside the extent polygon; its kept stops run from
   its first to its last anchor, where an anchor is a stop inside the polygon or a hub stop. Java resolves each
   name to every facility with that name (exact, case-sensitive) and fails on an unmatched name. An empty list
   gives the former cut exactly (pinned by the cutter's tests). The production list is owned by
   `configs/base_bs.yml` (`braunschweig.cutter.transit_hub_stop_names`; owner decision 2026-11-06: the
   Oberzentren with rail routes from the extent plus the Harz stations and Uelzen, an ASSUMPTION that these are
   the stations where the region's long-distance travellers change trains). Stations between the extent and a
   hub on the same line (Lehrte, Haldensleben) are kept by the rule itself.
2. **Kept-stop cut of pt trips (`--transit-cut-mode kept-stops`).** A pt trip is cut where its pre-cut route
   leaves the KEPT timetable, never where it crosses the polygon. Membership is per position on a route (a route
   kept with the range `first..last`): a ride on a dropped route is outside the simulated world even between two
   kept stations; an activity is in the world iff inside the polygon; a walk is simulated iff both its end points
   are in the world. Crossing points are the membership transitions: in-vehicle transitions as before (with the
   position test), a walk transition at the end point that is in the world with the walk's arrival (entry) or
   departure (exit) time.
3. **At most one `outside` activity per outside end (collapse).** Both ends inside: the trip stays one leg and is
   re-routed on the cut timetable (a walk there is an in-region walk fallback the mode choice may revisit, because
   the tour no longer touches `outside`). Outside origin: only the last entry. Outside destination: only the first
   exit. Both outside: the first entry and the last exit (a trip that never boards a kept position stays an
   `outside` leg). Every simulated piece is the inside prefix or suffix of the real route and therefore exists on
   the cut timetable by construction. The legs of a collapsed trip carry the String attribute `cutTransitCollapse`
   (`inside_inside`, `outside_origin`, `outside_destination`, `through`), which `PlanRouter` copies onto the legs
   it inserts.
4. **Attributes survive the cut.** `PlanCutter` copies the attributes of an activity it replaces by an `outside`
   activity, and the merge of consecutive `outside` activities passes every removed activity's attributes to the
   activity that absorbs it (survivor wins), so `portalGate` and `typeBeforeCutting` of a stay at a hub survive.
   Consequence: after a merge the surviving crossing activity carries the attributes of the real activity it
   absorbed, not of its own location (a reader of `typeBeforeCutting` sees the stay, at the crossing's coordinate).
5. **Portal pt gates and the check.** The exits of a pt portal stay are read from the cut timetable: the last
   kept stop of every kept route whose pre-cut route continues beyond it, plus the hubs as rows of their own
   (`is_hub`). A hub heads toward every external point and counts as served (ASSUMPTION: an Oberzentrum has
   onward service; the long-distance feed is not in the model since #431, and the connection check decides whether
   the station is reachable at the time). The detour rule, the extra-detour cap (ADR-0184), the candidate cut,
   the return proxy of the non-hub rows and the resolution chain (ADR-0152, ADR-0183, ADR-0185) are unchanged. The
   batch router routes on the cut timetable (pre-cut network, gtfs vehicles) with the cut timetable's stops as
   `inside_stop_ids`, so the rejection reason `outside stop` cannot occur; a routed stop outside the cut timetable
   is a wiring defect and raises. The pt gate of a stay at a hub lies outside the extent; the cutter turns the
   stay into `outside` and the ride to the hub is simulated.
6. **One truth.** The synthesis builds its cut timetable with the SAME Java cutter, extent helper and hub list
   (stage `braunschweig.data.transit.cut_schedule`, CLI `RunTransitScheduleCutter`); prepare passes the same mode,
   hubs and extent to `RunScenarioCutter` and fails when its cut timetable differs from the stage's in any route
   or stop sequence.
7. **Observability.** The cutter logs the kept stops by class, the routes kept and dropped, the trips collapsed
   per case and, after its final routing, the collapsed trips that walk, and writes them to
   `<prefix>transit_cut_summary.csv`; prepare logs every count as a rate and WARNs above
   `braunschweig.matsim.simulation.prepare.COLLAPSED_WALK_WARN_SHARE` (an ASSUMPTION, not a calibrated bound,
   documented at the constant). The portal stage reports the hub gates
   chosen and the size of the cut timetable. The hub table (`hub_stops.csv`) records the resolved facilities.
8. **Switch.** `braunschweig.cutter.transit_cut_mode` (`extent` = the upstream behaviour, the code default;
   `kept-stops` pinned in `configs/base_bs.yml`); hub names are refused with `extent`.

## Rejected options

- A Python copy of the cut rule for the check: two rules drift (the shape rule forbids a second home).
- Routing the whole population on the cut timetable in prepare: every trip with an end outside the extent has no
  stop near that end on the cut timetable and would walk.
- Keeping the per-crossing split and slicing the pre-cut route instead of re-routing: correct, but a deep change
  of eqasim's cutter and router; the collapse gives feasibility with a small, testable change.
- Enlarging the extent so that the transfer stations lie inside (`cordon_network_buffer_fraction`): a modelling
  decision of the owner that moves every gate and in-commuter home and needs a new production run; deferred, the
  key exists, the hubs make it less pressing. The distances of the stations beyond the extent are in the run
  manifest of this record.
- Treating the hubs as "inside" for activities: a shop in Hildesheim would be a real activity when reached by
  train and an `outside` stay when reached by car.
- Counting the collapsed walks in the plan check: it runs after the initial mode choice (ADR-0178), which
  re-routes the non-frozen tours and loses the marker, so the count would read low for the wrong reason; the
  cutter counts before that.

## Consequences

- Every hub gets, like every in-commuter home in the ring today, an `outside` facility on the nearest road link
  and with it a car path to the reference link in the cut network (`OutsideActivityAdapter`,
  `MinimumNetworkFinder`): road spines to the hubs that inside car trips could only use as a detour; measured in
  the 1 % A/B. The kept vehicles carry simulated passengers outside the polygon (a hub to Lehrte, say).
- Outputs change for every cordon run: the pt gates, the connection rate, the cut plans of every pt trip with an
  outside end and of every inside-to-inside trip whose route left the extent. The run manifests give the rates.
- The in-commuter, student and outside-tour checks keep routing on the pre-cut timetable (one trip end lies
  outside the extent); decision 3 makes their simulated part feasible. #575 (the other pt legs of portal tours,
  the return to the real destination) is now a time-feasibility question only; those legs are never split.
- A pt trip whose routed result is a walk only (the walk fallback kept the routing mode pt) crosses the world at
  the inside activity's coordinate under decision 2 (an activity is the only world point of such a trip), where
  the former cut placed the crossing on the polygon boundary; its outside piece is a zero-length leg the final
  routing walks. Counted with the other collapsed cases; the 1 % A/B checks its size.
- Known limitation, shared with the former cut and not changed here: the `outside` activity of a pt entry sits
  on the nearest road link's facility, not at the stop, so the re-routed inside piece starts with an access walk
  the planned departure did not include; an agent whose planned wait is shorter boards the next service.
- #487: fewer trips are split, so more `walkFallbackFrom` stamps survive; the 1 % A/B counts them, the defect of
  the marker on split trips is not addressed here.
- Status: the portal check is replayed on the cache of `prod100_20261031` against main in
  `docs/runs/i574-portal-replay-prod100-2026-11-06.yml` (model output, no validation claim; the numbers live there).
  The population cut (decisions 2-4: the collapsed trips, the walks among them, the road spines to the hubs) is not
  replayed: the owner decided on 2026-11-06 against a 1 % prepare A/B, so it is measured by the next production run
  (#22) through `<prefix>transit_cut_summary.csv` and the plan check.
