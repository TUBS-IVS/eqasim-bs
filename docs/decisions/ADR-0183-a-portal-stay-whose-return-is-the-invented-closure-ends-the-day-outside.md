# ADR-0183 · 2026-11-05 · A portal stay whose return is the invented closure trip ends the day outside

- **Status:** accepted (implemented; the portal stage alone replayed on the 100 % inputs of `prod100_20261031`,
  run manifest `docs/runs/i570-portal-replay-prod100-2026-11-05.yml`; a full 100 % run is planned in #570)
- **Numbering:** 0183 checked free across all local and remote branches and open PRs on 2026-11-05 (0182 is taken by
  PR #568).
- **Issue:** #570 (B2 cause 1 of the demand-side findings of run `prod100_20261031`).
  **Amends:** ADR-0152 (the pt gate of these stays is checked outward only), ADR-0156 (the earlier-return shift
  no longer applies to them: they have no return) and, for these stays, the home closure of ADR-0108.
- **Owner decisions (2026-11-05):** every portal stay whose return trip is a synthetic closure trip ends the day
  outside, whatever its mode; a 36 h simulated day with a next-morning return was considered and dropped (only
  0-24 h is evaluated). For the spliced home-office donor days the flag is carried through the splice (option A).

## Context

- The plan repair appends an invented trip home to every diary that does not end at home and marks it
  `is_synthetic_closure` (`braunschweig.popsim.plan_validation._append_return_home`, ADR-0108). When the last
  diary activity lies beyond the portal threshold, that trip becomes the return leg of a portal stay (ADR-0141).
  The donor did not come home that day; the invented trip leaves after a drawn dwell, often late in the evening or
  after midnight.
- The timetable-aware pt gate checks both directions of a stay (ADR-0152). An invented late return finds no
  service, and the no-connection chain then switches the whole home-based tour to car or a lift (ADR-0152,
  ADR-0156), the daytime trip out included. The defect's shares in run `prod100_20261031` are in #570 (read-only
  probe of the old code, model output, not committed); the replay of this rule on the same inputs is the run manifest
  `docs/runs/i570-portal-replay-prod100-2026-11-05.yml`.
- The flag did not survive the home-office splice: `braunschweig.synthesis.commute_day.donor_pool.donor_trips`
  carried only the trip contract plus `euclidean_distance` and `trip_key`, and
  `braunschweig.synthesis.commute_day.plan_replacement.build_day_trips` nulls every other extra column on the
  replaced rows.

## Decision

1. **The day ends outside.** `braunschweig.synthesis.portal_trips.classification.drop_closure_returns` runs right
   after the stays are found and before any per-stay step. A stay whose return trip carries
   `is_synthetic_closure` loses that trip and has no return, so every later step takes the existing path of a stay
   without a return: the pt gate is checked outward only, a time shift moves only the outbound leg, the rewrite
   writes no return. Every mode. Stays with a genuine return are unchanged.
2. **Counted.** The stage report holds `n_stays_closure_return_dropped`; the rate over all stays is logged at INFO
   under `[portal_trips]` (log line in `docs/codebase/notes/portal-trips.md`).
3. **Fail fast.** The portal stage requires the `is_synthetic_closure` column and raises on a missing value and on a
   flagged return trip that is not the person's last trip. Its configure step rejects the ENTD trip source
   (`braunschweig.population.popsim.source: entd`), which runs no plan repair and so has no flag; carrying an
   all-False flag there was rejected because that module is hashed by the PopulationSim stage, so the change would
   recompute PopulationSim for a combination no configuration uses.
4. **The flag survives the splice.** `donor_pool.donor_trips` carries `is_synthetic_closure` (and raises when the
   donor build lacks it); `plan_replacement` copies it onto the replaced rows like `trip_key`.

## Rejected options

- **A 36 h simulated day with a next-morning return** (owner, 2026-11-05): the evaluated day is 0-24 h; ADR-0146
  rejected the 36 h day before.
- **Drop the invented return at the rewrite:** the gate choice would still have checked the return direction.
- **Read the `trip_key` suffix `_closure` where the flag is missing** (option B): a second identification path
  through a naming convention next to the documented marker.
- **Leave the spliced home-office days uncovered** (option C): the rule would silently skip them.

## Consequences

- **Output change by decision.** These persons' days end at the gate; the inside part of their invented return
  disappears from the simulated trips; fewer tours switch to car or a lift for want of a return connection; fewer
  days are expected to move earlier through the day-end margin (ADR-0154, ADR-0160), where the invented return
  was the last arrival (expectation, see #570). No observed reference exists for these shares; the
  portal-stage replay is in the run manifest above, the full-run before/after measurement is planned in #570.
- **Simulation.** MATSim does not reject a plan whose first and last activity types differ: it warns once and scores
  the first activity from midnight and the last one to midnight (`CharyparNagelActivityScoring`, checked at the
  matsim-libs tags `matsim-0.7.0` to `2026.0`). The written config turns activity scoring off for every type,
  `outside` included (`org.eqasim.core.scenario.config.GenerateConfig`, TUBS-IVS/eqasim-java-bs `f30cb725`), and
  the residents choose with the eqasim utilities and KeepLastSelected, so no choice depends on it. The discrete mode
  choice splits tours at `home` and `outside`, freezes tours that touch `outside` (OutsideFilter), and its
  `VehicleTourConstraint` allows a vehicle to end at the last activity. A car that ends the day at a gate is its
  owner's vehicle (the written vehicles give each car to one person), so no other agent loses it.
- **Analyses.** The SrV plan-structure and departure-time comparisons and the work-participation report read the
  pre-portal trips (`braunschweig.synthesis.portal_trips.config_keys.final_view_trips_stage`), so this rule does
  not change them; their closure counts now include the spliced home-office days, which carried no flag before.
- **Cache.** The helper-hash audit (`scripts/audit_synpp_helper_hash.py`) lists the stages that re-execute:
  `braunschweig.synthesis.portal_trips.stage` (and everything after it) for `classification`,
  `braunschweig.synthesis.commute_day.home_office_donors_stage` and `state_stage` for `donor_pool`, and
  `braunschweig.synthesis.commute_day.trips_day_stage` for `plan_replacement`; popsim stays cached.

## Evidence

- Issue #570; commits `2701f139` (failing test of the primary path), `773e03fe` (the rule in the portal stage),
  `e8678cb4` (the flag survives the home-office splice), `90e11de3`, `d05efe42` and `5fc16b65` (tests below).
- Tests: `tests/test_portal_trips_stage.py::test_a_portal_stay_whose_return_is_an_invented_closure_ends_the_day_outside`
  and `::test_inconsistent_closure_flags_fail_fast`;
  `::test_closure_return_rule_on_multi_stay_and_merged_stay_days`;
  `tests/test_commute_day_plan_replacement.py::test_spliced_rows_keep_the_donors_synthetic_closure_flag`;
  the donor closure-flag contract tests in `tests/test_commute_day_donor_pool.py`;
  `tests/test_population_writer_portal_gate.py::test_a_day_ending_at_a_portal_gate_passes_the_activities_transform_and_the_writer`.
