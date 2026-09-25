# eqasim's transit schedule cutter keeps the last inside stop and the original times

`braunschweig.matsim.simulation.prepare` cuts the prepared scenario to the cordon extent
(`_cut_to_cordon` in `braunschweig/matsim/simulation/prepare.py`, which runs
`org.eqasim.core.scenario.cutter.RunScenarioCutter`). Its transit part,
`org.eqasim.core.scenario.cutter.transit.TransitScheduleCutter`, reduces every transit route to
its stops between the first and the last crossing of the extent boundary and drops a route that
keeps fewer than two stops; `NetworkCutter` then keeps the links of the kept routes and stops.

## Two defects, fixed in the fork only

Upstream eqasim-java has both (checked on its master on 2026-09-25). The fork eqasim-java-bs fixes
them on branch `fix/transit-cutter-last-inside-stop`, each pinned by `TestTransitScheduleCutter`
in its core tests:

1. **Last inside stop dropped** (`TransitScheduleCutter.reduceStopSequence`, commit 19fe3d24f). The
   kept sequence ended at the index of the last outgoing crossing. That index is the crossing's
   INSIDE stop, and `subList` excludes its end index, so every route that leaves the extent lost
   its last stop inside it, and a route with two inside stops was dropped entirely (every ICE
   Wolfsburg -> Braunschweig, which leaves the extent after Braunschweig). The population cutter's
   transit crossing finder
   (`DefaultTransitRouteCrossingPointFinder`) already uses `egressStopIndex + 1`; the schedule
   cutter now uses `index + 1`.
2. **Entering routes served early** (`TransitScheduleCutter.reduceRoute`, commit 83312f932). The cut
   route kept the original stops, whose offsets count from the original route's start, but every
   departure was moved earlier by the first kept stop's offset, so a route that enters the extent
   served all its kept stops early by its travel time outside. Departures now keep their times.

## Rules for maintainers

- A cut route may start with a non-zero stop offset. That is intended: offsets and departure times
  stay as in the uncut schedule, so the time at every kept stop is unchanged. Do not "normalise"
  the departures without rebasing the offsets too; rebasing is not needed and would give a negative
  arrival offset at a first stop with dwell time.
- The Braunschweig runs simulate PT by teleporting along the schedule (no transit vehicles, no
  `TransitDriverStarts` events), so a timetable error never fails a run; it only moves routed PT
  times and connections. After any change to the cutter, compare a cut schedule with the uncut
  (mapped) one: same route and departure ids, kept stops = the inside stops between the first and
  last crossing, same time at every kept stop. The scripts of the run manifest
  `transit-cutter-fix-smoke-1pct-2026-09-25` do exactly this.
- The fix is not reported upstream yet (maintainer decision 2026-09-25: keep it in the fork for
  now). When eqasim-java-bs takes a new upstream eqasim-java version, keep both commits and
  `TestTransitScheduleCutter`, or check that upstream fixed both.
- Every cordon run made before the fix used a cut schedule with both defects. Their size in the
  1 % scenario and the check of the fixed cut are recorded in the run manifest
  `transit-cutter-fix-smoke-1pct-2026-09-25`.
