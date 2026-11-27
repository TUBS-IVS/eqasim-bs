# Post-run road-traffic emissions (HBEFA)

Method of the feature `post_run_emissions` (issue #688). Why it is built this way and what was
rejected: [ADR-0209](../decisions/ADR-0209-post-run-emissions-are-read-only-stages-on-the-run-archive.md).
Per-stage contracts and config keys: the stage records `braunschweig.data.hbefa`,
`braunschweig.emissions.inputs`, `.events`, `.summary` under `docs/registry/stages/`.
Rules for maintainers: [`docs/codebase/notes/post-run-emissions.md`](../codebase/notes/post-run-emissions.md).

## What it computes

Emissions of the simulated road traffic of one finished MATSim run, per pollutant and per vehicle
group, from the run's events. The pollutants are the components the HBEFA tables hold; in the
HBEFA 4.1 layout these are 23 warm components (CO2, NOx, PM, fuel consumption and others, with
three non-exhaust components: `PM_non_exhaust`, `PM2_5_non_exhaust`, `BC_non_exhaust`) and 18 of
them for cold starts. The emission factors come from HBEFA (Handbook Emission
Factors for Road Transport); the calculation is done by MATSim's emissions contrib through its
tool `org.matsim.application.analysis.emissions.AirPollutionAnalysis`. The pipeline adds what the
tool needs and does not have: HBEFA keys on the vehicle types, an HBEFA road type on every link,
a check of every lookup before the hours-long run, and a summary that says how much of the
driving was answered by which table level.

Every output is **model output**. Nothing is compared to an observed or published emission
figure, because none is in the repository.

## Chain

```
braunschweig.data.hbefa -> braunschweig.emissions.inputs -> braunschweig.emissions.events -> braunschweig.emissions.summary
```

The stages read the run archive `<output_path>/matsim_output` and write only into their own stage
directories. They are in no run list; request `braunschweig.emissions.summary` with the
configuration of the archived run plus the four table paths and the computation method.

## Vehicle types

Committed tables decide, per vehicle type of the archive, what the tool gets
(`braunschweig/emissions/vehicle_type_rules.csv`, `technology_names.csv`):

| Vehicle type | Treatment | Basis |
|---|---|---|
| fleet types `car_*` | own technology, size class and emission concept; three technology names translated to the contrib's ids | fleet synthesis (ADR-0019, ADR-0021) |
| `default_car` | HBEFA passenger-car average | as written by the vehicle stage |
| `heavy_truck` | HBEFA heavy-goods-vehicle average, no cold start | ASSUMPTION, owner decision 2026-11-26 |
| `default_car_passenger` | emits nothing | ASSUMPTION, owner decision 2026-11-26 |
| transit types (Bus, Rail, Tram, ...) | emit nothing | ASSUMPTION, owner decision 2026-11-26; public transport is not simulated on the network |

Any other type raises. One translation is unverified: `petrol (hybrid)` -> `petrol (4S)`
(the contrib has no hybrid technology id; status `ASSUMPTION_unverified` in the table).

## Road types

HBEFA factors depend on a road type per link (for example `URB/Local/50`). The method is a
faithful port of MATSim's `OsmHbefaMapping`: OSM highway class plus speed give the HBEFA class and
speed band (`braunschweig/emissions/osm_hbefa_road_classes.csv`). Known properties of that
mapping, kept on purpose and documented in `braunschweig/emissions/road_types.py`: every link is
urban (`URB/`), every motorway is `MW-Nat.`, and the speed is clamped into the class range.

The port was compared with the original link by link on the network of a 100 % run and gives the
same road type on every link a car or truck may use (run manifest
`emissions-road-type-parity-2026-11-26`). That holds for one version of the original, so the
calculation stage refuses a pipeline jar whose mapping classes differ from the proven ones
(`braunschweig/emissions/road_type_proof.py`). Equal to the original does not mean right for the
road: the properties above remain.

LIMITATION: the model network's free speeds are not legal speed limits everywhere, so the speed
band comes from the clamp on many links. How this interacts with the computation method
`StopAndGoFraction` is untested.

## Pre-check and fallback

The tool has no lookup counter and stops at the first key it cannot find. The input stage
therefore replays the tool's lookup order on the key columns of the tables, per vehicle type, road
type and pollutant component: detailed table, technology average, average table. It records which
level answers each type and stops before the Java run when

- a lookup has no answer at all,
- more drivable link length than allowed has no HBEFA road class, or
- more fleet types than allowed are answered below the detailed table.

Both bounds default to 0: a fallback has to be accepted on purpose. After the run the summary
reports the same fallback weighted by vehicle-km.

## Outputs and units

| File (summary stage) | Content |
|---|---|
| `emissions_by_pollutant.csv` | `total_kg` over the whole simulated time, `before_24h_kg`, `after_24h_share`, `gram_per_vehicle_km` |
| `emissions_by_vehicle_group.csv` | kg per pollutant and vehicle group, with the group's scaled vehicle-km |
| `emissions_summary_report.json` | both tables, archive, sha256 and `Case` of each HBEFA table, settings, sample size, fallback share, excluded types |

- **Scaling:** the tool multiplies by `1 / sample size`; nothing is scaled again. The events
  stage refuses a `sampling_rate` that differs from the rate the archive was simulated with.
- **After 24:00:** the tool's total, per-link and grid files drop every emission from 24:00 on;
  its per-vehicle-type file does not. Totals are taken from the per-vehicle-type file and the
  difference is reported as the after-24:00 share.
- **Vehicle-km:** full link length per `entered link` event, per vehicle type; sample values,
  scaled only for grams per vehicle-km.

## What is not covered

Public transport, trips within one link, any traffic that is not in the simulation, and road
gradient (the tool ignores the tables' `Gradient` column). Cold-start emissions of heavy trucks are excluded by assumption; how that is
implemented depends on the real tables (ADR-0209, open points).
