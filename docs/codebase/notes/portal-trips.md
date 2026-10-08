# Portal trips (`braunschweig/synthesis/portal_trips/`)

Maintenance rules for the portal layer: what its threshold means, which invariants a change must keep, how
the mode choice and the analyses interact with it, and how to read the stage's log and report. Why the layer exists, what was
rejected and the assumptions are in ADR-0141 (issue #442); the production state is the Feature Registry record
`portal_trips`; the stage semantics are in the three stage records
`braunschweig.synthesis.portal_trips.{stage,anchors}` and `synthesis.population.trips.final` (the DAG node
of `trips_final`). Nothing here repeats those.

## Layout

| Module | Role |
|---|---|
| `config_keys.py` | The ONE home of the `braunschweig.portal.*` key names, defaults, `validate_settings`, the RNG offset, the `outside` purpose, the `portal_` location id prefix, the `portalGate` attribute name and `final_view_trips_stage` (which trips stage the validation analyses read). Every reader declares the keys with these constants. |
| `classification.py` | Which legs are portal legs; consecutive portal destinations become one outside stay. |
| `gates.py` | Road gate points, rail exit stations, the external point draw, the detour rule. |
| `timing.py`, `modes.py` | Arrival at the gate and re-entry time from the diary; the fixed mode and its substitution. |
| `rewrite.py` | Rewrites the trip table around the stays and emits the gate anchors. |
| `stage.py`, `trips_final.py`, `anchors.py` | The synpp stage (one dict `{trips, anchors, report}`) and the two thin stages that hand one frame each to their consumers. |

`synthesis.population.trips.final` resolves to `trips_final` in `configs/base_bs.yml`; the fixtures keep their own
alias, run without a cordon and set `braunschweig.portal.enabled: false`, so the portal stage is not part of their
DAG (see the first invariant below).

## Threshold semantics

`braunschweig.portal.max_routable_distance_m` (straight-line metres, default 45,000, valid range > 0) is
read by three consumers and bounds three things:

1. the portal stage: a leg is a portal leg when its distance exceeds it. The distance is home to the ASSIGNED
   location for work and education legs, and the donor's reported `euclidean_distance` for every other
   purpose; a return home or a missing distance is never a portal leg;
2. `braunschweig.popsim.distance_distributions`: the CDFs are built from donor trips within it (guard rail
   one: the sampler cannot draw beyond the supplied area);
3. `braunschweig.synthesis.locations.secondary_candidates`: external Gemeinde centroids are kept only inside
   the supply ring (guard rail two). The ring is built from `cordon_network_source_buffer_m` (the network SOURCE
   clip), NOT from the MATSim cut (`cordon_network_buffer_fraction`); candidates between the two still become
   cutter outside activities.

The threshold and the ring width are separate keys with the same default on purpose. Changing one without the
other is legitimate but is a decision: lower the threshold and legs that the supply could serve become portal
trips; raise it above the ring and a leg can be classified normal although no supply exists at its end. All
other keys: `external_point_distance_tolerance` (0, 1), `mode_substitution_warn_share` (0, 1],
`fallback_warn_share` (0, 1] (default 0.1; the warn level of the two classification/draw fallbacks).

## Invariants a change must keep

- **Any config that aliases `synthesis.population.trips.final` to something other than
  `braunschweig.synthesis.portal_trips.trips_final` must set `braunschweig.portal.enabled: false`.** The code
  default of the flag is true and the chain solver stages the portal anchors on the flag alone (it does not look
  at the alias), so a fixture with its own alias and the default flag would pull the portal stage, and with it
  the cordon stages it requires, into a DAG that has none (the dag extraction of `simple_ipf_open` failed this
  way, ruling R29). All `configs/fixtures/*` configs set the flag to false; production
  (`configs/base_bs.yml`) is the only config with the flag on.
- **The gate point must lie inside the cut extent.** The cutter runs unchanged after the portal stage; a stay
  outside the extent would be cut again and the stay would be corrupted. Road gates use the inside end node of the
  gate link (`gates.gate_inside_points`, which raises listing every gate with no endpoint inside); rail gates are
  stations inside the extent from which a rail route continues to a stop outside it (`gates.rail_exit_stations`,
  derived from the supply timetable, not from `braunschweig.data.cordon_pt_gates`). Pinned by
  `tests/test_portal_trips_gates.py::test_gate_inside_points_takes_the_link_endpoint_inside_the_polygon` and
  `...::test_gate_inside_points_raises_when_no_endpoint_is_inside`.
- **`outside` is a fixed, anchored purpose of the vendored problem splitter** (`FIXED_PURPOSES` and
  `ANCHORED_PURPOSES` in `synthesis/population/spatial/secondary/problems.py`). Any new producer of `outside`
  trips must also hand `(person_id, activity_index) -> geometry` anchors to the chain solver, otherwise the
  splitter raises on the first `outside` boundary; the chain solver fails early with a clear message when trips
  carry `outside` activities but the anchors frame is empty.
- **Every gate is a real facility.** The location rows carry `location_id = "portal_<gate_id>"`
  (`config_keys.PORTAL_LOCATION_ID_PREFIX`, the only home of the prefix) because eqasim core's Java
  `LinkAssignment` throws for any activity whose facility does not exist.
  `braunschweig/matsim/scenario/facilities.py` (`portal_facility_frame`) registers one facility per distinct
  `portal_*` id of the realised secondary locations, at the gate coordinate, through the optional `df_portal`
  parameter of the vendored `matsim/scenario/facilities.py` writer (offering the activity type `outside`);
  `validate_secondary_coverage` accepts exactly these ids and still raises on any other dangling id (a
  `portal_*` id that was not registered included). Two caveats: a gate id that sits at two coordinates raises,
  and a secondary candidate id that starts with `portal_` would be misread as a gate (no such candidate id
  exists today). The log line `N portal gate facilities registered` should equal the number of distinct gates
  in the stage's `gate_usage`.
- **Same gate and same mode out and back.** Do not give the return leg its own mode or gate; the discrete mode
  choice's `VehicleContinuity` relies on it.
- **The OFF path is byte-identical.** With `braunschweig.portal.enabled: false` the stage returns the input frame
  and an empty anchors frame, the chain solver adds no anchors, the CDFs and candidates are unbounded and the
  population writer sets no marker. The tests that pin it are listed in the feature record.
- **Reported `euclidean_distance` of the two portal legs is the inside part only** (origin to gate, gate to the
  stay's origin). The chain solver ignores donor distances; only reporting sees them.
- **The outbound leg arrives at the gate after the inside share of its reported duration**
  (`timing.outbound_arrivals`: `departure + duration x min(1, inside / reported)`, counted when capped). The chain
  solver samples the distance of the preceding secondary leg from `arrival - departure`; keeping the diary's
  arrival at the far destination would inflate that distance. MATSim ignores planned arrivals, so only the
  synthesis is affected. The re-entry clamp lower bound is the arrival at the gate, so the gate activity never
  ends before it starts. Known unmeasured case: a rail gate that coincides with the origin proxy gives an inside
  distance of 0 and thus a zero-duration outbound leg; look at the outbound-capped rate and the outbound duration
  distribution in the first smoke run.
- **The spatial inputs of the stage are in one CRS.** `assert_consistent_crs` (municipalities, home locations,
  gates, links, external points) raises before the first spatial step and in the pure entry point
  `build_portal_trips`; the cordon polygon carries no CRS and is covered through the municipalities.
- **Fallbacks stay observable.** A new fallback in this package needs a counted rate in `build_portal_trips`'s
  report, a log line in `_log_report`, and a test that exercises the primary path.

## The mode choice and the `portalGate` marker

No Java code of this feature exists (ADR-0141, decision 6). The production discrete mode choice runs
`ModelType.Tour` with the `ActivityBased` tour finder on the activity types `[home, outside]` and `OutsideFilter`
(eqasim core `GenerateConfig`; the Braunschweig `RunAdaptConfig` never selects `IsolatedOutsideTrips`). A gate is
an `outside` activity, so it bounds tours, and `OutsideFilter` removes from the choice every tour that touches
one: both tours adjacent to a stay keep their diary (or substituted) modes, exactly as the cutter-outside persons
do today. `VehicleContinuity` holds because both portal legs share the mode. The parked Java branch
`feature/i442-portal-trip-constraint` (`PortalTripConstraint`) would never fire under this configuration; selecting
`IsolatedOutsideTrips` for the mode choice would need it and is a separate decision.

The Python population writer (`matsim/scenario/population.py`) still sets the Boolean activity attribute
`portalGate=true` (a `java.lang.Boolean`) on every `outside` activity it writes. In the Python-written population
only portal stays have that purpose (the in-commuters' gate homes are `home`), so the attribute is an
identification marker: it tells a portal gate from the outside activities the cutter creates at crossing points,
for analyses. The attribute name exists twice, `config_keys.PORTAL_GATE_ACTIVITY_ATTRIBUTE` and the literal
`PORTAL_GATE_ATTRIBUTE` of the vendored writer (which does not import `braunschweig`);
`tests/test_population_writer_portal_gate.py::test_portal_gate_attribute_name_matches_single_home_in_config_keys`
pins them equal.

## The `cross_boundary` category in the analyses

Trips that touch an `outside` activity are reported separately; all existing keys keep their all-trips
definition, so earlier run manifests stay comparable.

- `run_metrics.metrics_matsim` gains the additive blocks `in_region` (trips touching no `outside` activity:
  `n_trips`, `mode_share_pct`, `mean_trip_km`, `median_trip_km`, `commute`) and `cross_boundary` (`n_trips`,
  `share_pct`, `mode_share_pct`, `mean_km`). Both are skipped with a warning when `eqasim_trips.csv` lacks the
  purpose columns.
- `cross_boundary` holds EVERY trip with an `outside` purpose at either end: the portal stays of residents, the
  in-commuters' trips AND the cutter-created outside activities of residents' trips to candidates between the
  scenario cut and the supply ring (the candidate ring is the 45 km network source clip, wider than the cut). It
  is not a portal-only count; the `portalGate` marker is the way to separate portal gates in a plan-level
  analysis.
- `run_mid_validation` gains an additive `long_distance_trips` block (measurement only, no reference); its MiD
  comparisons are unchanged because the MiD side is not filtered.
- The SimWrapper behaviour sankey excludes trips touching an `outside` activity.
- Limitation: MATSim's own `modestats.csv` (read by `modestats_inside`) has no purposes, so it keeps counting
  portal legs under their mode and cannot separate them.

### Analyses that read the pre-portal trips while the flag is on

`plan_structure_vs_srv` and `departure_time_vs_srv` (view `final`) and `work_participation_by_kreis` validate the
donor-based day, not the portal rewrite. With `braunschweig.portal.enabled` true they read
`braunschweig.synthesis.commute_day.trips_day_stage` (`config_keys.PRE_PORTAL_TRIPS_STAGE`) instead of
`synthesis.population.trips.final`; the choice has one home, `config_keys.final_view_trips_stage`, and their
provenance records `portal_layer_enabled` and the stage actually read. Any new stage that validates the donor day
against a reference must use the same helper.

### Known limitations: file-based validators (not changed in this feature, ruling R36)

Validators that read the written trips CSV instead of staging a trips frame cannot use the helper above, and with
the flag on they count the far legs as `outside`: `braunschweig/analysis/population_validation/participation_fit.py`,
`braunschweig/analysis/population_validation/trip_coherence.py` and the commute and purpose tables of
`braunschweig/analysis/run_mid_validation.py` (its MiD side is unfiltered). Their work/education participation and
purpose tables are therefore expected to move in the OFF/ON A/B; a difference there is not a portal defect. A
follow-up issue (read the pre-portal trips, or exclude persons with a portal stay) is proposed to the project
owner and is not yet opened.

## Reading the stage's log and report (tag `[portal_trips]`)

The same numbers are in the `report` of the core stage and, for an enabled run, in the file
`portal_trips_report.json` in the stage's cache directory (the OFF run writes no file; its report is
`{"enabled": false}`); cite that file, not the log, in the A/B run manifest. Each rate that can mean "the
primary method is broken" warns above `braunschweig.portal.fallback_warn_share`.

| Log line (abridged) | What it tells you |
|---|---|
| `primary legs assigned a/b ..., reported-distance fallback c` | Work/education legs classified by the assigned location (primary) versus the donor's reported distance (fallback, legitimate for persons without an assigned location). Raises when EVERY work/education leg falls back: the join of the primary locations is broken. It can also fire on a tiny run without primary locations. |
| `N portal legs -> M outside stays for p/q persons` | Size of the effect; also removed inner legs, stays without return, stays by kind (road/rail) and mode. |
| `gate usage (top 10 ...)` | A single gate taking most stays points to a wrong gate set or a degenerate detour rule. |
| `external point drawn outside the distance band` | Band-miss rate of the external point draw (the nearest point is used). High = tolerance too tight or the external point set too sparse around the reported distances. |
| `origin proxied by home` | Stays whose real origin (a secondary activity, or a primary activity without a location) was replaced by home for the gate choice. |
| `re-entry clamped` / `donor return mode differs` | Defensive clamp (should be 0 with consistent diaries) and the count of stays whose return mode differs from the outbound mode. |
| `share_out capped ...; reported distance missing or zero for K` | Stays whose outside share of the return leg was forced to 1; K is the part caused by a missing or zero reported return distance. |
| `outbound share capped ...; reported distance missing or zero for K` | The same for the outbound leg's inside share (arrival at the gate); warns above `fallback_warn_share`. |
| `stay persons have no has_license value` | Persons treated as unlicensed in the car check. |
| `donor outbound mode the mode check does not know` | Modes outside the substitution table; kept unchecked. |
| `mode substituted for a/b stays` | Mode substitutions by reason; warns above `mode_substitution_warn_share`. |

Outside the stage: the population writer logs `re-moded car -> car_passenger: a/b car legs ..., of which c
portal legs` (the pre-existing carless re-mode shim, now counted), the distance-distribution stage logs the
dropped share per mode (`trips beyond N m dropped`), and the candidate stage logs
`external centroids inside the supply ring: a/b kept`, and the facilities stage logs
`N portal gate facilities registered`.
