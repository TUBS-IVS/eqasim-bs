# eqasim-bs — synthetic population for Großraum Braunschweig

> 🚧 **Work in progress.** This project is under **active development** and
> not yet released. Interfaces, configuration keys, calibrated parameters,
> and outputs may still change between commits. It is already usable for
> research runs, but treat results as preliminary and pin a commit hash for
> reproducibility.

## Built on eqasim

**eqasim-bs is a regional fork of the [eqasim](https://github.com/eqasim-org)
pipeline.** It is built directly on top of
[`eqasim-org/eqasim-bavaria`](https://github.com/eqasim-org/eqasim-bavaria)
(branched @ `b20fbe6`) and re-uses its
[synpp](https://github.com/eqasim-org/synpp) DAG, stage structure, and MATSim
scenario builder. The Bavaria-specific data loaders are replaced by
Niedersachsen / Braunschweig equivalents, while the region-neutral eqasim
machinery (synthesis, location assignment, MATSim export) is inherited largely
unchanged. If you know eqasim, you already know how this pipeline is wired —
only the regional inputs and a handful of calibrated, Braunschweig-specific
models differ (see
[*How eqasim-bs differs from eqasim-bavaria*](#how-eqasim-bs-differs-from-eqasim-bavaria)).

**Region scope:** Zweckverband Großraum Braunschweig (ZGB-8, ARS prefix
`031`), ~1.13 M inhabitants.

This repository builds an open **synthetic population** of the ZGB for use as
input to agent-based transport simulations such as
[MATSim](https://matsim.org). It is region-locked to Braunschweig and fed by
German open data (BKG, DESTATIS, BBSR, BA, LGLN, BMV, LSN, OpenStreetMap,
Zensus 2022) and the MiD 2023 regional sample.

The pipeline is implemented as a content-hashed
[synpp](https://github.com/eqasim-org/synpp) DAG in Python 3.10 and
produces:

1. A synthetic population (households, persons, daily activity chains,
   trips) at 1 %, 10 %, or 25 % sampling rate.
2. A MATSim scenario (network, transit schedule, vehicle definitions,
   plans) ready for `matsim-{plans|main}` runs.

### Highlights

- **Real German open data end to end** — population, employment, commuting,
  households, income, buildings, and land use are all sourced from
  authoritative federal / Niedersachsen registers (no synthetic proxies).
- **Calibrated commuting gravity model** — work and education trips follow a
  distance-decay model fitted to BA Pendleratlas Kreis-pair flows, with a
  per-RegioStaR-7 slope so urban and rural origins decay at their own rate.
- **Data-driven education location models** — schools, kindergartens, and
  universities are assigned by dedicated gravity models on real
  Niedersachsen facility registers (LSN), calibrated against MiD 2023 and the
  DESTATIS Mikrozensus 2024. See the education sections in
  [`CLAUDE.md`](CLAUDE.md) and sections E/F of
  [`eqasim-data/DOWNLOAD_CHECKLIST_BS.md`](eqasim-data/DOWNLOAD_CHECKLIST_BS.md).
- **Privacy-conscious, fully documented data** — for data-protection reasons
  this repository hosts **no** third-party statistical registers. Only the small
  derived **MiD 2023 aggregate tables** are committed; every other input is
  downloaded by you from its official source. Each dataset is documented below
  with its exact source, table/code, target path, and licence so anyone can
  reproduce the run — see [Input data](#input-data--what-to-download).

## Region scope (ZGB-8)

Eight Kreise / kreisfreie Städte (AGS prefixes):

| AGS | Name |
|-----|------|
| 03101 | SK Braunschweig |
| 03102 | SK Salzgitter |
| 03103 | SK Wolfsburg |
| 03151 | LK Gifhorn |
| 03153 | LK Goslar |
| 03154 | LK Helmstedt |
| 03157 | LK Peine |
| 03158 | LK Wolfenbüttel |

Excludes Göttingen (03159) and Northeim (03155) — outside ZGB.
Bounding box (UTM 32N): 542 000 – 691 000 E, 5 700 000 – 5 900 000 N.

## Quickstart

### 1. Clone

```powershell
git clone https://github.com/<owner>/eqasim-bs.git
cd eqasim-bs
```

### 2. Environment

The pinned environment is described in [`environment.yml`](environment.yml)
(Python 3.10, pinned scientific stack). Using miniforge:

```powershell
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"
conda env create -f environment.yml
conda activate eqasim
```

### 3. Inputs

Download every dataset listed in
[`eqasim-data/DOWNLOAD_CHECKLIST_BS.md`](eqasim-data/DOWNLOAD_CHECKLIST_BS.md)
and place each file under the indicated path inside `eqasim-data/data/`.
Two preprocessing scripts must be run once after the initial download:

```powershell
python scripts/preprocess_alkis_landuse.py    # ALKIS + ATKIS -> parquet
python scripts/preprocess_osm_pois.py         # OSM PBF       -> parquet
python scripts/download_regiostar.py          # auto-fetch RegioStaR
python scripts/download_zensus_grid.py        # auto-fetch 100 m grid
```

Verify the inventory:

```powershell
python scripts/verify_braunschweig_inputs.py --matsim
```

### 4. Run

| Sample | Config | Output | Wall time (laptop) |
|--------|--------|--------|--------------------|
| 1 %  | [`config_local_braunschweig.yml`](config_local_braunschweig.yml) | `eqasim-data/output_bs/` | ~10 min |
| 10 % | [`config_local_braunschweig_10pct.yml`](config_local_braunschweig_10pct.yml) | `eqasim-data/output_bs_10pct/` | ~4 h |
| 25 % | [`config_local_braunschweig_25pct.yml`](config_local_braunschweig_25pct.yml) | `eqasim-data/output_bs_25pct/` | ~10 h |

```powershell
python -m synpp config_local_braunschweig.yml
```

A 0.1 % CI dry run (`config_dryrun_braunschweig.yml`) is available for
smoke-testing without producing artefacts. Seed is fixed at `1234` and
gravity slope at `-0.065` across all configs — see
[`AGENTS.md`](AGENTS.md) for the rules around changing those.

## Input data — what to download

> **Data-protection policy.** This repository hosts **no** third-party
> statistical data. The only data files committed are the small derived
> **MiD 2023 aggregate tables** (`eqasim-data/data/braunschweig/mid/mid2023_*.csv`,
> a few rows each) and the project's own calibration-evaluation outputs
> (`.../mid/education_calibration/*`). **Every dataset below must be downloaded
> by you** from its official source and placed under `eqasim-data/data/` at the
> indicated path.

All target paths are **relative to `eqasim-data/data/`**. After downloading,
verify the inventory with:

```powershell
python scripts/verify_braunschweig_inputs.py            # synthesis.output inputs
python scripts/verify_braunschweig_inputs.py --matsim   # + MATSim-only inputs
```

The script prints `[OK]` / `[--]` per dataset. The exhaustive companion (with
every note and edge case) is
[`eqasim-data/DOWNLOAD_CHECKLIST_BS.md`](eqasim-data/DOWNLOAD_CHECKLIST_BS.md);
the tables below are the primary, self-contained guide.

### A. Federal datasets (shared across regions)

| Dataset | Where / which table | Target path |
|---------|---------------------|-------------|
| **VG250-EW 31.12.** — administrative boundaries with population (BKG) | [gdz.bkg.bund.de](https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html) — VG250-EW (GeoPackage UTM32s) | `germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip` |
| **KBA Fahrerlaubnisbestand FE4** — driving licences by Bundesland | [kba.de](https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html) — table FE4 | `germany/fe4_2024.xlsx` |
| **ENTD 2008** — French national HTS, reused as the activity-chain donor | [statistiques.developpement-durable.gouv.fr](https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008) | `entd_2008/{Q_individu,Q_tcm_individu,Q_menage,Q_tcm_menage_0,K_deploc,Q_ind_lieu_teg}.csv` |

### B. Niedersachsen / Braunschweig statistical inputs (`synthesis.output`)

| Dataset | Where / which table | Target path |
|---------|---------------------|-------------|
| **Population** — DESTATIS 12411-0018, Kreis × sex × age class | [www-genesis.destatis.de](https://www-genesis.destatis.de/genesis/online) code `12411` (Flat-CSV) | `braunschweig/12411-0018_de.csv` |
| **Gemeinde population shares** — urbistat age table (11 classes) | [urbistat.com](https://urbistat.com) — Gemeinde age table | `braunschweig/urbistat_age_gemeinden.csv` |
| **Employees by residence** — GENESIS 13111-06-02-4 | [regionalstatistik.de](https://www.regionalstatistik.de/genesis/online) code `13111` | `braunschweig/13111-06-02-4.xlsx` |
| **Employees at workplace** — GENESIS 13111-01-03-5 | regionalstatistik.de code `13111` | `braunschweig/13111-01-03-5.xlsx` |
| **Employees by Wirtschaftsabteilung** — BA gemband-dlk | [statistik.arbeitsagentur.de](https://statistik.arbeitsagentur.de) — Beschäftigung, Gemeindeband | `braunschweig/gemband-dlk-0-202506-xlsx.xlsx` |
| **Commuters — Einpendler** (Arbeitsort ZGB) | statistik.arbeitsagentur.de — Pendleratlas (krpend) | `braunschweig/statistik_pendler_2026042493412.csv` |
| **Commuters — Auspendler** (Wohnort ZGB) | same Pendleratlas explorer | `braunschweig/statistik_pendler_2026042493430.csv` |
| **Households** — Zensus 2022 5000H-2001, Gemeinde × HH-size × type | [ergebnisse.zensus2022.de](https://ergebnisse.zensus2022.de) table `5000H-2001` (Flat-File) | `braunschweig/5000H-2001_de_flat.csv` |
| **Household income** — BBSR INKAR (Kreis × year) | [inkar.de](https://www.inkar.de) — `E_Haushaltseinkommen.xls` | `braunschweig/E_Haushaltseinkommen.xls` |
| **MiD 2023 reference tables** — numbered result tables (regional sample, non-commercial) | **committed** (`mid/mid2023_*.csv`); raw volume only needed to regenerate | `braunschweig/mid/mid2023_*.csv` |
| **RegioStaR-7** — BMV/BBSR Gemeinde typology | auto-download: `python scripts/download_regiostar.py` | `regiostar/regiostar_referenzdatei.xlsx` |
| **Zensus 100 m population grid** | auto-download: `python scripts/download_zensus_grid.py` | `zensus_grid/population_100m.parquet` |

### C. Large geodata (downloaded once, then preprocessed to parquet)

The synpp pipeline reads only the compact GeoParquet; the raw archives are not
loaded by stages. Run the two preprocessors (see [Quickstart](#3-inputs)).

| Raw input | Where | Target path → preprocessed output |
|-----------|-------|-----------------------------------|
| **ALKIS Hausumringe Niedersachsen** (~1.7 GB) | [opengeodata.lgln.niedersachsen.de](https://opengeodata.lgln.niedersachsen.de) — "Hausumringe Niedersachsen" | `braunschweig/buildings/gebaeude-ni.zip` → `braunschweig/preprocessed/alkis_buildings.parquet` |
| **ATKIS Basis-DLM landuse Niedersachsen** (~3.2 GB) | opengeodata.lgln.niedersachsen.de — "ATKIS Basis-DLM" | `braunschweig/landuse/FS_LN_03_NI_260101.zip` → `braunschweig/preprocessed/landuse.parquet` |
| **OSM Niedersachsen PBF** (~470 MB) | [download.geofabrik.de](https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf) | `osm/niedersachsen-latest.osm.pbf` → `braunschweig/preprocessed/osm_pois.parquet` |

### D. MATSim-only inputs (only for `matsim.output`)

| Dataset | Where | Target path |
|---------|-------|-------------|
| **OSM Niedersachsen PBF** (same file as C) | download.geofabrik.de | `osm/niedersachsen-latest.osm.pbf` |
| **GTFS Deutschland (Delfi) or ZGB feed** | [opendata-oepnv.de](https://www.opendata-oepnv.de/ht/de/organisation/delfi/startseite) / [zgb.de](https://www.zgb.de) | `gtfs/<any>.zip` |
| **VRB tariff zones** (ÖV-fare module) | [vrb-online.de](https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen) → `scripts/build_vrb_stations_json.py` | `vrb/tarifzonen.html` → `vrb/stations.json` |

### E. Education facility inputs (only with `education_gravity_enabled: true`)

These build the school / kindergarten / university gravity models. The derived
facility CSVs are **not committed** (data-protection); download the raw LSN
registers and regenerate them with the listed scripts. See
[`eqasim-data/data/braunschweig/schools/README.md`](eqasim-data/data/braunschweig/schools/README.md)
and `DATA_FLOW.md` for the full provenance.

| Raw input | Where | Regenerate → output (local only) |
|-----------|-------|----------------------------------|
| **LSN Schulverzeichnis ABS + BBS** (allgemein- + berufsbildende Schulen) | [statistik.niedersachsen.de](https://www.statistik.niedersachsen.de) — `Schulverzeichnis_ABS_2025.xlsx`, `Verzeichnis_der_BBS_2024.xlsx` | `scripts/extract_nds_schools.py` → `schools/nds_schools_zgb.csv` |
| **LSN Studierende nach Hochschule** (SS2025) | statistik.niedersachsen.de — Hochschulstatistik | `scripts/seed_nds_hochschulen.py` → `schools/nds_hochschulen.csv` |
| **LSN Kindertageseinrichtungen — Plätze** (table K2300112) | [nls.niedersachsen.de](https://www1.nls.niedersachsen.de/statistik/) — Kinder-/Jugendhilfestatistik | `scripts/extract_nds_kitas.py` → `schools/nds_kitas_zgb.csv` |
| **DESTATIS Mikrozensus 2024** — school-trip distance by school type | [destatis.de](https://www.destatis.de) — Mikrozensus Pendler tables | `scripts/seed_mikrozensus_school_distance.py` → `mikrozensus/mikrozensus2024_*.csv` |
| **MiD 2023 Tabelle 43** — school distance by RegioStaR-7 × age | **committed** (`mid/mid2023_T43_school_distance_by_rs7.csv`) | `scripts/seed_mid_t43_school_distance.py` |

Licences span dl-de/by-2-0 (BKG, Statistische Ämter, BA, BBSR, BMV),
dl-de/zero-2-0 (LGLN ALKIS/ATKIS), ODbL 1.0 (OSM), and BMDV non-commercial
(MiD 2023). Redistribution rules apply per dataset — which is exactly why only
the small derived MiD aggregates are committed here.

## Pipeline architecture

```mermaid
flowchart LR
    A[Federal + NDS<br/>statistical inputs] --> IPF[Iterative<br/>Proportional Fitting<br/>Gemeinde × HH-size × age × sex × employment]
    IPF --> POP[Synthetic<br/>households + persons]
    POP --> ENR[Enrichment<br/>income, licence, RegioStaR]
    POP --> HOME[Home zones<br/>ALKIS + Zensus 100 m]
    HOME --> GRAV[Gravity model<br/>BA Pendleratlas calibrated]
    GRAV --> WORK[Work / education<br/>locations]
    ENR --> CHAIN[Activity chains<br/>ENTD 2008 donor + MiD CDFs]
    WORK --> CHAIN
    CHAIN --> SEC[Secondary locations<br/>ALKIS / ATKIS / OSM]
    SEC --> OUT[CSV / Parquet output]
    OUT --> MATSIM[MATSim scenario<br/>network, schedule, plans]
```

Region-neutral building blocks live under
[`eqasim_common/`](eqasim_common/); region overrides under
[`braunschweig/`](braunschweig/). The former `bavaria/` tree of
inherited upstream modules has been removed; the few utilities still
needed were migrated into `eqasim_common/` and `braunschweig/`.

For the synpp DAG and stage layout, see
[`docs/codebase/ARCHITECTURE.md`](docs/codebase/ARCHITECTURE.md) and
[`docs/codebase/STRUCTURE.md`](docs/codebase/STRUCTURE.md).

## How eqasim-bs differs from eqasim-bavaria

| Area | eqasim-bavaria | eqasim-bs |
|------|----------------|-----------|
| Region | Free State of Bavaria (~13 M) | Großraum Braunschweig ZGB-8 (~1.1 M) |
| Population reference | GENESIS Bavaria 12111-101 | DESTATIS 12411-0018 + urbistat Gemeinde shares |
| Employment OD | LfStat Bavaria pendler XLSX | BA Pendleratlas 2025 CSVs (Ein-/Auspendler) |
| Households | Derived from MiD-Bayern | Zensus 2022 5000H-2001 (Gemeinde × size × type) |
| Income | LfStat Bavaria | BBSR INKAR Haushaltseinkommen (Kreis × year) |
| Buildings | OSM tags | ALKIS Hausumringe (LGLN) — preprocessed parquet |
| Landuse | OSM tags | ATKIS Basis-DLM (LGLN) — preprocessed parquet |
| Travel survey | MiD-Bayern | MiD 2023 regional sample for distance / mode CDFs; ENTD 2008 still feeds activity chains |
| Spatial fix | Bavaria-wide | ARS prefixes 031xx pinned in every config |

### Modelling and engineering changes on top of upstream

Beyond swapping the regional inputs, a few model and code changes were made
relative to eqasim-bavaria. They are intentionally additive — the upstream
behaviour is preserved unless a change is explicitly enabled:

- **Per-RegioStaR-7 gravity slope.** The commuting gravity model keeps the
  eqasim distance-decay friction but differentiates the slope by the
  origin Gemeinde's RegioStaR-7 class (urban vs. rural), calibrated on a single
  identified full-panel Poisson GLM over BA Pendleratlas Kreis-pair flows. The
  flow-weighted mean equals the scalar slope, so the regional mean commute is
  unchanged.
- **Data-driven education location models (flag-gated).** Schools,
  kindergartens, and universities are assigned by dedicated gravity models on
  real LSN facility registers instead of the generic OSM hard-radius sampler.
  Off by default (`education_gravity_enabled=false` → byte-identical to the
  legacy assignment); on, the slopes are calibrated against MiD 2023 Tabelle 43
  and the DESTATIS Mikrozensus 2024.
- **MiD-based categorical enrichment.** PT subscription type and driving
  licence are drawn from a three-margin IPF (raking) on MiD 2023 Tabellen P24.1
  / P17.1 (Kreis × sex × age), replacing the legacy single-target seeding and
  the KBA-based licence model. The boolean flags (`has_pt_subscription`,
  `has_license`) are derived from the categorical attributes.
- **Commute-distance override.** ZGB residents' commute distances are sampled
  from MiD 2023 P13 Kreis-level CDFs, overriding the ENTD-derived distances.
- **Reference values as CSV tables, not Python literals.** All MiD / Mikrozensus
  reference numbers live as versioned CSV tables and are regenerated only via
  dedicated seed scripts. For data-protection reasons only the small derived
  **MiD 2023 aggregates** are committed; the Mikrozensus and LSN-derived tables
  are kept local and regenerated from their official sources — see
  [`CLAUDE.md`](CLAUDE.md) and the
  [Input data](#input-data--what-to-download) guide.
- **Repository hygiene.** The inherited `bavaria/` tree was removed (its few
  still-needed utilities migrated into `eqasim_common/` / `braunschweig/`), and
  the codebase was documented under [`docs/codebase/`](docs/codebase).

## Calibration & validation

- The gravity model is calibrated to BA Pendleratlas Kreis × Kreis flows
  (Poisson-GLM MLE on 939 ZGB pairs, current slope `β = -0.065`).
- The household-size IPF margin is taken directly from Zensus 2022.
- Commute distance CDFs are sampled from MiD 2023 P13 (Kreis-level)
  and override the ENTD-derived distances for ZGB residents.
- Validation harness (10 % run): [`scripts/validate_bs_10pct.py`](scripts/validate_bs_10pct.py).
- Quality-playbook protocols: [`quality/QUALITY.md`](quality/QUALITY.md),
  [`quality/RUN_FUNCTIONAL_TESTS.md`](quality/RUN_FUNCTIONAL_TESTS.md),
  [`quality/RUN_INTEGRATION_TESTS.md`](quality/RUN_INTEGRATION_TESTS.md),
  [`quality/RUN_CODE_REVIEW.md`](quality/RUN_CODE_REVIEW.md),
  [`quality/RUN_SPEC_AUDIT.md`](quality/RUN_SPEC_AUDIT.md).

Test gate: `pytest tests/ -q` → 171 tests collected (smoke / pipeline /
determinism tests are opt-in via `EQASIM_BS_RUN_PIPELINE=1`).

## Known limitations

- The activity-chain donor is still ENTD 2008 (French HTS); a German
  HTS replacement is open work.
- 11 documented bugs from the upstream Bavaria pipeline remain tracked
  in [`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md). Per
  Decision D-5 (see [`AGENTS.md`](AGENTS.md)), the refactor is
  behaviour-preserving; only bugs that actively block the BS pipeline
  are fixed inline.
- The Java MATSim package is still `org.eqasim.bavaria.*`; renaming is
  out of scope for this refactor (Decision D-1c).

## Documentation

- [`AGENTS.md`](AGENTS.md) — single-page bootstrap for AI / human contributors.
- [`docs/codebase/`](docs/codebase) — architecture, stack, conventions, integrations, testing, concerns.
- [`docs/population.md`](docs/population.md) — upstream Bavaria population doc, partially applicable.
- [`docs/simulation.md`](docs/simulation.md) — upstream MATSim run doc, partially applicable.
- [`plan/refactor-eqasim-bs.md`](plan/refactor-eqasim-bs.md) — current refactor phase plan.
- [`quality/QUALITY.md`](quality/QUALITY.md) — fitness-to-purpose scenarios.

## Citation

If you use this code or the synthetic population it produces, please
cite both the upstream eqasim methodology and this regional fork. See
[`CITATION.cff`](CITATION.cff).

```
Hörl, S. and Balac, M. (2021). Synthetic population and travel demand
for Paris and Île-de-France based on open and publicly available data.
Transportation Research Part C: Emerging Technologies, 130, 103291.
https://doi.org/10.1016/j.trc.2021.103291

eqasim-bs (2026). Synthetic population for the Großraum Braunschweig
region. https://github.com/<owner>/eqasim-bs
```

## License

Code: GPL-3.0 (inherited from upstream eqasim).
Generated synthetic population data: see individual input licences;
the most restrictive (MiD 2023) requires non-commercial use of any
output that depends on it.
