# popsim_mid workflow (PopulationSim + MiD)

`population.method = popsim_mid` selects the PopulationSim producer
(`braunschweig.popsim.stage`, resolved by `braunschweig.population.selector`). It
synthesises households at the Zensus 100 m grid from the prepared cell controls
and a restricted MiD 2023 seed, running PopulationSim in its own `uv` environment
as a subprocess.

## Status

- **Validated end-to-end** on real data (`scripts/popsim_mid_smoke.py`): prepared
  cells → control totals → complete-household MiD seed → PopulationSim → merged
  expanded households (e.g. 2 1 km parents / 188 cells → 28 346 households).
- The stage (`braunschweig.popsim.stage`) runs the full chain
  (filter ZGB → batch by 1 km → PopulationSim per batch → cell-disjoint merge) and
  returns the merged expanded-household table (one row per synthetic household,
  located by 100 m cell).
- **Remaining layer (not yet wired into `matsim.output`):** expanding these donor
  households into the full eqasim persons schema — persons + attributes (from the
  MiD donor) + home locations (via `braunschweig.popsim.handoff` cell→building) +
  activity chains, harmonised to the schema contract
  (`braunschweig.population.schema`). Until that exists, `popsim_mid` produces the
  cell-located households but does not yet replace `data.census.filtered` in the
  DAG. `simple_ipf_open` remains the default and is unchanged.

## Environment

PopulationSim runs in the popsimprep `uv` env (Python 3.11). Set it up once:

```powershell
uv sync --python 3.11   # in the popsimprep repo; --python 3.11 is required
                        # (numpy 1.26.4 has no cp313 wheel; default py3.13 fails)
```

The expand_households step needs a CONSISTENT seed (every household has its
persons); the `kernwo` complete-household filter (`braunschweig.popsim.seed`,
default `day_filter_values=(1,2,3)`) guarantees this. Without it PopulationSim
crashes with a float `group_id` in `expand_households` — that is a seed-consistency
issue, not a PopulationSim/pandas bug.

## Configuration keys

All under `braunschweig.population.popsim.*` (paths default to the canonical
local-only layout in `DATA_LAYOUT.md` and the committed popsimprep config):

| Key | Meaning |
|---|---|
| `cells_100m_path` | prepared 100 m cell parquet (`.../popsim/cells/zensus2022_grid_100m_de_prepared.parquet`) |
| `mid_raw_path` | MiD 2023 raw dir (`.../popsim/mid2023_raw/`) — RESTRICTED, local-only |
| `controls_path` | the control spec (`popsimprep/popsim/configs/_prep3_controls.csv`) |
| `settings_path` / `logging_path` | the committed PopulationSim `settings.yaml` / `logging.yaml` |
| `popsimprep_dir` | the popsimprep repo (cwd for `uv run populationsim`) |
| `uv_path` | the `uv` executable |
| `max_cells` | batch size (100 m cells per PopulationSim run; default 3000) |
| `num_workers` | parallel PopulationSim subprocesses (default 3) |
| `work_dir` | where the per-batch folders are written |

`braunschweig.political_prefix` (the eight ZGB Kreis ARS-5 codes) restricts the
national grid to ZGB. The MiD path is required ONLY for `popsim_mid` (validated by
`braunschweig.population.config`); the open workflows never read it.

## Run the stage standalone

```powershell
python -m synpp <config-with-population.method=popsim_mid>.yml   # run target: braunschweig.popsim.stage
```
