# ADR-0151 · 2026-10-22 · The transit schedule gets next-day departures by repeating the service day's early departures at +24 h

- **Status:** accepted (issue #465). Measured once, 1 % smoke `docs/runs/i465-wpe-smoke-1pct-2026-10-23.yml`
  (no validation): the expected doubling at 24 to 26 h is small before the cordon cut (+46, +24, +19 departures)
  and absent after it, because the study area has no departure before 03:00; in the cut schedule the extension
  adds the next morning at 27 to 29 h (39 -> 42, 12 -> 163, 0 -> 552 departures per hour).
- **Numbering:** ADR-0151 is the next free id. Checked on 2026-10-22 after `git fetch origin`: across
  origin/main, every local and remote branch and the `docs/decisions` directories of all sibling
  worktrees the highest record is ADR-0150.
- **Issue:** #465. **Feature record:** `docs/registry/features/transit_next_day.yml`.

## Context

- pt2matsim (`Gtfs2TransitSchedule`) converts ONE service day (config `gtfs_date`, default
  `dayWithMostServices`). The schedule therefore ends at that day's last departure, while the simulated
  day is 30 h long (`braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`) and plans contain pt
  trips after midnight. Those trips found no timetable connection and fell back to very long walks.

## Decision

1. The region override of `matsim.scenario.supply.gtfs` (`braunschweig/matsim/scenario/supply_gtfs.py`)
   runs the upstream stage, keeps its output as `*_service_day` files and writes a schedule in which
   every departure before `braunschweig.transit_schedule.next_day_hours` (default 6.0) is repeated at
   +24 h as `<id>_d2` on a new vehicle `<vehicle>_d2` (`braunschweig/matsim/scenario/transit_next_day.py`).
2. **ASSUMPTION:** the next day has the same timetable as the chosen service day (no weekday-specific
   calendar). No observed reference for next-day service exists in the repo; nothing is validated.
3. The default 6 h is the 30 h simulated day minus 24 h (`MAX_PLAN_TIME_SECONDS` / 3600 - 24); a later
   hour would lie outside the simulated day.
4. `next_day_hours: 0` is the OFF path: byte-identical copies of the pt2matsim files.
5. The DOCTYPE of the input files is kept, because the MATSim readers select their format from it.

## Rejected options

- **A second service day from the GTFS feed.** pt2matsim picks exactly one day per run; a second day
  would need a second pt2matsim run and a merge of two schedules with colliding line and route ids.
- **A longer pt2matsim service day.** pt2matsim has no option for a day longer than 24 h; trips past
  24:00 of the chosen day are kept as the feed states them, but the following day's service is not generated.

## Consequences

- Original GTFS night trips at 24:00 and later are kept and the 00:00 to 06:00 duplicates are added on top,
  so lines serving both get doubled service between 24 h and about 26 h (ASSUMPTION: accepted; visible in
  the per-hour histogram logged under `[transit_next_day]`; checked at the 1 % smoke, see the status line:
  small before the cut, absent after it).
- The default changes simulation results by design; the stage recomputes once.
