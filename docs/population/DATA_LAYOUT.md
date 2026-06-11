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

| Original (in popsimprep `inputs/`) | Canonical (eqasim-bs) | Notes |
|---|---|---|
| `cells_1km_with_binneds.parquet` | `popsim/cells/zensus2022_grid_1km_de_binned.parquet` | 212,758 cells × 348 cols; EPSG:3035 INSPIRE; binned control marginals |
| `cells_100m_with_gender_backf_binneds_happyorphans_with_aggs_regiostar.parquet` | `popsim/cells/zensus2022_grid_100m_de_prepared.parquet` | 3,148,482 × 570; the COMPLETE prepared table: binned + gender backfill + `is_orphan` + `scale`/`_adj` + banded `M_AGE_*_agg`/`F_AGE_*_agg` (the control_field set) + RegioStaR2..17. The base `..._happyorphans.parquet` (without `_agg`/RegioStaR) was superseded by this and deleted. |
| `inputs/MiD2023/.../CSV/MiD2023_Haushalte.csv` | `popsim/mid2023_raw/MiD2023_Haushalte.csv` | RESTRICTED; PopulationSim seed (households) |
| `inputs/MiD2023/.../CSV/MiD2023_Personen.csv` | `popsim/mid2023_raw/MiD2023_Personen.csv` | RESTRICTED; PopulationSim seed (persons) |
| `inputs/MiD2023/.../CSV/MiD2023_Wege.csv` | `popsim/mid2023_raw/MiD2023_Wege.csv` | RESTRICTED; trip/activity source (popsim_mid) |
| `inputs/buildings_with_households.gpkg` | `popsim/buildings/buildings_with_households_zgb.gpkg` | 7.58 M MultiPolygons, EPSG:25832; fields incl. `cell_id` (=ZENSUS100m), `has_home`, `hh_count`; the cell->building handoff target |

## Controls are the prepared cell columns (popsim_mid)

The 100 m cell parquet is already prepared with the binned control marginals. As in
the popsimprep notebook (Step 2), the PopulationSim control **targets are the cell
parquet columns themselves** (cleaned + suffixed `_ZENSUS100m` / `_ZENSUS1km`), with
the 1 km totals aggregated from the 100 m values. The per-control **seed expression**
(how to count that quantity on the MiD seed) is the declarative control spec. So the
cell-column -> control-target binding is: target column name == a prepared cell
column; no synthetic renaming. `braunschweig.popsim.control_spec` provides typed
control definitions; the concrete target set follows the prepared parquet columns.

## Provenance (to verify)

- **Cell parquets:** German Zensus 2022 INSPIRE 100 m / 1 km grids with attached
  binned control marginals + a gender backfill + a population rescale
  (`scale`/`_adj`) + orphan handling (`is_orphan`). The exact preprocessing
  (backfill method, "happyorphans" rule, source Zensus tables) is **[TODO]** — to
  be documented and, ideally, made reproducible by a committed script before these
  become a load-bearing input. Until then, treat them as a vendored preprocessed
  input with this layout doc as the provenance record.
- **MiD 2023 raw:** BMDV / infas MiD 2023 B1 scientific-use file (Datensatzpaket),
  CSV variants. Licence: BMDV scientific-use — non-redistributable; local-only.

## Configuration

The locations above are the **defaults** for the `population.input.*` /
`population.popsim.*` config keys (see the config design in the refactor plan).
Restricted paths are supplied via config / environment / a local ignored config
file — never hard-coded, never in a committed default config. A missing
`mid2023_raw/` path is an error **only** when `population.method = popsim_mid`.
