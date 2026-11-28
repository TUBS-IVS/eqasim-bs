# Post-run emissions: package layout and maintainer rules

Package `braunschweig/emissions/` plus the data stage `braunschweig/data/hbefa.py` (issue #688).
Method: [`docs/features/post-run-emissions.md`](../../features/post-run-emissions.md). Decisions:
ADR-0209.

## Layout

| Module | Role | Reads / writes |
|---|---|---|
| `braunschweig/data/hbefa.py` | synpp stage: the four tables, checked and indexed | reads the configured tables |
| `inputs.py`, `events.py`, `summary.py` | synpp stages of the chain | own stage directory only |
| `vehicle_types.py` + `vehicle_type_rules.csv`, `technology_names.csv` | vehicle type -> HBEFA keys | pure |
| `road_types.py` + `osm_hbefa_road_classes.csv` | link -> HBEFA road type (`RoadTypeTagger`) | pure |
| `road_type_proof.py` + `osm_hbefa_parity_proof.json`, `osm_hbefa_parity_cases.csv` | ties the port to the proven contrib classes (`require_proven_jar`) | reads the pipeline jar |
| `view.py` | writes the folder the tool loads (`build_emissions_view`) | reads the archive, writes the view |
| `hbefa_tables.py` | file checks, refusal of the sample tables (`inspect_hbefa_tables`) | reads the tables |
| `table_keys.py` | key index and lookup replay (`read_table_keys`, `precheck_lookups`) | reads key columns only |
| `totals.py`, `vehicle_km.py` | aggregation, vehicle-km from the events | pure / streams the events |

Scientific content (which type emits what, which OSM class is which road class) lives in the three
committed CSV tables, not in Python. The stages add them to their `validate()` token through
`COMMITTED_TABLE_FILES`.

## Rules

1. **Read-only on the archive and on upstream stages.** Nothing here may write below
   `<output_path>` or change the fleet, freight, network or simulation stages. Vehicle types are
   fixed in the view, never upstream. `braunschweig/synthesis/vehicles/hbefa.py` is not edited for
   emissions; a technology name the contrib does not know gets a row in `technology_names.csv`.
2. **No Java code.** A new class in the `braunschweig` Java module rebuilds the jar and invalidates
   the cached simulation (ADR-0149).
3. **The sample tables stay refused.** `hbefa_tables.SAMPLE_TABLE_SHA256` and
   `SAMPLE_NAME_PREFIX` are switched off only in a driver script of a wiring run, never in pipeline
   code or in a test of the stage chain. No emission value computed with them is written into
   the repository, an issue or a report.
4. **Three modules replay contrib behaviour and must be re-checked on every MATSim version
   change:** `road_types.py` (`org.matsim.contrib.emissions.OsmHbefaMapping`), `table_keys.py`
   (`HbefaTables`, `EmissionUtils`, `WarmEmissionAnalysisModule.getEf`,
   `ColdEmissionAnalysisModule.getEmissionsFactor`) and `hbefa_tables.py` (the sample-table
   hashes). They were checked against matsim-libs `main` and, for enum values and string constants,
   with `javap` against the pinned `2026.0-2026w12` jar on 2026-11-26. Also re-check the three
   tool facts the summary relies on: outputs already scaled by `1 / sample size`, the 24:00
   cut-off of the total, per-link and grid files, and the file and column names.
5. **Line patterns, not an XML parser.** `view.py` and `vehicle_km.py` read MATSim's files line
   by line, so that the view stays byte-identical outside the edited lines and the events stream.
   They rely on the attribute order MATSim writes; `vehicle_km_by_type` raises when it reads no
   vehicle or no link, and the view tests pin the layout.
   For `road_types.py` the re-check is enforced: `events.py` refuses a jar whose
   `OsmHbefaMapping`, `OsmHbefaMapping$Hbefa` or `NetworkUtils` class file is not in
   `osm_hbefa_parity_proof.json`. To admit a new jar, repeat the run of manifest
   `emissions-road-type-parity-2026-11-26` against it (its artifacts are the scripts), replace
   `osm_hbefa_parity_cases.csv` by the new cases, make
   `test_the_port_reproduces_every_case_of_the_parity_proof` pass, and only then add a proof
   record with the new digests. Never add digests without the run, and never edit the cases by
   hand: they are output of the Java original.
6. **Every fallback is counted and bounded** (road class per link, table level per fleet type,
   the same by vehicle-km). A new fallback gets its primary-against-fallback log line, a report
   field and a bound, like the existing three.
