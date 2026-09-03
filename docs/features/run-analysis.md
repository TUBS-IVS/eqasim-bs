# Run analysis (post-simulation)

### MATSim simulation output archive

The `matsim.output` synpp stage mirrors the MATSim run's `simulation_output/`
(events, plans, `ITERS/`, config, logfile) from the synpp hash-cache directory
into a stable, run-named `<output_path>/matsim_output/`, so the archive
survives a synpp cache-dir wipe (issue #156). Controlled by the
`archive_matsim_output` config flag (default **true**); set it to `false` to
skip archiving and leave `output_path` unchanged from today. Each file is
hardlinked where possible (zero extra disk on the same volume) and copied as a
fallback (e.g. cross-volume); the run log reports the primary(hardlink)-vs-
fallback(copy) rate explicitly, with a warning if the hardlink rate is 0%. A
provenance file `<output_path>/matsim_output/ARCHIVE_INFO.json` records the
source hash directory, file/hardlink/copy counts and a UTC creation timestamp,
mirroring the `documentation.meta_output` `*meta.json` pattern.

The validation notebook `braunschweig/analysis/validation_mid2023.ipynb`
has a runnable counterpart that produces every table, figure and
`report.json` for one eqasim run output directory:

```powershell
python -m braunschweig.analysis.run_mid_validation `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --label "25pct_parking"
```

Outputs land in `<output-dir>/analysis/mid_validation/`:

- `report.json` — headline KPIs (persons, trips, license/employment by
  Kreis, mean commute km vs MiD P13).
- `summary.md` — Markdown digest with three reference-comparison tables.
- `commute_bands_vs_p13.csv`, `commute_mean_vs_p13.csv`,
  `license_vs_p17_1.csv`, `employment_vs_p9.csv`,
  `secondary_success.csv`, `persons_with_kreis.csv` — intermediate
  long-form tables for downstream comparison scripts (e.g. parking-on
  vs. no-parking).
- `01_demographics.png` … `07_employment_rate.png` — figures.

Combined dashboard + MiD validation in one call:

```powershell
python -m braunschweig.analysis.run_full_analysis `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --sim-cache  eqasim-data/cache_bs_25pct_parking `
    --label      "25pct_parking"
```

Tests: `tests/test_run_mid_validation.py` covers the helpers
(`band_share`, `_bool_share`, markdown rendering, CLI parser).

### SimWrapper dashboards

The run analytics can additionally be exported as a self-contained
**SimWrapper** dashboard project (https://simwrapper.app), so the whole
dashboard is viewable inside the MATSim/SimWrapper ecosystem. There are two
complementary, flag-gated layers:

**Layer 1 - MATSim simwrapper contrib (Java).** Merged and active (ADR-0074,
`eqasim-java-bs#12`; re-verified against the fork's `main` on 2026-08-17): the
`braunschweig` module's `pom.xml` declares the `simwrapper` contrib and
`org.eqasim.braunschweig.RunSimulation` imports `SimWrapperModule`, exposes a
`simwrapper` command-line option and registers the module when that option is
true. The Python side (`matsim.simulation.run`) reads the config key
`simwrapper_dashboards` (**default `True`** per the feature-flag policy for an
analysis-only module, ADR-0074) and appends `--simwrapper true` when it is set;
`RunSimulation` then writes standard dashboards (network volumes, mode share,
trips/legs) as `dashboard-*.yaml` into `simulation_output/`. Setting the key
`false` omits the option entirely, so the Java side falls back to its own
`false` default and the run's output directory stays byte-identical.

> An earlier revision of this paragraph recorded Layer 1 as absent from the
> fork and the flag as defaulting to `False`. Both statements predated ADR-0074
> and were wrong on 2026-08-17; see issue #253. `tests/test_simwrapper_dashboards_default.py`
> now pins the default from both the shipped `configure()` and
> `braunschweig.documentation.checks.CODE_DEFAULT_TRUE`.

**Layer 2 - Python emitter (`braunschweig.analysis.simwrapper`).** Converts the
existing `record` dict from
`braunschweig.analysis.dashboard.build_dashboard.assemble_run_record` (the same
metrics that drive the interactive HTML dashboard - **no scientific logic is
duplicated**) into SimWrapper-native CSV + `dashboard-*.yaml` written to
`<output_dir>/simwrapper/`. It rebuilds the full HTML dashboard as 8 tabs:
Overview (KPI tiles), Mode share (final / commute-vs-MiD P12_1 / iteration
evolution), Distances (commute distribution vs MiD P13 + mean km by mode),
Time of day, Convergence (score + distance evolution), Per-Kreis (table + bar),
OD (matrix table + a real **aggregate-od spider** built from the 8 ZGB Kreis
zones, VG250 EPSG:25832, written as `zones.shp`), and Quality (EMD vs MiD).
Tabs whose source data is absent are skipped with an explicit log line (no
silent fallback). Regenerate standalone:

```powershell
python -m braunschweig.analysis.simwrapper.export `
    --output-dir eqasim-data/output_bs_25pct `
    --sim-cache  eqasim-data/cache_bs_25pct `
    --label      "25pct"
```

It also runs **default-on** inside `run_full_analysis` (disable with
`--no-simwrapper`; it is read-only and writes only into the new `simwrapper/`
subfolder). Open `<output_dir>/simwrapper/` via "View local files" in
simwrapper.app; the Layer-1 MATSim dashboards open from `simulation_output/`.

**Spatial / fleet map tabs (`braunschweig.analysis.simwrapper.spatial_export`).**
On top of the 8 chart/table tabs, four interactive **map** tabs are emitted from
the per-agent geodata (reusing
`braunschweig.analysis.population_validation.population_source.load_population`
and `braunschweig.analysis.spatial` for the VG250 Kreis polygons -- no geo logic
is duplicated): **Fleet** (per-vehicle `xytime` point clouds coloured by engine
power and by BEV status, a per-Kreis BEV-share / mean-power **choropleth** on the
VG250 GeoJSON, and a brand-mix or powertrain-mix bar -- "where are the VW / the
E-vehicles"); **Spatial demand** (`hexagons` density of trip origins &
destinations from `eqasim_trips.csv`); **Socio** (`xytime` home points coloured by
`household_income_eur`); **Behaviour** (`sankey` purpose->mode + a `scatter` of the
per-Kreis car share Sim vs MiD P12). All coordinates are EPSG:25832 for the point
plugins; the choropleth GeoJSON is reprojected to EPSG:4326. Each tab is
**skipped with an explicit log line** when its source columns/files are absent
(e.g. the rich fleet exists only in the all-features run; `eqasim_trips.csv` only
when MATSim has run) -- no silent skips. BEV is identified by the verified real
`powertrain == "bev"` value.

**Commuter (Pendler) tab.** `braunschweig.analysis.simwrapper.commuters` +
`spatial_export.emit_commuters` add an in-/out-/internal-commuter analysis per
Kreis: `commuter_balance` (Einpendler / Auspendler / Binnen / netto, plus the
cross-cordon `einpendler_extern` from the OD "external" zone), `top_relations`
(Kreis->Kreis flows), a per-Kreis **net-balance choropleth**
(`kreis_commuters.geojson`) and an in/out/internal bar. It works in **both
modes**: the work Kreis x Kreis matrix comes from the MATSim realised work OD
(`record["matsim"]["od_matrix"]["work"]`) when MATSim has run, otherwise from
the **synthesis** home->work assignment (`*commutes.gpkg`, classified to Kreise
via VG250) -- the active source is named in the tab title so the two are never
conflated. (`einpendler_extern` is 0 for the synthesis population, which lives
entirely inside ZGB; cross-cordon Einpendler are a separate injection.)

**Automatic pipeline stage + two modes.** `braunschweig.analysis.simwrapper_export`
is a synpp stage that writes `<output_path>/simwrapper/` on **every** run (add it
to a config's `run:` list). It always depends on `synthesis.output`; when
`simwrapper_include_matsim: true` it reads the MATSim outputs from the
`<output_path>/matsim_output` archive written by `matsim.output`
(config-derived, never a `matsim.simulation.run` stage edge — ADR-0101 /
issue #354, so an analysis-only invocation never recomputes the simulation).
Thus: a **synthesis-only** run writes all synthesis tabs (fleet, socio,
commuters-from-synthesis, ...) and the MATSim tabs skip with a log — as they
do, loudly, when the flag is on but the archive is absent; a **full**
run additionally writes all MATSim tabs. Flag-gated by `simwrapper_export_enabled`
(default true); it only adds the `simwrapper/` subfolder, so existing run outputs
stay byte-identical. The CLI / stage share one entry point
`braunschweig.analysis.simwrapper.export.export_all(output_dir, sim_cache=None, ...)`
(`sim_cache=None` => synthesis-only).

**Performance.** Raw `xytime` point clouds are down-sampled to `MAX_XYT_POINTS`
(default 150 000) with a fixed seed and an explicit log line (no silent
truncation); aggregate maps (choropleths, hexagon density, commuter balance) use
the full data. The Kreis key normalisation is vectorised. A 1% sample run is the
intended fast end-to-end test vehicle (a fresh 1% pipeline run writes the full
`synthesis.output` the export consumes).

Tests: `tests/test_simwrapper_writers.py`,
`tests/test_simwrapper_export.py` (synthetic `record` fixture per tab + a
real-VG250 OD-spider test exercising the primary geometry path),
`tests/test_simwrapper_spatial.py` (card helpers + the pure
`_trips_xy`/`_purpose_to_mode`/`fleet_by_kreis`/economic-status-ordinal logic +
the commuter integration), `tests/test_simwrapper_commuters.py` (commuter matrix
+ balance + top-relations) and `tests/test_simwrapper_stage.py` (the synpp stage
configure/execute in both modes).
