## braunschweig.analysis.simwrapper

Exports a self-contained SimWrapper dashboard folder (CSV data + YAML tab
definitions) for one eqasim/Braunschweig run.

### Purpose

The dashboard is assembled from the run record produced by
`braunschweig.analysis.dashboard.build_dashboard.assemble_run_record`.
No scientific logic is duplicated here; this module only reshapes that
existing data structure into the file layout expected by simwrapper.app.

### Regenerate command

```powershell
python -m braunschweig.analysis.simwrapper.export `
    --output-dir <out>   `
    --sim-cache  <cache> `
    --label      <label>
```

`--output-dir` and `--sim-cache` are required; `--label` is optional and
defaults to the output-directory name. `<out>` is the eqasim CSV output
directory; `<cache>` is the synpp cache folder containing
`matsim.simulation.run__*.cache/simulation_output/`. The dashboard is
written into `<out>/simwrapper/`.

### Dashboard tabs

1. Overview      -- headline KPI tiles + full KPI table
2. Mode share    -- final all-trip shares, commute vs MiD P12_1, evolution
3. Distances     -- commute distribution vs MiD P13, mean km by mode
4. Time of day   -- hourly trip counts by mode and purpose
5. Convergence   -- MATSim score and distance evolution across iterations
6. Per-Kreis     -- per-Kreis table and mean-commute bar chart
7. OD            -- origin-destination flow table (+ aggregate-od spider when VG250 geometry is available)
8. Quality       -- checks vs MiD reference values (EMD, mean commute deviation)

### Layer-1 MATSim dashboards

The network-volume, link-level, and agent-level dashboards produced by
the Java simwrapper contrib (registered in `org.eqasim.braunschweig.RunSimulation`
behind the `--simwrapper` flag) live in
`<cache>/matsim.simulation.run__*.cache/simulation_output/` and are opened
separately.

### Opening the dashboard

Open `<output-dir>/simwrapper/` via "View local files" in simwrapper.app
(https://simwrapper.github.io) or a locally running SimWrapper server.
