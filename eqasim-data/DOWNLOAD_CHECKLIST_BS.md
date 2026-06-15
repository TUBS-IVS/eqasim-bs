# Download checklist — Braunschweig (ARS 031, Niedersachsen)

> All paths are relative to `eqasim-data/data/`. The authoritative
> verifier is [`scripts/verify_braunschweig_inputs.py`](../scripts/verify_braunschweig_inputs.py).
> This checklist is the human-readable companion: if you change inputs
> in the config or in `verify_braunschweig_inputs.py`, update this file
> in the same commit.
>
> **Data-protection policy:** this repository hosts **no** third-party
> statistical data. The **only** committed data files are the small derived
> **MiD 2023 aggregate tables** (`mid/mid2023_*.csv`) and the project's own
> calibration-evaluation outputs (`mid/education_calibration/*`) — see
> [Section F](#f-committed-reference-data-on-github). Everything else listed
> below (incl. the DESTATIS Mikrozensus tables and the derived NDS school /
> Hochschule / Kita facility tables) must be downloaded / regenerated locally
> and is **never** committed.

## How to verify

```powershell
python scripts/verify_braunschweig_inputs.py            # synthesis.output only
python scripts/verify_braunschweig_inputs.py --matsim   # add MATSim inputs
```

The script prints `[OK]` / `[--]` per dataset and writes a remediation
list for missing files.

---

## A. Federal datasets (shared across regions)

| # | Dataset | Source | Target path | Licence |
|---|---------|--------|-------------|---------|
| A1 | **VG250-EW 31.12.** (administrative boundaries with population) | https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html | `germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip` | dl-de/by-2-0 (BKG) |
| A2 | **KBA Fahrerlaubnisbestand FE4** (driving licences by Bundesland) | https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html | `germany/fe4_2024.xlsx` | KBA terms (free download) |
| A3 | **ENTD 2008** (French HTS — reused as travel-pattern donor) | https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008 | `entd_2008/{Q_individu,Q_tcm_individu,Q_menage,Q_tcm_menage_0,K_deploc,Q_ind_lieu_teg}.csv` | INSEE/SDES open data |

A1 is required for the spatial pipeline (VG250 zones + landuse clipping).
A2 is required for the licence-attribution stage (FE4.2 / FE4.3 / FE4.4
sheets, NDS filter applied automatically). A3 is the upstream travel-
pattern donor; the BS pipeline does not yet have a German HTS replacement.

## B. Lower-Saxony statistical inputs (synthesis.output)

| # | Dataset | Source | Target path | Licence |
|---|---------|--------|-------------|---------|
| B1 | **DESTATIS 12411-0018 population** (Kreis × sex × age class) | https://www-genesis.destatis.de/genesis/online?operation=statistic&code=12411 (Flat-CSV export) | `braunschweig/12411-0018_de.csv` | dl-de/by-2-0 (Statistische Ämter) |
| B1b | **urbistat Gemeinde-level population shares** (11 age classes, scraped) | https://urbistat.com — Gemeinde-level age table (project archive) | `braunschweig/urbistat_age_gemeinden.csv` | urbistat terms (non-redistributable) |
| B2 | **Employees by residence — GENESIS 13111-06-02-4** (Wohnort × Alter × Sex) | https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111 | `braunschweig/13111-06-02-4.xlsx` | dl-de/by-2-0 |
| B3 | **Employees at workplace — GENESIS 13111-01-03-5** (SvB Arbeitsort, Gemeinde) | https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111 | `braunschweig/13111-01-03-5.xlsx` | dl-de/by-2-0 |
| B4 | **BA Beschäftigtenstatistik gemband-dlk** (employees by Wirtschaftsabteilung × Gemeinde) | https://statistik.arbeitsagentur.de — Beschäftigung / sozialversicherungspflichtig / Gemeindeband | `braunschweig/gemband-dlk-0-202506-xlsx.xlsx` | BA terms |
| B5a | **BA Pendleratlas — Einpendler** (Arbeitsort ZGB, CSV) | https://statistik.arbeitsagentur.de/SiteGlobals/Forms/Suche/Einzelheftsuche_Formular.html?topic_f=beschaeftigung-sozbe-krpend | `braunschweig/statistik_pendler_2026042493412.csv` | BA terms |
| B5b | **BA Pendleratlas — Auspendler** (Wohnort ZGB, CSV) | (same Pendleratlas explorer) | `braunschweig/statistik_pendler_2026042493430.csv` | BA terms |
| B7 | **Zensus 2022 — Households (5000H-2001 flat-CSV)** Gemeinde × HH-size × HH-type | https://ergebnisse.zensus2022.de (Tabelle `5000H-2001`, Flat-File) | `braunschweig/5000H-2001_de_flat.csv` | dl-de/by-2-0 (Statistische Ämter) |
| B8 | **BBSR INKAR — household income** (Kreis × year, Haushaltseinkommen €/EW/Monat) | https://www.inkar.de (Indikatorenexport `E_Haushaltseinkommen.xls`) | `braunschweig/E_Haushaltseinkommen.xls` | dl-de/by-2-0 (BBSR) |
| B9 | **BBSR INKAR — full panel (optional)** other indicator exports `E_*.xls` (population density, unemployment, education, healthcare) | https://www.inkar.de | `braunschweig/E_Bevoelkerungsdichte.xls`, `E_Arbeitslosenquote.xls`, `E_HochschulabsolventenQuote.xls`, `E_AerzteJeEinwohner.xls` | dl-de/by-2-0 |
| B10 | **Additional reference tables** (numbered tables; needed only to *regenerate* the committed CSVs) | result-table volume provided to the project | `braunschweig/mid/mid2023_result_tables.pdf` (any local filename) | see source terms |
| B10a | **MiD 2023 — extracted reference CSVs** (the numbered tables P9 / P12.1 / P13 / P17.1 / P24.1 / Tabelle 43 etc.) | **committed to the repo** (see Section F); regenerate from B10 via `scripts/extract_mid_tables.py` / the seed scripts | `braunschweig/mid/mid2023_*.csv` | aggregate reference values |
| B11 | **BMV/BBSR RegioStaR-7 reference** (Gemeinde-level RegioStaR class) | https://www.bmv.de/SharedDocs/DE/Anlage/G/regiostar-referenzdateien.xlsx — auto-downloaded by `python scripts/download_regiostar.py` | `regiostar/regiostar_referenzdatei.xlsx` | dl-de/by-2-0 (BMV) |
| B12 | **Zensus 2022 100 m population grid (parquet)** | `https://github.com/JsLth/z22data` (BKG GeoGitter + Statistische Ämter) — auto-downloaded by `python scripts/download_zensus_grid.py` | `zensus_grid/population_100m.parquet`, `zensus_grid/grid_100m.parquet` | dl-de/by-2-0 (BKG / Statistische Ämter) |

Notes:

- **B1 / B1b**: the BS pipeline derives Gemeinde × age × sex by combining the authoritative DESTATIS 12411-0018 Kreis totals (B1) with the urbistat Gemeinde-level shares (B1b). See [`braunschweig/data/census/population.py`](../braunschweig/data/census/population.py).
- **Legacy dead-config keys** in `config_local_braunschweig.yml`: `bavaria.population_path: braunschweig/12111-0001_population_ni.xlsx` and `bavaria.work_flow_path: braunschweig/pendler_ni.xlsx`. Both are read only by upstream `bavaria/data/census/{population,employees}.py` modules, which are aliased to the `braunschweig.*` forks on the active DAG. The two filenames do **not** need to exist on disk for the BS pipeline to run; they remain only because the bavaria stages still resolve those config keys at import time.
- **B5a/B5b** filenames carry the Pendleratlas export timestamp; rename in `config_local_braunschweig.yml` if you re-export. `braunschweig.data.census.pendler` parses both files via the `r"\d{5}"` ARS regex.
- **B7** is required by [`braunschweig/data/census/household_size.py`](../braunschweig/data/census/household_size.py), [`households_size_age.py`](../braunschweig/data/census/households_size_age.py), and [`households_type.py`](../braunschweig/data/census/households_type.py). All three loaders raise with a download hint to https://ergebnisse.zensus2022.de if missing.
- **B10 / B10a**: the numbered MiD 2023 reference tables (e.g. P13 for the BS-specific commute-distance CDFs) are **committed as small aggregate CSVs** (Section F), so the raw B10 volume is needed only to regenerate them. If a CSV is absent the consuming stage falls back to its documented default (e.g. [`commute_distance.py`](../braunschweig/synthesis/spatial/commute_distance.py) falls back to the ZGB aggregate row).
- **B11 / B12** are auto-downloaded — do not commit them; they live in user-local `eqasim-data/`.

## C. ALKIS / ATKIS / OSM raw inputs (preprocessed once)

These are large raw files that are processed into compact GeoParquet by
the scripts under `scripts/`. The synpp pipeline reads only the
preprocessed parquet — the raw zips are not loaded by stages.

| # | Raw input | Source | Target path | Preprocessor → output |
|---|-----------|--------|-------------|-----------------------|
| C1 | **ALKIS Hausumringe Niedersachsen** (`gebaeude-ni.shp` inside ZIP, ~1.7 GB) | https://opengeodata.lgln.niedersachsen.de — "Hausumringe Niedersachsen" (Shapefile) | `braunschweig/buildings/gebaeude-ni.zip` | `scripts/preprocess_alkis_landuse.py` → `braunschweig/preprocessed/alkis_buildings.parquet` |
| C2 | **ATKIS Basis-DLM landuse Niedersachsen** (`FS_LN_03_NI_*.zip`, ~3.2 GB GPKG inside) | https://opengeodata.lgln.niedersachsen.de — "ATKIS Basis-DLM" (GeoPackage / Shape) | `braunschweig/landuse/FS_LN_03_NI_260101.zip` | `scripts/preprocess_alkis_landuse.py` → `braunschweig/preprocessed/landuse.parquet` |
| C3 | **OSM Niedersachsen PBF** (~470 MB) | https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf | `osm/niedersachsen-latest.osm.pbf` | `scripts/preprocess_osm_pois.py` → `braunschweig/preprocessed/osm_pois.parquet` (also reused directly by MATSim output) |

Preprocessing commands:

```powershell
# C1 + C2 → ALKIS + landuse parquet (one-off, ~10 min)
python scripts/preprocess_alkis_landuse.py `
    --raw-root eqasim-data/data/braunschweig `
    --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip `
    --prefix 03101,03102,03103,03151,03153,03154,03157,03158 `
    --out-root eqasim-data/data/braunschweig/preprocessed

# C3 → OSM POIs parquet (one-off, ~5 min)
python scripts/preprocess_osm_pois.py `
    --pbf eqasim-data/data/osm/niedersachsen-latest.osm.pbf `
    --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip `
    --prefix 03101,03102,03103,03151,03153,03154,03157,03158 `
    --out eqasim-data/data/braunschweig/preprocessed/osm_pois.parquet
```

Licences:

- ALKIS / ATKIS (LGLN): dl-de/zero-2-0 (open data, no attribution required as of LGLN 2023 release).
- OSM (Geofabrik export): ODbL 1.0, © OpenStreetMap contributors.

The `bavaria.buildings_path: braunschweig/buildings` line in
`config_local_braunschweig.yml` is **legacy** — only `bavaria/data/buildings.py`
reads it, and that stage is not on the active BS DAG. Kept for the cached
`bavaria.*` graph but not required by the BS pipeline. Leaving the directory
empty is harmless.

## D. MATSim-only inputs (optional)

Required only when running `matsim.output` (Java MATSim scenario build).
None of these are required for `synthesis.output`.

| # | Dataset | Source | Target path | Licence |
|---|---------|--------|-------------|---------|
| D1 | **OSM Niedersachsen PBF** (same file as C3) | https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf | `osm/niedersachsen-latest.osm.pbf` | ODbL 1.0 |
| D2 | **GTFS Deutschland (Delfi) or ZGB feeds** (zip) | https://www.opendata-oepnv.de/ht/de/organisation/delfi/startseite or `https://www.zgb.de` | `gtfs/<any>.zip` | DELFI / ZGB terms |
| D3 | **VRB tariff-zone mapping** for `braunschweig.data.vrb.zones` (consumed by Java `AddTransitZoneInformation` → ÖV-fare module). Built from the public VRB website (preferred) or from a Waben polygon delivery (fallback) | https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen (HTML) — alt: VRB / LGLN polygon delivery | `vrb/tarifzonen.html` → `vrb/stations.json` via `scripts/build_vrb_stations_json.py` (or `vrb/waben.gpkg` → same script) | VRB terms |

GTFS should be pre-clipped to the ZGB bounding box (see below).

D3 preprocessing (build `vrb/stations.json` once, in MVG schema):

**Recommended — scrape the public VRB tariff-zone page** (no licence
delivery required; ~9 000 / 25 000 GTFS stops in the ZGB bbox match):

```powershell
Invoke-WebRequest "https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen" `
    -OutFile eqasim-data/data/vrb/tarifzonen.html

python scripts/build_vrb_stations_json.py `
    --vrb-html eqasim-data/data/vrb/tarifzonen.html `
    --gtfs eqasim-data/data/gtfs/latest.zip `
    --out eqasim-data/data/vrb/stations.json
```

**Fallback — Waben polygon spatial join** (when VRB delivers an
authoritative shapefile):

```powershell
python scripts/build_vrb_stations_json.py `
    --waben eqasim-data/data/vrb/waben.gpkg `
    --waben-zone-column WABE `
    --gtfs eqasim-data/data/gtfs/latest.zip `
    --out eqasim-data/data/vrb/stations.json
```

The output JSON mirrors the MVG REST schema (`name`, `latitude`,
`longitude`, `tariffZones`) so `braunschweig.data.vrb.zones` reuses the
MVG algorithm bit-for-bit (400 m buffered MultiPoint per zone, EPSG:25832).

## E. Education facility inputs (real-data gravity models)

The education location assignment (`education_gravity_enabled: true`) places
**school pupils, kindergarten children, and university students on real
facilities**. The derived facility CSVs are **not committed** (data-protection):
you must download the raw LSN registers below and regenerate the CSVs with the
listed scripts before running with the flag on. With the flag off (default) the
legacy OSM education sampler runs and none of this is consulted.

### Raw sources (download, then regenerate the local CSV)

| # | Dataset | Source | Local path (any name) | Produces (local CSV) |
|---|---------|--------|-----------------------|--------------------------|
| E1 | **LSN Schulverzeichnis — allgemeinbildende Schulen** (per school: Schulgliederung + Schülerzahlen + address) | https://www.statistik.niedersachsen.de — Verzeichnisse, `Schulverzeichnis_ABS_2025.xlsx` | `Schulen NDS/Schulverzeichnis_ABS_2025.xlsx` | `schools/nds_schools_zgb.csv` via `scripts/extract_nds_schools.py` (geocodes + OSM-validates) |
| E2 | **LSN Verzeichnis der berufsbildenden Schulen (BBS)** (per school: Schulform + Schülerzahlen + address) | same — `Verzeichnis_der_BBS_2024.xlsx` | `Schulen NDS/Verzeichnis_der_BBS_2024.xlsx` | same `nds_schools_zgb.csv` (BBS rows) |
| E3 | **LSN Studierende nach Hochschule** (enrollment per Hochschule, SS2025) | https://www.statistik.niedersachsen.de/hochschulen-studierende-hochschulfinanzen-niedersachsen/ — `VOE_Stud._1.HS_SS25_HSArt_HS_*.xlsx` | `Hochschulen NDS/VOE_Stud._1.HS_SS25_HSArt_HS_Barrierefrei.xlsx` | `schools/nds_hochschulen.csv` via `scripts/seed_nds_hochschulen.py` (LSN enrollment + curated surrounding/cross-border campus points) |
| E4 | **LSN Kindertageseinrichtungen — Plätze** (places per Einheits-/Samtgemeinde, table K2300112) | https://www1.nls.niedersachsen.de/statistik/ — Kinder- und Jugendhilfestatistik 22544 (Excel-2003 SpreadsheetML, in a ZIP) | `Kitas NDS/K2300112_Kindertageseinrichtungen_Plaetze_2025.xml` | `schools/nds_kitas_zgb.csv` via `scripts/extract_nds_kitas.py` |
| E5 | **DESTATIS Mikrozensus 2024 — school-trip distance by school type** (ABS / BBS / Hochschule distance bands) | https://www.destatis.de/.../Erwerbstaetigkeit/Tabellen/pendler2.html | (values pinned in seed script) | `mikrozensus/mikrozensus2024_school_distance_by_type.csv` via `scripts/seed_mikrozensus_school_distance.py` |
| E6 | **MiD 2023 Tabelle 43** — Kita-/Schulweglängen by RegioStaR-7 + age group (school distance calibration target) | MiD 2023 (values pinned in seed script) | (values pinned in seed script) | `mid/mid2023_T43_school_distance_by_rs7.csv` via `scripts/seed_mid_t43_school_distance.py` |

E1/E2 feed the capacity-constrained school gravity (grundschule / sekundar_1 /
oberstufe / bbs); E4 the Kita gravity (kindergarten); E3 the university decay
model; E5/E6 are the distance-calibration targets. OSM kindergarten/university POIs
come from `preprocessed/osm_pois.parquet` (C3) — no separate download. Regenerate
commands and the full SGL/Schulform→level and ARS→Samtgemeinde rules are in
`eqasim-data/data/braunschweig/schools/README.md` + `.../schools/DATA_FLOW.md`
and in `CLAUDE.md` (sections "Education gravity model (NDS school data)").

## F. What is committed vs. local-only (data-protection)

`eqasim-data/` is `.gitignore`-d. Only the files below are deliberately
`git add -f`-committed; **everything else stays local on your machine** and must
be downloaded / regenerated from the sources in Sections A–E. Do not hand-edit
the committed CSVs — change the seed script / raw source and re-run (see
CLAUDE.md). Do **not** `git add -f` any other data file.

**Committed (on GitHub):**

| File (under `eqasim-data/data/braunschweig/`) | Content | Regenerate with |
|---|---|---|
| `mid/mid2023_*.csv` | MiD 2023 numbered reference tables (P9/P12.1/P13/P17.1/P24.1/H4/H7/H12.3/P36.1/W1/W2/Tabelle 43 + margins + class-midpoint) — small aggregate tables, a few rows each | `scripts/seed_mid_constraint_tables.py`, `scripts/extract_mid_tables.py`, `scripts/seed_mid_t43_school_distance.py` |
| `mid/education_calibration/*` | the project's own calibration-evaluation outputs (results CSV, figures, summary) — model diagnostics, no third-party data | `scripts/calibrate_education_slopes.py --output-dir ...` |

**Local-only — NEVER committed (download / regenerate yourself):**

| File (under `eqasim-data/data/braunschweig/`) | Content | Regenerate with |
|---|---|---|
| `schools/nds_schools_zgb.csv` | NDS schools (ABS+BBS) typed by level + geocoded + capacity | `scripts/extract_nds_schools.py` (E1+E2) |
| `schools/nds_hochschulen.csv` | Hochschule enrollment + campus coords (local + surrounding) | `scripts/seed_nds_hochschulen.py` (E3) |
| `schools/nds_kitas_zgb.csv` | Kita Plätze per Einheits-/Samtgemeinde | `scripts/extract_nds_kitas.py` (E4) |
| `mikrozensus/mikrozensus2024_*.csv` | DESTATIS Mikrozensus 2024 commute time/mode/distance + school distance by type | `scripts/seed_mikrozensus_school_distance.py` (+ the mikrozensus extractors) |
| all Section A–D inputs | population/employment/commuting/household/income registers, ALKIS/ATKIS, OSM, GTFS, ... | download per Sections A–D |

Licence note: the committed tables are small derived aggregate reference values;
all other inputs are kept local. Check each dataset's own terms before reuse.

---

## H. Long-haul freight inputs (only with `freight_enabled: true`, default on)

Adds heavy-goods-vehicle traffic from the open VSP **german-wide-freight v3**
model. Both files are large and **local-only** (gitignored); fetch them with the
committed download script (writes a provenance README + sha256 log next to them):

```bash
python scripts/download_german_wide_freight.py
```

| # | Dataset | Source | Target path | Licence |
|---|---------|--------|-------------|---------|
| H1 | **german_freight.100pct.plans.xml.gz** (~72 MB; one agent per long-haul freight trip per average workday) | [VSP public SVN `matsim/scenarios/countries/de/german-wide-freight/v3`](https://svn.vsp.tu-berlin.de/repos/public-svn/matsim/scenarios/countries/de/german-wide-freight/v3/) | `braunschweig/freight/german-wide-freight-v3/german_freight.100pct.plans.xml.gz` | VSP/MATSim open data (see SVN) |
| H2 | **germany-europe-network.xml.gz** (~61 MB; German-European routing network, EPSG:25832) | same SVN directory | `braunschweig/freight/german-wide-freight-v3/germany-europe-network.xml.gz` | VSP/MATSim open data |

Provenance: **Lu, C., Martins-Turner, K., Nagel, K. (2022): _Creating an
agent-based long-haul freight transport model for Germany_. Procedia Computer
Science 201, 614–620, [doi:10.1016/j.procs.2022.03.080](https://doi.org/10.1016/j.procs.2022.03.080)**
(CC BY-NC-ND). Demand = BMVI _Verkehrsprognose 2030_ NUTS-3 goods flows → daily
truck trips (≈13 t average load), calibrated against BASt HGV counts. The data
never enters the synthetic-resident model; it is routed + classified
(internal / incoming / outgoing / transit) by the published matsim
application-contrib tool and injected as a `truck` `freight` subpopulation. See
[`CLAUDE.md`](../CLAUDE.md) ("Long-haul freight injection") for the pipeline.

---

## Bounding box (ARS 031xx, eight ZGB Kreise)

- AGS prefixes: `03101, 03102, 03103, 03151, 03153, 03154, 03157, 03158`
- UTM 32N (EPSG:25832): 542 000 – 691 000 E, 5 700 000 – 5 900 000 N
- WGS84 (rough): 9.6° – 11.4° E, 51.4° – 52.7° N

Use these bounds when pre-filtering OSM (C3 / D1) and GTFS (D2).

## Notes

- **No raw KBA download per region.** A2 (`fe4_2024.xlsx`) covers all of Germany; the BS pipeline reads it with a `Niedersachsen` filter on sheet FE4.3.
- **Household sizes / income are required for the BS pipeline** (B7, B8) — unlike the upstream Bavaria setup, which derived those from MiD. Do not reuse the Bavaria value here.
- **z22data and RegioStaR scripts** write into `eqasim-data/`; that directory is `.gitignore`-d at the repo root. Re-run the download scripts after any clean checkout.
- **Status tracking** is intentionally not part of this file — use `python scripts/verify_braunschweig_inputs.py` for an automated check rather than manual checkboxes.

## Cordon in-commuter mode reference (Mikrozensus / MiD)

The fixed travel mode of cordon in-commuter (Einpendler) agents is drawn from a
German commute mode-by-distance reference, anchored **per source Bundesland**.
Modes modelled: `walk`, `bicycle`, `car`, `pt`. The committed clean CSVs live
under `eqasim-data/data/braunschweig/mikrozensus/` (gitignored like all raw data;
copied to the server by `sync_data_to_server.ps1`). Full provenance:
`braunschweig/mikrozensus/README.md`; flow: `.../DATA_FLOW.md`.

| # | Source | How to obtain | CSV(s) produced | Extraction script |
|---|--------|---------------|-----------------|-------------------|
| G1 | **MiT 2023** national mode x distance for commute trips (rbW; Hauptverkehrsmittel imputed; Wegelaenge groups; row-%). | mobilitaet-in-deutschland.de (free account) | `mid2023_commute_mode_by_distance_de.csv`, `mid2023_commute_distance_de.csv` | `scripts/extract_mit_commute_mode_by_distance.py` |
| G2a | **GENESIS 12251-0105** Verkehrsmittel x Bundesland (Mikrozensus 2024). | www-genesis.destatis.de (free account), code 12251 | `mid_mode_margin_by_bundesland.csv` | `scripts/extract_mikrozensus_bundesland_margins.py` |
| G2b | **GENESIS 12251-0106** Entfernung x Bundesland (Mikrozensus 2024). | www-genesis.destatis.de (free account), code 12251 | `mid_distance_margin_by_bundesland.csv` | `scripts/extract_mikrozensus_bundesland_margins.py` |
| G3 | **LSN PM 096/2025** Anlagen (NDS twins of G2, provenance only). | statistik.niedersachsen.de (PM 096/2025, public) | `lsn_pm096-2025_*.pdf`, `Anlage_{1..4}_*.xlsx` (not extracted) | — |
| G4 | **MiD 2023 Grossraum Braunschweig** (infas 7555) regional tables P38.4/P38.2/W12. **NON-PUBLIC** — sanity cross-check only, **NOT used in the reference**. | infas 7555 (via ZGB/BMDV); not redistributable | `mid2023zgb_*.csv` | `scripts/extract_mid_zgb_commute_tables.py` (needs `pdfplumber`) |

Notes:
- G1/G2 exports are login-gated; export with the exact filters, file the raw
  `.xlsx`/`.csv`, then run the extraction scripts to produce the clean CSVs.
- **G4 is NON-PUBLIC** — only the extracted CSVs exist locally; the raw report must
  not be redistributed, and it never enters the mode model (cross-check only).
- The reference itself uses **G1 + G2** only. Regenerate:
  ```powershell
  python scripts/extract_mit_commute_mode_by_distance.py
  python scripts/extract_mikrozensus_bundesland_margins.py
  ```
