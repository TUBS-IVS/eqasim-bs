# ADR-0174 · 2026-10-30 · Purpose chains are repaired and asserted after the last reordering step, the way eqasim does

- **Status:** accepted (implemented on branch `fix/i258-donor-chain-consistency`; no pipeline run has measured it yet;
  the numbers below come from a probe and tests, which are not a validation)
- **Numbering:** 0174 checked free across all local and remote branches and open PRs on 2026-10-30.
- **Issue:** #258. **Relates to:** ADR-0108 (home-end closure with an observed dwell), ADR-0104 (reporting-day view),
  ADR-0146 (plan-time bound).
- **Owner decision:** issue #258 comment (2026-09-09): "handle this the way upstream eqasim does".

## Context

- A trip chain is consistent when each trip starts where the previous one ended:
  `following_purpose[i] == preceding_purpose[i+1]` within a person.
- eqasim-france enforces this in every HTS cleaning stage with `hts.fix_activity_types` and asserts it in every
  `filtered` stage with `hts.check`. Our MiD diary build (`braunschweig.popsim.trips.build_trip_table`, step 8) runs
  the same fix, but nothing asserted it afterwards.
- Probe on the MiD 2023 B1 delivery (60,000 donor persons drawn with seed 1, all as synthetic persons, escort
  purposes on, `PlanValidator.repair_trips` with the constant dwell; scratch script, not a run): 0 broken links in
  diary order after the build, 117 of 167,839 after the repair. Two causes, both after step 8:
  1. **Departure-time tie in the closure.** `plan_validation._append_return_home` took each person's last trip with
     `idxmax(departure_time)`, which returns the FIRST of two trips with the same departure (MiD reports 0-minute
     legs). The return-home trip was then anchored on the wrong trip, or appended to a day that already ended at
     home. 18 of the 117 links.
  2. **Diary order is not time order.** `repair_trips` re-sorts each person by `departure_time`. Where a MiD diary
     numbers its trips (`W_ID`) out of time order, the purposes still follow the diary order. 99 of the 117 links.
- Most affected persons are replaced by the resample anyway (51 of 53 after the tie fix), so the 34 links measured on
  2026-08-11 in a 5 % `trips_stage` frame are the survivors of these two causes.

## Decision

1. **Fix the tie at the source.** `_append_return_home` takes the last row of each person in the stable
   `(person_id, departure_time)` sort, so on a tie the diary order decides.
2. **Repair once more after the last reordering step, as eqasim does.** `plan_validation.enforce_purpose_chain_consistency`
   sorts by `(person_id, trip_index)`, runs eqasim's `hts.fix_activity_types` (the LATER trip's origin is rewritten to
   the EARLIER trip's destination; a person's first trip is never touched) and logs the rewritten links as a rate of
   all within-person links. It WARNs above `PURPOSE_CHAIN_REPAIR_WARN_RATE` (1 %, an ASSUMPTION; the probe measured
   0.06 % after the tie fix).
3. **Assert, like `hts.check`.** The function raises when links are still inconsistent after the rewrite, and before it
   when `is_first_trip` / `is_last_trip` disagree with the person boundaries (the eqasim rewrite would then copy a
   purpose across persons).
4. **Call sites.** The end of `trips.build_validated_trip_table` (after repair, stage A and stage B; covers
   `trips_stage` and the home-office donor pool) and `commute_day.trips_day_stage` after `build_day_trips` (the last
   step that changes chains on the Phase B path).

## Rejected options

- **Configurable tolerance for the assertion (the issue's step 2):** after the rewrite the remaining count is zero by
  construction unless the first/last flags are stale, which is a code defect, not a data property. A tolerance would
  be a config key that can only hide a bug, so the assertion is unconditional and the data-dependent quantity (the
  rewrite rate) is logged and warned on instead.
- **Drop the affected persons:** loses real diaries over a reorder our own code introduced; eqasim repairs.
- **Keep and only log:** the activity builder would then read a chain whose activities do not follow each other.
- **Re-sort by diary order instead of time order:** the simulation needs time order; the repair re-sorts on purpose.

## Consequences

- Output change by design, small: persons with a departure tie at the end of their day lose a spurious return-home
  trip or get it anchored on the right trip; persons whose diary is out of time order get the eqasim purpose
  rewrite. `trips_stage` and `trips_day_stage` recompute (their helper hashes cover `plan_validation`).
- Every run logs `purpose chain: rewrote the origin purpose of n/N within-person links` per call site.
- A consistent chain is a structural property, not a validation of behaviour.
