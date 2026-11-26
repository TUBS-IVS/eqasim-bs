# Strolls and dog walks: the loop flag, the out-and-back split, the placement rule and the mode rules

This note is the ONE home of the definitions of a stroll, a loop and an out-and-back stroll (section "Definitions"),
of the rule that places a loop activity, of the marker that carries an out-and-back stroll into the plans, and of the two
Java mode rules with what each does not reach. The reasons, the rejected approaches and the assumptions are in
[ADR-0207](../../decisions/ADR-0207-strolls-and-dog-walks-stay-where-they-start.md); the switch and its production
state are in the feature record
[`secondary_loop_zero_displacement`](../../registry/features/secondary_loop_zero_displacement.yml); numbers are in the
run manifests those records link (the 1 % evidence of both switches and of the mode rule, arm D:
[`i679-stroll-loops-ab-1pct-2026-11-25`](../../runs/i679-stroll-loops-ab-1pct-2026-11-25.yml)).
Code: `braunschweig/synthesis/locations/secondary_chainsolvers/loops.py` (pure
functions: `estimate_loop_probabilities`, `flag_loops`, `out_and_back_strolls`, `diary_way_home_follows`,
`select_collapsible_loops`, `collapse_loops`, the `to_collapsed_*` translators, `restore_loops`),
`secondary_chainsolvers/__init__.py` (`execute`, `_flag_loop_trips`, `_linked_activity_keys`,
`_collapse_loops_and_move_anchors`, `_require_no_loop_origin_outside`, `_mark_out_and_back_strolls`, `_report_loops`),
`braunschweig/matsim/scenario/facilities.py` (`_loop_origin_facility_ids`, `validate_secondary_coverage`),
`braunschweig/synthesis/commute_day/spatial_locations_day.py` (`_with_out_and_back_strolls`),
`braunschweig/matsim/scenario/population.py` (`_require_stroll_marker`, `_injected_rows_without_stroll`),
`matsim/scenario/population.py` (`add_person`, `write_population`) and, for the loop-free estimates below,
`braunschweig/popsim/purpose_subtype.py` (`LOOP_CODES`, `LOOP_MODES`, `mark_diary_returns`, `is_out_and_back_stroll`,
`drop_loop_legs`), `braunschweig/popsim/distance_distributions.py` (`run`, Step 2 and Step 8) and
`secondary_chainsolvers/deciders.py` (`_build_leisure_subtype_decider`).

## Definitions: stroll, loop, out-and-back

- **Stroll** (the flag, `loops.flag_loops`): a leisure leg on foot or by bicycle whose detail code is 710 (stroll) or 711
  (dog walk), by the donor's label or drawn where the detail was not asked (section "The flag"). MiD records it as ONE
  trip whose length is the whole walk.
- **Diary way home** of a trip: the same person's NEXT diary trip ends at home. Survey side:
  `purpose_subtype.mark_diary_returns` (next leg by `W_ID` within `H_ID`, `P_ID`; the last leg of a person has none).
  Synthetic side: `loops.diary_way_home_follows` (next trip by `trip_index` ends at home AND is not the plan repair's
  invented home trip, the column `is_synthetic_closure` written by `braunschweig.popsim.plan_validation.PlanValidator`).
  The MiD diary has no invented trip, so a trip followed only by that closure ended the diary: it has no diary way home.
- **Out-and-back stroll**: a stroll that starts at home AND has a diary way home. Survey side
  `purpose_subtype.is_out_and_back_stroll`, synthetic side `loops.out_and_back_strolls`; both read the same two marks.
  The model places it like any leisure leg: the subtype comes from the decider, the distance from the pool, no bound
  from the diary's own length, and the way home keeps its drawn length. It stays in the loop-free estimates.
- **Loop**: a stroll that is NOT out-and-back (it starts elsewhere than at home, or no diary way home follows it, a stroll
  followed only by the invented closure included). Only loops are collapsed, placed at their start and left out of the
  loop-free estimates (the rest of this note).

## Switch

Config key `secondary_loop_zero_displacement` (constant `config_keys.KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT` in
`braunschweig/popsim/stage/config_keys.py`, re-exported as `loops.KEY_ENABLED`); its default and production value are
owned by the key and its comment in [`configs/base_bs.yml`](../../../configs/base_bs.yml). Off: no loop function runs,
no MiD table is read for it, the result has no `loop_origin` column and the stage writes no report file. The key is
declared by the secondary location stage and, as a read-only mirror, by the facilities stage; both read the same key of
one resolved config, so they cannot differ within a run.

The second switch of the change, `secondary_leisure_estimates_without_loops`, has its own section below.

## The flag (`loops.estimate_loop_probabilities`, `loops.flag_loops`, `loops.out_and_back_strolls`)

`flag_loops` gives a Boolean per synthetic trip, true when the trip leads INTO a stroll activity. It is computed on the
trips table before anything else of the stage rewrites it, and it is independent of the leisure subtype group (the group
and its decider stay untouched). `loops.out_and_back_strolls` then splits the strolls (section "Definitions"): the
out-and-back strolls stay on the solver, the other strolls are the loops that the rest of the stage handles
(`_flag_loop_trips` returns both flags). The trips must carry `is_synthetic_closure` (`ValueError` naming it
otherwise; there is no default for a missing mark).

- **Scope.** The destination activity is leisure, the mode is walk or bicycle (`purpose_subtype.LOOP_MODES`, which
  `loops` re-exports; it is owned there because the loop-free estimates need it), the donor's
  `W_ZWECK` is in `purpose_subtype.LEISURE_ZWECK`, the donor row exists and the donor's `W_ZWD` is present. Every other
  trip is false and consumes no random number: motorised legs, the coarse purpose 10 (`leisure_unspecified`) and the
  child purposes stay out.
- **Labelled leg.** The donor's `W_ZWD` is one of the leisure detail codes of `purpose_subtype.leisure_spec`
  (`labelled_codes`, which follows `purpose_subtype_codeplan_sentinels`: with it on, 799 "Freizeit k.A." is not a
  label). The leg is a stroll exactly when the code is in `purpose_subtype.LOOP_CODES` (710 stroll, 711 dog walk;
  re-exported by `loops`), by bicycle too.
- **Drawn leg.** A leg in scope whose `W_ZWD` is NOT one of those leisure detail codes draws one Bernoulli value with
  the probability of its cell. That is every other code the donor leg carries: the members of
  `purpose_subtype.LEISURE_SENTINELS` (the design codes for "not asked", and the shop and errand detail codes that occur
  on leisure legs, see the assumption in ADR-0207), and 799 with the sentinels on. The stream is `random_seed +
  loops.LOOP_SEED_OFFSET`, an offset that differs from every other stream of the stage, so the draw perturbs none of the
  subtype, escort, location-type or distance draws; draws are taken in `(person_id, trip_index)` order.
- **Missing detail code.** A leg that meets the scope except that its `W_ZWD` is missing (NaN) stays not-loop and draws
  nothing. It is counted per mode as `missing_detail_code` in the flag statistics and logged against
  `fallback.FALLBACK_WARN_RATE`, so a donor table that lost its detail codes shows up.
- **Cells.** `(mode, travel-time band, starts at home, way home follows)`. The band is `purpose_subtype.tt_band`, the
  band of the leisure subtype decider. "Starts at home": the purpose before the leg is home. "Way home follows": the
  diary way home of the leg (section "Definitions"), read the same way on both sides (`mark_diary_returns` on the MiD
  legs, `diary_way_home_follows` on the synthetic trips), so the plan repair's invented home trip counts on neither. The
  probability is the `W_GEW`-weighted
  loop share of the labelled MiD leisure legs of the cell, in the universe of the distance layers (weekday legs when
  `secondary_mid_weekday_legs_only` is on), for cells with at least `secondary_distance_min_obs` observations.
- **Marginal fallback.** A cell below that minimum takes the weighted loop share of its mode. The share of draws that
  did so is logged per mode (WARNING above `fallback.FALLBACK_WARN_RATE`) and reported; a mode without a marginal
  raises.

## The placement rule (`loops.select_collapsible_loops`, `loops.collapse_loops`, `loops.restore_loops`, `execute`)

1. **Selection** (`select_collapsible_loops`, with `_linked_activity_keys`). Every flagged loop is collapsed, whatever
   else the person's chain holds (an escort link, a portal stay or a passive joint link do not exclude a person). Two
   kinds stay on the solver and are counted, each under the first reason that applies:
   - `kept_loop_is_linked_activity`: the loop's own activity is anchored or linked: its key is in `activity_anchors`, it
     is a row of the linked escort or portal rows, or it is either side (child or adult) of a passive joint link. The
     anchor or link needs that activity in the chain.
   - `kept_after_outside`: the loop starts at an `outside` activity (a portal stay). Only the first loop of a run after
     the stay is kept; the next one collapses onto it. The reason is in ADR-0207.

   Out-and-back strolls are not selected at all: they are no loops and go through the solver like any leisure leg.
2. **Collapse** (`collapse_loops`). Every collapsible loop trip is dropped (a run of consecutive loops, and a loop that
   is the last activity of the day, included). The trip after a run of loops now starts at the activity before the run
   (its `preceding_purpose` is rewritten); mode, times and `travel_time` stay. Trip indices are renumbered without gaps,
   and `collapse_loops` returns the index map to the original activity indices and the loop table (original index,
   origin activity, origin kind). A loop must lead into a secondary activity, never into home, work or education
   (`ValueError`).
3. **Move anchors and links** (`_collapse_loops_and_move_anchors`, `to_collapsed_anchor_keys`,
   `to_collapsed_activity_rows`, `to_collapsed_links`). The anchor keys, the linked escort and portal rows and both sides
   of every passive joint link were built on the original activity indices; before the solve they are translated to the
   collapsed numbering with the index map, so the solver and the pass-2 anchor resolution see the numbering of the
   chains they solve. The stage raises when an anchored or linked activity has no place in the collapsed chain, when two
   anchors of one person end on the same index, when the number of collapsed loops differs from the number of
   collapsible flags, and when a collapsed loop starts at an outside stay (`_require_no_loop_origin_outside`: it would
   copy the gate's portal id).
   Escort runs (consecutive activity indices of one person, assigned by `escort_links.assign_escort_anchors`) are
   assigned on the diary as recorded, before the collapse: "escort, stroll, escort" keeps two escort runs, which a
   hand-removed stroll would have made one.
4. **Solve.** Problems, both passes and result extraction run on the collapsed table, with every anchor and link
   already translated to the collapsed numbering by step 3. The escort link and the portal anchors are NOT built here:
   they were built on the original trips before the collapse (step 3) and only translated. A person left without a secondary activity has nothing to solve.
5. **Restore** (`restore_loops`), once on the combined rows (solver rows, the pass-2 joint anchor rows, the linked
   escort and portal rows). Every row of a collapsed person gets its original activity index back; a row the index map
   does not know raises. The activity index is checked as whole numbers and read as int64 (an object column from an
   empty solver frame is accepted, a missing or fractional value raises). Then every loop activity gets one row at the
   place of the last activity before it that is not a loop (two loops in a row copy the same place). Origin kinds,
   written to the column `loop_origin`: `home` (`location_id` `home_<household_id>`, the home `geometry`), `work` and
   `education` (the person's primary `location_id` and `geometry`), `secondary` (the `location_id` and `geometry` of
   that activity's row in the restored rows). An anchored origin is a `secondary` origin: a stroll after a linked
   drop-off copies the child's education place, one after a child's joint activity the adult's placed location. A
   portal id is never copied (step 1 keeps such a loop on the solver). `loop_origin` is empty on every other row. A
   missing origin place raises; there is no stand-in place.
6. **Facility check.** `validate_secondary_coverage` accepts a loop row's identifier only when it is a written home
   facility (`home`) or a primary facility (`work`, `education`) of that kind, never because the loop row carries it. A
   `secondary` loop row is checked like any realised secondary row: the child's education id of a stroll after a linked
   drop-off passes only with `escort_household_link` on (the education ids are then extra valid ids). With the switch on
   and no `loop_origin` column in the realised rows the facilities stage raises.
7. **Report.** The log line `[secondary_loops] <mode>: in scope ...` gives, per mode, the legs in scope, the loops by
   label and by draw, the legs drawn and the share from the mode marginal. The line `[braunschweig.secondary_chainsolvers]
   loops: ...` and the block `loops` of `secondary_locations_report.json` (stage directory) give the loops flagged,
   those placed at their origin by origin kind, those kept on the solver by reason (`kept_loop_is_linked_activity`,
   `kept_after_outside`, their sum `kept_total`, each with a rate; WARNING when `kept_total` is above
   `FALLBACK_WARN_RATE`), the collapsed loops of persons with an escort link, a portal stay or a passive joint link
   (`collapsed_loops_of_persons_with_escort_link`, `..._portal_stay`, `..._passive_joint_link`: they show the
   translation of step 3 is exercised), the persons left without a secondary activity and the marginal share per mode.
   With `secondary_leisure_estimates_without_loops` on, the block also has `subtype_estimate_legs_left_out` (below).
   The block and the log line also count the strolls that are out-and-back (`strolls_out_and_back`; `flags.out_and_back`
   per mode) and the loops whose next trip is the invented home trip (`loops_followed_by_synthetic_closure`);
   `loops` counts the loops only.
8. **Marker** (`_mark_out_and_back_strolls`, after `restore_loops`). With the switch on, the stage result gets the Boolean
   column `is_out_and_back_stroll`: True on the one solver row of every out-and-back stroll activity, False on every
   other row (loop rows included, a loop kept on the solver too). An out-and-back stroll without a row, with a row
   copied from an origin or with more than one row raises `ValueError`. With the switch off the column does not exist.

The random streams of the solver are consumed by fewer legs with the switch on, so a comparison of the two switch
states compares distributions; the stage tests compare against an input from which the loop was removed by hand.

## The loop-free leisure estimates (`secondary_leisure_estimates_without_loops`)

With the loops taken out of the chain, the leisure legs that remain must not be assigned a subtype, or a distance,
from estimates that still count the loops. With the switch on, two estimates leave the loop legs out:

- **The rule** (`purpose_subtype.drop_loop_legs`, the one row filter): a leg is left out when its mode is in
  `purpose_subtype.LOOP_MODES`, its `W_ZWD` is in `purpose_subtype.LOOP_CODES` AND it is no out-and-back stroll
  (`purpose_subtype.is_out_and_back_stroll`, so the filter drops exactly the legs the stage treats as loops). This is the
  labelled rule only: the filter makes no draw, a motorised leg with 710 or 711 stays (the model keeps it as a trip to a
  destination) and so does an out-and-back stroll (the model places it like any leisure leg). The filter needs the two
  marks of `mark_diary_returns` (`ValueError` otherwise), which every caller sets on the whole diary BEFORE its purpose
  filter, because the next leg of a stroll is usually the way home, which is no leisure leg. It is logged per loop mode
  with the legs left out and kept and the out-and-back strolls kept (`out_and_back_kept` in the counts); a loop mode that
  loses all its legs is a WARNING.
- **Place 1: the leisure subtype distance layers** (`distance_distributions.run`, Step 8). The subtype layers are built
  from the leisure legs without the loop legs; the two marks are set right after Step 2 (`preceding_purpose`), and only
  with both `secondary_leisure_subtype_split` and `secondary_leisure_estimates_without_loops` on. Only the layer of the group that holds the loop codes changes. A loop
  mode that had legs in that layer with the loops and has none without raises `ValueError`; the sampler is never left
  to fall back to the aggregate layer, which still holds the loops.
- **Place 2: the leisure subtype decider** (`deciders._build_leisure_subtype_decider`). The filter runs on the MiD legs
  of the spec's own `W_ZWECK` values, after the code coverage guard and before `estimate_group_probabilities`; the two
  marks are set before that purpose filter on the weekday diary, with the chain derivation of
  `loops.estimate_loop_probabilities`. A
  (mode, travel-time band) cell that falls below the minimum number of observations takes the marginal, which the
  estimate's thin-cell log and the decider's fallback count already report.
- **The switch and its prerequisites.** Config key `secondary_leisure_estimates_without_loops`
  (`config_keys.KEY_SECONDARY_LEISURE_ESTIMATES_WITHOUT_LOOPS`; default and production value: the key comment in
  `braunschweig/popsim/stage/config_keys.py` and [`configs/base_bs.yml`](../../../configs/base_bs.yml)). On requires
  `secondary_leisure_subtype_split` and `secondary_loop_zero_displacement`: `config_keys.
  require_loop_free_estimate_prerequisites` runs in `configure` of both stages and raises `ValueError` naming the
  missing key. Both stages declare the key with the shared constants and read the same key of one resolved config, so
  they cannot differ within a run. Off: both estimates are as before the change.
- **Reports.** `subtype_estimate_legs_left_out` in the `loops` block of `secondary_locations_report.json` (the
  decider's counts per loop mode: legs before the filter, left out, kept; the key is absent with the switch off), and
  `distance_distributions_report.json` in the folder of the distance distributions stage (written only with the switch
  on), which holds `leisure_subtype_legs_left_out` (the same counts for the layers) and `leisure_subtype_layer_legs`
  (per leisure group and mode, `legs_with_loops` and `legs_without_loops`).
- **What stays untouched.** The aggregate `leisure` distance layer, the `leisure_unspecified` layer, every other
  purpose, and the estimate of the loop probability itself (it needs the labelled loops by definition). Motorised
  loops stay in both estimates. A loop kept on the solver (step 1) is placed and drawn like any other leisure leg, so it
  draws its subtype and its distance from the loop-free estimates.
- **Survey reference.** `scripts/extract_mid_w_zwd_groups.py --without-loops` applies the same rule through
  `mark_diary_returns` and `drop_loop_legs` (the mode is `trips.map_mode` of `hvm_imp`); see Comparisons.

The assumptions of this section are in ADR-0207.

## From the stage to the plans: the marker and the attribute

The Java stroll rule (below) needs to know which activity is an out-and-back stroll, and a place cannot tell it: the
activity has its own destination. The marker travels in four hops, all behind `secondary_loop_zero_displacement`:

1. The secondary location stage writes the column `is_out_and_back_stroll` (step 8 of the placement rule).
2. The day-view location join (`spatial_locations_day._with_out_and_back_strolls`) copies it to every located activity
   (False elsewhere); a marked row without a located activity, or a secondary result without the column, raises.
3. The plans writer wrapper (`braunschweig/matsim/scenario/population.py`) raises when the switch is on and the location
   frame has no such column (`_require_stroll_marker`: a pipeline whose location stage is the plain vendored join drops
   it, and the Java rule would silently never fire); the in-commuters' rows get False (`_injected_rows_without_stroll`:
   the marker comes from the residents' stage, an injected agent's plan is not solved by it).
4. The writer (`matsim/scenario/population.py`, `add_person`) writes the Boolean activity attribute `outAndBackStroll`
   (`java.lang.Boolean`, written only as `true`; a value that is no boolean raises). It logs the count and warns when it
   is zero with the column present.

With the switch off no column and no attribute exist. Loops carry no marker: a collapsed loop is covered by the
same-facility rule below, and a loop kept on the solver keeps the free mode choice (the reason: ADR-0207, decision 6).

## Mode choice (two Java trip rules)

Both rules are trip constraints of eqasim-java-bs, selected by name in `DiscreteModeChoice.tripConstraints`, and both
allow only walk or bicycle (one shared set in Java).

**Same-facility rule.** A loop activity sits at the facility of the activity before it, so the trip into it and the way
home after it start and end at one facility. The Java constraint `SameFacilityLeisureNonMotorised`, class
`org.eqasim.braunschweig.mode_choice.constraints.SameFacilityLeisureTripConstraint`: origin and destination activity
carry the same non-null facility id and at least one end is of type `leisure`; trips with the initial mode `outside`
are not constrained, and same-facility trips without a leisure end stay free. The rule reads the place, not the loop
flag or a marker. It acts only on the trips the mode choice evaluates: a caught trip in a tour the mode choice does not
re-choose keeps its initial mode; the factory (`SameFacilityLeisureTripConstraint.Factory`) counts the caught,
evaluated and not evaluated trips in the Java log. When the border tour vehicle rule of #678 is selected too, a caught
trip is not pinned there but gets no new vehicle mode (see the Javadoc of
`org.eqasim.braunschweig.mode_choice.constraints.BorderTourVehicleConstraint`).

**Stroll rule.** The Java constraint `StrollNonMotorised`, class
`org.eqasim.braunschweig.mode_choice.constraints.StrollTripConstraint`: a trip whose DESTINATION activity carries the
attribute `outAndBackStroll` with the value true may only be walk or bicycle; false or an absent attribute place no
restriction, and a value that is no `java.lang.Boolean` makes the constraint throw. Like the first rule it acts only
where the mode choice evaluates the trip, and its factory counts the caught, evaluated and not evaluated trips in the
Java log (the line starts with "stroll trips"). What it does not reach: the way home after the stroll (a different
trip, whose destination carries no marker, stays free), a loop kept on the solver (no marker), and any trip in a tour the
mode choice does not re-choose. It needs no coordination with the border tour rule: an out-and-back stroll starts at
home and its next trip ends at home, so its tour never touches an `outside` stay.

The Python side only selects both: `braunschweig/matsim/simulation/loop_mode_rule.py` and `prepare._select_loop_mode_rule`
add every missing name of `loop_mode_rule.CONSTRAINT_NAMES` to `DiscreteModeChoice.tripConstraints` of the final config
before the initial mode choice, behind the config key `braunschweig.mode_choice.loop_trips_non_motorised` (default and
production value: the key comment in [`configs/base_bs.yml`](../../../configs/base_bs.yml)), refuse a jar that lacks
either class (`loop_mode_rule.CONSTRAINT_CLASSES`; all missing ones are named), and write `loop_mode_rule_report.json`
into the prepare stage folder (written only while the switch is on; it names the constraints and the classes
checked). The reasons and the rejected alternatives are in ADR-0207, decision 6.

## Comparisons

`braunschweig/calibration/secondary_measurement.py` (`leave_out_loop_legs`), `scripts/validate_secondary_distances.py`
and `scripts/calibrate_detour_circuity.py` leave loop legs out of the model side where the cache carries `loop_origin`;
a secondary activity without a marker row raises. Both scripts read the locations, the activities, the trips and the
marker from the cache pickles of ONE run (`validate_secondary_distances.resolve_run_pickles`): synpp caches a stage
under its resolved alias target, so the production names are those of `configs/base_bs.yml` `aliases:` (the day
locations, the day activities, `trips_final`, the secondary locations); the plain names are the fallback of the
older workflows. The legs' locations pickle must be unique: several candidates (hashed variants of a copied cache, or
both names) are refused with their times listed, unless the run is named (`--locations-key <stage>__<hash>` of both
scripts). The activities, trips and marker are the pickles in the dependency closure of that pickle in the working
directory's `pipeline.json`; a role with one cached pickle and no link is taken with a WARNING, and several
candidates without a link are refused, never resolved by file age. The circuity script resolves only its secondary
legs this way; its other stages (home, work, enriched, education) are still the newest pickle of their name, which
it prints. The trips are the modes the chain solver
saw, before the outside-tour mode stage, and the locations and activities must hold the same `(person_id,
activity_index)` keys or the script raises. The survey side of `validate_secondary_distances` and of the circuity
calibration's band-shift impact is the published MiD W12 table, which keeps loops and has no `W_ZWD`; both scripts state
that difference. The W_ZWD group reference has a loop-free variant that applies the rule of the loop-free estimates
([data record](../../registry/data/mid2023_w_zwd_group_reference_without_loops.yml)).

The mapping of the MiD purposes and detail codes: [`mid-purpose-mapping.md`](mid-purpose-mapping.md).
