# ADR-0141 · 2026-10-08 · Portal trips: long-distance trips leave and re-enter the supplied area through the cordon gates

- **Status:** active (implemented; behaviour effect NOT yet validated, the 1 % OFF/ON A/B run manifest is owed)
- **Numbering:** ADR-0141 is the next free id. Checked on 2026-10-08 across all local and remote
  branches and the two open pull requests (#438, #441): the highest record anywhere is ADR-0140
  (parking cost zones v2, on a branch that is not yet merged).
- **Issue:** TUBS-IVS/eqasim-bs#442 (sub-project 1 of three; the plan feasibility check and stuck KPI are
  #443, the freight gate connector is #444).
- **Amends:** ADR-0027 (external centroids are bounded to the supply ring; beyond it the portal layer
  takes over). **Relates to:** ADR-0028 / ADR-0029 (cordon, in-commuters and gates), ADR-0104 (the
  reporting-day trips view the portal stage is stacked on).

## Context

- The secondary distance sampler (`braunschweig.synthesis.locations.secondary_chainsolvers.distance_sampling`,
  a 1:1 mirror of eqasim's `CustomDistanceSampler`) draws a leg's desired distance from the MiD distribution
  conditioned on mode and travel-time bin. The donor's own reported straight-line distance
  (`euclidean_distance` of the trip table, from MiD `wegkm_imp`) is not used.
- Since ADR-0027 every German Gemeinde centroid is a candidate for shop/leisure/other/escort legs, so a
  drawn long distance lands on a point far beyond the supplied area (the ZGB plus the 45 km network and
  timetable ring). Beyond that ring there is no network and no timetable.
- `RunScenarioCutter` routes the plans before cutting. A leg without a transit connection becomes a walk,
  and the cutter's `TeleportationTripProcessor` sets the outside activities' end times from the routed
  walk time while later activities keep the donor's end times. The result is non-monotonic plans, plans
  ending after the simulated day, and very long walk legs. The discrete mode choice repairs only part of
  this and cannot repair a location. The measurements behind this paragraph belong to issue #442, not here.
- eqasim France never meets the problem: `data/hts/egt/filtered.py` drops every donor with a trip outside
  the region and the candidate set is region-only. Both guard rails were removed in this model (nationwide
  MiD donors, nationwide candidates).
- Intent: trips whose destination lies beyond the supplied area must be present with a mode, a distance and
  a timeline that fit together, must not distort the in-region mode choice, and must not require nationwide
  supply. Everything inside the supplied area keeps eqasim's logic (sampler, chain solver, scenario cutter,
  discrete mode choice); only what is missing is added.

## Decision

Behind the flag `braunschweig.portal.enabled` (default on, `configs/base_bs.yml` only):

1. **One configured threshold decides portal versus normal (D2).** A leg is a portal leg when its
   straight-line distance exceeds `braunschweig.portal.max_routable_distance_m` (default 45,000 m, a separate
   key from the ring width `cordon_network_source_buffer_m` although it starts at the same value). For
   work and education legs the distance is home to the ASSIGNED location (that location exists before the
   secondary sampler runs); for every other purpose it is the donor's reported distance capped by the diary's
   own estimate of the destination's distance from home (amended 2026-10-09, approved by the user): the
   classification distance is `min(reported leg distance, way out before + way back)`, where the way out is
   the sum of the reported distances of the legs from the person's last home departure up to the leg's
   origin (0 when the leg starts at home) and the way back is the sum from the leg's destination to the next
   home arrival, inclusive of that home-bound leg. The reported distance (`euclidean_distance` =
   `wegkm_imp * 1000 / ROUTED_DETOUR_FACTOR`, factor 1.3) is a straight-line ESTIMATE of the donor's leg, not
   a measured displacement, and the cap is a diary-consistency cap at estimate level: a leg whose estimated
   length exceeds the estimated way back home (plus the way out) contradicts the rest of its own diary
   (typically a leisure round trip), and the smaller estimate is taken. Why: with the reported distance alone,
   the 1 % smoke had 35 of the 114 drawn (non-work/education) stays with a known way home whose reported way
   home was below 45 km (the `H-C drawn` line of
   `docs/runs/artifacts/portal-trips-ab-1pct-2026-10-09/share_cap_probe2_output.txt`; a way-home-only measure,
   not the full bound). The measured effect of the full cap is in the partial re-run recorded in
   `docs/runs/portal-trips-ab-1pct-2026-10-09.yml`. A side whose sum is
   unknown (a leg without a finite reported distance, e.g. the synthetic home closure, or a chain that does not
   start at home before the leg / does not reach home after it) gives no bound; the reported distance then
   stays (no silent guess), and both sides are needed. Work/education legs keep the assigned-location
   distance; a return home is never a portal leg and a missing distance (the synthetic home closure) is never
   one either. The bounded distance is the classification distance everywhere it is used, including the
   external point draw below.
2. **Portal trips are built before the cut, in cut form, at a gate (D3).** Consecutive portal destinations
   form one outside stay (the `MergeOutsideActivities` semantics); the outbound leg ends at an `outside`
   activity at the gate, the legs inside the stay are removed, the return leg starts at the gate. This is
   the same shape the in-commuters already have, so the cutter finds nothing to cut. The new stage
   `braunschweig.synthesis.portal_trips.stage` (with the thin stages `...trips_final`, aliased as
   `synthesis.population.trips.final`, and `...anchors`) rewrites the trip table once; the consumers of the
   finished day keep reading the alias. Three analyses that validate the pre-portal day read the pre-portal
   stage instead (decision 10).
3. **Gate choice by minimum detour.** The external point only fixes direction and outside distance. For work
   and education it is the assigned location; otherwise one external Gemeinde point is drawn with
   population weights among those within `classification distance x (1 +- tolerance)` of the leg's origin anchor
   (`braunschweig.portal.external_point_distance_tolerance`, default 0.2), the nearest point when the band
   is empty (counted as a band miss). The gate minimises `|origin - gate| + |gate - point|`; motorway gates
   are not preferred by rule. Car, car passenger, bicycle and walk use road gates (the inside end node of
   the gate link of `braunschweig.synthesis.cordon_gates`); public transport uses RAIL EXIT STATIONS derived
   from the supply timetable (`portal_trips.gates.rail_exit_stations`: stations inside the cut extent from
   which a rail route continues to a stop outside it), NOT `braunschweig.data.cordon_pt_gates` (which are
   the in-commuters' entry stations). Reason: every gate must lie inside the cut extent, otherwise the
   cutter would cut the stay again; schedule-derived exit stations are inside the extent by construction.
4. **Times split the donor diary's own durations by the synthetic geometry, no speed assumption (D4).** The
   outbound departure is unchanged. The outbound leg arrives at the gate at
   `departure + share_in x (arrival - departure)` with
   `share_in = |origin - gate| / (|origin - gate| + |gate - point|)`. The stay ends at
   `t_reentry = return.departure + share_out x (return.arrival - return.departure)` with
   `share_out = |gate - point| / (|gate - point| + |gate - return_proxy|)`, where `return_proxy` is the stay's
   origin (the same proxy that fixes the return leg's reported inside distance, see the assumptions). The
   return leg departs at `t_reentry` and arrives after the remaining `(1 - share_out)` share of its duration.
   Both shares lie in [0, 1] by construction and no reported (donor) distance enters the timing. A zero
   denominator (every distance involved is zero) gives the share 0.5 and is counted as degenerate; a re-entry
   before the arrival at the gate (or, with inconsistent diaries, before the outbound departure) is clamped
   (counted). Reason: the chain solver samples the distance of the preceding secondary leg from
   `travel_time = arrival - departure`, so the diary's arrival at the far destination would inflate it; MATSim
   ignores planned arrivals, so only the synthesis is affected. The donor's reported distance describes the
   donor's own trip, not the synthetic geometry: for a work/education stay the point is the ASSIGNED location
   (median 66 km from home in the 1 % smoke) while the donor reported a different trip (median 13.8 km; both from the probe named under the rejected alternative), and
   synthetic home-closure legs have no reported distance at all.
5. **Same gate and same mode out and back (D5).** The donor's OUTBOUND mode is used for both portal legs,
   checked against the synthetic person (car needs car availability and a licence, bicycle needs bicycle
   availability, car passenger needs passenger availability). Substitution order: car to car passenger to pt,
   car passenger to pt, bicycle to pt. Walk is kept (the diaries contain no long walk, measured in #442). No vehicle is left at
   a gate and the discrete mode choice's `VehicleContinuity` holds. A differing donor return mode is counted.
6. **The portal modes need no Java change (the spec's decision D6, a Java trip constraint, was superseded
   during implementation).** The production discrete mode choice runs `ModelType.Tour` with the
   `ActivityBased` tour finder on the activity types `[home, outside]` and the `OutsideFilter` (eqasim core
   `GenerateConfig`; the Braunschweig `RunAdaptConfig` never selects `IsolatedOutsideTrips`; checked in the
   code during the final review of #442). A gate is an `outside` activity, so it bounds tours, and the tours
   that touch it are removed from the choice by `OutsideFilter`: both tours adjacent to a stay keep their
   diary (or substituted) modes, exactly as the cutter-outside persons do today. The planned
   `PortalTripConstraint` (eqasim-java-bs branch `feature/i442-portal-trip-constraint`) would therefore never
   fire; it is NOT part of this feature, and the branch is parked without a pull request. Selecting
   `IsolatedOutsideTrips` for the mode choice would need that constraint and is a separate decision. The
   Python population writer (`matsim/scenario/population.py`) still sets the Boolean activity attribute
   `portalGate=true` on every `outside` activity it writes; with the portal off no activity carries it. It is
   an identification marker only (analyses can tell a portal gate from the outside activities of the cutter);
   no code of the production configuration reads it.
7. **Two guard rails restore eqasim's "stay inside the region" invariant behind the same flag.**
   `braunschweig.popsim.distance_distributions` builds the CDFs from donor trips within the threshold only
   (and raises when a mode loses every trip); `braunschweig.synthesis.locations.secondary_candidates` keeps
   only external Gemeinde centroids inside the supply ring (`restrict_external_to_supply_ring`; raises when
   a non-empty input keeps nothing, which means a CRS mismatch or a broken ring). The ring is the network
   SOURCE clip (`cordon_network_source_buffer_m`, 45 km), which is wider than the MATSim scenario cut
   (`cordon_network_buffer_fraction`): candidates between the cut and the ring still become cutter
   outside activities exactly as before this ADR.
8. **The chain solver treats the gate as a fixed anchor.** `outside` joins `FIXED_PURPOSES` and
   `ANCHORED_PURPOSES` of the vendored problem splitter, each gate is handed over through the existing
   `activity_anchors` path, and chains split at the gate like at work or education. Every gate is a real
   facility: the location rows carry `location_id = "portal_<gate_id>"` and
   `braunschweig/matsim/scenario/facilities.py` registers one facility per used gate at the gate coordinate
   (the vendored `matsim/scenario/facilities.py` writer got an optional `df_portal` parameter; none or empty
   writes nothing, so the OFF file is unchanged). Reason: eqasim core's Java `LinkAssignment` throws for any
   activity whose facility does not exist (the in-commuters' gate homes are the facilities
   `home_<household_id>`); the facilities coverage check accepts the registered `portal_*` ids and still
   raises on any other dangling id.
9. **Analyses report the cross-boundary trips as a category of their own, additively.** Every pre-existing
   dashboard key keeps its definition over all trips; `run_metrics.metrics_matsim` gains the additive
   `in_region` and `cross_boundary` blocks, `run_mid_validation` an additive long-distance block, and the
   SimWrapper behaviour sankey excludes trips touching an `outside` activity.
10. **The SrV comparisons and the work-participation report read the pre-portal trips while the flag is on.**
    `plan_structure_vs_srv` and `departure_time_vs_srv` (view `final`) and `work_participation_by_kreis` read
    `braunschweig.synthesis.commute_day.trips_day_stage` instead of `synthesis.population.trips.final` when
    `braunschweig.portal.enabled` is true (helper `config_keys.final_view_trips_stage`; the OFF path and the
    `pre_assignment` view are unchanged). Reason: they validate the donor-based day (plan structure, departure
    times, work participation), not the portal rewrite, and far legs that became outside stays would show up
    as an artificial drop in work trips and a distorted plan structure; this keeps the existing SrV
    comparisons unchanged. Their provenance records `portal_layer_enabled` and the stage actually read.
11. **The synthesis output writes the pre-portal trips next to the post-portal ones, and the file-based
    validators prefer them.** The written `<prefix>trips.csv` is the post-portal day, so no file kept the donor
    purposes: `trip_coherence.purpose_distribution` raised on the unmapped purpose `outside` and
    `participation_fit` counted a far worker as not working. While `braunschweig.portal.enabled` is true,
    `braunschweig.synthesis.commute_day.output_day` additionally writes `<prefix>trips_pre_portal.csv` (and
    `.parquet` when that output format is on) from `braunschweig.synthesis.commute_day.trips_day_stage` with the
    vendored trips column set, derived by the same function as `trips.csv`
    (`synthesis.output.prepare_trip_output_frame`). No mode is merged in, because the MATSim mode-choice trip
    indices refer to the post-portal table. `population_validation/population_source`, the trips read of
    `run_mid_validation` and `scripts/measure_trip_coherence.py` take the pre-portal file when it exists
    (`braunschweig.analysis.pipeline_trips_file`, which logs the choice and ignores, with a warning naming both
    modification times, a pre-portal file older than `trips.csv`, i.e. a leftover of an earlier run); readers that
    need the realised plan keep `trips.csv`. `run_mid_validation` also derives its activity-purpose counts from the
    pre-portal trips with the activities file's definition. Because `commutes.gpkg` (built by the vendored writer
    from the written activities) lacks the far commuters, the stage also writes
    `<prefix>commutes_pre_portal.gpkg` while the flag is on: the same home -> work lines with the vendored
    selection (`synthesis.output.build_commute_frame`), for the persons whose pre-portal trips contain a work
    activity, the work location taken from `synthesis.population.spatial.primary.locations`; the home -> education
    lines are its layer `education`. The commute and education tables of `run_mid_validation` read it when it
    exists and is current. Flag off: nothing is written and every output is byte-identical.

### Rejected alternatives

- **A per-leg reachability test instead of one threshold.** The reach of the supplied area differs by
  direction, so a reachability rule would steer long trips toward the directions the supplied area happens
  to reach farthest (for example toward Hannover). One threshold is direction-neutral (known cost: it
  ignores the home's position inside the region).
- **Extending the Java cutter** (deep eqasim-core change) and **patching plans after the cut** (the pt to
  walk fallback has already happened by then).
- **Nationwide supply** (network and timetable) so that nothing needs a portal.
- **Mode speeds for the outside part** of the trip instead of the diary's own times.
- **The donor's return mode** for the return leg (counted when it differs, not used).
- **A per-leg attribute plus writer extension** to mark the fixed mode; an activity marker identifies the
  portal gates without per-leg data. An earlier Java constraint variant keyed on the bare `outside` type
  alone was dropped because the cutter creates `outside` activities of its own on every cut plan; the
  explicit `portalGate` activity marker replaced it, and the whole constraint was then superseded (decision 6).
- **A Java `PortalTripConstraint` in the discrete mode choice** (the spec's D6). Superseded during
  implementation: under the production configuration `OutsideFilter` already freezes every tour that
  touches an `outside` activity, so the constraint would never fire (decision 6).
- **Splitting the diary durations by `distance in the synthetic geometry / donor reported distance`, capped at 1
  (the first implementation of D4).** Rejected after the 1 % smoke (run `portal-trips-ab-1pct-2026-10-09`): the
  numerator (synthetic geometry) and the denominator (a different, donor-reported trip) describe different
  geometries, so 440 of 617 return shares (71 %) and 183 of 617 outbound shares were capped at 1, which gives a
  planned inside return duration of zero. The geometric split above gave a median `share_out` of 0.60 (p10 0.19,
  p90 0.88) on the same stays, with no zero inside duration, in a probe on the cached smoke inputs (`share_cap_probe2.py` and its output
  `share_cap_probe2_output.txt`, artifacts of the run manifest; the capped counts are in the manifest and its
  report artifact). The fix is not
  re-measured in a run (eqasim-bs#442).
- **Classifying non-primary legs by the reported distance alone (the first implementation of D2).** Replaced
  by the diary-consistency cap above: 35 of the 114 drawn stays with a known way home had a reported way home
  below the threshold although the leg itself was reported above it (a way-home-only measure).
- **Conditioning the distance sampler on the reported distance class.** Kept as a possible later A/B, not
  part of this change.

### Assumptions (stated, not measured)

- ASSUMPTION: the direction of a long-distance secondary trip is a population-weighted distance proxy (the
  same assumption as ADR-0027; there is no secondary OD survey).
- ASSUMPTION: exit and re-entry use the same gate, and the same mode is used out and back.
- ASSUMPTION: the outside share of the diary duration of a leg scales with the share of the straight-line
  distances of the synthetic geometry (origin, gate, point; the return leg uses the stay's origin as proxy).
- ASSUMPTION: the detour rule for gate choice does not prefer motorways.
- ASSUMPTION: a fixed 45 km threshold ignores the home's position inside the region (D2).
- ASSUMPTION: the reported distances are straight-line estimates (route length / 1.3) with a per-leg detour
  error in both directions, so the triangle inequality holds for them only approximately; the cap is strict
  only if every leg's real detour is at least 1.3. It is a consistency cap at estimate level, not a proven
  upper bound of the displacement. Cost if wrong (inconsistent or detour-heavy diaries): some genuine far legs
  with a short reported way home become in-region legs.
- ASSUMPTION: the return leg's reported inside distance is gate to the stay's origin, not gate to the
  return leg's own destination; reporting only, the chain solver ignores donor distances.
- The threshold's default equals the ring width by choice, not by derivation.

## Consequences

- **Cache.** The `synthesis.population.trips.final` alias now resolves to the portal stage, so `trips.final`
  and everything downstream of it recompute once; `distance_distributions` and `secondary_candidates` also
  recompute (new flag and threshold keys, new helper tokens). PopulationSim and the stages upstream of
  `trips_day_stage` stay cached. Cost: the secondary location choice and the MATSim scenario build, on the
  order of the earlier `.final` alias switch of ADR-0104.
- **Results change by design.** With the flag on, plans with long-distance legs change (gate anchors, fixed
  portal modes, removed inner legs, a bounded distance pool and bounded candidates). The success criteria
  to be measured are: plans ending after the simulated day and non-monotonic end times near zero in the
  input population, no long walk legs in the input population, portal legs by mode and purpose of the same
  order of magnitude as the diaries' long-distance legs (measured in the SAME run), in-region SrV
  comparisons (departure times, plan structure, commute distances) unchanged, and an OFF path that is
  byte-identical. None of this is established yet: the 1 % OFF/ON A/B run manifest is owed, and until it is
  recorded the feature record states the validation as pending. Convergence of the mode shares is not
  validation.
- **OFF path.** `braunschweig.portal.enabled: false` returns the input trip frame unchanged, an empty
  anchors frame, no anchors in the chain solver, unbounded CDFs, unbounded candidates and a population file
  without the marker; the pinning tests are listed in the feature record.
- **Fail-early behaviour.** The stage raises at configure without the cordon and on an invalid setting; at
  execute when the spatial inputs (municipalities, home locations, gates, links, external points) are not in
  one CRS (`assert_consistent_crs`, before the first spatial step), when every work/education leg falls back
  to the reported distance (a broken join), when a road gate link has no endpoint inside the cut extent, and
  when the stay and trip tables disagree (for example an outbound leg without times). The chain solver
  (`_require_anchors_for_outside_trips`) raises only when the anchors frame is empty and trips touch an
  `outside` activity (the anchors stage and the trips are out of sync); any other outside activity without an
  anchor stops later in the vendored problem splitter. The work/education-fallback raise can also fire on a
  tiny run without assigned primary locations; that is a property of the input, not of the model.
- **Fallbacks are observable.** The stage logs, as rates and in its report, the reported-distance fallback
  of work/education legs, the external-point band misses, the origins proxied by home, the degenerate re-entry and
  outbound shares (all distances zero, share 0.5; the median return share is logged for orientation), the mode
  substitutions by reason,
  stay persons without a licence value and donor modes the mode check does not know; the rate keys warn above
  `braunschweig.portal.fallback_warn_share` (default 0.1) and `braunschweig.portal.mode_substitution_warn_share`.
  The report is also written as `portal_trips_report.json` in the stage's cache directory, so the A/B run
  manifest can cite it without the log. The population writer additionally logs the car to car-passenger
  re-mode rate, now counted.
- **Reporting limits.** MATSim's own `modestats.csv` has no purposes and keeps counting portal legs under their
  mode, so `modestats_inside` cannot separate them. The dashboard's `cross_boundary` block holds every trip
  with an `outside` activity at either end, which includes the in-commuters' trips, not only portal stays of
  residents.
- **Java.** No Java change is part of this feature (decision 6). The parked branch
  `feature/i442-portal-trip-constraint` of the eqasim-java-bs fork (`PortalTripConstraint`, commits 09889f79f,
  a9ad6f261, afdb3f278) has no pull request; reviving it is a separate decision that comes with selecting
  `IsolatedOutsideTrips`. Consequence for the A/B: the modes of both portal legs stay the diary (or
  substituted) modes, as for the cutter-outside persons today.
- **Validators that read the written trips CSV (decision 11).** `population_validation/participation_fit.py`,
  `population_validation/trip_coherence.py` (through `population_source`) and the trip counts of
  `run_mid_validation` read `<prefix>trips_pre_portal.csv` while it exists, so their work/education participation
  and purpose distributions describe the donor day, as do its activity-purpose counts and, through
  `<prefix>commutes_pre_portal.gpkg`, its commute and education distance tables. Without that file (an output
  directory written before it existed, or a run that did not write a spatial format) those two tables fall back to
  `<prefix>activities.gpkg`, the post-portal day, cover workplaces inside the portal threshold only and the run
  warns with the number of work persons without a commute row.
- **Documentation.** Stage records `braunschweig.synthesis.portal_trips.{stage,anchors}` and the re-pointed alias
  `synthesis.population.trips.final` (the DAG node of the thin stage `trips_final`), a record for
  `braunschweig.synthesis.commute_day.trips_day_stage` (now a DAG node of its own, carrying the content the
  alias record held before), the
  feature record `portal_trips`, the contributor note `docs/codebase/notes/portal-trips.md`; no new input
  dataset.

## Evidence

- Issue TUBS-IVS/eqasim-bs#442; branch `feature/i442-portal-trips`.
- Tests: `tests/test_portal_trips_*.py`, `tests/test_chainsolver_portal_anchors.py`,
  `tests/test_distance_distributions_portal_filter.py`, `tests/test_secondary_candidates_supply_ring.py`,
  `tests/test_population_writer_portal_gate.py`, `tests/test_facilities_portal_gates.py`,
  `tests/test_srv_comparisons_portal_view.py`, `tests/test_work_participation_by_kreis.py`,
  `tests/test_dashboard_cross_boundary.py`. No Java evidence: the Java constraint is parked (decision 6).
- Contributor note `docs/codebase/notes/portal-trips.md`; feature record `docs/registry/features/portal_trips.yml`.
