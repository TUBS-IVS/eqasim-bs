# ADR-0156 · 2026-10-23 · A portal pt stay without a connection returns earlier or becomes a lift, never a long walk

- **Status:** accepted (implemented; measured in the 1 % rerun of the #465 fixes, see the status line at the end)
- **Numbering:** 0156 checked free across all local and remote branches on 2026-10-23 (0153-0155/0157 are the sibling
  branches of #465).
- **Issue:** #465 (mechanism 3 of the 68 stuck residents of run `i465-wpe-smoke-1pct-2026-10-23`).
  **Amends:** ADR-0152 (decision 5, the no-connection chain, and decision 6, kept stays). The rest of ADR-0152 stands.
- **Owner decision (2026-10-23):** "die Fußwege sind ja absolut unrealistisch -> fixen", and the departure shift as
  the preferred resolution (stated for the in-commuters, ADR-0153).

## Context

- ADR-0152 resolves a pt portal stay without a timetable-aware gate by tour_to_car, tour_to_car_passenger, a LATER
  departure (up to 2 h) or keeps it as pt (`kept_no_connection`), and the router then realises its trips as walks.
- Run `i465-wpe-smoke-1pct-2026-10-23` (1 %): of 123 pt portal stays, 14 were kept (persons without car or lift
  availability, no later departure within 2 h); 21 planned-pt portal trips became walks, most of them returns at
  21-30 h (5 of 6 returns at 21-23 h, 5 of 6 at 24-30 h; felix `~/wt-i465-wpe/analysis/portal_trips_run1.txt`). Several of
  them were stuck walking at the end of the simulated day, with walks of 24-125 km.
- A return after the last connection cannot be served by a later departure (the next service is the next morning,
  beyond the 2 h window and often beyond the day), but often by an earlier one.

## Decision

1. **Earlier return.** After the later shifts of ADR-0152, the grid is walked a second time for the stays still
   unresolved, moving only the RETURN earlier by `k * shift_step_s` (up to `shift_window_s`) while `min_stay_s`
   remains between the outbound arrival and the shifted return departure; the probe runs once per step for all
   such stays. Counted as `departure_shifted`. Key `braunschweig.portal.shift_return_earlier` (default true).
2. **Lift as the last resort.** A stay that nothing resolves becomes a lift: its home-based tour switches to
   `car_passenger` WITHOUT the passenger availability, counted under its own label `tour_to_car_passenger_lift`
   (ASSUMPTION: an out-of-region trip that no timetable serves is made as a lift or taxi, not as a walk of tens of
   kilometres). Key `braunschweig.portal.no_connection_last_resort`: `car_passenger` (default) or `keep` (the former
   `kept_no_connection`).
3. `resolve_no_connection` keeps the former behaviour in its own defaults (`return_earlier=False`,
   `last_resort="keep"`); the stage passes the configured values. Both keys are validated at configure.

## Rejected options

- **Keep the stay and let the router walk** (ADR-0152 decision 6): the walks are the defect the owner asked to fix.
- **Widen the later shift window to reach the next morning:** a return the next morning ends after the simulated day.
- **Drop the stay or the person:** changes the demand of the synthesis (out-of-region activity counts).
- **Car without the car availability:** a person without car or licence cannot drive; a lift needs neither.

## Consequences

- Output change by design: the kept stays become earlier returns or lifts; car_passenger trips at the cordon rise by
  the lift count; no observed reference of that share exists.
- **Status:** implemented; measured in the 1 % rerun of the #465 fixes (run manifest `docs/runs/`, numbers to be filled
  from it). A smoke, not a validation.
