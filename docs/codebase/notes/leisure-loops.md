# Strolls and dog walks: the loop flag and the placement rule

This note is the ONE home of the definition of a loop leg (the flag) and of the rule that places a loop
activity. The reasons, the rejected approaches and the assumptions are in
[ADR-0207](../../decisions/ADR-0207-strolls-and-dog-walks-stay-where-they-start.md); the switch and its production
state are in the feature record
[`secondary_loop_zero_displacement`](../../registry/features/secondary_loop_zero_displacement.yml); numbers are in the
run manifests those records link. Code: `braunschweig/synthesis/locations/secondary_chainsolvers/loops.py` (pure
functions), `secondary_chainsolvers/__init__.py` (`execute`, `_flag_loop_trips`,
`_persons_with_anchored_activities`, `_collapse_loops_of_eligible_persons`, `_report_loops`) and
`braunschweig/matsim/scenario/facilities.py` (`_loop_origin_facility_ids`, `validate_secondary_coverage`).

## Switch

Config key `secondary_loop_zero_displacement` (code default `false`, production `true` in `configs/base_bs.yml`).
Off: no loop function runs, no MiD table is read for it, the result has no `loop_origin` column and the stage writes
no report file. The key is declared by the secondary location stage and, as a read-only mirror, by the facilities
stage; both must run with the same value.

## The flag (`loops.estimate_loop_probabilities`, `loops.flag_loops`)

A Boolean per synthetic trip, true when the trip leads INTO a loop activity. It is computed on the trips table before
anything else of the stage rewrites it, and it is independent of the leisure subtype group (the group and its decider
stay untouched).

- **Scope.** The destination activity is leisure, the mode is walk or bicycle (`loops.LOOP_MODES`), the donor's
  `W_ZWECK` is in `purpose_subtype.LEISURE_ZWECK` and the donor row exists. Every other trip is false and consumes no
  random number: motorised legs, the coarse purpose 10 (`leisure_unspecified`) and the child purposes stay out.
- **Labelled leg.** The donor's `W_ZWD` is one of the leisure detail codes of `purpose_subtype.leisure_spec`
  (`labelled_codes`, which follows `purpose_subtype_codeplan_sentinels`: with it on, 799 "Freizeit k.A." is not a
  label). The leg is a loop exactly when the code is in `loops.LOOP_CODES` (710 stroll, 711 dog walk), by bicycle too.
- **Drawn leg.** A leg in scope with another `W_ZWD` (a design sentinel, or 799 with the sentinels on) draws one
  Bernoulli value with the probability of its cell. The stream is `random_seed + loops.LOOP_SEED_OFFSET`, separate from
  the subtype, escort and distance draws; draws are taken in `(person_id, trip_index)` order.
- **Cells.** `(mode, travel-time band, starts at home, way home follows)`. The band is `purpose_subtype.tt_band`, the
  band of the leisure subtype decider. "Starts at home": the purpose before the leg is home. "Way home follows": the
  next trip of the same person ends at home (the last trip of a day has none). The probability is the `W_GEW`-weighted
  loop share of the labelled MiD leisure legs of the cell, in the universe of the distance layers (weekday legs when
  `secondary_mid_weekday_legs_only` is on), for cells with at least `secondary_distance_min_obs` observations.
- **Marginal fallback.** A cell below that minimum takes the weighted loop share of its mode. The share of draws that
  did so is logged per mode (WARNING above `fallback.FALLBACK_WARN_RATE`) and reported; a mode without a marginal
  raises.

## The placement rule (`loops.collapse_loops`, `loops.restore_loops`, `execute`)

1. **Eligibility.** A person is eligible when no activity of theirs is anchored or linked: no key in
   `activity_anchors`, no row of the linked escort or portal rows, and neither side (child or adult) of a passive joint
   link (`_persons_with_anchored_activities`). Their loops are kept: the solver places them like any other leisure
   activity, and they are counted (`kept_person_has_anchored_activity` in the report).
2. **Collapse.** For an eligible person every loop trip is dropped (a run of consecutive loops, and a loop that is the
   last activity of the day, included). The trip after a run of loops now starts at the activity before the run
   (its `preceding_purpose` is rewritten); mode, times and `travel_time` stay. Trip indices are renumbered without
   gaps, and `collapse_loops` returns the index map to the original activity indices and the loop table (original
   index, origin activity, origin kind). A loop must lead into a secondary activity, never into home, work or
   education (`ValueError`).
3. **Solve.** Escort link, portal anchors, problems, both passes and result extraction run on the collapsed table as
   before. A person left without a secondary activity has nothing to solve.
4. **Restore.** Solver rows of collapsed persons get their original activity indices back. Every loop activity then
   gets one row at the place of the last activity before it that is not a loop (two loops in a row copy the same
   place). Origin kinds, written to the column `loop_origin`: `home` (`location_id` `home_<household_id>`, the home
   `geometry`), `work` and `education` (the person's primary `location_id` and `geometry`), `secondary` (the
   `location_id` and `geometry` the solver gave that activity). `loop_origin` is empty on every other row. A missing
   origin place raises; there is no stand-in place.
5. **Facility check.** `validate_secondary_coverage` accepts a loop row's identifier only when it is a written home
   facility (`home`) or a primary facility (`work`, `education`) of that kind, never because the loop row carries it.
   With the switch on and no `loop_origin` column in the realised rows the facilities stage raises.
6. **Report.** The log line `[secondary_loops] <mode>: in scope ...` gives, per mode, the legs in scope, the loops by
   label and by draw, the legs drawn and the share from the mode marginal. The line `[braunschweig.secondary_chainsolvers]
   loops: ...` and the block `loops` of `secondary_locations_report.json` (stage directory) give the loops flagged,
   those placed at their origin by origin kind, those kept for an anchored or linked person (WARNING above
   `FALLBACK_WARN_RATE`), the persons left without a secondary activity and the marginal share per mode.

The random streams of the solver are consumed by fewer legs with the switch on, so a comparison of the two switch
states compares distributions; the stage tests compare against an input from which the loop was removed by hand.

## Comparisons

`braunschweig/calibration/secondary_measurement.py` (`leave_out_loop_legs`) and `scripts/validate_secondary_distances.py`
leave loop legs out of the model side where the cache carries `loop_origin`. The survey side of
`validate_secondary_distances` is the published MiD W12 table, which keeps loops and has no `W_ZWD`; the script states
that difference. The W_ZWD group reference has a loop-free variant
([data record](../../registry/data/mid2023_w_zwd_group_reference_without_loops.yml)).

The mapping of the MiD purposes and detail codes: [`mid-purpose-mapping.md`](mid-purpose-mapping.md).
