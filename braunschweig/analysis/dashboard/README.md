# Braunschweig Simulation Dashboard

Self-contained HTML dashboard that compares eqasim/MATSim outputs against the
MiD 2023 Braunschweig reference values, with built-in version comparison.

## Build / refresh

After every simulation run, register it:

```powershell
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"; conda activate eqasim
python -m braunschweig.analysis.dashboard.build_dashboard `
    --output-dir eqasim-data/output_bs_25pct `
    --sim-cache  eqasim-data/cache_bs_25pct `
    --label "25pct_baseline"
```

Then open [`braunschweig/analysis/dashboard/index.html`](index.html) directly
in a browser — no web-server needed.

## Compare versions

* Click any run in the sidebar to focus it.
* `Shift`-click (or `Cmd`/`Ctrl`-click) a second/third run to overlay them.
* Each run has a stable color across all charts and KPI cards.

## What is computed?

| Section | Source | Reference |
| --- | --- | --- |
| Persons / Households / Trips | `output_bs_*/braunschweig_*_persons.csv`, `..._trips.csv` | — |
| Beschäftigt / Führerschein / ÖV-Abo | `..._persons.csv` | MiD P9 / P17 / P24.1 |
| Mode share (final + evolution) | `simulation_output/modestats.csv` | MiD P12_1 (work) |
| Trip distance bands | `simulation_output/eqasim_trips.csv` | MiD P13 ZGB |
| Pendelweg ⌀ (km) | `eqasim_trips` `following_purpose=='work'` | MiD P13 = 20.7 km |
| Distanz-EMD vs MiD | computed | Quality-Schwelle 0.08 |
| Score / Distanz-Verlauf | `scorestats.csv`, `traveldistancestats.csv` | — |
| Per-Kreis MiD-Referenz-Tabelle | `mid2023_P12_1.csv`, `mid2023_P13.csv` | — |

## Run storage

Each run is stored under `runs/<timestamp>_<label>/metrics.json`. The
`index.html` is regenerated with **all** runs embedded, so you can ship it as
a single file or commit it.

## Re-render only

If you edit `metrics.json` files manually or want to re-build the HTML without
adding a new run:

```powershell
python -m braunschweig.analysis.dashboard.build_dashboard --rebuild-only
```
