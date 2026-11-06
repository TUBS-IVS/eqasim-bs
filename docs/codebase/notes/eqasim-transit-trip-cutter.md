# eqasim's population cutter cuts a public-transport trip where it leaves the kept timetable

`braunschweig.matsim.simulation.prepare` cuts the prepared scenario to the cordon extent (`_cut_to_cordon`, which
runs `org.eqasim.core.scenario.cutter.RunScenarioCutter` with `--transit-cut-mode kept-stops` and the hub names
file). For every pt trip the population cutter, `org.eqasim.core.scenario.cutter.population.trips.TransitTripProcessor`,
replaces the parts outside the simulated world by `outside` activities and `outside` legs. Why the rule exists,
what was rejected and the assumptions are in ADR-0186 (issue #574); this note holds what a maintainer must keep.

## The rule (mode `kept-stops`)

- The simulated world of a pt trip is defined per POSITION on a route: a position `(line, route, index)` is in
  the world iff the schedule cutter keeps the route and `first <= index <= last` of its kept range
  (`TransitScheduleCutter.keptStopRanges`, computed BEFORE the population cut on the pre-cut schedule); an
  activity is in the world iff inside the extent polygon; a walk (access, transfer, egress) is simulated iff both
  of its end points are in the world. A ride on a dropped route is outside the world even between two kept
  stations. `KeptStopsTransitTripCrossingPointFinder` emits one crossing point per membership transition
  (in-vehicle as `TransitRouteCrossingPoint`, a walk transition as a `TeleportationCrossingPoint` at the end
  point that is in the world, with the walk's arrival or departure time).
- The crossing points are then collapsed to at most one `outside` activity per outside end: both activities
  inside -> none (one leg, re-routed on the cut timetable); outside origin -> the last entry only; outside
  destination -> the first exit only; both outside -> the first entry and the last exit. The legs of a collapsed
  trip carry `cutTransitCollapse` (`Constants.CUT_TRANSIT_COLLAPSE_ATTRIBUTE`; values `inside_inside`,
  `outside_origin`, `outside_destination`, `through`), which `PlanRouter` copies onto the legs it inserts.
- `PlanCutter` copies the attributes of an activity it replaces by `outside`, and
  `AttributeKeepingMergeOutsideActivities` passes the attributes of every merged-away `outside` activity to the
  survivor (`putIfAbsent`), so `portalGate` and `typeBeforeCutting` survive the cut of a stay at a hub.
- After its final routing `RunScenarioCutter` writes `<prefix>transit_cut_summary.csv` (`TransitCutSummary`:
  kept stops by class, routes kept and dropped, pt trips with crossings, collapsed trips per case and the ones
  routed as a walk); prepare reads it and logs every count as a rate.

## Rules for maintainers

- Mode `extent` (the code default; `braunschweig.cutter.transit_cut_mode`) is the upstream behaviour byte for
  byte: the polygon crossing finders, the default merge, no collapse, no marker, no summary. Every change to the
  kept-stops classes must keep the existing cutter tests green unchanged; the extent-mode tests are the pin.
- The finders need the PRE-cut schedule (they interpret the routed legs with `DepartureFinder`), so the
  population cut must run before `TransitScheduleCutter.run` mutates the schedule; the dry pass gives the ranges
  without mutation. Do not reorder `RunScenarioCutter` so that the schedule is cut first.
- The `outside` activity of a pt entry is placed by `OutsideActivityAdapter` on the nearest ROAD link's facility,
  not at the stop (as for every outside activity): the re-routed piece starts with an access walk the planned
  departure did not include (ADR-0186, consequences). Changing that is a separate decision.
- The collapsed walks are counted in the cutter, not in the plan check: the initial mode choice (ADR-0178) runs
  after the cut and re-routes the non-frozen tours, which loses the marker.
- The in-commuter, student and outside-tour checks of the synthesis keep routing on the pre-cut timetable
  (ADR-0153, ADR-0159): one trip end lies outside the extent. Only the portal stage routes on the cut
  timetable ([portal-trips](portal-trips.md)).
