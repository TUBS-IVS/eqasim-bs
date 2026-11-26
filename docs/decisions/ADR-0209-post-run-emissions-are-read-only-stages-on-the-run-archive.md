# ADR-0209 · 2026-11-26 · Post-run emissions are read-only stages on the run archive

- **Status:** active (provisional where marked: four owner decisions are open, see "Open points")
- **Numbering:** 0209 checked free across all remote branches on 2026-11-26 (highest taken: 0208 on `main`, 0207 on a branch; 0206 is unused).
- **Issue:** #688. **Feature record:** `post_run_emissions`. **Method:** `docs/features/post-run-emissions.md`.
- **Who decided what:** D1, D2, D4 and D11 are owner decisions (Felix, 2026-11-26). D3 and D5 to D10 were set by the
  implementing session (Claude) and reported to the owner; each can be reversed by the default or table it names.

## Context

- The fleet synthesis writes HBEFA attributes on the passenger-car vehicle types (ADR-0019, ADR-0021), but nothing
  computed emissions from a run.
- MATSim's emissions contrib needs only the standard events of a finished run, HBEFA keys on every vehicle type that
  drives, an HBEFA road type on every link and four HBEFA tables.
- In the archive of a run, `heavy_truck` and the six transit types carry no HBEFA attributes, `default_car_passenger`
  carries the passenger-car average, three technology names differ from the contrib's ids, and no link has a road
  type (run manifest `emissions-wiring-1pct-2026-11-26`).
- HBEFA is licensed software. No table is on the run server; licence status and version at the institute were not
  clarified on 2026-11-26.

## Decision

- **D1. Post-run, never inside the MATSim loop.** Emissions are computed from `output_events` of the archive
  `<output_path>/matsim_output`; the simulation writes nothing extra.
- **D2. Read-only consumer with its own view.** Four synpp stages (`braunschweig.data.hbefa`,
  `braunschweig.emissions.inputs`, `.events`, `.summary`) read the archive (ADR-0101; ordering after `matsim.output`
  only, ADR-0177) and write into their own stage directories. What the tool needs is added in a copy, the
  *emissions view*; the archive stays byte-identical. No upstream stage, no Java code and no file of
  `braunschweig/synthesis/vehicles/` is changed.
- **D3. The calculation is MATSim's `AirPollutionAnalysis` from the existing pipeline jar,** called through
  `matsim.runtime.eqasim.run`. Python stages only.
- **D4. Owner assumptions** (committed in `braunschweig/emissions/vehicle_type_rules.csv`): public transport emits
  nothing, car passengers emit nothing, `heavy_truck` is the HBEFA heavy-goods-vehicle average without cold start,
  scaling is `1 / sampling_rate`.
- **D5. HBEFA tables are never downloaded, committed or copied.** Four config keys without default name the files.
  The MATSim sample tables are refused by file name and content hash; a table whose rows repeat a lookup key is
  refused.
- **D6. Road type per link: a faithful port of MATSim's `OsmHbefaMapping`** with its class table committed
  (`osm_hbefa_road_classes.csv`). PROVISIONAL: the owner has not confirmed the method.
- **D7. Every lookup is checked before the Java run,** by replaying the contrib's lookup order on the key columns
  of the tables. The chain stops when a lookup has no answer. Two fallback bounds default to 0
  (`emissions_max_road_type_fallback_length_share`, `emissions_max_detailed_fallback_type_share`), so a fallback is
  accepted on purpose or not at all.
- **D8. The computation method has no default** (`emissions_computation_method`: `StopAndGoFraction` or
  `AverageSpeed`). PROVISIONAL until the owner chooses; until then every run names its method.
- **D9. Defaults set without the owner:** lookup behaviour `tryDetailedThenTechnologyAverageThenAverageTable`;
  table consistency check `consistent`; grid size 1000 m; coordinate system `EPSG:25832`; emitting network modes
  `car`, `truck`; fallback road type `URB/Local/50`; `handleHighAverageSpeeds` false.
- **D10. Totals come from the tool's per-vehicle-type file,** the after-24:00 share is reported, nothing is scaled
  a second time, and the events stage stops when `sampling_rate` differs from the rate the archive was simulated
  with. The summary writes into its stage directory only.
- **D11. Not in the production run list.** `configs/base_bs.yml`, `scripts/verify_braunschweig_inputs.py` and the
  README are not touched; the chain is requested explicitly with its own overlay.

## Rationale

- **D1, D2:** the contrib needs only standard events (checked in the pinned jar). Fixing vehicle types upstream
  would change the hashes of the fleet and simulation stages and invalidate the cached 100 % simulation; a view
  keeps every existing cache valid and the feature free of overlap with the fleet, freight and simulation work.
- **D3:** the tool writes CSV per vehicle type, network mode, link and grid, scales by the sample size, and is
  what SimWrapper's emissions dashboard reads. It is in the jar already (`application` module).
- **D5:** a run on the sample tables looks plausible and is meaningless (their values are altered on purpose), and
  they ship inside the run jar, so a path check alone would not stop it. The tool keys a table row without
  `Year`, `Gradient` and `Case` and silently keeps the later of two rows with one key.
- **D6:** the contrib class cannot run on this network (it reads the link attribute `type`; the network carries
  `osm:way:highway`). A port keeps the method traceable to a published implementation instead of inventing a
  mapping. On the 100 % model network every drivable link was mapped, none fell back (run manifest).
- **D7:** the tool has no lookup counter and stops with an exception at the first missing key, which on a full
  archive is hours into the events. Without the pre-check a fleet could also be answered entirely by the average
  table and nobody would see it.
- **D9:** the full lookup order is needed because `default_car` and `heavy_truck` are average types. The contrib's
  default consistency level `allCombinations` cannot hold for a detailed table with more than one vehicle
  category. 100 m, the tool's own grid default, ran out of an 8 GB heap on a 1 % archive; 1000 m finished; the
  raster cannot be switched off. The coordinates of the archive are EPSG:25832 values under MATSim's placeholder
  label `Atlantis`; the view puts the real name on config, network and schedule, because the tool fails when it
  has to transform from the placeholder (found by the wiring run).
- **D10:** the tool's total, per-link and grid files drop every emission from 24:00 on; the per-vehicle-type file
  does not. The tool already multiplies by `1 / sample size` and scales by whatever rate it is given.
- **D11:** the stages stay out of `docs/registry/dag/production.json` while no real table exists and sibling
  branches edit the shared files.

## Rejected alternatives

- **An own Java runner in the `braunschweig` module:** `matsim.runtime.eqasim.validate` hashes the whole Java
  tree; a new class rebuilds the jar and invalidates the 100 % simulation (ADR-0149).
- **Re-implementing the HBEFA lookup in Python:** it would duplicate a scientific model that then needs its own
  validation. Only the ORDER of the lookup is replayed, on key columns, to predict what the tool will do.
- **eqasim's tools as the primary path** (`org.eqasim.core.components.emissions`, read in `eqasim-java-bs` at
  ae6a10a): `RunComputeEmissionsEvents` writes the contrib's emission events (`output_emissions_events.xml.gz`)
  and no table, does not scale by the sample size, counts no fallback (an unknown road class gets
  `URB/Access/30` with a log line per link) and uses the average tables alone when no detailed table is named;
  `RunExportEmissionsNetwork` writes a shapefile with the coordinate system `epsg:2154` hard-coded. What they
  offer and this chain does not is listed under "Consequences"; the events tool stays an option for a later
  per-vehicle output.
- **Downloading tables like `eqasim-org/eqasim-france` (`data/hbefa/raw.py`):** that stage fetches the sample
  tables (read on 2026-11-26, not pinned to a commit).
- **Editing `braunschweig/synthesis/vehicles/hbefa.py`** to rename the three technology names: it would change the
  fleet stage's hash; `technology_names.csv` translates them in the view.
- **A pre-check per dimension** (vehicle keys and road types separately): the tool falls back per full key and
  component, so only a joint replay predicts it.
- **Skipping quietly when the archive is missing,** as the dashboards do: the emissions stages are requested
  explicitly, so a missing archive raises.

## Consequences

- No emission value exists yet. The chain is proven to be wired on a real archive with the sample tables (run
  manifest); a result needs licensed tables.
- A new simulation under the same `output_path` re-runs the chain (archive identity in the `inputs` token). After
  an invocation that also ran the simulation, the next invocation re-runs the chain once.
- The first run on real tables will probably need the type bound of D7 raised or the fleet's technology and
  concept names aligned with the tables: whether the fleet's emission concepts (`PC P-Hybrid`, `PC PHEV petrol`,
  `PC BEV`) match real tables is UNVERIFIED.
- ASSUMPTION, unverified, set by the implementing session: `petrol (hybrid)` -> `petrol (4S)`
  (`technology_names.csv`), because the contrib has no hybrid technology id. The wiring run showed that this
  choice decides which table level answers the hybrids; real tables must confirm or replace it.
- What eqasim's tools offer and this chain does not: (a) the emission events themselves, per vehicle, link and
  time, from which any aggregation can be made later (by person, trip or hour) without the 24:00 cut-off and
  without the raster the MATSim tool always builds; (b) the contrib's own `OsmHbefaMapping` class instead of a
  port, by copying `osm:way:highway` into the link attribute `type` in memory. The two mappings differ for
  ramps: eqasim sends every `*_link` class to `unclassified` (classed by speed), the port keeps the contrib's
  own reading (`motorway_link` is a motorway). Same in both: consistency level `consistent`, the full lookup
  order, vehicle description from the engine information, abort on a vehicle without type.
- Three modules replay contrib behaviour and must be re-checked on a MATSim version change
  (`docs/codebase/notes/post-run-emissions.md`).

## Open points (owner)

1. HBEFA licence and version (the contrib documents 3.1 and 4.1; 5.x is unproven with it).
2. `heavy_truck` without cold start: the tool looks up cold factors for every non-excluded vehicle category and
   stops when none is found. If the real cold tables hold no heavy-goods-vehicle rows, the assumption needs a
   mechanism; the pre-check stops before the Java run and names the type. UNVERIFIED on real tables.
3. Confirmation of the road-type method (D6). The model network's free speeds are not legal limits everywhere, so
   on many links the speed band comes from the clamp.
4. `StopAndGoFraction` against `AverageSpeed` (D8); to be compared on real tables.

## Evidence

- Commits on `feature/i688-emissions`: e3349655, 65f817cc, efb51c91, ae15bc46, 7e81924d, ca9e53af, 1eaf4c47 and
  the two review and wiring fixes that follow them.
- Run manifest `docs/runs/emissions-wiring-1pct-2026-11-26.yml` (wiring proof on the sample tables; tool call,
  Java version, the 24:00 cut-off, grid memory, vehicle-km by vehicle type on three archives, road-type coverage
  of the 100 % network).
- Tests: `tests/test_emissions_*.py`, `tests/test_data_hbefa_stage.py`.
- Contrib behaviour: matsim-libs `main` source read on 2026-11-26; enum values and string constants compared with
  `javap` against the pinned `2026.0-2026w12` run jar. The upstream tag of that version is not on GitHub.
- Issue #688 and its comment of 2026-11-26 (wiring test and vehicle-km).
