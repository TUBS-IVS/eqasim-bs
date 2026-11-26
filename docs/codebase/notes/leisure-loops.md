# Strolls and dog walks: the loop flag and the placement rule

This note is the ONE home of the definition of a loop leg (the flag) and of the rule that places a loop
activity. The reasons, the rejected approaches and the assumptions are in
[ADR-0207](../../decisions/ADR-0207-strolls-and-dog-walks-stay-where-they-start.md); the switch and its production
state are in the feature record
[`secondary_loop_zero_displacement`](../../registry/features/secondary_loop_zero_displacement.yml); numbers are in the
run manifests those records link (the 1 % evidence of both switches:
[`i679-stroll-loops-ab-1pct-2026-11-25`](../../runs/i679-stroll-loops-ab-1pct-2026-11-25.yml)).
Code: `braunschweig/synthesis/locations/secondary_chainsolvers/loops.py` (pure
functions: `estimate_loop_probabilities`, `flag_loops`, `select_collapsible_loops`, `collapse_loops`, the
`to_collapsed_*` translators, `restore_loops`), `secondary_chainsolvers/__init__.py` (`execute`, `_flag_loop_trips`,
`_linked_activity_keys`, `_collapse_loops_and_move_anchors`, `_require_no_loop_origin_outside`, `_report_loops`),
`braunschweig/matsim/scenario/facilities.py` (`_loop_origin_facility_ids`, `validate_secondary_coverage`) and, for
the loop-free estimates below, `braunschweig/popsim/purpose_subtype.py` (`LOOP_CODES`, `LOOP_MODES`,
`drop_loop_legs`), `braunschweig/popsim/distance_distributions.py` (`run`, Step 8) and
`secondary_chainsolvers/deciders.py` (`_build_leisure_subtype_decider`).

## Switch

Config key `secondary_loop_zero_displacement` (constant `config_keys.KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT` in
`braunschweig/popsim/stage/config_keys.py`, re-exported as `loops.KEY_ENABLED`); its default and production value are
owned by the key and its comment in [`configs/base_bs.yml`](../../../configs/base_bs.yml). Off: no loop function runs,
no MiD table is read for it, the result has no `loop_origin` column and the stage writes no report file. The key is
declared by the secondary location stage and, as a read-only mirror, by the facilities stage; both read the same key of
one resolved config, so they cannot differ within a run.

The second switch of the change, `secondary_leisure_estimates_without_loops`, has its own section below.

## The flag (`loops.estimate_loop_probabilities`, `loops.flag_loops`)

A Boolean per synthetic trip, true when the trip leads INTO a loop activity. It is computed on the trips table before
anything else of the stage rewrites it, and it is independent of the leisure subtype group (the group and its decider
stay untouched).

- **Scope.** The destination activity is leisure, the mode is walk or bicycle (`purpose_subtype.LOOP_MODES`, which
  `loops` re-exports; it is owned there because the loop-free estimates need it), the donor's
  `W_ZWECK` is in `purpose_subtype.LEISURE_ZWECK`, the donor row exists and the donor's `W_ZWD` is present. Every other
  trip is false and consumes no random number: motorised legs, the coarse purpose 10 (`leisure_unspecified`) and the
  child purposes stay out.
- **Labelled leg.** The donor's `W_ZWD` is one of the leisure detail codes of `purpose_subtype.leisure_spec`
  (`labelled_codes`, which follows `purpose_subtype_codeplan_sentinels`: with it on, 799 "Freizeit k.A." is not a
  label). The leg is a loop exactly when the code is in `purpose_subtype.LOOP_CODES` (710 stroll, 711 dog walk; re-exported by
  `loops`), by bicycle too.
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
  next trip of the same person ends at home (the last trip of a day has none). The probability is the `W_GEW`-weighted
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
4. **Solve.** Escort link, portal anchors, problems, both passes and result extraction run on the collapsed table, with
   every anchor and link in the collapsed numbering. A person left without a secondary activity has nothing to solve.
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

The random streams of the solver are consumed by fewer legs with the switch on, so a comparison of the two switch
states compares distributions; the stage tests compare against an input from which the loop was removed by hand.

## The loop-free leisure estimates (`secondary_leisure_estimates_without_loops`)

With the loops taken out of the chain, the leisure legs that remain must not be assigned a subtype, or a distance,
from estimates that still count the loops. With the switch on, two estimates leave the loop legs out:

- **The rule** (`purpose_subtype.drop_loop_legs`, the one row filter): a leg is left out when its mode is in
  `purpose_subtype.LOOP_MODES` AND its `W_ZWD` is in `purpose_subtype.LOOP_CODES`. This is the labelled rule only: the
  filter makes no draw, and a motorised leg with 710 or 711 stays (the model keeps it as a trip to a destination). It is
  logged per loop mode with the legs left out and kept; a loop mode that loses all its legs is a WARNING.
- **Place 1: the leisure subtype distance layers** (`distance_distributions.run`, Step 8). The subtype layers are built
  from the leisure legs without the loop legs. Only the layer of the group that holds the loop codes changes. A loop
  mode that had legs in that layer with the loops and has none without raises `ValueError`; the sampler is never left
  to fall back to the aggregate layer, which still holds the loops.
- **Place 2: the leisure subtype decider** (`deciders._build_leisure_subtype_decider`). The filter runs on the MiD legs
  of the spec's own `W_ZWECK` values, after the code coverage guard and before `estimate_group_probabilities`. A
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
  `drop_loop_legs` (the mode is `trips.map_mode` of `hvm_imp`); see Comparisons.

The assumptions of this section are in ADR-0207.

## Mode choice (same-facility leisure trips)

A loop activity sits at the facility of the activity before it, so the trip into it and the way home after it start and
end at one facility. The rule that such a trip may only be walk or bicycle lives in the Java trip constraint
`SameFacilityLeisureNonMotorised`, class `org.eqasim.braunschweig.mode_choice.constraints.SameFacilityLeisureTripConstraint`
(eqasim-java-bs): origin and destination activity carry the same non-null facility id and at least one end is of type
`leisure`; trips with the initial mode `outside` are not constrained, and same-facility trips without a leisure end stay
free. The rule reads the place, not the loop flag or a marker. It acts only on the trips the mode choice evaluates: a
caught trip in a tour the mode choice does not re-choose keeps its initial mode; the factory
(`SameFacilityLeisureTripConstraint.Factory`) counts the caught, evaluated and not evaluated trips in the Java log. When
the border tour vehicle rule of #678 is selected too, a caught trip is not pinned there but gets no new vehicle mode (see
the Javadoc of `org.eqasim.braunschweig.mode_choice.constraints.BorderTourVehicleConstraint`). The Python side only selects it:
`braunschweig/matsim/simulation/loop_mode_rule.py` and `prepare._select_loop_mode_rule` add the constraint name to
`DiscreteModeChoice.tripConstraints` of the final config before the initial mode choice, behind the config key
`braunschweig.mode_choice.loop_trips_non_motorised` (default and production value: the key comment in
[`configs/base_bs.yml`](../../../configs/base_bs.yml)), refuse a jar without the class, and write
`loop_mode_rule_report.json` into the prepare stage folder (written only while the switch is on). The reasons and the
rejected alternatives are in ADR-0207, decision 6.

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
