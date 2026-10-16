# ADR-0144 · 2026-10-15 · Freight boundary endpoints are linked by capacity and trip direction, not by MATSim's nearest node

- **Status:** accepted (maintainer request in the session of 2026-10-15, issue #444)
- **Numbering:** ADR-0144 is the next free id. Checked on 2026-10-15 across all local and
  remote branches: the highest record anywhere is ADR-0143 (ADR-0142 exists on a branch).
- **Issue:** #444. **Code:** eqasim-java-bs `FreightBoundaryLinkSelector`, `RunInjectFreight`;
  eqasim-bs `braunschweig/matsim/simulation/prepare.py` (`_inject_freight`).
- **Evidence:** run manifest `docs/runs/freight-boundary-links-prod100-2026-10-15.yml`.

## Context

- Issue #444 reported that 6,907 of the 7,595 trucks stuck at the end of the 100 % run
  `prod100_20260925` (final iteration 92) stood on link `501844`, a 209 m one-way
  residential street (600 veh/h, 30 km/h) next to the A2-west motorway gates. The issue
  assumed that the motorway is cut short of the network edge so that the gates hang off a
  local road.
- That assumption does not hold. The A2 carriageways reach the boundary: link `199731`
  (eastbound, 6,000 veh/h) passes 1.4 m from the crossing point.
- The cause is how freight agents get their links. The freight extraction trims every
  incoming, outgoing and transit route at the study area and keeps only the crossing
  coordinate. `RunInjectFreight` created the activities from coordinates only, so MATSim's
  `XY2Links` (`NetworkUtils.getNearestLink`) assigned them: it takes the nearest network
  NODE and then one of that node's links. Motorway nodes lie hundreds of metres apart,
  residential nodes are dense. The A2-west crossing point `(567989.4, 5804588.6)` is 91 m
  from residential node `299293713`, so all 11,922 trucks starting there (10,179 transit,
  1,743 incoming) started on `501844`. At 3.5 PCE that is several times the street's
  daily capacity, so the queue built up during the day and never drained.
- The same rule also put endpoints on the wrong carriageway: at the A2-east crossing both
  directions were linked to `450785`, and outgoing A2-west trucks ended on the eastbound
  carriageway `199731`.
- Measured on all 81,740 boundary endpoints of that run: 13,147 (16.1 %) sat on a link
  below 1,000 veh/h, and 13,153 on a link outside the classes motorway, motorway_link,
  trunk, trunk_link and primary (12,514 of 40,417 starts, 639 of 41,323 ends).
- Residents are not affected: their boundary activities come from the eqasim
  `RunScenarioCutter`, which places them on the boundary link of the routed path. Freight
  is injected after the cut, so the cutter never sees it.

## Decision

1. `RunInjectFreight` sets the link of every boundary endpoint explicitly: the start of
   incoming and transit trips, the end of outgoing and transit trips. Internal trips keep
   coordinate-only activities.
2. The link is the closest car link to the crossing point, measured as the perpendicular
   distance to the link segment, among the links that
   - have at least `freight_boundary_link_minimum_capacity_veh_per_h` (default 1,000
     veh/h, converted with the network capacity period), and
   - point in the trip direction: from the crossing point towards the other trip end for
     an entry, away from it for an exit. This selects the correct carriageway of a dual
     carriageway even when the opposite one is closer.
   Only links within `freight_boundary_link_maximum_distance_m` (default 300 m) count.
   Ties go to the lower link id.
3. An endpoint without such a link keeps MATSim's snapping. These fallbacks are counted
   per role (start/end), logged with a warning, and written to
   `freight_boundary_link_summary.csv`. The injection fails if they exceed
   `freight_boundary_link_maximum_fallback_share` (default 0.05) of all boundary
   endpoints. A high share means the rule is broken, for example a wrong CRS or a
   minimum capacity above the network's major roads.
4. The three parameters are synpp config options of `matsim.simulation.prepare`. Python
   always passes them, so the Java defaults only mirror the Python ones.
5. Unknown trip types now fail the injection instead of being injected without a
   boundary role.

## Choice of the parameters (measured, not calibrated)

On the 100 % network and endpoints of `prod100_20260925`, using
`scripts/measure_freight_boundary_links.py`, an independent re-implementation of the rule:

| eligibility filter | 100 m | 200 m | 300 m | 500 m |
|---|---|---|---|---|
| OSM highway class motorway/motorway_link/trunk/trunk_link/primary | 36.47 % | 0.67 % | 0.05 % | 0.05 % |
| capacity >= 1,000 veh/h | 36.47 % | 0.67 % | 0.05 % | 0.05 % |
| capacity >= 1,500 veh/h | 36.47 % | 0.67 % | 0.05 % | 0.05 % |

The cells are the fallback share of boundary endpoints at each maximum distance. All three
filters give the same result at every distance. Every selected link has at least 1,500
veh/h. 300 m is the smallest swept distance at which the fallback share reaches its floor
(39 endpoints). Under the rule, A2-west entries go to `199731` (eastbound, 1.4 m) and
A2-west exits to `224246` (westbound, 0.2 m). At A2-east, entries go to `430866` and exits
to `450785`.

## Rejected alternatives

- **OSM highway-class allowlist.** Measured identical to the capacity filter. Rejected
  because it needs the `osm:way:highway` link attribute, which a network built another way
  may not have. Capacity is the physical quantity that failed: a 600 veh/h street cannot
  carry the boundary flow. The maintainer proposed the capacity rule in the session.
- **Network extent in which motorways cross the cordon as motorways, or explicit motorway
  connector links per gate** (the two options in #444). Both address a missing motorway at
  the boundary. That defect does not exist: the motorway reaches the crossing point, and
  only the snapping missed it.
- **Linking in Python and adding link columns to the freight CSV.** This changes the
  cross-language CSV contract and needs a second network reader in Python. The Java
  injector already holds the cut network.
- **Nearest link by distance alone, without the direction test.** This links a crossing
  point between two carriageways to whichever is closer. The trip then has to turn at the
  next interchange or on local roads, which is the defect class above.

## Consequences

- Freight routes, and therefore network loads and travel times near every cordon
  crossing, change in every run with `freight_enabled`. In the measured run, 55,887 of
  81,740 boundary endpoints get a different link than before. Most move to the adjacent
  segment or to the opposite carriageway of the same road.
- The `matsim.simulation.prepare` stage hash changes (code and config), so its cache and
  everything downstream is recomputed.
- eqasim-bs needs an eqasim-java-bs build that has the new options. An older jar rejects
  the unknown `--boundary-link-*` options and fails the prepare stage early.
- Not shown yet: that the stuck-truck queue at `501844` disappears in a simulation.
  That needs a re-run (smoke, then production). It is tracked in #444 and is not claimed
  here.
- The parameters are measured on one network and one freight extraction. They are not
  calibrated, and a different network or study area can need other values; the fallback
  bound in decision 3 makes such a mismatch fail loudly.
