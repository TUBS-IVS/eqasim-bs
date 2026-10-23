# ADR-0154 · 2026-10-23 · A day moves earlier as a whole when its last arrival leaves no margin before the end of the simulated day

- **Status:** accepted (implemented; measured in the 1 % rerun of the #465 fixes, see the status line at the end)
- **Numbering:** ADR-0153 is taken by the sibling branch of #488 (in-commuter timetable check); 0154 checked free
  across all local and remote branches on 2026-10-23.
- **Issue:** #465 (mechanism 2 of the 68 stuck residents of run `i465-wpe-smoke-1pct-2026-10-23`).
  **Relates to:** ADR-0146 (plan check, the simulated day = `qsim.endTime` = `MAX_PLAN_TIME_SECONDS` = 30 h).

## Context

- The synthesis bounds every plan by the simulated day, `braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`
  (30 h): the departure time model clips the chain so that the LAST ARRIVAL BY THE SURVEY TRAVEL TIMES is at most
  30 h. It leaves no margin, and MATSim routes the trips (longer than the survey times for some) and meets
  congestion and transfer waits.
- Run `i465-wpe-smoke-1pct-2026-10-23` (1 %, felix, final iteration 87): 20 of the 68 stuck residents were in a car
  (16) or a car as passenger (4) at the end of the simulation. All 20 have night-shift-like plans (for example
  home -> work 21:15 -> 29:45 -> home) whose last trip departs 29.17-29.97 h; their routed last arrival in the
  prepared population is 29.96-30.54 h (13 of the 20 at or after 30 h). Three more stuck walkers in the region end
  their day the same way (last departure 29.58-29.92 h). Two in-commuters were stuck in their work activity because
  their donor times end it at 30.42 h and 32.17 h. Measurement: `analysis/late_plans_run1.txt` and the stuck trace of
  that run (felix `~/wt-i487/analysis`, `~/wt-i465-wpe/analysis/stuck_trace_run1.csv`).
- In the prepared population 79 plans end after 29.5 h: 30 in-region residents, 21 residents with an outside
  tour, 14 in-commuters (planned arrival, routed, free flow).

## Decision

1. **Rule.** A day whose last planned arrival is later than `MAX_PLAN_TIME_SECONDS - margin` moves earlier AS A
   WHOLE: every activity start and end and every trip departure and arrival of the person by the same whole number
   of seconds (the excess rounded up). Every activity and trip duration is kept.
2. **Limit.** No time moves before 0 s: when the first departure leaves no room for the full shift, the day moves
   as far as it can and is counted as unresolved (WARNING).
3. **Place.** `braunschweig.matsim.scenario.day_end_margin.shift_late_days`, called by the population writer
   `braunschweig.matsim.scenario.population` after the in-commuter merge, so residents, portal trips and both
   in-commuter sources follow one rule (`late_day_shift_s` holds the rule on arrays, for a synthesis stage that must
   check its days at the written times, ADR-0153).
4. **Margin.** `braunschweig.day_end_margin.last_arrival_margin_s`, default 3600 s, ASSUMPTION: the largest excess of
   a routed last arrival over 30 h among the 20 stuck car residents was 0.54 h, and congestion comes on top. It is a
   technical margin, not a behavioural reference value. Switch: `braunschweig.day_end_margin.enabled` (default true;
   off writes the synthesis times, byte-identical plans). Both pinned in `configs/base_bs.yml`.
5. **Observability.** One log line with the late persons, the shifted persons, the mean and maximum shift and the
   unresolved persons.

## Rejected options

- **Cap only the last activity** (the owner's first idea): it shortens that activity, and the activity durations
  are the hold-out dimension of the departure time model; moving the whole day keeps them.
- **Run the simulation longer than the plan bound** (`qsim.endTime` > 30 h): ADR-0146 keeps one constant for the
  simulated day and the plan bound on purpose; the next-day transit extension (ADR-0151) and the plan check read it.
- **Tighten the bound in every synthesis path** (departure time model, portal stage, in-commuters, students): four
  places that drift; the writer sees every person once.
- **Count them as "en route at the end" instead of stuck:** reporting only; the plans stay infeasible in 30 h.

## Consequences

- Output change by design: the shifted days start up to the shift earlier (night-shift-like plans and late leisure
  chains). The departure time distributions move for those persons only; the durations do not.
- The population writer now hashes `day_end_margin` and `plan_validation` (and its imports) in its token.
- **Status:** implemented; measured in the 1 % rerun of the #465 fixes (run manifest `docs/runs/`, this ADR's
  numbers to be filled from it). A smoke, not a validation.
