# ADR-0141 · 2026-10-08 · Portal trips: long-distance trips leave and re-enter the supplied area through the cordon gates

- **Status:** active (implemented on `feature/i442-portal-trips`; behaviour effect NOT yet validated, the
  1 % OFF/ON A/B run manifest is still owed, see Consequences)
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
   secondary sampler runs); for every other purpose it is the donor's reported distance. A return home is
   never a portal leg and a missing distance (the synthetic home closure) is never one either.
2. **Portal trips are built before the cut, in cut form, at a gate (D3).** Consecutive portal destinations
   form one outside stay (the `MergeOutsideActivities` semantics); the outbound leg ends at an `outside`
   activity at the gate, the legs inside the stay are removed, the return leg starts at the gate. This is
   the same shape the in-commuters already have, so the cutter finds nothing to cut. The new stage
   `braunschweig.synthesis.portal_trips.stage` (with the thin stages `...trips_final`, aliased as
   `synthesis.population.trips.final`, and `...anchors`) rewrites the trip table once; no consumer of
   `trips.final` changes.
3. **Gate choice by minimum detour.** The external point only fixes direction and outside distance. For work
   and education it is the assigned location; otherwise one external Gemeinde point is drawn with
   population weights among those within `reported x (1 +- tolerance)` of the leg's origin anchor
   (`braunschweig.portal.external_point_distance_tolerance`, default 0.2), the nearest point when the band
   is empty (counted as a band miss). The gate minimises `|origin - gate| + |gate - point|`; motorway gates
   are not preferred by rule. Car, car passenger, bicycle and walk use road gates (the inside end node of
   the gate link of `braunschweig.synthesis.cordon_gates`); public transport uses RAIL EXIT STATIONS derived
   from the supply timetable (`portal_trips.gates.rail_exit_stations`: stations inside the cut extent from
   which a rail route continues to a stop outside it), NOT `braunschweig.data.cordon_pt_gates` (which are
   the in-commuters' entry stations). Reason: every gate must lie inside the cut extent, otherwise the
   cutter would cut the stay again; schedule-derived exit stations are inside the extent by construction.
4. **Times come from the donor diary, no speed assumption (D4).** The outbound departure is unchanged. The
   stay ends at `t_reentry = return.departure + share_out x (return.arrival - return.departure)` with
   `share_out = min(1, |point - gate| / return.euclidean_distance)`; a missing or zero reported distance gives
   `share_out = 1` (counted), a re-entry before the outbound departure is clamped (counted). The return leg
   departs at `t_reentry` and arrives after the remaining `(1 - share_out)` share of its reported duration.
5. **Same gate and same mode out and back (D5).** The donor's OUTBOUND mode is used for both portal legs,
   checked against the synthetic person (car needs car availability and a licence, bicycle needs bicycle
   availability, car passenger needs passenger availability). Substitution order: car to car passenger to pt,
   car passenger to pt, bicycle to pt. Walk is kept (the diaries contain no long walk). No vehicle is left at
   a gate and the discrete mode choice's `VehicleContinuity` holds. A differing donor return mode is counted.
6. **The mode is fixed in the Java discrete mode choice by a trip constraint keyed on a marker (D6).**
   `org.eqasim.braunschweig.mode_choice.constraints.PortalTripConstraint` (eqasim-java-bs branch
   `feature/i442-portal-trip-constraint`) keeps the initial mode of a trip that touches an `outside` activity
   carrying the Boolean activity attribute `portalGate=true`. The Python population writer
   (`matsim/scenario/population.py`) sets that attribute on every `outside` activity it writes; the cutter's
   own outside activities (crossing points, virtual activities) never carry it and stay unconstrained, and
   with the portal off no activity carries it, so the constraint is a no-op.
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
   `activity_anchors` path, and chains split at the gate like at work or education. Gate location rows carry
   `location_id = -1` (coordinate-only, like the in-commuter gate home); the facilities coverage check
   excludes the placeholder explicitly and logs the count.
9. **Analyses report the cross-boundary trips as a category of their own, additively.** Every pre-existing
   dashboard key keeps its definition over all trips; `run_metrics.metrics_matsim` gains the additive
   `in_region` and `cross_boundary` blocks, `run_mid_validation` an additive long-distance block, and the
   SimWrapper behaviour sankey excludes trips touching an `outside` activity.

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
- **A leg attribute plus writer extension** to mark the fixed mode; the activity-type-based Java constraint
  needs no per-leg data. An earlier variant keyed on the bare `outside` type alone was dropped because the
  cutter creates `outside` activities of its own on every cut plan; the explicit `portalGate` marker makes
  the constraint a true no-op without portal gates.
- **Conditioning the distance sampler on the reported distance class.** Kept as a possible later A/B, not
  part of this change.

### Assumptions (stated, not measured)

- ASSUMPTION: the direction of a long-distance secondary trip is a population-weighted distance proxy (the
  same assumption as ADR-0027; there is no secondary OD survey).
- ASSUMPTION: exit and re-entry use the same gate, and the same mode is used out and back.
- ASSUMPTION: the outside share of the reported duration scales with the straight-line distance share.
- ASSUMPTION: the detour rule for gate choice does not prefer motorways.
- ASSUMPTION: a fixed 45 km threshold ignores the home's position inside the region (D2).
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
- **Fail-early behaviour.** The stage raises at configure without the cordon and on an invalid setting, at
  execute when every work/education leg falls back to the reported distance (a broken join), when the stay
  and trip tables disagree, and when an `outside` activity reaches the chain solver without an anchor. The
  work/education-fallback raise can also fire on a tiny run without assigned primary locations; that is a
  property of the input, not of the model.
- **Fallbacks are observable.** The stage logs, as rates and in its report, the reported-distance fallback
  of work/education legs, the external-point band misses, the origins proxied by home, the capped
  re-entry shares (including those caused by a missing reported distance), the mode substitutions by reason,
  stay persons without a licence value and donor modes the mode check does not know; the rate keys warn above
  `braunschweig.portal.fallback_warn_share` (default 0.1) and `braunschweig.portal.mode_substitution_warn_share`.
  The population writer additionally logs the car to car-passenger re-mode rate, now counted.
- **Reporting limits.** MATSim's own `modestats.csv` has no purposes and keeps counting portal legs under their
  mode, so `modestats_inside` cannot separate them. The dashboard's `cross_boundary` block holds every trip
  with an `outside` activity at either end, which includes the in-commuters' trips, not only portal stays of
  residents.
- **Java.** `PortalTripConstraint` lives in the eqasim-java-bs fork (commits 09889f79f, a9ad6f261,
  afdb3f278 on `feature/i442-portal-trip-constraint`); the pipeline builds it through `eqasim_source_path`
  and the fork needs its own pull request. The constraint is registered in
  `BraunschweigModeChoiceModule` and added to the discrete mode choice's trip constraints next to
  `OutsideConstraint` in `RunAdaptConfig`.
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
  `tests/test_population_writer_portal_gate.py`, `tests/test_facilities_portal_placeholder.py`,
  `tests/test_dashboard_cross_boundary.py`; Java `PortalTripConstraintTest` in eqasim-java-bs.
- Contributor note `docs/codebase/notes/portal-trips.md`; feature record `docs/registry/features/portal_trips.yml`.
