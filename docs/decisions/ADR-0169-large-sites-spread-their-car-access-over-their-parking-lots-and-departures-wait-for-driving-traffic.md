# ADR-0169 · 2026-10-23 · Large sites spread their car access over their parking lots, and departing vehicles wait for driving traffic

- **Status:** accepted (owner decisions of 2026-10-23, listed under "Decision"). Implemented; measured on the 1 %
  rerun of #465 in a continued-iteration A/B (run manifest `docs/runs/i465-large-facility-access-1pct-2026-10-23.yml`),
  a simulated effect, not a validation; see "Measured effect".
- **Numbering:** ADR-0169. Drafted as ADR-0165 (claimed in the shared session notes); main merged another
  ADR-0165 (generated views merge by rebuild) first, so this record was renumbered before its merge. 0166 to 0168 are
  taken by sibling #465 sessions or branches; 0169 re-checked free across all local and remote branches, worktrees,
  open pull requests and the shared session notes on 2026-10-23.
- **Issue:** #465 (road side of the stuck person 693334). **Relates to:** ADR-0144 (the capacity-aware link rule for
  freight boundary endpoints, reused here), ADR-0161 (the transit side of the same stuck).
- **Design:** `docs/superpowers/specs/2026-10-23-large-site-parking-access-design.md`, plan
  `docs/superpowers/plans/2026-10-23-large-site-parking-access.md` (local, not committed).

## Context

- In the 1 % rerun of #465 (felix `~/wt-i487-rerun/output_rerun/matsim_output`, eqasim-bs `be886cc6`, eqasim-java-bs
  `ad3f7e22`, final iteration 49) person 693334's car trip home took 3 h 21 min instead of the routed 19 min 42 s,
  2 h 35 min of it on link 129867 (Guntherstrasse, 28.5 m, tertiary, 600 veh/h), and then missed the last tram (the
  stuck itself is fixed by ADR-0161).
- **The network conversion is not the cause.** The OSM way is two-way with `lanes=2` (both directions), which
  pt2matsim turns into one lane per direction; 600 veh/h per lane is pt2matsim's `CreateDefaultOsmConfig` default for
  `tertiary` (the stage `matsim.scenario.supply.osm` uses that default config unchanged).
- **Facility placement is.** eqasim's `FacilityPlacement` (with `OSMFacilityPlacementVoter`) attaches every facility
  to its ONE nearest car link that is not a motorway, trunk or ramp, whatever its capacity, and `LinkAssignment`
  copies that link to every activity. All car departures of a site therefore enter the network on one link. MATSim
  allows one link per facility, and the routers use the facility's link, so a per-activity link does not survive.
- **Sites, not facility ids.** Incommuters get one facility each (`ic_work_<person>`, one activity) at exactly the
  coordinate of their workplace (`braunschweig/matsim/scenario/facilities.py`, work facilities block). At the VW
  Wolfsburg plant location (an inference from the coordinates only) the 100 % run `prod100_20260925` has 7004
  facilities with 41 926 activities in total: `work_101999` (34 303), 7002 `ic_work_*` and one `sec_b_*`.
- **MATSim's insertion priority makes it worse.** The QSim default `insertingWaitingVehiclesBeforeDrivingVehicles =
  true` (MATSim 2026.0, read from the jar; not set by this project) moves departing vehicles onto a link before the
  vehicles already driving on it. From about 14:30 to 19:00 a car from `work_97721` entered link 129867 every 600 s,
  its whole capacity at `flowCapacityFactor` 0.01.
- **Measured pattern** (probe scripts on felix `~/i465-link`; delay = time on link minus free-flow time):

  | run | through traversals delayed >= 1 h | on or upstream of a link saturated by site departures |
  |---|---|---|
  | 1 % rerun, final iteration | 138 on 14 links (126 persons) | 25 (5 links, departures 87-99 % of capacity) |
  | 100 % run `prod100_20260925`, final iteration | 28 300 on 20 links | 4 links directly (departures 70-110 %), plus the Mittelstrasse queue upstream of one of them |

  The rest are through demand above the link capacity, mostly 600 veh/h links and motorways; this ADR does not
  change capacities (see "Rejected options"). The motorway case at portal `outside_35` is the freight snapping defect
  of ADR-0144 (separate session, PR #503).
- **Large sites, 100 % run** (excluding portals, external centroids, homes): 236 sites of at least 1000 activities
  (541 769 activities); by activity mix 13 work sites (work >= 50 %, 84 819 activities), 9 universities (education
  94-100 %), the rest shops and leisure (`sec_b` 109), schools (`abs_` 49), kitas and other secondary locations.
- **Parking lots exist in the data.** The parking module's OSM inventory holds the plants' employee lots (e.g. "VW WOB
  P7 Sandkamp", "VW-WOB-MA-P11-Nord", "VW WOB MA P6"): within 2 km of the VW point 282 lots with about 28 700
  estimated spaces.

## Decision

Owner decisions of 2026-10-23 (chat): (1) implement a capacity-aware access and the insertion rule; (2) spread the
car access of large sites over the parking lots around them, weighted by spaces, with the activity location (the
facility coordinate) unchanged; (3) lots nearest first until their spaces cover 0.5 x the site size, at most 1500 m
from the site, the longer walk from the lot to the site intended; (4) the distribution for work sites and
universities only, every other large site gets one high-capacity link; (5) success criteria of the A/B, among them at
most +2 % car travel time against the control (the owner's approval of the design: "Approved, including your calls and
the 2 % car-time criterion"; the 2 % itself is an ASSUMPTION, not a reference).

1. **Sites.** A site is every facility at one coordinate (x, y to 0.01 m), except those whose id starts with
   `large_facility_access_excluded_prefixes` (default `outside_,EXT,home_,portal_`: outside activities of cut
   trips, external Gemeinde centroids, homes, and the portal gates, whose link the facilities stage pins and
   prepare verifies, ADR-0167). Size = activities at its facilities in the selected plans / `sampling_rate` (at 100 %); large from
   `large_facility_access_minimum_activities` (1000).
2. **Work sites and universities: parking-lot access** (eqasim-java-bs `LargeSiteAccessAssignment`, run by
   `RunAdaptLargeFacilityAccess` in `matsim.simulation.prepare` between eqasim's `RunPreparation` and the routing).
   In scope: `work` activities >= `large_facility_access_scope_work_share` (0.5) of the site's activities, or
   a facility id with a prefix of `large_facility_access_scope_prefixes` (`uni_loc_`).
   - Lots from the release `parking_lots_osm_2026` (data record; preflight D20): nearest first (ties: lot id) within
     `large_facility_access_lot_maximum_distance_m` (1500 m) until their spaces reach
     `large_facility_access_lot_demand_share` (0.5, ASSUMPTION of the owner: half of the activities park at once)
     x size; fewer spaces in reach = `distributed_partial`, all lots in reach used.
   - Access link of a lot: the closest link of >= `large_facility_access_minimum_capacity_veh_per_h` (1000 veh/h)
     within `large_facility_access_lot_link_maximum_distance_m` (300 m), else the closest eligible link of any
     capacity within 300 m, else the lot is skipped (counted); eligible = car, not motorway, trunk or ramp, as in
     eqasim's placement voter. Lots on the same link form one access group (weight = their spaces).
   - Every person with an activity at the site (any plan) gets ONE group: persons ordered by the CRC32 of site key and
     person id, cut into blocks by the largest-remainder method over the group weights (exact, deterministic, no RNG).
   - A facility keeps its id for the group with most of its persons; for each other group with persons there it gets
     a sub-facility `<id>__access_<n>` with the same coordinate, its activity options and attributes and the attribute
     `largeSiteParentFacilityId`. Single-person facilities (incommuters) are only relinked. The persons' activities at
     the site take their group's facility and link (all plans); adjacent leg routes are removed.
3. **Every other large site, and an in-scope site without a usable lot: one high-capacity link** (the first version of
   this ADR, now per site): kept when the link of its facility with most activities has >= 1000 veh/h (`adequate`);
   else all its facilities and their activities move to the closest such link within
   `large_facility_access_maximum_distance_m` (1000 m) (`moved`); else kept (`fallback`).
4. **Bounds and observability.** Lots outside the network extent grown by 2 km are dropped and counted; none left
   fails the step (a file in another CRS or region). The step fails when in-scope sites without a usable lot exceed
   `large_facility_access_maximum_no_lot_share` (0.10) of the in-scope sites, or when `fallback` exceeds
   `large_facility_access_maximum_fallback_share` (0.15) of `moved` + `fallback`. Rates are logged by the Java step
   and again by the stage; `large_facility_access_summary.csv` (one row per site) and
   `large_facility_access_groups.csv` (one row per access group) are written to the prepare stage directory. The
   production stage `braunschweig.matsim.simulation.prepare` (the alias of `matsim.simulation.prepare` in every
   workflow) hashes the lots release in its `validate()`, so a rebuilt release recomputes it; the stage checks the
   file and its header before the preparation runs, and the prefix keys and switches are type-checked at configure.
5. **Departing vehicles wait for driving traffic:** the prepared config sets
   `qsim.insertingWaitingVehiclesBeforeDrivingVehicles` from `matsim_qsim_inserting_waiting_vehicles_first` (default
   false; true restores the MATSim default). A vehicle leaving a site yields to the traffic on the road.
6. Switches: `large_facility_access_enabled` (default true; off, the Java step does not run and no lots file is
   needed). `configs/base_bs.yml` sets both switches explicitly.

**First version, replaced the same day:** a per-facility-id rule (facilities of >= 1000 activities on links below
1000 veh/h move to the closest >= 1000 veh/h link within 1000 m). It missed the incommuter workplaces of a site (each
has one activity) and left a site such as the VW plant on one 1800 veh/h link; the owner asked for the distribution
over parking lots. Its parameters were measured with `scripts/measure_large_facility_access.py`, which now sizes per
site by default (`--group-by site|facility`): at 500 m about a quarter of the facilities needing a new link fell back,
at 1000 m 6.8 % (100 %) and 8.6 % (1 %); a 1500 veh/h minimum left 14-23 % without a link.

## Rejected options

- **Raise the network capacities** (600 veh/h per lane for tertiary is pt2matsim's default and at the low end of
  MATSim practice): it would address the larger pattern (through demand above capacity) but changes the whole network
  and needs a committed reference for urban lane capacities, which the repository does not have.
- **Per-activity links on one facility:** MATSim routes from the facility's link, so they do not survive routing.
- **Moving the activity location to the lot:** it would move walk, bicycle and pt trips too; the owner kept the site
  coordinate (decision 2).
- **The lot distribution for every large site:** the 0.5 simultaneous-parking assumption is workplace logic; for shops
  (customers turn over) and schools (pupils do not park) it would pull lots from up to 1500 m and create long walks
  (owner decision 4).
- **Only the insertion rule:** it moves the delay from through traffic to the departing vehicles and leaves the site
  on a link that cannot carry it. **Only the access change:** departures could still starve the traffic on the new
  links at peak.

## Consequences

- Results change by design. The car (and `car_passenger`, a network mode) access/egress walk between the site
  coordinate and its access link becomes longer at large sites (spaces-weighted site-to-lot distance at 1 %: VW about
  1080 m, other work sites 460-910 m); the Braunschweig car utility includes it (`betaAccessTime_u_min`), so car
  shares at those sites can fall. Walk, bicycle and pt trips keep their coordinates.
- New facility ids `<id>__access_<n>` appear in the outputs; analyses that join by facility id map them through
  `largeSiteParentFacilityId` or the groups file.
- Lots of other owners near a site count too (OSM records no ownership), and 86 % of the release's spaces rest on the
  area heuristic of the parking module (ASSUMPTION B-d).
- A MATSim link has one direction, so a group's persons arrive and leave by one direction of the lot's road; detours
  through the next junction remain possible (as with the original nearest-link placement).
- At 1 % a site holds few persons (VW about 412 activities), so the group shares are coarse, and site sizes scaled
  from a small sample are noisy (294 large sites at 1 % against 236 at 100 %).
- Cordon runs: eqasim's `NetworkCutter` keeps every link an activity or facility uses (also outside the extent) and
  connects it, and `FacilitiesCutter` keeps every used facility, so sub-facilities and lot links survive the cut.
- The jar must contain the eqasim-java-bs change (branch `feat/i465-large-facility-access`); the pipeline builds the
  jar from the sibling checkout (`eqasim_source_path`).

## Measured effect

Run manifest `docs/runs/i465-large-facility-access-1pct-2026-10-23.yml`: a 1 % continuation of the #465 rerun's final
plans (replanning on; control, insertion rule only, the first per-facility rule, and this decision), one draw per arm, a
simulated A/B and not a validation.

| arm (last iteration) | car leg hours | car trip hours, door to door | through traversals delayed >= 1 h | car share at the in-scope sites |
|---|---|---|---|---|
| control (32) | 11 507 | 12 247 | 171 on 13 links | 54.9 % |
| insertion rule only (32) | 11 515 | 12 253 (+0.1 %) | 205 on 12 links | 54.7 % |
| first per-facility rule + insertion (40) | 11 705 (+1.7 %) | 12 994 (+6.1 %) | 302 on 15 links | 65.2 % |
| this decision (32) | 11 099 (-3.5 %) | 12 364 (+1.0 %) | 46 on 9 links | 67.7 % |

Car leg hours (`ph_modestats`) count only the time in the car. Car trip hours (`output_trips` `trav_time` of the trips
with main mode car) run from activity to activity and so include the access and egress walks that this decision
lengthens, plus the car trips the mode shift adds (20 727 -> 21 062, +1.6 %).

- The design's criteria are met in this draw: (a) no access-group link carries more car departures than its simulated
  capacity in any hour (230 links with departures); (b) the site-caused queues are gone (Mittelstrasse 641838 / 426591 /
  426596, Guntherstrasse 129867); the 46 remaining traversals lie on links with no site departures and no access role
  (through demand above capacity); (c) car time against the control within the +2 % limit on both measures: car legs
  -3.5 %, car trips door to door +1.0 % in total and -0.6 % per trip (35.5 -> 35.2 min). All trips of all modes
  together: 18 956 -> 18 803 h (-0.8 %); the 2570 trips that start or end at an in-scope site: 3099 -> 2502 h
  (-19.3 %). Person 693334 drives
  home in 18.6 min (control 47.4 min).
- Side effects without a reference: walk hours +21.5 % (the longer access walks), car share of all trips 53.0 % ->
  53.9 %, and at the in-scope sites 54.9 % -> 67.7 % (the car no longer queues at the site exit). This shift needs a
  check against observed commute mode shares before it is relied on.
- On the rerun's prepared scenario the step distributed 39 of 39 in-scope sites (2 partly covered, 0 without a lot),
  moved 196 other large sites to a high-capacity link and kept 9 (4.4 %, bound 15 %).
