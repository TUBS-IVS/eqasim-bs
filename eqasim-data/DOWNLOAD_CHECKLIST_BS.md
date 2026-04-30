# Download checklist — Braunschweig (ARS 031, Niedersachsen)

> All paths are relative to `eqasim-data/data/`. The authoritative
> verifier is [`scripts/verify_braunschweig_inputs.py`](../scripts/verify_braunschweig_inputs.py).
> This checklist is the human-readable companion: if you change inputs
> in the config or in `verify_braunschweig_inputs.py`, update this file
> in the same commit.

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
| B10 | **MiD 2023 — Großraum Braunschweig (infas 7555)** result tables PDF | infas mobility report — provided by ZGB / BMDV | `braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf` | infas / BMDV non-commercial |
| B10a | **MiD 2023 — extracted CSVs** (P9 / P12_1 / P13 / P17_1 — produced from B10 by `scripts/extract_mid_tables.py`) | Generated locally | `braunschweig/mid/mid2023_P{9,12_1,13,17_1}.csv` | derived from B10 |
| B11 | **BMV/BBSR RegioStaR-7 reference** (Gemeinde-level RegioStaR class) | https://www.bmv.de/SharedDocs/DE/Anlage/G/regiostar-referenzdateien.xlsx — auto-downloaded by `python scripts/download_regiostar.py` | `regiostar/regiostar_referenzdatei.xlsx` | dl-de/by-2-0 (BMV) |
| B12 | **Zensus 2022 100 m population grid (parquet)** | `https://github.com/JsLth/z22data` (BKG GeoGitter + Statistische Ämter) — auto-downloaded by `python scripts/download_zensus_grid.py` | `zensus_grid/population_100m.parquet`, `zensus_grid/grid_100m.parquet` | dl-de/by-2-0 (BKG / Statistische Ämter) |

Notes:

- **B1 / B1b**: the BS pipeline derives Gemeinde × age × sex by combining the authoritative DESTATIS 12411-0018 Kreis totals (B1) with the urbistat Gemeinde-level shares (B1b). See [`braunschweig/data/census/population.py`](../braunschweig/data/census/population.py).
- **Legacy dead-config keys** in `config_local_braunschweig.yml`: `bavaria.population_path: braunschweig/12111-0001_population_ni.xlsx` and `bavaria.work_flow_path: braunschweig/pendler_ni.xlsx`. Both are read only by upstream `bavaria/data/census/{population,employees}.py` modules, which are aliased to the `braunschweig.*` forks on the active DAG. The two filenames do **not** need to exist on disk for the BS pipeline to run; they remain only because the bavaria stages still resolve those config keys at import time.
- **B5a/B5b** filenames carry the Pendleratlas export timestamp; rename in `config_local_braunschweig.yml` if you re-export. `braunschweig.data.census.pendler` parses both files via the `r"\d{5}"` ARS regex.
- **B7** is required by [`braunschweig/data/census/household_size.py`](../braunschweig/data/census/household_size.py), [`households_size_age.py`](../braunschweig/data/census/households_size_age.py), and [`households_type.py`](../braunschweig/data/census/households_type.py). All three loaders raise with a download hint to https://ergebnisse.zensus2022.de if missing.
- **B10** is the source of the BS-specific commute-distance CDFs (P13). If absent, [`braunschweig/synthesis/spatial/commute_distance.py`](../braunschweig/synthesis/spatial/commute_distance.py) falls back to the ZGB aggregate row.
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

## E. Education capacity inputs (Phase 0 of feature-education-gravity-bs-1)

Required only when running with `gravity_education_separate: true`
(see `plan/feature-education-gravity-bs-1.md`). The default off-state
of the flag does not consult any of these files; verify them only when
the feature is being calibrated.

| # | Dataset | Source | Target path | Licence |
|---|---------|--------|-------------|---------|
| E1 | **LSN Schulstatistik — Schüler nach Schulform und Gemeinde** (allgemein bildende Schulen, table `K3300101` LSN / `21111-04-01-4` RDB) | https://www1.nls.niedersachsen.de/statistik/ — alt: https://www.regionalstatistik.de/genesis/online/ | `braunschweig/lsn/lsn_schulen_<year>.csv` | dl-de/by-2-0 (LSN) |
| E2 | **LSN Schulstatistik — berufsbildende Schulen** (table `K3320101` LSN / `21121-04-01-4` RDB) | same | `braunschweig/lsn/lsn_berufsschulen_<year>.csv` | dl-de/by-2-0 (LSN) |
| E3 | **DESTATIS Hochschulstatistik — Studierende nach Studienort** (table `21311-0007`) | https://www-genesis.destatis.de/genesis/online | `braunschweig/destatis/hochschulen_<year>.csv` | dl-de/by-2-0 (DESTATIS) |
| E4 | **Hochschul-Standorte ZGB-8** (curated mapping institution → 8-digit AGS) | https://www.hochschulkompass.de/ | `braunschweig/education/hochschul_orte_zgb.csv` | HRK terms (research reuse) |
| E5 | **OSM education POIs cross-check** (derivative of C3 + the main `osm_pois.parquet` artefact) | derived locally | `braunschweig/osm/osm_education_pois.gpkg` | ODbL 1.0 |

### E1 / E2 — LSN download

```powershell
python scripts/download_lsn_schulen.py `
    --dest eqasim-data/data/braunschweig/lsn/lsn_schulen_<year>.csv `
    --url '<authenticated GENESIS / RDB CSV URL>'
```

The script also runs in verify-only mode (no `--url`) to re-check the
SHA-256 of an already-present file. Pin the digest with
`--update-checksums` after the first verified download.

### E3 — DESTATIS download

```powershell
python scripts/download_destatis_hochschulen.py `
    --dest eqasim-data/data/braunschweig/destatis/hochschulen_<year>.csv `
    --url '<authenticated GENESIS REST URL>'
```

### E5 — OSM education POI cross-check

After the main `osm_pois.parquet` was produced by
`scripts/preprocess_osm_pois.py`:

```powershell
python scripts/extract_osm_education_pois.py `
    --pois eqasim-data/data/braunschweig/preprocessed/osm_pois.parquet `
    --out  eqasim-data/data/braunschweig/osm/osm_education_pois.gpkg
```

The GeoPackage is purely a QGIS cross-check artefact and is not
consumed by the synpp DAG.

---

## Bounding box (ARS 031xx, eight ZGB Kreise)

- AGS prefixes: `03101, 03102, 03103, 03151, 03153, 03154, 03157, 03158`
- UTM 32N (EPSG:25832): 542 000 – 691 000 E, 5 700 000 – 5 900 000 N
- WGS84 (rough): 9.6° – 11.4° E, 51.4° – 52.7° N

Use these bounds when pre-filtering OSM (C3 / D1) and GTFS (D2).

## Notes

- **No raw KBA download per region.** A2 (`fe4_2024.xlsx`) covers all of Germany; the BS pipeline reads it with a `Niedersachsen` filter on sheet FE4.3.
- **Household sizes / income are required for the BS pipeline** (B7, B8) — unlike the upstream Bavaria setup, which derived those from MiD. Do not reuse the Bavaria value here.
- **MiD 2023 (B10) is non-commercial.** The PDF is shared under infas / BMDV terms; do not redistribute. The extracted CSVs (B10a) are derivative works of B10 and inherit those terms.
- **z22data and RegioStaR scripts** write into `eqasim-data/`; that directory is `.gitignore`-d at the repo root. Re-run the download scripts after any clean checkout.
- **Status tracking** is intentionally not part of this file — use `python scripts/verify_braunschweig_inputs.py` for an automated check rather than manual checkboxes.
