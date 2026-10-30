# ADR-0153 · 2026-10-23 · The pt days of in-commuters are checked against the timetable and resolved departure first

- **Status:** accepted (implemented; measured in the 1 % rerun of the #465 fixes, see the status line at the end)
- **Amended by ADR-0172:** the chain gains `station_redrawn` after `day_shifted` and before `to_car`; the rejected option "Choose another home station" below is overturned there (#467).
- **Numbering:** 0153 checked free across all local and remote branches on 2026-10-23 (the sibling branches of
  #465 take 0154-0157).
- **Issue:** #488 (part of #465). **Relates to:** ADR-0152 (the same check for the portal pt stays of residents),
  ADR-0154 (day-end margin, whose rule this check applies first), ADR-0099 (in-commuter attributes).
- **Owner decision (2026-10-23, brainstormed in the session):** "Abfahrt verschieben" — the departure shift comes
  first, before a mode switch.

## Context

- `braunschweig.synthesis.incommuters` draws an in-commuter's mode from the Mikrozensus reference (car or pt) and its
  times from survey donors. Nothing checked whether the timetable serves a pt in-commuter's two trips (home station
  -> work, work -> home station) at those times.
- Run `i465-wpe-smoke-1pct-2026-10-23` (1 %, prepared population; felix `~/wt-i487/analysis/incommuter_walks_wpe.txt`):
  599 in-commuters, about 91 by pt; 22 of the pt ones had 27 trips that the prepare routing realised as walks over
  5 km (median 52 km, 8.3-107.5 km), mostly the return from work at 16-23 h; only 4 of the 27 carried the
  walk-fallback stamp (the rest lost it in the cutter, #487). 10 of the 20 frozen residents stuck walking at the end
  of that run were such in-commuters (73 km walk from 21:00, 16 h travel time, for example); 2 more were stuck in
  their work activity, which their donor times end at 30.42 h and 32.17 h.
- 13 of the in-commuters' first pt boardings waited more than 60 min (not addressed here).

## Decision

1. **Check.** After the donor times are imputed, both trips of every pt in-commuter's day are probed with the batch
   pt router of `braunschweig.data.transit.connections` (the production `RunBatchPublicTransportRouter`, pre-cut network
   and schedule) and its connection rule (`connection_mask`), here over ALL stops of the schedule (an in-commuter's
   home station may lie outside the cut extent, so the cut-extent stop rule of ADR-0152 does not fit) with the walk
   limit `plan_check.DEFAULT_WALK_MAX_DISTANCE_M`.
2. **Day-end rule first.** A day that ends after `MAX_PLAN_TIME_SECONDS - braunschweig.day_end_margin.last_arrival_margin_s`
   moves earlier first, with the population writer's rule (`day_end_margin.late_day_shift_s`, ADR-0154), so the timetable
   is asked at the times that are finally written and the writer does not move a checked day again.
3. **Chain** (`braunschweig.synthesis.incommuter_pt_connections.resolve_pt_days`), counted per day:
   `connection_found`; `day_shifted` — the WHOLE day (both trips and the work activity) moves by the first offset
   of -step, +step, -2 step, +2 step, ... up to the window at which both trips are connections (earlier first: the
   typical failure is a late return after the last connection); a shift never moves the home departure before 0 s or
   the return arrival after the bound; the probe runs once per offset for all unresolved days; `to_car` — the
   in-commuter drives and boards at its road gate (the path the synthesis already uses for pt in-commuters without a
   station; a real in-ring origin keeps its home; the home departure follows the new access distance, as
   `_agent_times` seeds it); `kept_no_connection` — only when car is not a commute mode (then the router realises
   the trips as walks; warned).
4. **Config** (pinned in `configs/base_bs.yml`): `braunschweig.incommuters.pt_connection_check.enabled` (true),
   `pt_shift_window_s` 7200 and `pt_shift_step_s` 1800 (ASSUMPTION: the values of the portal shift of ADR-0152, not
   calibrated), `pt_no_connection_warn_share` 0.05 (WARNING above that share of `to_car` + `kept` days).
5. **Report.** The frames carry `pt_connection` (actions, median shift, days moved before the bound); one log line.

## Rejected options

- **Car first, as for the portal stays (ADR-0152):** the owner chose the departure first, which keeps the
  Mikrozensus pt share of the in-commuters where a shift suffices.
- **Keep the day and let the router walk:** the walks of tens of kilometres are the defect.
- **A per-trip shift** (only the failing trip moves): the work duration of the donor would change; the in-commuter's
  day is one stay, so the whole day moves.
- **Choose another home station:** the station draw is weighted by population and accessibility
  (`pt_reachability.weight_entry_stations`); redrawing it against the timetable is a larger change and not needed when
  the shift or the car resolves the day.

## Consequences

- The in-commuter stage now depends on the eqasim jar, the Java runtime and the supply stages (the DAG changed);
  the student in-commuter stage, which shares the module, hashes the larger import closure.
- Output change by design: pt in-commuters whose day no timetable serves within the window drive, so the realised pt
  share of the in-commuters falls below the Mikrozensus draw by the `to_car` count (logged; no observed reference of
  the realised share exists).
- **Status:** implemented; measured in the 1 % rerun of the #465 fixes (run manifest `docs/runs/`, numbers to be
  filled from it). A smoke, not a validation.
