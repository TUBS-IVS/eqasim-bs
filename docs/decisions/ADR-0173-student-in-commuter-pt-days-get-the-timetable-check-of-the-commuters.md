# ADR-0173 · 2026-10-30 · The pt days of student in-commuters get the timetable check of the commuter in-commuters

- **Status:** accepted (implemented; not yet run, see the status line at the end)
- **Numbering:** 0173 checked free across all remote branches and open PRs on 2026-10-30 (highest taken: 0172).
- **Issue:** #528. **Relates to:** ADR-0153 (the check and its chain, #488), ADR-0172 (the station re-draw and the pt
  re-balancing of the commuter in-commuters, #467), ADR-0154 (day-end margin).
- **Owner decision (2026-10-30, in the session):** reuse the solutions already built for the commuter in-commuters,
  no duplicated code, general helpers where sensible.

## Context

- `braunschweig.synthesis.student_incommuters` draws a student in-commuter's mode (car or pt) from the Mikrozensus
  distance-band reference and its times from survey donors. Since ADR-0172 the day is bounded to the simulated day,
  but nothing checked whether the timetable serves a pt student's two trips (home -> campus, campus -> home).
- Measured (run manifest `docs/runs/student-incommuter-pt-walks-2026-10-30.yml`, a measurement, not a validation):
  pt students with a routed walk over 5 km — 0 of 4 and 1 of 7 in two 1 % runs, 10 of 100 in an older 25 % run
  (6.3 to 69.4 km, median 30.5 km, departures 14 to 23 h, mostly campus -> outside).

## Decision

1. **Same check, same switch.** When `braunschweig.incommuters.pt_connection_check.enabled` is true (one switch for
   both in-commuter kinds), the student stage builds the probe with `incommuters._pt_check` (batch pt router, all
   schedule stops, the walk limit, the bound of `incommuters._time_bound_s`) and declares its keys and stages with
   the shared `incommuters.configure_pt_check`.
2. **Same chain, one helper.** `incommuter_pt_connections.check_pt_days` runs `resolve_pt_days`, applies every
   `day_shifted` offset to the whole day, switches every `to_car` day to car and logs the rates. Both stages call it;
   the commuter stage keeps only what is its own (board the car at the road gate, board at a re-drawn station). It
   runs after the ADR-0172 day bound and the mode draw, at the times that are finally written.
3. **No station step for students.** Students board at no drawn entry station (home = a real in-ring origin point or
   the nearest road gate), so `station_redrawn` cannot occur. A student switched to car drives from the home it
   already has; its home departure is unchanged (the seed does not depend on the mode).
4. **Report.** The frames carry `pt_connection` (same keys as the commuter stage), one log line with the rates, and a
   WARNING above `pt_no_connection_warn_share`.

## Rejected options

- **A student copy of the chain:** duplicated logic that drifts; the chain is already mode- and purpose-agnostic.
- **Give students drawn entry stations, to allow the station re-draw:** a new model element (station weighting by
  origin Kreis for students) with no measured need; owner decision pending if the counts after this change call for it.
- **Re-balance the student pt share after `to_car` (as ADR-0172 does for commuters):** the student stage has no mode
  balancer; the `to_car` count is reported so the loss of pt share is visible. Revisit if a run shows a material loss.

## Consequences

- The student stage now depends on the eqasim jar, the Java runtime and the supply stages (DAG snapshots updated).
- Output change by design: pt students whose day no timetable serves within the window move in time or drive; the
  realised student pt share falls below the Mikrozensus draw by the `to_car` count (logged; no observed reference of
  the realised share exists).
- The commuter stage is refactored onto `check_pt_days` without a change of its output (its tests are unchanged).
- **Status:** implemented and unit-tested; not yet run. The next run should repeat
  `scripts/measure_incommuter_pt_walks.py --prefix ic_edu_` on its prepared population and record the
  `pt_connection` counts of the student stage. A smoke, not a validation.
