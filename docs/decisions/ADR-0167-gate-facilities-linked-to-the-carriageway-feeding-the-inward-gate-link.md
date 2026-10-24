# ADR-0167 · 2026-10-23 · Gate facilities are linked to the carriageway that feeds the inward gate link

- **Status:** accepted (maintainer decision in the session of 2026-10-23, issue #465: "Fix now", variant A
  "outside the cordon" chosen over per-direction facilities and over keeping the portal stays inside).
- **Numbering:** ADR-0167 is the next free id. Checked on 2026-10-23 across all local and remote branches, all
  worktrees and the shared session notes: the highest record or claim anywhere is ADR-0166.
- **Issue:** #465. **Relates to:** ADR-0141 (portal trips; supersedes the "gate inside the extent" part of its
  decision 3 for the road gates of car legs, see Consequences), ADR-0144 (the same defect class for freight).
- **Code:** eqasim-bs `braunschweig/data/cordon/gate_facility_links.py` (`select_gate_facility_links`,
  `attach_gate_facility_links`, `read_car_links`), `braunschweig/matsim/scenario/facilities.py`
  (`_attach_gate_facility_links`), `matsim/scenario/facilities.py` (`_optional_links`), `matsim/writers.py`
  (`FacilitiesWriter.start_facility`); eqasim-java-bs `FacilityPlacement` (core). Measurement:
  `scripts/measure_gate_facility_links.py`.
- **Evidence:** run manifest `docs/runs/gate-facility-links-1pct-2026-10-23.yml`; the defect itself in
  `docs/runs/freight-a2-east-turnaround-prod100-2026-10-23.yml` (`gate_links_rerun.csv`).

## Context

- Two kinds of facility sit at a road gate of `braunschweig.synthesis.cordon_gates`: the portal stays
  `portal_<gate_id>` (ADR-0141, at the inside end node of the gate link) and the homes `home_<household_id>` of the
  in-commuters anchored at the gate point (`braunschweig.synthesis.incommuters`, and the nearest-gate homes of
  `braunschweig.synthesis.student_incommuters`).
- They were written with coordinates only. eqasim core's `RunPreparation` attaches every facility with
  `FacilityPlacement` to the closest link that `OSMFacilityPlacementVoter` accepts, and that voter rejects every
  link whose `osm:way:highway` contains `motorway`, `trunk` or `_link`. It also overwrote any link the file carried.
- Measured in the 1 % rerun `~/wt-i487-rerun/output_rerun` (run manifest
  `freight-a2-east-turnaround-prod100-2026-10-23`, corrected count): 84 of 90 gate facilities were not on their
  gate link, and no motorway-class gate had its facilities on a motorway link: A2-west `gate_0000`/`gate_0007` on
  `501844` (the 600 veh/h residential street of #444), A2-east `gate_0004`/`gate_0006` on `647295`. In the 1 %
  rerun2 (run manifest `gate-facility-links-1pct-2026-10-23`, `gate_facility_table_before.txt`) none of the 90
  gate facilities was on the link this ADR selects, 28 sat on links below 1,000 veh/h, none on a motorway or trunk
  link.
- **A placement defect, not a measured traffic problem.** The same manifest measured no traffic effect at 1 %:
  `501844` has 0 network departures (31 walks), and the highest peak-hour share of the simulated capacity on any
  gate-facility link is 0.83, local traffic included (`gate_departures_rerun.txt`). Portal stays leave by
  teleported `outside` legs. Whether the placement loads the streets at 100 % is unmeasured. The decision rests
  on the placement being wrong (trips that enter the region by motorway start on a residential street), not on an
  observed queue.
- `cordon_gates` keeps both carriageways of some motorways as separate gates (`gate_0004` inward, `gate_0006`
  outward at A2-east), and the gate assignment gives them identical counts for every municipality. Linking each
  gate to its own gate link would send half of the A2-east in-commuters onto the outward carriageway, which turns
  at the network end: the turnaround that queued trucks for 16 h in `prod100_20260925` (ADR-0144 Context).
- A MATSim activity has one link. A trip arriving at link L reaches L's from-node and traverses L; a trip departing
  from L starts at L's to-node. A stay at a gate is reached outward and left inward, so no single link on a
  carriageway serves both without a turn.
- eqasim's `RunScenarioCutter` (`DefaultNetworkRouteCrossingPointFinder`, `NetworkTripProcessor`, `PlanCutter`,
  `DefaultMergeOutsideActivities`, read in eqasim-java-bs on 2026-10-23) turns every route crossing into an
  `outside` activity on the crossing link and merges three consecutive outside activities into two. Every other
  cross-boundary resident trip already gets this form: arrival on the outward crossing link, departure from the
  inward one.

## Decision

1. **Facility link rule** (`braunschweig/data/cordon/gate_facility_links.py`). For each road gate:
   - the **entry link** is the car link that crosses into the cut extent (from-node outside, to-node inside, by the
     cutter's own `polygon.contains`) with at least `cordon_gate_facility_link_minimum_capacity_veh_per_h` and the
     smallest perpendicular distance to the gate point within `cordon_gate_facility_link_maximum_distance_m`; ties
     go to the lower link id;
   - the **facility link** is, among the car links that end at the entry link's from-node, start outside the extent
     and carry at least the minimum capacity, the one with the highest capacity (the carriageway, not an on-ramp);
     ties go to the lower link id. It lies wholly outside the extent.
   The capacity is converted from the network's `capperiod` to veh/h. The extent is the polygon
   `matsim.simulation.prepare` writes for the cutter (`build_cordon_polygon` with `cordon_network_buffer_fraction`).
2. **Which facilities get it.** A portal facility `portal_<gate_id>` of a road gate, and every home whose
   coordinate equals a gate point exactly (the in-commuter stages copy the gate geometry). Rail exit stations and
   all other facilities are untouched. The facility keeps its coordinate; only the link is set.
3. **eqasim keeps a preset link.** The facilities file carries `linkId` for these facilities. eqasim-java-bs
   `FacilityPlacement` keeps a preset link, validates before its worker threads start that it is a car link of the
   network (else the preparation fails), and logs the numbers of kept and placed facilities. A file without links
   behaves as before.
4. **Effect.** A departure from the facility link enters through the entry link. An arrival leaves on the router's
   outward path and turns outside the extent. The cutter puts the outside activities on those two crossing links,
   so in the cut scenario the gate traffic uses the inward and the outward carriageway, whatever gate of the
   corridor (`gate_0004` or `gate_0006`) the agent was assigned to. The direction problem of the gate assignment is
   solved by the rule, not by splitting the gates.
5. **Fallback, counted.** A gate without an entry link (`no_entry_link`) or without a feeding link
   (`no_feeding_link`) gets no preset link: its facilities keep eqasim's placement. The facilities stage logs
   primary vs fallback facilities as a rate (warning when any falls back, with the gates and reasons), writes the
   per-gate selection to `gate_facility_links.csv` in its stage directory, and fails when the fallback share of the
   gate facilities exceeds `cordon_gate_facility_link_maximum_fallback_share`.
6. **Options** of `braunschweig.matsim.scenario.facilities`, declared only with `cordon_enabled`:
   `cordon_gate_facility_links_enabled` (default true), `..._minimum_capacity_veh_per_h` (1,000),
   `..._maximum_distance_m` (300), `..._maximum_fallback_share` (0.05). OFF writes no link (byte-identical file).

## Choice of the parameters (measured, not calibrated)

On the pre-cut network (`matsim.scenario.supply.processed`) and the 47 road gates of the 1 % rerun2 cache, with
`scripts/measure_gate_facility_links.py` (output in the run manifest's artifacts):

| minimum capacity | 50 m | 100 m | 200 m | 300 m | 500 m |
|---|---|---|---|---|---|
| 600 veh/h | 2/47 | 2/47 | 2/47 | 2/47 | 2/47 |
| 1,000 veh/h | 2/47 | 2/47 | 2/47 | 2/47 | 2/47 |
| 1,500 veh/h | 2/47 | 2/47 | 2/47 | 2/47 | 2/47 |

The cells are fallback gates. The result does not depend on the parameters: every entry link lies within 24 m of
its gate point, and every selected facility link is a motorway, motorway_link, trunk or primary link of at least
1,500 veh/h. A2-west (`gate_0000`, `gate_0007`) gets `199729` feeding `199731`; A2-east (`gate_0004`, `gate_0006`)
gets `430864` feeding `430865`. The two fallback gates `gate_0028`/`gate_0037` are one place where a primary road
leaves the extent for one node and re-enters: both links into that node come from inside, so no outside carriageway
exists (`no_feeding_link`). In the 1 % rerun only one portal facility (0 activities) used them. 1,000 veh/h and
300 m mirror ADR-0144; they leave room for a different network without changing this one's result.

## Rejected alternatives

- **Per-direction facilities and activities** (entry facility on the inward gate link, exit facility on the outward
  one; the portal stay split into two activities). Cleanest in the simulation and needs no turnaround before the
  cut, but rewrites the portal stage, the trip table and the population writer. Rejected by the maintainer as the
  larger change for the same cut result.
- **Keep the portal stays inside the extent on one link** (ADR-0141 decision 3) and link only the in-commuter homes
  outside. Any carriageway link inside makes either the arrival or the departure turn outside, and the cutter then
  adds an extra outside/inside pair with a short car trip to every portal stay.
- **Link each gate facility to its own gate link.** The gate link crosses the boundary: an arrival on the inward
  gate link crosses twice, and `gate_0006`'s facility would sit on the outward carriageway (the turnaround above).
- **Fix the placement in Java only** (a direction-aware voter in `RunPreparation`). The gate set, the gate points
  and which facility belongs to which gate are Python facts; the Java side would have to re-derive them. Python
  already reads the pre-cut network for the gates (`braunschweig.data.cordon_network`).

## Consequences

- Network loads and travel times near every road gate change in every run with `cordon_enabled`: the in-commuter
  and portal car trips use the motorway or trunk carriageways instead of residential streets. Synthesis is not
  affected; `braunschweig.matsim.scenario.facilities` and everything downstream recompute.
- **Supersedes part of ADR-0141 decision 3 for car legs.** A portal stay reached by car now becomes, after the cut,
  the cutter's own pair `outside` (outward crossing) -> `outside` (inward crossing), and the portal activity (with
  its `portalGate` marker) is merged away; the stay's end time passes to the first outside activity
  (`DefaultMergeOutsideActivities`). No production code reads the marker (checked 2026-10-23: only a run-artifact
  script). Portal stays reached by a teleported mode stay inside, as before, with the facility link outside; the
  cutter keeps that link and a connection to it in the cut network (`NetworkCutter` retains facility links and
  `MinimumNetworkFinder` connects them).
- The arrival route reaches the facility link through a turnaround outside the extent in the pre-cut network. The
  cut removes that part. If the turnaround re-entered the extent, the cutter would make extra crossings; the smoke
  checks this (run manifest).
- eqasim-bs needs an eqasim-java-bs build with the preset-link change. An older jar overwrites the links, so
  `matsim.simulation.prepare` compares every pinned facility of the written file with the prepared facilities file
  right after the eqasim preparation and fails on any difference (`verify_preset_links_kept`): the Java-side
  fallback cannot pass silently.
- Not shown here: that the delays on the residential streets at the gates disappear in a 100 % run. That needs a
  100 % run and is not claimed.
- Open, not changed by this ADR: `cordon_gates` keeps both carriageways of a corridor as two gates, which counts
  that corridor twice in the gravity gate assignment (follow-up, not an issue yet).
