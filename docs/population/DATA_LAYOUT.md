# Population-generation data layout (three workflows)

Canonical, documented layout for the data consumed by the three population
workflows selected via `population.method`
(`simple_ipf_open` / `popsim_open` / `popsim_mid`). This file is **tracked**; the
data files it describes are **not** (they live under the gitignored
`eqasim-data/` tree). It mirrors the discipline of
[`eqasim-data/DOWNLOAD_CHECKLIST_BS.md`](../../eqasim-data/DOWNLOAD_CHECKLIST_BS.md).

## Data-protection policy (unchanged, restated)

`eqasim-data/*` is gitignored; only small **derived MiD aggregate tables**
(`mid/mid2023_*.csv`) and the project's own calibration outputs are whitelisted.
**Raw MiD 2023 microdata and the large Zensus cell parquets are NEVER committed.**
The `popsim_open` workflow must run without any raw-MiD path; only `popsim_mid`
reads raw MiD, and only when explicitly selected.

## Canonical locations

All paths are relative to `eqasim-data/data/braunschweig/`. The PopulationSim
workflows get a dedicated `popsim/` home so the data tree stays übersichtlich:

```
eqasim-data/data/braunschweig/
  popsim/                         <-- all NEW PopulationSim-workflow inputs (local-only)
    cells/                        <-- preprocessed Zensus 2022 INSPIRE grid (open data, but large)
      zensus2022_grid_1km_de_binned.parquet
      zensus2022_grid_100m_de_prepared.parquet   <-- complete: binned + gender backfill + _agg age bands + RegioStaR
    mid2023_raw/                  <-- RESTRICTED MiD 2023 scientific-use microdata (popsim_mid only)
      MiD2023_Haushalte.csv
      MiD2023_Personen.csv
      MiD2023_Wege.csv
      (other MiD2023_*.csv as needed)
    buildings/                    <-- building stock tagged with 100 m cell + has_home (handoff)
      buildings_with_households_zgb.gpkg
    seed_open/                    <-- open seed for popsim_open (e.g. ENTD-derived); see below
  mid/                            <-- EXISTING committed derived MiD aggregates (unchanged)
    mid2023_*.csv
```

`popsim_open` reuses the existing open **ENTD 2008** donor already in the repo
(`eqasim-data/data/entd_2008/…`, checklist A3) as its PopulationSim seed +
activity source; `popsim/seed_open/` holds only any derived/adapted seed artefacts
built from it (documented + reproducible, never restricted).

## Naming convention

- Clean `snake_case`, source + content + scope encoded in the name; no ad-hoc
  suffix chains.
- Keep **original MiD filenames** (`MiD2023_Haushalte.csv` …) for traceability to
  the BMDV scientific-use package — the cleanliness comes from the dedicated,
  clearly-named restricted `mid2023_raw/` directory, not from renaming MiD files.
- One rename mapping table (below) records every imported file's original name.

## Rename mapping (original → canonical)

| Original (cleancensus output) | Canonical (eqasim-bs) | Notes |
|---|---|---|
| `zensus2022_grid_1km_de_v3_midctrl.parquet` | `popsim/cells/zensus2022_grid_1km_de_binned.parquet` | produced by cleancensus `config_mid_controls.toml` (topics: Whg_Gebaeudetyp + HH_Seniorenstatus + HH_Familientyp, sanity 0 failures); 331 cols; EPSG:3035 INSPIRE; binned control marginals + reconciled `_adj` totals. Pre-import backup: `zensus2022_grid_1km_de_binned_pre_midctrl.parquet`. |
| `zensus2022_grid_100m_de_v3_midctrl.parquet` | `popsim/cells/zensus2022_grid_100m_de_prepared.parquet` | produced by cleancensus `config_mid_controls.toml` (topics: Whg_Gebaeudetyp + HH_Seniorenstatus + HH_Familientyp, sanity 0 failures); 576 cols; 3,148,482 rows; the COMPLETE prepared table: binned + gender backfill + `is_orphan` + `scale`/`_adj` + banded `M_AGE_*_agg`/`F_AGE_*_agg` (the control_field set) + RegioStaR2..17 + reconciled `Insgesamt_Haushalte_Typ_priv_HH_Familie_100m-Gitter_adj` (Familientyp reconciliation: max\|diff\|=0.0, 0 NaN). Pre-import backup: `zensus2022_grid_100m_de_prepared_pre_midctrl.parquet`. |
| `cells_100m_with_gender_backf_binneds_happyorphans_with_aggs_regiostar.parquet` | *(superseded)* | previous canonical 100m file (570 cols); replaced 2026-06-14 by v3_midctrl import above. |
| `inputs/MiD2023/.../CSV/MiD2023_Haushalte.csv` | `popsim/mid2023_raw/MiD2023_Haushalte.csv` | RESTRICTED; PopulationSim seed (households) |
| `inputs/MiD2023/.../CSV/MiD2023_Personen.csv` | `popsim/mid2023_raw/MiD2023_Personen.csv` | RESTRICTED; PopulationSim seed (persons) |
| `inputs/MiD2023/.../CSV/MiD2023_Wege.csv` | `popsim/mid2023_raw/MiD2023_Wege.csv` | RESTRICTED; trip/activity source (popsim_mid) |
| `inputs/buildings_with_households.gpkg` | `popsim/buildings/buildings_with_households_zgb.gpkg` | 7.58 M MultiPolygons, EPSG:25832; fields incl. `cell_id` (=ZENSUS100m), `has_home`, `hh_count`; the cell->building handoff target |

## Controls are the prepared cell columns (popsim_mid)

The 100 m cell parquet is already prepared with the binned control marginals. As in
the popsimprep notebook (Step 2), the PopulationSim control **targets are the cell
parquet columns themselves** (cleaned + suffixed `_ZENSUS100m` / `_ZENSUS1km`), with
the 1 km totals aggregated from the 100 m values. The per-control **seed expression**
(how to count that quantity on the MiD seed) lives in the hand-edited PopulationSim
controls CSV. So the cell-column -> control-target binding is: target column name ==
a prepared cell column; no synthetic renaming. (A speculative typed control-spec
module, `braunschweig.popsim.control_spec`, was removed 2026-06-11 as dead code --
the real controls always came from the CSV; resurrect from git history if the
declarative-controls wave wants it.)

## Provenance

- **Cell parquets:** produced by the **cleancensus** pipeline
  ([TUBS-IVS/cleancensus](https://github.com/TUBS-IVS/cleancensus)), a
  config-driven, sanity-gated preprocessing pipeline for German Zensus 2022 INSPIRE
  grids. The outputs are config-reproducible: `config_mid_controls.toml` drives the
  v3_midctrl build (topics Whg_Gebaeudetyp + HH_Seniorenstatus + HH_Familientyp;
  harmonised `_adj` totals; sanity 0 failures). This repo vendors the pipeline
  outputs (the large parquets are gitignored); **cleancensus is the source of
  record**. Pre-import backups of the previous canonical files are kept in the same
  `popsim/cells/` directory as `*_pre_midctrl.parquet` (2026-06-14 import).
- **MiD 2023 raw:** BMDV / infas MiD 2023 B1 scientific-use file (Datensatzpaket),
  CSV variants. Licence: BMDV scientific-use — non-redistributable; local-only.

## Configuration

The locations above are the **defaults** for the `population.input.*` /
`population.popsim.*` config keys (see the config design in the refactor plan).
Restricted paths are supplied via config / environment / a local ignored config
file — never hard-coded, never in a committed default config. A missing
`mid2023_raw/` path is an error **only** when `population.method = popsim_mid`.
