# ADR-0165 · 2026-10-23 · Large facilities get a high-capacity access link, and departing vehicles wait for driving traffic

- **Status:** accepted (owner decision 2026-10-23: implement both parts). Implemented; measured on the 1 % rerun
  of #465 (run manifest `docs/runs/i465-large-facility-access-1pct-2026-10-23.yml`), a simulated A/B, not a
  validation; see "Measured effect".
- **Numbering:** ADR-0165; 0153 to 0162 are on main or open branches, 0163 and 0164 are claimed by the sibling
  #465 session `in-commuter-car-trips` (shared session notes); re-checked across all local and remote branches and
  worktrees on 2026-10-23.
- **Issue:** #465 (road side of the stuck person 693334). **Relates to:** ADR-0144 (the same capacity-aware link
  rule for freight boundary endpoints), ADR-0161 (the transit side of the same stuck).

## Context

- In the 1 % rerun of #465 (felix `~/wt-i487-rerun/output_rerun/matsim_output`, eqasim-bs `be886cc6`,
  eqasim-java-bs `ad3f7e22`, final iteration 49) person 693334's car trip home took 3 h 21 min instead of the
  routed 19 min 42 s, 2 h 35 min of it on link 129867 (Guntherstrasse, 28.5 m, tertiary, 600 veh/h), and then
  missed the last tram (the stuck itself is fixed by ADR-0161).
- **The network conversion is not the cause.** The OSM way is two-way with `lanes=2` (both directions), which
  pt2matsim turns into one lane per direction; 600 veh/h per lane is pt2matsim's `CreateDefaultOsmConfig`
  default for `tertiary` (the stage `matsim.scenario.supply.osm` uses that default config unchanged).
- **Facility placement is.** eqasim's `FacilityPlacement` (with `OSMFacilityPlacementVoter`) attaches every
  facility to its ONE nearest car link that is not a motorway, trunk or ramp, whatever the link's capacity, and
  `LinkAssignment` copies that link to every activity. All car departures of a facility therefore enter the
  network on that one link. Facility `work_97721` (44 work activities at 1 %) sits on link 129867.
- **MATSim's insertion priority makes it worse.** The QSim default `insertingWaitingVehiclesBeforeDrivingVehicles
  = true` (MATSim 2026.0, read from the jar; not set by this project) moves departing vehicles onto a link before
  the vehicles already driving on it, so departures take the flow slots first. From about 14:30 to 19:00 a car
  from `work_97721` entered link 129867 every 600 s, its whole capacity at `flowCapacityFactor` 0.01.
- **Measured pattern** (scripts on felix `~/i465-link`, findings note in the #465 session notes;
  delay = time on link minus free-flow time):

  | run | through traversals delayed >= 1 h | of them on or upstream of a facility-saturated link |
  |---|---|---|
  | 1 % rerun, final iteration | 138 on 14 links (126 persons) | 25 (5 links, departures 87-99 % of capacity) |
  | 100 % run `prod100_20260925`, final iteration | 28 300 on 20 links | 4 links directly (departures 70-110 %), plus the Mittelstrasse queue upstream of one of them |

  The rest are through demand above the link capacity, mostly 600 veh/h links and motorways; this ADR does not
  change capacities (see "Rejected options").
- **Large facilities on small links are common.** In the 100 % run 300 facilities have at least 1000 activities;
  206 of them sit on links of at most 600 veh/h. The largest non-portal facility, `work_101999` (34 303
  activities; coordinates at the VW Wolfsburg plant site, an inference from the coordinates only), sits on one
  600 veh/h tertiary link (Mittelstrasse); its departures used 110 % of that link's capacity and through traffic
  waited up to 9.4 h.
- **One-iteration replay of the insertion rule** (same final plans, only the rule changed): with `false`,
  693334's leg takes 41.9 min instead of 201.3 min, the five facility-saturated links leave the >= 1 h list, and
  the insertion wait grows from 768 to 868 vehicle-hours: the delay moves from through traffic to the departing
  vehicles, total car and truck travel time changes by +0.1 %.

## Decision

1. **Capacity-aware access link for large facilities** (eqasim-java-bs `RunAdaptLargeFacilityAccess`,
   `LargeFacilityAccessAssignment`, `LargeFacilityAccessLinkSelector`), run by `matsim.simulation.prepare`
   between eqasim's `RunPreparation` and the population routing, on the prepared facilities and population:
   - size = activities at the facility in the selected plans divided by `sampling_rate` (stated for 100 %);
   - a facility of at least `large_facility_access_minimum_activities` (default 1000) whose link has less than
     `large_facility_access_minimum_capacity_veh_per_h` (default 1000) moves to the closest eligible link within
     `large_facility_access_maximum_distance_m` (default 1000 m): eligible = carries `car`, has the minimum
     capacity (veh/h via the network capacity period), and passes the class filter of eqasim's placement voter
     (no motorway, trunk or `*_link`); distance is perpendicular to the segment; ties go to the lower link id;
   - its activities take the new link and the routes of adjacent legs are removed (the population is routed
     afterwards);
   - facilities whose id starts with `large_facility_access_excluded_prefixes` (default `outside_`, the cordon
     portals, which the portal stages place on purpose) are never moved;
   - no eligible link within the distance = fallback (the facility keeps its link): counted, logged as a rate,
     listed per facility in `large_facility_access_summary.csv` in the prepare stage directory, and the step
     fails when the fallback share among the facilities that needed a new link exceeds
     `large_facility_access_maximum_fallback_share` (default 0.15).
   - Switch: `large_facility_access_enabled` (default true). Off, the step does not run.
2. **Departing vehicles wait for driving traffic:** `matsim.simulation.prepare` sets
   `qsim.insertingWaitingVehiclesBeforeDrivingVehicles` in the prepared config from the declared key
   `matsim_qsim_inserting_waiting_vehicles_first` (default false; true restores the MATSim default). A vehicle
   leaving a site yields to the traffic on the road, as at a property exit.

**Parameter choice** (`scripts/measure_large_facility_access.py`, an independent re-implementation of the rule
that doubles as its cross-check; sweep of size 500/1000/2000/5000, capacity 1000/1500 veh/h, distance
200/300/500/1000 m, on the 1 % rerun and the 100 % run): at 500 m about a quarter of the facilities that need a
new link fall back, among them `work_101999` (no link of 1000 veh/h within 500 m of its coordinate); at 1000 m
the fallback is 6.8 % (100 %) and 8.6 % (1 %), with a median move of about 245 m. A 1500 veh/h minimum leaves
14-23 % without a link. Hence 1000 activities / 1000 veh/h / 1000 m, and a fallback bound of 0.15 that the
measured shares stay below with margin. In the pipeline step on the 1 % rerun's plans the Java rule moved 603 of
662 facilities that needed a new link and kept 59 (8.9 %), consistent with the script (it counts activities from
`output_activities.csv`, the Java step from the selected plans, so the facility sets differ slightly). These
values are engineering choices measured on this network, not calibrated against observed data.

## Rejected options

- **Raise the network capacities** (tertiary 600 veh/h per lane is pt2matsim's default and at the low end of
  MATSim practice): it would address the larger pattern (through demand above capacity) but changes the whole
  network and needs a committed reference for urban lane capacities, which the repository does not have. Not
  part of this decision.
- **Several access links per facility** (split a large site into gates): MATSim allows one link per facility,
  and the routers use the facility's link, so this needs facility splitting with new ids in the population.
  Kept as the next step if a single high-capacity link is not enough (see "Consequences").
- **Only the insertion rule:** it moves the delay from through traffic to the departing vehicles and leaves the
  site on a link that cannot carry it.
- **Only the access link:** without the insertion rule, departures from a moved site can still starve the
  traffic on the new link at peak.

## Consequences

- Results change by design: activities of the moved facilities start and end on other links; the walk between
  the facility coordinate and the link (routing `accessEgressType` `accessEgressModeToLink`) gets longer, by
  about 245 m at the median, at most 1000 m. The set of moved facilities depends on the sample (at 1 % one
  activity stands for 100), a stated ASSUMPTION of the size proxy.
- `work_101999` moves to a 1800 veh/h link 549 m away; with about 34 800 activities it may still exceed a single
  link at shift changes. The summary file lists every large facility with its old and new link and capacity.
- The prepare stage now runs one more Java step (seconds at 1 %) and writes `large_facility_access_summary.csv`.
- The jar must contain the eqasim-java-bs change (branch `feat/i465-large-facility-access`); the pipeline builds
  the jar from the sibling checkout (`eqasim_source_path`).
