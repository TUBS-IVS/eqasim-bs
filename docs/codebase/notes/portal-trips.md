# Portal trips (`braunschweig/synthesis/portal_trips/`)

Maintenance rules for the portal layer: what its threshold means, which invariants a change must keep, how
the Java side and the analyses fit in, and how to read the stage's log. Why the layer exists, what was
rejected and the assumptions are in ADR-0141 (issue #442); the production state is the Feature Registry record
`portal_trips`; the stage semantics are in the three stage records
`braunschweig.synthesis.portal_trips.{stage,anchors}` and `synthesis.population.trips.final` (the DAG node
of `trips_final`). Nothing here repeats those.

## Layout

| Module | Role |
|---|---|
| `config_keys.py` | The ONE home of the `braunschweig.portal.*` key names, defaults, `validate_settings`, the RNG offset, the `outside` purpose and the `portalGate` attribute name. Every reader declares the keys with these constants. |
| `classification.py` | Which legs are portal legs; consecutive portal destinations become one outside stay. |
| `gates.py` | Road gate points, rail exit stations, the external point draw, the detour rule. |
| `timing.py`, `modes.py` | Re-entry time from the diary; the fixed mode and its substitution. |
| `rewrite.py` | Rewrites the trip table around the stays and emits the gate anchors. |
| `stage.py`, `trips_final.py`, `anchors.py` | The synpp stage (one dict `{trips, anchors, report}`) and the two thin stages that hand one frame each to their consumers. |

`synthesis.population.trips.final` resolves to `trips_final` in `configs/base_bs.yml`; the fixtures keep their own
alias and never see the portal stage.

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
- **Portal location rows carry `location_id = -1`.** A gate is coordinate-only (no facility), like the
  in-commuter gate home. `braunschweig/matsim/scenario/facilities.py` (`validate_secondary_coverage`) excludes
  `-1` from the dangling-id check and logs how many rows it excluded. That count must equal the chain solver's
  `portal anchors: N outside stays fixed at their gates` line; a mismatch means a stray `-1` from another
  source. A genuinely dangling id still raises.
- **Same gate and same mode out and back.** Do not give the return leg its own mode or gate; `VehicleContinuity`
  and the Java constraint rely on it.
- **The OFF path is byte-identical.** With `braunschweig.portal.enabled: false` the stage returns the input frame
  and an empty anchors frame, the chain solver adds no anchors, the CDFs and candidates are unbounded and the
  population writer sets no marker. The tests that pin it are listed in the feature record.
- **Reported `euclidean_distance` of the two portal legs is the inside part only** (origin to gate, gate to the
  stay's origin). The chain solver ignores donor distances; only reporting sees them.
- **Fallbacks stay observable.** A new fallback in this package needs a counted rate in `build_portal_trips`'s
  report, a log line in `_log_report`, and a test that exercises the primary path.

## The Java constraint and the marker

`PortalTripConstraint` (eqasim-java-bs, module `braunschweig`, branch `feature/i442-portal-trip-constraint`)
keeps a trip's initial mode when the trip touches an `outside` activity whose Boolean attribute `portalGate`
is `true`. The Python writer `matsim/scenario/population.py` sets `portalGate=true` as a `java.lang.Boolean` (a
string would not be recognised) on every `outside` activity it writes. In the Python-written population only
portal stays have that purpose: the in-commuters' gate homes are `home`. Interplay with the existing
constraints:

- `OutsideFilter` still excludes from mode choice every tour whose first origin or last destination is an
  `outside` activity: the in-commuters' tours and, for a stay without return (the day ends outside), the
  resident's last tour. A portal stay in the middle of a tour passes the filter and is what the new constraint
  acts on;
- `OutsideConstraint` pins the cutter's legs whose initial MODE is `outside` (and forbids that mode otherwise);
  the portal constraint pins the donor mode of legs that touch a marked gate. The cutter's own outside
  activities (crossing points, virtual activities) carry no marker, so the portal constraint leaves them alone;
- `VehicleContinuity` holds because both portal legs share the mode.

The attribute name exists in three places that must stay equal: the Java constant
`PortalTripConstraint.PORTAL_GATE_ATTRIBUTE`, `config_keys.PORTAL_GATE_ACTIVITY_ATTRIBUTE` and the literal
`PORTAL_GATE_ATTRIBUTE` of the vendored writer (which does not import `braunschweig`).
`tests/test_population_writer_portal_gate.py::test_portal_gate_attribute_name_matches_single_home_in_config_keys`
pins the two Python ones; the Java unit tests (`PortalTripConstraintTest`) use the Java constant.

## The `cross_boundary` category in the analyses

Trips that touch an `outside` activity are reported separately; all existing keys keep their all-trips
definition, so earlier run manifests stay comparable.

- `run_metrics.metrics_matsim` gains the additive blocks `in_region` (trips touching no `outside` activity:
  `n_trips`, `mode_share_pct`, `mean_trip_km`, `median_trip_km`, `commute`) and `cross_boundary` (`n_trips`,
  `share_pct`, `mode_share_pct`, `mean_km`). Both are skipped with a warning when `eqasim_trips.csv` lacks the
  purpose columns.
- `cross_boundary` holds EVERY trip with an `outside` purpose at either end: the portal stays of residents AND
  the in-commuters' trips. It is not a portal-only count.
- `run_mid_validation` gains an additive `long_distance_trips` block (measurement only, no reference); its MiD
  comparisons are unchanged because the MiD side is not filtered.
- The SimWrapper behaviour sankey excludes trips touching an `outside` activity.
- Limitation: MATSim's own `modestats.csv` (read by `modestats_inside`) has no purposes, so it keeps counting
  portal legs under their mode and cannot separate them. The eqasim termination criterion is unaffected (the
  modes of portal legs do not change between iterations).

## Reading the stage's log (tag `[portal_trips]`)

The same numbers are in the `report` of the core stage. Each rate that can mean "the primary method is broken"
warns above `braunschweig.portal.fallback_warn_share`.

| Log line (abridged) | What it tells you |
|---|---|
| `primary legs assigned a/b ..., reported-distance fallback c` | Work/education legs classified by the assigned location (primary) versus the donor's reported distance (fallback, legitimate for persons without an assigned location). Raises when EVERY work/education leg falls back: the join of the primary locations is broken. It can also fire on a tiny run without primary locations. |
| `N portal legs -> M outside stays for p/q persons` | Size of the effect; also removed inner legs, stays without return, stays by kind (road/rail) and mode. |
| `gate usage (top 10 ...)` | A single gate taking most stays points to a wrong gate set or a degenerate detour rule. |
| `external point drawn outside the distance band` | Band-miss rate of the external point draw (the nearest point is used). High = tolerance too tight or the external point set too sparse around the reported distances. |
| `origin proxied by home` | Stays whose real origin (a secondary activity, or a primary activity without a location) was replaced by home for the gate choice. |
| `re-entry clamped` / `donor return mode differs` | Defensive clamp (should be 0 with consistent diaries) and the count of stays whose return mode differs from the outbound mode. |
| `share_out capped ...; reported distance missing or zero for K` | Stays whose outside share was forced to 1; K is the part caused by a missing or zero reported return distance. |
| `stay persons have no has_license value` | Persons treated as unlicensed in the car check. |
| `donor outbound mode the mode check does not know` | Modes outside the substitution table; kept unchecked. |
| `mode substituted for a/b stays` | Mode substitutions by reason; warns above `mode_substitution_warn_share`. |

Outside the stage: the population writer logs `re-moded car -> car_passenger: a/b car legs ..., of which c
portal legs` (the pre-existing carless re-mode shim, now counted), the distance-distribution stage logs the
dropped share per mode (`trips beyond N m dropped`), and the candidate stage logs
`external centroids inside the supply ring: a/b kept`.
